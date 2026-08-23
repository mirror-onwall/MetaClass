from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import Field, ValidationError

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import LearningContent, LearningSection, TeachingSegment
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.presentation.layout_registry import (
    build_fallback_elements,
    registry_prompt_payload,
    select_fallback_layout,
    split_points_for_layout,
)
from metaclass.modules.presentation.layout_constraints import (
    ACADEMIC_LAYOUT_GUIDANCE,
    DEFAULT_LAYOUT_CONSTRAINTS,
)
from metaclass.modules.presentation.brand_palette import (
    BRAND_PALETTE,
    apply_brand_palette,
)
from metaclass.modules.presentation.schemas import PresentationPlan, SlideElement, SlidePlan


class SlidePlanDraft(SchemaModel):
    source_section_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    speaker_script: str = Field(min_length=1)
    suggested_visual: str = Field(min_length=1)
    layout: str = "freeform"
    visual_payload: list[str] = Field(default_factory=list)
    background: str = "F7F9F7"
    elements: list[SlideElement] = Field(default_factory=list)


class PresentationPlanDraft(SchemaModel):
    title: str = Field(min_length=1)
    slides: list[SlidePlanDraft] = Field(min_length=1)


class SourceSlideNarrationDraft(SchemaModel):
    page_no: int = Field(ge=1)
    title: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    speaker_script: str = Field(min_length=1)


class SourceSlideNarrationBatch(SchemaModel):
    slides: list[SourceSlideNarrationDraft] = Field(min_length=1)


class SlideSceneDraft(SchemaModel):
    background: str = Field(pattern=r"^[0-9A-Fa-f]{6}$")
    elements: list[SlideElement] = Field(min_length=1, max_length=40)


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str, str], None]
NarrationCheckpointCallback = Callable[[str, SourceSlideNarrationBatch], None]


