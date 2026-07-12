from __future__ import annotations

import json
from uuid import uuid4

from pydantic import Field, ValidationError

from metaclass.core.schemas import SchemaModel
from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.classroom.agents import TeacherAgent
from metaclass.modules.classroom.schemas import (
    AskQuizAction,
    ClassroomPlan,
    ClassroomScene,
    ExplainAction,
    GiveFeedbackAction,
    ProbeAction,
    ProbePayload,
    ReviewAction,
    ReviewPayload,
)
from metaclass.modules.content.schemas import LearningContent


class ScenePlanBlueprint(SchemaModel):
    section_id: str = Field(min_length=1)
    include_probe: bool = False
    probe_question: str | None = None
    include_quiz: bool = True
    include_review: bool = False
    teaching_note: str | None = None


class ClassroomPlanBlueprint(SchemaModel):
    scenes: list[ScenePlanBlueprint] = Field(default_factory=list)


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
        fallback = self._fallback_plan(content)
        if not self.llm:
            return fallback
        try:
            raw = self.llm.complete_json(self._build_messages(content), temperature=0.2)
            payload = json.loads(raw)
            blueprint = ClassroomPlanBlueprint.model_validate(payload)
            plan = self._hydrate_blueprint(content, blueprint)
            self._validate_runtime_sequence(plan)
            return plan
        except (TimeoutError, json.JSONDecodeError, ValidationError, RuntimeError, ValueError):
            return fallback

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

# 规划原则
- 每个输入 section 都应该返回一个 scene 蓝图。
- 如果 section 没有 quiz，include_quiz 必须是 false。
- 正式小测只给真实用户做；学生 agent 不会替用户答题。
- include_probe 不要每页都 true，只在适合课堂讨论或关键概念检查时打开。
- include_review 只在阶段性总结、目录页、收束页或知识点密集页打开。
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
                    "has_quiz": bool(section.quiz_items),
                    "quiz_questions": [
                        quiz.question[:160] for quiz in section.quiz_items[:2]
                    ],
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
