import json
import logging
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.base import LearningProvider
from metaclass.modules.content.repository import ContentRepository
from metaclass.modules.content.schemas import (
    ContentGenerationJob,
    ContentGenerationJobStatus,
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    KnowledgeCanonicalizationDraft,
    KnowledgeRelation,
    KnowledgeUnit,
    LearningContent,
    LearningContentDraft,
    LearningContentDiagnostics,
    MaterialLearningContentSummary,
    LearningSection,
    LearningSectionDraft,
    ConceptNote,
    PageRef,
    PageUnderstanding,
    PageUnderstandingDraft,
    QuizItem,
    SourceExcerpt,
    SourceDeckLearningContentDraft,
    SourceDeckSectionDraft,
    SourceDeckTeachingStructureDraft,
    TeachingSegment,
    TeachingPoint,
    VisualOpportunity,
)
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.materials.service import MaterialService


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str, str], None]
PageProgressCallback = Callable[[int, int], None]
MAX_UNITS_PER_TOPIC_NODE = 3
MAX_TREE_NODES_PER_SECTION = 2


class ContentGenerationPaused(RuntimeError):
    pass


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
        self._pause_events: dict[str, Event] = {}
        data_dir = getattr(materials, "data_dir", None)
        self._runtime_dir = (
            Path(data_dir) / "runtime" / "content_generation"
            if isinstance(data_dir, (str, Path))
            else None
        )
        self._restore_jobs()

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

    def create_source_deck_generation_job(self, material_id: str) -> ContentGenerationJob:
        self.materials.get(material_id)
        job = ContentGenerationJob(
            id=f"content_job_{uuid4().hex[:12]}",
            material_id=material_id,
            organization_mode="source_deck",
            status=ContentGenerationJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to reconstruct the source deck structure",
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

    def list_generation_jobs(self) -> list[ContentGenerationJob]:
        with self._job_lock:
            return sorted(
                (job.model_copy(deep=True) for job in self._jobs.values()),
                key=lambda job: job.updated_at,
                reverse=True,
            )

    def _restore_jobs(self) -> None:
        if not self._runtime_dir:
            return
        jobs_dir = self._runtime_dir / "jobs"
        if not jobs_dir.exists():
            return
        for path in jobs_dir.glob("*.json"):
            try:
                job = ContentGenerationJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Ignoring invalid content job state: %s", path)
                continue
            if job.status == ContentGenerationJobStatus.RUNNING:
                job.status = ContentGenerationJobStatus.PAUSED
                job.step = "paused"
                job.message = "服务中断，已保存进度，可以继续"
                job.error = None
                job.updated_at = utc_now()
                self._write_json_atomic(path, job.model_dump(mode="json"))
            self._jobs[job.id] = job
            self._pause_events[job.id] = Event()

    def pause_generation_job(self, job_id: str) -> ContentGenerationJob:
        job = self.get_generation_job(job_id)
        if job.status not in {ContentGenerationJobStatus.QUEUED, ContentGenerationJobStatus.RUNNING}:
            raise HTTPException(409, "Only queued or running jobs can be paused")
        with self._job_lock:
            self._pause_events.setdefault(job_id, Event()).set()
        job.status = ContentGenerationJobStatus.PAUSED
        job.step = "paused"
        job.message = "已请求暂停；当前结果保存后停止"
        job.error = None
        job.updated_at = utc_now()
        self._save_job(job)
        return job

    def resume_generation_job(self, job_id: str) -> ContentGenerationJob:
        job = self.get_generation_job(job_id)
        if job.status != ContentGenerationJobStatus.PAUSED:
            raise HTTPException(409, "Only paused jobs can be resumed")
        with self._job_lock:
            self._pause_events[job_id] = Event()
        job.status = ContentGenerationJobStatus.QUEUED
        job.step = "queued"
        job.message = "等待从 checkpoint 继续"
        job.updated_at = utc_now()
        self._save_job(job)
        return job

    def discard_generation_job(self, job_id: str) -> None:
        job = self.get_generation_job(job_id)
        if job.status == ContentGenerationJobStatus.RUNNING:
            raise HTTPException(409, "Pause the job before discarding it")
        with self._job_lock:
            self._jobs.pop(job_id, None)
            self._pause_events.pop(job_id, None)
        if self._runtime_dir:
            (self._runtime_dir / "jobs" / f"{job_id}.json").unlink(missing_ok=True)
        if job.organization_mode == "source_deck" and job.material_id:
            self._clear_source_checkpoint(job.material_id)

    def generation_job_result(self, job_id: str) -> LearningContent:
        job = self.get_generation_job(job_id)
        if job.status == ContentGenerationJobStatus.FAILED:
            raise HTTPException(422, job.error or "Content generation job failed")
        if job.status != ContentGenerationJobStatus.SUCCEEDED or not job.content_id:
            raise HTTPException(409, "Content generation job is not finished")
        return self.get(job.content_id)

    def run_generation_job(self, job_id: str) -> None:
        job = self.get_generation_job(job_id)
        with self._job_lock:
            pause_event = self._pause_events.setdefault(job_id, Event())
        try:
            if job.content_id and self.repository.get(job.content_id):
                job.status = ContentGenerationJobStatus.SUCCEEDED
                job.progress = 100
                job.step = "completed"
                job.message = "Learning content generation completed"
                job.updated_at = utc_now()
                self._save_job(job)
                return
            job.status = ContentGenerationJobStatus.RUNNING
            job.progress = 10
            job.step = "building"
            job.message = "Generating learning content"
            job.updated_at = utc_now()
            self._save_job(job)

            def report_progress(progress: int, step: str, message: str) -> None:
                self._update_job_progress(job_id, progress, step, message)
                if pause_event.is_set():
                    raise ContentGenerationPaused("Content generation paused")

            if job.organization_mode == "source_deck":
                content = self.build_source_deck(
                    job.material_id or "", progress_callback=report_progress
                )
            else:
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
        except ContentGenerationPaused:
            job = self.get_generation_job(job_id)
            candidate_id = (
                f"content_source_{(job.material_id or '').removeprefix('mat_')}"
                if job.organization_mode == "source_deck"
                else f"content_{(job.collection_id or job.material_id or '').removeprefix('col_').removeprefix('mat_')}"
            )
            if self.repository.get(candidate_id):
                job.content_id = candidate_id
            job.status = ContentGenerationJobStatus.PAUSED
            job.step = "paused"
            job.message = "已暂停，checkpoint 已保存"
            job.error = None
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
            if self._runtime_dir:
                self._write_json_atomic(
                    self._runtime_dir / "jobs" / f"{job.id}.json",
                    job.model_dump(mode="json"),
                )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _source_checkpoint_path(self, material_id: str) -> Path | None:
        if not self._runtime_dir:
            return None
        return self._runtime_dir / "checkpoints" / f"source_deck_{material_id}.json"

    def _load_source_checkpoint(self, material_id: str, pages: list[PageMetadata]) -> dict:
        path = self._source_checkpoint_path(material_id)
        if not path or not path.exists():
            return {}
        material = self.materials.get(material_id)
        expected = {
            "version": 1,
            "material_id": material_id,
            "file_hash": material.file_hash,
            "page_count": len(pages),
            "prompt_version": getattr(self.provider, "prompt_version", "v1"),
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Ignoring corrupt source-deck checkpoint: %s", path)
            return {}
        if any(payload.get(key) != value for key, value in expected.items()):
            logger.info("Ignoring stale source-deck checkpoint: %s", path)
            return {}
        return payload

    def _save_source_checkpoint(
        self, material_id: str, pages: list[PageMetadata], **state: object
    ) -> None:
        path = self._source_checkpoint_path(material_id)
        if not path:
            return
        material = self.materials.get(material_id)
        self._write_json_atomic(
            path,
            {
                "version": 1,
                "material_id": material_id,
                "file_hash": material.file_hash,
                "page_count": len(pages),
                "prompt_version": getattr(self.provider, "prompt_version", "v1"),
                "updated_at": utc_now().isoformat(),
                **state,
            },
        )

    def _clear_source_checkpoint(self, material_id: str) -> None:
        path = self._source_checkpoint_path(material_id)
        if path and path.exists():
            path.unlink()

    def _update_job_progress(
        self,
        job_id: str,
        progress: int,
        step: str,
        message: str,
    ) -> None:
        saved_job: ContentGenerationJob | None = None
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
            saved_job = job.model_copy(deep=True)
        if saved_job and self._runtime_dir:
            self._write_json_atomic(
                self._runtime_dir / "jobs" / f"{saved_job.id}.json",
                saved_job.model_dump(mode="json"),
            )

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
        current_prompt_version = getattr(self.provider, "prompt_version", "v1")
        reusable = {
            item.page_id: item
            for item in existing
            if item.prompt_version == current_prompt_version and not regenerate
        }
        understandings: list[PageUnderstanding] = []
        understand_with_context = getattr(self.provider, "understand_page_with_context", None)
        describe_visual = getattr(self.provider, "describe_page_visual", None)
        for index, page in enumerate(pages, start=1):
            cached = reusable.get(page.id)
            if cached:
                understanding = cached
            elif understand_with_context:
                visual_description = describe_visual(page) if describe_visual else ""
                draft = understand_with_context(
                    page_no=page.page_no,
                    title=page.title,
                    raw_text=page.raw_text,
                    previous_page=pages[index - 2] if index > 1 else None,
                    next_page=pages[index] if index < len(pages) else None,
                    visual_description=visual_description,
                )
                understanding = self._understanding_from_draft(material_id, page, draft)
            else:
                draft = self.provider.understand_page(page.title, page.raw_text, page.page_no)
                understanding = self._understanding_from_draft(material_id, page, draft)
            understandings.append(understanding)
            if not cached:
                # Each successfully understood page is its own durable checkpoint.
                self.repository.save_understandings([understanding])
            if page_progress:
                page_progress(index, len(pages))
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
        return self._build_from_materials(
            content_id=f"content_{material_id.removeprefix('mat_')}",
            material_ids=[material_id],
            primary_material_id=material_id,
            collection_id=None,
            page_groups=[(material_id, page_list)],
            progress_callback=progress_callback,
        )

    def build_source_deck(
        self,
        material_id: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> LearningContent:
        self._report_progress(
            progress_callback, 8, "preparing", "Preparing the source deck structure"
        )
        pages = self.materials.pages(material_id)
        if not pages:
            raise HTTPException(409, "Parse the material before building learning content")
        checkpoint = self._load_source_checkpoint(material_id, pages)
        understandings = self.understand_pages(
            material_id,
            page_progress=lambda current, total: self._report_page_progress(
                progress_callback, current, total, start=12, end=62
            ),
        )
        self._report_progress(
            progress_callback, 68, "extracting_knowledge_units", "Extracting knowledge units"
        )
        if checkpoint.get("knowledge_units"):
            self._report_progress(
                progress_callback, 68, "resuming", "Resuming from saved knowledge units"
            )
            knowledge_units = [
                KnowledgeUnit.model_validate(item) for item in checkpoint["knowledge_units"]
            ]
            warnings = list(checkpoint.get("warnings", []))
        else:
            raw_units = self._source_deck_knowledge_units_from_understandings(understandings)
            knowledge_units, warnings = self._canonicalize_source_deck_knowledge_units(raw_units)
            self._save_source_checkpoint(
                material_id,
                pages,
                phase="knowledge_units",
                knowledge_units=[item.model_dump(mode="json") for item in knowledge_units],
                warnings=warnings,
            )
        organizer = getattr(self.provider, "organize_source_deck_learning_content", None)
        if not organizer:
            raise HTTPException(409, "Source-deck LearningContent organizer is unavailable")
        self._report_progress(
            progress_callback, 78, "reconstructing_deck", "Reconstructing source deck chapters"
        )
        if checkpoint.get("source_draft"):
            source_draft = SourceDeckLearningContentDraft.model_validate(checkpoint["source_draft"])
        else:
            source_draft = organizer(
                material_id=material_id,
                pages=pages,
                understandings=understandings,
                knowledge_units=knowledge_units,
            )
            self._validate_source_deck_draft(source_draft, pages, material_id)
            self._save_source_checkpoint(
                material_id,
                pages,
                phase="source_draft",
                knowledge_units=[item.model_dump(mode="json") for item in knowledge_units],
                warnings=warnings,
                source_draft=source_draft.model_dump(mode="json"),
            )
        if checkpoint.get("source_segments"):
            source_segments = {
                section_id: [TeachingSegment.model_validate(item) for item in items]
                for section_id, items in checkpoint["source_segments"].items()
            }
        else:
            source_segments, segment_warnings = self._build_source_deck_teaching_segments(
                source_draft,
                knowledge_units=knowledge_units,
            )
            warnings.extend(segment_warnings)
            self._save_source_checkpoint(
                material_id,
                pages,
                phase="segments",
                knowledge_units=[item.model_dump(mode="json") for item in knowledge_units],
                warnings=warnings,
                source_draft=source_draft.model_dump(mode="json"),
                source_segments={
                    key: [item.model_dump(mode="json") for item in value]
                    for key, value in source_segments.items()
                },
            )
        draft = self._hydrate_source_deck_draft(
            source_draft,
            understandings=understandings,
            knowledge_units=knowledge_units,
            segments_by_section=source_segments,
        )
        tree, draft = self._build_source_deck_tree(
            tree_id=f"tree_source_{material_id.removeprefix('mat_')}",
            draft=draft,
            units=knowledge_units,
        )
        content_id = f"content_source_{material_id.removeprefix('mat_')}"
        existing = self.repository.get(content_id)
        content = self._content_from_draft(
            content_id=content_id,
            material_id=material_id,
            draft=draft,
            pages=pages,
            created_at=existing.created_at if existing else utc_now(),
            knowledge_units=knowledge_units,
            knowledge_tree=tree,
            quality_warnings=warnings,
            organization_mode="source_deck",
            version=2,
        )
        content = content.model_copy(
            update={
                "quality": self._assess_content_quality(
                    content, expected_material_ids=[material_id]
                )
            }
        )
        self.repository.save(content)
        self._report_progress(progress_callback, 96, "saving", "Source-deck content saved")
        self._clear_source_checkpoint(material_id)
        return content

    @staticmethod
    def _validate_source_deck_draft(
        draft: SourceDeckLearningContentDraft,
        pages: list[PageMetadata],
        material_id: str,
    ) -> None:
        expected = [page.page_no for page in sorted(pages, key=lambda item: item.page_no)]
        actual = [
            ref.page_no
            for section in draft.sections
            for ref in section.page_refs
            if ref.material_id == material_id
        ]
        if actual != expected:
            raise ValueError(
                f"Source-deck LearningContent page coverage mismatch: expected {expected}, got {actual}"
            )
        for section in draft.sections:
            page_nos = [ref.page_no for ref in section.page_refs]
            if page_nos != list(range(page_nos[0], page_nos[-1] + 1)):
                raise ValueError("Source-deck LearningContent sections must use contiguous pages")
        if [item.page_no for item in draft.page_flow] != expected:
            raise ValueError("Source-deck page_flow must describe every source page in order")

    @classmethod
    def _hydrate_source_deck_draft(
        cls,
        draft: SourceDeckLearningContentDraft,
        *,
        understandings: list[PageUnderstanding],
        knowledge_units: list[KnowledgeUnit],
        segments_by_section: dict[int, list[TeachingSegment]] | None = None,
    ) -> LearningContentDraft:
        """Convert the structural source-deck draft into the shared content contract."""
        understanding_by_page = {item.page_no: item for item in understandings}
        sections: list[LearningSectionDraft] = []
        non_quiz_roles = {
            "cover",
            "agenda",
            "section",
            "transition",
            "reference",
            "appendix",
        }
        for section_index, section in enumerate(draft.sections, start=1):
            page_nos = [ref.page_no for ref in section.page_refs]
            page_items = [understanding_by_page[page_no] for page_no in page_nos]
            key_points = (
                section.key_points
                or cls._dedupe_strings(
                    [point for item in page_items for point in item.knowledge_points]
                )[:8]
            )
            quiz_items = []
            for item in page_items:
                if item.page_role in non_quiz_roles:
                    continue
                quiz_items.extend(item.quiz_items)
                if len(quiz_items) >= 2:
                    break
            sections.append(
                LearningSectionDraft(
                    title=section.title,
                    role=section.role,
                    content_goal=section.content_goal,
                    page_nos=page_nos,
                    page_refs=section.page_refs,
                    summary=section.summary or " ".join(item.summary for item in page_items),
                    key_points=key_points,
                    knowledge_points=key_points,
                    teaching_narrative=section.teaching_approach,
                    source_excerpts=[
                        excerpt for item in page_items for excerpt in item.key_excerpts
                    ][:12],
                    formulas=[formula for item in page_items for formula in item.formulas][:8],
                    misconceptions=[
                        misconception
                        for item in page_items
                        for misconception in item.misconceptions
                    ][:8],
                    quiz_items=quiz_items[:2],
                    transition_to_next=section.transition_to_next,
                    segments=(segments_by_section or {}).get(section_index, []),
                )
            )
        return LearningContentDraft(
            title=draft.title,
            subtitle=draft.subtitle,
            objectives=draft.objectives,
            outline=[section.title for section in draft.sections],
            teaching_intent={
                "organization_mode": "source_deck",
                "goal": "按照原稿章节与页面叙事顺序组织课堂内容。",
            },
            material_overview={
                "organization_mode": "source_deck",
                "structure_summary": draft.structure_summary,
                "detected_agenda": draft.detected_agenda,
                "page_flow": [item.model_dump(mode="json") for item in draft.page_flow],
            },
            global_concepts=cls._global_concepts_from_units(knowledge_units),
            generation_guidance={
                "preserve_source_order": True,
                "use_page_flow_for_narration": True,
            },
            sections=sections,
        )

    def _build_source_deck_teaching_segments(
        self,
        draft: SourceDeckLearningContentDraft,
        *,
        knowledge_units: list[KnowledgeUnit],
    ) -> tuple[dict[int, list[TeachingSegment]], list[str]]:
        planner = getattr(self.provider, "plan_source_deck_teaching_segments", None)
        flow_by_page = {item.page_no: item for item in draft.page_flow}
        result: dict[int, list[TeachingSegment]] = {}
        warnings: list[str] = []
        for index, section in enumerate(draft.sections):
            section_pages = [ref.page_no for ref in section.page_refs]
            section_page_set = set(section_pages)
            section_flow = [flow_by_page[page_no] for page_no in section_pages]
            section_units = [
                unit
                for unit in knowledge_units
                if any(ref.page_no in section_page_set for ref in unit.page_refs)
            ]
            section_units_by_id = {unit.id: unit for unit in section_units}
            segments = None
            if planner:
                for attempt in range(2):
                    try:
                        planned = planner(
                            section=section,
                            page_flow=section_flow,
                            knowledge_units=section_units,
                            previous_section_title=(
                                draft.sections[index - 1].title if index > 0 else ""
                            ),
                            next_section_title=(
                                draft.sections[index + 1].title
                                if index + 1 < len(draft.sections)
                                else ""
                            ),
                        )
                        segments = self._hydrate_source_teaching_segments(
                            section,
                            planned,
                            section_index=index + 1,
                            material_id=section.page_refs[0].material_id,
                            knowledge_units_by_id=section_units_by_id,
                        )
                        self._validate_source_teaching_segments(
                            section,
                            segments,
                            knowledge_unit_ids=set(section_units_by_id),
                        )
                        break
                    except (RuntimeError, TimeoutError, ValueError) as exc:
                        if attempt:
                            warnings.append(
                                f"Teaching segments for section '{section.title}' used the "
                                f"deterministic fallback after {type(exc).__name__}: {exc}"
                            )
            if segments is None:
                segments = self._fallback_source_teaching_segments(
                    section,
                    section_index=index + 1,
                    page_flow=section_flow,
                    knowledge_units=section_units,
                )
                self._validate_source_teaching_segments(
                    section,
                    segments,
                    knowledge_unit_ids=set(section_units_by_id),
                )
            result[index + 1] = segments
        return result, warnings

    @staticmethod
    def _hydrate_source_teaching_segments(
        section: SourceDeckSectionDraft,
        draft: SourceDeckTeachingStructureDraft,
        *,
        section_index: int,
        material_id: str,
        knowledge_units_by_id: dict[str, KnowledgeUnit],
    ) -> list[TeachingSegment]:
        if draft.section_title != section.title:
            raise ValueError("Teaching-structure section title does not match")
        title_to_id = {
            item.title: f"segment_{section_index:03d}_{index:02d}"
            for index, item in enumerate(draft.segments, start=1)
        }
        result = []
        for index, item in enumerate(draft.segments, start=1):
            unknown = set(item.knowledge_unit_ids) - set(knowledge_units_by_id)
            if unknown:
                raise ValueError(f"Teaching segment references unknown units: {sorted(unknown)}")
            for unit_id in item.knowledge_unit_ids:
                unit_pages = {ref.page_no for ref in knowledge_units_by_id[unit_id].page_refs}
                if any(
                    page_no < item.start_page or page_no > item.end_page for page_no in unit_pages
                ):
                    raise ValueError(
                        f"Teaching segment splits knowledge unit {unit_id} across boundaries"
                    )
            prerequisites = []
            for title in item.prerequisite_segment_titles:
                prerequisite_id = title_to_id.get(title)
                if not prerequisite_id:
                    raise ValueError(f"Unknown prerequisite segment title: {title}")
                if prerequisite_id >= f"segment_{section_index:03d}_{index:02d}":
                    raise ValueError(
                        "Teaching segment prerequisites must refer to earlier segments"
                    )
                prerequisites.append(prerequisite_id)
            result.append(
                TeachingSegment(
                    id=f"segment_{section_index:03d}_{index:02d}",
                    title=item.title,
                    role=item.role,
                    teaching_goal=item.teaching_goal,
                    summary=item.summary,
                    page_refs=[
                        PageRef(material_id=material_id, page_no=page_no)
                        for page_no in range(item.start_page, item.end_page + 1)
                    ],
                    knowledge_unit_ids=item.knowledge_unit_ids,
                    prerequisite_segment_ids=prerequisites,
                    transition_to_next=item.transition_to_next,
                    suggested_delivery=item.suggested_delivery,
                    order=index,
                )
            )
        return result

    @staticmethod
    def _validate_source_teaching_segments(
        section: SourceDeckSectionDraft,
        segments: list[TeachingSegment],
        *,
        knowledge_unit_ids: set[str],
    ) -> None:
        if not segments:
            raise ValueError("Source-deck section must contain at least one teaching segment")
        expected = [ref.page_no for ref in section.page_refs]
        actual = [ref.page_no for segment in segments for ref in segment.page_refs]
        if actual != expected:
            raise ValueError(
                f"Teaching segments must cover section pages exactly: {actual} != {expected}"
            )
        material_ids = {ref.material_id for ref in section.page_refs}
        seen_unit_ids: set[str] = set()
        normalized_section_title = ContentService._normalize_topic(section.title)
        for index, segment in enumerate(segments):
            page_nos = [ref.page_no for ref in segment.page_refs]
            if not page_nos or page_nos != list(range(page_nos[0], page_nos[-1] + 1)):
                raise ValueError("Teaching segment pages must be contiguous")
            if any(ref.material_id not in material_ids for ref in segment.page_refs):
                raise ValueError("Teaching segment cannot cross its source section")
            if (
                len(segments) > 1
                and ContentService._normalize_topic(segment.title) == normalized_section_title
            ):
                raise ValueError("Teaching segment title must not repeat its parent section")
            unknown = set(segment.knowledge_unit_ids) - knowledge_unit_ids
            if unknown:
                raise ValueError(f"Teaching segment references unknown units: {sorted(unknown)}")
            duplicates = seen_unit_ids & set(segment.knowledge_unit_ids)
            if duplicates:
                raise ValueError(
                    f"Knowledge units cannot belong to multiple segments: {duplicates}"
                )
            seen_unit_ids.update(segment.knowledge_unit_ids)
            valid_prerequisites = {item.id for item in segments[:index]}
            if any(item not in valid_prerequisites for item in segment.prerequisite_segment_ids):
                raise ValueError("Teaching segment prerequisites must refer to earlier segments")

    @classmethod
    def _fallback_source_teaching_segments(
        cls,
        section: SourceDeckSectionDraft,
        *,
        section_index: int,
        page_flow: list,
        knowledge_units: list[KnowledgeUnit],
    ) -> list[TeachingSegment]:
        page_nos = [ref.page_no for ref in section.page_refs]
        if len(page_nos) <= 8:
            ranges = [(page_nos[0], page_nos[-1])]
        else:
            semantic_breaks = {
                item.page_no
                for item in page_flow[1:]
                if item.page_role in {"section", "transition", "summary"}
            }
            unit_starts = {
                min(ref.page_no for ref in unit.page_refs)
                for unit in knowledge_units
                if unit.page_refs
            }
            candidate_breaks = sorted(
                page_no for page_no in semantic_breaks | unit_starts if page_no > page_nos[0]
            )
            unit_ranges = [
                (
                    min(ref.page_no for ref in unit.page_refs),
                    max(ref.page_no for ref in unit.page_refs),
                )
                for unit in knowledge_units
                if unit.page_refs
            ]
            candidate_breaks = [
                page_no
                for page_no in candidate_breaks
                if not any(start < page_no <= end for start, end in unit_ranges)
            ]
            starts = [page_nos[0]]
            current_start = page_nos[0]
            for page_no in candidate_breaks:
                if page_no - current_start >= 3:
                    starts.append(page_no)
                    current_start = page_no
            ranges = [
                (start, starts[index + 1] - 1 if index + 1 < len(starts) else page_nos[-1])
                for index, start in enumerate(starts)
            ]
        result = []
        used_unit_ids: set[str] = set()
        for index, (start_page, end_page) in enumerate(ranges, start=1):
            units = [
                unit
                for unit in knowledge_units
                if unit.id not in used_unit_ids
                and any(start_page <= ref.page_no <= end_page for ref in unit.page_refs)
            ]
            used_unit_ids.update(unit.id for unit in units)
            flow_items = [item for item in page_flow if start_page <= item.page_no <= end_page]
            title = (
                units[0].title
                if units
                else next(
                    (item.content_summary for item in flow_items if item.content_summary),
                    section.title,
                )
            )
            if len(ranges) > 1 and cls._normalize_topic(title) == cls._normalize_topic(
                section.title
            ):
                title = f"{section.title}（第 {start_page}–{end_page} 页）"
            result.append(
                TeachingSegment(
                    id=f"segment_{section_index:03d}_{index:02d}",
                    title=title,
                    role=units[0].unit_type if units else "orientation",
                    teaching_goal=(f"理解并能够说明{title}。" if units else section.content_goal),
                    summary=" ".join(unit.summary for unit in units)[:1200]
                    or " ".join(item.content_summary for item in flow_items)[:1200],
                    page_refs=[
                        PageRef(
                            material_id=section.page_refs[0].material_id,
                            page_no=page_no,
                        )
                        for page_no in range(start_page, end_page + 1)
                    ],
                    knowledge_unit_ids=[unit.id for unit in units],
                    transition_to_next=flow_items[-1].leads_to_next if flow_items else "",
                    suggested_delivery=section.teaching_approach,
                    order=index,
                )
            )
        return result

    @staticmethod
    def _build_source_deck_tree(
        *,
        tree_id: str,
        draft: LearningContentDraft,
        units: list[KnowledgeUnit],
    ) -> tuple[CourseKnowledgeTree, LearningContentDraft]:
        units_by_id = {unit.id: unit for unit in units}
        assigned_unit_ids: set[str] = set()
        nodes: list[CourseKnowledgeTreeNode] = []
        root_ids: list[str] = []
        segment_node_id_by_ref: dict[str, str] = {}
        unit_node_id_by_ref: dict[str, str] = {}
        updated_sections: list[LearningSectionDraft] = []

        for section_index, section in enumerate(draft.sections, start=1):
            section_node_id = f"{tree_id}_section_{section_index:03d}"
            root_ids.append(section_node_id)
            nodes.append(
                CourseKnowledgeTreeNode(
                    id=section_node_id,
                    title=section.title,
                    role=section.role,
                    summary=section.summary,
                    order=section_index,
                    node_type="section",
                    ref_id=f"section_{section_index:03d}",
                    page_refs=section.page_refs,
                )
            )

            for segment_index, segment in enumerate(section.segments, start=1):
                segment_node_id = f"{section_node_id}_segment_{segment_index:03d}"
                segment_node_id_by_ref[segment.id] = segment_node_id
                nodes.append(
                    CourseKnowledgeTreeNode(
                        id=segment_node_id,
                        title=segment.title,
                        role=segment.role,
                        summary=segment.summary,
                        parent_id=section_node_id,
                        order=segment_index,
                        node_type="segment",
                        ref_id=segment.id,
                        page_refs=segment.page_refs,
                    )
                )

            section_page_nos = {ref.page_no for ref in section.page_refs}
            section_units = [
                unit
                for unit in units
                if unit.id not in assigned_unit_ids
                and any(ref.page_no in section_page_nos for ref in unit.page_refs)
            ]
            segment_units: dict[str, list[KnowledgeUnit]] = {
                segment.id: [] for segment in section.segments
            }
            for unit in section_units:
                explicit_segment = next(
                    (
                        segment
                        for segment in section.segments
                        if unit.id in segment.knowledge_unit_ids
                    ),
                    None,
                )
                target_segment = explicit_segment or ContentService._segment_for_unit(
                    unit, section.segments
                )
                if target_segment:
                    segment_units[target_segment.id].append(unit)
                    assigned_unit_ids.add(unit.id)

            for segment in section.segments:
                parent_id = segment_node_id_by_ref[segment.id]
                for unit_index, unit in enumerate(segment_units[segment.id], start=1):
                    unit_node_id = f"{parent_id}_unit_{unit_index:03d}"
                    unit_node_id_by_ref[unit.id] = unit_node_id
                    nodes.append(
                        CourseKnowledgeTreeNode(
                            id=unit_node_id,
                            title=unit.title,
                            role=unit.unit_type,
                            summary=unit.summary,
                            parent_id=parent_id,
                            knowledge_unit_ids=[unit.id],
                            order=unit_index,
                            node_type="knowledge_unit",
                            ref_id=unit.id,
                            page_refs=unit.page_refs,
                        )
                    )
            updated_sections.append(section.model_copy(update={"tree_node_ids": [section_node_id]}))

        missing_unit_ids = set(units_by_id) - assigned_unit_ids
        if missing_unit_ids:
            raise ValueError(
                "Source-deck knowledge units do not map to any teaching segment: "
                f"{sorted(missing_unit_ids)}"
            )

        prerequisite_updates: dict[str, list[str]] = {}
        for section in draft.sections:
            for segment in section.segments:
                node_id = segment_node_id_by_ref[segment.id]
                prerequisite_updates[node_id] = [
                    segment_node_id_by_ref[prerequisite_id]
                    for prerequisite_id in segment.prerequisite_segment_ids
                ]
        for unit in units:
            node_id = unit_node_id_by_ref[unit.id]
            prerequisite_updates[node_id] = [
                unit_node_id_by_ref[relation.target_unit_id]
                for relation in unit.relations
                if relation.relation_type in {"prerequisite", "depends_on", "builds_on"}
                and relation.target_unit_id in unit_node_id_by_ref
            ]
        nodes = [
            node.model_copy(update={"prerequisite_node_ids": prerequisite_updates.get(node.id, [])})
            for node in nodes
        ]

        updated_draft = draft.model_copy(update={"sections": updated_sections})
        tree = CourseKnowledgeTree(
            id=tree_id,
            title=draft.title,
            nodes=nodes,
            root_node_ids=root_ids,
            teaching_sequence=[
                segment_node_id_by_ref[segment.id]
                for section in draft.sections
                for segment in section.segments
            ],
        )
        ContentService._validate_source_deck_tree(tree, updated_draft, units)
        return tree, updated_draft

    @staticmethod
    def _segment_for_unit(
        unit: KnowledgeUnit, segments: list[TeachingSegment]
    ) -> TeachingSegment | None:
        unit_pages = {ref.page_no for ref in unit.page_refs}
        ranked = sorted(
            segments,
            key=lambda segment: (
                -len(unit_pages & {ref.page_no for ref in segment.page_refs}),
                segment.order,
            ),
        )
        return (
            ranked[0]
            if ranked and unit_pages & {ref.page_no for ref in ranked[0].page_refs}
            else None
        )

    @staticmethod
    def _validate_source_deck_tree(
        tree: CourseKnowledgeTree,
        draft: LearningContentDraft,
        units: list[KnowledgeUnit],
    ) -> None:
        node_by_id = {node.id: node for node in tree.nodes}
        if len(node_by_id) != len(tree.nodes):
            raise ValueError("Source-deck tree contains duplicate node ids")
        section_nodes = [node for node in tree.nodes if node.node_type == "section"]
        if tree.root_node_ids != [node.id for node in section_nodes]:
            raise ValueError("Source-deck tree roots must match LearningContent sections")
        if len(section_nodes) != len(draft.sections):
            raise ValueError("Source-deck tree must contain one node per section")
        for section, section_node in zip(draft.sections, section_nodes, strict=True):
            if (
                section_node.ref_id not in section.tree_node_ids
                and section_node.id not in section.tree_node_ids
            ):
                raise ValueError("LearningContent section does not reference its tree node")
            if (
                section_node.title != section.title
                or section_node.summary != section.summary
                or section_node.page_refs != section.page_refs
            ):
                raise ValueError("Section tree node differs from LearningContent")
            segment_nodes = [
                node
                for node in tree.nodes
                if node.parent_id == section_node.id and node.node_type == "segment"
            ]
            if len(segment_nodes) != len(section.segments):
                raise ValueError("Source-deck tree must contain one node per teaching segment")
            for segment, segment_node in zip(section.segments, segment_nodes, strict=True):
                if (
                    segment_node.ref_id != segment.id
                    or segment_node.title != segment.title
                    or segment_node.summary != segment.summary
                    or segment_node.page_refs != segment.page_refs
                ):
                    raise ValueError("Segment tree node differs from LearningContent")
        unit_nodes = [node for node in tree.nodes if node.node_type == "knowledge_unit"]
        covered = [node.ref_id for node in unit_nodes]
        if len(covered) != len(set(covered)) or set(covered) != {unit.id for unit in units}:
            raise ValueError("Source-deck tree must cover every knowledge unit exactly once")
        for node in unit_nodes:
            unit = next(unit for unit in units if unit.id == node.ref_id)
            if (
                node.title != unit.title
                or node.summary != unit.summary
                or node.page_refs != unit.page_refs
            ):
                raise ValueError("Knowledge-unit tree node differs from its source object")
            page_nos = [ref.page_no for ref in node.page_refs]
            if not page_nos or page_nos != list(range(page_nos[0], page_nos[-1] + 1)):
                raise ValueError("Knowledge-unit tree node pages must be contiguous")
            parent = node_by_id[node.parent_id]
            parent_pages = {ref.page_no for ref in parent.page_refs}
            if any(page_no not in parent_pages for page_no in page_nos):
                raise ValueError("Knowledge-unit tree node must stay inside its segment")

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
        return self._build_from_materials(
            content_id=f"content_{collection_id.removeprefix('col_')}",
            material_ids=material_ids,
            primary_material_id=primary_material_id,
            collection_id=collection_id,
            page_groups=page_groups,
            progress_callback=progress_callback,
        )

    def _build_from_materials(
        self,
        *,
        content_id: str,
        material_ids: list[str],
        primary_material_id: str,
        collection_id: str | None,
        page_groups: list[tuple[str, list[PageMetadata]]],
        progress_callback: ProgressCallback | None,
    ) -> LearningContent:
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
        self._report_progress(
            progress_callback,
            78,
            "building_knowledge_tree",
            "Building the course knowledge tree",
        )
        title = next((page.title for page in all_pages if page.title), "Course Knowledge")
        knowledge_tree, tree_warnings = self._build_course_knowledge_tree(
            tree_id=f"tree_{content_id.removeprefix('content_')}",
            title=title,
            units=knowledge_units,
        )
        quality_warnings = canonicalization_warnings + tree_warnings

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
                    collection_id=collection_id or f"single_{primary_material_id}",
                    material_ids=material_ids,
                    pages=all_pages,
                    understandings=understandings,
                    knowledge_units=knowledge_units,
                    knowledge_tree=knowledge_tree,
                )
                self._validate_draft_tree_coverage(draft, knowledge_tree)
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
                    knowledge_tree=knowledge_tree,
                    quality_warnings=quality_warnings,
                )
                content = content.model_copy(
                    update={
                        "quality": self._assess_content_quality(
                            content,
                            expected_material_ids=material_ids,
                        )
                    }
                )
                self.repository.save(content)
                self._report_progress(progress_callback, 96, "saving", "Learning content saved")
                return content
            except ContentGenerationPaused:
                raise
            except Exception as exc:
                logger.warning("Collection learning-content organizer failed: %s", exc)
                canonicalization_warnings.append(
                    "The global LLM organizer failed; knowledge-tree sections were used."
                )

        self._report_progress(
            progress_callback,
            84,
            "organizing",
            "Organizing deterministic teaching units",
        )
        content = self._fallback_tree_content(
            content_id=content_id,
            collection_id=collection_id,
            primary_material_id=primary_material_id,
            material_ids=material_ids,
            knowledge_units=knowledge_units,
            knowledge_tree=knowledge_tree,
            understandings=understandings,
            quality_warnings=quality_warnings + canonicalization_warnings,
        )
        content = content.model_copy(
            update={
                "quality": self._assess_content_quality(
                    content,
                    expected_material_ids=material_ids,
                )
            }
        )
        self.repository.save(content)
        self._report_progress(progress_callback, 96, "saving", "Learning content saved")
        return content

    def _fallback_tree_content(
        self,
        *,
        content_id: str,
        collection_id: str | None,
        primary_material_id: str,
        material_ids: list[str],
        knowledge_units: list[KnowledgeUnit],
        knowledge_tree: CourseKnowledgeTree,
        understandings: list[PageUnderstanding] | None = None,
        quality_warnings: list[str],
    ) -> LearningContent:
        sections = self._sections_from_knowledge_tree(
            knowledge_tree,
            knowledge_units,
            understandings=understandings,
        )

        if not sections:
            raise HTTPException(409, "No page understanding is available")
        existing = self.repository.get(content_id)
        return LearningContent(
            id=content_id,
            material_id=primary_material_id,
            material_ids=material_ids,
            collection_id=collection_id,
            title=knowledge_tree.title,
            subtitle=(
                "Multi-material learning content"
                if len(material_ids) > 1
                else "Course learning content"
            ),
            objectives=[f"Understand {section.title}" for section in sections[:3]],
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
            knowledge_tree=knowledge_tree,
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

    def _build_course_knowledge_tree(
        self,
        *,
        tree_id: str,
        title: str,
        units: list[KnowledgeUnit],
    ) -> tuple[CourseKnowledgeTree, list[str]]:
        builder = getattr(self.provider, "build_course_knowledge_tree", None)
        if builder:
            try:
                tree = builder(tree_id=tree_id, title=title, units=units)
                tree = self._complete_course_knowledge_tree(tree, units)
                self._validate_course_knowledge_tree(tree, units)
                return tree, []
            except Exception as exc:
                logger.warning("Course knowledge-tree generation failed: %s", exc)
                warning = (
                    "LLM course knowledge-tree generation failed; deterministic teaching "
                    "chapters were used."
                )
                return self._fallback_course_knowledge_tree(tree_id, title, units), [warning]
        return self._fallback_course_knowledge_tree(tree_id, title, units), []

    def _complete_course_knowledge_tree(
        self,
        tree: CourseKnowledgeTree,
        units: list[KnowledgeUnit],
    ) -> CourseKnowledgeTree:
        expected_unit_ids = {unit.id for unit in units}
        seen_unit_ids: set[str] = set()
        nodes = []
        warnings = list(tree.warnings)
        for node in tree.nodes:
            valid_unit_ids = []
            for unit_id in node.knowledge_unit_ids:
                if unit_id not in expected_unit_ids or unit_id in seen_unit_ids:
                    continue
                valid_unit_ids.append(unit_id)
                seen_unit_ids.add(unit_id)
            nodes.append(node.model_copy(update={"knowledge_unit_ids": valid_unit_ids}))

        missing_units = [unit for unit in units if unit.id not in seen_unit_ids]
        if missing_units:
            existing_node_ids = {node.id for node in nodes}
            root_id = f"{tree.id}_supplementary"
            suffix = 1
            while root_id in existing_node_ids:
                suffix += 1
                root_id = f"{tree.id}_supplementary_{suffix}"
            root_node_ids = list(tree.root_node_ids)
            root_node_ids.append(root_id)
            nodes.append(
                CourseKnowledgeTreeNode(
                    id=root_id,
                    title="Supplementary Knowledge",
                    role="concept",
                    summary=(
                        "Knowledge units added by the backend because the LLM tree omitted them."
                    ),
                    order=len(root_node_ids),
                )
            )
            existing_node_ids.add(root_id)
            for chunk_index in range(0, len(missing_units), MAX_UNITS_PER_TOPIC_NODE):
                chunk = missing_units[chunk_index : chunk_index + MAX_UNITS_PER_TOPIC_NODE]
                child_id = f"{root_id}_topic_{chunk_index // MAX_UNITS_PER_TOPIC_NODE + 1:02d}"
                nodes.append(
                    CourseKnowledgeTreeNode(
                        id=child_id,
                        title=chunk[0].title if len(chunk) == 1 else "Supplementary Topic",
                        role=chunk[0].unit_type,
                        summary=" ".join(unit.summary for unit in chunk)[:1200],
                        parent_id=root_id,
                        knowledge_unit_ids=[unit.id for unit in chunk],
                        order=chunk_index // MAX_UNITS_PER_TOPIC_NODE + 1,
                    )
                )
            warnings.append(
                "The LLM course knowledge tree omitted some knowledge units; supplementary "
                "nodes were added automatically."
            )
            return tree.model_copy(
                update={
                    "nodes": nodes,
                    "root_node_ids": root_node_ids,
                    "teaching_sequence": list(tree.teaching_sequence or []) + [root_id],
                    "warnings": self._dedupe_strings(warnings),
                }
            )

        return tree.model_copy(update={"nodes": nodes, "warnings": self._dedupe_strings(warnings)})

    def _fallback_course_knowledge_tree(
        self,
        tree_id: str,
        title: str,
        units: list[KnowledgeUnit],
    ) -> CourseKnowledgeTree:
        categories = [
            ("foundations", "Foundations and Core Concepts", {"motivation", "concept"}),
            ("methods", "Methods and Formulas", {"method", "formula"}),
            ("applications", "Examples and Applications", {"example", "case", "comparison"}),
            ("summary", "Summary and References", {"summary", "reference"}),
        ]
        nodes: list[CourseKnowledgeTreeNode] = []
        root_node_ids: list[str] = []
        assigned_ids: set[str] = set()
        previous_root_id: str | None = None

        for category_key, category_title, roles in categories:
            category_units = [unit for unit in units if unit.unit_type in roles]
            if not category_units:
                continue
            root_id = f"{tree_id}_{category_key}"
            root_node_ids.append(root_id)
            nodes.append(
                CourseKnowledgeTreeNode(
                    id=root_id,
                    title=category_title,
                    role=category_units[0].unit_type,
                    summary=" ".join(unit.summary for unit in category_units)[:1200],
                    order=len(root_node_ids),
                    prerequisite_node_ids=[previous_root_id] if previous_root_id else [],
                )
            )
            previous_root_id = root_id
            for chunk_index in range(0, len(category_units), MAX_UNITS_PER_TOPIC_NODE):
                chunk = category_units[chunk_index : chunk_index + MAX_UNITS_PER_TOPIC_NODE]
                child_index = chunk_index // MAX_UNITS_PER_TOPIC_NODE + 1
                child_id = f"{root_id}_topic_{child_index:02d}"
                nodes.append(
                    CourseKnowledgeTreeNode(
                        id=child_id,
                        title=(
                            chunk[0].title if len(chunk) == 1 else f"{category_title} {child_index}"
                        ),
                        role=chunk[0].unit_type,
                        summary=" ".join(unit.summary for unit in chunk)[:1200],
                        parent_id=root_id,
                        knowledge_unit_ids=[unit.id for unit in chunk],
                        order=child_index,
                    )
                )
                assigned_ids.update(unit.id for unit in chunk)

        unassigned = [unit for unit in units if unit.id not in assigned_ids]
        if unassigned:
            root_id = f"{tree_id}_additional"
            root_node_ids.append(root_id)
            nodes.append(
                CourseKnowledgeTreeNode(
                    id=root_id,
                    title="Additional Knowledge",
                    role="concept",
                    summary="Additional units that do not match the standard teaching roles.",
                    knowledge_unit_ids=[unit.id for unit in unassigned],
                    order=len(root_node_ids),
                    prerequisite_node_ids=[previous_root_id] if previous_root_id else [],
                )
            )

        return CourseKnowledgeTree(
            id=tree_id,
            title=title,
            nodes=nodes,
            root_node_ids=root_node_ids,
            teaching_sequence=root_node_ids,
        )

    @staticmethod
    def _validate_course_knowledge_tree(
        tree: CourseKnowledgeTree,
        units: list[KnowledgeUnit],
    ) -> None:
        node_ids = [node.id for node in tree.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Course knowledge tree contains duplicate node ids")
        node_id_set = set(node_ids)
        if any(root_id not in node_id_set for root_id in tree.root_node_ids):
            raise ValueError("Course knowledge tree references an unknown root node")
        for node in tree.nodes:
            if node.parent_id and node.parent_id not in node_id_set:
                raise ValueError(f"Tree node {node.id} references an unknown parent")
            if any(item not in node_id_set for item in node.prerequisite_node_ids):
                raise ValueError(f"Tree node {node.id} references an unknown prerequisite")
            if len(node.knowledge_unit_ids) > MAX_UNITS_PER_TOPIC_NODE:
                raise ValueError(
                    f"Tree node {node.id} contains too many knowledge units for one topic"
                )

        expected_unit_ids = {unit.id for unit in units}
        assigned_unit_ids = [unit_id for node in tree.nodes for unit_id in node.knowledge_unit_ids]
        if any(unit_id not in expected_unit_ids for unit_id in assigned_unit_ids):
            raise ValueError("Course knowledge tree references an unknown knowledge unit")
        if len(assigned_unit_ids) != len(set(assigned_unit_ids)):
            raise ValueError("A knowledge unit appears in more than one tree node")
        if set(assigned_unit_ids) != expected_unit_ids:
            raise ValueError("Course knowledge tree does not cover every knowledge unit")

    @staticmethod
    def _validate_draft_tree_coverage(
        draft: LearningContentDraft,
        tree: CourseKnowledgeTree,
    ) -> None:
        node_by_id = {node.id: node for node in tree.nodes}
        required_node_ids = {node.id for node in tree.nodes if node.knowledge_unit_ids}
        section_node_ids = [
            node_id for section in draft.sections for node_id in section.tree_node_ids
        ]
        if not section_node_ids:
            raise ValueError("LearningContent sections do not reference the course knowledge tree")
        if any(not section.page_refs for section in draft.sections):
            raise ValueError("LearningContent sections must retain source page references")
        if any(node_id not in required_node_ids for node_id in section_node_ids):
            raise ValueError("LearningContent must reference knowledge-bearing topic nodes")
        if len(section_node_ids) != len(set(section_node_ids)):
            raise ValueError("A course knowledge-tree topic appears in more than one section")
        if set(section_node_ids) != required_node_ids:
            raise ValueError("LearningContent does not cover every knowledge-bearing topic")
        for section in draft.sections:
            if len(section.tree_node_ids) > MAX_TREE_NODES_PER_SECTION:
                raise ValueError("LearningContent section merges too many teaching topics")
            unit_count = sum(
                len(node_by_id[node_id].knowledge_unit_ids) for node_id in section.tree_node_ids
            )
            if unit_count > MAX_UNITS_PER_TOPIC_NODE:
                raise ValueError("LearningContent section contains too many knowledge units")

    def _sections_from_knowledge_tree(
        self,
        tree: CourseKnowledgeTree,
        units: list[KnowledgeUnit],
        *,
        understandings: list[PageUnderstanding] | None = None,
    ) -> list[LearningSection]:
        unit_by_id = {unit.id: unit for unit in units}
        children_by_parent: dict[str, list[CourseKnowledgeTreeNode]] = {}
        for node in tree.nodes:
            if node.parent_id:
                children_by_parent.setdefault(node.parent_id, []).append(node)
        node_by_id = {node.id: node for node in tree.nodes}
        sequence_rank = {node_id: index for index, node_id in enumerate(tree.teaching_sequence)}

        def node_order(node: CourseKnowledgeTreeNode) -> tuple[int, int, str]:
            return (sequence_rank.get(node.id, len(sequence_rank)), node.order, node.id)

        teaching_nodes: list[CourseKnowledgeTreeNode] = []

        def visit(node_id: str) -> None:
            node = node_by_id[node_id]
            if node.knowledge_unit_ids:
                teaching_nodes.append(node)
            for child in sorted(children_by_parent.get(node_id, []), key=node_order):
                visit(child.id)

        for root_id in sorted(
            tree.root_node_ids,
            key=lambda node_id: node_order(node_by_id[node_id]),
        ):
            visit(root_id)

        sections = []
        for index, topic_node in enumerate(teaching_nodes, start=1):
            section_units = [
                unit_by_id[unit_id]
                for unit_id in topic_node.knowledge_unit_ids
                if unit_id in unit_by_id
            ]
            if not section_units:
                continue
            next_title = teaching_nodes[index].title if index < len(teaching_nodes) else ""
            source_refs = self._dedupe_source_refs(
                [ref for unit in section_units for ref in unit.source_refs]
            )
            quiz_items = self._quiz_items_from_understandings(
                index,
                source_refs,
                understandings or [],
            )
            if not quiz_items:
                quiz_unit = next(
                    (unit for unit in section_units if unit.keywords and unit.source_refs),
                    None,
                )
                if quiz_unit:
                    point = quiz_unit.keywords[0]
                    quiz_items = [
                        QuizItem(
                            id=f"quiz_{index:03d}_01",
                            question=f"关于“{point}”，下列哪一项最能体现理解到位？",
                            options=[
                                f"能够解释 {point} 的含义、适用条件或具体例子",
                                f"只记住 {point} 在材料中出现过",
                                f"把 {point} 与所有相关概念无条件混用",
                            ],
                            correct_index=0,
                            explanation=(
                                "理解一个知识点，需要能说明它解决的问题、适用条件和使用边界。"
                            ),
                            knowledge_point=point,
                            source_refs=quiz_unit.source_refs,
                        )
                    ]
            sections.append(
                LearningSection(
                    id=f"section_{index:03d}",
                    title=topic_node.title,
                    role=topic_node.role,
                    content_goal=f"Teach the connected knowledge in {topic_node.title}",
                    summary=topic_node.summary
                    or " ".join(unit.summary for unit in section_units)[:1600],
                    key_points=self._dedupe_strings(
                        [keyword for unit in section_units for keyword in unit.keywords]
                    )[:10],
                    teaching_narrative="\n\n".join(
                        self._teaching_narrative_from_unit(unit) for unit in section_units
                    )[:4000],
                    knowledge_points=self._dedupe_strings(
                        [keyword for unit in section_units for keyword in unit.keywords]
                    )[:12],
                    source_excerpts=[
                        excerpt for unit in section_units for excerpt in unit.source_excerpts
                    ][:12],
                    formulas=[formula for unit in section_units for formula in unit.formulas],
                    examples=[example for unit in section_units for example in unit.examples],
                    misconceptions=[item for unit in section_units for item in unit.misconceptions],
                    transition={"to_next": f"Next, move to {next_title}." if next_title else ""},
                    source_refs=source_refs,
                    page_refs=self._dedupe_page_refs(
                        [ref for unit in section_units for ref in unit.page_refs]
                    ),
                    tree_node_ids=[topic_node.id],
                    quiz_items=quiz_items,
                )
            )
        return sections

    @staticmethod
    def _quiz_items_from_understandings(
        section_index: int,
        source_refs: list,
        understandings: list[PageUnderstanding],
    ) -> list[QuizItem]:
        if not source_refs or not understandings:
            return []
        understanding_by_page = {(item.material_id, item.page_no): item for item in understandings}
        result = []
        seen_questions = set()
        for ref in source_refs:
            understanding = understanding_by_page.get((ref.material_id, ref.page_no))
            if not understanding:
                continue
            for quiz_index, draft in enumerate(understanding.quiz_items, start=1):
                question = draft.question.strip()
                if not question or question in seen_questions:
                    continue
                seen_questions.add(question)
                result.append(
                    QuizItem(
                        id=f"quiz_{section_index:03d}_{len(result) + 1:02d}",
                        question=draft.question,
                        options=draft.options,
                        correct_index=draft.correct_index,
                        explanation=draft.explanation,
                        knowledge_point=draft.knowledge_point,
                        source_refs=source_refs,
                    )
                )
                if len(result) >= 2:
                    return result
        return result

    def _assess_content_quality(
        self,
        content: LearningContent,
        *,
        expected_material_ids: list[str],
    ) -> dict:
        quality = dict(content.quality)
        warnings = list(quality.get("warnings", []))
        covered_material_ids = {
            ref.material_id for section in content.sections for ref in section.source_refs
        }
        missing_material_ids = sorted(set(expected_material_ids) - covered_material_ids)
        if missing_material_ids:
            warnings.append(
                "Some source materials are not represented in sections: "
                + ", ".join(missing_material_ids)
            )

        normalized_titles = [self._normalize_topic(section.title) for section in content.sections]
        duplicate_titles = sorted(
            {title for title in normalized_titles if normalized_titles.count(title) > 1}
        )
        if duplicate_titles:
            warnings.append("Duplicate LearningContent section titles were detected.")

        sections_without_sources = [
            section.id for section in content.sections if not section.source_refs
        ]
        if sections_without_sources:
            warnings.append("Some LearningContent sections have no source evidence.")

        tree_unit_ids = {
            unit_id
            for node in (content.knowledge_tree.nodes if content.knowledge_tree else [])
            for unit_id in node.knowledge_unit_ids
        }
        expected_unit_ids = {unit.id for unit in content.knowledge_units}
        orphan_unit_ids = sorted(expected_unit_ids - tree_unit_ids)
        if orphan_unit_ids:
            warnings.append("Some knowledge units are not assigned to the course knowledge tree.")

        tree_node_by_id = {
            node.id: node
            for node in (content.knowledge_tree.nodes if content.knowledge_tree else [])
        }
        section_unit_counts = {
            section.id: sum(
                len(tree_node_by_id[node_id].knowledge_unit_ids)
                for node_id in section.tree_node_ids
                if node_id in tree_node_by_id
            )
            for section in content.sections
        }
        overloaded_section_ids = sorted(
            section_id
            for section_id, unit_count in section_unit_counts.items()
            if unit_count > MAX_UNITS_PER_TOPIC_NODE
        )
        recommended_min_section_count = (
            (len(expected_unit_ids) + MAX_UNITS_PER_TOPIC_NODE - 1) // MAX_UNITS_PER_TOPIC_NODE
            if expected_unit_ids
            else 0
        )
        if overloaded_section_ids:
            warnings.append("Some LearningContent sections contain too many knowledge units.")
        if len(content.sections) < recommended_min_section_count:
            warnings.append("LearningContent may be over-compressed for the available knowledge.")

        low_confidence_unit_ids = sorted(
            unit.id for unit in content.knowledge_units if unit.confidence < 0.7
        )
        low_confidence_relations = sum(
            1
            for unit in content.knowledge_units
            for relation in unit.relations
            if relation.confidence < 0.7
        )
        conflicts = sum(
            1
            for unit in content.knowledge_units
            for relation in unit.relations
            if relation.relation_type == "contrasts_with"
        )
        if low_confidence_unit_ids or low_confidence_relations:
            warnings.append("Low-confidence knowledge merges or relations require review.")
        if conflicts:
            warnings.append("Cross-document knowledge conflicts require review.")

        material_coverage = (
            len(covered_material_ids & set(expected_material_ids)) / len(expected_material_ids)
            if expected_material_ids
            else 1.0
        )
        knowledge_coverage = (
            len(tree_unit_ids & expected_unit_ids) / len(expected_unit_ids)
            if expected_unit_ids
            else 1.0
        )
        quality.update(
            {
                "coverage_score": round((material_coverage + knowledge_coverage) / 2, 3),
                "material_coverage": round(material_coverage, 3),
                "knowledge_coverage": round(knowledge_coverage, 3),
                "missing_material_ids": missing_material_ids,
                "orphan_unit_ids": orphan_unit_ids,
                "duplicate_section_titles": duplicate_titles,
                "sections_without_sources": sections_without_sources,
                "section_unit_counts": section_unit_counts,
                "overloaded_section_ids": overloaded_section_ids,
                "recommended_min_section_count": recommended_min_section_count,
                "low_confidence_unit_ids": low_confidence_unit_ids,
                "low_confidence_relation_count": low_confidence_relations,
                "conflict_count": conflicts,
                "warnings": self._dedupe_strings(warnings),
            }
        )
        return quality

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

    def _source_deck_knowledge_units_from_understandings(
        self, understandings: list[PageUnderstanding]
    ) -> list[KnowledgeUnit]:
        """Extract teaching units while excluding source-deck navigation pages."""
        excluded_roles = {"cover", "agenda", "section", "transition", "appendix"}
        return self._knowledge_units_from_understandings(
            [item for item in understandings if item.page_role not in excluded_roles]
        )

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

    def _canonicalize_source_deck_knowledge_units(
        self, units: list[KnowledgeUnit]
    ) -> tuple[list[KnowledgeUnit], list[str]]:
        preliminary = self._merge_source_deck_knowledge_units(units)
        canonicalizer = getattr(self.provider, "canonicalize_source_deck_knowledge_units", None)
        if not canonicalizer or len(preliminary) < 2:
            return preliminary, []
        try:
            draft = canonicalizer(preliminary)
            self._validate_source_deck_canonical_groups(preliminary, draft)
            return self._apply_canonicalization(preliminary, draft), []
        except Exception as exc:
            logger.warning("Source-deck knowledge-unit canonicalization failed: %s", exc)
            return preliminary, [
                "Source-deck semantic canonicalization failed; contiguous local units were used."
            ]

    def _merge_source_deck_knowledge_units(self, units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
        """Locally merge only same-topic units on contiguous pages of one material."""
        ordered = sorted(units, key=self._knowledge_unit_source_order)
        groups: list[list[KnowledgeUnit]] = []
        for unit in ordered:
            if not groups:
                groups.append([unit])
                continue
            current = groups[-1]
            if self._source_units_can_merge(current[-1], unit):
                current.append(unit)
            else:
                groups.append([unit])
        return [
            self._merge_unit_group(items, unit_id=f"ku_source_{index:03d}")
            for index, items in enumerate(groups, start=1)
        ]

    @classmethod
    def _source_units_can_merge(cls, left: KnowledgeUnit, right: KnowledgeUnit) -> bool:
        if left.unit_type != right.unit_type:
            return False
        left_locations = {(ref.material_id, ref.page_no) for ref in left.page_refs}
        right_locations = {(ref.material_id, ref.page_no) for ref in right.page_refs}
        if not left_locations or not right_locations:
            return False
        adjacent = any(
            left_material == right_material and right_page == left_page + 1
            for left_material, left_page in left_locations
            for right_material, right_page in right_locations
        )
        return adjacent and cls._topic_similarity(left, right) >= 0.82

    @staticmethod
    def _knowledge_unit_source_order(unit: KnowledgeUnit) -> tuple[str, int, str]:
        if not unit.page_refs:
            return ("", 0, unit.id)
        first = min(unit.page_refs, key=lambda ref: (ref.material_id, ref.page_no))
        return (first.material_id, first.page_no, unit.id)

    @staticmethod
    def _validate_source_deck_canonical_groups(
        units: list[KnowledgeUnit], draft: KnowledgeCanonicalizationDraft
    ) -> None:
        unit_by_id = {unit.id: unit for unit in units}
        for group in draft.groups:
            items = [unit_by_id[unit_id] for unit_id in group.unit_ids if unit_id in unit_by_id]
            locations = sorted(
                {(ref.material_id, ref.page_no) for item in items for ref in item.page_refs}
            )
            material_ids = {material_id for material_id, _ in locations}
            if len(material_ids) > 1:
                raise ValueError("Source-deck knowledge groups cannot cross materials")
            page_nos = [page_no for _, page_no in locations]
            if page_nos and page_nos != list(range(page_nos[0], page_nos[-1] + 1)):
                raise ValueError("Source-deck knowledge groups must use contiguous pages")

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
            "exercise": "practice",
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
            return f"{unit.summary}\n\n{evidence}"[:2000]
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
        knowledge_tree: CourseKnowledgeTree | None = None,
        quality_warnings: list[str] | None = None,
        organization_mode: str = "knowledge",
        version: int = 1,
    ) -> LearningContent:
        page_by_no = {page.page_no: page for page in pages}
        page_by_source = {(page.material_id, page.page_no): page for page in pages}
        sections = []
        for index, section in enumerate(draft.sections, start=1):
            if section.page_refs:
                section_pages = [
                    page_by_source.get((page_ref.material_id, page_ref.page_no), pages[0])
                    for page_ref in section.page_refs
                ]
            else:
                section_pages = [
                    page_by_no.get(page_no, page_by_no[pages[0].page_no])
                    for page_no in section.page_nos
                ]
            source_refs = [ref for page in section_pages for ref in page.source_refs]
            if not source_refs:
                source_refs = pages[0].source_refs
            valid_image_paths = {
                image.image_path for page in section_pages for image in page.embedded_images
            }
            visual_opportunities = self._hydrate_visual_opportunities(
                section.visual_opportunities,
                source_refs,
                valid_image_paths,
            )
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
                    visual_opportunities=visual_opportunities,
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
                    tree_node_ids=section.tree_node_ids,
                    quiz_items=quiz_items,
                    page_nos=section.page_nos,
                    outline_level=1,
                    teaching_script=section.teaching_script,
                    visual_summary=section.visual_summary,
                    transition_to_next=section.transition_to_next,
                    segments=section.segments,
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
            organization_mode=organization_mode,
            title=draft.title,
            subtitle=draft.subtitle,
            audience=draft.audience,
            teaching_intent=draft.teaching_intent,
            material_overview=draft.material_overview,
            global_concepts=draft.global_concepts,
            knowledge_units=knowledge_units or [],
            knowledge_tree=knowledge_tree,
            objectives=draft.objectives or draft.outline,
            sections=sections,
            generation_guidance=draft.generation_guidance,
            quality=quality,
            version=version,
            created_at=created_at,
            updated_at=utc_now(),
        )

    @staticmethod
    def _hydrate_visual_opportunities(
        opportunities: list[VisualOpportunity],
        source_refs: list,
        valid_image_paths: set[str],
    ) -> list[VisualOpportunity]:
        hydrated = []
        for index, opportunity in enumerate(opportunities, start=1):
            refs = opportunity.source_refs or source_refs
            image_path = (
                opportunity.image_path if opportunity.image_path in valid_image_paths else None
            )
            image_description = (
                opportunity.image_description
                or opportunity.description
                or "Embedded source image selected from the material."
            )
            hydrated.append(
                opportunity.model_copy(
                    update={
                        "id": opportunity.id or f"visual_{index:02d}",
                        "image_path": image_path,
                        "image_description": image_description,
                        "source_refs": refs,
                    }
                )
            )
        if hydrated:
            return hydrated
        return []

    def get(self, content_id: str) -> LearningContent:
        content = self.repository.get(content_id)
        if not content:
            raise HTTPException(404, "Learning content not found")
        return content

    def list_material_summaries(self) -> list[MaterialLearningContentSummary]:
        return self.repository.list_material_summaries()

    def get_knowledge_tree(self, content_id: str) -> CourseKnowledgeTree:
        content = self.get(content_id)
        if not content.knowledge_tree:
            raise HTTPException(404, "Course knowledge tree not found")
        return content.knowledge_tree

    def get_diagnostics(self, content_id: str) -> LearningContentDiagnostics:
        content = self.get(content_id)
        return LearningContentDiagnostics(
            content_id=content.id,
            knowledge_units=content.knowledge_units,
            knowledge_tree=content.knowledge_tree,
            quality=content.quality,
        )

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
