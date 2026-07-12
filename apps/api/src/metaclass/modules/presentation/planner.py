from __future__ import annotations

import json
from uuid import uuid4

from pydantic import Field, ValidationError

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import LearningContent
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan


class SlidePlanDraft(SchemaModel):
    source_section_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    speaker_script: str = Field(min_length=1)
    suggested_visual: str = Field(min_length=1)


class PresentationPlanDraft(SchemaModel):
    title: str = Field(min_length=1)
    slides: list[SlidePlanDraft] = Field(min_length=1)


class PresentationPlanGenerator:
    """Generate the shared slide/script layer from LearningContent."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    def generate(self, content: LearningContent) -> PresentationPlan:
        fallback = self._fallback_plan(content)
        if not self.llm:
            return fallback
        try:
            raw = self.llm.complete_json(self._build_messages(content), temperature=0.2)
            payload = json.loads(raw)
            draft = PresentationPlanDraft.model_validate(payload)
            return self._hydrate_draft(content, draft)
        except (TimeoutError, json.JSONDecodeError, ValidationError, RuntimeError, ValueError):
            return fallback

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
                        "Use the source page image as the main visual and highlight "
                        f"{points[0]}."
                    ),
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
        if len(draft.slides) != len(content.sections):
            raise ValueError("PresentationPlan must keep one slide per LearningContent section")
        known_section_ids = {section.id for section in content.sections}
        slides = []
        for index, (section, slide) in enumerate(zip(content.sections, draft.slides), start=1):
            source_section_ids = [
                section_id
                for section_id in slide.source_section_ids
                if section_id in known_section_ids
            ]
            if source_section_ids != [section.id]:
                raise ValueError("slide source_section_ids must match the section order")
            slides.append(
                SlidePlan(
                    id=f"slide_{index:03d}",
                    order=index,
                    source_section_ids=source_section_ids,
                    title=slide.title,
                    key_points=slide.key_points[:6],
                    speaker_script=slide.speaker_script,
                    suggested_visual=slide.suggested_visual,
                )
            )
        return PresentationPlan(
            id=f"presentation_plan_{uuid4().hex[:12]}",
            content_id=content.id,
            title=draft.title,
            slides=slides,
        )

    @staticmethod
    def _build_messages(content: LearningContent) -> list[LLMMessage]:
        system = """你是 MetaClass 的 PresentationPlan planner，负责把 LearningContent 转成 PPT 和课堂共同引用的中间层。

# 输出
你必须只输出 JSON object，不要 markdown，不要解释。格式必须为：
{
  "title": "演示文稿标题",
  "slides": [
    {
      "source_section_ids": ["section_xxx"],
      "title": "本页标题",
      "key_points": ["关键点1", "关键点2"],
      "speaker_script": "这一页的逐字或半逐字讲稿，适合老师直接讲解。",
      "suggested_visual": "给 PPT 生成 skill 的视觉建议，例如图示、流程图、对比表、源页面截图等。"
    }
  ]
}

# 规划原则
- 当前版本必须严格保持一个输入 section 生成一页 slide，不要合并相邻 section，也不要把一个 section 拆成多页。
- slides 的顺序必须和输入 sections 的顺序一致。
- 每个 slide 必须引用真实存在的 source_section_ids，且当前只能引用一个 section_id。
- 讲稿要口语化、自然，不要只是重复 bullet。
- key_points 控制在 3 到 6 条。
- suggested_visual 要具体，方便后续 PPT skill 生成真实页面。
- 不要编造 source section id。
"""
        compact_sections = [
            {
                "id": section.id,
                "title": section.title,
                "summary": section.summary[:900],
                "knowledge_points": section.knowledge_points[:10],
                "quiz_questions": [quiz.question[:160] for quiz in section.quiz_items[:3]],
                "page_numbers": sorted({ref.page_no for ref in section.source_refs}),
            }
            for section in content.sections
        ]
        user = {
            "content_id": content.id,
            "title": content.title,
            "objectives": content.objectives[:8],
            "sections": compact_sections,
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]
