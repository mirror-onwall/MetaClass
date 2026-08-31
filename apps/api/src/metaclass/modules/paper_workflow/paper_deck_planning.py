"""Contracts and deterministic audit for the Paper Deck planning smoke test."""

from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel
from metaclass.modules.paper_workflow.schemas import PaperSourceBundle, SourceReference


class GroundedPlanningClaim(SchemaModel):
    id: str = Field(min_length=1)
    kind: Literal[
        "contribution",
        "context",
        "method",
        "experiment",
        "result",
        "limitation",
    ]
    statement: str = Field(min_length=1)
    importance: Literal["core", "supporting"] = "supporting"
    source_refs: list[SourceReference] = Field(min_length=1)


class PlanningFigureSelection(SchemaModel):
    asset_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    target_slide_ids: list[str] = Field(default_factory=list)


class PlanningSlide(SchemaModel):
    id: str = Field(min_length=1)
    order: int = Field(ge=1)
    role: Literal[
        "cover",
        "context",
        "problem",
        "method",
        "mechanism",
        "evidence",
        "result",
        "comparison",
        "limitation",
        "takeaway",
    ]
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    visual: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(min_length=1)


class PlanningSectionCoverage(SchemaModel):
    section_id: str = Field(min_length=1)
    slide_ids: list[str] = Field(default_factory=list)
    omission_reason: str | None = None

    @model_validator(mode="after")
    def require_coverage_or_reason(self) -> PlanningSectionCoverage:
        if not self.slide_ids and not self.omission_reason:
            raise ValueError("section coverage requires slide_ids or an omission_reason")
        return self


class PaperDeckPlan(SchemaModel):
    schema_version: Literal["1.0"] = "1.0"
    paper_title: str = Field(min_length=1)
    central_question: str = Field(min_length=1)
    main_contribution_claim_id: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    duration_minutes: int = Field(ge=5, le=120)
    language: str = Field(min_length=2)
    style_preset: Literal["journal-minimal"] = "journal-minimal"
    narrative_arc: str = Field(min_length=1)
    claims: list[GroundedPlanningClaim] = Field(min_length=1)
    figure_selections: list[PlanningFigureSelection] = Field(default_factory=list)
    section_coverage: list[PlanningSectionCoverage] = Field(default_factory=list)
    slides: list[PlanningSlide] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> PaperDeckPlan:
        claim_ids = [item.id for item in self.claims]
        slide_ids = [item.id for item in self.slides]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("planning claim ids must be unique")
        if self.main_contribution_claim_id not in set(claim_ids):
            raise ValueError("main contribution must reference a planning claim")
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("planning slide ids must be unique")
        if [item.order for item in self.slides] != list(range(1, len(self.slides) + 1)):
            raise ValueError("planning slide order must be continuous and start at 1")
        return self


class PlanningSlideEvidence(SchemaModel):
    slide_id: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(min_length=1)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"] = "direct"


class PaperDeckSlideEvidenceDraft(SchemaModel):
    schema_version: Literal["1.0"] = "1.0"
    slides: list[PlanningSlideEvidence] = Field(min_length=1)


class PlanningAuditFinding(SchemaModel):
    severity: Literal["error", "warning", "manual_review"]
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None


class PaperDeckPlanningAudit(SchemaModel):
    passed: bool
    requires_human_review: bool = True
    slide_count: int = Field(ge=0)
    selected_figure_count: int = Field(ge=0)
    grounded_claim_count: int = Field(ge=0)
    findings: list[PlanningAuditFinding] = Field(default_factory=list)


_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*[%×xX])?(?![\w.])"
)
_FORMULA_ITERATION = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*(?:\^\s*)?\(\d+\)[A-Za-z0-9_]*"
)


