import pytest

from metaclass.modules.paper_workflow.paper_deck_planning import (
    PaperDeckPlan,
    PaperDeckSlideEvidenceDraft,
)
from metaclass.modules.paper_workflow.paper_deck_planning_adapter import (
    PaperDeckPlanningAdapter,
    PaperDeckPlanningAdapterError,
)
from metaclass.modules.paper_workflow.schemas import (
    PaperSourceBundle,
    SourceAsset,
    SourceBlock,
    SourceSection,
)


def _bundle() -> PaperSourceBundle:
    return PaperSourceBundle(
        material_id="paper_material",
        file_hash="sha256:" + "a" * 64,
        page_count=2,
        pdf_path="paper.pdf",
        paper_source_path="paper_source.json",
        paper_content_path="paper_content.md",
        asset_directory="existing_assets",
        sections=[
            SourceSection(
                id="section_method", title="Method", level=1, start_page=1, end_page=2
            )
        ],
        blocks=[
            SourceBlock(
                id="block_method",
                type="paragraph",
                page_no=1,
                section_path=["Method"],
                text="TraceNet introduces two grounded stages.",
            ),
            SourceBlock(
                id="block_result",
                type="paragraph",
                page_no=2,
                section_path=["Method"],
                text="TraceNet reaches 91.2 accuracy on Dataset Z.",
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


def _planning() -> PaperDeckPlan:
    return PaperDeckPlan.model_validate(
        {
            "paper_title": "TraceNet",
            "central_question": "How can tracing remain grounded?",
            "main_contribution_claim_id": "claim_method",
            "audience": "research students",
            "duration_minutes": 10,
            "language": "zh-CN",
            "narrative_arc": "method to evidence",
            "claims": [
                {
                    "id": "claim_method",
                    "kind": "contribution",
                    "statement": "TraceNet introduces two grounded stages.",
                    "importance": "core",
                    "source_refs": [
                        {
                            "page_no": 1,
                            "block_id": "block_method",
                            "quote": "TraceNet introduces two grounded stages",
                        }
                    ],
                },
                {
                    "id": "claim_result",
                    "kind": "result",
                    "statement": "TraceNet reaches 91.2 accuracy.",
                    "importance": "core",
                    "source_refs": [
                        {
                            "page_no": 2,
                            "block_id": "block_result",
                            "quote": "TraceNet reaches 91.2 accuracy",
                        }
                    ],
                },
            ],
            "figure_selections": [
                {
                    "asset_id": "asset_figure_1",
                    "reason": "method overview",
                    "claim_ids": ["claim_method"],
                    "target_slide_ids": ["slide_cover"],
                }
            ],
            "section_coverage": [
                {
                    "section_id": "section_method",
                    "slide_ids": ["slide_cover", "slide_result"],
                }
            ],
            "slides": [
                {
                    "id": "slide_cover",
                    "order": 1,
                    "role": "cover",
                    "title": "TraceNet 用两个阶段保持证据可追溯",
                    "message": "TraceNet 提出两个有证据约束的阶段。",
                    "key_points": ["第一阶段", "第二阶段"],
                    "visual": "使用论文方法总览图。",
                    "claim_ids": ["claim_method"],
                    "asset_ids": ["asset_figure_1"],
                    "source_refs": [
                        {
                            "page_no": 1,
                            "block_id": "block_method",
                            "quote": "TraceNet introduces two grounded stages",
                        },
                        {"page_no": 1, "asset_id": "asset_figure_1"},
                    ],
                },
                {
                    "id": "slide_result",
                    "order": 2,
                    "role": "takeaway",
                    "title": "TraceNet 在 Dataset Z 上达到 91.2",
                    "message": "实验结果支持核心方法。",
                    "key_points": ["91.2 accuracy", "证据来自原论文"],
                    "visual": "突出结果数字与证据脚注。",
                    "claim_ids": ["claim_result"],
                    "asset_ids": [],
                    "source_refs": [
                        {
                            "page_no": 2,
                            "block_id": "block_result",
                            "quote": "TraceNet reaches 91.2 accuracy",
                        }
                    ],
                },
            ],
        }
    )


def _evidence(planning: PaperDeckPlan) -> PaperDeckSlideEvidenceDraft:
    return PaperDeckSlideEvidenceDraft.model_validate(
        {
            "slides": [
                {
                    "slide_id": slide.id,
                    "claim_ids": slide.claim_ids,
                    "asset_ids": slide.asset_ids,
                    "source_refs": [ref.model_dump() for ref in slide.source_refs],
                    "evidence_strength": "direct",
                }
                for slide in planning.slides
            ]
        }
    )


def _convert(planning: PaperDeckPlan | None = None):
    actual = planning or _planning()
    return PaperDeckPlanningAdapter().convert(
        plan_id="plan_paper_test",
        content_id="content_paper_test",
        planning=actual,
        evidence=_evidence(actual),
        source_bundle=_bundle(),
        source_paper_material_id="paper_material",
        paper_artifact_bundle_id="bundle_test",
        generation_model="test-model",
    )


def test_adapter_converts_planning_into_frozen_presentation_plan() -> None:
    result = _convert()

    assert result.mode == "paper_deck"
    assert result.generation_provider == "paper-deck"
    assert result.source_material_id is None
    assert [slide.id for slide in result.slides] == ["slide_cover", "slide_result"]
    assert result.slides[0].source_section_ids == ["section_method"]
    assert result.slides[0].source_kind == "generated"
    assert result.slides[0].paper_claim_ids == ["claim_method"]
    assert result.slides[0].paper_asset_ids == ["asset_figure_1"]
    assert result.slides[0].speaker_script_source == "authoring"
    assert result.slides[0].speaker_script.startswith("TraceNet 提出")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda plan: setattr(plan.slides[1], "id", "slide_cover"), "ids must be unique"),
        (lambda plan: setattr(plan.slides[1], "order", 3), "order must be continuous"),
        (lambda plan: setattr(plan.slides[1], "title", "TBD"), "complete non-placeholder"),
        (lambda plan: setattr(plan.slides[1], "key_points", []), "non-empty key points"),
        (
            lambda plan: setattr(plan.slides[1], "claim_ids", ["claim_missing"]),
            "unknown claims",
        ),
        (
            lambda plan: setattr(plan.slides[1], "asset_ids", ["asset_missing"]),
            "unknown assets",
        ),
    ],
)
def test_adapter_rejects_invalid_slide_contract(mutation, message: str) -> None:
    planning = _planning().model_copy(deep=True)
    mutation(planning)

    with pytest.raises(PaperDeckPlanningAdapterError, match=message):
        _convert(planning)


def test_adapter_rejects_mixed_or_drifting_source_refs() -> None:
    planning = _planning().model_copy(deep=True)
    planning.slides[0].source_refs[0].asset_id = "asset_figure_1"

    with pytest.raises(PaperDeckPlanningAdapterError, match="exactly one block or asset"):
        _convert(planning)


def test_adapter_uses_role_fallback_when_normalized_sections_are_absent() -> None:
    planning = _planning().model_copy(deep=True)
    planning.section_coverage = []
    bundle = _bundle().model_copy(update={"sections": []})

    result = PaperDeckPlanningAdapter().convert(
        plan_id="plan_paper_test",
        content_id="content_paper_test",
        planning=planning,
        evidence=_evidence(planning),
        source_bundle=bundle,
        source_paper_material_id="paper_material",
    )

    assert result.slides[0].source_section_ids == ["paper_narrative_cover"]
    assert result.slides[1].source_section_ids == ["paper_narrative_takeaway"]
