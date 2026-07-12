from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from metaclass.infrastructure.providers.fake import FakeLearningProvider
from metaclass.infrastructure.providers.llm import FakeLLMProvider
from metaclass.modules.assessment.schemas import Evidence
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.agent_schemas import (
    StudentAgentType,
    get_default_student_agent_states,
    get_default_student_agent_profiles,
    get_student_agent_states,
)
from metaclass.modules.classroom.agents import TeacherAgent
from metaclass.modules.classroom.planner import ClassroomPlanGenerator
from metaclass.modules.classroom.schemas import (
    ActionExecutedEvent,
    ActionExecutedPayload,
    ActionType,
    AskQuizAction,
    ClassroomEvent,
    ClassroomPlan,
    ClassroomSession,
    CreateClassroomSessionRequest,
    GiveFeedbackAction,
    TeachingAction,
)
from metaclass.modules.classroom.service import ClassroomService
from metaclass.modules.content.schemas import (
    LearningContent,
    LearningSection,
    PageUnderstanding,
    QuizItem,
)
from metaclass.modules.materials.schemas import PageMetadata, SourceRef
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.schemas import PPTGenerationJob, PresentationPlan
from metaclass.modules.video.schemas import VideoJob


def source_ref() -> SourceRef:
    return SourceRef(
        material_id="mat_001",
        page_id="page_001",
        page_no=1,
        text_span="线性规划的定义",
        image_path="data/processed/mat_001/page_001.png",
    )


def test_source_ref_is_structured_and_forbids_unknown_fields() -> None:
    ref = source_ref()
    assert ref.page_no == 1
    with pytest.raises(ValidationError):
        SourceRef(
            material_id="mat_001",
            page_id="page_001",
            page_no=1,
            unknown="not allowed",  # type: ignore[call-arg]
        )


def test_quiz_correct_index_must_reference_an_option() -> None:
    with pytest.raises(ValidationError):
        QuizItem(
            id="quiz_001",
            question="问题",
            options=["A", "B"],
            correct_index=2,
            explanation="解释",
            knowledge_point="知识点",
            source_refs=[source_ref()],
        )


def test_teaching_action_uses_discriminated_union() -> None:
    adapter = TypeAdapter(TeachingAction)
    action = adapter.validate_python(
        {
            "id": "action_001",
            "type": "SHOW_PAGE",
            "actor": "system",
            "payload": {"source_ref": source_ref().model_dump()},
        }
    )
    assert action.type == "SHOW_PAGE"
    summary_action = adapter.validate_python(
        {
            "id": "action_003",
            "type": "SUMMARIZE",
            "actor": "teacher",
            "payload": {
                "text": "总结本页重点。",
                "source_refs": [source_ref().model_dump()],
            },
        }
    )
    assert summary_action.type == "SUMMARIZE"

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "id": "action_002",
                "type": "FREE_FORM_JSON",
                "actor": "system",
                "payload": {},
            }
        )


def test_parsed_page_and_llm_understanding_are_separate() -> None:
    ref = source_ref()
    page = PageMetadata(
        id="page_001",
        material_id="mat_001",
        page_no=1,
        title="原始标题",
        raw_text="原始文本",
        image_path="data/processed/mat_001/page_001.png",
        source_refs=[ref],
    )
    assert "summary" not in page.model_dump()

    understanding = PageUnderstanding(
        id="understanding_page_001",
        material_id="mat_001",
        page_id=page.id,
        page_no=1,
        summary="模型生成摘要",
        knowledge_points=["知识点"],
        teaching_focus=["教学重点"],
        possible_questions=["可能问题"],
        source_refs=[ref],
        provider="fake",
    )
    assert understanding.summary == "模型生成摘要"


def test_classroom_event_requires_identity_and_session() -> None:
    adapter = TypeAdapter(ClassroomEvent)
    event = adapter.validate_python(
        {
            "id": "event_001",
            "session_id": "session_001",
            "type": "USER_QUESTION",
            "payload": {"question": "为什么？"},
        }
    )
    assert event.id == "event_001"

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "type": "USER_QUESTION",
                "payload": {"question": "缺少事件身份"},
            }
        )


def test_default_student_agent_profiles_include_required_classroom_roles() -> None:
    profiles = get_default_student_agent_profiles()
    profile_types = {profile.type for profile in profiles}

    assert {
        StudentAgentType.ATMOSPHERE_REGULATOR,
        StudentAgentType.DEEP_THINKER,
        StudentAgentType.NOTE_TAKER,
        StudentAgentType.RESEARCHER,
    }.issubset(profile_types)
    assert len(profiles) >= 8
    assert all(profile.behaviors for profile in profiles)


def test_default_student_agent_states_start_with_four_classroom_roles() -> None:
    states = get_default_student_agent_states()

    assert [state.agent_type for state in states] == [
        StudentAgentType.ATMOSPHERE_REGULATOR,
        StudentAgentType.DEEP_THINKER,
        StudentAgentType.NOTE_TAKER,
        StudentAgentType.RESEARCHER,
    ]
    assert len({state.id for state in states}) == 4


