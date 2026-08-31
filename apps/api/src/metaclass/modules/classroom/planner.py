from __future__ import annotations

import json
import math
from uuid import uuid4

from pydantic import Field, ValidationError

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.classroom.agents import TeacherAgent
from metaclass.modules.classroom.schemas import (
    AskQuizAction,
    ClassroomPlan,
    ClassroomPlanGenerationMeta,
    ClassroomScene,
    EndAction,
    EndPayload,
    ExplainAction,
    GiveFeedbackAction,
    ProbeAction,
    ProbePayload,
    ReviewAction,
    ReviewPayload,
    ShowSlideAction,
    ShowSlidePayload,
    StudentQuestionAction,
    StudentQuestionPayload,
    TeacherQAResponseAction,
    TeacherQAResponsePayload,
)
from metaclass.modules.content.schemas import LearningContent
from metaclass.modules.presentation.schemas import PresentationPlan
from metaclass.modules.question_bank.schemas import ClassroomQA


class ScenePlanBlueprint(SchemaModel):
    section_id: str = Field(min_length=1)
    include_probe: bool = False
    probe_question: str | None = None
    include_quiz: bool = True
    include_review: bool = False
    teaching_note: str | None = None


class ClassroomPlanBlueprint(SchemaModel):
    scenes: list[ScenePlanBlueprint] = Field(default_factory=list)


class TeacherCheckBlueprint(SchemaModel):
    slide_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    target_knowledge_point: str = Field(min_length=1)


