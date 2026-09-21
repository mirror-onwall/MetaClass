from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.paper_workflow.final_paper_deck_page_analyzer import (
    FinalSlideObservation,
)
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
    NativePaperDeckSlide,
)
from metaclass.modules.paper_workflow.schemas import (
    PaperAnalysis,
    PaperSourceBundle,
    SourceReference,
)


class GroundedSlidePacketError(ValueError):
    """Raised when semantic alignment attempts to escape authoritative evidence."""


class NumericVerification(SchemaModel):
    mention: str = Field(min_length=1)
    status: Literal["verified", "figure_verified", "unverified"]
    source_refs: list[SourceReference] = Field(default_factory=list)


class SlideRepairDirective(SchemaModel):
    slide_id: str = Field(min_length=1)
    prompt_path: str = Field(min_length=1)
    unverified_mentions: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    scope: Literal["single_slide"] = "single_slide"


class GroundedSlidePacket(SchemaModel):
    slide_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    final_page: FinalSlideObservation
    authoring_intent: NativePaperDeckSlide
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"]
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    previous_slide_id: str | None = None
    next_slide_id: str | None = None
    numeric_verifications: list[NumericVerification] = Field(default_factory=list)
    repair_directive: SlideRepairDirective | None = None

    @model_validator(mode="after")
    def require_repair_for_unverified_numbers(self) -> GroundedSlidePacket:
        has_unverified = any(item.status == "unverified" for item in self.numeric_verifications)
        if has_unverified != (self.repair_directive is not None):
            raise ValueError("unverified numbers and repair directive must appear together")
        return self


class _SlideMapping(SchemaModel):
    slide_id: str
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"] = "contextual"


class _MappingBatch(SchemaModel):
    slides: list[_SlideMapping]