def test_selected_student_agent_states_keep_the_requested_roles() -> None:
    states = get_student_agent_states(
        [StudentAgentType.NOTE_TAKER, StudentAgentType.RESEARCHER]
    )

    assert [state.agent_type for state in states] == [
        StudentAgentType.NOTE_TAKER,
        StudentAgentType.RESEARCHER,
    ]
    assert [state.id for state in states] == ["student_agent_001", "student_agent_002"]


def test_missing_student_selection_uses_the_default_four_roles() -> None:
    states = get_student_agent_states(None)

    assert [state.agent_type for state in states] == [
        StudentAgentType.ATMOSPHERE_REGULATOR,
        StudentAgentType.DEEP_THINKER,
        StudentAgentType.NOTE_TAKER,
        StudentAgentType.RESEARCHER,
    ]


def test_empty_student_selection_keeps_an_empty_roster() -> None:
    assert get_student_agent_states([]) == []


def test_session_request_uses_yj_agent_values_and_accepts_legacy_values() -> None:
    request = CreateClassroomSessionRequest(
        student_agent_types=["deep_thinker", "NOTE_TAKER"]
    )

    assert request.student_agent_types == [
        StudentAgentType.DEEP_THINKER,
        StudentAgentType.NOTE_TAKER,
    ]


def test_session_request_limits_student_selection_to_eight_agents() -> None:
    with pytest.raises(ValidationError):
        CreateClassroomSessionRequest(
            student_agent_types=[StudentAgentType.DEEP_THINKER] * 9
        )


def test_finished_video_job_requires_result_id() -> None:
    with pytest.raises(ValidationError):
        VideoJob(id="job_001", content_id="content_001", status="finished", progress=1)

    job = VideoJob(
        id="job_001",
        content_id="content_001",
        status="finished",
        progress=1,
        result_id="result_001",
    )
    assert job.result_id == "result_001"


def test_finished_ppt_job_requires_artifact_id() -> None:
    with pytest.raises(ValidationError):
        PPTGenerationJob(
            id="ppt_job_001",
            presentation_plan_id="presentation_plan_001",
            status="finished",
            progress=1,
        )

    job = PPTGenerationJob(
        id="ppt_job_001",
        presentation_plan_id="presentation_plan_001",
        status="finished",
        progress=1,
        artifact_id="ppt_artifact_001",
    )
    assert job.artifact_id == "ppt_artifact_001"


def test_presentation_plan_generator_uses_learning_content_sections() -> None:
    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        title="演示内容",
        sections=[
            LearningSection(
                id="section_001",
                title="概念介绍",
                summary="介绍核心概念。",
                knowledge_points=["核心概念"],
                source_refs=[source_ref()],
            )
        ],
    )

    plan = PresentationPlanGenerator(FakeLLMProvider()).generate(content)

    assert isinstance(plan, PresentationPlan)
    assert plan.content_id == content.id
    assert plan.slides[0].source_section_ids == ["section_001"]
    assert plan.slides[0].speaker_script
    assert plan.slides[0].suggested_visual


def test_mastery_estimate_keeps_session_identity() -> None:
    evidence = Evidence(
        id="evidence_001",
        session_id="session_001",
        action_id="quiz_001",
        type="QUIZ",
        knowledge_point="矩阵乘法",
        score=1,
        weight=1,
        confidence=1,
    )

    estimate = estimate_mastery("session_001", [evidence])[0]
    assert estimate.session_id == "session_001"
    assert estimate.value == 1


def test_plan_can_be_created_for_section_without_quiz() -> None:
    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        title="无小测内容",
        sections=[
            LearningSection(
                id="section_001",
                title="概念介绍",
                summary="只讲解，不测验。",
                source_refs=[source_ref()],
            )
        ],
    )
    contents = Mock()
    contents.get.return_value = content
    repository = Mock()

    plan = ClassroomService(repository, contents).create_plan(content.id)

    assert [action.type for action in plan.scenes[0].actions] == [
        "SHOW_PAGE",
        "EXPLAIN",
        "END",
    ]
    repository.save_plan.assert_called_once_with(plan)


def test_llm_classroom_plan_generator_validates_quiz_feedback_sequence() -> None:
    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        title="小测内容",
        sections=[
            LearningSection(
                id="section_001",
                title="概念介绍",
                summary="先讲解，再测验。",
                source_refs=[source_ref()],
                quiz_items=[
                    QuizItem(
                        id="quiz_001",
                        question="哪个说法正确？",
                        options=["正确说法", "错误说法"],
                        correct_index=0,
                        explanation="第一个选项符合材料。",
                        knowledge_point="概念",
                        source_refs=[source_ref()],
                    )
                ],
            )
        ],
    )

    plan = ClassroomPlanGenerator(FakeLLMProvider()).generate(content)
    actions = plan.scenes[0].actions
    quiz_index = next(index for index, action in enumerate(actions) if action.type == "ASK_QUIZ")

    assert plan.content_id == content.id
    assert isinstance(actions[quiz_index], AskQuizAction)
    assert isinstance(actions[quiz_index + 1], GiveFeedbackAction)
    assert actions[quiz_index + 1].payload.quiz_action_id == actions[quiz_index].id