class ClassroomPlanGenerator:
    """Generate a validated classroom plan from LearningContent.

    The planner is the bridge between upstream PDF/PPT parsing and classroom
    runtime. It accepts the stable LearningContent contract and returns the
    ClassroomPlan contract consumed by ClassroomController/ClassroomService.
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        fallback_teacher: TeacherAgent | None = None,
    ) -> None:
        self.llm = llm
        self.fallback_teacher = fallback_teacher or TeacherAgent()

    def generate(self, content: LearningContent) -> ClassroomPlan:
        plan, _ = self.generate_with_meta(content)
        return plan

    def generate_with_meta(
        self,
        content: LearningContent,
        presentation_plan: PresentationPlan | None = None,
        qa_items: list[ClassroomQA] | None = None,
    ) -> tuple[ClassroomPlan, ClassroomPlanGenerationMeta]:
        fallback = self._fallback_plan(content)
        if not self.llm:
            if presentation_plan:
                fallback = self._align_to_presentation(content, fallback, presentation_plan)
                fallback = self._prepare_teacher_check_actions(
                    fallback, presentation_plan, qa_items or []
                )
                fallback = self._insert_question_bank_actions(
                    fallback, presentation_plan, qa_items or []
                )
            return fallback, self._meta(
                fallback,
                source="fallback",
                fallback_reason="No LLM provider configured",
            )
        try:
            raw = self.llm.complete_json(self._build_messages(content), temperature=0.2)
            payload = json.loads(raw)
            blueprint = ClassroomPlanBlueprint.model_validate(payload)
            plan = self._hydrate_blueprint(content, blueprint)
            if presentation_plan:
                plan = self._align_to_presentation(content, plan, presentation_plan)
                plan = self._prepare_teacher_check_actions(
                    plan, presentation_plan, qa_items or []
                )
                plan = self._insert_question_bank_actions(
                    plan, presentation_plan, qa_items or []
                )
            self._validate_runtime_sequence(plan)
            return plan, self._meta(
                plan,
                source="llm",
                raw_response=raw,
                parsed_blueprint=blueprint.model_dump(mode="json"),
            )
        except (
            TimeoutError,
            json.JSONDecodeError,
            ValidationError,
            RuntimeError,
            ValueError,
        ) as exc:
            if presentation_plan:
                fallback = self._align_to_presentation(content, fallback, presentation_plan)
                fallback = self._prepare_teacher_check_actions(
                    fallback, presentation_plan, qa_items or []
                )
                fallback = self._insert_question_bank_actions(
                    fallback, presentation_plan, qa_items or []
                )
            return fallback, self._meta(
                fallback,
                source="fallback",
                fallback_reason=str(exc),
                raw_response=locals().get("raw"),
            )

    @staticmethod
    def _align_to_presentation(
        content: LearningContent,
        classroom_plan: ClassroomPlan,
        presentation_plan: PresentationPlan,
    ) -> ClassroomPlan:
        """Expand section-based classroom scenes to cover every generated PPT slide."""
        sections = {section.id: section for section in content.sections}
        scenes_by_section = {
            section.id: scene
            for section, scene in zip(content.sections, classroom_plan.scenes, strict=False)
        }
        last_slide_for_section: dict[str, int] = {}
        for slide_no, slide in enumerate(presentation_plan.slides, start=1):
            for section_id in slide.source_section_ids:
                last_slide_for_section[section_id] = slide_no

        scenes: list[ClassroomScene] = []
        for slide_no, slide in enumerate(presentation_plan.slides, start=1):
            section_id = next(
                (item for item in slide.source_section_ids if item in sections),
                content.sections[0].id,
            )
            section = sections[section_id]
            prefix = f"slide_scene_{slide_no:03d}"
            actions = [
                ShowSlideAction(
                    id=f"{prefix}_show",
                    type="SHOW_SLIDE",
                    actor="system",
                    payload=ShowSlidePayload(
                        presentation_resource_id=(
                            presentation_plan.presentation_resource_id
                            or presentation_plan.id
                        ),
                        slide_id=slide.id,
                        slide_no=slide_no,
                    ),
                ),
                ExplainAction(
                    id=f"{prefix}_explain",
                    type="EXPLAIN",
                    actor="teacher",
                    payload={
                        "text": slide.speaker_script,
                        "source_refs": section.source_refs,
                    },
                ),
            ]
            if last_slide_for_section.get(section_id) == slide_no:
                base_scene = scenes_by_section.get(section_id)
                if base_scene:
                    actions.extend(base_scene.actions[2:])
            if not isinstance(actions[-1], EndAction):
                actions.append(
                    EndAction(
                        id=f"{prefix}_end",
                        type="END",
                        actor="system",
                        payload=EndPayload(
                            summary="；".join(slide.key_points[:3]) or slide.title
                        ),
                    )
                )
            scenes.append(ClassroomScene(id=prefix, title=slide.title, actions=actions))
        return ClassroomPlan(
            id=f"plan_{uuid4().hex[:12]}",
            content_id=content.id,
            scenes=scenes,
        )

    @classmethod
    def _insert_question_bank_actions(
        cls,
        classroom_plan: ClassroomPlan,
        presentation_plan: PresentationPlan,
        qa_items: list[ClassroomQA],
    ) -> ClassroomPlan:
        selected = cls._select_classroom_questions(presentation_plan, qa_items)
        by_slide = {item.slide_id: item for item in selected}
        scenes = []
        for slide, scene in zip(
            presentation_plan.slides, classroom_plan.scenes, strict=False
        ):
            qa = by_slide.get(slide.id)
            if not qa:
                scenes.append(scene)
                continue
            actions = list(scene.actions)
            pair = [
                StudentQuestionAction(
                    id=f"{scene.id}_student_qa_{qa.id}",
                    type="STUDENT_QUESTION",
                    actor="student",
                    payload=StudentQuestionPayload(
                        qa_id=qa.id,
                        preferred_agent_type=qa.agent_type,
                        fallback_agent_types=(
                            qa.compatible_agent_types
                            or cls._fallback_agent_types(qa.agent_type)
                        ),
                    ),
                ),
                TeacherQAResponseAction(
                    id=f"{scene.id}_teacher_qa_{qa.id}",
                    type="TEACHER_QA_RESPONSE",
                    actor="teacher",
                    payload=TeacherQAResponsePayload(qa_id=qa.id),
                ),
            ]
            if qa.moment == "before_explanation":
                insert_at = next(
                    (index for index, action in enumerate(actions) if isinstance(action, ExplainAction)),
                    1,
                )
            elif qa.moment == "before_next_slide":
                insert_at = next(
                    (index for index, action in enumerate(actions) if isinstance(action, EndAction)),
                    len(actions),
                )
            else:
                insert_at = next(
                    (
                        index + 1
                        for index, action in enumerate(actions)
                        if isinstance(action, ExplainAction)
                    ),
                    min(2, len(actions)),
                )
            actions[insert_at:insert_at] = pair
            scenes.append(scene.model_copy(update={"actions": actions}))
        return classroom_plan.model_copy(update={"scenes": scenes})

    def _prepare_teacher_check_actions(
        self,
        classroom_plan: ClassroomPlan,
        presentation_plan: PresentationPlan,
        qa_items: list[ClassroomQA],
    ) -> ClassroomPlan:
        """Pre-generate a small number of teacher listening checks before class.

        Student-originated QA is the primary scripted interaction. Teacher checks
        are placed on different slides so the lesson does not become a sequence of
        teacher questions followed by student answers.
        """
        student_qa_slide_ids = {
            item.slide_id
            for item in self._select_classroom_questions(presentation_plan, qa_items)
        }
        eligible = [
            slide for slide in presentation_plan.slides if slide.id not in student_qa_slide_ids
        ]
        # Let the teacher model choose checkpoints by instructional value rather
        # than page count. _generate_teacher_checks guarantees at least one check.
        prepared = self._generate_teacher_checks(eligible, presentation_plan.slides)
        checks_by_slide = {item.slide_id: item for item in prepared}

        scenes = []
        for slide, scene in zip(
            presentation_plan.slides, classroom_plan.scenes, strict=False
        ):
            # Remove section-blueprint probes. They were generated before the PPT
            # and are replaced by checks grounded in the actual page and script.
            actions = [action for action in scene.actions if not isinstance(action, ProbeAction)]
            check = checks_by_slide.get(slide.id)
            if check:
                explain = next(
                    (action for action in actions if isinstance(action, ExplainAction)),
                    None,
                )
                if explain:
                    insert_at = actions.index(explain) + 1
                    actions.insert(
                        insert_at,
                        ProbeAction(
                            id=f"{scene.id}_prepared_teacher_check",
                            type="PROBE",
                            actor="teacher",
                            payload=ProbePayload(
                                question=check.question,
                                target_knowledge_point=check.target_knowledge_point,
                                source_refs=explain.payload.source_refs,
                            ),
                        ),
                    )
            scenes.append(scene.model_copy(update={"actions": actions}))
        return classroom_plan.model_copy(update={"scenes": scenes})

    def _generate_teacher_checks(
        self, slides: list, all_slides: list
    ) -> list[TeacherCheckBlueprint]:
        if not slides:
            return []
        fallback = [
            TeacherCheckBlueprint(
                slide_id=slide.id,
                question=(
                    f"根据刚才的讲解，谁能用自己的话说明“"
                    f"{(slide.key_points or [slide.title])[0].rstrip('。')}”为什么成立？"
                ),
                target_knowledge_point=(slide.key_points or [slide.title])[0],
            )
            for slide in slides
        ]
        if not self.llm:
            return [fallback[len(fallback) // 2]]
        system = """你是备课阶段的教师智能体。请根据候选页面截至当前页已经讲过的全部 PPT 页面内容和全部老师讲稿，选择真正值得检查理解的节点，并提前设计课堂上的听课检查问题。
