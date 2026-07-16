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
from metaclass.modules.content.schemas import LearningContent
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


class SlideSceneDraft(SchemaModel):
    background: str = Field(pattern=r"^[0-9A-Fa-f]{6}$")
    elements: list[SlideElement] = Field(min_length=1, max_length=40)


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str, str], None]


class PresentationPlanGenerator:
    """Generate the shared slide/script layer from LearningContent."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm
        self.skill_path = Path(__file__).with_name("pptx_skill") / "SKILL.md"

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
            return self._add_fallback_scenes(fallback)
        try:
            raw = self.llm.complete_json(self._build_content_messages(content), temperature=0.2)
            payload = json.loads(raw)
            draft = PresentationPlanDraft.model_validate(payload)
            plan = self._hydrate_draft(content, draft)
        except (
            TimeoutError,
            json.JSONDecodeError,
            ValidationError,
            RuntimeError,
            ValueError,
        ) as exc:
            logger.warning("PPT content planning fell back: %s", exc)
            plan = fallback
        self._report_progress(progress_callback, 35, "planning_scenes", "Designing slide layouts")
        return self._generate_scenes(content, plan, progress_callback)

    def _generate_scenes(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        progress_callback: ProgressCallback | None = None,
    ) -> PresentationPlan:
        sections = {section.id: section for section in content.sections}
        slides = []
        for index, slide in enumerate(plan.slides):
            source_sections = [sections[section_id] for section_id in slide.source_section_ids]
            try:
                raw = self.llm.complete_json(
                    self._build_scene_messages(
                        content,
                        source_sections,
                        slide,
                        index,
                        len(plan.slides),
                    ),
                    temperature=0.35,
                )
                scene = self._normalize_scene(
                    slide,
                    SlideSceneDraft.model_validate(json.loads(raw)),
                    index,
                )
                slides.append(
                    slide.model_copy(
                        update={
                            "layout": "freeform",
                            "background": scene.background,
                            "elements": scene.elements,
                        }
                    )
                )
            except (
                TimeoutError,
                json.JSONDecodeError,
                ValidationError,
                RuntimeError,
                ValueError,
            ) as exc:
                logger.warning("PPT scene %s fell back: %s", slide.id, exc)
                slides.append(self._with_fallback_scene(slide, index))
            self._report_scene_progress(progress_callback, index + 1, len(plan.slides))
        return plan.model_copy(update={"slides": slides})

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
        slides = []
        for index, section in enumerate(content.sections, start=1):
            points = section.knowledge_points[:5] or [section.title]
            slides.append(
                SlidePlan(
                    id=f"slide_{index:03d}",
                    order=index,
                    source_section_ids=[section.id],
                    title=section.title,
                    key_points=points,
                    speaker_script=f"{section.title}。{section.summary}",
                    suggested_visual=(
                        f"Use the source page image as the main visual and highlight {points[0]}."
                    ),
                    layout="two_column",
                    visual_payload=points[:4],
                    background="F7F9F7",
                )
            )
        return PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=content.title,
            slides=slides,
        )

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
                    title=slide.title,
                    key_points=slide.key_points[:6],
                    speaker_script=slide.speaker_script,
                    suggested_visual=slide.suggested_visual,
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

    def _build_content_messages(self, content: LearningContent) -> list[LLMMessage]:
        # Read on every request so prompt edits take effect without code changes.
        system = (
            self.skill_path.read_text(encoding="utf-8")
            + """

