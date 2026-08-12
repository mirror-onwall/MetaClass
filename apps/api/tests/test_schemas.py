import json
import threading
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from metaclass.infrastructure.providers.fake import FakeLearningProvider
from metaclass.infrastructure.providers.llm import FakeLLMProvider
from metaclass.modules.assessment.schemas import Evidence
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.agent_schemas import (
    AgentTurn,
    StudentAgentType,
    get_default_student_agent_states,
    get_default_student_agent_profiles,
    get_student_agent_states,
)
from metaclass.modules.classroom.agents import TeacherAgent
from metaclass.modules.classroom.agents.prompts import build_student_messages
from metaclass.modules.classroom.planner import ClassroomPlanGenerator
from metaclass.modules.classroom.schemas import (
    ActionExecutedEvent,
    ActionExecutedPayload,
    ActionType,
    AgentTurnEvent,
    AgentTurnPayload,
    AskQuizAction,
    ClassroomEvent,
    ClassroomPlan,
    ClassroomSession,
    ClassroomState,
    CreateClassroomSessionRequest,
    GiveFeedbackAction,
    ShowSlideAction,
    StudentQuestionAction,
    StudentQuestionPayload,
    TeachingAction,
)
from metaclass.modules.classroom.service import ClassroomService
from metaclass.modules.content.schemas import (
    FormulaNote,
    LearningContent,
    LearningSection,
    PageUnderstanding,
    QuizItem,
    VisualOpportunity,
)
from metaclass.modules.materials.schemas import PageMetadata, SourceRef
from metaclass.modules.presentation.diagnostics import diagnose_presentation_plan
from metaclass.modules.presentation.planner import (
    PresentationPlanDraft,
    PresentationPlanGenerator,
    SlidePlanDraft,
)
from metaclass.modules.presentation.layout_registry import (
    LAYOUT_REGISTRY,
    build_fallback_elements,
    select_fallback_layout,
    split_points_for_layout,
)
from metaclass.modules.presentation.brand_palette import (
    BRAND_PALETTE,
    apply_brand_palette,
)
from metaclass.modules.presentation.schemas import (
    PPTGenerationJob,
    PresentationPlan,
    SlideElement,
    SlideElementStyle,
    SlidePlan,
)
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.schemas import QuestionCandidate
from metaclass.modules.video.schemas import VideoJob


def test_formula_note_normalizes_llm_variable_shapes() -> None:
    from_strings = FormulaNote(variables=["u", "v", "θ"])
    from_mapping = FormulaNote(variables={"n_v": "number of samples"})
    from_single = FormulaNote(variables="v")

    assert from_strings.variables == [
        {"symbol": "u", "meaning": ""},
        {"symbol": "v", "meaning": ""},
        {"symbol": "θ", "meaning": ""},
    ]
    assert from_mapping.variables == [{"symbol": "n_v", "meaning": "number of samples"}]
    assert from_single.variables == [{"symbol": "v", "meaning": ""}]


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
    states = get_student_agent_states([StudentAgentType.NOTE_TAKER, StudentAgentType.RESEARCHER])

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
    request = CreateClassroomSessionRequest(student_agent_types=["deep_thinker", "NOTE_TAKER"])

    assert request.student_agent_types == [
        StudentAgentType.DEEP_THINKER,
        StudentAgentType.NOTE_TAKER,
    ]


def test_session_request_limits_student_selection_to_eight_agents() -> None:
    with pytest.raises(ValidationError):
        CreateClassroomSessionRequest(student_agent_types=[StudentAgentType.DEEP_THINKER] * 9)


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
    assert plan.slides[0].layout == "freeform"
    assert plan.slides[0].visual_payload
    assert plan.slides[0].elements


def test_source_deck_narration_prompt_preserves_every_page() -> None:
    class SourceNarrationLLM:
        name = "source-narration-test"
        model = "test-model"

        def complete_json(self, messages, temperature=0.2):
            payload = json.loads(messages[-1].content)
            return json.dumps(
                {
                    "slides": [
                        {
                            "page_no": page["page_no"],
                            "title": page["page_title"] or f"第 {page['page_no']} 页",
                            "key_points": [f"要点 {page['page_no']}"],
                            "speaker_script": f"逐页讲稿 {page['page_no']}",
                        }
                        for page in payload["pages"]
                    ]
                },
                ensure_ascii=False,
            )

    content = LearningContent(
        id="content_source_narration",
        material_id="mat_source",
        title="原稿课程",
        sections=[
            LearningSection(
                id="section_source",
                title="原稿章节",
                summary="章节说明",
                source_refs=[
                    SourceRef(
                        material_id="mat_source",
                        page_id="page_001",
                        page_no=1,
                    )
                ],
            )
        ],
    )
    pages = [
        PageMetadata(
            id=f"page_{page_no:03d}",
            material_id="mat_source",
            page_no=page_no,
            title=f"页面 {page_no}",
            raw_text=f"页面内容 {page_no}",
            image_path=f"/tmp/page_{page_no:03d}.png",
            source_refs=[
                SourceRef(
                    material_id="mat_source",
                    page_id=f"page_{page_no:03d}",
                    page_no=page_no,
                )
            ],
        )
        for page_no in (1, 2)
    ]

    plan = PresentationPlanGenerator(SourceNarrationLLM()).generate_from_source_deck(
        content, pages, "mat_source"
    )

    assert plan.mode == "source_deck"
    assert [slide.source_page_no for slide in plan.slides] == [1, 2]
    assert [slide.speaker_script for slide in plan.slides] == [
        "逐页讲稿 1",
        "逐页讲稿 2",
    ]
    assert plan.generation_source == "llm"


