from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    SourceAsset,
    SourceBlock,
    SourceReference,
)

from .grounded_slide_packet import GroundedSlidePacket
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


class GroundedPaperNarrationPacket(SchemaModel):
    slide: GroundedSlidePacket
    knowledge_units: list[KnowledgeUnit] = Field(default_factory=list)
    claims: list[PaperClaim] = Field(default_factory=list)
    quantitative_results: list[QuantitativeResult] = Field(default_factory=list)
    source_blocks: list[SourceBlock] = Field(default_factory=list)
    source_assets: list[SourceAsset] = Field(default_factory=list)
    paper_deck_analysis: str = ""
    outline_message: str
    previous_message: str | None = None
    next_message: str | None = None
    verified_numbers: list[str] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)


class SlideNarration(SchemaModel):
    slide_id: str = Field(min_length=1)
    speaker_script: str = Field(min_length=1)
    transition: str = ""
    used_claim_ids: list[str] = Field(default_factory=list)
    used_source_refs: list[SourceReference] = Field(default_factory=list)
    used_asset_ids: list[str] = Field(default_factory=list)
    validation_status: Literal["pending", "validated", "invalid"] = "pending"


class GroundedNarrationBatch(SchemaModel):
    slides: list[SlideNarration] = Field(min_length=1)