class PresentationPlanGenerator:
    """Generate the shared slide/script layer from LearningContent."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm
        self.skill_path = Path(__file__).with_name("pptx_skill") / "SKILL.md"
        self.source_narration_prompt_path = Path(__file__).with_name(
            "source_deck_narration_prompt.md"
        )

    def generate(
        self,
        content: LearningContent,
        progress_callback: ProgressCallback | None = None,
    ) -> PresentationPlan:
        self._report_progress(
            progress_callback,
            10,
            "planning_content",
            "Planning slide content",
        )
        fallback = self._fallback_plan(content)
        if not self.llm:
            self._report_progress(
                progress_callback,
                45,
                "planning_scenes",
                "Creating fallback slide layouts",
            )
            return self._add_fallback_scenes(
                fallback.model_copy(
                    update={
                        "generation_source": "fallback",
                        "generation_provider": "none",
                        "fallback_reason": "No LLM provider configured",
                    }
                )
            )
        plan = self._generate_content_batches(content, fallback, progress_callback)
        self._report_progress(progress_callback, 35, "planning_scenes", "Designing slide layouts")
        return self._generate_scenes(content, plan, progress_callback)

    def generate_from_source_deck(
        self,
        content: LearningContent,
        pages: list[PageMetadata],
        source_material_id: str,
        progress_callback: ProgressCallback | None = None,
        completed_narration: dict[str, SourceSlideNarrationBatch] | None = None,
        narration_checkpoint_callback: NarrationCheckpointCallback | None = None,
    ) -> PresentationPlan:
        """Create a one-to-one teaching plan without redesigning the uploaded deck."""
        if not pages:
            raise ValueError("Source PPT has no parsed pages")
        slides = []
        previous_section = content.sections[0]
        for index, page in enumerate(sorted(pages, key=lambda item: item.page_no), start=1):
            matched = [
                section
                for section in content.sections
                if any(
                    ref.material_id == source_material_id and ref.page_no == page.page_no
                    for ref in section.page_refs
                )
                or any(
                    ref.material_id == source_material_id and ref.page_no == page.page_no
                    for ref in section.source_refs
                )
                or (
                    page.page_no in section.page_nos
                    and not section.page_refs
                    and not section.source_refs
                )
            ]
            section = matched[0] if matched else previous_section
            previous_section = section
            page_text = " ".join(page.raw_text.split())
            page_summary = page_text[:500]
            key_points = list(section.key_points or section.knowledge_points)[:5]
            if not key_points and page_summary:
                key_points = [page_summary[:120]]
            script_parts = [
                section.teaching_narrative or section.teaching_script or section.summary
            ]
            if page_summary and page_summary not in script_parts[0]:
                script_parts.append(f"结合当前页面来看：{page_summary}")
            slides.append(
                SlidePlan(
                    id=f"source_slide_{index:03d}",
                    order=index,
                    source_section_ids=[item.id for item in matched] or [section.id],
                    source_page_no=page.page_no,
                    source_kind="source",
                    title=page.title.strip() or section.title or f"第 {page.page_no} 页",
                    key_points=key_points,
                    speaker_script="\n\n".join(part for part in script_parts if part).strip()
                    or f"下面讲解第 {page.page_no} 页的核心内容。",
                    suggested_visual="使用上传 PPT 的原始页面",
                    layout="source",
                    visual_payload=[],
                    elements=[],
                )
            )
            self._report_progress(
                progress_callback,
                10 + int(60 * index / len(pages)),
                "mapping_source_slides",
                f"Mapping source slide {index}/{len(pages)}",
            )
        plan = PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=content.title,
            mode="source_deck",
            source_material_id=source_material_id,
            slides=slides,
            generation_source="fallback",
            generation_provider="source_deck_mapper",
        )
        if not self.llm:
            return plan
        return self._enhance_source_deck_scripts(
            content,
            sorted(pages, key=lambda item: item.page_no),
            plan,
            progress_callback,
            completed_narration=completed_narration,
            narration_checkpoint_callback=narration_checkpoint_callback,
        )

    def _enhance_source_deck_scripts(
        self,
        content: LearningContent,
        pages: list[PageMetadata],
        plan: PresentationPlan,
        progress_callback: ProgressCallback | None,
        *,
        completed_narration: dict[str, SourceSlideNarrationBatch] | None = None,
        narration_checkpoint_callback: NarrationCheckpointCallback | None = None,
    ) -> PresentationPlan:
        """Generate one continuous narration batch per teaching segment."""
        slides_by_page = {
            slide.source_page_no: slide for slide in plan.slides if slide.source_page_no
        }
        units_by_id = {unit.id: unit for unit in content.knowledge_units}
        page_flow = {
            item["page_no"]: item
            for item in content.material_overview.get("page_flow", [])
            if isinstance(item, dict) and isinstance(item.get("page_no"), int)
        }
        batches = self._source_narration_batches(content, pages)
        failures: list[str] = []
        successes = 0
        completed_pages = 0
        for batch_index, (section, segment, batch) in enumerate(batches):
            checkpoint_key = segment.id if segment else f"legacy_{section.id}"
            previous_scripts = [
                {
                    "page_no": page.page_no,
                    "speaker_script": slides_by_page[page.page_no].speaker_script,
                }
                for page in pages[max(0, completed_pages - 2) : completed_pages]
            ]
            next_segment = batches[batch_index + 1][1] if batch_index + 1 < len(batches) else None
            segment_units = [
                units_by_id[unit_id]
                for unit_id in (segment.knowledge_unit_ids if segment else [])
                if unit_id in units_by_id
            ]
            payload = [
                {
                    "page_no": page.page_no,
                    "page_title": page.title,
                    "page_text": page.raw_text[:1800],
                    "page_flow": page_flow.get(page.page_no),
                }
                for page in batch
            ]
            messages = [
                LLMMessage(
                    role="system",
                    content=self.source_narration_prompt_path.read_text(encoding="utf-8"),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "course_title": content.title,
                            "course_objectives": content.objectives[:8],
                            "current_section": {
                                "id": section.id,
                                "title": section.title,
                                "learning_goal": section.content_goal,
                                "summary": section.summary,
                            },
                            "current_segment": (
                                segment.model_dump(mode="json")
                                if segment
                                else {
                                    "id": f"legacy_{section.id}",
                                    "title": section.title,
                                    "teaching_goal": section.content_goal,
                                    "summary": section.summary,
                                }
                            ),
                            "segment_knowledge_units": [
                                unit.model_dump(mode="json") for unit in segment_units
                            ],
                            "pages": payload,
                            "previous_segment_tail_scripts": previous_scripts,
                            "next_segment": (
                                {
                                    "title": next_segment.title,
                                    "teaching_goal": next_segment.teaching_goal,
                                }
                                if next_segment
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
            try:
                draft = (completed_narration or {}).get(checkpoint_key)
                if draft is None:
                    raw = self.llm.complete_json(messages, temperature=0.2)
                    draft = SourceSlideNarrationBatch.model_validate(json.loads(raw))
                expected = {page.page_no for page in batch}
                received = {item.page_no for item in draft.slides}
                if received != expected:
                    raise ValueError(
                        f"Source narration pages mismatch: expected {sorted(expected)}, "
                        f"received {sorted(received)}"
                    )
                for item in draft.slides:
                    current = slides_by_page[item.page_no]
                    slides_by_page[item.page_no] = current.model_copy(
                        update={
                            "title": item.title,
                            "key_points": item.key_points or current.key_points,
                            "speaker_script": item.speaker_script,
                        }
                    )
                if checkpoint_key not in (completed_narration or {}):
                    if narration_checkpoint_callback:
                        narration_checkpoint_callback(checkpoint_key, draft)
                successes += 1
            except (
                TimeoutError,
                json.JSONDecodeError,
                ValidationError,
                RuntimeError,
                ValueError,
            ) as exc:
                failures.append(
                    f"segment {segment.id if segment else section.id} "
                    f"(pages {batch[0].page_no}-{batch[-1].page_no}): "
                    f"{type(exc).__name__}: {exc}"
                )
            completed_pages += len(batch)
            self._report_progress(
                progress_callback,
                70 + int(20 * completed_pages / len(pages)),
                "writing_source_scripts",
                f"Writing source slide scripts ({completed_pages}/{len(pages)})",
            )
        slides = [slides_by_page[page.page_no] for page in pages]
        return plan.model_copy(
            update={
                "slides": slides,
                "generation_source": "llm" if successes else "fallback",
                "generation_provider": getattr(self.llm, "name", "unknown"),
                "generation_model": getattr(self.llm, "model", None),
                "fallback_reason": "; ".join(failures) or None,
            }
        )

    @staticmethod
    def _source_narration_batches(
        content: LearningContent,
        pages: list[PageMetadata],
    ) -> list[tuple[LearningSection, TeachingSegment | None, list[PageMetadata]]]:
        pages_by_no = {page.page_no: page for page in pages}
        batches = []
        assigned: set[int] = set()
        for section in content.sections:
            if section.segments:
                for segment in sorted(section.segments, key=lambda item: item.order):
                    segment_pages = [
                        pages_by_no[ref.page_no]
                        for ref in segment.page_refs
                        if ref.page_no in pages_by_no and ref.page_no not in assigned
                    ]
                    if segment_pages:
                        batches.append((section, segment, segment_pages))
                        assigned.update(page.page_no for page in segment_pages)
                continue
            refs = section.page_refs or [
                ref for ref in section.source_refs if ref.page_no in pages_by_no
            ]
            section_pages = [
                pages_by_no[ref.page_no]
                for ref in refs
                if ref.page_no in pages_by_no and ref.page_no not in assigned
            ]
            if section_pages:
                batches.append((section, None, section_pages))
                assigned.update(page.page_no for page in section_pages)
        for page in pages:
            if page.page_no not in assigned:
                section = content.sections[0]
                batches.append((section, None, [page]))
        return batches

    def _generate_content_batches(
        self,
        content: LearningContent,
        fallback: PresentationPlan,
        progress_callback: ProgressCallback | None = None,
    ) -> PresentationPlan:
        batches = self._section_batches(content.sections)
        body_slides: list[SlidePlan] = []
        failures: list[str] = []
        for batch_index, sections in enumerate(batches, start=1):
            batch_content = content.model_copy(update={"sections": sections})
            try:
                raw = self.llm.complete_json(
                    self._build_content_messages(
                        content,
                        sections=sections,
                        batch_index=batch_index,
                        batch_count=len(batches),
                    ),
                    temperature=0.2,
                )
                draft = PresentationPlanDraft.model_validate(json.loads(raw))
                body_slides.extend(self._hydrate_draft(batch_content, draft).slides)
            except (
                TimeoutError,
                json.JSONDecodeError,
                ValidationError,
                RuntimeError,
                ValueError,
            ) as exc:
                reason = f"batch {batch_index}/{len(batches)}: {type(exc).__name__}: {exc}"
                logger.warning("PPT content planning batch fell back: %s", reason)
                failures.append(reason)
                body_slides.extend(self._fallback_plan(batch_content).slides[1:-1])
            self._report_progress(
                progress_callback,
                10 + int(25 * batch_index / len(batches)),
                "planning_content",
                f"Planning slide content batch {batch_index}/{len(batches)}",
            )

        merged = [fallback.slides[0], *body_slides, fallback.slides[-1]]
        slides = [
            slide.model_copy(update={"id": f"slide_{index:03d}", "order": index})
            for index, slide in enumerate(merged, start=1)
        ]
        return PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=content.title,
            slides=slides,
            generation_source="fallback" if failures else "llm",
            generation_provider=getattr(self.llm, "name", "unknown"),
            generation_model=getattr(self.llm, "model", None),
            fallback_reason="; ".join(failures) or None,
        )

    @staticmethod
    def _section_batches(sections: list) -> list[list]:
        """Split contiguous sections into balanced batches of three to five."""
        if len(sections) <= 5:
            return [sections]
        batch_count = math.ceil(len(sections) / 5)
        base_size, remainder = divmod(len(sections), batch_count)
        sizes = [base_size + (1 if index < remainder else 0) for index in range(batch_count)]
        batches = []
        cursor = 0
        for size in sizes:
            batches.append(sections[cursor : cursor + size])
            cursor += size
        return batches

    def _generate_scenes(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        progress_callback: ProgressCallback | None = None,
    ) -> PresentationPlan:
        # Skill-style stable path: the LLM owns the teaching content, while
        # executable, tested layout skeletons own geometry.  Free-coordinate
        # scene generation remains available through _generate_scene_with_repair
        # for future exceptional/exhibit slides, but is no longer the default.
        expanded: list[SlidePlan] = []
        for index, slide in enumerate(plan.slides):
            spec = select_fallback_layout(slide, index)
            source_points = slide.key_points or slide.visual_payload
            chunks = [source_points] if index == 0 else split_points_for_layout(source_points, spec)
            for part, chunk in enumerate(chunks, start=1):
                continuation = part > 1
                expanded.append(
                    slide.model_copy(
                        update={
                            "id": slide.id if not continuation else f"{slide.id}_part_{part}",
                            "title": slide.title if not continuation else f"{slide.title}（续）",
                            "key_points": chunk,
                            "visual_payload": chunk or slide.visual_payload,
                            "speaker_script": (
                                slide.speaker_script
                                if not continuation
                                else f"继续讲解本页内容：{'；'.join(chunk)}"
                            ),
                        }
                    )
                )

        slides = []
        for index, slide in enumerate(expanded):
            slide = slide.model_copy(update={"order": index + 1})
            slides.append(self._with_fallback_scene(slide, index))
            self._report_scene_progress(progress_callback, index + 1, len(expanded))
        return plan.model_copy(update={"slides": slides})

    def _generate_scene_with_repair(
        self, slide: SlidePlan, messages: list[LLMMessage], index: int
    ) -> SlideSceneDraft:
        first_error: Exception | None = None
        for attempt in range(2):
            current_messages = messages
            if attempt and first_error:
                current_messages = [
                    *messages,
                    LLMMessage(
                        role="user",
                        content=(
                            "上一版画布未通过校验。只重新输出完整 scene JSON。"
                            f"失败原因：{first_error}。请保持内容不变，调整坐标、间距和文本框高度；"
                            "正文对象不得重叠，优先减少碎片并扩大安全间距。"
                        ),
                    ),
                ]
            try:
                raw = self.llm.complete_json(current_messages, temperature=0.3 if attempt else 0.35)
                return self._normalize_scene(
                    slide,
                    SlideSceneDraft.model_validate(json.loads(raw)),
                    index,
                )
            except (json.JSONDecodeError, ValidationError, RuntimeError, ValueError) as exc:
                first_error = exc
                if attempt:
                    raise
        raise RuntimeError("scene generation failed")

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
    def _report_scene_progress(
        cls,
        callback: ProgressCallback | None,
        current: int,
        total: int,
    ) -> None:
        if not callback or total <= 0:
            return
        progress = 35 + int(35 * current / total)
        cls._report_progress(
            callback,
            progress,
            "planning_scenes",
            f"Designing slide layouts ({current}/{total})",
        )

    def _add_fallback_scenes(self, plan: PresentationPlan) -> PresentationPlan:
        return plan.model_copy(
            update={
                "slides": [
                    self._with_fallback_scene(slide, index)
                    for index, slide in enumerate(plan.slides)
                ]
            }
        )

    def _fallback_plan(self, content: LearningContent) -> PresentationPlan:
        first_section = content.sections[0]
        last_section = content.sections[-1]
        cover_subtitle = content.subtitle.strip() or "课程学习与核心内容讲解"
        slides = [
            SlidePlan(
                id="slide_001",
                order=1,
                source_section_ids=[first_section.id],
                title=content.title,
                key_points=[cover_subtitle],
                speaker_script=(
                    f"欢迎进入《{content.title}》。接下来我们将围绕课程核心内容展开学习，"
                    "逐步建立概念、方法与应用之间的联系。"
                ),
                suggested_visual="简洁课程封面，突出主标题与副标题，不展示目录。",
                layout="hero",
                visual_payload=[cover_subtitle],
                background="F7F9F7",
            )
        ]
        for section in content.sections:
            points = self._fallback_key_points(section)
            script_parts = [
                self._clean_internal_meta_text(section.teaching_script),
                self._clean_internal_meta_text(section.teaching_narrative),
                self._clean_internal_meta_text(section.summary),
            ]
            speaker_script = next((item for item in script_parts if item), section.title)
            slides.append(
                SlidePlan(
                    id=f"slide_{len(slides) + 1:03d}",
                    order=len(slides) + 1,
                    source_section_ids=[section.id],
                    title=section.title,
                    key_points=points,
                    speaker_script=speaker_script,
                    suggested_visual=(
                        f"Use the source page image as the main visual and highlight {points[0]}."
                    ),
                    layout="two_column",
                    visual_payload=points[:4],
                    background="F7F9F7",
                )
            )
        summary_points = []
        for section in content.sections:
            for point in self._fallback_key_points(section)[:2]:
                if point not in summary_points:
                    summary_points.append(point)
                if len(summary_points) == 5:
                    break
            if len(summary_points) == 5:
                break
        slides.append(
            SlidePlan(
                id=f"slide_{len(slides) + 1:03d}",
                order=len(slides) + 1,
                source_section_ids=[last_section.id],
                title="课程总结",
                key_points=summary_points,
                speaker_script=(
                    "最后把本次课程的核心结论串联起来，并回到学习目标检查已经建立的"
                    "关键认识，以及后续可以继续思考和迁移应用的方向。"
                ),
                suggested_visual="用结构化总结图串联课程核心结论与迁移方向。",
                layout="cards",
                visual_payload=summary_points,
                background="F7F9F7",
            )
        )
        return PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=content.title,
            slides=slides,
        )

    @staticmethod
    def _fallback_key_points(section) -> list[str]:
        """Prefer substantive source material over outline-only labels in fallback decks."""
        candidates: list[str] = []
        candidates.extend(point.strip() for point in section.key_points if point.strip())
        candidates.extend(point.strip() for point in section.knowledge_points if point.strip())
        candidates.extend(
            excerpt.text.strip() for excerpt in section.source_excerpts if excerpt.text.strip()
        )
        if not candidates and section.summary.strip():
            candidates.append(section.summary.strip())
        if not candidates:
            candidates.append(section.title)

        unique: list[str] = []
        for candidate in candidates:
            compact = re.sub(r"\s+", " ", candidate)
            if compact not in unique:
                unique.append(compact)
            if len(unique) == 5:
                break
        return unique

    def _hydrate_draft(
        self, content: LearningContent, draft: PresentationPlanDraft
    ) -> PresentationPlan:
        section_order = {section.id: index for index, section in enumerate(content.sections)}
        known_section_ids = set(section_order)
        slides = []
        covered_section_ids: set[str] = set()
        previous_section_index = -1
        for index, slide in enumerate(draft.slides, start=1):
            source_section_ids = [
                section_id
                for section_id in slide.source_section_ids
                if section_id in known_section_ids
            ]
            if source_section_ids != slide.source_section_ids:
                raise ValueError("slide source_section_ids must reference known sections")
            if len(source_section_ids) != len(set(source_section_ids)):
                raise ValueError("slide source_section_ids must not contain duplicates")
            source_indexes = [section_order[section_id] for section_id in source_section_ids]
            if source_indexes != sorted(source_indexes):
                raise ValueError("slide source_section_ids must follow the section order")
            if source_indexes[0] < previous_section_index:
                raise ValueError("slides must not move backward through LearningContent sections")
            previous_section_index = source_indexes[-1]
            covered_section_ids.update(source_section_ids)
            slides.append(
                SlidePlan(
                    id=f"slide_{index:03d}",
                    order=index,
                    source_section_ids=source_section_ids,
                    title=self._clean_internal_meta_text(slide.title),
                    key_points=[
                        self._clean_internal_meta_text(point) for point in slide.key_points[:6]
                    ],
                    speaker_script=self._clean_internal_meta_text(slide.speaker_script),
                    suggested_visual=self._clean_internal_meta_text(slide.suggested_visual),
                    layout=(
                        slide.layout
                        if slide.elements
                        or slide.layout
                        in {
                            "hero",
                            "two_column",
                            "cards",
                            "process",
                            "comparison",
                            "timeline",
                            "pyramid",
                            "spotlight",
                        }
                        else "two_column"
                    ),
                    visual_payload=slide.visual_payload[:6],
                    background=slide.background,
                    elements=slide.elements[:40],
                )
            )
        missing_section_ids = known_section_ids - covered_section_ids
        if missing_section_ids:
            missing = ", ".join(
                section.id for section in content.sections if section.id in missing_section_ids
            )
            raise ValueError(
                f"PresentationPlan must cover every LearningContent section: {missing}"
            )
        return PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=draft.title,
            slides=slides,
        )

    def _build_content_messages(
        self,
        content: LearningContent,
        *,
        sections: list | None = None,
        batch_index: int | None = None,
        batch_count: int | None = None,
    ) -> list[LLMMessage]:
        # Read on every request so prompt edits take effect without code changes.
        system = (
            self.skill_path.read_text(encoding="utf-8")
            + """