def test_source_deck_narration_uses_natural_style_prompt_file() -> None:
    generator = PresentationPlanGenerator()
    prompt = generator.source_narration_prompt_path.read_text(encoding="utf-8")

    assert generator.source_narration_prompt_path.name == "source_deck_narration_prompt.md"
    assert "不要机械逐条复述页面文字" in prompt
    assert "`page_text`" in prompt
    assert "像老师面对学生讲课" in prompt
    assert '"slides"' in prompt


def test_presentation_content_generation_uses_balanced_section_batches() -> None:
    sections = [
        LearningSection(
            id=f"section_{index:03d}",
            title=f"章节 {index}",
            summary=f"章节 {index} 的完整内容。",
            knowledge_points=[f"知识点 {index}"],
            source_refs=[source_ref().model_copy(update={"page_no": index})],
        )
        for index in range(1, 12)
    ]
    content = LearningContent(
        id="content_batched",
        material_id="mat_001",
        title="分批生成测试",
        sections=sections,
    )
    llm = FakeLLMProvider()
    complete_json = llm.complete_json
    batch_sizes = []

    def track_batches(messages, *, temperature=0.2):
        if "PPT_CONTENT_BATCH" in messages[0].content:
            batch_sizes.append(len(json.loads(messages[-1].content)["sections"]))
        return complete_json(messages, temperature=temperature)

    llm.complete_json = track_batches

    plan = PresentationPlanGenerator(llm).generate(content)

    assert batch_sizes == [4, 4, 3]
    assert plan.generation_source == "llm"
    assert {section.id for section in sections}.issubset(
        {section_id for slide in plan.slides for section_id in slide.source_section_ids}
    )


def test_presentation_batch_failure_only_falls_back_failed_batch() -> None:
    sections = [
        LearningSection(
            id=f"section_{index:03d}",
            title=f"章节 {index}",
            summary=f"章节 {index} 的完整内容。",
            source_refs=[source_ref().model_copy(update={"page_no": index})],
        )
        for index in range(1, 9)
    ]
    content = LearningContent(
        id="content_partial_fallback",
        material_id="mat_001",
        title="局部回退测试",
        sections=sections,
    )
    llm = FakeLLMProvider()
    complete_json = llm.complete_json
    call_count = 0

    def fail_second_batch(messages, *, temperature=0.2):
        nonlocal call_count
        if "PPT_CONTENT_BATCH" in messages[0].content:
            call_count += 1
            if call_count == 2:
                raise TimeoutError("second batch timeout")
        return complete_json(messages, temperature=temperature)

    llm.complete_json = fail_second_batch

    plan = PresentationPlanGenerator(llm).generate(content)

    assert call_count == 2
    assert plan.generation_source == "fallback"
    assert "batch 2/2: TimeoutError" in (plan.fallback_reason or "")
    assert {section.id for section in sections}.issubset(
        {section_id for slide in plan.slides for section_id in slide.source_section_ids}
    )


def test_presentation_plan_allows_multiple_slides_for_one_section() -> None:
    content = LearningContent(
        id="content_split",
        material_id="mat_001",
        title="可拆页内容",
        sections=[
            LearningSection(
                id="section_001",
                title="概念、公式与案例",
                summary="本节内容较丰富，适合拆成多页。",
                source_refs=[source_ref()],
            )
        ],
    )
    draft = PresentationPlanDraft(
        title=content.title,
        slides=[
            SlidePlanDraft(
                source_section_ids=["section_001"],
                title="先理解核心概念",
                key_points=["概念页"],
                speaker_script="先讲概念。",
                suggested_visual="概念关系图",
            ),
            SlidePlanDraft(
                source_section_ids=["section_001"],
                title="再看公式与案例",
                key_points=["公式页", "案例页"],
                speaker_script="再讲公式和案例。",
                suggested_visual="公式与案例并列",
            ),
        ],
    )

    plan = PresentationPlanGenerator()._hydrate_draft(content, draft)

    assert len(plan.slides) == 2
    assert [slide.source_section_ids for slide in plan.slides] == [
        ["section_001"],
        ["section_001"],
    ]


def test_presentation_prompt_is_loaded_from_editable_skill_file() -> None:
    generator = PresentationPlanGenerator(FakeLLMProvider())
    messages = generator._build_messages(
        LearningContent(
            id="content_skill",
            material_id="mat_001",
            title="Prompt test",
            sections=[
                LearningSection(
                    id="section_skill",
                    title="Skill prompt",
                    summary="Verify runtime prompt loading.",
                    source_refs=[source_ref()],
                )
            ],
        )
    )
    assert generator.skill_path.name == "SKILL.md"
    assert "# 自由画布" in messages[0].content
    assert "visual_payload" in messages[0].content
    assert "概念页" in messages[0].content
    assert "例子页" in messages[0].content
    assert "推导页" in messages[0].content
    assert "练习页" in messages[0].content
    assert "禁止" in messages[0].content
    assert "不是内容上限" in messages[0].content
    assert "输入与目标、核心步骤、停止或输出、适用条件" in messages[0].content
    assert "本页讲解 K-means 的算法" in messages[0].content
    assert "像真实老师连续讲课" in messages[0].content
    assert "先完全根据 LearningContent" in messages[0].content
    assert "在最前面补一页纯封面" in messages[0].content
    assert "在最后面补一页“课程总结”" in messages[0].content
    assert "不要生成目录页" in messages[0].content
    assert "只允许在 LearningContent 基础上扩充" in messages[0].content
    assert "禁止把不同 section 合并成一张 slide" in messages[0].content
    assert "一个 section 内容较多时必须拆成多张连续 slide" in messages[0].content


