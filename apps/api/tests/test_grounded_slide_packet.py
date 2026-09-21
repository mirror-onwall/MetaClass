import json

import pytest

from metaclass.modules.content.service import ContentService
from metaclass.modules.interaction_planning import (
    InteractionPolicy,
    InteractionValidator,
    PaperDeckInteractionAdapter,
    QuestionGenerator,
    TeachingNodeSelector,
    UnifiedInteractionPlanningPipeline,
)
from metaclass.modules.live_questions import (
    LiveQuestionService,
    PaperDeckLiveQuestionAdapter,
)
from metaclass.modules.live_questions.schemas import (
    LiveEvidenceDocument,
    LiveQuestionIndex,
)
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.paper_workflow.final_paper_deck_page_analyzer import (
    FinalSlideObservation,
)
from metaclass.modules.paper_workflow.grounded_slide_packet import (
    GroundedSlidePacketBuilder,
    GroundedSlidePacketError,
)
from metaclass.modules.paper_workflow.paper_classroom import (
    GroundedNarrationPacketBuilder,
    LLMPaperClassroomComposer,
    SlideNarration,
)
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
    NativePaperDeckSlide,
)
from metaclass.modules.paper_workflow.paper_deck_playback_plan import (
    PaperDeckPlaybackPlanBuilder,
    PaperDeckPlaybackPlanError,
)
from metaclass.modules.paper_workflow.paper_knowledge import PaperKnowledgeTreeBuilder
from metaclass.modules.paper_workflow.schemas import (
    PaperAnalysis,
    PaperClaim,
    PaperSourceBundle,
    QuantitativeResult,
    SourceAsset,
    SourceBlock,
    SourceReference,
)


class MappingLLM:
    name = "mapping-fake"
    model = "test"

    def __init__(self, *, unknown: bool = False) -> None:
        self.unknown = unknown

    def complete_json(self, messages, *, temperature: float = 0.2) -> str:
        assert "GROUNDED_FINAL_SLIDE_ALIGNMENT_V1" in messages[0].content
        assert "never factual evidence" in messages[0].content
        assert temperature == 0.0
        return json.dumps(
            {
                "slides": [
                    {
                        "slide_id": "paper_deck_slide_001",
                        "claim_ids": ["claim_missing" if self.unknown else "claim_method"],
                        "result_ids": ["result_exact", "result_figure", "result_unverified"],
                        "asset_ids": ["fig_2"],
                        "evidence_strength": "direct",
                    }
                ]
            }
        )


