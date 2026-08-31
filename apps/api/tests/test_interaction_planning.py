import json
from unittest.mock import Mock

import pytest

from metaclass.modules.classroom.agent_schemas import StudentAgentType
from metaclass.modules.classroom.planner import ClassroomPlanGenerator
from metaclass.modules.content.schemas import LearningContent, LearningSection
from metaclass.modules.interaction_planning.jobs import (
    InteractionPlanningJob,
    InteractionPlanningJobService,
)
from metaclass.modules.interaction_planning.service import (
    InteractionBudget,
    InteractionPlanningService,
)
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan
from metaclass.modules.question_bank.generator import QuestionBankGenerator


def lesson() -> tuple[LearningContent, PresentationPlan]:
    content = LearningContent(
        id="content_interactions",
        material_id="material_interactions",
        title="互动规划测试",
        objectives=["验证统一入口"],
        sections=[
            LearningSection(
                id="section_001",
                title="核心概念",
                summary="介绍概念。",
                source_refs=[
                    SourceRef(
                        material_id="material_interactions",
                        page_id="page_001",
                        page_no=1,
                        text_span="概念定义",
                    )
                ],
            )
        ],
    )
    plan = PresentationPlan(
        id="plan_interactions",
        content_id=content.id,
        title=content.title,
        slides=[
            SlidePlan(
                id="slide_001",
                order=1,
                source_section_ids=["section_001"],
                title="核心概念",
                key_points=["概念定义", "适用条件"],
                speaker_script="完整解释核心概念、适用条件、常见误区以及它与后续内容的关系。",
                suggested_visual="概念图",
            )
        ],
    )
    return content, plan


def test_interaction_job_write_is_atomic_and_leaves_no_temporary_file(tmp_path) -> None:
    service = InteractionPlanningJobService(tmp_path, Mock(), Mock(), Mock())
    job = InteractionPlanningJob(
        id="interaction_job_atomic",
        presentation_plan_id="plan_atomic",
        content_id="content_atomic",
        intensity="standard",
    )

    service._write(job)

    target = service.jobs_dir / f"{job.id}.json"
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == job.id
    assert list(service.jobs_dir.glob(f".{target.name}.*.tmp")) == []


def test_none_intensity_skips_interaction_planning_entirely() -> None:
    generator = Mock()
    repository = Mock()
    content, plan = lesson()

    InteractionPlanningService(generator, repository).plan(content, plan, "none")

    repository.list_for_plan.assert_not_called()
    generator.generate_batches.assert_not_called()


@pytest.mark.parametrize("intensity", ["light", "standard", "rich"])
def test_interactive_intensities_share_the_same_planning_entry(intensity: str) -> None:
    generator = Mock()
    generator.generate_node_batches.return_value = iter([[Mock(slide_id="slide_001")]])
    repository = Mock()
    repository.list_for_plan.return_value = []
    content, plan = lesson()

    InteractionPlanningService(generator, repository).plan(content, plan, intensity)

    generator.generate_node_batches.assert_called_once_with(
        content,
        plan,
        target_slide_ids={"slide_001"},
        completed_slide_ids=set(),
    )
    repository.append_for_plan.assert_called_once()


def test_planned_route_does_not_run_legacy_student_profile_scan() -> None:
    generator = Mock()
    generator.generate_node_batches.return_value = iter([[]])
    repository = Mock()
    repository.list_for_plan.return_value = []
    content, plan = lesson()

    InteractionPlanningService(generator, repository).plan(content, plan, "standard")

    generator.generate_node_batches.assert_called_once()
    generator.generate_batches.assert_not_called()