问题用于检查学生是否听懂刚讲过的概念、机制、步骤或前后关系，而不是让学生猜尚未讲过的知识。
你可以检查当前页本身，也可以检查当前页与此前页面的概念衔接、因果关系和综合理解；不得使用当前页之后的内容。
每页只生成一个具体、可简短作答的问题；不要问“听懂了吗”，不要重复讲稿原句，不要出冷知识。
不要按固定页数或固定间隔机械安排。只有问题能暴露关键误解、检查重要推理或连接核心知识时才安排。
整节课至少安排一次检查；高价值检查点可以安排多次，但不要为了数量打断课堂。
问题会在该页讲解完成后由老师说出。只输出 JSON：
{"checks":[{"slide_id":"...","question":"...","target_knowledge_point":"..."}]}"""
        user = {
            "checkpoints": [
                {
                    "current_slide_id": slide.id,
                    "current_slide_order": slide.order,
                    "course_so_far": [
                        {
                            "slide_id": history.id,
                            "slide_order": history.order,
                            "title": history.title,
                            "page_content": history.key_points,
                            "visual_content": history.visual_payload,
                            "speaker_script": history.speaker_script,
                        }
                        for history in all_slides
                        if history.order <= slide.order
                    ],
                }
                for slide in slides
            ]
        }
        try:
            raw = self.llm.complete_json(
                [
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
                ],
                temperature=0.2,
            )
            payload = json.loads(raw)
            checks = [
                TeacherCheckBlueprint.model_validate(item)
                for item in payload.get("checks", [])
            ]
            allowed = {slide.id for slide in slides}
            checks = [item for item in checks if item.slide_id in allowed]
            if checks:
                return checks
            return [fallback[len(fallback) // 2]]
        except (
            TypeError,
            TimeoutError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ):
            return [fallback[len(fallback) // 2]]

    @staticmethod
    def _spread_slides(slides: list, budget: int) -> list:
        if not slides or budget <= 0:
            return []
        if budget == 1:
            return [slides[len(slides) // 2]]
        indexes = sorted(
            {round(index * (len(slides) - 1) / (budget - 1)) for index in range(budget)}
        )
        return [slides[index] for index in indexes]

    @staticmethod
    def _select_classroom_questions(
        presentation_plan: PresentationPlan,
        qa_items: list[ClassroomQA],
    ) -> list[ClassroomQA]:
        approved = [item for item in qa_items if item.status == "approved"]
        groups = []
        for slide in presentation_plan.slides:
            candidates = [item for item in approved if item.slide_id == slide.id]
            if candidates:
                groups.append(candidates)
        if not groups:
            return []
        budget = min(len(groups), max(1, math.ceil(len(presentation_plan.slides) / 2)))
        if budget == 1:
            group_indexes = [0]
        else:
            group_indexes = sorted(
                {
                    round(index * (len(groups) - 1) / (budget - 1))
                    for index in range(budget)
                }
            )
        selected = []
        used_types = set()
        type_priority = [
            "deep_thinker",
            "concept_confused",
            "foundation_weak",
            "practical_applier",
            "researcher",
            "classroom_atmosphere_regulator",
            "note_taker",
            "silent_observer",
        ]
        for group_index in group_indexes:
            candidates = groups[group_index]
            candidates = sorted(
                candidates,
                key=lambda item: (
                    item.agent_type in used_types,
                    type_priority.index(item.agent_type.value)
                    if item.agent_type.value in type_priority
                    else len(type_priority),
                ),
            )
            selected.append(candidates[0])
            used_types.add(candidates[0].agent_type)
        return selected

    @staticmethod
    def _fallback_agent_types(agent_type):
        from metaclass.modules.classroom.agent_schemas import StudentAgentType

        fallbacks = {
            StudentAgentType.DEEP_THINKER: [
                StudentAgentType.RESEARCHER,
                StudentAgentType.CONCEPT_CONFUSED,
            ],
            StudentAgentType.FOUNDATION_WEAK: [
                StudentAgentType.CONCEPT_CONFUSED,
                StudentAgentType.SILENT_OBSERVER,
            ],
            StudentAgentType.PRACTICAL_APPLIER: [
                StudentAgentType.RESEARCHER,
                StudentAgentType.ATMOSPHERE_REGULATOR,
            ],
            StudentAgentType.NOTE_TAKER: [
                StudentAgentType.SILENT_OBSERVER,
                StudentAgentType.FOUNDATION_WEAK,
            ],
        }
        return fallbacks.get(
            agent_type,
            [
                StudentAgentType.DEEP_THINKER,
                StudentAgentType.RESEARCHER,
                StudentAgentType.CONCEPT_CONFUSED,
            ],
        )

    def _meta(
        self,
        plan: ClassroomPlan,
        *,
        source: str,
        fallback_reason: str | None = None,
        raw_response: str | None = None,
        parsed_blueprint: dict | None = None,
    ) -> ClassroomPlanGenerationMeta:
        provider = getattr(self.llm, "name", None) or "none"
        model = getattr(self.llm, "model", None)
        return ClassroomPlanGenerationMeta(
            plan_id=plan.id,
            content_id=plan.content_id,
            source=source,
            provider=provider,
            model=model,
            fallback_reason=fallback_reason,
            raw_response=raw_response,
            parsed_blueprint=parsed_blueprint,
        )

    def _fallback_plan(self, content: LearningContent) -> ClassroomPlan:
        return ClassroomPlan(
            id=f"plan_{uuid4().hex[:12]}",
            content_id=content.id,
            scenes=[
                self.fallback_teacher.build_scene(index, section)
                for index, section in enumerate(content.sections, start=1)
            ],
        )

    def _hydrate_blueprint(
        self, content: LearningContent, blueprint: ClassroomPlanBlueprint
    ) -> ClassroomPlan:
        blueprints_by_section_id = {scene.section_id: scene for scene in blueprint.scenes}
        scenes: list[ClassroomScene] = []
        for index, section in enumerate(content.sections, start=1):
            scene = self.fallback_teacher.build_scene(index, section)
            scene_blueprint = blueprints_by_section_id.get(section.id)
            if scene_blueprint:
                actions = list(scene.actions)
                if not scene_blueprint.include_quiz:
                    actions = [
                        action
                        for action in actions
                        if not isinstance(action, AskQuizAction | GiveFeedbackAction)
                    ]
                if scene_blueprint.include_probe:
                    probe_index = next(
                        (
                            action_index + 1
                            for action_index, action in enumerate(actions)
                            if isinstance(action, ExplainAction)
                        ),
                        2,
                    )
                    actions.insert(
                        probe_index,
                        ProbeAction(
                            id=f"scene_{index:03d}_probe",
                            type="PROBE",
                            actor="teacher",
                            payload=ProbePayload(
                                question=scene_blueprint.probe_question
                                or f"你能用自己的话解释{section.title}的核心意思吗？",
                                target_knowledge_point=section.knowledge_points[0]
                                if section.knowledge_points
                                else section.title,
                                source_refs=section.source_refs,
                            ),
                        ),
                    )
                if scene_blueprint.include_review:
                    end_index = max(len(actions) - 1, 0)
                    actions.insert(
                        end_index,
                        ReviewAction(
                            id=f"scene_{index:03d}_review",
                            type="REVIEW",
                            actor="teacher",
                            payload=ReviewPayload(
                                text=scene_blueprint.teaching_note
                                or f"回顾一下：{section.summary}",
                                knowledge_points=section.knowledge_points or [section.title],
                                source_refs=section.source_refs,
                            ),
                        ),
                    )
                scene = ClassroomScene(id=scene.id, title=scene.title, actions=actions)
            scenes.append(scene)
        return ClassroomPlan(
            id=f"plan_{uuid4().hex[:12]}",
            content_id=content.id,
            scenes=scenes,
        )

    @staticmethod
    def _build_messages(content: LearningContent) -> list[LLMMessage]:
        system = """你是 MetaClass 的 ClassroomPlan planner，负责根据 LearningContent 生成课堂计划。