class RepairingMappingLLM(MappingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def complete_json(self, messages, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"slides": ['
        assert "REPAIR_GROUNDED_FINAL_SLIDE_ALIGNMENT_V1" in messages[0].content
        repair_payload = json.loads(messages[1].content)
        assert repair_payload["expected_slide_ids"] == ["paper_deck_slide_001"]
        return super().complete_json(
            [
                type(messages[0])(
                    role="system",
                    content="GROUNDED_FINAL_SLIDE_ALIGNMENT_V1 never factual evidence",
                ),
                messages[1],
            ],
            temperature=temperature,
        )


class NarrationLLM:
    name = "narration-fake"
    model = "test"

    def __init__(self, *, unsupported_number: bool = False) -> None:
        self.unsupported_number = unsupported_number

    def complete_json(self, messages, *, temperature: float = 0.2) -> str:
        assert (
            "GROUNDED_PAPER_CLASSROOM_NARRATION_V2" in messages[0].content
            or "REPAIR_GROUNDED_PAPER_CLASSROOM_NARRATION_V1" in messages[0].content
        )
        payload = json.loads(messages[1].content)
        slide = payload["slides"][0]
        assert "PaperDeck analysis" in slide["paper_deck_analysis"]
        number = "99.9%" if self.unsupported_number else "81.4%"
        return json.dumps(
            {
                "slides": [
                    {
                        "slide_id": slide["slide"]["slide_id"],
                        "speaker_script": (
                            "先看当前页面呈现的方法和实验结果，它把论文的核心处理过程与证据放在"
                            f"同一个视野里。论文原文报告的准确率是 {number}，这个结果需要结合"
                            "页面中的方法主张理解，而不能脱离实验条件扩大。接下来我们沿着论文"
                            "自己的论证顺序，继续观察后续证据如何支撑这一判断。"
                        ),
                        "transition": "接下来继续检查论文证据与结论之间的关系。",
                        "used_claim_ids": ["claim_method"],
                        "used_source_refs": slide["slide"]["source_refs"],
                        "used_asset_ids": ["fig_2"],
                        "validation_status": "pending",
                    }
                ]
            },
            ensure_ascii=False,
        )


def _inputs():
    source = PaperSourceBundle(
        material_id="mat_paper",
        file_hash="a" * 64,
        page_count=3,
        pdf_path="paper.pdf",
        paper_source_path="paper_source.json",
        paper_content_path="paper_content.md",
        asset_directory="assets",
        blocks=[
            SourceBlock(
                id="block_result",
                type="paragraph",
                page_no=1,
                text="The exact result is 81.4%.",
            )
        ],
        assets=[
            SourceAsset(
                id="fig_2",
                type="figure",
                page_no=2,
                path="assets/fig_2.png",
                caption="Figure 2 reports the evaluation score.",
            )
        ],
    )
    analysis = PaperAnalysis(
        paper_type="methods",
        central_question="Can backtranslation align a model?",
        knowledge_gap="Instruction data is scarce.",
        main_claim="Backtranslation can construct instruction data.",
        claims=[
            PaperClaim(
                id="claim_method",
                statement="Backtranslation constructs instruction data.",
                importance="core",
                confidence=0.9,
                source_refs=[SourceReference(page_no=1, block_id="block_result")],
            )
        ],
        quantitative_results=[
            QuantitativeResult(
                id="result_exact",
                statement="Accuracy reaches 81.4%.",
                value="81.4%",
                source_refs=[SourceReference(page_no=1, block_id="block_result")],
            ),
            QuantitativeResult(
                id="result_figure",
                statement="The figure reports 6.95.",
                value="6.95",
                source_refs=[SourceReference(page_no=2, asset_id="fig_2")],
            ),
            QuantitativeResult(
                id="result_unverified",
                statement="A draft says 99.9%.",
                value="99.9%",
                source_refs=[SourceReference(page_no=3)],
            ),
        ],
    )
    slide = NativePaperDeckSlide(
        id="paper_deck_slide_001",
        order=1,
        image_path="images/01.png",
        image_hash="b" * 64,
        pdf_page_no=1,
        title_hint="Method and results",
        role="method",
        message="Explain the method and its evidence.",
        visual_intent="Show Figure 2.",
        planned_text=["81.4%", "6.95"],
        evidence_hint="Figure 2 and paper page 1",
        source_visual_hint="Figure 2",
        prompt_path="prompts/01.md",
    )
    manifest = NativePaperDeckManifest(
        provider="native_paper_deck",
        style_preset="journal-minimal",
        language="en",
        slide_count=1,
        pdf_path="presentation.pdf",
        slides=[slide],
    )
    observation = FinalSlideObservation(
        slide_id=slide.id,
        visible_title="Method and results",
        visible_text=["81.4%", "6.95", "99.9%"],
        visual_summary="Figure 2 with three reported numbers.",
        figure_labels=["Figure 2"],
        quantitative_mentions=["81.4%", "6.95", "99.9%"],
        formula_mentions=[],
        page_type="result",
        detected_warnings=["unverified_quantitative_mention:99.9%"],
    )
    return source, analysis, manifest, observation


def test_builds_grounded_packet_and_scopes_numeric_repair() -> None:
    source, analysis, manifest, observation = _inputs()

    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports an exact value of 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )

    packet = packets[0]
    assert packet.claim_ids == ["claim_method"]
    assert packet.result_ids == ["result_exact", "result_figure"]
    assert packet.asset_ids == ["fig_2"]
    assert {ref.page_no for ref in packet.source_refs} == {1, 2}
    assert [item.status for item in packet.numeric_verifications] == [
        "verified",
        "figure_verified",
        "unverified",
    ]
    assert packet.repair_directive is not None
    assert packet.repair_directive.scope == "single_slide"
    assert packet.repair_directive.unverified_mentions == ["99.9%"]
    assert packet.previous_slide_id is None
    assert packet.next_slide_id is None


def test_repairs_invalid_semantic_mapping_response_once() -> None:
    source, analysis, manifest, observation = _inputs()
    llm = RepairingMappingLLM()

    packets = GroundedSlidePacketBuilder(llm).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )

    assert llm.calls == 2
    assert packets[0].slide_id == "paper_deck_slide_001"


def test_rejects_llm_ids_not_present_in_paper_authority() -> None:
    source, analysis, manifest, observation = _inputs()

    with pytest.raises(GroundedSlidePacketError, match="unknown IDs"):
        GroundedSlidePacketBuilder(MappingLLM(unknown=True)).build(
            source_bundle=source,
            paper_content="The paper reports 81.4%.",
            analysis=analysis,
            manifest=manifest,
            observations=[observation],
            knowledge_units=[],
        )


def test_grounded_knowledge_tree_keeps_non_visible_details_in_evidence_index() -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )

    projection = PaperKnowledgeTreeBuilder().build(
        job_id="paper_job_native",
        source_paper_material_id="mat_paper",
        deck_material_id="mat_deck",
        analysis=analysis,
        grounded_packets=packets,
    )

    tree = projection.knowledge_tree
    taught_ids = {unit_id for node in tree.nodes for unit_id in node.knowledge_unit_ids}
    assert tree.nodes[0].ref_id == "paper_deck_slide_001"
    assert tree.nodes[0].page_refs[0].material_id == "mat_deck"
    assert tree.teaching_sequence == [tree.nodes[0].id]
    assert "ku_paper_native_claim_claim_method" in taught_ids
    assert "ku_paper_native_result_result_exact" in taught_ids
    assert "ku_paper_native_result_result_figure" in taught_ids
    assert "ku_paper_native_result_result_unverified" in tree.orphan_unit_ids
    assert "ku_paper_native_question" in tree.orphan_unit_ids
    assert set(projection.evidence_index_unit_ids or []) == set(tree.orphan_unit_ids)
    unverified_unit = next(
        item
        for item in projection.knowledge_units
        if item.id == "ku_paper_native_result_result_unverified"
    )
    assert unverified_unit.page_refs == []
    ContentService._validate_course_knowledge_tree(tree, projection.knowledge_units)


