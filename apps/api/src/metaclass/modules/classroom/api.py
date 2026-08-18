from fastapi import APIRouter, BackgroundTasks, Header

from metaclass.modules.classroom.agent_schemas import AgentTurn, DirectedAgentTurn
from metaclass.modules.classroom.schemas import (
    AgentTurnRequest,
    AnswerRequest,
    AutoClassroomStep,
    ClassroomPlan,
    ClassroomPlanGenerationMeta,
    ClassroomPlanJob,
    ClassroomPlanLibrarySummary,
    ClassroomNavigationResult,
    ClassroomSession,
    ClassroomState,
    ControllerResult,
    CreateClassroomSessionRequest,
    LearningMode,
    QuestionRequest,
    SwitchClassroomModeRequest,
)
from metaclass.modules.classroom.service import ClassroomService


def create_router(classrooms: ClassroomService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["classroom"])

    @router.post(
        "/learning-contents/{content_id}/classroom-plans",
        response_model=ClassroomPlan,
        status_code=201,
    )
    async def create_plan(
        content_id: str, presentation_plan_id: str | None = None
    ) -> ClassroomPlan:
        return classrooms.create_plan(content_id, presentation_plan_id)

    @router.post(
        "/learning-contents/{content_id}/classroom-plan-jobs",
        response_model=ClassroomPlanJob,
        status_code=202,
    )
    async def create_plan_job(
        content_id: str,
        background_tasks: BackgroundTasks,
        presentation_plan_id: str | None = None,
    ) -> ClassroomPlanJob:
        job = classrooms.create_plan_job(content_id, presentation_plan_id)
        background_tasks.add_task(classrooms.run_plan_job, job.id)
        return job

    @router.get("/classroom-plan-jobs/{job_id}", response_model=ClassroomPlanJob)
    async def get_plan_job(job_id: str) -> ClassroomPlanJob:
        return classrooms.get_plan_job(job_id)

    @router.get(
        "/learning-contents/{content_id}/latest-classroom-plan-job",
        response_model=ClassroomPlanJob,
    )
    async def get_latest_plan_job(
        content_id: str, presentation_plan_id: str | None = None
    ) -> ClassroomPlanJob:
        return classrooms.get_latest_plan_job(content_id, presentation_plan_id)

    @router.get("/classroom-plans/{plan_id}", response_model=ClassroomPlan)
    async def get_plan(plan_id: str) -> ClassroomPlan:
        return classrooms.get_plan(plan_id)

    @router.get(
        "/classroom-plan-library",
        response_model=list[ClassroomPlanLibrarySummary],
    )
    async def list_classroom_plan_library() -> list[ClassroomPlanLibrarySummary]:
        return classrooms.list_plan_summaries()

    @router.post(
        "/classroom-plans/{plan_id}/lecture-variant",
        response_model=ClassroomPlan,
        status_code=201,
    )
    async def create_lecture_variant(plan_id: str) -> ClassroomPlan:
        return classrooms.create_lecture_variant(plan_id)

    @router.get(
        "/classroom-plans/{plan_id}/generation-meta",
        response_model=ClassroomPlanGenerationMeta,
    )
    async def get_plan_generation_meta(plan_id: str) -> ClassroomPlanGenerationMeta:
        return classrooms.get_plan_generation_meta(plan_id)

    @router.post(
        "/classroom-plans/{plan_id}/sessions",
        response_model=ClassroomSession,
        status_code=201,
    )
    async def create_session(
        plan_id: str,
        request: CreateClassroomSessionRequest | None = None,
    ) -> ClassroomSession:
        mode = request.mode if request else LearningMode.LECTURE
        student_agent_types = request.student_agent_types if request else None
        return classrooms.create_session(plan_id, mode, student_agent_types)

    @router.get("/classroom-sessions/{session_id}", response_model=ClassroomSession)
    async def get_session(session_id: str) -> ClassroomSession:
        return classrooms.get_session(session_id)

    @router.post(
        "/classroom-sessions/{session_id}/replay",
        response_model=ClassroomSession,
        status_code=201,
    )
    async def replay_session(session_id: str) -> ClassroomSession:
        return classrooms.replay_session(session_id)

    @router.get("/classroom-sessions/{session_id}/state", response_model=ClassroomState)
    async def get_state(session_id: str) -> ClassroomState:
        return classrooms.get_state(session_id)

    @router.post(
        "/classroom-sessions/{session_id}/mode",
        response_model=ClassroomSession,
    )
    async def switch_mode(
        session_id: str,
        request: SwitchClassroomModeRequest,
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        expected_version: int | None = Header(
            default=None, alias="X-Expected-Session-Version"
        ),
    ) -> ClassroomSession:
        if request_id is None or expected_version is None:
            return classrooms.switch_mode(
                session_id, request.mode, request.student_agent_types
            )
        return classrooms.execute_mode_switch(
            session_id,
            request.mode,
            request.student_agent_types,
            request_id,
            expected_version,
        )

    @router.post(
        "/classroom-sessions/{session_id}/next",
        response_model=ControllerResult,
    )
    async def next_action(
        session_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        expected_version: int | None = Header(
            default=None, alias="X-Expected-Session-Version"
        ),
    ) -> ControllerResult:
        if request_id is None or expected_version is None:
            return classrooms.next(session_id)
        return classrooms.execute_next(session_id, request_id, expected_version)

    @router.post(
        "/classroom-sessions/{session_id}/navigation/{direction}",
        response_model=ClassroomNavigationResult,
    )
    async def navigate_classroom(session_id: str, direction: str) -> ClassroomNavigationResult:
        return classrooms.navigate(session_id, direction)

    @router.post(
        "/classroom-sessions/{session_id}/auto-step",
        response_model=AutoClassroomStep,
    )
    async def auto_step(
        session_id: str,
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        expected_version: int | None = Header(
            default=None, alias="X-Expected-Session-Version"
        ),
    ) -> AutoClassroomStep:
        if request_id is None or expected_version is None:
            return classrooms.auto_step(session_id)
        return classrooms.execute_auto_step(session_id, request_id, expected_version)

    @router.post(
        "/classroom-sessions/{session_id}/answers",
        response_model=ControllerResult,
    )
    async def submit_answer(
        session_id: str,
        request: AnswerRequest,
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        expected_version: int | None = Header(
            default=None, alias="X-Expected-Session-Version"
        ),
    ) -> ControllerResult:
        if request_id is None or expected_version is None:
            return classrooms.answer(session_id, request.selected_index)
        return classrooms.execute_answer(
            session_id, request.selected_index, request_id, expected_version
        )

    @router.post(
        "/classroom-sessions/{session_id}/questions",
        response_model=ControllerResult,
    )
    async def ask_question(
        session_id: str,
        request: QuestionRequest,
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        expected_version: int | None = Header(
            default=None, alias="X-Expected-Session-Version"
        ),
    ) -> ControllerResult:
        if request_id is None or expected_version is None:
            return classrooms.answer_question(session_id, request.question)
        return classrooms.execute_question(
            session_id, request.question, request_id, expected_version
        )

    @router.post(
        "/classroom-sessions/{session_id}/teacher-turn",
        response_model=AgentTurn,
    )
    async def generate_teacher_turn(session_id: str, request: AgentTurnRequest) -> AgentTurn:
        return classrooms.generate_teacher_turn(session_id, request.prompt)

    @router.post(
        "/classroom-sessions/{session_id}/student-turns",
        response_model=list[AgentTurn],
    )
    async def generate_student_turns(session_id: str, request: AgentTurnRequest) -> list[AgentTurn]:
        return classrooms.generate_student_turns(session_id, request.prompt)

    @router.post(
        "/classroom-sessions/{session_id}/agent-turns/next",
        response_model=DirectedAgentTurn,
    )
    async def generate_next_agent_turn(session_id: str) -> DirectedAgentTurn:
        return classrooms.generate_next_agent_turn(session_id)

    return router
