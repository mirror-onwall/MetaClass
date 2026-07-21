import random
from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.assessment.service import estimate_mastery
from metaclass.modules.classroom.agent_schemas import (
    AgentTurn,
    ControllerDecision,
    DirectedAgentTurn,
    StudentAgentType,
    student_name_for_type,
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
    ClassroomNavigationResult,
    ClassroomSession,
    ClassroomState,
    ControllerResult,
    GiveFeedbackAction,
    LearningMode,
    ProbeAction,
    QAInteractionExecutedEvent,
    QAInteractionExecutedPayload,
    QuizEvaluatedEvent,
    QuizEvaluatedPayload,
    TeacherAnswerEvent,
    StudentQuestionAction,
    TeacherQAResponseAction,
    UserQuestionEvent,
    UserQuestionPayload,
)
from metaclass.modules.classroom.repository import ClassroomRepository
from metaclass.modules.content.service import ContentService
from metaclass.modules.presentation.service import PresentationService
from metaclass.modules.question_bank.service import QuestionBankService


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
        question_banks: QuestionBankService | None = None,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.teacher = teacher or TeacherAgent()
        self.evaluator = evaluator or EvaluatorAgent()
        self.student_roster = student_roster or StudentRosterAgent()
        self.controller = controller or ClassroomController()
        self.planner = planner or ClassroomPlanGenerator(fallback_teacher=self.teacher)
        self.presentations = presentations
        self.question_banks = question_banks

    def create_plan(
        self, content_id: str, presentation_plan_id: str | None = None
    ) -> ClassroomPlan:
        content = self.contents.get(content_id)
        presentation_plan = None
        qa_items = []
        if presentation_plan_id:
            if not self.presentations:
                raise HTTPException(409, "Presentation service is unavailable")
            presentation_plan = self.presentations.get_plan(presentation_plan_id)
            if presentation_plan.content_id != content_id:
                raise HTTPException(409, "PresentationPlan does not belong to LearningContent")
            if self.question_banks:
                qa_items = self.question_banks.get_for_plan(presentation_plan.id).items
        plan, meta = self.planner.generate_with_meta(content, presentation_plan, qa_items)
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
        plan = self.get_plan(plan_id)
        if mode == LearningMode.INTERACTIVE and self._plan_has_missing_qa(plan):
            presentation_plan_id = self.repository.get_presentation_plan_id_for_plan(plan.id)
            if presentation_plan_id and self.question_banks:
                self.question_banks.generate_for_plan(presentation_plan_id)
                plan = self.create_plan(plan.content_id, presentation_plan_id)
                plan_id = plan.id
        session = ClassroomSession(
            id=f"session_{uuid4().hex[:12]}",
            plan_id=plan_id,
            mode=mode,
            student_states=self.student_roster.create_states(student_agent_types),
        )
        self._save_session(session)
        return session

    def _plan_has_missing_qa(self, plan: ClassroomPlan) -> bool:
        if not self.question_banks:
            return False
        qa_ids = {
            action.payload.qa_id
            for scene in plan.scenes
            for action in scene.actions
            if isinstance(action, (StudentQuestionAction, TeacherQAResponseAction))
        }
        for qa_id in qa_ids:
            try:
                self.question_banks.get_item(qa_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return True
                raise
        return False

    def create_lecture_variant(self, plan_id: str) -> ClassroomPlan:
        """Create a deterministic lecture-only view of an existing classroom plan."""
        source = self.get_plan(plan_id)
        scenes = []
        for scene in source.scenes:
            actions = [
                action
                for action in scene.actions
                if action.type in {ActionType.SHOW_PAGE, ActionType.EXPLAIN, ActionType.END}
            ]
            if actions:
                scenes.append(scene.model_copy(update={"actions": actions}, deep=True))
        if not scenes:
            raise HTTPException(409, "Classroom plan has no lecture actions")
        plan = ClassroomPlan(
            id=f"plan_{uuid4().hex[:12]}",
            content_id=source.content_id,
            scenes=scenes,
            version=source.version,
        )
        self.repository.save_plan(plan)
        return plan

    def replay_session(self, session_id: str) -> ClassroomSession:
        source = self.get_session(session_id)
        agent_types = [state.agent_type for state in source.student_states]
        return self.create_session(source.plan_id, source.mode, agent_types)

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
        target_student = None
        if isinstance(action, ProbeAction) and session.mode == LearningMode.INTERACTIVE:
            target_student = self._select_dialog_student(
                self._build_state(session),
                question=action.payload.question,
                knowledge_point=action.payload.target_knowledge_point,
            )
            if target_student:
                student_name = student_name_for_type(target_student.agent_type)
                action = action.model_copy(
                    update={
                        "payload": action.payload.model_copy(
                            update={
                                "question": f"{student_name}同学，{action.payload.question}"
                            }
                        )
                    }
                )
        session.events.append(
            ActionExecutedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id=action.id,
                    action_type=ActionType(action.type),
                    target_agent_id=target_student.id if target_student else None,
                ),
            )
        )
        session.action_index += 1
        if isinstance(action, AskQuizAction):
            session.waiting_for = "quiz_answer"
        self._save_session(session)
        return ControllerResult(status="action", action=action, session=session)

    def navigate(self, session_id: str, direction: str) -> ClassroomNavigationResult:
        """Move between learner-facing narration beats.

        Lecture mode treats every EXPLAIN action as a segment. Interactive mode
        does the same, except a quiz still pending on the current scene is the
        next beat; probes and prepared agent Q&A are intentionally skipped.
        """
        if direction not in {"previous", "next"}:
            raise HTTPException(400, "Direction must be previous or next")
        session = self.get_session(session_id)
        plan = self.get_plan(session.plan_id)
        if direction == "next" and session.waiting_for == "quiz_answer":
            return ClassroomNavigationResult(
                status="waiting",
                feedback="请先完成当前小测。",
                session=session,
            )
        session.waiting_for = None

        positions = [
            (scene_index, action_index, action)
            for scene_index, scene in enumerate(plan.scenes)
            for action_index, action in enumerate(scene.actions)
        ]
        cursor_order = next(
            (
                index
                for index, (scene_index, action_index, _) in enumerate(positions)
                if (scene_index, action_index) >= (session.scene_index, session.action_index)
            ),
            len(positions),
        )

        target = None
        if direction == "previous":
            explains_before_cursor = [
                item for index, item in enumerate(positions[:cursor_order])
                if item[2].type == "EXPLAIN"
            ]
            # The last explanation is the currently displayed segment.
            if len(explains_before_cursor) >= 2:
                target = explains_before_cursor[-2]
            elif explains_before_cursor:
                target = explains_before_cursor[0]
        else:
            for scene_index, action_index, candidate in positions[cursor_order:]:
                if candidate.type == "EXPLAIN":
                    target = (scene_index, action_index, candidate)
                    break
                if (
                    session.mode == LearningMode.INTERACTIVE
                    and scene_index == session.scene_index
                    and candidate.type == "ASK_QUIZ"
                ):
                    target = (scene_index, action_index, candidate)
                    break

        if target is None:
            return ClassroomNavigationResult(
                status="completed" if direction == "next" else "action",
                feedback="已经是最后一段讲解。" if direction == "next" else "已经是第一段讲解。",
                session=session,
            )

        scene_index, action_index, action = target
        scene = plan.scenes[scene_index]
        page_action = next(
            (item for item in scene.actions[: action_index + 1] if item.type == "SHOW_PAGE"),
            None,
        )
        session.scene_index = scene_index
        session.action_index = action_index + 1
        session.status = "running"
        self._record_action_executed(session, action)
        if isinstance(action, AskQuizAction):
            session.waiting_for = "quiz_answer"
        self._save_session(session)
        return ClassroomNavigationResult(
            status="action",
            action=action,
            page_action=page_action,
            session=session,
        )

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

        scripted_qa_step = self._execute_scripted_qa_step(session)
        if scripted_qa_step:
            return scripted_qa_step

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

    def _execute_scripted_qa_step(
        self, session: ClassroomSession
    ) -> AutoClassroomStep | None:
        plan = self.get_plan(session.plan_id)
        self._normalize_cursor(session, plan)
        if session.status == "completed":
            return None
        scene = plan.scenes[session.scene_index]
        action = scene.actions[session.action_index]
        if not isinstance(action, StudentQuestionAction | TeacherQAResponseAction):
            return None

        if session.mode != LearningMode.INTERACTIVE or not self.question_banks:
            self._skip_scripted_qa_pair(session, scene)
            result = self.next(session.id)
            return AutoClassroomStep(
                status=result.status,
                action=result.action,
                feedback=result.feedback,
                session=result.session,
            )

        qa = self.question_banks.get_item(action.payload.qa_id)
        if isinstance(action, StudentQuestionAction):
            student = self._select_scripted_qa_student(session, action)
            if not student:
                self._skip_scripted_qa_pair(session, scene)
                result = self.next(session.id)
                return AutoClassroomStep(
                    status=result.status,
                    action=result.action,
                    feedback=result.feedback,
                    session=result.session,
                )
            self._record_action_executed(session, action)
            session.action_index += 1
            turn = AgentTurn(
                agent_id=student.id,
                role="student",
                speech=qa.student_question,
                actions=[],
                intent="scripted_qa_question",
            )
            self._record_agent_turns(session, [turn])
            directed = DirectedAgentTurn(
                decision=ControllerDecision(
                    next_role="student",
                    next_agent_id=student.id,
                    reason="课堂剧本在当前页面安排了预生成学生问题。",
                    prompt="请按预生成问题自然发言。",
                ),
                turns=[turn],
            )
            return AutoClassroomStep(
                status="agent_turn",
                directed_turn=directed,
                feedback=turn.speech,
                session=self.get_session(session.id),
            )

        previous_action = scene.actions[session.action_index - 1]
        if not isinstance(previous_action, StudentQuestionAction):
            raise HTTPException(500, "Invalid prepared QA action sequence")
        student_turn = self._last_student_turn_for_scripted_qa(session)
        if not student_turn:
            raise HTTPException(500, "Prepared QA response has no student question turn")
        self._record_action_executed(session, action)
        session.action_index += 1
        teacher_turn = AgentTurn(
            agent_id="teacher",
            role="teacher",
            speech=qa.teacher_answer,
            actions=[],
            intent="scripted_qa_answer",
        )
        self._record_agent_turns(session, [teacher_turn])
        session = self.get_session(session.id)
        session.events.append(
            QAInteractionExecutedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="QA_INTERACTION_EXECUTED",
                payload=QAInteractionExecutedPayload(
                    qa_id=qa.id,
                    slide_id=qa.slide_id,
                    student_action_id=previous_action.id,
                    teacher_action_id=action.id,
                    student_agent_id=student_turn.agent_id,
                    student_question=student_turn.speech,
                    teacher_answer=teacher_turn.speech,
                ),
            )
        )
        self._save_session(session)
        directed = DirectedAgentTurn(
            decision=ControllerDecision(
                next_role="teacher",
                next_agent_id="teacher",
                reason="老师按课堂剧本回答预生成学生问题。",
                prompt="请按预生成答案回应学生并继续课堂。",
            ),
            turns=[teacher_turn],
        )
        return AutoClassroomStep(
            status="agent_turn",
            directed_turn=directed,
            feedback=teacher_turn.speech,
            source_refs=qa.source_refs,
            session=self.get_session(session.id),
        )

    def _skip_scripted_qa_pair(self, session: ClassroomSession, scene) -> None:
        action = scene.actions[session.action_index]
        if isinstance(action, StudentQuestionAction):
            session.action_index += 2
        else:
            session.action_index += 1
        self._save_session(session)

    @staticmethod
    def _select_scripted_qa_student(
        session: ClassroomSession, action: StudentQuestionAction
    ):
        preferred_types = [
            action.payload.preferred_agent_type,
            *action.payload.fallback_agent_types,
        ]
        for agent_type in preferred_types:
            selected = next(
                (
                    student
                    for student in session.student_states
                    if student.agent_type == agent_type
                ),
                None,
            )
            if selected:
                return selected
        return session.student_states[0] if session.student_states else None

    @staticmethod
    def _last_student_turn_for_scripted_qa(
        session: ClassroomSession,
    ) -> AgentTurn | None:
        for event in reversed(session.events):
            if event.type == "AGENT_TURN" and event.payload.turn.role == "student":
                if event.payload.turn.intent == "scripted_qa_question":
                    return event.payload.turn
        return None

    @staticmethod
    def _record_action_executed(session: ClassroomSession, action) -> None:
        session.events.append(
            ActionExecutedEvent(
                id=f"event_{uuid4().hex[:12]}",
                session_id=session.id,
                type="ACTION_EXECUTED",
                payload=ActionExecutedPayload(
                    action_id=action.id,
                    action_type=ActionType(action.type),
                ),
            )
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
        self._ensure_user_question_allowed(session, plan)
        state = self._build_state(session)
        matched_qa = None
        if self.question_banks and self.presentations:
            try:
                presentation = self.presentations.get_plan_for_content(plan.content_id)
                matches = self.question_banks.search(presentation.id, question, limit=1)
                if matches and matches[0].score >= 3.0:
                    matched_qa = matches[0].item
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        teacher_answer = self.teacher.answer_question(
            plan,
            session,
            question,
            state,
            retrieved_question=matched_qa.canonical_question if matched_qa else None,
            retrieved_answer=matched_qa.canonical_answer if matched_qa else None,
            retrieved_source_refs=matched_qa.source_refs if matched_qa else None,
        )
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

    def _ensure_user_question_allowed(
        self, session: ClassroomSession, plan: ClassroomPlan
    ) -> None:
        """Reject user interruptions while a student/teacher QA exchange is in flight."""
        if session.status == "completed":
            return
        self._normalize_cursor(session, plan)
        if session.status == "completed":
            return
        current_action = plan.scenes[session.scene_index].actions[session.action_index]
        last_turn = self._last_agent_turn(session)
        if isinstance(current_action, TeacherQAResponseAction) or (
            last_turn and last_turn.role == "student"
        ):
            raise HTTPException(
                409,
                "User questions are unavailable while a student question is being answered",
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
            student_state = next(
                (student for student in state.students if student.id == last_turn.agent_id),
                None,
            )
            student_name = (
                student_name_for_type(student_state.agent_type)
                if student_state
                else "这位"
            )
            teacher_turn = self.teacher.generate_turn(
                state,
                (
                    f"刚刚发言的学生姓名是“{student_name}”，学生说：{last_turn.speech}。"
                    f"请老师先自然回应，并称呼“{student_name}同学”；"
                    "禁止用深度思考者、基础薄弱者、研究型同学等画像或智能体类型称呼学生。"
                    "如果是问题就回答，如果是回答就做简短反馈，然后把课堂拉回主线。"
                    "这是反馈收束回合，不得再提出新问题，不得邀请其他同学继续回答。"
                ),
            )
            teacher_turn.speech = self._remove_unplanned_follow_up_question(
                teacher_turn.speech
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

    @staticmethod
    def _remove_unplanned_follow_up_question(speech: str) -> str:
        """Keep a feedback turn from asking a question the state machine will not await."""
        follow_up_markers = (
            "那么接下来，我想请大家思考",
            "接下来，我想请大家思考",
            "那我们再进一步思考",
            "下面我想请大家思考",
            "我再问一个问题",
            "哪位同学愿意",
            "哪位同学可以",
            "哪位同学来",
        )
        cut_at = min(
            (speech.find(marker) for marker in follow_up_markers if marker in speech),
            default=-1,
        )
        if cut_at > 0:
            trimmed = speech[:cut_at].rstrip("，,；;：:。 ")
            return f"{trimmed}。"
        return speech

    def _continue_after_planned_probe(self, session: ClassroomSession) -> AutoClassroomStep | None:
        if not session.events or session.events[-1].type != "ACTION_EXECUTED":
            return None
        if session.events[-1].payload.action_type != ActionType.PROBE:
            return None

        state = self._build_state(session)
        target_agent_id = session.events[-1].payload.target_agent_id
        student = next(
            (item for item in state.students if item.id == target_agent_id),
            None,
        ) or self._select_dialog_student(state)
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

    @classmethod
    def _select_dialog_student(
        cls,
        state: ClassroomState,
        *,
        question: str = "",
        knowledge_point: str = "",
    ):
        if not state.students:
            return None
        spoken_ids = {
            event.payload.turn.agent_id
            for event in state.recent_events
            if event.type == "AGENT_TURN" and event.payload.turn.role == "student"
        }
        spoken_ids.update(student.id for student in state.students if student.last_intent)
        recent_speakers = cls._recent_student_speaker_id_list(state, limit=3)
        context = f"{question} {knowledge_point}".lower()

        def score(student) -> float:
            # Fairness and classroom state deliberately outweigh small random noise.
            total = 3.0 if student.id not in spoken_ids else 0.0
            total += 1.4 * student.engagement
            total -= 1.2 * student.pressure
            if student.id not in recent_speakers:
                total += 0.8
            elif recent_speakers[0] == student.id:
                total -= 3.5
            else:
                total -= 1.5 / (recent_speakers.index(student.id) + 1)
            total += cls._question_match_score(student.agent_type, context)
            total += random.uniform(-0.25, 0.25)
            return total

        return max(state.students, key=score)

    @staticmethod
    def _question_match_score(agent_type: StudentAgentType, context: str) -> float:
        keywords = {
            StudentAgentType.DEEP_THINKER: (
                "为什么", "原因", "条件", "前提", "推理", "成立", "机制", "因果", "关系",
            ),
            StudentAgentType.CONCEPT_CONFUSED: (
                "区别", "区分", "辨析", "混淆", "相似", "比较", "异同", "概念",
            ),
            StudentAgentType.FOUNDATION_WEAK: (
                "定义", "基础", "基本", "是什么", "第一步", "步骤", "前置",
            ),
            StudentAgentType.NOTE_TAKER: (
                "总结", "概括", "复述", "要点", "重点", "核心", "梳理",
            ),
            StudentAgentType.RESEARCHER: (
                "研究", "拓展", "延伸", "局限", "进一步", "开放", "假设",
            ),
            StudentAgentType.PRACTICAL_APPLIER: (
                "应用", "实际", "案例", "怎么做", "操作", "场景", "解决", "任务",
            ),
            StudentAgentType.ATMOSPHERE_REGULATOR: (
                "类比", "生活", "直观", "简单", "日常", "轻松",
            ),
            StudentAgentType.SILENT_OBSERVER: (),
        }
        matches = sum(keyword in context for keyword in keywords[agent_type])
        return min(matches * 0.9, 3.0)

    @staticmethod
    def _recent_student_speaker_ids(state: ClassroomState, limit: int = 1) -> set[str]:
        return set(ClassroomService._recent_student_speaker_id_list(state, limit))

    @staticmethod
    def _recent_student_speaker_id_list(
        state: ClassroomState, limit: int = 1
    ) -> list[str]:
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
        return recent_ids

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