def test_llm_composer_generates_and_validates_grounded_narration() -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )
    projection = PaperKnowledgeTreeBuilder().build(
        job_id="paper_job_native",
        source_paper_material_id="mat_paper",
        deck_material_id="mat_deck",
        analysis=analysis,
        grounded_packets=packets,
    )
    narration_packets = GroundedNarrationPacketBuilder().build(
        packets=packets,
        knowledge_units=projection.knowledge_units,
        analysis=analysis,
        source_bundle=source,
        paper_deck_analysis="PaperDeck analysis of the full paper.",
    )
    assert narration_packets[0].knowledge_units
    assert all(
        "paper_deck_slide_001" in item.source_unit_ids
        for item in narration_packets[0].knowledge_units
    )

    narrations = LLMPaperClassroomComposer(NarrationLLM()).compose_grounded(
        narration_packets,
        audience="graduate students",
        language="zh-CN",
    )

    assert narrations[0].validation_status == "validated"
    assert narrations[0].used_claim_ids == ["claim_method"]
    assert narrations[0].used_asset_ids == ["fig_2"]
    assert "81.4%" in narrations[0].speaker_script
    assert "99.9%" in narration_packets[0].unverified_numbers


def test_grounded_narration_canonicalizes_authorized_ref_without_quote() -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )
    projection = PaperKnowledgeTreeBuilder().build(
        job_id="paper_job_native",
        source_paper_material_id="mat_paper",
        deck_material_id="mat_deck",
        analysis=analysis,
        grounded_packets=packets,
    )
    narration_packet = GroundedNarrationPacketBuilder().build(
        packets=packets,
        knowledge_units=projection.knowledge_units,
        analysis=analysis,
        source_bundle=source,
        paper_deck_analysis="PaperDeck analysis.",
    )[0]
    authoritative = narration_packet.slide.source_refs[0]
    narration = SlideNarration(
        slide_id=narration_packet.slide.slide_id,
        speaker_script=(
            "This sufficiently detailed narration explains the authorized method claim and its "
            "reported 81.4% result while keeping the interpretation within the paper evidence."
        ),
        transition="Next, inspect the following evidence.",
        used_claim_ids=["claim_method"],
        used_source_refs=[
            SourceReference(
                page_no=authoritative.page_no,
                block_id=authoritative.block_id,
                asset_id=authoritative.asset_id,
            )
        ],
        used_asset_ids=["fig_2"],
    )

    validated = LLMPaperClassroomComposer._validate_grounded_narration(
        narration_packet,
        narration,
    )

    assert validated.used_source_refs[0] == authoritative
    assert validated.validation_status == "validated"


