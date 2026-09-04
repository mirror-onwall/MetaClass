import pytest

from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.interaction_planning import (
    InteractionPolicy,
    PresentationInteractionAdapter,
    UnifiedInteractionPlanningPipeline,
)
from metaclass.modules.interaction_planning.contracts import (
    EvidenceIndex,
    InteractionEvidenceEntry,
    InteractionKnowledgeUnit,
    InteractionPlanningContext,
    InteractionSlide,
)
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan


def _plan(mode: str) -> PresentationPlan:
    return PresentationPlan(
        id=f"plan_{mode}",
        content_id=f"content_{mode}",
        title="Shared interaction route",
        mode=mode,
        source_material_id=f"material_{mode}",
        slides=[
            SlidePlan(
                id=f"{mode}_slide_001",
                order=1,
                source_section_ids=["section_1"],
                source_page_no=1,
                source_kind="source" if mode == "source_deck" else "generated",
                title="Opening",
                key_points=["Set the context"],
                speaker_script="Introduce the topic without testing it yet.",
                suggested_visual="Title",
            ),
            SlidePlan(
                id=f"{mode}_slide_002",
                order=2,
                source_section_ids=["section_1"],
                source_page_no=2,
                source_kind="source" if mode == "source_deck" else "generated",
                title="The method connects evidence to a conclusion",
                key_points=["Explain the method and evidence"],
                speaker_script="The method links an observable result to the central conclusion.",
                suggested_visual="Method diagram",
                knowledge_unit_ids=["ku_method"],
                visual_payload=["method diagram"],
            ),
        ],
    )


def _paper_context() -> InteractionPlanningContext:
    ref = SourceRef(material_id="paper", page_id="block_method", page_no=2)
    return InteractionPlanningContext(
        content_id="content_paper_deck",
        presentation_plan_id="plan_paper_deck",
        mode="paper_deck",
        duration_minutes=20,
        slides=[
            InteractionSlide(
                slide_id="paper_slide_001",
                order=1,
                title="Opening",
                role="cover",
            ),
            InteractionSlide(
                slide_id="paper_slide_002",
                order=2,
                title="The method",
                message="Explain the grounded method.",
                role="method",
                speaker_script="Explain the method from the paper evidence.",
                knowledge_unit_ids=["ku_method"],
                claim_ids=["claim_method"],
                source_refs=[ref],
                evidence_strength="direct",
                visual_labels=["Figure 1"],
            ),
        ],
        knowledge_units=[
            InteractionKnowledgeUnit(
                id="ku_method",
                title="Method",
                summary="The method links evidence to its conclusion.",
                source_refs=[ref],
            )
        ],
        evidence_index=EvidenceIndex(
            entries=[
                InteractionEvidenceEntry(
                    slide_id="paper_slide_002",
                    claim_ids=["claim_method"],
                    source_refs=[ref],
                )
            ]
        ),
    )


@pytest.mark.parametrize("mode", ["generated", "source_deck", "paper_deck"])
def test_all_three_routes_enter_the_same_interaction_pipeline(mode: str) -> None:
    if mode == "paper_deck":
        context = _paper_context()
    else:
        plan = _plan(mode)
        context = PresentationInteractionAdapter().adapt(
            plan=plan,
            knowledge_units=[
                KnowledgeUnit(
                    id="ku_method",
                    title="Method",
                    summary="The method links evidence to its conclusion.",
                )
            ],
            duration_minutes=20,
        )

    blueprints, bank = UnifiedInteractionPlanningPipeline().plan(
        context,
        InteractionPolicy(minimum_nodes=1, maximum_nodes=1),
    )

    assert context.mode == mode
    assert len(blueprints) == 1
    assert bank.validation_status == "validated"
    assert bank.presentation_plan_id == context.presentation_plan_id


def test_interaction_pipeline_auto_falls_back_when_summary_adds_unknown_number() -> None:
    context = _paper_context()
    context = context.model_copy(
        update={
            "knowledge_units": [
                context.knowledge_units[0].model_copy(
                    update={"summary": "An unsupported draft summary mentions 99.9%."}
                )
            ]
        }
    )

    _, bank = UnifiedInteractionPlanningPipeline().plan(
        context,
        InteractionPolicy(minimum_nodes=1, maximum_nodes=1),
    )

    assert bank.validation_status == "validated"
    assert "99.9%" not in bank.items[0].answer
    assert "不引入页面之外的数值" in bank.items[0].answer


def test_interaction_accepts_number_from_validated_current_narration() -> None:
    context = _paper_context()
    context = context.model_copy(
        update={
            "slides": [
                context.slides[0],
                context.slides[1].model_copy(
                    update={"speaker_script": "The paper evidence reports 81.4%."}
                ),
            ],
            "knowledge_units": [
                context.knowledge_units[0].model_copy(
                    update={"summary": "The reported result is 81.4%."}
                )
            ],
        }
    )

    _, bank = UnifiedInteractionPlanningPipeline().plan(
        context,
        InteractionPolicy(minimum_nodes=1, maximum_nodes=1),
    )

    assert bank.validation_status == "validated"
    assert "81.4%" in bank.items[0].answer