def test_effective_slides_exclude_non_body_and_thin_pages() -> None:
    content, plan = lesson()

    def slide(slide_id: str, order: int, title: str, script: str) -> SlidePlan:
        return SlidePlan(
            id=slide_id,
            order=order,
            source_section_ids=["section_001"],
            title=title,
            key_points=[],
            speaker_script=script,
            suggested_visual="页面",
        )

    plan = plan.model_copy(
        update={
            "title": "互动规划测试",
            "slides": [
                slide("cover", 1, "互动规划测试", "课程开场。"),
                slide("agenda", 2, "目录", "课程目录。"),
                slide("section", 3, "第一章", "进入第一章。"),
                slide("thanks", 4, "感谢聆听", "谢谢大家。"),
                slide("references", 5, "参考文献", "资料来源。"),
                slide("thin", 6, "提示", "请注意。"),
                slide(
                    "body",
                    7,
                    "核心机制",
                    "这一页完整解释核心机制、成立条件、推理过程以及它与前置概念的关系。",
                ),
            ],
        }
    )

    result = InteractionPlanningService(Mock(), Mock()).assess(
        content, plan, "standard"
    )

    assert result.effective_slide_ids == ("body",)
    assert result.effective_slide_count == 1
    assert {item.slide_id: item.reason for item in result.assessments} == {
        "cover": "cover_like_first_slide",
        "agenda": "non_body_title",
        "section": "non_body_title",
        "thanks": "non_body_title",
        "references": "non_body_title",
        "thin": "insufficient_content",
        "body": "body_content",
    }


def _selection_plan(
    content: LearningContent,
    plan: PresentationPlan,
    page_count: int,
    valuable_orders: set[int],
    duplicate_orders: set[int] | None = None,
) -> PresentationPlan:
    duplicate_orders = duplicate_orders or set()
    slides = []
    for order in range(1, page_count + 1):
        if order in valuable_orders:
            title = "重复的核心机制" if order in duplicate_orders else f"核心机制 {order}"
            points = ["关键机制", "成立条件", "证据局限"]
            script = (
                "这一页详细分析关键机制、成立条件、证据边界和实际案例，"
                "需要学生进行推理而不是简单复述。"
            )
            visual = ["mechanism_chart"]
        else:
            title = f"一般说明 {order}"
            points = [f"补充信息 {order}"]
            script = "这里提供一般性的补充说明，帮助课程自然衔接并形成基本背景信息。"
            visual = []
        slides.append(
            SlidePlan(
                id=f"selection_{order:03d}",
                order=order,
                source_section_ids=[content.sections[0].id],
                title=title,
                key_points=points,
                speaker_script=script,
                suggested_visual="课堂页面",
                visual_payload=visual,
            )
        )
    return plan.model_copy(update={"title": "选择测试课程", "slides": slides})


def test_repeated_high_value_pages_only_select_one() -> None:
    content, plan = lesson()
    plan = _selection_plan(content, plan, 20, {3, 8, 14}, {3, 14})

    result = InteractionPlanningService(Mock(), Mock()).assess(content, plan, "rich")

    repeated = {"selection_003", "selection_014"} & set(result.selected_slide_ids)
    assert len(repeated) == 1


def test_selection_follows_page_value_instead_of_fixed_intervals() -> None:
    content, plan = lesson()
    valuable_orders = {2, 7, 13}
    plan = _selection_plan(content, plan, 20, valuable_orders)

    result = InteractionPlanningService(Mock(), Mock()).assess(content, plan, "rich")

    assert set(result.selected_slide_ids) == {
        f"selection_{order:03d}" for order in valuable_orders
    }


def test_adjacent_high_value_nodes_are_limited() -> None:
    content, plan = lesson()
    plan = _selection_plan(content, plan, 20, {5, 6, 12})

    result = InteractionPlanningService(Mock(), Mock()).assess(content, plan, "rich")

    assert len({"selection_005", "selection_006"} & set(result.selected_slide_ids)) == 1
    assert "selection_012" in result.selected_slide_ids


def test_low_value_pages_can_produce_fewer_nodes_than_suggested_minimum() -> None:
    content, plan = lesson()
    plan = _selection_plan(content, plan, 40, set())

    result = InteractionPlanningService(Mock(), Mock()).assess(content, plan, "standard")

    assert result.budget.minimum == 4
    assert result.selected_slide_ids == ()


