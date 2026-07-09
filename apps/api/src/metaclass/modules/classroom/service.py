from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.agent_schemas import AgentTurn, DirectedAgentTurn
from metaclass.modules.classroom.agents import EvaluatorAgent, StudentRosterAgent, TeacherAgent
from metaclass.modules.classroom.controller import ClassroomController
from metaclass.modules.classroom.schemas import (
    AgentTurnEvent,
    AgentTurnPayload,
    ActionExecutedEvent,
    ActionExecutedPayload,
    ActionType,
    AskQuizAction,
    ClassroomPlan,
    ClassroomSession,
    ClassroomState,
    ControllerResult,
    GiveFeedbackAction,
    LearningMode,
    QuizEvaluatedEvent,
    QuizEvaluatedPayload,
    TeacherAnswerEvent,
    UserQuestionEvent,
    UserQuestionPayload,
)
from metaclass.modules.classroom.repository import ClassroomRepository
from metaclass.modules.content.service import ContentService


class ClassroomService:
    def __init__(
        self,
        repository: ClassroomRepository,
        contents: ContentService,
        teacher: TeacherAgent | None = None,
        evaluator: EvaluatorAgent | None = None,
        student_roster: StudentRosterAgent | None = None,
        controller: ClassroomController | None = None,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.teacher = teacher or TeacherAgent()
        self.evaluator = evaluator or EvaluatorAgent()
        self.student_roster = student_roster or StudentRosterAgent()
        self.controller = controller or ClassroomController()

    def create_plan(self, content_id: str) -> ClassroomPlan:
        content = self.contents.get(content_id)
        scenes = [
            self.teacher.build_scene(index, section)
            for index, section in enumerate(content.sections, start=1)
        ]
        plan = ClassroomPlan(id=f"plan_{uuid4().hex[:12]}", content_id=content_id, scenes=scenes)
        self.repository.save_plan(plan)
        return plan

    def get_plan(self, plan_id: str) -> ClassroomPlan:
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Classroom plan not found")
        return plan

    def create_session(
        self,
        plan_id: str,
        mode: LearningMode = LearningMode.LECTURE,
    ) -> ClassroomSession:
        self.get_plan(plan_id)
        session = ClassroomSession(
            id=f"session_{uuid4().hex[:12]}",
            plan_id=plan_id,
            mode=mode,
            student_states=self.student_roster.create_default_states(),
        )
        self._save_session(session)
        return session

    def get_session(self, session_id: str) -> ClassroomSession:
        session = self.repository.get_session(session_id)
        if not session:
            raise HTTPException(404, "Classroom session not found")
        return session

    def get_state(self, session_id: str) -> ClassroomState:
        session = self.get_session(session_id)
        return self._build_state(session)

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
        evaluation = self.evaluator.evaluate_quiz(
            session_id=session.id,
            quiz_action=quiz_action,
            selected_index=selected_index,
            evidence_id=f"evidence_{uuid4().hex[:12]}",
        )
        session.evidence.append(evaluation.evidence)
        session.mastery = estimate_mastery(session.id, session.evidence)
        session.events.append(
            QuizEvaluatedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="QUIZ_EVALUATED",
                payload=QuizEvaluatedPayload(
                    action_id=quiz_action.id,
                    feedback_action_id=feedback_action.id,
                    correct=evaluation.correct,
                    selected_index=selected_index,
                ),
            )
        )
        session.action_index += 1
        session.waiting_for = None
        self._save_session(session)
        return ControllerResult(
            status="evaluated",
            feedback=evaluation.feedback,
            correct=evaluation.correct,
            session=session,
        )

    def answer_question(self, session_id: str, question: str) -> ControllerResult:
        session = self.get_session(session_id)
        plan = self.get_plan(session.plan_id)
        teacher_answer = self.teacher.answer_question(plan, session, question)
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
                    payload=teacher_answer,
                ),
            ]
        )
        self._save_session(session)
        return ControllerResult(
            status="answered",
            feedback=teacher_answer.answer,
            source_refs=teacher_answer.source_refs,
            session=session,
        )

    def generate_teacher_turn(self, session_id: str, prompt: str) -> AgentTurn:
        state = self.get_state(session_id)
        return self.teacher.generate_turn(state, prompt)

    def generate_student_turns(self, session_id: str, prompt: str) -> list[AgentTurn]:
        state = self.get_state(session_id)
        return [
            self.student_roster.generate_turn(student_state, state, prompt)
            for student_state in state.students
        ]

    def generate_next_agent_turn(self, session_id: str) -> DirectedAgentTurn:
        session = self.get_session(session_id)
        state = self._build_state(session)
        decision = self.controller.decide(state)
        if decision.next_role == "end":
            return DirectedAgentTurn(decision=decision, turns=[])
        if decision.next_role == "teacher":
            result = DirectedAgentTurn(
                decision=decision, turns=[self.teacher.generate_turn(state, decision.prompt)]
            )
            self._record_agent_turns(session, result.turns)
            return result
        if decision.next_role == "student":
            selected = next(
                (
                    student_state
                    for student_state in state.students
                    if student_state.id == decision.next_agent_id
                ),
                state.students[0] if state.students else None,
            )
            if not selected:
                return DirectedAgentTurn(decision=decision, turns=[])
            result = DirectedAgentTurn(
                decision=decision,
                turns=[self.student_roster.generate_turn(selected, state, decision.prompt)],
            )
            self._record_agent_turns(session, result.turns)
            return result
        result = DirectedAgentTurn(
            decision=decision,
            turns=[
                AgentTurn(
                    agent_id="evaluator",
                    role="evaluator",
                    speech="我会根据小测和互动证据更新掌握度判断。",
                    actions=[],
                    intent="evaluation_ready",
                )
            ],
        )
        self._record_agent_turns(session, result.turns)
        return result

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

    def _build_state(self, session: ClassroomSession) -> ClassroomState:
        plan = self.get_plan(session.plan_id)
        self._normalize_cursor(session, plan)

        current_scene = None
        current_action = None
        if session.status != "completed" and session.scene_index < len(plan.scenes):
            current_scene = plan.scenes[session.scene_index]
            if session.action_index < len(current_scene.actions):
                current_action = current_scene.actions[session.action_index]

        return ClassroomState(
            session_id=session.id,
            plan_id=session.plan_id,
            mode=session.mode,
            status=session.status,
            scene_index=session.scene_index,
            action_index=session.action_index,
            current_scene_id=current_scene.id if current_scene else None,
            current_scene_title=current_scene.title if current_scene else None,
            current_action_id=current_action.id if current_action else None,
            current_action_type=ActionType(current_action.type) if current_action else None,
            waiting_for=session.waiting_for,
            students=session.student_states,
            mastery=session.mastery,
            recent_events=session.events[-10:],
        )

    def _record_agent_turns(self, session: ClassroomSession, turns: list[AgentTurn]) -> None:
        for turn in turns:
            session.events.append(
                AgentTurnEvent(
                    id=f"event_{uuid4().hex[:12]}",
                    session_id=session.id,
                    type="AGENT_TURN",
                    payload=AgentTurnPayload(turn=turn),
                )
            )
            for student in session.student_states:
                if student.id == turn.agent_id:
                    student.last_intent = turn.intent
                    student.updated_at = utc_now()
        self._save_session(session)
