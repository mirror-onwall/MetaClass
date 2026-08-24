from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.paper_workflow.schemas import (
    FigureAsset,
    FigureCatalog,
    OutlineSlide,
    PaperAnalysis,
    PaperClaim,
    PaperSourceBundle,
    PresentationOutline,
    QuantitativeResult,
    SlideEvidence,
    SourceReference,
)

from .paper_evidence import PaperEvidenceContext, PaperEvidenceRetriever


class PaperSlideEvidencePacket(SchemaModel):
    """Complete, evidence-grounded input for composing one classroom narration."""

    slide_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    visible_text: str = ""
    key_points: list[str] = Field(default_factory=list)
    authoring_note: str = ""
    claims: list[PaperClaim] = Field(default_factory=list)
    quantitative_results: list[QuantitativeResult] = Field(default_factory=list)
    figures: list[FigureAsset] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)
    evidence_contexts: list[PaperEvidenceContext] = Field(default_factory=list)
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    knowledge_unit_titles: list[str] = Field(default_factory=list)
    evidence_strength: str = "direct"
    previous_slide_title: str | None = None
    next_slide_title: str | None = None
    target_seconds: int = Field(default=60, ge=15)


class PaperSlideNarration(SchemaModel):
    slide_id: str = Field(min_length=1)
    opening: str = Field(min_length=1)
    main_explanation: str = Field(min_length=1)
    evidence_interpretation: str = Field(min_length=1)
    teaching_emphasis: str = Field(min_length=1)
    transition: str = ""
    speaker_script: str = Field(min_length=1)
    used_claim_ids: list[str] = Field(default_factory=list)
    used_result_ids: list[str] = Field(default_factory=list)
    used_source_refs: list[SourceReference] = Field(default_factory=list)


class PaperNarrationBatch(SchemaModel):
    slides: list[PaperSlideNarration] = Field(min_length=1)


@dataclass(frozen=True)
class NarrationValidationIssue:
    slide_id: str
    code: str
    message: str


class SlideEvidencePacketBuilder:
    def build(
        self,
        *,
        pages: list[PageMetadata],
        analysis: PaperAnalysis,
        outline: PresentationOutline,
        evidence: SlideEvidence,
        figures: FigureCatalog,
        source_bundle: PaperSourceBundle,
        knowledge_units: list[KnowledgeUnit],
        authoring_notes: dict[str, str],
        duration_minutes: int | None,
    ) -> list[PaperSlideEvidencePacket]:
        pages_by_no = {page.page_no: page for page in pages}
        claims_by_id = {claim.id: claim for claim in analysis.claims}
        figures_by_id = {figure.id: figure for figure in figures.figures}
        evidence_by_slide = {entry.slide_id: entry for entry in evidence.slides}
        retriever = PaperEvidenceRetriever(source_bundle)
        unit_by_source_id: dict[str, list[KnowledgeUnit]] = {}
        for unit in knowledge_units:
            for source_id in unit.source_unit_ids:
                unit_by_source_id.setdefault(source_id, []).append(unit)
        seconds_per_slide = max(
            30,
            round((duration_minutes or max(5, len(outline.slides))) * 60 / len(outline.slides)),
        )

        packets: list[PaperSlideEvidencePacket] = []
        for index, slide in enumerate(outline.slides):
            entry = evidence_by_slide[slide.id]
            claims = [claims_by_id[claim_id] for claim_id in entry.claim_ids]
            results = [
                result
                for result in analysis.quantitative_results
                if self._result_matches(result, entry.source_refs, claims)
            ]
            slide_figures = [figures_by_id[asset_id] for asset_id in entry.asset_ids]
            units = self._units_for_slide(
                knowledge_units=knowledge_units,
                unit_by_source_id=unit_by_source_id,
                slide=slide,
                claim_ids=entry.claim_ids,
                result_ids=[result.id for result in results],
            )
            page = pages_by_no[slide.order]
            packets.append(
                PaperSlideEvidencePacket(
                    slide_id=slide.id,
                    order=slide.order,
                    title=slide.title,
                    purpose=slide.purpose,
                    visible_text=" ".join(page.raw_text.split()),
                    key_points=slide.key_points,
                    authoring_note=authoring_notes.get(slide.id) or slide.speaker_note,
                    claims=claims,
                    quantitative_results=results,
                    figures=slide_figures,
                    source_refs=entry.source_refs,
                    evidence_contexts=retriever.retrieve(entry.source_refs),
                    knowledge_unit_ids=[unit.id for unit in units],
                    knowledge_unit_titles=[unit.title for unit in units],
                    evidence_strength=entry.evidence_strength,
                    previous_slide_title=(outline.slides[index - 1].title if index else None),
                    next_slide_title=(
                        outline.slides[index + 1].title if index + 1 < len(outline.slides) else None
                    ),
                    target_seconds=seconds_per_slide,
                )
            )
        return packets

    @staticmethod
    def _result_matches(
        result: QuantitativeResult,
        refs: list[SourceReference],
        claims: list[PaperClaim],
    ) -> bool:
        result_pages = {ref.page_no for ref in result.source_refs}
        evidence_pages = {ref.page_no for ref in refs}
        claim_pages = {ref.page_no for claim in claims for ref in claim.source_refs}
        return bool(result_pages & (evidence_pages | claim_pages))

    @staticmethod
    def _units_for_slide(
        *,
        knowledge_units: list[KnowledgeUnit],
        unit_by_source_id: dict[str, list[KnowledgeUnit]],
        slide: OutlineSlide,
        claim_ids: list[str],
        result_ids: list[str],
    ) -> list[KnowledgeUnit]:
        selected: dict[str, KnowledgeUnit] = {}
        for source_id in [*claim_ids, *result_ids, slide.id]:
            for unit in unit_by_source_id.get(source_id, []):
                selected[unit.id] = unit
        if not selected:
            for unit in knowledge_units:
                if any(ref.page_no == slide.order for ref in unit.page_refs):
                    selected[unit.id] = unit
        return list(selected.values())