PPT_CONTENT_ONLY
本次只做整套演示文稿的内容策划，不生成画布 elements。输出仍为 title/slides，
每页只需包含 source_section_ids、title、key_points、speaker_script、
suggested_visual、visual_payload；layout 固定写 freeform。不要输出 background 和 elements。
不要按 section 数量机械决定页数。先把输入拆成连续的教学功能单元，再决定 slide：
1. 概念页：一个核心概念、必要定义、与相邻概念的关系；
2. 例子页：一个完整情境及其如何解释概念；
3. 推导页：公式含义、变量、步骤或因果过程，复杂推导可拆成连续多页；
4. 练习页：问题、选项/任务、预期答案与讲评依据；
5. 总结页：综合前面结论、比较、迁移或反思，不得只是目录复述。
同一 section 可以贡献多种功能页，也可以跨相邻 section 合并一个教学功能单元；页数不设上下限。
每个 section 至少被一页覆盖，slide 顺序必须遵循 section 顺序，不能回退，也不要为增加页数重复内容。
标题和 key_points 必须直接陈述要教给学生的知识或任务，禁止“This page introduces”、
“本页介绍”“本节将讲”“Overview of”“Summary of”之类描述页面行为的元话语。
优先使用 source_excerpts、source_refs、公式、案例和题目中的具体证据；不要把不相干内容压进同一页。
"""
        )
        compact_sections = [
            {
                "id": section.id,
                "title": section.title,
                "role": section.role,
                "content_goal": section.content_goal,
                "summary": section.summary[:900],
                "key_points": section.key_points[:8],
                "knowledge_points": section.knowledge_points[:10],
                "teaching_narrative": section.teaching_narrative[:1200],
                "teaching_script": section.teaching_script[:1500],
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
            for section in content.sections
        ]
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
                for item in content.knowledge_units[:20]
            ],
            "knowledge_tree": (
                content.knowledge_tree.model_dump(mode="json") if content.knowledge_tree else None
            ),
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
除输入 source_images 外不得虚构图片路径；没有可靠数字就不要生成 chart。
不要把“图标”“插画”“示意图”等占位说明写进 text；如果需要图标感，
请用 oval/rectangle/line/chevron 等 shape 直接画出来。
text 元素必须给足高度：标题 h >= 0.11，正文 h >= 0.075；
不要用 0.04 这类只能容纳一行英文的高度放中文短句。
shape 只能做背景/卡片/标记，正文必须是独立 text 元素且 z 更高。
先判断本页的教学功能并据此构图：概念用关系图/对比，例子用情境与步骤，
推导用公式与逐步标注，练习必须清楚展示题目和作答区域，总结用综合框架而非泛化 bullet。
必须利用输入中的 formulas、examples、interactions、quiz_items、misconceptions、
source_excerpts 和带 page_no/text_span 的 source_refs；有对应 source image 时优先让图像承担证据或讲解作用。
"""
        )
        payload = {
            "deck": {
                "title": content.title,
                "objectives": content.objectives[:8],
                "slide_index": index + 1,
                "slide_count": slide_count,
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
                        "summary": section.summary[:1200],
                        "knowledge_points": section.knowledge_points[:10],
                        "teaching_narrative": section.teaching_narrative[:1200],
                        "teaching_script": section.teaching_script[:1500],
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
                        ref.image_path
                        for section in sections
                        for ref in section.source_refs
                        if ref.image_path
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
            "image_path": ref.image_path,
        }

    def _normalize_scene(
        self,
        slide: SlidePlan,
        scene: SlideSceneDraft,
        index: int,
    ) -> SlideSceneDraft:
        """Repair common LLM layout mistakes before python-pptx renders them."""
        elements: list[SlideElement] = []
        has_visual = False
        for element in scene.elements:
            updates = {}
            if element.type == "text":
                text = self._clean_text_placeholders(element.text or "\n".join(element.items))
                style = element.style
                font_size = style.font_size
                min_h = self._estimate_text_height(
                    text=text,
                    width=element.w,
                    font_size=font_size,
                    is_title=self._looks_like_title(text, slide.title),
                )
                y = element.y
                h = max(element.h, min_h)
                if y + h > 0.96:
                    y = max(0.04, 0.96 - h)
                if y + h > 1:
                    h = max(0.06, 1 - y)
                updates.update({"text": text, "y": y, "h": h})
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
            return SlideSceneDraft.model_validate(
                {
                    "background": scene.background,
                    "elements": self._with_fallback_scene(slide, index).elements,
                }
            )
        if self._has_unsafe_scene_collisions(elements, slide.title):
            logger.warning("Replaced overlapping PPT scene on %s with safe layout", slide.id)
            return SlideSceneDraft.model_validate(
                {
                    "background": scene.background,
                    "elements": self._with_fallback_scene(slide, index).elements,
                }
            )
        return SlideSceneDraft(background=scene.background, elements=elements)

    @classmethod
    def _has_unsafe_scene_collisions(
        cls,
        elements: list[SlideElement],
        slide_title: str,
    ) -> bool:
        """Reject collisions between content objects while allowing text on card shapes."""
        content = [
            element
            for element in elements
            if element.type in {"text", "image", "table", "chart"}
            and not (
                element.type == "text"
                and cls._looks_like_title(element.text or "\n".join(element.items), slide_title)
            )
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

    def _with_fallback_scene(self, slide: SlidePlan, index: int) -> SlidePlan:
        """Create a safe but varied free-form scene when one LLM scene is invalid."""
        palettes = [
            ("F7F3EA", "6B3F2A", "D97757", "FFFDFC"),
            ("EEF5F2", "173F3A", "45A08A", "FFFFFF"),
            ("F2F1F8", "302B63", "7165A8", "FFFFFF"),
        ]
        background, ink, accent, card = palettes[index % len(palettes)]
        points = (slide.key_points or slide.visual_payload or [slide.title])[:4]
        elements: list[SlideElement] = [
            SlideElement(
                type="text",
                x=0.07,
                y=0.07,
                w=0.82,
                h=0.12,
                z=5,
                text=slide.title,
                style={"font_size": 34, "bold": True, "color": ink},
            )
        ]

        pattern = index % 3
        if pattern == 0:
            elements.append(
                SlideElement(
                    type="shape",
                    x=0.07,
                    y=0.26,
                    w=0.38,
                    h=0.57,
                    z=0,
                    shape="rounded_rectangle",
                    style={"fill": accent, "line_color": accent},
                )
            )
            elements.append(
                SlideElement(
                    type="text",
                    x=0.105,
                    y=0.32,
                    w=0.31,
                    h=0.4,
                    z=2,
                    text=points[0],
                    style={"font_size": 25, "bold": True, "color": "FFFFFF", "valign": "middle"},
                )
            )
            for point_index, point in enumerate(points[1:4]):
                y = 0.26 + point_index * 0.19
                elements.extend(
                    [
                        SlideElement(
                            type="shape",
                            x=0.51,
                            y=y,
                            w=0.41,
                            h=0.15,
                            z=0,
                            shape="rounded_rectangle",
                            style={"fill": card, "line_color": accent},
                        ),
                        SlideElement(
                            type="text",
                            x=0.55,
                            y=y + 0.035,
                            w=0.33,
                            h=0.08,
                            z=2,
                            text=point,
                            style={"font_size": 17, "color": ink, "valign": "middle"},
                        ),
                    ]
                )
        elif pattern == 1:
            for point_index, point in enumerate(points):
                row, column = divmod(point_index, 2)
                x, y = 0.08 + column * 0.44, 0.27 + row * 0.27
                elements.extend(
                    [
                        SlideElement(
                            type="shape",
                            x=x,
                            y=y,
                            w=0.39,
                            h=0.21,
                            z=0,
                            shape="rounded_rectangle",
                            style={"fill": card, "line_color": accent, "line_width": 2},
                        ),
                        SlideElement(
                            type="text",
                            x=x + 0.035,
                            y=y + 0.045,
                            w=0.32,
                            h=0.12,
                            z=2,
                            text=point,
                            style={"font_size": 18, "bold": point_index == 0, "color": ink},
                        ),
                    ]
                )
        else:
            count = max(1, len(points))
            node_w = min(0.19, 0.76 / count)
            gap = (0.82 - node_w * count) / max(1, count - 1) if count > 1 else 0
            for point_index, point in enumerate(points):
                x = 0.09 + point_index * (node_w + gap)
                if point_index < count - 1:
                    elements.append(
                        SlideElement(
                            type="line",
                            x=x + node_w,
                            y=0.48,
                            w=gap,
                            h=0,
                            z=0,
                            style={"line_color": accent, "line_width": 3},
                        )
                    )
                elements.extend(
                    [
                        SlideElement(
                            type="shape",
                            x=x,
                            y=0.36,
                            w=node_w,
                            h=0.25,
                            z=1,
                            shape="rounded_rectangle",
                            style={"fill": card, "line_color": accent, "line_width": 2},
                        ),
                        SlideElement(
                            type="text",
                            x=x + 0.02,
                            y=0.405,
                            w=node_w - 0.04,
                            h=0.15,
                            z=2,
                            text=point,
                            style={
                                "font_size": 16,
                                "bold": True,
                                "color": ink,
                                "align": "center",
                                "valign": "middle",
                            },
                        ),
                    ]
                )
        return slide.model_copy(
            update={
                "layout": "freeform",
                "background": background,
                "elements": elements,
            }
        )