PPT_CONTENT_ONLY
本次只做整套演示文稿的内容策划，不生成画布 elements。输出仍为 title/slides，
每页只需包含 source_section_ids、title、key_points、speaker_script、
suggested_visual、visual_payload；layout 固定写 freeform。不要输出 background 和 elements。
先完全根据 LearningContent 的结构、已有材料以及必要的可靠扩充内容规划所有正文页；
正文的拆分、合并和教学顺序不得受封面或总结页影响。正文规划完成后，再补充以下首尾页：
- 在最前面补一页纯封面：title 使用课程标题，key_points 最多放一个简短副标题；
  source_section_ids 只填写第一个 section 的 id，
  speaker_script 只做自然简短的课程开场。
- 在最后面补一页“课程总结”：综合前面正文的核心结论、知识联系、迁移方向或反思问题，
  不能只是重复标题；source_section_ids 只填写最后一个 section 的 id。
- 最终输出顺序必须是：纯封面、全部正文页、课程总结。
- 不要生成目录页。

内容保真是最高优先级：只允许在 LearningContent 基础上扩充，不允许压缩、删减或用概括性表述替代已有内容。
- LearningContent 是必须完整覆盖的内容下限，不是可任意摘要的大纲。每个 section 中已有的核心定义、机制、步骤、
  条件、公式、变量、案例、结论、误区辨析和重要 source_excerpts，都必须出现在对应页面文字或 speaker_script 中。
