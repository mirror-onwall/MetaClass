from metaclass.modules.paper_workflow.paper_deck_planning import (
    PaperDeckPlan,
    PaperDeckPlanningAuditor,
    PaperDeckSlideEvidenceDraft,
)
from metaclass.modules.paper_workflow.schemas import (
    PaperSourceBundle,
    SourceAsset,
    SourceBlock,
    SourceReference,
    SourceSection,
)


def _ref(block_id: str, quote: str, page_no: int = 1) -> SourceReference:
    return SourceReference(page_no=page_no, block_id=block_id, quote=quote)


def _bundle() -> PaperSourceBundle:
    return PaperSourceBundle(
        material_id="mat_paper",
        file_hash="sha256:" + "a" * 64,
        page_count=2,
        pdf_path="paper.pdf",
        paper_source_path="paper_source.json",
        paper_content_path="paper_content.md",
        asset_directory="existing_assets",
        sections=[
            SourceSection(id="section_method", title="Method", level=1, start_page=1, end_page=1),
            SourceSection(id="section_results", title="Results", level=1, start_page=2, end_page=2),
        ],
        blocks=[
            SourceBlock(
                id="block_method",
                type="paragraph",
                page_no=1,
                section_path=["Method"],
                text="We introduce the TraceNet method with two grounded stages.",
            ),
            SourceBlock(
                id="block_result",
                type="paragraph",
                page_no=2,
                section_path=["Results"],
                text="TraceNet reaches 91.2% accuracy on Dataset Z.",
            ),
        ],
        assets=[
            SourceAsset(
                id="asset_figure_1",
                type="figure",
                page_no=1,
                path="existing_assets/figure.png",
                caption="Figure 1. TraceNet overview.",
                source="pdf",
            )
        ],
    )


def _plan() -> PaperDeckPlan:
    return PaperDeckPlan.model_validate(
        {
            "paper_title": "TraceNet",
            "central_question": "How can tracing remain grounded?",
            "main_contribution_claim_id": "claim_contribution",
            "audience": "research students",
            "duration_minutes": 20,
            "language": "zh-CN",
            "style_preset": "journal-minimal",
            "narrative_arc": "problem to method to evidence",
            "claims": [
                {
                    "id": "claim_contribution",
                    "kind": "contribution",
                    "statement": "TraceNet introduces two grounded stages.",
                    "importance": "core",
                    "source_refs": [
                        _ref(
                            "block_method", "introduce the TraceNet method with two grounded stages"
                        ).model_dump()
                    ],
                },
                {
                    "id": "claim_method",
                    "kind": "method",
                    "statement": "The method uses two grounded stages.",
                    "importance": "core",
                    "source_refs": [
                        _ref(
                            "block_method", "TraceNet method with two grounded stages"
                        ).model_dump()
                    ],
                },
                {
                    "id": "claim_result",
                    "kind": "result",
                    "statement": "TraceNet reaches 91.2% accuracy.",
                    "importance": "core",
                    "source_refs": [
                        _ref("block_result", "TraceNet reaches 91.2% accuracy", 2).model_dump()
                    ],
                },
            ],
            "figure_selections": [
                {
                    "asset_id": "asset_figure_1",
                    "reason": "overview",
                    "claim_ids": ["claim_method"],
                    "target_slide_ids": ["slide_method"],
                }
            ],
            "section_coverage": [
                {"section_id": "section_method", "slide_ids": ["slide_method"]},
                {"section_id": "section_results", "slide_ids": ["slide_result"]},
            ],
            "slides": [
                {
                    "id": "slide_cover",
                    "order": 1,
                    "role": "cover",
                    "title": "TraceNet",
                    "message": "TraceNet introduces two grounded stages.",
                    "visual": "quiet cover",
                    "claim_ids": ["claim_contribution"],
                    "source_refs": [
                        _ref(
                            "block_method", "introduce the TraceNet method with two grounded stages"
                        ).model_dump()
                    ],
                },
                {
                    "id": "slide_method",
                    "order": 2,
                    "role": "method",
                    "title": "The method uses two grounded stages",
                    "message": "TraceNet uses two grounded stages.",
                    "visual": "source overview",
                    "claim_ids": ["claim_method"],
                    "asset_ids": ["asset_figure_1"],
                    "source_refs": [
                        _ref(
                            "block_method", "TraceNet method with two grounded stages"
                        ).model_dump(),
                        SourceReference(page_no=1, asset_id="asset_figure_1").model_dump(),
                    ],
                },
                {
                    "id": "slide_result",
                    "order": 3,
                    "role": "result",
                    "title": "TraceNet reaches 91.2% accuracy",
                    "message": "The decisive result is 91.2% accuracy.",
                    "visual": "evidence callout",
                    "claim_ids": ["claim_result"],
                    "source_refs": [
                        _ref("block_result", "TraceNet reaches 91.2% accuracy", 2).model_dump()
                    ],
                },
                {
                    "id": "slide_takeaway",
                    "order": 4,
                    "role": "takeaway",
                    "title": "TraceNet combines a grounded method with measured evidence",
                    "message": "The method and result support the contribution.",
                    "visual": "synthesis",
                    "claim_ids": ["claim_contribution"],
                    "source_refs": [
                        _ref(
                            "block_method", "introduce the TraceNet method with two grounded stages"
                        ).model_dump()
                    ],
                },
            ],
        }
    )


