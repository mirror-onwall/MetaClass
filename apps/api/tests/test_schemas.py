from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from metaclass.infrastructure.providers.fake import FakeLearningProvider
from metaclass.modules.assessment.schemas import Evidence
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.schemas import ClassroomEvent, TeachingAction
from metaclass.modules.classroom.service import ClassroomService
from metaclass.modules.content.schemas import (
    LearningContent,
    LearningSection,
    PageUnderstanding,
    QuizItem,
)
from metaclass.modules.materials.schemas import PageMetadata, SourceRef
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


def test_fake_provider_extracts_short_chinese_points() -> None:
    draft = FakeLearningProvider().understand_page(
        "矩阵乘法", "矩阵乘法：行向量与列向量相乘，得到新的矩阵。", 1
    )
    assert draft.knowledge_points
    assert all(len(point) <= 12 for point in draft.knowledge_points)