@pytest.mark.parametrize("mode", ["generated", "source_deck", "paper_deck"])
def test_all_three_presentation_routes_generate_interactions(mode: str) -> None:
    content, plan = lesson()
    plan = plan.model_copy(update={"mode": mode})
    generator = Mock()
    generated = Mock(slide_id="slide_001")
    generator.generate_node_batches.return_value = iter([[generated]])
    repository = Mock()
    repository.list_for_plan.side_effect = [[], [generated]]

    InteractionPlanningService(generator, repository).plan(content, plan, "standard")

    generator.generate_node_batches.assert_called_once()
    repository.append_for_plan.assert_called_once_with([generated])


@pytest.mark.parametrize("mode", ["generated", "source_deck", "paper_deck"])
def test_none_intensity_skips_question_generation_for_all_routes(mode: str) -> None:
    content, plan = lesson()
    plan = plan.model_copy(update={"mode": mode})
    generator = Mock()
    repository = Mock()

    InteractionPlanningService(generator, repository).plan(content, plan, "none")

    generator.generate_node_batches.assert_not_called()
    repository.list_for_plan.assert_not_called()


@pytest.mark.parametrize(
    ("count", "intensity", "expected"),
    [
        (0, "none", InteractionBudget(0, 0)),
        (40, "none", InteractionBudget(0, 0)),
        (20, "light", InteractionBudget(1, 2)),
        (5, "light", InteractionBudget(1, 1)),
        (5, "standard", InteractionBudget(1, 1)),
        (5, "rich", InteractionBudget(1, 1)),
        (40, "light", InteractionBudget(2, 4)),
        (40, "standard", InteractionBudget(4, 6)),
        (40, "rich", InteractionBudget(5, 8)),
        (60, "light", InteractionBudget(4, 6)),
        (60, "standard", InteractionBudget(6, 9)),
        (60, "rich", InteractionBudget(8, 12)),
        (200, "light", InteractionBudget(6, 6)),
        (200, "standard", InteractionBudget(9, 9)),
        (200, "rich", InteractionBudget(12, 12)),
    ],
)
def test_interaction_budget_formula(
    count: int,
    intensity: str,
    expected: InteractionBudget,
) -> None:
    assert InteractionPlanningService.calculate_budget(count, intensity) == expected


def test_candidate_prefilter_keeps_only_twice_the_upper_budget() -> None:
    content, plan = lesson()
    slides = [
        SlidePlan(
            id=f"slide_{index:03d}",
            order=index,
            source_section_ids=["section_001"],
            title=f"核心机制 {index}",
            key_points=["机制", "条件", "证据"],
            speaker_script=f"第 {index} 页详细解释机制成立的条件、证据、局限和案例。" * 4,
            suggested_visual="机制图",
            visual_payload=["diagram"],
        )
        for index in range(1, 41)
    ]
    plan = plan.model_copy(update={"slides": slides})

    result = InteractionPlanningService(Mock(), Mock()).assess(
        content, plan, "standard"
    )

    assert result.budget == InteractionBudget(4, 6)
    assert len(result.candidates) == 12
    assert len(result.selected_slide_ids) == 6
    assert result.selection_source == "deterministic"


