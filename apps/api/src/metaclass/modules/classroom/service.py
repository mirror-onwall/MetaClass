from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.assessment.schemas import Evidence
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.schemas import (
    ActionExecutedEvent,
    ActionExecutedPayload,
    ActionType,
    AskQuizAction,
    AskQuizPayload,
    ClassroomPlan,
    ClassroomScene,
    ClassroomSession,
    ControllerResult,
    EndAction,
    EndPayload,
    ExplainAction,
    ExplainPayload,
    GiveFeedbackAction,
    GiveFeedbackPayload,
    QuizEvaluatedEvent,
    QuizEvaluatedPayload,
    ShowPageAction,
    ShowPagePayload,
    TeacherAnswerEvent,
    TeacherAnswerPayload,
    UserQuestionEvent,
    UserQuestionPayload,
)
from metaclass.modules.classroom.repository import ClassroomRepository
from metaclass.modules.content.service import ContentService


class ClassroomService:
    def __init__(self, repository: ClassroomRepository, contents: ContentService) -> None:
        self.repository = repository
        self.contents = contents

    def create_plan(self, content_id: str) -> ClassroomPlan:
        content = self.contents.get(content_id)
        scenes = []
        for index, section in enumerate(content.sections, start=1):
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
            actions.append(
                EndAction(
                    id=f"{prefix}_end",
                    type="END",
                    actor="system",
                    payload=EndPayload(summary=f"本页要点：{section.summary}"),
                )
            )
            scenes.append(ClassroomScene(id=prefix, title=section.title, actions=actions))
        plan = ClassroomPlan(id=f"plan_{uuid4().hex[:12]}", content_id=content_id, scenes=scenes)
        self.repository.save_plan(plan)
        return plan

    def get_plan(self, plan_id: str) -> ClassroomPlan:
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Classroom plan not found")
        return plan

    def create_session(self, plan_id: str) -> ClassroomSession:
        self.get_plan(plan_id)
        session = ClassroomSession(id=f"session_{uuid4().hex[:12]}", plan_id=plan_id)
        self._save_session(session)
        return session

    def get_session(self, session_id: str) -> ClassroomSession:
        session = self.repository.get_session(session_id)
        if not session:
            raise HTTPException(404, "Classroom session not found")
        return session

    def next(self, session_id: str) -> ControllerResult:
        session = self.get_session(session_id)
        if session.status == "completed":
            return ControllerResult(status="completed", session=session)
        if session.waiting_for:
            return ControllerResult(status="waiting", session=session)
        plan = self.get_plan(session.plan_id)
        self._normalize_cursor(session, plan)
        if session.status == "completed":
            self._save_session(session)
            return ControllerResult(status="completed", session=session)

        action = plan.scenes[session.scene_index].actions[session.action_index]
        if isinstance(action, GiveFeedbackAction):
            raise HTTPException(409, "Submit the pending quiz answer before feedback")
        session.events.append(
            ActionExecutedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id=action.id, action_type=ActionType(action.type)
                ),
            )
        )
        session.action_index += 1
        if isinstance(action, AskQuizAction):
            session.waiting_for = "quiz_answer"
        self._save_session(session)
        return ControllerResult(status="action", action=action, session=session)

    def answer(self, session_id: str, selected_index: int) -> ControllerResult:
        session = self.get_session(session_id)
        if session.waiting_for != "quiz_answer":
            raise HTTPException(409, "Session is not waiting for a quiz answer")
        plan = self.get_plan(session.plan_id)
        scene = plan.scenes[session.scene_index]
        quiz_action = scene.actions[session.action_index - 1]
        feedback_action = scene.actions[session.action_index]
        if not isinstance(quiz_action, AskQuizAction) or not isinstance(
            feedback_action, GiveFeedbackAction
        ):
            raise HTTPException(500, "Invalid classroom plan sequence")

        quiz = quiz_action.payload.quiz
        if selected_index >= len(quiz.options):
            raise HTTPException(422, "Selected option does not exist")
        correct = selected_index == quiz.correct_index
        evidence = Evidence(
            id=f"evidence_{uuid4().hex[:12]}",
            session_id=session.id,
            action_id=quiz_action.id,
            type="QUIZ",
            knowledge_point=quiz.knowledge_point,
            score=1.0 if correct else 0.0,
            weight=1.0,
            confidence=1.0,
            note=f"selected={selected_index}, correct={quiz.correct_index}",
        )
        session.evidence.append(evidence)
        session.mastery = estimate_mastery(session.id, session.evidence)
        session.events.append(
            QuizEvaluatedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="QUIZ_EVALUATED",
                payload=QuizEvaluatedPayload(
                    action_id=quiz_action.id,
                    feedback_action_id=feedback_action.id,
                    correct=correct,
                    selected_index=selected_index,
                ),
            )
        )
        session.action_index += 1
        session.waiting_for = None
        self._save_session(session)
        feedback = (
            "回答正确。"
            if correct
            else f"回答不正确，正确答案是：{quiz.options[quiz.correct_index]}"
        )
        return ControllerResult(
            status="evaluated", feedback=feedback, correct=correct, session=session
        )

    def answer_question(self, session_id: str, question: str) -> ControllerResult:
        session = self.get_session(session_id)
        plan = self.get_plan(session.plan_id)
        scene_index = min(session.scene_index, max(len(plan.scenes) - 1, 0))
        explain_action = next(
            action
            for action in plan.scenes[scene_index].actions
            if isinstance(action, ExplainAction)
        )
        answer = f"根据当前材料：{explain_action.payload.text}"
        session.events.extend(
            [
                UserQuestionEvent(
                    id=f"event_{uuid4().hex[:12]}",
                    session_id=session.id,
                    type="USER_QUESTION",
                    payload=UserQuestionPayload(question=question),
                ),
                TeacherAnswerEvent(
                    id=f"event_{uuid4().hex[:12]}",
                    session_id=session.id,
                    type="TEACHER_ANSWER",
                    payload=TeacherAnswerPayload(
                        answer=answer, source_refs=explain_action.payload.source_refs
                    ),
                ),
            ]
        )
        self._save_session(session)
        return ControllerResult(
            status="answered",
            feedback=answer,
            source_refs=explain_action.payload.source_refs,
            session=session,
        )

    @staticmethod
    def _normalize_cursor(session: ClassroomSession, plan: ClassroomPlan) -> None:
        while session.scene_index < len(plan.scenes):
            if session.action_index < len(plan.scenes[session.scene_index].actions):
                return
            session.scene_index += 1
            session.action_index = 0
        session.status = "completed"

    def _save_session(self, session: ClassroomSession) -> None:
        session.updated_at = utc_now()
        self.repository.save_session(session)