def test_presentation_messages_only_pass_visual_opportunity_image_paths() -> None:
    embedded_image = "data/processed/mat_001/embedded_figure.png"
    content = LearningContent(
        id="content_images",
        material_id="mat_001",
        title="Image source test",
        sections=[
            LearningSection(
                id="section_images",
                title="Source image selection",
                summary="Use the extracted original figure.",
                visual_opportunities=[
                    VisualOpportunity(
                        type="source_image",
                        description="An original figure extracted from the material.",
                        image_path=embedded_image,
                        image_description="Original explanatory figure",
                        usage_hint="Use as the main visual",
                        source_refs=[source_ref()],
                    )
                ],
                source_refs=[source_ref()],
            )
        ],
    )
    generator = PresentationPlanGenerator()

    content_payload = json.loads(generator._build_content_messages(content)[1].content)
    content_section = content_payload["sections"][0]
    assert content_section["visual_opportunities"][0]["image_path"] == embedded_image
    assert "image_path" not in content_section["source_refs"][0]
    assert "image_path" not in content_section["visual_opportunities"][0]["source_refs"][0]

    slide = generator._fallback_plan(content).slides[0]
    scene_payload = json.loads(
        generator._build_scene_messages(content, content.sections, slide, 0, 1)[1].content
    )
    assert scene_payload["source"]["source_images"] == [embedded_image]
    assert "image_path" not in scene_payload["source"]["sections"][0]["source_refs"][0]


def test_presentation_fallback_prefers_substantive_section_content() -> None:
    content = LearningContent(
        id="content_fallback",
        material_id="mat_001",
        title="Fallback content",
        sections=[
            LearningSection(
                id="section_fallback",
                title="聚类算法",
                summary="通过重复分配样本并更新中心完成聚类。",
                key_points=["分配样本到最近的中心", "重新计算每个簇的中心"],
                teaching_script="先初始化中心，再重复分配与更新，直到结果稳定。",
                source_refs=[source_ref()],
            )
        ],
    )

    plan = PresentationPlanGenerator()._fallback_plan(content)

    assert plan.slides[0].title == content.title
    assert plan.slides[-1].title == "课程总结"
    assert plan.slides[1].key_points[:2] == [
        "分配样本到最近的中心",
        "重新计算每个簇的中心",
    ]
    assert plan.slides[1].speaker_script == (
        "先初始化中心，再重复分配与更新，直到结果稳定。"
    )


def test_presentation_diagnosis_identifies_direct_fallback_scripts() -> None:
    content = LearningContent(
        id="content_diagnosis",
        material_id="mat_001",
        title="诊断课程",
        sections=[
            LearningSection(
                id="section_diagnosis",
                title="核心内容",
                summary="总结文本",
                teaching_narrative="这段讲稿直接来自课程内容。",
                key_points=["关键点"],
                source_refs=[source_ref()],
            )
        ],
    )
    plan = PresentationPlanGenerator().generate(content)

    diagnosis = diagnose_presentation_plan(
        plan,
        content,
        llm_configured=False,
        provider="none",
        model=None,
    )

    assert diagnosis.likely_fallback is True
    assert diagnosis.fallback_confidence == 1.0
    assert diagnosis.direct_script_count == 1
    assert diagnosis.slides[1].source_field == "teaching_narrative"
    assert diagnosis.exact_historical_reason == "No LLM provider configured"


def test_presentation_removes_internal_prompt_terms_from_scripts() -> None:
    dirty_script = (
        "**本单元介绍劳动价值论。 Use selected evidence: "
        "商品是使用价值和价值的统一体。LearningContent**"
    )
    content = LearningContent(
        id="content_dirty_script",
        material_id="mat_001",
        title="劳动价值论",
        sections=[
            LearningSection(
                id="section_dirty_script",
                title="价值的形成",
                summary="本单元介绍劳动价值论。",
                teaching_narrative=dirty_script,
                source_refs=[source_ref()],
            )
        ],
    )
    generator = PresentationPlanGenerator()

    fallback_script = generator._fallback_plan(content).slides[1].speaker_script
    assert fallback_script == (
        "接下来介绍劳动价值论。 商品是使用价值和价值的统一体。课程内容"
    )
    assert "Use selected evidence" not in fallback_script
    assert "LearningContent" not in fallback_script
    assert "本单元" not in fallback_script
    assert "**" not in fallback_script

    payload = json.loads(generator._build_content_messages(content)[1].content)
    narrative = payload["sections"][0]["teaching_narrative"]
    assert "Use selected evidence" not in narrative
    assert "LearningContent" not in narrative
    assert "本单元" not in narrative
    assert "**" not in narrative