- 一个 section 内容较多时必须拆成多张连续 slide，不能为了减少页数缩短讲解、合并知识点或只保留标题与摘要。
- 禁止把不同 section 合并成一张 slide。
- 可以补充可靠的解释、例子、过渡、前置知识和应用，但补充内容不能取代或挤掉 LearningContent 已有内容。
- 课程总结可以概括前文，但正文页不得以“总结”“概览”“知识单元介绍”等方式跳过具体教学内容。
不要按 section 数量机械决定页数。先把输入拆成连续的教学功能单元，再决定 slide：
1. 概念页：一个核心概念、必要定义、与相邻概念的关系；
2. 例子页：一个完整情境及其如何解释概念（例子能和其他主内容放在一页时可以放在同一页）；
3. 推导页：公式含义、变量、步骤或因果过程，复杂推导可拆成连续多页；
4. 练习页：问题、选项/任务、预期答案与讲评依据；
5. 总结页：综合前面结论、比较、迁移或反思，不得只是目录复述。
同一 section 可以贡献多种功能页；不得跨 section 合并，页数不设上限。
每个 section 至少被一页覆盖，slide 顺序必须遵循 section 顺序，不能回退，也不要为增加页数重复内容。
把 LearningContent 当作课程大纲、结构边界和已有材料，而不是内容上限。逐个 section 先判断内容充分度：
- 若定义、机制、步骤、变量、例子和条件已经足够，忠实使用并合理拆页；
- 若只有标题、关键词或总结句，必须补充稳定、通用、可验证的学科基础知识，把主题真正讲清楚；
- 概念至少说明“是什么、解决什么问题、关键特征或条件”；算法/方法至少说明“输入与目标、核心步骤、停止或输出、适用条件”，并视需要补充直观例子、局限或常见误区；
- 扩充必须服务于现有大纲，不能另起主题；可以使用公认基础知识，但不得虚构数据、实验、论文、人物或特定事实，无法可靠确定的内容不要补。
标题和 key_points 必须直接陈述要教给学生的知识或任务，禁止“This page introduces”、
“本页介绍”“本节将讲”“Overview of”“Summary of”之类描述页面行为的元话语。
页面上必须出现实质性内容。例如不能只写“本页讲解 K-means 的算法”，而应写清初始化中心、按最近中心分配样本、重算簇中心、迭代至稳定等实际过程。又例如，不能只写对ndbi的分析，而不写具体的分析是什么。又例如，不能只写svm的意义，而不写具体意义是什么。
把定义、关键公式、算法步骤、对比条件和案例结论放在页面；把完整推理、补充例子、自然过渡放进 speaker_script。页面不能像讲稿一样堆满段落，也不能只剩空泛标签。
speaker_script 必须像真实老师连续讲课：承接上下文、解释本页核心、讲清原因或步骤、给出恰当例子或辨析、自然引向后续；不要逐字朗读 key_points，也不要反复使用机械的“这一页我们讲……”。讲稿中不要输出“本页要点：……”这种格式。
讲稿是课堂现场口语，不是教材章节摘要。不要用“本章”“本单元”“本文”“本节”等书面化自指开头；
直接从问题、现象、概念或与上一页的联系切入，并让相邻页面的开头句式有所变化。
LearningContent、section、source_refs、source_excerpts、selected evidence、prompt 等都是系统内部术语，
绝对不能出现在标题、页面文字或 speaker_script 中。不要输出 Markdown 加粗符号 **。
优先使用 source_excerpts、source_refs、公式、案例和题目中的具体证据；不要把不相干内容压进同一页。
"""
        )
        selected_sections = sections or content.sections
        if batch_index is not None and batch_count is not None:
            system += f"""