def test_llm_composer_rejects_unverified_number_in_script() -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )
    projection = PaperKnowledgeTreeBuilder().build(
        job_id="paper_job_native",
        source_paper_material_id="mat_paper",
        deck_material_id="mat_deck",
        analysis=analysis,
        grounded_packets=packets,
    )
    narration_packets = GroundedNarrationPacketBuilder().build(
        packets=packets,
        knowledge_units=projection.knowledge_units,
        analysis=analysis,
        source_bundle=source,
        paper_deck_analysis="PaperDeck analysis of the full paper.",
    )

    with pytest.raises(ValueError, match="unverified numbers"):
        LLMPaperClassroomComposer(
            NarrationLLM(unsupported_number=True)
        ).compose_grounded(
            narration_packets,
            audience="graduate students",
            language="zh-CN",
        )


def test_builds_pdf_backed_playback_plan_without_generated_slides(tmp_path) -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )
    clean_packet = packets[0].model_copy(
        update={
            "numeric_verifications": [
                item for item in packets[0].numeric_verifications if item.status != "unverified"
            ],
            "repair_directive": None,
        }
    )
    projection = PaperKnowledgeTreeBuilder().build(
        job_id="paper_job_native",
        source_paper_material_id="mat_paper",
        deck_material_id="mat_deck",
        analysis=analysis,
        grounded_packets=[clean_packet],
    )
    narration_packets = GroundedNarrationPacketBuilder().build(
        packets=[clean_packet],
        knowledge_units=projection.knowledge_units,
        analysis=analysis,
        source_bundle=source,
        paper_deck_analysis="PaperDeck analysis of the full paper.",
    )
    narrations = LLMPaperClassroomComposer(NarrationLLM()).compose_grounded(
        narration_packets,
        audience="graduate students",
        language="zh-CN",
    )

    plan = PaperDeckPlaybackPlanBuilder().build(
        content_id="content_paper",
        title="Instruction Backtranslation",
        generated_pdf_material_id="mat_deck",
        original_paper_material_id="mat_paper",
        paper_artifact_bundle_id="bundle_native",
        presentation_resource_id="pres_resource_native",
        packets=[clean_packet],
        narrations=narrations,
        knowledge_units=projection.knowledge_units,
    )

    assert plan.mode == "paper_deck"
    assert plan.source_material_id == "mat_deck"
    assert plan.presentation_resource_id == "pres_resource_native"
    assert plan.slides[0].source_kind == "source"
    assert plan.slides[0].source_page_no == 1
    assert plan.slides[0].elements == []
    assert plan.slides[0].knowledge_unit_ids
    assert plan.slides[0].paper_claim_ids == ["claim_method"]
    assert plan.slides[0].speaker_script == narrations[0].speaker_script

    context = PaperDeckInteractionAdapter().adapt(
        plan=plan,
        packets=[clean_packet],
        knowledge_units=projection.knowledge_units,
        original_paper_material_id="mat_paper",
        duration_minutes=20,
    )
    assert context.mode == "paper_deck"
    assert context.slides[0].claim_ids == ["claim_method"]
    assert context.slides[0].result_ids == ["result_exact", "result_figure"]
    assert context.slides[0].asset_ids == ["fig_2"]
    assert context.slides[0].source_refs[0].material_id == "mat_paper"
    assert context.slides[0].evidence_strength == "direct"

    blueprints = TeachingNodeSelector().select(
        context,
        InteractionPolicy(minimum_nodes=1, maximum_nodes=1),
    )
    bank = QuestionGenerator().generate(context, blueprints)
    validated = InteractionValidator().validate(context, blueprints, bank)
    assert len(blueprints) == 1
    assert validated.validation_status == "validated"
    assert validated.items[0].claim_ids == ["claim_method"]
    assert validated.items[0].asset_ids == ["fig_2"]
    pipeline_blueprints, pipeline_bank = UnifiedInteractionPlanningPipeline().plan(
        context,
        InteractionPolicy(minimum_nodes=1, maximum_nodes=1),
    )
    assert [item.id for item in pipeline_blueprints] == [item.id for item in blueprints]
    assert pipeline_bank.validation_status == "validated"

    live_index = PaperDeckLiveQuestionAdapter().adapt(
        plan=plan,
        source_bundle=source,
        analysis=analysis,
        knowledge_units=projection.knowledge_units,
        packets=[clean_packet],
    )
    live_questions = LiveQuestionService(tmp_path)
    live_questions.register(live_index)
    live_answer = live_questions.answer(
        presentation_plan_id=plan.id,
        question="What accuracy does the paper report?",
        current_slide_id="paper_deck_slide_001",
        taught_slide_ids=[],
    )
    assert "81.4%" in live_answer.answer
    assert live_answer.source_refs
    assert all(item.material_id == "mat_paper" for item in live_answer.source_refs)
    assert live_questions.get_index(plan.id).documents


