import logging
from collections.abc import Callable
from difflib import SequenceMatcher
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.base import LearningProvider
from metaclass.modules.content.repository import ContentRepository
from metaclass.modules.content.schemas import (
    ContentGenerationJob,
    ContentGenerationJobStatus,
    KnowledgeCanonicalizationDraft,
    KnowledgeRelation,
    KnowledgeUnit,
    LearningContent,
    LearningContentDraft,
    LearningSection,
    ConceptNote,
    PageRef,
    PageUnderstanding,
    PageUnderstandingDraft,
    QuizItem,
    SourceExcerpt,
    TeachingPoint,
)
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.materials.service import MaterialService


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str, str], None]
PageProgressCallback = Callable[[int, int], None]


class ContentService:
    def __init__(
        self,
        repository: ContentRepository,
        materials: MaterialService,
        provider: LearningProvider,
    ) -> None:
        self.repository = repository
        self.materials = materials
        self.provider = provider
        self._jobs: dict[str, ContentGenerationJob] = {}
        self._job_lock = Lock()

    def create_generation_job(self, material_id: str) -> ContentGenerationJob:
        self.materials.get(material_id)
        job = ContentGenerationJob(
            id=f"content_job_{uuid4().hex[:12]}",
            material_id=material_id,
            status=ContentGenerationJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to generate learning content",
        )
        self._save_job(job)
        return job

    def create_collection_generation_job(self, collection_id: str) -> ContentGenerationJob:
        collection = self.materials.get_collection(collection_id)
        if not collection.material_ids:
            raise HTTPException(409, "Material collection has no materials")
        job = ContentGenerationJob(
            id=f"content_job_{uuid4().hex[:12]}",
            material_id=collection.primary_material_id or collection.material_ids[0],
            collection_id=collection_id,
            status=ContentGenerationJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to generate multi-material learning content",
        )
        self._save_job(job)
        return job

    def get_generation_job(self, job_id: str) -> ContentGenerationJob:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise HTTPException(404, "Content generation job not found")
            return job.model_copy(deep=True)

    def generation_job_result(self, job_id: str) -> LearningContent:
        job = self.get_generation_job(job_id)
        if job.status == ContentGenerationJobStatus.FAILED:
            raise HTTPException(422, job.error or "Content generation job failed")
        if job.status != ContentGenerationJobStatus.SUCCEEDED or not job.content_id:
            raise HTTPException(409, "Content generation job is not finished")
        return self.get(job.content_id)

    def run_generation_job(self, job_id: str) -> None:
        job = self.get_generation_job(job_id)
        try:
            job.status = ContentGenerationJobStatus.RUNNING
            job.progress = 10
            job.step = "building"
            job.message = "Generating learning content"
            job.updated_at = utc_now()
            self._save_job(job)
            def report_progress(progress: int, step: str, message: str) -> None:
                self._update_job_progress(job_id, progress, step, message)

            content = (
                self.build_collection(job.collection_id, progress_callback=report_progress)
                if job.collection_id
                else self.build(job.material_id or "", progress_callback=report_progress)
            )
            job.status = ContentGenerationJobStatus.SUCCEEDED
            job.progress = 100
            job.step = "completed"
            job.message = "Learning content generation completed"
            job.content_id = content.id
            job.updated_at = utc_now()
            self._save_job(job)
        except Exception as exc:
            job.status = ContentGenerationJobStatus.FAILED
            job.progress = 100
            job.step = "failed"
            job.message = "Learning content generation failed"
            job.error = str(exc)
            job.updated_at = utc_now()
            self._save_job(job)

    def _save_job(self, job: ContentGenerationJob) -> None:
        with self._job_lock:
            self._jobs[job.id] = job.model_copy(deep=True)

    def _update_job_progress(
        self,
        job_id: str,
        progress: int,
        step: str,
        message: str,
    ) -> None:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if not job or job.status != ContentGenerationJobStatus.RUNNING:
                return
            progress = min(progress, 99)
            if progress < job.progress:
                return
            job.progress = progress
            job.step = step
            job.message = message
            job.updated_at = utc_now()
            self._jobs[job_id] = job.model_copy(deep=True)

    @staticmethod
    def _report_progress(
        callback: ProgressCallback | None,
        progress: int,
        step: str,
        message: str,
    ) -> None:
        if callback:
            callback(progress, step, message)

    @classmethod
    def _report_page_progress(
        cls,
        callback: ProgressCallback | None,
        current: int,
        total: int,
        *,
        start: int,
        end: int,
    ) -> None:
        if not callback or total <= 0:
            return
        progress = start + int((end - start) * current / total)
        cls._report_progress(
            callback,
            progress,
            "understanding_pages",
            f"Understanding source pages ({current}/{total})",
        )

    def understand_pages(
        self,
        material_id: str,
        *,
        regenerate: bool = False,
        page_progress: PageProgressCallback | None = None,
    ) -> list[PageUnderstanding]:
        material = self.materials.get(material_id)
        pages = self.materials.pages(material_id)
        if material.status != "parsed" or not pages:
            raise HTTPException(409, "Parse the material before understanding its pages")

        existing = self.repository.list_understandings(material_id)
        if existing and len(existing) == len(pages) and not regenerate:
            if page_progress:
                page_progress(len(pages), len(pages))
            return existing

        understandings = []
        for index, page in enumerate(pages, start=1):
            draft = self.provider.understand_page(page.title, page.raw_text, page.page_no)
            understandings.append(self._understanding_from_draft(material_id, page, draft))
            if page_progress:
                page_progress(index, len(pages))
        self.repository.save_understandings(understandings)
        return understandings

    def list_understandings(self, material_id: str) -> list[PageUnderstanding]:
        self.materials.get(material_id)
        return self.repository.list_understandings(material_id)

    def build(
        self,
        material_id: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> LearningContent:
        self._report_progress(progress_callback, 8, "preparing", "Preparing source material")
        page_list = self.materials.pages(material_id)
        if not page_list:
            raise HTTPException(409, "Parse the material before building learning content")

        if hasattr(self.provider, "organize_learning_content"):
            try:
                content = self._build_with_global_organizer(
                    material_id,
                    page_list,
                    progress_callback=progress_callback,
                )
                self._report_progress(progress_callback, 96, "saving", "Saving learning content")
                self.repository.save(content)
                return content
            except Exception:
                pass

        pages = {page.id: page for page in page_list}
        understandings = self.understand_pages(
            material_id,
            page_progress=lambda current, total: self._report_page_progress(
                progress_callback,
                current,
                total,
                start=12,
                end=72,
            ),
        )
        if not understandings:
            raise HTTPException(409, "No page understanding is available")

        sections = []
        for index, understanding in enumerate(understandings, start=1):
            page = pages[understanding.page_id]
            sections.append(
                LearningSection(
                    id=f"section_{page.page_no:03d}",
                    title=page.title or f"第 {page.page_no} 页",
                    role=understanding.page_role,
                    content_goal="Explain the selected teaching material",
                    summary=understanding.summary,
                    key_points=understanding.knowledge_points,
                    teaching_narrative=understanding.summary,
                    knowledge_points=understanding.knowledge_points,
                    source_excerpts=understanding.key_excerpts,
                    formulas=understanding.formulas,
                    misconceptions=understanding.misconceptions,
                    source_refs=understanding.source_refs,
                    page_refs=[
                        PageRef(
                            material_id=page.material_id,
                            page_no=page.page_no,
                            reason="Primary source page for this teaching unit",
                        )
                    ],
                    quiz_items=self._build_quiz_items(understanding, page.title),
                )
            )
            self._report_progress(
                progress_callback,
                72 + int(22 * index / len(understandings)),
                "organizing",
                f"Organizing teaching units ({index}/{len(understandings)})",
            )

        first_section = sections[0]
        content_id = f"content_{material_id.removeprefix('mat_')}"
        existing = self.repository.get(content_id)
        content = LearningContent(
            id=content_id,
            material_id=material_id,
            material_ids=[material_id],
            title=first_section.title,
            objectives=[f"理解：{point}" for point in first_section.knowledge_points[:3]],
            sections=sections,
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
        )
        self._report_progress(progress_callback, 96, "saving", "Saving learning content")
        self.repository.save(content)
        return content

    def build_collection(
        self,
        collection_id: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> LearningContent:
        self._report_progress(progress_callback, 8, "preparing", "Preparing material collection")
        collection = self.materials.get_collection(collection_id)
        if not collection.material_ids:
            raise HTTPException(409, "Material collection has no materials")
        page_groups = [
            (material_id, self.materials.pages(material_id))
            for material_id in collection.material_ids
        ]
        if any(not pages for _, pages in page_groups):
            raise HTTPException(409, "Parse every material before building collection content")

        material_ids = collection.material_ids
        primary_material_id = collection.primary_material_id or material_ids[0]
        all_pages = [page for _, pages in page_groups for page in pages]
        total_pages = len(all_pages)
        processed_pages = 0
        understandings_by_material = {}
        for material_id, material_pages in page_groups:
            page_offset = processed_pages
            understandings_by_material[material_id] = self.understand_pages(
                material_id,
                page_progress=lambda current, _total, offset=page_offset: (
                    self._report_page_progress(
                        progress_callback,
                        offset + current,
                        total_pages,
                        start=12,
                        end=62,
                    )
                ),
            )
            processed_pages += len(material_pages)
        understandings = [
            understanding
            for material_id in material_ids
            for understanding in understandings_by_material[material_id]
        ]
        self._report_progress(
            progress_callback,
            66,
            "extracting_knowledge_units",
            "Extracting knowledge units",
        )
        raw_knowledge_units = self._knowledge_units_from_understandings(understandings)
        self._report_progress(
            progress_callback,
            72,
            "canonicalizing",
            "Merging duplicate concepts and building knowledge relations",
        )
        knowledge_units, canonicalization_warnings = self._canonicalize_knowledge_units(
            raw_knowledge_units
        )

        organizer = getattr(self.provider, "organize_collection_learning_content", None)
        if organizer:
            try:
                self._report_progress(
                    progress_callback,
                    84,
                    "organizing",
                    "Organizing the course-level learning content",
                )
                draft = organizer(
                    collection_id=collection_id,
                    material_ids=material_ids,
                    pages=all_pages,
                    understandings=understandings,
                    knowledge_units=knowledge_units,
                )
                content_id = f"content_{collection_id.removeprefix('col_')}"
                existing = self.repository.get(content_id)
                content = self._content_from_draft(
                    content_id=content_id,
                    material_id=primary_material_id,
                    draft=draft,
                    pages=all_pages,
                    created_at=existing.created_at if existing else utc_now(),
                    material_ids=material_ids,
                    collection_id=collection_id,
                    knowledge_units=knowledge_units,
                    quality_warnings=canonicalization_warnings,
                )
                self._report_progress(
                    progress_callback, 96, "saving", "Saving learning content"
                )
                self.repository.save(content)
                return content
            except Exception as exc:
                logger.warning("Collection learning-content organizer failed: %s", exc)
                canonicalization_warnings.append(
                    "The global LLM organizer failed; deterministic knowledge-unit sections were used."
                )

        self._report_progress(
            progress_callback,
            84,
            "organizing",
            "Organizing deterministic teaching units",
        )
        content = self._fallback_collection_content(
            collection_id=collection_id,
            primary_material_id=primary_material_id,
            material_ids=material_ids,
            knowledge_units=knowledge_units,
            quality_warnings=canonicalization_warnings,
        )
        self._report_progress(progress_callback, 96, "saving", "Saving learning content")
        self.repository.save(content)
        return content

    def _fallback_collection_content(
        self,
        *,
        collection_id: str,
        primary_material_id: str,
        material_ids: list[str],
        knowledge_units: list[KnowledgeUnit],
        quality_warnings: list[str],
    ) -> LearningContent:
        sections = []
        for index, unit in enumerate(knowledge_units, start=1):
            sections.append(
                LearningSection(
                    id=f"section_{index:03d}",
                    title=unit.title,
                    role=unit.unit_type,
                    content_goal=f"Help learners understand {unit.title} as a teaching unit",
                    summary=unit.summary,
                    key_points=unit.keywords[:6],
                    teaching_narrative=self._teaching_narrative_from_unit(unit),
                    knowledge_points=unit.keywords[:8],
                    source_excerpts=unit.source_excerpts,
                    formulas=unit.formulas,
                    examples=unit.examples,
                    misconceptions=unit.misconceptions,
                    source_refs=unit.source_refs,
                    page_refs=unit.page_refs,
                    quiz_items=[],
                )
            )

        if not sections:
            raise HTTPException(409, "No page understanding is available")
        content_id = f"content_{collection_id.removeprefix('col_')}"
        existing = self.repository.get(content_id)
        first_section = sections[0]
        return LearningContent(
            id=content_id,
            material_id=primary_material_id,
            material_ids=material_ids,
            collection_id=collection_id,
            title=first_section.title,
            subtitle="Multi-material learning content",
            objectives=[f"Understand {point}" for point in first_section.knowledge_points[:3]],
            material_overview={
                "source_type": "mixed" if len(material_ids) > 1 else "single",
                "structure_summary": "Content is organized across uploaded materials by teaching topics.",
                "global_structure": [
                    {"id": section.id, "title": section.title, "role": section.role}
                    for section in sections
                ],
            },
            global_concepts=self._global_concepts_from_units(knowledge_units),
            knowledge_units=knowledge_units,
            sections=sections,
            generation_guidance={
                "recommended_teaching_flow": [section.title for section in sections],
                "ppt_guidance": {
                    "avoid": ["Do not map one source page mechanically to one PPT slide"]
                },
            },
            quality={"warnings": self._dedupe_strings(quality_warnings)},
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
        )

    def _knowledge_units_from_understandings(
        self, understandings: list[PageUnderstanding]
    ) -> list[KnowledgeUnit]:
        units = []
        for understanding in understandings:
            title = (
                understanding.teachable_points[0].point
                if understanding.teachable_points
                else understanding.knowledge_points[0]
                if understanding.knowledge_points
                else understanding.title or f"Page {understanding.page_no}"
            )
            page_refs = [
                PageRef(
                    material_id=ref.material_id,
                    page_no=ref.page_no,
                    reason="Knowledge unit source page",
                )
                for ref in understanding.source_refs
            ]
            units.append(
                KnowledgeUnit(
                    id=f"ku_{understanding.material_id}_{understanding.page_no:03d}",
                    title=title,
                    unit_type=self._unit_type_from_page_role(understanding.page_role),
                    summary=understanding.summary,
                    keywords=understanding.knowledge_points or [title],
                    concepts=understanding.concepts,
                    source_excerpts=understanding.key_excerpts,
                    formulas=understanding.formulas,
                    misconceptions=understanding.misconceptions,
                    source_refs=understanding.source_refs,
                    page_refs=page_refs,
                    source_unit_ids=[f"ku_{understanding.material_id}_{understanding.page_no:03d}"],
                    importance="core" if understanding.teaching_focus else "supporting",
                )
            )
        return units

    def _merge_knowledge_units(self, units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
        grouped: list[list[KnowledgeUnit]] = []
        for unit in units:
            matching_group = next(
                (
                    group
                    for group in grouped
                    if max(self._topic_similarity(unit, item) for item in group) >= 0.82
                ),
                None,
            )
            if matching_group is None:
                grouped.append([unit])
            else:
                matching_group.append(unit)

        merged = []
        for index, items in enumerate(grouped, start=1):
            merged.append(self._merge_unit_group(items, unit_id=f"ku_canonical_{index:03d}"))

        role_order = {
            "motivation": 0,
            "concept": 1,
            "method": 2,
            "formula": 3,
            "example": 4,
            "case": 5,
            "comparison": 6,
            "summary": 7,
            "reference": 8,
        }
        return sorted(merged, key=lambda unit: (role_order.get(unit.unit_type, 4), unit.title))

    def _canonicalize_knowledge_units(
        self, units: list[KnowledgeUnit]
    ) -> tuple[list[KnowledgeUnit], list[str]]:
        preliminary = self._merge_knowledge_units(units)
        canonicalizer = getattr(self.provider, "canonicalize_knowledge_units", None)
        if not canonicalizer or len(preliminary) < 2:
            return preliminary, []
        try:
            draft = canonicalizer(preliminary)
            return self._apply_canonicalization(preliminary, draft), []
        except Exception as exc:
            logger.warning("Knowledge-unit canonicalization failed: %s", exc)
            return preliminary, [
                "Semantic knowledge-unit canonicalization failed; local similarity grouping was used."
            ]

    def _apply_canonicalization(
        self,
        units: list[KnowledgeUnit],
        draft: KnowledgeCanonicalizationDraft,
    ) -> list[KnowledgeUnit]:
        unit_by_id = {unit.id: unit for unit in units}
        used_unit_ids: set[str] = set()
        result: list[KnowledgeUnit] = []
        result_ids: set[str] = set()

        for group in draft.groups:
            if group.id in result_ids:
                raise ValueError(f"Duplicate canonical knowledge-unit id: {group.id}")
            group_unit_ids = self._dedupe_strings(group.unit_ids)
            if any(unit_id not in unit_by_id for unit_id in group_unit_ids):
                raise ValueError(f"Canonical group {group.id} references an unknown unit")
            if any(unit_id in used_unit_ids for unit_id in group_unit_ids):
                raise ValueError(f"A knowledge unit appears in more than one group: {group.id}")
            items = [unit_by_id[unit_id] for unit_id in group_unit_ids]
            result.append(
                self._merge_unit_group(
                    items,
                    unit_id=group.id,
                    title=group.title,
                    unit_type=group.unit_type,
                    summary=group.summary,
                    aliases=group.aliases,
                    confidence=group.confidence,
                )
            )
            result_ids.add(group.id)
            used_unit_ids.update(group_unit_ids)

        for unit in units:
            if unit.id not in used_unit_ids:
                result.append(unit)
                result_ids.add(unit.id)

        relations_by_source: dict[str, list[KnowledgeRelation]] = {}
        for relation in draft.relations:
            if (
                relation.source_group_id not in result_ids
                or relation.target_group_id not in result_ids
                or relation.source_group_id == relation.target_group_id
            ):
                continue
            relations_by_source.setdefault(relation.source_group_id, []).append(
                KnowledgeRelation(
                    target_unit_id=relation.target_group_id,
                    relation_type=relation.relation_type,
                    reason=relation.reason,
                    confidence=relation.confidence,
                )
            )

        result = [
            unit.model_copy(update={"relations": relations_by_source.get(unit.id, [])})
            for unit in result
        ]
        role_order = {
            "motivation": 0,
            "concept": 1,
            "method": 2,
            "formula": 3,
            "example": 4,
            "case": 5,
            "comparison": 6,
            "summary": 7,
            "reference": 8,
        }
        return sorted(result, key=lambda unit: (role_order.get(unit.unit_type, 4), unit.title))

    def _merge_unit_group(
        self,
        items: list[KnowledgeUnit],
        *,
        unit_id: str,
        title: str = "",
        unit_type: str = "",
        summary: str = "",
        aliases: list[str] | None = None,
        confidence: float = 1.0,
    ) -> KnowledgeUnit:
        first = items[0]
        titles = self._dedupe_strings([item.title for item in items])
        return KnowledgeUnit(
            id=unit_id,
            title=title or first.title,
            unit_type=unit_type or self._dominant_unit_type(items),
            summary=summary or " ".join(item.summary for item in items)[:1200],
            aliases=self._dedupe_strings((aliases or []) + titles),
            keywords=self._dedupe_strings([word for item in items for word in item.keywords]),
            concepts=self._dedupe_concepts(
                [concept for item in items for concept in item.concepts]
            ),
            source_excerpts=[excerpt for item in items for excerpt in item.source_excerpts][:8],
            formulas=[formula for item in items for formula in item.formulas],
            examples=[example for item in items for example in item.examples],
            misconceptions=[item for unit in items for item in unit.misconceptions],
            source_refs=self._dedupe_source_refs(
                [ref for item in items for ref in item.source_refs]
            ),
            page_refs=self._dedupe_page_refs([ref for item in items for ref in item.page_refs]),
            source_unit_ids=self._dedupe_strings(
                [source_id for item in items for source_id in (item.source_unit_ids or [item.id])]
            ),
            importance="core" if any(item.importance == "core" for item in items) else "supporting",
            confidence=confidence,
        )

    @classmethod
    def _topic_similarity(cls, left: KnowledgeUnit, right: KnowledgeUnit) -> float:
        left_title = cls._normalize_topic(left.title)
        right_title = cls._normalize_topic(right.title)
        if left_title == right_title:
            return 1.0
        if min(len(left_title), len(right_title)) >= 4 and (
            left_title in right_title or right_title in left_title
        ):
            return 0.9
        title_score = SequenceMatcher(None, left_title, right_title).ratio()
        left_terms = {
            cls._normalize_topic(value) for value in [left.title, *left.keywords] if value
        }
        right_terms = {
            cls._normalize_topic(value) for value in [right.title, *right.keywords] if value
        }
        overlap = len(left_terms & right_terms)
        union = len(left_terms | right_terms)
        keyword_score = overlap / union if union else 0.0
        return max(title_score, keyword_score)

    @staticmethod
    def _unit_type_from_page_role(page_role: str) -> str:
        mapping = {
            "cover": "motivation",
            "agenda": "motivation",
            "concept": "concept",
            "method": "method",
            "formula": "formula",
            "example": "example",
            "data": "case",
            "summary": "summary",
            "reference": "reference",
            "appendix": "reference",
        }
        return mapping.get(page_role, "concept")

    @staticmethod
    def _normalize_topic(title: str) -> str:
        return "".join(ch.lower() for ch in title if ch.isalnum())[:80] or "general"

    @staticmethod
    def _dominant_unit_type(units: list[KnowledgeUnit]) -> str:
        priority = [
            "motivation",
            "concept",
            "method",
            "formula",
            "example",
            "case",
            "comparison",
            "summary",
        ]
        types = {unit.unit_type for unit in units}
        return next((item for item in priority if item in types), units[0].unit_type)

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            key = value.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _dedupe_source_refs(values: list) -> list:
        result = []
        seen = set()
        for ref in values:
            key = (ref.material_id, ref.page_id, ref.page_no, ref.text_span, ref.image_path)
            if key not in seen:
                seen.add(key)
                result.append(ref)
        return result

    @staticmethod
    def _dedupe_page_refs(values: list[PageRef]) -> list[PageRef]:
        result = []
        seen = set()
        for ref in values:
            key = (ref.material_id, ref.page_no)
            if key not in seen:
                seen.add(key)
                result.append(ref)
        return result

    @staticmethod
    def _dedupe_concepts(values: list[ConceptNote]) -> list[ConceptNote]:
        result = []
        seen = set()
        for concept in values:
            key = concept.name.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(concept)
        return result

    @staticmethod
    def _global_concepts_from_units(units: list[KnowledgeUnit]) -> list[ConceptNote]:
        return ContentService._dedupe_concepts(
            [concept for unit in units for concept in unit.concepts]
        )

    @staticmethod
    def _teaching_narrative_from_unit(unit: KnowledgeUnit) -> str:
        evidence = " ".join(excerpt.text for excerpt in unit.source_excerpts[:2])
        if evidence:
            return f"{unit.summary}\n\nUse selected evidence: {evidence}"[:2000]
        return unit.summary

    def _build_with_global_organizer(
        self,
        material_id: str,
        pages: list[PageMetadata],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> LearningContent:
        drafts = self._draft_understandings_with_context(
            pages,
            page_progress=lambda current, total: self._report_page_progress(
                progress_callback,
                current,
                total,
                start=12,
                end=68,
            ),
        )
        understandings = [
            self._understanding_from_draft(material_id, page, draft)
            for page, draft in zip(pages, drafts, strict=False)
        ]
        self.repository.save_understandings(understandings)

        organizer = getattr(self.provider, "organize_learning_content")
        self._report_progress(
            progress_callback,
            82,
            "organizing",
            "Organizing the course-level learning content",
        )
        draft = organizer(material_id=material_id, pages=pages, understandings=drafts)
        content_id = f"content_{material_id.removeprefix('mat_')}"
        existing = self.repository.get(content_id)
        return self._content_from_draft(
            content_id=content_id,
            material_id=material_id,
            draft=draft,
            pages=pages,
            created_at=existing.created_at if existing else utc_now(),
        )

    def _draft_understandings_with_context(
        self,
        pages: list[PageMetadata],
        *,
        page_progress: PageProgressCallback | None = None,
    ) -> list[PageUnderstandingDraft]:
        result = []
        for index, page in enumerate(pages):
            visual_description = ""
            describe_page_visual = getattr(self.provider, "describe_page_visual", None)
            if describe_page_visual:
                visual_description = describe_page_visual(page)
            understand_page_with_context = getattr(self.provider, "understand_page_with_context")
            result.append(
                understand_page_with_context(
                    page_no=page.page_no,
                    title=page.title,
                    raw_text=page.raw_text,
                    previous_page=pages[index - 1] if index > 0 else None,
                    next_page=pages[index + 1] if index + 1 < len(pages) else None,
                    visual_description=visual_description,
                )
            )
            if page_progress:
                page_progress(index + 1, len(pages))
        return result

    def _understanding_from_draft(
        self, material_id: str, page: PageMetadata, draft: PageUnderstandingDraft
    ) -> PageUnderstanding:
        key_excerpts = [
            excerpt.model_copy(update={"source_refs": excerpt.source_refs or page.source_refs})
            for excerpt in draft.key_excerpts
        ] or self._fallback_source_excerpts(page)
        concepts = draft.concepts or [
            ConceptNote(
                id=f"concept_{index:02d}",
                name=point,
                definition="",
                plain_explanation=point,
                why_it_matters="Selected as a teachable point from this page.",
                source_refs=page.source_refs,
            )
            for index, point in enumerate(draft.knowledge_points[:5], start=1)
        ]
        teachable_points = draft.teachable_points or [
            TeachingPoint(point=point, importance="core" if index == 1 else "supporting")
            for index, point in enumerate(draft.knowledge_points[:5], start=1)
        ]
        return PageUnderstanding(
            id=f"understanding_{page.id}",
            material_id=material_id,
            page_id=page.id,
            page_no=page.page_no,
            page_role=draft.page_role,
            title=page.title,
            summary=draft.summary,
            teachable_points=teachable_points,
            key_excerpts=key_excerpts,
            concepts=concepts,
            formulas=draft.formulas,
            visual_analysis=draft.visual_analysis,
            knowledge_points=draft.knowledge_points,
            teaching_focus=draft.teaching_focus,
            misconceptions=draft.misconceptions,
            possible_questions=draft.possible_questions,
            quiz_items=draft.quiz_items,
            relations={
                "depends_on_pages": draft.depends_on_pages,
                "leads_to_pages": draft.leads_to_pages,
                "same_topic_pages": draft.same_topic_pages,
            },
            source_refs=page.source_refs,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=getattr(self.provider, "prompt_version", "v1"),
        )

    @staticmethod
    def _fallback_source_excerpts(page: PageMetadata) -> list[SourceExcerpt]:
        text = " ".join(page.raw_text.split())
        if not text:
            return []
        return [
            SourceExcerpt(
                id=f"excerpt_{page.id}",
                text=text[:500],
                type="evidence",
                reason="Representative source text extracted from the parsed page.",
                importance="supporting",
                usage="reference_only",
                source_refs=page.source_refs,
            )
        ]

    def _content_from_draft(
        self,
        *,
        content_id: str,
        material_id: str,
        draft: LearningContentDraft,
        pages: list[PageMetadata],
        created_at,
        material_ids: list[str] | None = None,
        collection_id: str | None = None,
        knowledge_units: list[KnowledgeUnit] | None = None,
        quality_warnings: list[str] | None = None,
    ) -> LearningContent:
        page_by_no = {page.page_no: page for page in pages}
        page_by_source = {(page.material_id, page.page_no): page for page in pages}
        sections = []
        for index, section in enumerate(draft.sections, start=1):
            if section.page_refs:
                source_refs = [
                    ref
                    for page_ref in section.page_refs
                    for ref in page_by_source.get(
                        (page_ref.material_id, page_ref.page_no),
                        pages[0],
                    ).source_refs
                ]
            else:
                source_refs = [
                    ref
                    for page_no in section.page_nos
                    for ref in page_by_no.get(page_no, page_by_no[pages[0].page_no]).source_refs
                ]
            if not source_refs:
                source_refs = pages[0].source_refs
            quiz_items = [
                QuizItem(
                    id=f"quiz_{index:03d}_{quiz_index:02d}",
                    question=quiz.question,
                    options=quiz.options,
                    correct_index=quiz.correct_index,
                    explanation=quiz.explanation,
                    knowledge_point=quiz.knowledge_point,
                    source_refs=source_refs,
                )
                for quiz_index, quiz in enumerate(section.quiz_items, start=1)
            ]
            sections.append(
                LearningSection(
                    id=f"section_{index:03d}",
                    title=section.title,
                    role=section.role,
                    content_goal=section.content_goal,
                    summary=section.summary,
                    key_points=section.key_points or section.knowledge_points,
                    teaching_narrative=section.teaching_narrative or section.teaching_script,
                    knowledge_points=section.knowledge_points,
                    source_excerpts=section.source_excerpts,
                    formulas=section.formulas,
                    examples=section.examples,
                    visual_opportunities=section.visual_opportunities,
                    misconceptions=section.misconceptions,
                    interaction_opportunities=section.interaction_opportunities,
                    transition={"to_next": section.transition_to_next},
                    source_refs=source_refs,
                    page_refs=section.page_refs
                    or [
                        PageRef(
                            material_id=ref.material_id,
                            page_no=ref.page_no,
                            reason="Source page selected by organizer",
                        )
                        for ref in source_refs
                    ],
                    quiz_items=quiz_items,
                    page_nos=section.page_nos,
                    outline_level=1,
                    teaching_script=section.teaching_script,
                    visual_summary=section.visual_summary,
                    transition_to_next=section.transition_to_next,
                )
            )
        quality = dict(draft.quality)
        quality["warnings"] = self._dedupe_strings(
            list(quality.get("warnings", [])) + list(quality_warnings or [])
        )
        return LearningContent(
            id=content_id,
            material_id=material_id,
            material_ids=material_ids or [material_id],
            collection_id=collection_id,
            title=draft.title,
            subtitle=draft.subtitle,
            audience=draft.audience,
            teaching_intent=draft.teaching_intent,
            material_overview=draft.material_overview,
            global_concepts=draft.global_concepts,
            knowledge_units=knowledge_units or [],
            objectives=draft.objectives or draft.outline,
            sections=sections,
            generation_guidance=draft.generation_guidance,
            quality=quality,
            created_at=created_at,
            updated_at=utc_now(),
        )

    def get(self, content_id: str) -> LearningContent:
        content = self.repository.get(content_id)
        if not content:
            raise HTTPException(404, "Learning content not found")
        return content

    @staticmethod
    def _build_quiz_items(understanding: PageUnderstanding, page_title: str) -> list[QuizItem]:
        if understanding.quiz_items:
            return [
                QuizItem(
                    id=f"quiz_{understanding.page_no:03d}_{index:02d}",
                    question=draft.question,
                    options=draft.options,
                    correct_index=draft.correct_index,
                    explanation=draft.explanation,
                    knowledge_point=draft.knowledge_point,
                    source_refs=understanding.source_refs,
                )
                for index, draft in enumerate(understanding.quiz_items, start=1)
            ]

        point = understanding.knowledge_points[0] if understanding.knowledge_points else page_title
        if not point:
            return []
        return [
            QuizItem(
                id=f"quiz_{understanding.page_no:03d}_01",
                question=f"下面哪一项最能说明你理解了“{point}”？",
                options=[
                    f"能说出{point}的含义、条件或例子",
                    "只记住它在材料中出现过",
                    "把它和其他概念混在一起使用",
                ],
                correct_index=0,
                explanation=f"理解{point}需要能解释它如何成立或如何使用。",
                knowledge_point=point,
                source_refs=understanding.source_refs,
            )
        ]