PPT_CONTENT_BATCH
这是正文内容生成的第 {batch_index}/{batch_count} 批。本批只处理输入 sections 中的连续 section：
- 只输出本批正文 slide，不生成封面、目录或课程总结。
- 必须覆盖本批每一个 section，不能引用本批之外的 section id。
- 保持 course_outline 中的全课顺序和上下文，但不要替其他批次生成页面。
- 内容丰富时继续拆页，不得压缩。
"""
        compact_sections = [
            {
                "id": section.id,
                "title": section.title,
                "role": section.role,
                "content_goal": section.content_goal,
                "summary": self._clean_internal_meta_text(section.summary)[:900],
                "key_points": [
                    self._clean_internal_meta_text(point) for point in section.key_points[:8]
                ],
                "knowledge_points": section.knowledge_points[:10],
                "teaching_narrative": self._clean_internal_meta_text(section.teaching_narrative)[
                    :1200
                ],
                "teaching_script": self._clean_internal_meta_text(section.teaching_script)[:1500],
                "source_excerpts": [
                    {
                        "id": item.id,
                        "text": item.text[:800],
                        "type": item.type,
                        "reason": item.reason[:300],
                        "importance": item.importance,
                        "usage": item.usage,
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in item.source_refs[:4]
                        ],
                    }
                    for item in section.source_excerpts[:12]
                ],
                "examples": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "scenario": item.scenario[:400],
                        "explanation": item.explanation[:600],
                        "takeaway": item.takeaway[:300],
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in item.source_refs[:4]
                        ],
                    }
                    for item in section.examples[:6]
                ],
                "formulas": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "latex": item.latex,
                        "meaning": item.meaning[:300],
                        "variables": item.variables,
                        "when_to_use": item.when_to_use[:400],
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in item.source_refs[:4]
                        ],
                    }
                    for item in section.formulas[:8]
                ],
                "visual_opportunities": [
                    {
                        "type": item.type,
                        "description": item.description[:500],
                        "image_path": item.image_path,
                        "image_description": item.image_description[:500],
                        "usage_hint": item.usage_hint[:300],
                        "priority": item.priority,
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in item.source_refs[:4]
                        ],
                    }
                    for item in section.visual_opportunities[:8]
                ],
                "misconceptions": [
                    {"mistake": item.mistake[:300], "correction": item.correction[:400]}
                    for item in section.misconceptions[:3]
                ],
                "interaction_opportunities": [
                    {
                        "type": item.type,
                        "prompt": item.prompt[:300],
                        "expected_answer": item.expected_answer[:400],
                        "target_concept_ids": item.target_concept_ids,
                        "difficulty": item.difficulty,
                    }
                    for item in section.interaction_opportunities[:6]
                ],
                "quiz_questions": [
                    {
                        "question": quiz.question[:200],
                        "knowledge_point": quiz.knowledge_point[:160],
                        "explanation": quiz.explanation[:300],
                        "options": quiz.options,
                        "correct_index": quiz.correct_index,
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in quiz.source_refs[:4]
                        ],
                    }
                    for quiz in section.quiz_items[:6]
                ],
                "transition_to_next": section.transition_to_next[:500],
                "visual_summary": section.visual_summary[:600],
                "page_refs": [item.model_dump(mode="json") for item in section.page_refs],
                "source_refs": [self._source_ref_payload(ref) for ref in section.source_refs[:20]],
            }
            for section in selected_sections
        ]
        selected_node_ids = {
            node_id for section in selected_sections for node_id in section.tree_node_ids
        }
        selected_unit_ids = {
            unit_id
            for node in (content.knowledge_tree.nodes if content.knowledge_tree else [])
            if node.id in selected_node_ids
            for unit_id in node.knowledge_unit_ids
        }
        user = {
            "content_id": content.id,
            "title": content.title,
            "subtitle": content.subtitle,
            "objectives": content.objectives[:8],
            "audience": content.audience,
            "teaching_intent": content.teaching_intent,
            "material_overview": content.material_overview,
            "global_concepts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "definition": item.definition[:500],
                    "plain_explanation": item.plain_explanation[:500],
                    "why_it_matters": item.why_it_matters[:400],
                }
                for item in content.global_concepts[:12]
            ],
            "knowledge_units": [
                {
                    "id": item.id,
                    "title": item.title,
                    "unit_type": item.unit_type,
                    "summary": item.summary[:700],
                    "keywords": item.keywords[:10],
                    "importance": item.importance,
                    "confidence": item.confidence,
                    "aliases": item.aliases,
                    "concepts": [concept.model_dump(mode="json") for concept in item.concepts[:8]],
                    "source_excerpts": [
                        excerpt.model_dump(mode="json") for excerpt in item.source_excerpts[:8]
                    ],
                    "formulas": [formula.model_dump(mode="json") for formula in item.formulas[:6]],
                    "examples": [example.model_dump(mode="json") for example in item.examples[:6]],
                    "misconceptions": [
                        misconception.model_dump(mode="json")
                        for misconception in item.misconceptions[:6]
                    ],
                    "source_refs": [self._source_ref_payload(ref) for ref in item.source_refs[:12]],
                    "page_refs": [ref.model_dump(mode="json") for ref in item.page_refs],
                    "relations": [
                        relation.model_dump(mode="json") for relation in item.relations[:6]
                    ],
                }
                for item in content.knowledge_units
                if item.id in selected_unit_ids
            ],
            "course_outline": [
                {"id": section.id, "title": section.title, "role": section.role}
                for section in content.sections
            ],
            "batch": (
                {"index": batch_index, "count": batch_count}
                if batch_index is not None and batch_count is not None
                else None
            ),
            "knowledge_tree": None,
            "generation_guidance": content.generation_guidance,
            "quality": content.quality,
            "sections": compact_sections,
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]

    def _build_messages(self, content: LearningContent) -> list[LLMMessage]:
        """Backward-compatible alias used by prompt inspection tests/tools."""
        return self._build_content_messages(content)

    def _build_scene_messages(
        self,
        content: LearningContent,
        sections: list,
        slide: SlidePlan,
        index: int,
        slide_count: int,
    ) -> list[LLMMessage]:
        system = (
            self.skill_path.read_text(encoding="utf-8")
            + """