class PaperClassroomComposer:
    """Compose readable narration without introducing facts beyond the packet."""

    def compose(
        self,
        packet: PaperSlideEvidencePacket,
        *,
        audience: str,
        language: str,
    ) -> PaperSlideNarration:
        if not language.lower().startswith("zh"):
            return self._compose_english(packet, audience)

        if packet.previous_slide_title:
            opening = (
                f"承接上一页关于“{packet.previous_slide_title}”的讨论，"
                f"现在把注意力转向“{packet.title}”。"
            )
        else:
            opening = (
                f"这次汇报从“{packet.title}”开始。我们先明确论文试图解决的问题，"
                f"再判断作者给出的证据是否足以支持结论。"
            )

        points = "；".join(packet.key_points)
        visible = self._trim_visible_text(packet.visible_text, packet.title)
        main_parts = [packet.purpose]
        if points:
            main_parts.append(f"需要抓住的关键信息是：{points}。")
        if visible:
            main_parts.append(f"从页面呈现的内容来看，{visible}")
        main_explanation = "".join(main_parts)

        evidence_parts = []
        if packet.claims:
            evidence_parts.append(
                "论文在这里提出的主张是："
                + "；".join(claim.statement for claim in packet.claims)
                + "。"
            )
        if packet.quantitative_results:
            evidence_parts.append(
                "对应的定量证据包括："
                + "；".join(result.statement for result in packet.quantitative_results)
                + "。"
            )
        if packet.figures:
            evidence_parts.append(
                "图表证据中需要重点观察的是："
                + "；".join(figure.caption for figure in packet.figures)
                + "。讲解时要同时说明比较对象、指标和变化方向，不能只复述图题。"
            )
        if packet.evidence_contexts:
            selected_contexts = packet.evidence_contexts[:2]
            selected_texts = {context.exact_text for context in selected_contexts}
            evidence_parts.append(
                "原文中与这一页直接相关的表述是：“"
                + "”“".join(context.exact_text for context in selected_contexts)
                + "”。这里的解释应保留作者原有的条件和限定，不能把局部结果扩大为普遍结论。"
            )
            primary = selected_contexts[0]
            if primary.before_text and primary.before_text not in selected_texts:
                evidence_parts.append(
                    f"为了理解这句话的条件，前文还交代了：“{primary.before_text}”。"
                )
            if primary.after_text and primary.after_text not in selected_texts:
                evidence_parts.append(f"紧接着作者进一步说明：“{primary.after_text}”。")
        source_pages = sorted({ref.page_no for ref in packet.source_refs})
        if source_pages:
            evidence_parts.append(
                "这些信息可以回到原论文第"
                + "、".join(str(page) for page in source_pages)
                + "页核对。"
            )
        evidence_interpretation = "".join(evidence_parts) or (
            "这一页承担的是叙事衔接作用，结论应限定在页面已有信息内，"
            "不要把背景说明扩展成论文没有提出的新主张。"
        )

        if packet.evidence_strength == "direct":
            teaching_emphasis = (
                f"面向{audience}，这里要把“论文报告了什么”和“我们如何解释它”分开。"
                "先陈述可核对的事实，再说明它对研究问题意味着什么。"
            )
        else:
            teaching_emphasis = (
                f"面向{audience}，需要明确提醒：这里属于{packet.evidence_strength}证据，"
                "可以用于理解论文叙事，但不能当作未经限定的直接结论。"
            )

        transition = (
            f"在这个基础上，下一页将进入“{packet.next_slide_title}”，继续检查论证链条中的下一环。"
            if packet.next_slide_title
            else "最后回到论文的研究问题：结论的价值与边界，都应由前面核对过的证据共同决定。"
        )
        script = (
            f"{opening}\n\n{main_explanation}\n\n{evidence_interpretation}"
            f"\n\n{teaching_emphasis}\n\n{transition}"
        )
        return PaperSlideNarration(
            slide_id=packet.slide_id,
            opening=opening,
            main_explanation=main_explanation,
            evidence_interpretation=evidence_interpretation,
            teaching_emphasis=teaching_emphasis,
            transition=transition,
            speaker_script=script,
            used_claim_ids=[claim.id for claim in packet.claims],
            used_result_ids=[result.id for result in packet.quantitative_results],
            used_source_refs=packet.source_refs,
        )

    def _compose_english(
        self, packet: PaperSlideEvidencePacket, audience: str
    ) -> PaperSlideNarration:
        opening = (
            f"Building on {packet.previous_slide_title}, we now turn to {packet.title}."
            if packet.previous_slide_title
            else f"We begin with {packet.title} and the question that motivates the paper."
        )
        main = packet.purpose + (
            " The key points are: " + "; ".join(packet.key_points) + "."
            if packet.key_points
            else ""
        )
        evidence = (
            " ".join(
                [
                    *(f"The paper claims that {claim.statement}" for claim in packet.claims),
                    *(
                        f"The reported quantitative evidence is: {item.statement}"
                        for item in packet.quantitative_results
                    ),
                    *(
                        f"The relevant figure is captioned: {figure.caption}"
                        for figure in packet.figures
                    ),
                    *(
                        f'The source text states: "{context.exact_text}"'
                        for context in packet.evidence_contexts[:2]
                    ),
                ]
            )
            or "This slide provides context and should not be presented as a new empirical claim."
        )
        emphasis = (
            f"For {audience}, separate what the paper directly reports from our interpretation "
            "of why that evidence matters."
        )
        transition = (
            f"With that distinction in place, we can move to {packet.next_slide_title}."
            if packet.next_slide_title
            else "We can now return to the research question and assess both the contribution and its limits."
        )
        return PaperSlideNarration(
            slide_id=packet.slide_id,
            opening=opening,
            main_explanation=main,
            evidence_interpretation=evidence,
            teaching_emphasis=emphasis,
            transition=transition,
            speaker_script=(f"{opening}\n\n{main}\n\n{evidence}\n\n{emphasis}\n\n{transition}"),
            used_claim_ids=[claim.id for claim in packet.claims],
            used_result_ids=[result.id for result in packet.quantitative_results],
            used_source_refs=packet.source_refs,
        )

    @staticmethod
    def _trim_visible_text(text: str, title: str) -> str:
        compact = " ".join(text.split())
        if compact.startswith(title):
            compact = compact[len(title) :].strip(" ：:。.")
        if not compact:
            return ""
        return compact[:320].rstrip() + ("。" if not compact[:320].endswith(("。", ".")) else "")


