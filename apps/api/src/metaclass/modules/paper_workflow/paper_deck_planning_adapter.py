"""Convert a grounded Paper Deck planning result into a frozen PresentationPlan."""

from __future__ import annotations

import re

from metaclass.modules.paper_workflow.paper_deck_planning import (
    PaperDeckPlan,
    PaperDeckSlideEvidenceDraft,
    PlanningSlide,
    PlanningSlideEvidence,
)
from metaclass.modules.paper_workflow.schemas import PaperSourceBundle, SourceReference
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan


class PaperDeckPlanningAdapterError(ValueError):
    """Raised when planning artifacts cannot safely become a PresentationPlan."""


class PaperDeckPlanningAdapter:
    """Freeze Paper Deck narrative and evidence for downstream PPT providers.

    Structural identity and reference integrity are hard gates. Quantitative wording
    and prose traceability remain authoring-prompt responsibilities so natural teaching
    language is not rejected merely for paraphrasing the source.
    """

    _PLACEHOLDER_TITLE = re.compile(
        r"^(?:tbd|todo|untitled|slide\s*\d+|待定|标题)$", re.IGNORECASE
    )

    def convert(
        self,
        *,
        plan_id: str,
        content_id: str,
        planning: PaperDeckPlan,
        evidence: PaperDeckSlideEvidenceDraft,
        source_bundle: PaperSourceBundle,
        source_paper_material_id: str,
        paper_artifact_bundle_id: str | None = None,
        generation_model: str | None = None,
    ) -> PresentationPlan:
        self._validate(planning, evidence, source_bundle)
        evidence_by_slide = {item.slide_id: item for item in evidence.slides}
        sections_by_slide = self._sections_by_slide(planning, source_bundle)

        slides = [
            self._slide(
                item,
                evidence=evidence_by_slide[item.id],
                source_section_ids=sections_by_slide[item.id],
            )
            for item in planning.slides
        ]
        return PresentationPlan(
            id=plan_id,
            content_id=content_id,
            title=planning.paper_title.strip(),
            mode="paper_deck",
            source_paper_material_id=source_paper_material_id,
            paper_artifact_bundle_id=paper_artifact_bundle_id,
            slides=slides,
            generation_source="llm",
            generation_provider="paper-deck",
            generation_model=generation_model,
        )

    def _validate(
        self,
        planning: PaperDeckPlan,
        evidence: PaperDeckSlideEvidenceDraft,
        source_bundle: PaperSourceBundle,
    ) -> None:
        slide_ids = [item.id for item in planning.slides]
        if len(slide_ids) != len(set(slide_ids)):
            raise PaperDeckPlanningAdapterError("paper-deck slide ids must be unique")
        if [item.order for item in planning.slides] != list(range(1, len(slide_ids) + 1)):
            raise PaperDeckPlanningAdapterError(
                "paper-deck slide order must be continuous and start at 1"
            )

        claims = {item.id: item for item in planning.claims}
        assets = {item.id: item for item in source_bundle.assets}
        blocks = {item.id: item for item in source_bundle.blocks}
        evidence_ids = [item.slide_id for item in evidence.slides]
        if evidence_ids != slide_ids:
            raise PaperDeckPlanningAdapterError(
                "slide evidence must match every planning slide in presentation order"
            )

        for claim in planning.claims:
            self._validate_refs(claim.source_refs, blocks, assets, f"claim {claim.id}")
        for slide, packet in zip(planning.slides, evidence.slides, strict=True):
            title = slide.title.strip()
            if len(title) < 2 or self._PLACEHOLDER_TITLE.fullmatch(title):
                raise PaperDeckPlanningAdapterError(
                    f"slide {slide.id} requires a complete non-placeholder title"
                )
            if not slide.key_points or any(not item.strip() for item in slide.key_points):
                raise PaperDeckPlanningAdapterError(
                    f"slide {slide.id} requires non-empty key points"
                )
            unknown_claims = set(slide.claim_ids) - claims.keys()
            if unknown_claims:
                raise PaperDeckPlanningAdapterError(
                    f"slide {slide.id} references unknown claims: {sorted(unknown_claims)}"
                )
            unknown_assets = set(slide.asset_ids) - assets.keys()
            if unknown_assets:
                raise PaperDeckPlanningAdapterError(
                    f"slide {slide.id} references unknown assets: {sorted(unknown_assets)}"
                )
            self._validate_refs(slide.source_refs, blocks, assets, f"slide {slide.id}")
            if packet.slide_id != slide.id:
                raise PaperDeckPlanningAdapterError(
                    f"evidence identity mismatch for slide {slide.id}"
                )
            if set(packet.claim_ids) != set(slide.claim_ids):
                raise PaperDeckPlanningAdapterError(
                    f"evidence claim mapping differs on slide {slide.id}"
                )
            if set(packet.asset_ids) != set(slide.asset_ids):
                raise PaperDeckPlanningAdapterError(
                    f"evidence asset mapping differs on slide {slide.id}"
                )
            if self._ref_keys(packet.source_refs) != self._ref_keys(slide.source_refs):
                raise PaperDeckPlanningAdapterError(
                    f"evidence source mapping differs on slide {slide.id}"
                )
            self._validate_refs(packet.source_refs, blocks, assets, f"evidence {slide.id}")

        for selection in planning.figure_selections:
            if selection.asset_id not in assets:
                raise PaperDeckPlanningAdapterError(
                    f"figure selection references unknown asset {selection.asset_id}"
                )
            if set(selection.claim_ids) - claims.keys():
                raise PaperDeckPlanningAdapterError(
                    f"figure {selection.asset_id} references an unknown claim"
                )
            if set(selection.target_slide_ids) - set(slide_ids):
                raise PaperDeckPlanningAdapterError(
                    f"figure {selection.asset_id} targets an unknown slide"
                )

    @classmethod
    def _validate_refs(cls, refs, blocks, assets, owner: str) -> None:
        for ref in refs:
            if bool(ref.block_id) == bool(ref.asset_id):
                raise PaperDeckPlanningAdapterError(
                    f"{owner} source refs must identify exactly one block or asset"
                )
            if ref.block_id:
                block = blocks.get(ref.block_id)
                if block is None:
                    raise PaperDeckPlanningAdapterError(
                        f"{owner} references unknown block {ref.block_id}"
                    )
                if block.page_no != ref.page_no:
                    raise PaperDeckPlanningAdapterError(
                        f"{owner} block {ref.block_id} has the wrong page"
                    )
                if not ref.quote or cls._normalize(ref.quote) not in cls._normalize(block.text):
                    raise PaperDeckPlanningAdapterError(
                        f"{owner} quote is not present in block {ref.block_id}"
                    )
            else:
                asset = assets.get(ref.asset_id)
                if asset is None:
                    raise PaperDeckPlanningAdapterError(
                        f"{owner} references unknown asset {ref.asset_id}"
                    )
                if asset.page_no != ref.page_no:
                    raise PaperDeckPlanningAdapterError(
                        f"{owner} asset {ref.asset_id} has the wrong page"
                    )

    @staticmethod
    def _slide(
        slide: PlanningSlide,
        *,
        evidence: PlanningSlideEvidence,
        source_section_ids: list[str],
    ) -> SlidePlan:
        script = "\n".join([slide.message.strip(), *[item.strip() for item in slide.key_points]])
        refs = [item.model_dump(mode="json") for item in evidence.source_refs]
        return SlidePlan(
            id=slide.id,
            order=slide.order,
            source_section_ids=source_section_ids,
            source_kind="generated",
            title=slide.title.strip(),
            key_points=[item.strip() for item in slide.key_points],
            speaker_script=script,
            suggested_visual=slide.visual.strip(),
            layout="freeform",
            visual_payload=list(slide.asset_ids),
            paper_claim_ids=list(evidence.claim_ids),
            paper_asset_ids=list(evidence.asset_ids),
            paper_source_refs=refs,
            evidence_strength=evidence.evidence_strength,
            authoring_note=slide.message.strip(),
            speaker_script_source="authoring",
            paper_evidence_packet={
                "planning_role": slide.role,
                "message": slide.message.strip(),
                "claim_ids": list(evidence.claim_ids),
                "asset_ids": list(evidence.asset_ids),
                "source_refs": refs,
            },
        )

    @staticmethod
    def _sections_by_slide(
        planning: PaperDeckPlan, source_bundle: PaperSourceBundle
    ) -> dict[str, list[str]]:
        known_sections = {item.id for item in source_bundle.sections}
        result: dict[str, list[str]] = {item.id: [] for item in planning.slides}
        for coverage in planning.section_coverage:
            if coverage.section_id not in known_sections:
                raise PaperDeckPlanningAdapterError(
                    f"section coverage references unknown section {coverage.section_id}"
                )
            for slide_id in coverage.slide_ids:
                if slide_id not in result:
                    raise PaperDeckPlanningAdapterError(
                        f"section coverage references unknown slide {slide_id}"
                    )
                result[slide_id].append(coverage.section_id)
        role_by_slide = {item.id: item.role for item in planning.slides}
        return {
            slide_id: section_ids or [f"paper_narrative_{role_by_slide[slide_id]}"]
            for slide_id, section_ids in result.items()
        }

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.split()).lower()

    @staticmethod
    def _ref_keys(refs: list[SourceReference]) -> set[tuple[int, str | None, str | None]]:
        return {(item.page_no, item.block_id, item.asset_id) for item in refs}
