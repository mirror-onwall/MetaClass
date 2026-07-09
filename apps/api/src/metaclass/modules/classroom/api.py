from fastapi import APIRouter

from metaclass.modules.classroom.agent_schemas import AgentTurn, DirectedAgentTurn
from metaclass.modules.classroom.schemas import (
    AgentTurnRequest,
    AnswerRequest,
    ClassroomPlan,
    ClassroomSession,
    ClassroomState,
    ControllerResult,
    CreateClassroomSessionRequest,
    LearningMode,
    QuestionRequest,
)
from metaclass.modules.classroom.service import ClassroomService


def create_router(classrooms: ClassroomService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["classroom"])

    @router.post(
        "/learning-contents/{content_id}/classroom-plans",
        response_model=ClassroomPlan,
        status_code=201,
    )
    async def create_plan(content_id: str) -> ClassroomPlan:
        return classrooms.create_plan(content_id)

    @router.get("/classroom-plans/{plan_id}", response_model=ClassroomPlan)
    async def get_plan(plan_id: str) -> ClassroomPlan:
        return classrooms.get_plan(plan_id)

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
        return classrooms.create_session(plan_id, mode)

    @router.get("/classroom-sessions/{session_id}", response_model=ClassroomSession)
    async def get_session(session_id: str) -> ClassroomSession:
        return classrooms.get_session(session_id)

    @router.get("/classroom-sessions/{session_id}/state", response_model=ClassroomState)
    async def get_state(session_id: str) -> ClassroomState:
        return classrooms.get_state(session_id)

    @router.post(
        "/classroom-sessions/{session_id}/next",
        response_model=ControllerResult,
    )
    async def next_action(session_id: str) -> ControllerResult:
        return classrooms.next(session_id)

    @router.post(
        "/classroom-sessions/{session_id}/answers",
        response_model=ControllerResult,
    )
    async def submit_answer(session_id: str, request: AnswerRequest) -> ControllerResult:
        return classrooms.answer(session_id, request.selected_index)

    @router.post(
        "/classroom-sessions/{session_id}/questions",
        response_model=ControllerResult,
    )
    async def ask_question(session_id: str, request: QuestionRequest) -> ControllerResult:
        return classrooms.answer_question(session_id, request.question)

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