PPT_SCENE_ONLY
本次只设计一页自由画布，不要输出整套 presentation，也不要重写讲稿。
只输出 {"background":"六位十六进制颜色","elements":[...]}。
elements 必须满足上文自由画布契约，并依据本页语义构图。标题也必须作为 text 元素出现。
优先使用 6–14 个清晰的大元素；不要套用固定模板，不要与相邻页机械重复。
除 visual_opportunities.image_path 汇总得到的 source_images 外不得虚构图片路径；
不要使用 source_refs.image_path；没有可靠数字就不要生成 chart。
不要把“图标”“插画”“示意图”等占位说明写进 text；如果需要图标感，
请用 oval/rectangle/line/chevron 等 shape 直接画出来。
text 元素必须给足高度：标题 h >= 0.11，正文 h >= 0.075；
不要用 0.04 这类只能容纳一行英文的高度放中文短句。
shape 只能做背景/卡片/标记，正文必须是独立 text 元素且 z 更高。
先判断本页的教学功能并据此构图：概念用关系图/对比，例子用情境与步骤，
推导用公式与逐步标注，练习必须清楚展示题目和作答区域，总结用综合框架而非泛化 bullet。
必须利用输入中的 formulas、examples、interactions、quiz_items、misconceptions、
source_excerpts 和带 page_no/text_span 的 source_refs；有对应 source image 时优先让图像承担证据或讲解作用。
本页画布以 slide.title、key_points、visual_payload 和 speaker_script 中已经完成的教学内容为直接依据；source sections 用于核对结构和证据。不得把有实质内容的 key_points 再退化成“概念介绍”“算法流程”“案例分析”等空泛标签。
页面正文应呈现足以独立理解本页的定义、步骤、变量关系、条件或案例结论，但不要把 speaker_script 整段复制到画布上。优先用流程、关系、对比、分组和逐步标注压缩信息。
"""
        )
        payload = {
            "deck": {
                "title": content.title,
                "objectives": content.objectives[:8],
                "slide_index": index + 1,
                "slide_count": slide_count,
                "layout_registry": registry_prompt_payload(),
                "layout_constraints": DEFAULT_LAYOUT_CONSTRAINTS.prompt_payload(),
                "academic_layout_guidance": ACADEMIC_LAYOUT_GUIDANCE,
                "brand_palette": BRAND_PALETTE.prompt_payload(),
            },
            "slide": {
                "title": slide.title,
                "key_points": slide.key_points,
                "suggested_visual": slide.suggested_visual,
                "visual_payload": slide.visual_payload,
                "speaker_script": slide.speaker_script,
            },
            "source": {
                "sections": [
                    {
                        "section_id": section.id,
                        "section_title": section.title,
                        "summary": self._clean_internal_meta_text(section.summary)[:1200],
                        "knowledge_points": section.knowledge_points[:10],
                        "teaching_narrative": self._clean_internal_meta_text(
                            section.teaching_narrative
                        )[:1200],
                        "teaching_script": self._clean_internal_meta_text(section.teaching_script)[
                            :1500
                        ],
                        "source_excerpts": [
                            excerpt.model_dump(mode="json")
                            for excerpt in section.source_excerpts[:12]
                        ],
                        "formulas": [
                            formula.model_dump(mode="json") for formula in section.formulas[:8]
                        ],
                        "examples": [
                            example.model_dump(mode="json") for example in section.examples[:6]
                        ],
                        "visual_opportunities": [
                            opportunity.model_dump(mode="json")
                            for opportunity in section.visual_opportunities[:8]
                        ],
                        "misconceptions": [
                            misconception.model_dump(mode="json")
                            for misconception in section.misconceptions[:6]
                        ],
                        "interactions": [
                            interaction.model_dump(mode="json")
                            for interaction in section.interaction_opportunities[:6]
                        ],
                        "quiz_items": [
                            quiz.model_dump(mode="json") for quiz in section.quiz_items[:6]
                        ],
                        "source_refs": [
                            self._source_ref_payload(ref) for ref in section.source_refs[:20]
                        ],
                    }
                    for section in sections
                ],
                "source_images": list(
                    dict.fromkeys(
                        opportunity.image_path
                        for section in sections
                        for opportunity in section.visual_opportunities
                        if opportunity.image_path
                    )
                )[:4],
            },
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    @staticmethod
    def _source_ref_payload(ref) -> dict:
        return {
            "material_id": ref.material_id,
            "page_id": ref.page_id,
            "page_no": ref.page_no,
            "text_span": ref.text_span,
        }

    @staticmethod
    def _clean_internal_meta_text(value: str) -> str:
        text = re.sub(r"Use selected evidence\s*:\s*", "", value, flags=re.IGNORECASE)
        text = re.sub(r"\bLearning\s*Content\b", "课程内容", text, flags=re.IGNORECASE)
        text = re.sub(r"\bLearningContent\b", "课程内容", text, flags=re.IGNORECASE)
        text = re.sub(r"本(?:章|单元)\s*从", "我们从", text)
        text = re.sub(r"本(?:章|单元)\s*将", "接下来将", text)
        text = re.sub(r"本(?:章|单元)\s*(?:主要|重点)", "这里重点", text)
        text = re.sub(
            r"本(?:章|单元)\s*(介绍|讲解|讨论|分析|探讨)",
            r"接下来\1",
            text,
        )
        text = re.sub(r"本(?:章|单元)", "这部分内容", text)
        return re.sub(r"\*\*", "", text).strip()

    def _normalize_scene(
        self,
        slide: SlidePlan,
        scene: SlideSceneDraft,
        index: int,
    ) -> SlideSceneDraft:
        """Repair common LLM layout mistakes before python-pptx renders them."""
        elements: list[SlideElement] = []
        has_visual = False
        for source_element in scene.elements:
            element = apply_brand_palette(source_element)
            updates = {}
            if element.type == "text":
                text = self._clean_text_placeholders(element.text or "\n".join(element.items))
                style = element.style
                is_title = self._looks_like_title(text, slide.title)
                fitted_size = self._fit_font_size(
                    text=text,
                    width=element.w,
                    height=element.h,
                    font_size=style.font_size,
                    min_font_size=(
                        DEFAULT_LAYOUT_CONSTRAINTS.title_min_font_size
                        if is_title
                        else DEFAULT_LAYOUT_CONSTRAINTS.body_min_font_size
                    ),
                    is_title=is_title,
                )
                if fitted_size is None:
                    raise ValueError(f"text does not fit its box at minimum font size: {text[:60]}")
                updates.update(
                    {
                        "text": text,
                        "style": style.model_copy(update={"font_size": fitted_size}),
                    }
                )
            else:
                has_visual = True

            try:
                elements.append(element.model_copy(update=updates))
            except ValidationError:
                logger.warning(
                    "Dropped invalid PPT scene element on %s: %s",
                    slide.id,
                    element.model_dump(mode="json"),
                )

        if not has_visual:
            raise ValueError("scene contains no non-text visual element")
        self._validate_scene_safe_zones(elements, slide.title, allow_centered_title=index == 0)
        if self._has_unsafe_scene_collisions(elements, slide.title):
            raise ValueError("scene contains overlapping content objects after text fitting")
        background = BRAND_PALETTE.board if index == 0 else BRAND_PALETTE.paper
        return SlideSceneDraft(background=background, elements=elements)

    @classmethod
    def _has_unsafe_scene_collisions(
        cls,
        elements: list[SlideElement],
        slide_title: str,
    ) -> bool:
        """Reject collisions between content objects while allowing text on card shapes."""
        content = [
            element for element in elements if element.type in {"text", "image", "table", "chart"}
        ]
        for index, left in enumerate(content):
            for right in content[index + 1 :]:
                overlap_w = min(left.x + left.w, right.x + right.w) - max(left.x, right.x)
                overlap_h = min(left.y + left.h, right.y + right.h) - max(left.y, right.y)
                if overlap_w <= 0 or overlap_h <= 0:
                    continue
                overlap_area = overlap_w * overlap_h
                smaller_area = min(left.w * left.h, right.w * right.h)
                if smaller_area and overlap_area / smaller_area >= 0.08:
                    return True
        return False

    @classmethod
    def _validate_scene_safe_zones(
        cls,
        elements: list[SlideElement],
        slide_title: str,
        *,
        allow_centered_title: bool = False,
    ) -> None:
        rules = DEFAULT_LAYOUT_CONSTRAINTS
        geometry_tolerance = 1e-9
        for element in elements:
            if element.type not in {"text", "image", "table", "chart"}:
                continue
            text = element.text or "\n".join(element.items)
            is_title = element.type == "text" and cls._looks_like_title(text, slide_title)
            if (
                element.x < rules.canvas_margin_x - geometry_tolerance
                or element.x + element.w > 1 - rules.canvas_margin_x + geometry_tolerance
            ):
                raise ValueError("content element violates horizontal canvas margin")
            if is_title:
                title_bottom = 0.65 if allow_centered_title else rules.title_bottom
                if (
                    element.y < rules.title_top - geometry_tolerance
                    or element.y + element.h > title_bottom + geometry_tolerance
                ):
                    raise ValueError("title element violates title safe zone")
            elif (
                element.y < rules.content_top - geometry_tolerance
                or element.y + element.h > rules.content_bottom + geometry_tolerance
            ):
                raise ValueError("content element violates body safe zone")

    @staticmethod
    def _clean_text_placeholders(text: str) -> str:
        lines = []
        for line in text.splitlines():
            clean = re.sub(
                r"[（(][^（）()]{0,18}(?:图标|icon|插画|示意图)[^（）()]{0,18}[）)]", "", line
            )
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean:
                lines.append(clean)
        return "\n".join(lines)

    @staticmethod
    def _looks_like_title(text: str, title: str) -> bool:
        return text.strip() == title.strip() or len(text.strip()) > 18 and title.strip() in text

    @staticmethod
    def _estimate_text_height(
        *, text: str, width: float, font_size: float, is_title: bool
    ) -> float:
        if not text:
            return 0.06
        usable_inches = max(0.6, width * 13.333 - 0.15)
        chars_per_line = max(5, int(usable_inches * 72 / max(font_size * 0.72, 1)))
        line_count = 0
        for line in text.splitlines() or [text]:
            visual_len = sum(2 if ord(char) > 127 else 1 for char in line)
            line_count += max(1, math.ceil(visual_len / chars_per_line))
        inches = line_count * font_size * 1.35 / 72 + 0.1
        min_norm = 0.11 if is_title else 0.075
        return min(0.42, max(min_norm, inches / 7.5))

    @classmethod
    def _fit_font_size(
        cls,
        *,
        text: str,
        width: float,
        height: float,
        font_size: float,
        min_font_size: float,
        is_title: bool,
    ) -> float | None:
        size = max(font_size, min_font_size)
        while size >= min_font_size:
            required = cls._estimate_text_height(
                text=text,
                width=width,
                font_size=size,
                is_title=is_title,
            )
            if required <= height + 0.005:
                return round(size, 1)
            size -= 1
        return None

    def _with_fallback_scene(self, slide: SlidePlan, index: int) -> SlidePlan:
        """Create a safe constrained scene selected from the layout registry."""
        background = BRAND_PALETTE.board if index == 0 else BRAND_PALETTE.paper
        ink = BRAND_PALETTE.chalk if index == 0 else BRAND_PALETTE.ink
        # A single mid-blue accent keeps hierarchy consistent across the deck.
        accent = BRAND_PALETTE.chalk if index == 0 else BRAND_PALETTE.mint
        card = BRAND_PALETTE.wall if index == 0 else BRAND_PALETTE.chalk
        spec = select_fallback_layout(slide, index)
        elements = build_fallback_elements(slide, spec, (background, ink, accent, card))
        return slide.model_copy(
            update={
                "layout": "freeform",
                "layout_id": spec.id,
                "background": background,
                "elements": elements,
            }
        )

    @staticmethod
    def _fallback_layout_id(slide: SlidePlan) -> str:
        return slide.layout_id or "freeform"
