from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.agent_schemas import (
    AgentTurn,
    ControllerDecision,
    DirectedAgentTurn,
    StudentAgentType,
)
from metaclass.modules.classroom.agents import EvaluatorAgent, StudentRosterAgent, TeacherAgent
from metaclass.modules.classroom.controller import ClassroomController
from metaclass.modules.classroom.planner import ClassroomPlanGenerator
from metaclass.modules.classroom.schemas import (
    AgentTurnEvent,
    AgentTurnPayload,
    ActionExecutedEvent,
    ActionExecutedPayload,
    ActionType,
    AskQuizAction,
    AutoClassroomStep,
    ClassroomPlan,
    ClassroomPlanJob,
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
        planner: ClassroomPlanGenerator | None = None,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.teacher = teacher or TeacherAgent()
        self.evaluator = evaluator or EvaluatorAgent()
        self.student_roster = student_roster or StudentRosterAgent()
        self.controller = controller or ClassroomController()
        self.planner = planner or ClassroomPlanGenerator(fallback_teacher=self.teacher)

    def create_plan(self, content_id: str) -> ClassroomPlan:
        content = self.contents.get(content_id)
        plan = self.planner.generate(content)
        self.repository.save_plan(plan)
        return plan

    def create_plan_job(self, content_id: str) -> ClassroomPlanJob:
        self.contents.get(content_id)
        job = ClassroomPlanJob(
            id=f"plan_job_{uuid4().hex[:12]}",
            content_id=content_id,
            status="queued",
            step="queued",
            progress=0,
            message="Classroom plan generation queued",
        )
        self._save_plan_job(job)
        return job

    def get_plan_job(self, job_id: str) -> ClassroomPlanJob:
        job = self.repository.get_plan_job(job_id)
        if not job:
            raise HTTPException(404, "Classroom plan job not found")
        return job

    def run_plan_job(self, job_id: str) -> None:
        job = self.get_plan_job(job_id)
        try:
            job.status = "running"
            job.step = "planning"
            job.progress = 20
            job.message = "Generating classroom plan"
            self._save_plan_job(job)

            plan = self.create_plan(job.content_id)

            job.status = "running"
            job.step = "persisting"
            job.progress = 90
            job.message = "Persisting classroom plan"
            job.plan_id = plan.id
            self._save_plan_job(job)

            job.status = "succeeded"
            job.step = "completed"
            job.progress = 100
            job.message = "Classroom plan generation completed"
            job.plan_id = plan.id
            self._save_plan_job(job)
        except Exception as exc:
            job.status = "failed"
            job.step = "failed"
            job.progress = 100
            job.message = "Classroom plan generation failed"
            job.error = str(exc)
            self._save_plan_job(job)

    def get_plan(self, plan_id: str) -> ClassroomPlan:
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Classroom plan not found")
        return plan

    def create_session(
        self,
        plan_id: str,
        mode: LearningMode = LearningMode.LECTURE,
        student_agent_types: list[StudentAgentType] | None = None,
    ) -> ClassroomSession:
        self.get_plan(plan_id)
        session = ClassroomSession(
            id=f"session_{uuid4().hex[:12]}",
            plan_id=plan_id,
            mode=mode,
            student_states=self.student_roster.create_states(student_agent_types),
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

    def auto_step(self, session_id: str) -> AutoClassroomStep:
        """Advance one natural classroom beat.

        This is intentionally one step, not an endless background loop. The web
        client can poll it for autoplay today, and the same method can power an
        SSE/WebSocket stream later.
        """
        session = self.get_session(session_id)
        if session.status == "completed":
            return AutoClassroomStep(status="completed", session=session)

        if session.waiting_for:
            return AutoClassroomStep(
                status="waiting",
                feedback="等待用户完成当前小测。",
                session=session,
            )

        if session.mode == LearningMode.INTERACTIVE:
            directed_step = self._maybe_generate_auto_dialog_turn(session)
            if directed_step:
                return directed_step

        result = self.next(session.id)
        return AutoClassroomStep(
            status=result.status if result.status != "evaluated" else "quiz_answered",
            action=result.action,
            feedback=result.feedback,
            correct=result.correct,
            source_refs=result.source_refs,
            session=result.session,
        )

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
        return self._generate_next_agent_turn_for_session(session)

    def _generate_next_agent_turn_for_session(
        self, session: ClassroomSession, decision=None
    ) -> DirectedAgentTurn:
        state = self._build_state(session)
        decision = decision or self.controller.decide(state)
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

    def _maybe_generate_auto_dialog_turn(
        self, session: ClassroomSession
    ) -> AutoClassroomStep | None:
        follow_up = self._continue_pending_dialog(session)
        if follow_up:
            return follow_up

        if session.events and session.events[-1].type == "AGENT_TURN":
            return None

        after_explain = self._maybe_start_dialog_after_explain(session)
        if after_explain:
            return after_explain

        state = self._build_state(session)
        if state.current_action_type in {
            ActionType.SHOW_PAGE,
            ActionType.EXPLAIN,
            ActionType.PROBE,
            ActionType.REVIEW,
            ActionType.REMEDIATE,
            ActionType.END,
        }:
            return None
        if state.current_action_type == ActionType.ASK_QUIZ:
            if (
                session.events
                and session.events[-1].type == "ACTION_EXECUTED"
                and session.events[-1].payload.action_type == ActionType.PROBE
            ):
                return None
            teacher_turn = self.teacher.generate_turn(
                state,
                (
                    "正式小测开始前，先向一位同学提出一个开放式短问题，"
                    "帮助大家说出自己的理解。不要直接给出小测答案。"
                ),
            )
            teacher_turn.actions = ["PROBE"]
            teacher_turn.intent = "teacher_probe_before_quiz"
            directed = DirectedAgentTurn(
                decision=ControllerDecision(
                    next_role="teacher",
                    next_agent_id="teacher",
                    reason="进入正式小测前，老师先发起一个开放提问。",
                    prompt="请老师先提出一个开放式理解检查问题。",
                ),
                turns=[teacher_turn],
            )
            self._record_agent_turns(session, directed.turns)
            return AutoClassroomStep(
                status="agent_turn",
                directed_turn=directed,
                feedback=teacher_turn.speech,
                session=self.get_session(session.id),
            )

        state = self._build_state(session)
        decision = self.controller.decide(state)
        if decision.next_role == "end":
            session.status = "completed"
            self._save_session(session)
            return AutoClassroomStep(
                status="completed",
                directed_turn=DirectedAgentTurn(decision=decision, turns=[]),
                session=session,
            )
        if decision.next_role not in {"student", "evaluator"}:
            return None

        directed = self._generate_next_agent_turn_for_session(session, decision)
        return AutoClassroomStep(
            status="agent_turn",
            directed_turn=directed,
            feedback=directed.turns[0].speech if directed.turns else decision.reason,
            session=self.get_session(session.id),
        )

    def _continue_pending_dialog(self, session: ClassroomSession) -> AutoClassroomStep | None:
        last_turn = self._last_agent_turn(session)
        if not last_turn:
            return None

        state = self._build_state(session)
        if last_turn.role == "teacher" and "PROBE" in last_turn.actions:
            student = self._select_dialog_student(state)
            if not student:
                return None
            student_turn = self.student_roster.generate_turn(
                student,
                state,
                f"老师刚刚问：{last_turn.speech}。请你像课堂学生一样简短回答，可以有一点不确定。",
            )
            student_turn.intent = "student_answer_teacher_probe"
            directed = DirectedAgentTurn(
                decision=ControllerDecision(
                    next_role="student",
                    next_agent_id=student.id,
                    reason="老师发起了开放提问，需要学生智能体先回应。",
                    prompt="请学生回答老师的开放问题。",
                ),
                turns=[student_turn],
            )
            self._record_agent_turns(session, directed.turns)
            return AutoClassroomStep(
                status="agent_turn",
                directed_turn=directed,
                feedback=student_turn.speech,
                session=self.get_session(session.id),
            )

        if last_turn.role == "student":
            teacher_turn = self.teacher.generate_turn(
                state,
                (
                    f"学生刚刚说：{last_turn.speech}。请老师先自然回应这位学生，"
                    "如果是问题就回答，如果是回答就做简短反馈，然后把课堂拉回主线。"
                ),
            )
            teacher_turn.actions = []
            teacher_turn.intent = "teacher_reply_to_student"
            directed = DirectedAgentTurn(
                decision=ControllerDecision(
                    next_role="teacher",
                    next_agent_id="teacher",
                    reason="学生刚刚发言，老师需要回应后再继续课程。",
                    prompt="请老师回应学生发言并回到主线。",
                ),
                turns=[teacher_turn],
            )
            self._record_agent_turns(session, directed.turns)
            return AutoClassroomStep(
                status="agent_turn",
                directed_turn=directed,
                feedback=teacher_turn.speech,
                session=self.get_session(session.id),
            )

        return None

    def _maybe_start_dialog_after_explain(
        self, session: ClassroomSession
    ) -> AutoClassroomStep | None:
        if not session.events or session.events[-1].type != "ACTION_EXECUTED":
            return None
        if session.events[-1].payload.action_type != ActionType.EXPLAIN:
            return None

        state = self._build_state(session)
        student = self._select_dialog_student(state)
        if not student:
            return None
        student_turn = self.student_roster.generate_turn(
            student,
            state,
            (
                "老师刚讲完当前 PPT 页。请你像课堂学生一样自然插一句："
                "可以提一个短问题、说一个困惑，或用一句话复述重点。不要替用户回答正式小测。"
            ),
        )
        student_turn.intent = "student_comment_after_explain"
        directed = DirectedAgentTurn(
            decision=ControllerDecision(
                next_role="student",
                next_agent_id=student.id,
                reason="互动课堂中，老师讲完当前页后安排学生智能体自然插话。",
                prompt="请学生围绕刚讲完的 PPT 页做一次短互动。",
            ),
            turns=[student_turn],
        )
        self._record_agent_turns(session, directed.turns)
        return AutoClassroomStep(
            status="agent_turn",
            directed_turn=directed,
            feedback=student_turn.speech,
            session=self.get_session(session.id),
        )

    @staticmethod
    def _last_agent_turn(session: ClassroomSession) -> AgentTurn | None:
        if not session.events or session.events[-1].type != "AGENT_TURN":
            return None
        return session.events[-1].payload.turn

    @staticmethod
    def _select_dialog_student(state: ClassroomState):
        return next(
            (student for student in state.students if student.id == "student_agent_002"),
            state.students[0] if state.students else None,
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

    def _save_plan_job(self, job: ClassroomPlanJob) -> None:
        job.updated_at = utc_now()
        self.repository.save_plan_job(job)

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
