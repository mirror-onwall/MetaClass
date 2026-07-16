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
    ClassroomPlanGenerationMeta,
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
from metaclass.modules.presentation.service import PresentationService


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
        presentations: PresentationService | None = None,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.teacher = teacher or TeacherAgent()
        self.evaluator = evaluator or EvaluatorAgent()
        self.student_roster = student_roster or StudentRosterAgent()
        self.controller = controller or ClassroomController()
        self.planner = planner or ClassroomPlanGenerator(fallback_teacher=self.teacher)
        self.presentations = presentations

    def create_plan(
        self, content_id: str, presentation_plan_id: str | None = None
    ) -> ClassroomPlan:
        content = self.contents.get(content_id)
        presentation_plan = None
        if presentation_plan_id:
            if not self.presentations:
                raise HTTPException(409, "Presentation service is unavailable")
            presentation_plan = self.presentations.get_plan(presentation_plan_id)
            if presentation_plan.content_id != content_id:
                raise HTTPException(409, "PresentationPlan does not belong to LearningContent")
        plan, meta = self.planner.generate_with_meta(content, presentation_plan)
        self.repository.save_plan(plan)
        self.repository.save_plan_generation_meta(meta)
        return plan

    def create_plan_job(
        self, content_id: str, presentation_plan_id: str | None = None
    ) -> ClassroomPlanJob:
        self.contents.get(content_id)
        if presentation_plan_id:
            if not self.presentations:
                raise HTTPException(409, "Presentation service is unavailable")
            self.presentations.get_plan(presentation_plan_id)
        job = ClassroomPlanJob(
            id=f"plan_job_{uuid4().hex[:12]}",
            content_id=content_id,
            presentation_plan_id=presentation_plan_id,
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

            plan = self.create_plan(job.content_id, job.presentation_plan_id)

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

    def get_plan_generation_meta(self, plan_id: str) -> ClassroomPlanGenerationMeta:
        self.get_plan(plan_id)
        meta = self.repository.get_plan_generation_meta(plan_id)
        if not meta:
            raise HTTPException(404, "Classroom plan generation meta not found")
        return meta

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
        state = self._build_state(session)
        teacher_answer = self.teacher.answer_question(plan, session, question, state)
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

        planned_probe_follow_up = self._continue_after_planned_probe(session)
        if planned_probe_follow_up:
            return planned_probe_follow_up

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

    def _continue_after_planned_probe(self, session: ClassroomSession) -> AutoClassroomStep | None:
        if not session.events or session.events[-1].type != "ACTION_EXECUTED":
            return None
        if session.events[-1].payload.action_type != ActionType.PROBE:
            return None

        state = self._build_state(session)
        student = self._select_dialog_student(state)
        if not student:
            return None
        probe_question = self._executed_probe_question(session)
        student_turn = self.student_roster.generate_turn(
            student,
            state,
            (
                f"老师刚刚问：{probe_question}。这是回答回合，请直接回答老师的问题，"
                "先给出自己的判断，再用一句理由或很短的例子说明。可以不完全确定，"
                "但不要反问老师、不要提出新的问题，也不要转移话题。"
            ),
        )
        student_turn.intent = "student_answer_planned_probe"
        directed = DirectedAgentTurn(
            decision=ControllerDecision(
                next_role="student",
                next_agent_id=student.id,
                reason="课堂计划安排了开放互动点，学生智能体需要回应老师追问。",
                prompt="请学生回应老师的计划内开放问题。",
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

    def _executed_probe_question(self, session: ClassroomSession) -> str:
        event = session.events[-1]
        action_id = event.payload.action_id
        plan = self.get_plan(session.plan_id)
        for scene in plan.scenes:
            for action in scene.actions:
                if action.id == action_id and action.type == "PROBE":
                    return action.payload.question
        return "请说说你对刚才知识点的理解。"

    @staticmethod
    def _last_agent_turn(session: ClassroomSession) -> AgentTurn | None:
        if not session.events or session.events[-1].type != "AGENT_TURN":
            return None
        return session.events[-1].payload.turn

    @staticmethod
    def _select_dialog_student(state: ClassroomState):
        preferred_types = [
            StudentAgentType.DEEP_THINKER,
            StudentAgentType.CONCEPT_CONFUSED,
            StudentAgentType.FOUNDATION_WEAK,
            StudentAgentType.RESEARCHER,
            StudentAgentType.PRACTICAL_APPLIER,
            StudentAgentType.ATMOSPHERE_REGULATOR,
            StudentAgentType.NOTE_TAKER,
            StudentAgentType.SILENT_OBSERVER,
        ]
        recent_student_ids = ClassroomService._recent_student_speaker_ids(state)
        candidates = [student for student in state.students if student.id not in recent_student_ids]
        if not candidates:
            candidates = state.students

        fresh_candidates = [student for student in candidates if not student.last_intent]
        if fresh_candidates:
            candidates = fresh_candidates

        for preferred_type in preferred_types:
            selected = next(
                (student for student in candidates if student.agent_type == preferred_type),
                None,
            )
            if selected:
                return selected
        return candidates[0] if candidates else None

    @staticmethod
    def _recent_student_speaker_ids(state: ClassroomState, limit: int = 1) -> set[str]:
        recent_ids: list[str] = []
        for event in reversed(state.recent_events):
            if event.type != "AGENT_TURN":
                continue
            turn = event.payload.turn
            if turn.role != "student":
                continue
            recent_ids.append(turn.agent_id)
            if len(recent_ids) >= limit:
                break
        return set(recent_ids)

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