class PaperDeckPlanningAuditor:
    """Fail closed on traceability; leave semantic quality as explicit human review."""

    def audit(
        self,
        bundle: PaperSourceBundle,
        plan: PaperDeckPlan,
        evidence: PaperDeckSlideEvidenceDraft,
        *,
        minimum_slides: int,
        maximum_slides: int,
    ) -> PaperDeckPlanningAudit:
        findings: list[PlanningAuditFinding] = []
        blocks = {item.id: item for item in bundle.blocks}
        assets = {item.id: item for item in bundle.assets}
        claims = {item.id: item for item in plan.claims}
        slides = {item.id: item for item in plan.slides}

        main_claim = claims[plan.main_contribution_claim_id]
        if main_claim.kind != "contribution" or main_claim.importance != "core":
            self._add(
                findings,
                "error",
                "invalid_main_contribution",
                "main_contribution_claim_id must identify a core contribution claim",
            )

        if not minimum_slides <= len(plan.slides) <= maximum_slides:
            self._add(
                findings,
                "error",
                "unreasonable_slide_count",
                f"slide count {len(plan.slides)} is outside {minimum_slides}-{maximum_slides}",
                "paper_deck_plan.json.slides",
            )
        if plan.slides[0].role != "cover":
            self._add(findings, "error", "missing_cover", "first slide must be cover")
        if plan.slides[-1].role != "takeaway":
            self._add(findings, "error", "missing_takeaway", "last slide must be takeaway")

        self._validate_refs(findings, bundle, plan.claims, "claims")
        self._validate_refs(findings, bundle, plan.slides, "slides")
        for claim in plan.claims:
            self._validate_numbers(
                findings, claim.statement, claim.source_refs, blocks, assets, f"claims.{claim.id}"
            )
        for slide in plan.slides:
            visible = " ".join([slide.title, slide.message, *slide.key_points])
            self._validate_numbers(
                findings, visible, slide.source_refs, blocks, assets, f"slides.{slide.id}"
            )
            for claim_id in slide.claim_ids:
                if claim_id not in claims:
                    self._add(
                        findings,
                        "error",
                        "unknown_claim",
                        f"slide {slide.id} references unknown claim {claim_id}",
                    )
            for asset_id in slide.asset_ids:
                if asset_id not in assets:
                    self._add(
                        findings,
                        "error",
                        "unknown_asset",
                        f"slide {slide.id} references unknown asset {asset_id}",
                    )

        evidence_ids = [item.slide_id for item in evidence.slides]
        if Counter(evidence_ids) != Counter(slides.keys()):
            self._add(
                findings,
                "error",
                "evidence_slide_mismatch",
                "slide_evidence_draft.json must cover every planned slide exactly once",
            )
        self._validate_refs(findings, bundle, evidence.slides, "slide_evidence")
        for item in evidence.slides:
            slide = slides.get(item.slide_id)
            if not slide:
                continue
            if set(item.claim_ids) != set(slide.claim_ids):
                self._add(
                    findings,
                    "error",
                    "claim_mapping_drift",
                    f"evidence claim ids differ on {item.slide_id}",
                )
            if set(item.asset_ids) != set(slide.asset_ids):
                self._add(
                    findings,
                    "error",
                    "asset_mapping_drift",
                    f"evidence asset ids differ on {item.slide_id}",
                )
            if self._ref_keys(item.source_refs) != self._ref_keys(slide.source_refs):
                self._add(
                    findings,
                    "error",
                    "source_mapping_drift",
                    f"evidence source refs differ on {item.slide_id}",
                )

        for selection in plan.figure_selections:
            if selection.asset_id not in assets:
                self._add(
                    findings,
                    "error",
                    "unknown_selected_figure",
                    f"selected figure {selection.asset_id} does not exist",
                )
            for claim_id in selection.claim_ids:
                if claim_id not in claims:
                    self._add(
                        findings,
                        "error",
                        "unknown_figure_claim",
                        f"figure {selection.asset_id} references unknown claim {claim_id}",
                    )
            for slide_id in selection.target_slide_ids:
                if slide_id not in slides:
                    self._add(
                        findings,
                        "error",
                        "unknown_figure_slide",
                        f"figure {selection.asset_id} targets unknown slide {slide_id}",
                    )
        visual_assets = [item for item in bundle.assets if item.type in {"figure", "table"}]
        if visual_assets and not plan.figure_selections:
            self._add(
                findings,
                "error",
                "no_core_figure_selected",
                "source contains figures/tables but the plan selects none",
            )

        covered_claims = {claim_id for slide in plan.slides for claim_id in slide.claim_ids}
        for claim in plan.claims:
            if (
                claim.kind in {"contribution", "method", "experiment", "result"}
                and claim.id not in covered_claims
            ):
                self._add(
                    findings,
                    "error",
                    "omitted_core_content",
                    f"{claim.kind} claim {claim.id} is absent from all slides",
                )

        section_ids = {item.id for item in bundle.sections}
        coverage_ids = [item.section_id for item in plan.section_coverage]
        if Counter(coverage_ids) != Counter(section_ids):
            self._add(
                findings,
                "error",
                "section_coverage_mismatch",
                "section_coverage must account for every normalized source section exactly once",
            )
        for item in plan.section_coverage:
            for slide_id in item.slide_ids:
                if slide_id not in slides:
                    self._add(
                        findings,
                        "error",
                        "unknown_coverage_slide",
                        f"section {item.section_id} references unknown slide {slide_id}",
                    )

        role_set = {item.role for item in plan.slides}
        for kind, expected_roles in {
            "method": {"method", "mechanism"},
            "experiment": {"evidence", "result", "comparison"},
            "result": {"evidence", "result", "comparison"},
        }.items():
            if any(item.kind == kind for item in plan.claims) and not role_set & expected_roles:
                self._add(
                    findings,
                    "error",
                    "narrative_role_missing",
                    f"claims include {kind} but no matching slide role exists",
                )

        self._add(
            findings,
            "manual_review",
            "semantic_accuracy_review",
            "A human must confirm that the contribution wording is faithful, selected figures are the best choices, and no key method or experiment was absent from the model inventory.",
        )
        return PaperDeckPlanningAudit(
            passed=not any(item.severity == "error" for item in findings),
            slide_count=len(plan.slides),
            selected_figure_count=len(plan.figure_selections),
            grounded_claim_count=len(plan.claims),
            findings=findings,
        )

    def _validate_refs(self, findings, bundle, items, prefix: str) -> None:
        blocks = {item.id: item for item in bundle.blocks}
        assets = {item.id: item for item in bundle.assets}
        for item in items:
            refs = item.source_refs
            for index, ref in enumerate(refs):
                path = f"{prefix}.{getattr(item, 'id', getattr(item, 'slide_id', index))}.source_refs.{index}"
                if not ref.block_id and not ref.asset_id:
                    self._add(
                        findings,
                        "error",
                        "unresolved_source_ref",
                        "source ref requires block_id or asset_id",
                        path,
                    )
                    continue
                if ref.block_id:
                    block = blocks.get(ref.block_id)
                    if not block:
                        self._add(
                            findings,
                            "error",
                            "unknown_block",
                            f"unknown block {ref.block_id}",
                            path,
                        )
                    elif block.page_no != ref.page_no:
                        self._add(
                            findings,
                            "error",
                            "source_page_mismatch",
                            f"block {ref.block_id} is on page {block.page_no}, not {ref.page_no}",
                            path,
                        )
                    elif not ref.quote or self._normalize(ref.quote) not in self._normalize(
                        block.text
                    ):
                        self._add(
                            findings,
                            "error",
                            "quote_not_in_source",
                            f"quote is not an exact normalized excerpt of block {ref.block_id}",
                            path,
                        )
                if ref.asset_id:
                    asset = assets.get(ref.asset_id)
                    if not asset:
                        self._add(
                            findings,
                            "error",
                            "unknown_source_asset",
                            f"unknown asset {ref.asset_id}",
                            path,
                        )
                    elif asset.page_no != ref.page_no:
                        self._add(
                            findings,
                            "error",
                            "asset_page_mismatch",
                            f"asset {ref.asset_id} is on page {asset.page_no}, not {ref.page_no}",
                            path,
                        )

    def _validate_numbers(self, findings, text, refs, blocks, assets, path: str) -> None:
        numbers = self._quantitative_numbers(text)
        if not numbers:
            return
        evidence_text = " ".join(
            [blocks[ref.block_id].text for ref in refs if ref.block_id and ref.block_id in blocks]
            + [
                assets[ref.asset_id].caption or ""
                for ref in refs
                if ref.asset_id and ref.asset_id in assets
            ]
        )
        evidence_numbers = {
            self._canonical_number(number) for number in self._quantitative_numbers(evidence_text)
        }
        unsupported = sorted(
            number
            for number in numbers
            if self._canonical_number(number) not in evidence_numbers
        )
        if unsupported:
            self._add(
                findings,
                "error",
                "unsupported_number",
                f"numbers are absent from cited evidence: {unsupported}",
                path,
            )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.split()).lower()

    @staticmethod
    def _canonical_number(value: str) -> str:
        # PDF table extraction often drops a column-level percent sign. Preserve
        # every digit and sign while treating only a trailing % as presentation.
        return value.strip().removesuffix("%").rstrip().replace(",", "")

    @staticmethod
    def _quantitative_numbers(value: str) -> set[str]:
        # Iteration labels such as A(1)k / A(2)k are symbolic identifiers, not
        # quantitative claims. Model names such as M2 are already excluded by
        # _NUMBER's word-boundary guards.
        return set(_NUMBER.findall(_FORMULA_ITERATION.sub("", value)))

    @staticmethod
    def _ref_keys(refs: list[SourceReference]) -> set[tuple[int, str | None, str | None]]:
        return {(item.page_no, item.block_id, item.asset_id) for item in refs}

    @staticmethod
    def _add(findings, severity, code, message, path=None) -> None:
        findings.append(
            PlanningAuditFinding(severity=severity, code=code, message=message, path=path)
        )