class GroundedSlidePacketBuilder:
    """Join paper authority, semantic analysis, authoring intent, and final pixels."""

    _number = re.compile(
        r"(?<![\w.])(?:[<>~=±≤≥]\s*)?-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?"
    )

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def build(
        self,
        *,
        source_bundle: PaperSourceBundle,
        paper_content: str,
        analysis: PaperAnalysis,
        manifest: NativePaperDeckManifest,
        observations: list[FinalSlideObservation],
        knowledge_units: list[KnowledgeUnit],
    ) -> list[GroundedSlidePacket]:
        self._validate_pages(manifest, observations)
        mappings = self._map_slides(analysis, source_bundle, manifest, observations)
        mappings_by_id = {item.slide_id: item for item in mappings.slides}
        expected_ids = [slide.id for slide in manifest.slides]
        if list(mappings_by_id) != expected_ids:
            raise GroundedSlidePacketError("semantic mapping must cover slides once and in order")

        claims = {item.id: item for item in analysis.claims}
        results = {item.id: item for item in analysis.quantitative_results}
        assets = {item.id: item for item in source_bundle.assets}
        packets: list[GroundedSlidePacket] = []
        for index, slide in enumerate(manifest.slides):
            observation = observations[index]
            mapping = mappings_by_id[slide.id]
            self._validate_mapping(mapping, claims=set(claims), results=set(results), assets=set(assets))
            number_checks = self._verify_numbers(
                observation,
                paper_content=paper_content,
                source_bundle=source_bundle,
                analysis=analysis,
                selected_asset_ids=mapping.asset_ids,
            )
            unverified = [item.mention for item in number_checks if item.status == "unverified"]
            safe_result_ids = self._safe_result_ids(mapping.result_ids, results, unverified)
            safe_mapping = mapping.model_copy(update={"result_ids": safe_result_ids})
            refs = self._source_refs(safe_mapping, claims=claims, results=results, assets=assets)
            packets.append(
                GroundedSlidePacket(
                    slide_id=slide.id,
                    order=slide.order,
                    final_page=observation,
                    authoring_intent=slide,
                    claim_ids=mapping.claim_ids,
                    result_ids=safe_result_ids,
                    asset_ids=mapping.asset_ids,
                    source_refs=refs,
                    evidence_strength=mapping.evidence_strength,
                    knowledge_unit_ids=self._knowledge_units(
                        knowledge_units,
                        slide=slide,
                        claim_ids=mapping.claim_ids,
                        result_ids=safe_result_ids,
                        source_refs=refs,
                    ),
                    previous_slide_id=(manifest.slides[index - 1].id if index else None),
                    next_slide_id=(
                        manifest.slides[index + 1].id
                        if index + 1 < len(manifest.slides)
                        else None
                    ),
                    numeric_verifications=number_checks,
                    repair_directive=(
                        SlideRepairDirective(
                            slide_id=slide.id,
                            prompt_path=slide.prompt_path,
                            unverified_mentions=unverified,
                            instruction=(
                                "定向修改本页 prompt：删除或替换无法由论文原文或对应 Figure/"
                                "Table 验证的数字；仅重新生成本页、重合成 PDF，并重新执行页面分析与数字验证。"
                            ),
                        )
                        if unverified
                        else None
                    ),
                )
            )
        return packets

    def _map_slides(
        self,
        analysis: PaperAnalysis,
        source_bundle: PaperSourceBundle,
        manifest: NativePaperDeckManifest,
        observations: list[FinalSlideObservation],
    ) -> _MappingBatch:
        payload = {
            "claims": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "source_refs": [ref.model_dump(mode="json") for ref in item.source_refs],
                }
                for item in analysis.claims
            ],
            "results": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "source_refs": [ref.model_dump(mode="json") for ref in item.source_refs],
                }
                for item in analysis.quantitative_results
            ],
            "assets": [
                {"id": item.id, "type": item.type, "page_no": item.page_no, "caption": item.caption}
                for item in source_bundle.assets
            ],
            "slides": [
                {
                    "slide_id": slide.id,
                    "order": slide.order,
                    "outline": {
                        "title": slide.title_hint,
                        "role": slide.role,
                        "message": slide.message,
                        "visual_intent": slide.visual_intent,
                        "planned_text": slide.planned_text,
                        "evidence_hint": slide.evidence_hint,
                    },
                    "final_page_observation": observations[index].model_dump(mode="json"),
                }
                for index, slide in enumerate(manifest.slides)
            ],
        }
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "GROUNDED_FINAL_SLIDE_ALIGNMENT_V1\n"
                        "Map each slide to only the supplied claim/result/asset IDs. Paper analysis "
                        "defines semantics; final-page observations only describe visible pixels and "
                        "are never factual evidence. Return JSON {slides:[{slide_id,claim_ids,"
                        "result_ids,asset_ids,evidence_strength}]}. Preserve slide order."
                    ),
                ),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.0,
        )
        try:
            return self._parse_mapping_response(response)
        except (ValueError, json.JSONDecodeError):
            # Models occasionally return a truncated or schema-shaped object even
            # when JSON mode is enabled. Retry with a compact repair prompt instead
            # of repeating the large paper payload or failing the whole workflow.
            repair_payload = {
                "expected_slide_ids": [slide.id for slide in manifest.slides],
                "allowed_claim_ids": [item.id for item in analysis.claims],
                "allowed_result_ids": [item.id for item in analysis.quantitative_results],
                "allowed_asset_ids": [item.id for item in source_bundle.assets],
                "invalid_response": response,
            }
            repaired = self.llm.complete_json(
                [
                    LLMMessage(
                        role="system",
                        content=(
                            "REPAIR_GROUNDED_FINAL_SLIDE_ALIGNMENT_V1\n"
                            "Repair the supplied response into exactly this JSON shape: "
                            "{slides:[{slide_id,claim_ids,result_ids,asset_ids,evidence_strength}]}. "
                            "Include every expected slide exactly once and in the given order. "
                            "Use only allowed IDs. evidence_strength must be direct, derived, or "
                            "contextual. Return JSON only."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(repair_payload, ensure_ascii=False),
                    ),
                ],
                temperature=0.0,
            )
        try:
            return self._parse_mapping_response(repaired)
        except (ValueError, json.JSONDecodeError) as exc:
            raise GroundedSlidePacketError("semantic slide mapping is invalid") from exc

    @staticmethod
    def _parse_mapping_response(response: str) -> _MappingBatch:
        start, end = response.find("{"), response.rfind("}")
        if start < 0 or end < start:
            raise ValueError("semantic mapping response does not contain a JSON object")
        return _MappingBatch.model_validate_json(response[start : end + 1])

    @staticmethod
    def _validate_pages(
        manifest: NativePaperDeckManifest,
        observations: list[FinalSlideObservation],
    ) -> None:
        if manifest.slide_count != len(observations):
            raise GroundedSlidePacketError("manifest and final observations differ in length")
        if [slide.id for slide in manifest.slides] != [item.slide_id for item in observations]:
            raise GroundedSlidePacketError("manifest and final observations differ in identity/order")

    @staticmethod
    def _validate_mapping(
        mapping: _SlideMapping,
        *,
        claims: set[str],
        results: set[str],
        assets: set[str],
    ) -> None:
        unknown = {
            "claim_ids": set(mapping.claim_ids) - claims,
            "result_ids": set(mapping.result_ids) - results,
            "asset_ids": set(mapping.asset_ids) - assets,
        }
        invalid = {key: sorted(value) for key, value in unknown.items() if value}
        if invalid:
            raise GroundedSlidePacketError(f"semantic mapping references unknown IDs: {invalid}")

    @staticmethod
    def _source_refs(mapping, *, claims, results, assets) -> list[SourceReference]:
        refs = [ref for item_id in mapping.claim_ids for ref in claims[item_id].source_refs]
        refs += [ref for item_id in mapping.result_ids for ref in results[item_id].source_refs]
        refs += [
            SourceReference(page_no=assets[item_id].page_no, asset_id=item_id)
            for item_id in mapping.asset_ids
        ]
        return list({json.dumps(ref.model_dump(mode="json"), sort_keys=True): ref for ref in refs}.values())

    def _verify_numbers(
        self,
        observation: FinalSlideObservation,
        *,
        paper_content: str,
        source_bundle: PaperSourceBundle,
        analysis: PaperAnalysis,
        selected_asset_ids: list[str],
    ) -> list[NumericVerification]:
        source_numbers = {self._normalize_number(item) for item in self._number.findall(paper_content)}
        source_numbers.update(
            self._normalize_number(item)
            for block in source_bundle.blocks
            for item in self._number.findall(block.text)
        )
        assets = {item.id: item for item in source_bundle.assets}
        selected_pages = {assets[item].page_no for item in selected_asset_ids}
        result_numbers: dict[str, list[SourceReference]] = {}
        for result in analysis.quantitative_results:
            value = "" if result.value is None else str(result.value)
            for number in self._number.findall(f"{result.statement} {value}"):
                result_numbers.setdefault(self._normalize_number(number), []).extend(result.source_refs)
        checks = []
        for mention in observation.quantitative_mentions:
            normalized = self._normalize_number(mention)
            if normalized in source_numbers:
                checks.append(NumericVerification(mention=mention, status="verified"))
                continue
            refs = result_numbers.get(normalized, [])
            if refs and any(ref.asset_id in selected_asset_ids or ref.page_no in selected_pages for ref in refs):
                checks.append(
                    NumericVerification(
                        mention=mention,
                        status="figure_verified",
                        source_refs=refs,
                    )
                )
                continue
            checks.append(NumericVerification(mention=mention, status="unverified"))
        return checks

    def _safe_result_ids(self, result_ids, results, unverified_numbers) -> list[str]:
        blocked = {self._normalize_number(item) for item in unverified_numbers}
        return [
            result_id
            for result_id in result_ids
            if not (
                {self._normalize_number(item) for item in self._number.findall(results[result_id].statement)}
                & blocked
            )
        ]

    @staticmethod
    def _knowledge_units(
        units: list[KnowledgeUnit],
        *,
        slide: NativePaperDeckSlide,
        claim_ids: list[str],
        result_ids: list[str],
        source_refs: list[SourceReference],
    ) -> list[str]:
        source_ids = {slide.id, *claim_ids, *result_ids}
        pages = {ref.page_no for ref in source_refs}
        return [
            unit.id
            for unit in units
            if source_ids.intersection(unit.source_unit_ids)
            or pages.intersection(ref.page_no for ref in unit.page_refs)
        ]

    @staticmethod
    def _normalize_number(value: str) -> str:
        return re.sub(r"[\s,]", "", value).casefold().replace("×", "x")