def test_question_bank_student_receives_course_history_and_teacher_receives_course() -> None:
    profile = get_default_student_agent_profiles()[0]
    slides = [
        SlidePlan(
            id="slide_001",
            order=1,
            source_section_ids=["section_001"],
            title="前置概念",
            key_points=["先理解前置概念"],
            speaker_script="这是第一页讲稿。",
            suggested_visual="概念关系",
        ),
        SlidePlan(
            id="slide_002",
            order=2,
            source_section_ids=["section_001"],
            title="当前概念",
            key_points=["当前结论建立在前置概念上"],
            speaker_script="这是第二页讲稿。",
            suggested_visual="前后关系",
        ),
    ]
    student_messages = QuestionBankGenerator._student_messages(
        slides[1], profile, slides
    )
    assert "课程开始到当前页的全部 PPT" in student_messages[0].content
    assert "这是第一页讲稿" in student_messages[1].content
    assert "这是第二页讲稿" in student_messages[1].content

    content = LearningContent(
        id="content_qa_context",
        material_id="mat_001",
        title="完整课程",
        objectives=["理解前后概念关系"],
        sections=[
            LearningSection(
                id="section_001",
                title="课程知识",
                summary="LearningContent 中的完整课程材料。",
                source_refs=[source_ref()],
            )
        ],
    )
    plan = PresentationPlan(
        id="presentation_qa_context",
        content_id=content.id,
        title=content.title,
        slides=slides,
    )
    candidate = QuestionCandidate(
        candidate_id="candidate_001",
        slide_id="slide_002",
        slide_order=2,
        agent_type=profile.type,
        student_profile_id=profile.id,
        knowledge_point="前后关系",
        canonical_question="这两个概念有什么关系？",
        student_question="老师，这两个概念是怎么连起来的？",
        reason="需要建立前后联系。",
    )
    llm = Mock()
    llm.complete_json.return_value = (
        '{"answers":[{"candidate_id":"candidate_001",'
        '"canonical_answer":"标准答案",'
        '"teacher_answer":"课堂回答","answerable":true}]}'
    )
    QuestionBankGenerator(llm)._teacher_answers(content, plan, [candidate])
    teacher_messages = llm.complete_json.call_args.args[0]
    assert "整节课的 PPT 页面内容、全部讲稿和 LearningContent" in teacher_messages[0].content
    assert "这是第一页讲稿" in teacher_messages[1].content
    assert "LearningContent 中的完整课程材料" in teacher_messages[1].content

    batch_messages = QuestionBankGenerator._student_batch_messages(plan, profile)
    checkpoints = json.loads(batch_messages[1].content)["checkpoints"]
    assert [item["slide_id"] for item in checkpoints[0]["course_so_far"]] == [
        "slide_001"
    ]
    assert [item["slide_id"] for item in checkpoints[1]["course_so_far"]] == [
        "slide_001",
        "slide_002",
    ]