def test_playback_plan_rejects_page_waiting_for_targeted_repair() -> None:
    source, analysis, manifest, observation = _inputs()
    packets = GroundedSlidePacketBuilder(MappingLLM()).build(
        source_bundle=source,
        paper_content="The paper reports 81.4%.",
        analysis=analysis,
        manifest=manifest,
        observations=[observation],
        knowledge_units=[],
    )

    with pytest.raises(PaperDeckPlaybackPlanError, match="must be repaired"):
        PaperDeckPlaybackPlanBuilder().build(
            content_id="content_paper",
            title="Instruction Backtranslation",
            generated_pdf_material_id="mat_deck",
            original_paper_material_id="mat_paper",
            paper_artifact_bundle_id="bundle_native",
            presentation_resource_id="pres_resource_native",
            packets=packets,
            narrations=[],
            knowledge_units=[],
        )


def test_live_question_marks_evidence_from_unreached_slide(tmp_path) -> None:
    index = LiveQuestionIndex(
        presentation_plan_id="plan_live",
        content_id="content_live",
        mode="paper_deck",
        slide_order=["slide_001", "slide_002"],
        documents=[
            LiveEvidenceDocument(
                id="doc_future",
                kind="result",
                title="Later result",
                text="The later experiment reports 42% accuracy.",
                source_refs=[
                    SourceRef(
                        material_id="mat_paper",
                        page_id="block_later",
                        page_no=8,
                    )
                ],
                slide_ids=["slide_002"],
                earliest_slide_order=2,
                result_ids=["result_later"],
            )
        ],
    )
    service = LiveQuestionService(tmp_path)
    service.register(index)

    answer = service.answer(
        presentation_plan_id="plan_live",
        question="What accuracy does the later experiment report?",
        current_slide_id="slide_001",
        taught_slide_ids=[],
    )

    assert answer.future_slide_ids == ["slide_002"]
    assert answer.classroom_note == "其中部分证据来自课堂尚未讲到的后续页面。"
    assert "42%" in answer.answer