def test_candidate_scoring_rewards_teaching_value_and_penalizes_repetition() -> None:
    content, plan = lesson()
    plan = plan.model_copy(
        update={
            "slides": [
                SlidePlan(
                    id="valuable",
                    order=1,
                    source_section_ids=["section_001"],
                    title="实验机制与成立条件",
                    key_points=["机制", "实验步骤", "局限"],
                    speaker_script="通过图表和案例分析实验机制、成立条件、证据与适用边界。" * 4,
                    suggested_visual="实验流程图",
                    visual_payload=["chart"],
                ),
                SlidePlan(
                    id="repeated",
                    order=2,
                    source_section_ids=["section_001"],
                    title="实验机制与成立条件",
                    key_points=["机制", "实验步骤", "局限"],
                    speaker_script="通过图表和案例分析实验机制、成立条件、证据与适用边界。" * 4,
                    suggested_visual="实验流程图",
                    visual_payload=["chart"],
                ),
                SlidePlan(
                    id="plain",
                    order=3,
                    source_section_ids=["section_001"],
                    title="补充说明",
                    key_points=["一般信息"],
                    speaker_script="这里提供一些常规背景信息，供学习者继续阅读和理解。" * 2,
                    suggested_visual="文本",
                ),
            ]
        }
    )

    service = InteractionPlanningService(Mock(), Mock())
    effective_ids = tuple(slide.id for slide in plan.slides)
    ranked = service._rank_candidates(content, plan, effective_ids)
    scores = {item.slide_id: item.score for item in ranked}

    assert scores["valuable"] > scores["plain"]
    assert scores["valuable"] > scores["repeated"]
    assert "adjacent_repetition" in next(
        item.signals for item in ranked if item.slide_id == "repeated"
    )


def test_global_llm_selects_once_and_can_choose_below_suggested_minimum() -> None:
    content, plan = lesson()
    llm = Mock()
    llm.complete_json.return_value = '{"selected_slide_ids":["slide_001"],"reasons":[]}'
    service = InteractionPlanningService(Mock(), Mock(), llm)

    result = service.assess(content, plan, "standard")

    assert result.selected_slide_ids == ("slide_001",)
    assert result.selection_source == "llm"
    llm.complete_json.assert_called_once()
    assert "INTERACTION_NODE_SELECTOR_V1" in llm.complete_json.call_args.args[0][0].content


@pytest.mark.parametrize(
    "response",
    ["not-json", '{"selected_slide_ids":["unknown"]}', '{"selected_slide_ids":[]}'],
)
def test_global_selection_failure_falls_back_to_ranked_candidates(response: str) -> None:
    content, plan = lesson()
    llm = Mock()
    llm.complete_json.return_value = response

    result = InteractionPlanningService(Mock(), Mock(), llm).assess(
        content, plan, "standard"
    )

    assert result.selected_slide_ids == ("slide_001",)
    assert result.selection_source == "deterministic"


def test_node_question_generation_batches_six_and_returns_one_question_per_node() -> None:
    class NodeLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, messages, temperature=0.0):
            assert "INTERACTION_NODE_QUESTION_GENERATOR_V1" in messages[0].content
            self.calls += 1
            nodes = __import__("json").loads(messages[1].content)["nodes"]
            return __import__("json").dumps(
                {
                    "questions": [
                        {
                            "slide_id": node["slide_id"],
                            "agent_type": "deep_thinker",
                            "compatible_agent_types": [
                                "researcher",
                                "concept_confused",
                            ],
                            "knowledge_point": node["key_points"][0],
                            "canonical_question": "这个机制成立的条件是什么？",
                            "student_question": "老师，如果条件变了，这个机制还成立吗？",
                            "canonical_answer": "该机制只在页面列出的条件下成立。",
                            "teacher_answer": (
                                "要先检查这一页列出的条件；条件改变，结论可能不再成立。"
                            ),
                            "placement_reason": "讲解后检查条件意识。",
                        }
                        for node in nodes
                    ]
                },
                ensure_ascii=False,
            )

    content, plan = lesson()
    slides = [
        SlidePlan(
            id=f"node_{index}",
            order=index,
            source_section_ids=["section_001"],
            title=f"机制 {index}",
            key_points=[f"条件 {index}"],
            speaker_script=f"解释第 {index} 个机制的条件和证据。",
            suggested_visual="机制图",
        )
        for index in range(1, 8)
    ]
    plan = plan.model_copy(update={"slides": slides})
    llm = NodeLLM()

    batches = list(
        QuestionBankGenerator(llm).generate_node_batches(
            content,
            plan,
            target_slide_ids={slide.id for slide in slides},
        )
    )
    items = [item for batch in batches for item in batch]

    assert llm.calls == 2
    assert [len(batch) for batch in batches] == [6, 1]
    assert len(items) == 7
    assert len({item.slide_id for item in items}) == 7
    assert all(item.agent_type.value == "deep_thinker" for item in items)
    assert all(
        [agent_type.value for agent_type in item.compatible_agent_types]
        == ["researcher", "concept_confused"]
        for item in items
    )
    assert all(item.canonical_question and item.student_question for item in items)
    assert all(item.canonical_answer and item.teacher_answer for item in items)
    assert all(item.source_refs for item in items)