class LLMPaperClassroomComposer:
    prompt_version = "paper-classroom-narration-v1"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.prompt_path = Path(__file__).with_name("paper_narration_prompt.md")

    def compose(
        self,
        packets: list[PaperSlideEvidencePacket],
        *,
        audience: str,
        language: str,
    ) -> list[PaperSlideNarration]:
        payload = {
            "audience": audience,
            "language": language,
            "slides": [packet.model_dump(mode="json") for packet in packets],
            "output_schema": PaperNarrationBatch.model_json_schema(),
        }
        raw = self.provider.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=self.prompt_path.read_text(encoding="utf-8"),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(payload, ensure_ascii=False),
                ),
            ],
            temperature=0.3,
        )
        return PaperNarrationBatch.model_validate_json(raw).slides


class PaperNarrationValidator:
    PLACEHOLDERS = ("TODO", "TBD", "placeholder", "待补充", "待完善")

    def validate(
        self,
        *,
        packets: list[PaperSlideEvidencePacket],
        narrations: list[PaperSlideNarration],
    ) -> list[NarrationValidationIssue]:
        issues: list[NarrationValidationIssue] = []
        narration_by_slide = {item.slide_id: item for item in narrations}
        if len(narration_by_slide) != len(narrations):
            issues.append(
                NarrationValidationIssue("*", "duplicate_slide", "Duplicate narration slide id")
            )
        for packet in packets:
            narration = narration_by_slide.get(packet.slide_id)
            if narration is None:
                issues.append(
                    NarrationValidationIssue(packet.slide_id, "missing", "Narration is missing")
                )
                continue
            script = narration.speaker_script.strip()
            if len(script) < 120:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id, "too_short", "Narration is not detailed enough to read"
                    )
                )
            if packet.authoring_note and self._compact(script) == self._compact(
                packet.authoring_note
            ):
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "authoring_note_reused",
                        "Authoring note was reused as final narration",
                    )
                )
            if any(marker.lower() in script.lower() for marker in self.PLACEHOLDERS):
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id, "placeholder", "Narration has placeholder text"
                    )
                )
            expected_claims = {claim.id for claim in packet.claims}
            expected_results = {result.id for result in packet.quantitative_results}
            if set(narration.used_claim_ids) != expected_claims:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "claim_coverage",
                        "Narration claim usage does not match the evidence packet",
                    )
                )
            if set(narration.used_result_ids) != expected_results:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "result_coverage",
                        "Narration result usage does not match the evidence packet",
                    )
                )
            allowed_refs = {(ref.page_no, ref.block_id, ref.asset_id) for ref in packet.source_refs}
            used_refs = {
                (ref.page_no, ref.block_id, ref.asset_id) for ref in narration.used_source_refs
            }
            if not used_refs.issubset(allowed_refs) or (allowed_refs and not used_refs):
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "source_ref_coverage",
                        "Narration source refs are missing or outside the evidence packet",
                    )
                )
            if packet.source_refs and not packet.evidence_contexts:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "missing_original_context",
                        "Source references did not resolve to original paper context",
                    )
                )
            unsupported_numbers = self._numbers(script) - self._authorized_numbers(packet)
            if unsupported_numbers:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "unsupported_number",
                        f"Narration contains unsupported numbers: {sorted(unsupported_numbers)}",
                    )
                )
            if packet.next_slide_title and packet.next_slide_title not in narration.transition:
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "missing_transition",
                        "Narration does not introduce next slide",
                    )
                )
        return issues

    @staticmethod
    def _compact(value: str) -> str:
        return "".join(value.split()).lower()

    @staticmethod
    def _numbers(value: str) -> set[str]:
        return set(re.findall(r"\d+(?:\.\d+)?%?", value))

    @classmethod
    def _authorized_numbers(cls, packet: PaperSlideEvidencePacket) -> set[str]:
        texts = [
            packet.title,
            packet.purpose,
            packet.visible_text,
            *packet.key_points,
            *(claim.statement for claim in packet.claims),
            *(result.statement for result in packet.quantitative_results),
            *(
                str(result.value)
                for result in packet.quantitative_results
                if result.value is not None
            ),
            *(figure.caption for figure in packet.figures),
            *(context.exact_text for context in packet.evidence_contexts),
            *(context.before_text for context in packet.evidence_contexts),
            *(context.after_text for context in packet.evidence_contexts),
            *(str(ref.page_no) for ref in packet.source_refs),
        ]
        return cls._numbers(" ".join(texts))