def _evidence(plan: PaperDeckPlan) -> PaperDeckSlideEvidenceDraft:
    return PaperDeckSlideEvidenceDraft.model_validate(
        {
            "slides": [
                {
                    "slide_id": slide.id,
                    "claim_ids": slide.claim_ids,
                    "source_refs": [item.model_dump() for item in slide.source_refs],
                    "asset_ids": slide.asset_ids,
                    "evidence_strength": "direct",
                }
                for slide in plan.slides
            ]
        }
    )


def test_planning_audit_accepts_a_fully_grounded_plan() -> None:
    plan = _plan()

    audit = PaperDeckPlanningAuditor().audit(
        _bundle(), plan, _evidence(plan), minimum_slides=4, maximum_slides=6
    )

    assert audit.passed is True
    assert {item.severity for item in audit.findings} == {"manual_review"}


def test_planning_audit_accepts_percent_when_pdf_table_omits_percent_symbol() -> None:
    plan = _plan().model_copy(deep=True)
    bundle = _bundle().model_copy(deep=True)
    bundle.blocks[1].text = "Win Rate % TraceNet reaches 91.2 accuracy on Dataset Z."
    plan.claims[2].source_refs[0].quote = "TraceNet reaches 91.2 accuracy"
    plan.slides[2].source_refs[0].quote = "TraceNet reaches 91.2 accuracy"

    audit = PaperDeckPlanningAuditor().audit(
        bundle, plan, _evidence(plan), minimum_slides=4, maximum_slides=6
    )

    assert audit.passed is True


def test_planning_audit_does_not_treat_formula_iteration_as_quantity() -> None:
    plan = _plan().model_copy(deep=True)
    plan.slides[1].message = "Iteration A(2)k refines the selected training set."

    audit = PaperDeckPlanningAuditor().audit(
        _bundle(), plan, _evidence(plan), minimum_slides=4, maximum_slides=6
    )

    assert audit.passed is True


def test_planning_audit_normalizes_thousands_separators() -> None:
    plan = _plan().model_copy(deep=True)
    bundle = _bundle().model_copy(deep=True)
    bundle.blocks[1].text = "TraceNet reaches 91200 examples on Dataset Z."
    plan.claims[2].statement = "TraceNet reaches 91,200 examples."
    plan.claims[2].source_refs[0].quote = "TraceNet reaches 91200 examples"
    plan.slides[2].title = "TraceNet reaches 91,200 examples"
    plan.slides[2].message = "The decisive result is 91,200 examples."
    plan.slides[2].source_refs[0].quote = "TraceNet reaches 91200 examples"

    audit = PaperDeckPlanningAuditor().audit(
        bundle, plan, _evidence(plan), minimum_slides=4, maximum_slides=6
    )

    assert audit.passed is True


def test_planning_audit_rejects_unknown_numbers_missing_figures_and_coverage() -> None:
    plan = _plan().model_copy(deep=True)
    plan.slides[2].title = "TraceNet reaches 99.9% accuracy"
    plan.figure_selections = []
    plan.section_coverage = [plan.section_coverage[0]]
    evidence = _evidence(plan)
    evidence.slides[1].source_refs = evidence.slides[1].source_refs[:1]

    audit = PaperDeckPlanningAuditor().audit(
        _bundle(), plan, evidence, minimum_slides=4, maximum_slides=6
    )

    assert audit.passed is False
    codes = {item.code for item in audit.findings}
    assert {
        "unsupported_number",
        "no_core_figure_selected",
        "section_coverage_mismatch",
        "source_mapping_drift",
    }.issubset(codes)