def test_node_question_generation_falls_back_per_missing_node() -> None:
    content, plan = lesson()
    llm = Mock()
    llm.complete_json.return_value = '{"questions":[]}'

    batches = list(
        QuestionBankGenerator(llm).generate_node_batches(
            content, plan, target_slide_ids={"slide_001"}
        )
    )

    assert len(batches[0]) == 1
    assert batches[0][0].slide_id == "slide_001"
    assert batches[0][0].canonical_answer
    assert batches[0][0].teacher_answer


def test_teacher_check_is_not_generated_on_student_question_page() -> None:
    content, presentation = lesson()
    planner = ClassroomPlanGenerator()
    classroom_plan = planner._align_to_presentation(
        content, planner._fallback_plan(content), presentation
    )
    planner._generate_teacher_checks = Mock(return_value=[])
    qa = Mock(
        status="approved",
        slide_id="slide_001",
        agent_type=StudentAgentType.DEEP_THINKER,
    )

    planner._prepare_teacher_check_actions(classroom_plan, presentation, [qa])

    planner._generate_teacher_checks.assert_called_once_with([], presentation.slides)


def test_selected_nodes_are_saved_before_question_generation_failure() -> None:
    content, plan = lesson()
    generator = Mock()
    generator.generate_node_batches.side_effect = RuntimeError("provider unavailable")
    qa_repository = Mock()
    qa_repository.list_for_plan.return_value = []
    presentation_repository = Mock()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        InteractionPlanningService(
            generator,
            qa_repository,
            presentation_repository=presentation_repository,
        ).plan(content, plan, "standard")

    assert plan.interaction_node_ids == ["slide_001"]
    assert plan.interaction_planning_status == "nodes_selected"
    presentation_repository.save_plan.assert_called_once_with(plan)


def test_resume_uses_persisted_nodes_and_only_fills_missing_questions() -> None:
    content, plan = lesson()
    second_slide = plan.slides[0].model_copy(
        update={
            "id": "slide_002",
            "order": 2,
            "title": "第二个互动节点",
        }
    )
    plan = plan.model_copy(
        update={
            "slides": [plan.slides[0], second_slide],
            "interaction_intensity": "standard",
            "interaction_node_ids": ["slide_001", "slide_002"],
            "interaction_planning_status": "nodes_selected",
        }
    )
    existing = Mock(slide_id="slide_001")
    generated = Mock(slide_id="slide_002")
    qa_repository = Mock()
    qa_repository.list_for_plan.side_effect = [[existing], [existing, generated]]
    generator = Mock()
    generator.generate_node_batches.return_value = iter([[generated]])
    selector_llm = Mock()
    presentation_repository = Mock()

    result = InteractionPlanningService(
        generator,
        qa_repository,
        selector_llm,
        presentation_repository,
    ).plan(content, plan, "standard")

    assert result.selection_source == "persisted"
    selector_llm.complete_json.assert_not_called()
    generator.generate_node_batches.assert_called_once_with(
        content,
        plan,
        target_slide_ids={"slide_001", "slide_002"},
        completed_slide_ids={"slide_001"},
    )
    assert plan.interaction_planning_status == "complete"