def test_auto_step_treats_intermediate_end_as_scene_boundary() -> None:
    sections = [
        LearningSection(
            id="section_001",
            title="第一页",
            summary="第一页结束后还要继续。",
            source_refs=[source_ref()],
        ),
        LearningSection(
            id="section_002",
            title="第二页",
            summary="第二页内容。",
            source_refs=[source_ref()],
        ),
    ]
    teacher = TeacherAgent()
    plan = ClassroomPlan(
        id="plan_001",
        content_id="content_001",
        scenes=[
            teacher.build_scene(index, section)
            for index, section in enumerate(sections, start=1)
        ],
    )
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
        scene_index=0,
        action_index=len(plan.scenes[0].actions) - 1,
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan
    controller = Mock()
    controller.decide.side_effect = AssertionError("controller should not end on scene END")

    result = ClassroomService(repository, Mock(), controller=controller).auto_step(session.id)

    assert result.status == "action"
    assert result.action is not None
    assert result.action.type == "END"
    assert result.session.status == "running"
    controller.decide.assert_not_called()


def test_auto_step_executes_show_page_without_llm_controller() -> None:
    section = LearningSection(
        id="section_001",
        title="第一页",
        summary="先展示页面。",
        source_refs=[source_ref()],
    )
    teacher = TeacherAgent()
    plan = ClassroomPlan(
        id="plan_001",
        content_id="content_001",
        scenes=[teacher.build_scene(1, section)],
    )
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan
    controller = Mock()
    controller.decide.side_effect = AssertionError("SHOW_PAGE should not call controller")

    result = ClassroomService(repository, Mock(), controller=controller).auto_step(session.id)

    assert result.status == "action"
    assert result.action is not None
    assert result.action.type == "SHOW_PAGE"
    controller.decide.assert_not_called()


def test_auto_step_starts_student_dialog_after_planned_probe_in_interactive_mode() -> None:
    section = LearningSection(
        id="section_001",
        title="第一页",
        summary="老师按计划追问后应有学生互动。",
        source_refs=[source_ref()],
    )
    plan = ClassroomPlanGenerator(FakeLLMProvider()).generate(
        LearningContent(
            id="content_001",
            material_id="mat_001",
            title="测试内容",
            sections=[section],
        )
    )
    probe_index = next(
        index for index, action in enumerate(plan.scenes[0].actions) if action.type == "PROBE"
    )
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
        action_index=probe_index + 1,
        student_states=get_default_student_agent_states(),
        events=[
            ActionExecutedEvent(
                id="event_001",
                session_id="session_001",
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id="scene_001_probe",
                    action_type=ActionType.PROBE,
                ),
            )
        ],
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan

    result = ClassroomService(repository, Mock()).auto_step(session.id)

    assert result.status == "agent_turn"
    assert result.directed_turn is not None
    assert result.directed_turn.decision.next_role == "student"
    assert result.directed_turn.turns[0].role == "student"
    assert result.directed_turn.turns[0].intent == "student_answer_planned_probe"


def test_auto_step_continues_planned_probe_dialog_before_quiz() -> None:
    section = LearningSection(
        id="section_001",
        title="第一页",
        summary="先提问，再小测。",
        source_refs=[source_ref()],
        quiz_items=[
            QuizItem(
                id="quiz_001",
                question="哪个说法正确？",
                options=["正确说法", "错误说法"],
                correct_index=0,
                explanation="第一个选项符合材料。",
                knowledge_point="概念",
                source_refs=[source_ref()],
            )
        ],
    )
    plan = ClassroomPlanGenerator(FakeLLMProvider()).generate(
        LearningContent(
            id="content_001",
            material_id="mat_001",
            title="测试内容",
            sections=[section],
        )
    )
    quiz_index = next(
        index for index, action in enumerate(plan.scenes[0].actions) if action.type == "ASK_QUIZ"
    )
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
        action_index=quiz_index,
        student_states=get_default_student_agent_states(),
        events=[
            ActionExecutedEvent(
                id="event_001",
                session_id="session_001",
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id="scene_001_probe",
                    action_type=ActionType.PROBE,
                ),
            )
        ],
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan
    teacher = Mock(wraps=TeacherAgent())

    result = ClassroomService(repository, Mock(), teacher=teacher).auto_step(session.id)

    assert result.status == "agent_turn"
    assert result.directed_turn is not None
    assert result.directed_turn.turns[0].role == "student"
    teacher.generate_turn.assert_not_called()


def test_fake_provider_extracts_short_chinese_points() -> None:
    draft = FakeLearningProvider().understand_page(
        "矩阵乘法", "矩阵乘法：行向量与列向量相乘，得到新的矩阵。", 1
    )
    assert draft.knowledge_points
    assert all(len(point) <= 12 for point in draft.knowledge_points)