class GroundedNarrationPacketBuilder:
    """Resolve GroundedSlidePacket IDs into authoritative narration inputs."""

    _number = re.compile(
        r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?"
    )

    def build(
        self,
        *,
        packets: list[GroundedSlidePacket],
        knowledge_units: list[KnowledgeUnit],
        analysis: PaperAnalysis,
        source_bundle: PaperSourceBundle,
        paper_deck_analysis: str,
    ) -> list[GroundedPaperNarrationPacket]:
        claims = {item.id: item for item in analysis.claims}
        results = {item.id: item for item in analysis.quantitative_results}
        units = {item.id: item for item in knowledge_units}
        blocks = {item.id: item for item in source_bundle.blocks}
        assets = {item.id: item for item in source_bundle.assets}
        output: list[GroundedPaperNarrationPacket] = []
        for index, packet in enumerate(packets):
            unknown_units = set(packet.knowledge_unit_ids) - units.keys()
            if unknown_units:
                raise ValueError(f"narration packet references unknown knowledge units: {unknown_units}")
            resolved_unit_ids = list(
                dict.fromkeys(
                    [
                        *packet.knowledge_unit_ids,
                        *(
                            unit.id
                            for unit in knowledge_units
                            if packet.slide_id in unit.source_unit_ids
                            or any(
                                ref.material_id != source_bundle.material_id
                                and ref.page_no == packet.order
                                for ref in unit.page_refs
                            )
                        ),
                    ]
                )
            )
            selected_blocks = []
            for ref in packet.source_refs:
                if ref.block_id and ref.block_id in blocks:
                    selected_blocks.append(blocks[ref.block_id])
                else:
                    selected_blocks.extend(
                        item for item in source_bundle.blocks if item.page_no == ref.page_no
                    )
            asset_ids = list(
                dict.fromkeys(
                    [*packet.asset_ids, *(ref.asset_id for ref in packet.source_refs if ref.asset_id)]
                )
            )
            selected_claims = [claims[item] for item in packet.claim_ids]
            selected_results = [results[item] for item in packet.result_ids]
            selected_blocks = self._dedupe_by_id(selected_blocks)
            selected_assets = [assets[item] for item in asset_ids if item in assets]
            authoritative_number_text = " ".join(
                [
                    *(item.statement for item in selected_claims),
                    *(item.statement for item in selected_results),
                    *(
                        str(item.value)
                        for item in selected_results
                        if item.value is not None
                    ),
                    *(item.text for item in selected_blocks),
                    *(item.caption or "" for item in selected_assets),
                ]
            )
            verified_numbers = list(
                dict.fromkeys(
                    [
                        *(
                            item.mention
                            for item in packet.numeric_verifications
                            if item.status in {"verified", "figure_verified"}
                        ),
                        *self._number.findall(authoritative_number_text),
                    ]
                )
            )
            output.append(
                GroundedPaperNarrationPacket(
                    slide=packet,
                    knowledge_units=[units[item] for item in resolved_unit_ids],
                    claims=selected_claims,
                    quantitative_results=selected_results,
                    source_blocks=selected_blocks,
                    source_assets=selected_assets,
                    paper_deck_analysis=paper_deck_analysis[:12000],
                    outline_message=packet.authoring_intent.message,
                    previous_message=(
                        packets[index - 1].authoring_intent.message if index else None
                    ),
                    next_message=(
                        packets[index + 1].authoring_intent.message
                        if index + 1 < len(packets)
                        else None
                    ),
                    verified_numbers=verified_numbers,
                    unverified_numbers=[
                        item.mention
                        for item in packet.numeric_verifications
                        if item.status == "unverified"
                    ],
                )
            )
        return output

    @staticmethod
    def _dedupe_by_id(items):
        return list({item.id: item for item in items}.values())


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
        visible = (
            ""
            if packet.evidence_contexts
            else self._trim_visible_text(packet.visible_text, packet.title)
        )
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
                + self._trim(packet.claims[0].statement, 180)
                + "。"
            )
        if packet.quantitative_results:
            evidence_parts.append(
                "对应的定量证据包括："
                + self._trim(packet.quantitative_results[0].statement, 180)
                + "。"
            )
        if packet.figures:
            evidence_parts.append(
                "图表证据中需要重点观察的是："
                + self._trim(packet.figures[0].caption, 160)
                + "。讲解时要同时说明比较对象、指标和变化方向，不能只复述图题。"
            )
        if packet.evidence_contexts:
            selected_contexts = packet.evidence_contexts[:1]
            selected_texts = {context.exact_text for context in selected_contexts}
            evidence_parts.append(
                "原文中与这一页直接相关的表述是：“"
                + "”“".join(self._trim(context.exact_text, 220) for context in selected_contexts)
                + "”。这里的解释应保留作者原有的条件和限定，不能把局部结果扩大为普遍结论。"
            )
            primary = selected_contexts[0]
            if primary.before_text and primary.before_text not in selected_texts:
                evidence_parts.append(
                    f"为了理解这句话的条件，前文还交代了：“{self._trim(primary.before_text, 120)}”。"
                )
            if primary.after_text and primary.after_text not in selected_texts:
                evidence_parts.append(
                    f"紧接着作者进一步说明：“{self._trim(primary.after_text, 120)}”。"
                )
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
    def _trim(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        shortened = compact[:limit].rstrip()
        # Never turn an authorized value such as 95.24% into a new value such
        # as 9 merely because the evidence quotation hit its character limit.
        shortened = re.sub(r"[A-Za-z0-9.%+\-]+$", "", shortened).rstrip()
        return shortened + "…"

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

    def __init__(self, provider: LLMProvider, *, batch_size: int = 4) -> None:
        if batch_size < 1:
            raise ValueError("paper narration batch_size must be positive")
        self.provider = provider
        self.batch_size = batch_size
        self.prompt_path = Path(__file__).with_name("paper_narration_prompt.md")
        self.grounded_prompt_path = Path(__file__).with_name("grounded_paper_narration_prompt.md")

    def compose(
        self,
        packets: list[PaperSlideEvidencePacket],
        *,
        audience: str,
        language: str,
    ) -> list[PaperSlideNarration]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        narrations: list[PaperSlideNarration] = []
        for start in range(0, len(packets), self.batch_size):
            batch = packets[start : start + self.batch_size]
            payload = {
                "audience": audience,
                "language": language,
                "slides": [packet.model_dump(mode="json") for packet in batch],
                "output_schema": PaperNarrationBatch.model_json_schema(),
            }
            raw = self.provider.complete_json(
                [
                    LLMMessage(role="system", content=prompt),
                    LLMMessage(
                        role="user",
                        content=json.dumps(payload, ensure_ascii=False),
                    ),
                ],
                temperature=0.3,
            )
            generated = PaperNarrationBatch.model_validate_json(raw).slides
            expected_ids = [packet.slide_id for packet in batch]
            if [item.slide_id for item in generated] != expected_ids:
                raise ValueError("paper narration batch changed slide ids or order")
            narrations.extend(generated)
        return narrations

    def compose_grounded(
        self,
        packets: list[GroundedPaperNarrationPacket],
        *,
        audience: str,
        language: str,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[SlideNarration]:
        prompt = self.grounded_prompt_path.read_text(encoding="utf-8")
        narrations: list[SlideNarration] = []
        for start in range(0, len(packets), self.batch_size):
            if check_cancelled:
                check_cancelled()
            batch = packets[start : start + self.batch_size]
            payload = {
                "audience": audience,
                "language": language,
                "slides": [packet.model_dump(mode="json") for packet in batch],
                "output_schema": GroundedNarrationBatch.model_json_schema(),
            }
            raw = self.provider.complete_json(
                [
                    LLMMessage(role="system", content=prompt),
                    LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
                ],
                temperature=0.3,
            )
            try:
                validated = self._parse_and_validate_grounded_batch(batch, raw)
            except ValueError as exc:
                repair_payload = {
                    "validation_error": str(exc),
                    "invalid_response": raw,
                    "slides": [packet.model_dump(mode="json") for packet in batch],
                    "output_schema": GroundedNarrationBatch.model_json_schema(),
                }
                repaired = self.provider.complete_json(
                    [
                        LLMMessage(
                            role="system",
                            content=(
                                "REPAIR_GROUNDED_PAPER_CLASSROOM_NARRATION_V1\n"
                                "Repair the narration JSON using only the supplied slide packets. "
                                "Preserve slide IDs and order. Remove unsupported numbers and claims; "
                                "copy used_source_refs and used_asset_ids exactly from each packet; "
                                "ensure each non-final slide has a transition. Return JSON only."
                            ),
                        ),
                        LLMMessage(
                            role="user",
                            content=json.dumps(repair_payload, ensure_ascii=False),
                        ),
                    ],
                    temperature=0.2,
                )
                validated = self._parse_and_validate_grounded_batch(batch, repaired)
            narrations.extend(validated)
        return narrations

    @classmethod
    def _parse_and_validate_grounded_batch(
        cls,
        batch: list[GroundedPaperNarrationPacket],
        raw: str,
    ) -> list[SlideNarration]:
        generated = GroundedNarrationBatch.model_validate_json(raw).slides
        expected_ids = [packet.slide.slide_id for packet in batch]
        if [item.slide_id for item in generated] != expected_ids:
            raise ValueError("grounded narration batch changed slide ids or order")
        return [
            cls._validate_grounded_narration(packet, narration)
            for packet, narration in zip(batch, generated, strict=True)
        ]

    @classmethod
    def _validate_grounded_narration(
        cls,
        packet: GroundedPaperNarrationPacket,
        narration: SlideNarration,
    ) -> SlideNarration:
        if narration.validation_status == "invalid":
            raise ValueError(f"grounded narration was marked invalid: {narration.slide_id}")
        if len(narration.speaker_script.strip()) < 80:
            raise ValueError(f"grounded narration is too short: {narration.slide_id}")
        if packet.next_message and not narration.transition.strip():
            raise ValueError(f"grounded narration lacks a transition: {narration.slide_id}")
        allowed_claims = {item.id for item in packet.claims}
        if not set(narration.used_claim_ids).issubset(allowed_claims):
            raise ValueError(f"grounded narration used an unauthorized claim: {narration.slide_id}")
        allowed_assets = {item.id for item in packet.source_assets}
        if not set(narration.used_asset_ids).issubset(allowed_assets):
            raise ValueError(f"grounded narration used an unauthorized asset: {narration.slide_id}")
        allowed_refs = {
            (item.page_no, item.block_id, item.asset_id): item
            for item in packet.slide.source_refs
        }
        used_ref_keys = {
            (item.page_no, item.block_id, item.asset_id)
            for item in narration.used_source_refs
        }
        if not used_ref_keys.issubset(allowed_refs):
            raise ValueError(f"grounded narration used an unauthorized source ref: {narration.slide_id}")
        script_numbers = cls._grounded_numbers(narration.speaker_script)
        verified_numbers = {
            cls._normalize_grounded_number(item) for item in packet.verified_numbers
        }
        unsupported = script_numbers - verified_numbers
        if unsupported:
            raise ValueError(
                f"grounded narration contains unverified numbers on {narration.slide_id}: "
                f"{sorted(unsupported)}"
            )
        canonical_refs = [
            allowed_refs[(item.page_no, item.block_id, item.asset_id)]
            for item in narration.used_source_refs
        ]
        return narration.model_copy(
            update={
                "used_source_refs": canonical_refs,
                "validation_status": "validated",
            }
        )

    @staticmethod
    def _grounded_numbers(value: str) -> set[str]:
        return {
            LLMPaperClassroomComposer._normalize_grounded_number(item)
            for item in re.findall(
                r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?",
                value,
            )
        }

    @staticmethod
    def _normalize_grounded_number(value: str) -> str:
        return re.sub(r"[\s,]", "", value).casefold().replace("×", "x")


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
            if packet.next_slide_title and not narration.transition.strip():
                issues.append(
                    NarrationValidationIssue(
                        packet.slide_id,
                        "missing_transition",
                        "Narration does not provide a transition to the next slide",
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
            packet.previous_slide_title or "",
            packet.next_slide_title or "",
            packet.purpose,
            packet.visible_text,
            packet.authoring_note,
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