def test_question_bank_batches_students_in_parallel_and_teacher_once() -> None:
    class ParallelBatchLLM:
        def __init__(self) -> None:
            self.student_barrier = threading.Barrier(8)
            self.student_calls = 0
            self.teacher_calls = 0
            self.controller_calls = 0
            self.lock = threading.Lock()

        def complete_json(self, messages, temperature=0.0):
            system = messages[0].content
            if "一次性完成整节课各阶段" in system:
                with self.lock:
                    self.student_calls += 1
                self.student_barrier.wait(timeout=3)
                payload = json.loads(messages[1].content)
                slide_id = payload["checkpoints"][0]["current_slide_id"]
                return json.dumps(
                    {
                        "questions": [
                            {
                                "slide_id": slide_id,
                                "knowledge_point": "批量知识点",
                                "canonical_question": "为什么成立？",
                                "student_question": "老师，这为什么成立呀？",
                                "reason": "需要理解原因",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "教师智能体" in system:
                with self.lock:
                    self.teacher_calls += 1
                payload = json.loads(messages[1].content)
                return json.dumps(
                    {
                        "answers": [
                            {
                                "candidate_id": item["candidate_id"],
                                "canonical_answer": "标准答案",
                                "teacher_answer": "课堂答案",
                                "answerable": True,
                            }
                            for item in payload["questions"]
                        ]
                    },
                    ensure_ascii=False,
                )
            with self.lock:
                self.controller_calls += 1
            payload = json.loads(messages[1].content)
            return json.dumps(
                {
                    "placements": [
                        {
                            "candidate_id": item["candidate_id"],
                            "approved": True,
                            "moment": "after_explanation",
                            "placement_reason": "讲解后提问",
                        }
                        for item in payload["questions"]
                    ]
                },
                ensure_ascii=False,
            )

    content = LearningContent(
        id="content_parallel_qa",
        material_id="mat_001",
        title="并行问答",
        objectives=["验证批量生成"],
        sections=[
            LearningSection(
                id="section_001",
                title="核心内容",
                summary="核心内容摘要",
                source_refs=[source_ref()],
            )
        ],
    )
    plan = PresentationPlan(
        id="presentation_parallel_qa",
        content_id=content.id,
        title=content.title,
        slides=[
            SlidePlan(
                id="slide_001",
                order=1,
                source_section_ids=["section_001"],
                title="第一页",
                key_points=["批量知识点"],
                speaker_script="第一页讲稿",
                suggested_visual="示意图",
            )
        ],
    )
    llm = ParallelBatchLLM()

    result = QuestionBankGenerator(llm, student_concurrency=8).generate(content, plan)

    assert result
    assert llm.student_calls == 8
    assert llm.teacher_calls == 1
    assert llm.controller_calls == 1


def test_teacher_check_receives_only_current_and_previous_slides() -> None:
    slides = [
        SlidePlan(
            id=f"slide_{index:03d}",
            order=index,
            source_section_ids=["section_001"],
            title=f"第{index}页",
            key_points=[f"知识点{index}"],
            speaker_script=f"这是第{index}页讲稿。",
            suggested_visual="关系图",
        )
        for index in range(1, 4)
    ]
    llm = Mock()
    llm.complete_json.return_value = (
        '{"checks":[{"slide_id":"slide_002","question":"前两页如何衔接？",'
        '"target_knowledge_point":"知识点1与知识点2的关系"}]}'
    )

    checks = ClassroomPlanGenerator(llm)._generate_teacher_checks(
        [slides[1]], slides
    )

    assert checks[0].question == "前两页如何衔接？"
    messages = llm.complete_json.call_args.args[0]
    assert "截至当前页已经讲过的全部 PPT" in messages[0].content
    assert "这是第1页讲稿" in messages[1].content
    assert "这是第2页讲稿" in messages[1].content
    assert "这是第3页讲稿" not in messages[1].content


def test_teacher_check_guarantees_one_probe_when_model_selects_none() -> None:
    slides = [
        SlidePlan(
            id=f"slide_{index:03d}",
            order=index,
            source_section_ids=["section_001"],
            title=f"第{index}页",
            key_points=[f"知识点{index}"],
            speaker_script=f"这是第{index}页讲稿。",
            suggested_visual="关系图",
        )
        for index in range(1, 4)
    ]
    llm = Mock()
    llm.complete_json.return_value = '{"checks":[]}'

    checks = ClassroomPlanGenerator(llm)._generate_teacher_checks(slides, slides)

    assert len(checks) == 1
    assert checks[0].slide_id == "slide_002"
    assert "不要按固定页数或固定间隔" in llm.complete_json.call_args.args[0][0].content


def test_slide_element_must_stay_inside_canvas() -> None:
    with pytest.raises(ValidationError):
        SlideElement(type="text", x=0.9, y=0.1, w=0.2, h=0.1, text="overflow")
    line = SlideElement(type="line", x=0.1, y=0.2, w=0.5, h=0)
    assert line.h == 0


def test_presentation_scene_rejects_overlapping_content_objects() -> None:
    generator = PresentationPlanGenerator()
    elements = [
        SlideElement(
            type="text",
            x=0.05,
            y=0.06,
            w=0.9,
            h=0.12,
            text="页面标题",
        ),
        SlideElement(
            type="image",
            x=0.08,
            y=0.25,
            w=0.5,
            h=0.5,
            image_path="source.png",
        ),
        SlideElement(
            type="text",
            x=0.1,
            y=0.3,
            w=0.4,
            h=0.12,
            text="不应直接压在图片上的正文",
        ),
    ]

    assert generator._has_unsafe_scene_collisions(elements, "页面标题")


def test_presentation_scene_allows_text_on_background_card() -> None:
    generator = PresentationPlanGenerator()
    elements = [
        SlideElement(
            type="shape",
            x=0.08,
            y=0.25,
            w=0.4,
            h=0.3,
            shape="rounded_rectangle",
        ),
        SlideElement(
            type="text",
            x=0.11,
            y=0.3,
            w=0.34,
            h=0.15,
            text="卡片中的正文",
        ),
    ]

    assert not generator._has_unsafe_scene_collisions(elements, "其他标题")


def test_presentation_scene_rejects_title_body_overlap() -> None:
    generator = PresentationPlanGenerator()
    elements = [
        SlideElement(
            type="text",
            x=0.06,
            y=0.05,
            w=0.88,
            h=0.14,
            text="页面标题",
        ),
        SlideElement(
            type="text",
            x=0.08,
            y=0.15,
            w=0.5,
            h=0.16,
            text="正文不应压住标题",
        ),
    ]

    assert generator._has_unsafe_scene_collisions(elements, "页面标题")


def test_presentation_text_fitting_shrinks_font_without_growing_box() -> None:
    fitted = PresentationPlanGenerator._fit_font_size(
        text="较长的正文内容需要在原有文本框中通过字号调整完成适配",
        width=0.28,
        height=0.15,
        font_size=24,
        min_font_size=12,
        is_title=False,
    )

    assert fitted is not None
    assert 12 <= fitted < 24


def test_presentation_layout_registry_has_broad_semantic_variety() -> None:
    assert len(LAYOUT_REGISTRY) == 16
    assert len({item.family for item in LAYOUT_REGISTRY}) >= 8
    assert all(item.max_points >= 1 for item in LAYOUT_REGISTRY)


def test_every_registered_layout_builds_inside_canvas() -> None:
    slide = SlidePlan(
        id="slide_registry",
        order=1,
        source_section_ids=["section_001"],
        title="注册表版式边界测试",
        key_points=["要点一", "要点二", "要点三", "要点四", "要点五"],
        speaker_script="逐项讲解。",
        suggested_visual="结构化表达",
    )
    palette = ("FFFFFF", "172033", "3976D3", "F3F5F8")

    for spec in LAYOUT_REGISTRY:
        elements = build_fallback_elements(slide, spec, palette)
        assert elements
        assert all(item.x + item.w <= 1 and item.y + item.h <= 1 for item in elements)
        assert all(isinstance(item.style, SlideElementStyle) for item in elements)


def test_layout_skeleton_keeps_full_text_and_expands_secondary_cards() -> None:
    long_point = "页面仍有空白时，应优先扩大文本框并保留完整内容，而不是静默添加省略号"
    slide = SlidePlan(
        id="slide_capacity",
        order=2,
        source_section_ids=["section_001"],
        title="容量适配",
        key_points=["核心结论", long_point, long_point, long_point],
        speaker_script="解释容量适配策略。",
        suggested_visual="左右分栏",
    )
    spec = next(item for item in LAYOUT_REGISTRY if item.id == "split_left")

    elements = build_fallback_elements(
        slide, spec, ("FFFFFF", "17324D", "4F8FCB", "DCEBFA")
    )
    body = [item for item in elements if item.type == "text" and item.text == long_point]

    assert len(body) == 3
    assert all("…" not in (item.text or "") and "..." not in (item.text or "") for item in body)
    assert all(item.h > 0.11 for item in body)


def test_layout_capacity_split_preserves_all_points() -> None:
    spec = next(item for item in LAYOUT_REGISTRY if item.id == "focus_rail")
    points = [f"完整要点 {index}：这是不能被静默丢弃的教学内容。" for index in range(1, 8)]

    chunks = split_points_for_layout(points, spec)

    assert len(chunks) == 3
    assert [item for chunk in chunks for item in chunk] == points
    assert all(len(chunk) <= spec.max_points for chunk in chunks)


def test_layout_selection_avoids_narrow_flow_for_long_points() -> None:
    slide = SlidePlan(
        id="slide_long_process",
        order=2,
        source_section_ids=["section_001"],
        title="流程需要解释每一步的条件与结果",
        key_points=["这一阶段包含较长的条件说明、执行动作、边界情况以及执行完成后的结果解释" for _ in range(4)],
        speaker_script="解释流程。",
        suggested_visual="步骤流程",
    )

    selected = select_fallback_layout(slide, 1)

    assert selected.family not in {"process", "timeline", "hierarchy"}


def test_fallback_layout_selection_uses_current_slide_semantics() -> None:
    generator = PresentationPlanGenerator()
    slide = SlidePlan(
        id="slide_process",
        order=2,
        source_section_ids=["section_001"],
        title="算法步骤与迭代流程",
        key_points=["初始化", "分配", "更新", "停止"],
        speaker_script="依次解释算法步骤。",
        suggested_visual="横向流程",
    )
    first = generator._with_fallback_scene(slide, 1)
    second = generator._with_fallback_scene(slide, 1)

    assert first.layout == "freeform"
    assert first.layout_id
    assert second.layout_id
    assert first.layout_id == second.layout_id
    assert first.layout_id in {"sequence_horizontal", "sequence_vertical", "timeline_alternating", "ladder"}
    assert first.elements and second.elements


def test_scene_prompt_includes_layout_registry() -> None:
    content = LearningContent(
        id="content_layout_history",
        material_id="mat_001",
        title="布局历史",
        sections=[
            LearningSection(
                id="section_001",
                title="概念关系",
                summary="说明概念之间的关系。",
                source_refs=[source_ref()],
            )
        ],
    )
    generator = PresentationPlanGenerator()
    slide = generator._fallback_plan(content).slides[0]
    payload = json.loads(
        generator._build_scene_messages(content, content.sections, slide, 1, 2)[1].content
    )

    assert len(payload["deck"]["layout_registry"]) == 16
    assert "recent_layouts" not in payload["deck"]
    assert payload["deck"]["layout_constraints"]["body_min_font_size"] == 18
    assert payload["deck"]["academic_layout_guidance"]["argument_per_slide"] == 1
    assert payload["deck"]["brand_palette"]["tokens"]["amber"] == "2E75B6"


def test_scene_safe_zone_rejects_content_too_close_to_edge() -> None:
    generator = PresentationPlanGenerator()
    with pytest.raises(ValueError, match="horizontal canvas margin"):
        generator._validate_scene_safe_zones(
            [
                SlideElement(
                    type="text",
                    x=0.01,
                    y=0.25,
                    w=0.4,
                    h=0.2,
                    text="正文",
                )
            ],
            "页面标题",
        )


def test_scene_palette_maps_unrelated_colors_to_brand_tokens() -> None:
    element = SlideElement(
        type="shape",
        x=0.1,
        y=0.25,
        w=0.4,
        h=0.3,
        style={
            "color": "6A42C2",
            "fill": "1976D2",
            "line_color": "00BCD4",
        },
    )

    normalized = apply_brand_palette(element)

    assert normalized.style.color in BRAND_PALETTE.allowed_colors
    assert normalized.style.fill in BRAND_PALETTE.allowed_colors
    assert normalized.style.line_color in BRAND_PALETTE.allowed_colors
    assert "6A42C2" not in {
        normalized.style.color,
        normalized.style.fill,
        normalized.style.line_color,
    }


def test_scene_generation_retries_once_with_validation_feedback() -> None:
    llm = Mock()
    llm.complete_json.side_effect = [
        json.dumps(
            {
                "background": "FFFFFF",
                "elements": [
                    {
                        "type": "text",
                        "x": 0.06,
                        "y": 0.06,
                        "w": 0.88,
                        "h": 0.12,
                        "text": "页面标题",
                    }
                ],
            }
        ),
        json.dumps(
            {
                "background": "FFFFFF",
                "elements": [
                    {
                        "type": "text",
                        "x": 0.06,
                        "y": 0.06,
                        "w": 0.88,
                        "h": 0.12,
                        "text": "页面标题",
                    },
                    {
                        "type": "shape",
                        "x": 0.1,
                        "y": 0.3,
                        "w": 0.8,
                        "h": 0.4,
                        "shape": "rectangle",
                        "style": {"fill": "EDEDED"},
                    },
                ],
            }
        ),
    ]
    slide = SlidePlan(
        id="slide_retry",
        order=1,
        source_section_ids=["section_001"],
        title="页面标题",
        key_points=["核心内容"],
        speaker_script="讲解核心内容。",
        suggested_visual="使用一个视觉区域。",
    )
    generator = PresentationPlanGenerator(llm)

    scene = generator._generate_scene_with_repair(slide, [], 0)

    assert scene.elements[-1].type == "shape"
    assert llm.complete_json.call_count == 2
    repair_messages = llm.complete_json.call_args.args[0]
    assert "失败原因" in repair_messages[-1].content


def test_classroom_plan_covers_every_presentation_slide() -> None:
    sections = [
        LearningSection(
            id=f"section_{index:03d}",
            title=f"章节 {index}",
            summary=f"章节 {index} 的教学内容",
            source_refs=[source_ref().model_copy(update={"page_no": index})],
        )
        for index in range(1, 4)
    ]
    content = LearningContent(
        id="content_ten_slides",
        material_id="mat_001",
        title="三节十页课程",
        sections=sections,
    )
    slides = [
        SlidePlan(
            id=f"slide_{index:03d}",
            order=index,
            source_section_ids=[sections[min((index - 1) // 4, 2)].id],
            title=f"第 {index} 页",
            key_points=[f"第 {index} 页要点"],
            speaker_script=f"讲解第 {index} 页。",
            suggested_visual="教学图示",
        )
        for index in range(1, 11)
    ]
    presentation = PresentationPlan(
        id="presentation_plan_ten",
        content_id=content.id,
        title=content.title,
        slides=slides,
    )

    plan, _ = ClassroomPlanGenerator(FakeLLMProvider()).generate_with_meta(content, presentation)

    show_actions = [
        action
        for scene in plan.scenes
        for action in scene.actions
            if isinstance(action, ShowSlideAction)
    ]
    assert len(plan.scenes) == 10
    assert [action.payload.slide_no for action in show_actions] == list(range(1, 11))


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
            teacher.build_scene(index, section) for index, section in enumerate(sections, start=1)
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


def test_probe_names_the_same_selected_student_who_answers() -> None:
    section = LearningSection(
        id="section_001",
        title="第一页",
        summary="老师应点名具体学生回答。",
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
    students = get_student_agent_states([StudentAgentType.DEEP_THINKER])
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
        action_index=probe_index,
        student_states=students,
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan
    service = ClassroomService(repository, Mock())

    probe_result = service.auto_step(session.id)
    answer_result = service.auto_step(session.id)

    assert probe_result.action is not None
    assert probe_result.action.payload.question.startswith("浩浩同学，")
    assert session.events[0].payload.target_agent_id == students[0].id
    assert answer_result.directed_turn is not None
    assert answer_result.directed_turn.turns[0].agent_id == students[0].id


def test_student_answer_turn_forbids_starting_a_new_question() -> None:
    student = get_default_student_agent_states()[0]
    state = ClassroomSession(
        id="session_answer_rule",
        plan_id="plan_answer_rule",
        mode="interactive",
        student_states=[student],
    )
    classroom_state = ClassroomState(
        session_id=state.id,
        plan_id=state.plan_id,
        mode=state.mode,
        status="running",
        scene_index=0,
        action_index=0,
        students=state.student_states,
    )
    profile = get_default_student_agent_profiles()[0]

    messages = build_student_messages(
        profile=profile,
        state=student,
        classroom_state=classroom_state,
        prompt="老师刚刚问：这个结论为什么成立？这是回答回合，请直接回答。",
        allowed_actions=["PROBE"],
    )

    assert "不能反问、不能提出新问题" in messages[0].content


def test_teacher_feedback_removes_unplanned_question_before_quiz() -> None:
    speech = (
        "包包同学说得非常准确，FVC变化图确实能反映植被覆盖度变化。"
        "那么接下来，我想请大家思考一个问题：除了FVC还有哪些指数？"
        "哪位同学愿意分享一下？"
    )

    result = ClassroomService._remove_unplanned_follow_up_question(speech)

    assert result == "包包同学说得非常准确，FVC变化图确实能反映植被覆盖度变化。"
    assert "哪位同学" not in result
    assert "？" not in result


def test_planned_probe_dialog_skips_recent_student_speaker() -> None:
    section = LearningSection(
        id="section_001",
        title="第一页",
        summary="老师追问后应轮换学生，避免同一个学生连续发言。",
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
    students = get_student_agent_states(
        [StudentAgentType.DEEP_THINKER, StudentAgentType.RESEARCHER]
    )
    students[0].last_intent = "student_answer_planned_probe"
    session = ClassroomSession(
        id="session_001",
        plan_id=plan.id,
        mode="interactive",
        action_index=probe_index + 1,
        student_states=students,
        events=[
            AgentTurnEvent(
                id="event_000",
                session_id="session_001",
                type="AGENT_TURN",
                payload=AgentTurnPayload(
                    turn=AgentTurn(
                        agent_id=students[0].id,
                        role="student",
                        speech="我先说一下自己的理解。",
                        intent="student_answer_planned_probe",
                    )
                ),
            ),
            ActionExecutedEvent(
                id="event_001",
                session_id="session_001",
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id="scene_001_probe",
                    action_type=ActionType.PROBE,
                ),
            ),
        ],
    )
    repository = Mock()
    repository.get_session.return_value = session
    repository.get_plan.return_value = plan

    result = ClassroomService(repository, Mock()).auto_step(session.id)

    assert result.status == "agent_turn"
    assert result.directed_turn is not None
    assert result.directed_turn.decision.next_agent_id == students[1].id
    assert result.directed_turn.turns[0].agent_id == students[1].id


def test_scripted_qa_rotates_away_from_recent_preferred_student() -> None:
    students = get_student_agent_states(
        [StudentAgentType.DEEP_THINKER, StudentAgentType.RESEARCHER]
    )
    students[0].last_intent = "scripted_qa_question"
    session = ClassroomSession(
        id="session_scripted_rotation",
        plan_id="plan_scripted_rotation",
        mode="interactive",
        student_states=students,
        events=[
            AgentTurnEvent(
                id="event_recent_scripted",
                session_id="session_scripted_rotation",
                type="AGENT_TURN",
                payload=AgentTurnPayload(
                    turn=AgentTurn(
                        agent_id=students[0].id,
                        role="student",
                        speech="我刚刚提出过一个问题。",
                        intent="scripted_qa_question",
                    )
                ),
            )
        ],
    )
    action = StudentQuestionAction(
        id="student_question_rotation",
        type="STUDENT_QUESTION",
        actor="student",
        payload=StudentQuestionPayload(
            qa_id="qa_rotation",
            preferred_agent_type=StudentAgentType.DEEP_THINKER,
            fallback_agent_types=[StudentAgentType.RESEARCHER],
        ),
    )

    selected = ClassroomService._select_scripted_qa_student(session, action)

    assert selected is not None
    assert selected.id == students[1].id


@pytest.mark.parametrize(
    ("question", "expected_type"),
    [
        ("为什么这个结论在该前提下成立？", StudentAgentType.DEEP_THINKER),
        ("请总结这一页的核心要点。", StudentAgentType.NOTE_TAKER),
        ("这个方法在实际项目中怎么应用？", StudentAgentType.PRACTICAL_APPLIER),
    ],
)
def test_probe_student_selection_matches_question_type(
    monkeypatch, question: str, expected_type: StudentAgentType
) -> None:
    students = get_student_agent_states(
        [
            StudentAgentType.DEEP_THINKER,
            StudentAgentType.NOTE_TAKER,
            StudentAgentType.PRACTICAL_APPLIER,
        ]
    )
    state = ClassroomState(
        session_id="session_weighted_selection",
        plan_id="plan_weighted_selection",
        mode="interactive",
        status="running",
        scene_index=0,
        action_index=0,
        students=students,
    )
    monkeypatch.setattr(
        "metaclass.modules.classroom.service.random.uniform", lambda _low, _high: 0.0
    )

    selected = ClassroomService._select_dialog_student(state, question=question)

    assert selected is not None
    assert selected.agent_type == expected_type


def test_probe_student_selection_rewards_unspoken_and_penalizes_recent(monkeypatch) -> None:
    students = get_student_agent_states(
        [StudentAgentType.DEEP_THINKER, StudentAgentType.NOTE_TAKER]
    )
    students[0].last_intent = "student_answer_planned_probe"
    state = ClassroomState(
        session_id="session_rotation",
        plan_id="plan_rotation",
        mode="interactive",
        status="running",
        scene_index=0,
        action_index=0,
        students=students,
        recent_events=[
            AgentTurnEvent(
                id="event_recent",
                session_id="session_rotation",
                type="AGENT_TURN",
                payload=AgentTurnPayload(
                    turn=AgentTurn(
                        agent_id=students[0].id,
                        role="student",
                        speech="我刚刚回答过。",
                        intent="student_answer_planned_probe",
                    )
                ),
            )
        ],
    )
    monkeypatch.setattr(
        "metaclass.modules.classroom.service.random.uniform", lambda _low, _high: 0.0
    )

    selected = ClassroomService._select_dialog_student(
        state, question="为什么这个结论成立？"
    )

    assert selected is not None
    assert selected.id == students[1].id


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
