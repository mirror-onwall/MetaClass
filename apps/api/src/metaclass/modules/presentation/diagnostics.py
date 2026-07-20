from __future__ import annotations

import re

from metaclass.modules.content.schemas import LearningContent
from metaclass.modules.presentation.schemas import (
    PresentationPlan,
    PresentationPlanDiagnosis,
    SlideScriptDiagnosis,
)


def diagnose_presentation_plan(
    plan: PresentationPlan,
    content: LearningContent,
    *,
    llm_configured: bool,
    provider: str,
    model: str | None,
) -> PresentationPlanDiagnosis:
    """Infer whether a stored plan came from the planner or its fallback path.

    Older plans do not contain the caught planner exception, so this function
    reports evidence-based inference rather than inventing an exact exception.
    """
    sections = {section.id: section for section in content.sections}
    slide_diagnostics: list[SlideScriptDiagnosis] = []
    direct_count = 0
    fallback_marker_count = 0
    body_slides = [
        slide
        for index, slide in enumerate(plan.slides)
        if index != 0 and slide.title != "课程总结" and "_part_" not in slide.id
    ]

    for slide in plan.slides:
        source_field = None
        source_section_id = None
        exact_copy = False
        if len(slide.source_section_ids) == 1:
            source_section_id = slide.source_section_ids[0]
            section = sections.get(source_section_id)
            if section:
                candidates = (
                    ("teaching_script", section.teaching_script),
                    ("teaching_narrative", section.teaching_narrative),
                    ("summary", section.summary),
                    ("title", section.title),
                )
                for field, value in candidates:
                    if value.strip() and _compact(slide.speaker_script) == _compact(value):
                        source_field = field
                        exact_copy = True
                        direct_count += 1
                        break

        markers: list[str] = []
        if slide.suggested_visual.startswith("Use the source page image as the main visual"):
            markers.append("fallback_visual_template")
        if slide.speaker_script.startswith("继续讲解本页内容："):
            markers.append("fallback_continuation_template")
        if slide.speaker_script.startswith("欢迎进入《") and "课程核心内容" in slide.speaker_script:
            markers.append("fallback_cover_template")
        if slide.title == "课程总结" and slide.speaker_script.startswith("最后把本次课程的核心结论串联起来"):
            markers.append("fallback_summary_template")
        fallback_marker_count += len(markers)

        warnings = []
        if re.search(r"小组成员\s*[：:]", slide.speaker_script):
            warnings.append("speaker_script_contains_cover_credits")
        if slide.speaker_script.startswith(("本组知识单元", "本章", "本单元", "本节")):
            warnings.append("speaker_script_looks_like_section_summary")
        slide_diagnostics.append(
            SlideScriptDiagnosis(
                slide_id=slide.id,
                title=slide.title,
                source_section_id=source_section_id,
                source_field=source_field,
                exact_learning_content_copy=exact_copy,
                fallback_markers=markers,
                warnings=warnings,
            )
        )

    direct_ratio = direct_count / max(len(body_slides), 1)
    sections_with_body_slide = {
        section_id
        for slide in body_slides
        for section_id in slide.source_section_ids
        if section_id in sections
    }
    one_body_slide_per_section = (
        len(body_slides) == len(content.sections)
        and len(sections_with_body_slide) == len(content.sections)
    )
    fallback_score = min(
        1.0,
        direct_ratio * 0.65
        + (0.2 if fallback_marker_count >= 2 else 0.0)
        + (0.15 if one_body_slide_per_section else 0.0),
    )
    likely_fallback = fallback_score >= 0.65

    reasons = []
    if direct_count:
        reasons.append(
            f"{direct_count} 张正文页讲稿与 LearningContent 字段完全相同（占正文页 {direct_ratio:.0%}）。"
        )
    if fallback_marker_count:
        reasons.append(f"检测到 {fallback_marker_count} 个内置 fallback 模板标记。")
    if one_body_slide_per_section:
        reasons.append("正文页与 LearningContent section 呈一对一映射，没有内容层面的重新拆分或合并。")
    if plan.generation_source == "fallback" and plan.fallback_reason:
        reasons.append(f"生成时记录的 fallback 原因：{plan.fallback_reason}")
        exact_reason = plan.fallback_reason
    elif likely_fallback and llm_configured:
        reasons.append(
            "当前 Presentation Planner 已配置 LLM，因此最可能是内容规划调用或结果校验失败后进入 fallback。"
        )
        exact_reason = (
            "该计划生成时尚未持久化捕获到的异常，无法从历史数据区分超时、JSON 解析失败、"
            "字段校验失败或 section 覆盖/顺序校验失败。"
        )
    elif likely_fallback:
        reasons.append("Presentation Planner 未配置 LLM，系统直接进入 fallback。")
        exact_reason = "no_llm_configured"
    else:
        exact_reason = None

    return PresentationPlanDiagnosis(
        presentation_plan_id=plan.id,
        content_id=content.id,
        likely_fallback=likely_fallback,
        fallback_confidence=fallback_score,
        llm_configured=llm_configured,
        provider=provider,
        model=model,
        exact_historical_reason=exact_reason,
        direct_script_count=direct_count,
        body_slide_count=len(body_slides),
        one_body_slide_per_section=one_body_slide_per_section,
        reasons=reasons,
        slides=slide_diagnostics,
    )


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