# 输入
用户会给你一个压缩后的 LearningContent 摘要。

# 输出
你必须只输出一个 JSON object，不要 markdown，不要解释。输出必须严格符合这个轻量蓝图：
{
  "scenes": [
    {
      "section_id": "section_xxx",
      "include_probe": true,
      "probe_question": "一个开放式理解检查问题，或 null",
      "include_quiz": true,
      "include_review": false,
      "teaching_note": "给老师的简短讲解策略，或 null"
    }
  ]
}

# 规划思路
你不是把每页机械翻译成固定动作，而是在设计一节自然的课堂。
先判断每个 section 在整节课中的作用：
- 封面、目录、过渡页：通常只展示和轻讲，不安排小测。
- 新概念/关键机制页：适合讲解后安排开放追问。
- 容易误解、概念相近、步骤复杂的页：适合安排正式小测。
- 案例/结果/对比页：适合让学生 agent 提问“为什么这样”或“换个场景还成立吗”。
- 阶段收束页：适合 review，而不是再塞新问题。

# 规划原则
- 语言服从 LearningContent 主体语言：中文或中英混合内容统一使用自然简体中文；只有实质内容为全英文时才使用英文。不要在中文课堂计划中混入英文提示语。
- 每个输入 section 都应该返回一个 scene 蓝图。
- include_probe 表示“这里值得自然师生互动”，不是常规打断；只有当问题能帮助理解、暴露误区或连接例子时才打开。
- probe_question 必须具体指向本 section 的内容，不要写泛泛的“你理解了吗”。
- 如果 section 没有 quiz，include_quiz 必须是 false。
- 如果 section 有 quiz，也不代表必须出题；一节课只在关键概念、易错点、阶段收束处安排少量正式小测。
- 多页内容时不要每页都 include_quiz=true，通常每 2-4 个 section 最多安排 1 次；短课可以 0-2 次。
- 正式小测只给真实用户做；学生 agent 不会替用户答题。
- include_review 只在阶段性总结、目录页、收束页或知识点密集页打开。
- teaching_note 写“老师如何组织这一页”的简短建议，例如先讲例子、先比较两概念、或快速略过。
- 不要输出 source_refs、quiz_items 或完整 action；后端会根据蓝图组装可运行 ClassroomPlan。
"""
        compact_sections = []
        for section in content.sections:
            compact_sections.append(
                {
                    "id": section.id,
                    "title": section.title,
                    "summary": section.summary[:500],
                    "knowledge_points": section.knowledge_points[:8],
                    "quiz_knowledge_points": [
                        quiz.knowledge_point for quiz in section.quiz_items[:3]
                    ],
                    "has_quiz": bool(section.quiz_items),
                    "quiz_questions": [quiz.question[:160] for quiz in section.quiz_items[:2]],
                    "page_numbers": sorted({ref.page_no for ref in section.source_refs}),
                }
            )
        user = {
            "content_id": content.id,
            "title": content.title,
            "objectives": content.objectives[:8],
            "sections": compact_sections,
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]

    @staticmethod
    def _validate_runtime_sequence(plan: ClassroomPlan) -> None:
        if not plan.scenes:
            raise ValueError("ClassroomPlan must contain at least one scene")
        seen_action_ids: set[str] = set()
        for scene in plan.scenes:
            ClassroomPlanGenerator._validate_scene(scene, seen_action_ids)

    @staticmethod
    def _validate_scene(scene: ClassroomScene, seen_action_ids: set[str]) -> None:
        for index, action in enumerate(scene.actions):
            if action.id in seen_action_ids:
                raise ValueError(f"Duplicate action id: {action.id}")
            seen_action_ids.add(action.id)
            if isinstance(action, AskQuizAction):
                next_index = index + 1
                if next_index >= len(scene.actions):
                    raise ValueError("ASK_QUIZ must be followed by GIVE_FEEDBACK")
                next_action = scene.actions[next_index]
                if not isinstance(next_action, GiveFeedbackAction):
                    raise ValueError("ASK_QUIZ must be followed by GIVE_FEEDBACK")
                if next_action.payload.quiz_action_id != action.id:
                    raise ValueError("GIVE_FEEDBACK must reference the previous ASK_QUIZ")
            if isinstance(action, StudentQuestionAction):
                next_index = index + 1
                if next_index >= len(scene.actions) or not isinstance(
                    scene.actions[next_index], TeacherQAResponseAction
                ):
                    raise ValueError(
                        "STUDENT_QUESTION must be followed by TEACHER_QA_RESPONSE"
                    )
                if scene.actions[next_index].payload.qa_id != action.payload.qa_id:
                    raise ValueError("Prepared QA action pair must reference the same qa_id")
