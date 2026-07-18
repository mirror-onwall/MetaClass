from metaclass.infrastructure.providers.llm import LLMProvider
from metaclass.modules.classroom.agent_schemas import AgentTurn
from metaclass.modules.classroom.agents.prompts import (
    build_teacher_answer_messages,
    build_teacher_messages,
    parse_agent_turn_json,
    parse_teacher_answer_json,
)
from metaclass.modules.classroom.schemas import (
    AskQuizAction,
    AskQuizPayload,
    ClassroomPlan,
    ClassroomScene,
    ClassroomSession,
    ClassroomState,
    EndAction,
    EndPayload,
    ExplainAction,
    ExplainPayload,
    GiveFeedbackAction,
    GiveFeedbackPayload,
    ShowPageAction,
    ShowPagePayload,
    TeacherAnswerPayload,
)
from metaclass.modules.content.schemas import LearningSection


class TeacherAgent:
    """Builds teacher-facing classroom actions and answers free questions."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    def build_scene(self, index: int, section: LearningSection) -> ClassroomScene:
        prefix = f"scene_{index:03d}"
        actions = [
            ShowPageAction(
                id=f"{prefix}_show",
                type="SHOW_PAGE",
                actor="system",
                payload=ShowPagePayload(source_ref=section.source_refs[0]),
            ),
            ExplainAction(
                id=f"{prefix}_explain",
                type="EXPLAIN",
                actor="teacher",
                payload=ExplainPayload(
                    text=f"本节学习{section.title}。{section.summary}",
                    source_refs=section.source_refs,
                ),
            ),
        ]
        if section.quiz_items:
            quiz_action_id = f"{prefix}_quiz"
            actions.extend(
                [
                    AskQuizAction(
                        id=quiz_action_id,
                        type="ASK_QUIZ",
                        actor="teacher",
                        payload=AskQuizPayload(quiz=section.quiz_items[0]),
                    ),
                    GiveFeedbackAction(
                        id=f"{prefix}_feedback",
                        type="GIVE_FEEDBACK",
                        actor="evaluator",
                        payload=GiveFeedbackPayload(quiz_action_id=quiz_action_id),
                    ),
                ]
            )
        actions.extend(
            [
                EndAction(
                    id=f"{prefix}_end",
                    type="END",
                    actor="system",
                    payload=EndPayload(summary=section.summary),
                ),
            ]
        )
        return ClassroomScene(id=prefix, title=section.title, actions=actions)

    def answer_question(
        self,
        plan: ClassroomPlan,
        session: ClassroomSession,
        question: str,
        classroom_state: ClassroomState | None = None,
        retrieved_question: str | None = None,
        retrieved_answer: str | None = None,
        retrieved_source_refs: list | None = None,
    ) -> TeacherAnswerPayload:
        scene_index = min(session.scene_index, max(len(plan.scenes) - 1, 0))
        explain_action = next(
            action
            for action in plan.scenes[scene_index].actions
            if isinstance(action, ExplainAction)
        )
        if self.llm and classroom_state:
            try:
                raw = self.llm.complete_json(
                    build_teacher_answer_messages(
                        classroom_state=classroom_state,
                        question=question,
                        current_explanation=explain_action.payload.text,
                        retrieved_question=retrieved_question,
                        retrieved_answer=retrieved_answer,
                    ),
                    temperature=0.2,
                )
                answer = parse_teacher_answer_json(raw)
                return TeacherAnswerPayload(
                    answer=answer,
                    source_refs=(
                        retrieved_source_refs
                        if retrieved_answer and retrieved_source_refs
                        else explain_action.payload.source_refs
                    ),
                )
            except RuntimeError:
                pass

        if retrieved_answer:
            return TeacherAnswerPayload(
                answer=f"关于你问的“{question}”，可以这样理解：{retrieved_answer}",
                source_refs=retrieved_source_refs or explain_action.payload.source_refs,
            )
        return TeacherAnswerPayload(
            answer=(
                f"你问的是“{question}”。结合当前材料，这里可以这样理解："
                f"{explain_action.payload.text}"
            ),
            source_refs=explain_action.payload.source_refs,
        )

    def generate_turn(self, classroom_state: ClassroomState, topic: str) -> AgentTurn:
        if not self.llm:
            if "学生刚刚说" in topic or "学生说" in topic:
                if any(keyword in topic for keyword in ["走神", "无聊", "上厕所"]):
                    return AgentTurn(
                        agent_id="teacher",
                        role="teacher",
                        speech="可以，真实课堂也会累。我们先用一句话把当前页抓住：看条件、看关系，再继续往下。",
                        actions=[],
                        intent="fake_teacher_handles_attention_drift",
                    )
                return AgentTurn(
                    agent_id="teacher",
                    role="teacher",
                    speech="这个问题提得很好。我们可以先抓住材料里的关键点，再用当前页的例子把它落回主线。",
                    actions=[],
                    intent="fake_teacher_reply_to_student",
                )
            if "开放式短问题" in topic:
                return AgentTurn(
                    agent_id="teacher",
                    role="teacher",
                    speech="在进入小测前，我先问一个问题：你能用自己的话说说这一页最核心的关系是什么吗？",
                    actions=["PROBE"],
                    intent="fake_teacher_probe_before_quiz",
                )
            return AgentTurn(
                agent_id="teacher",
                role="teacher",
                speech=f"我们先抓住重点：{topic}。我会用一个问题检查大家是否理解。",
                actions=["PROBE"],
                intent="fake_teacher_probe",
            )
        raw = self.llm.complete_json(
            build_teacher_messages(
                teacher_id="teacher",
                classroom_state=classroom_state,
                topic=topic,
                allowed_actions=["EXPLAIN", "PROBE", "SUMMARIZE", "REVIEW", "REMEDIATE"],
            )
        )
        return parse_agent_turn_json(raw, agent_id="teacher", role="teacher")
