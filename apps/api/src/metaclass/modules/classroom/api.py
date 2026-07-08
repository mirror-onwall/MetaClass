from fastapi import APIRouter

from metaclass.modules.classroom.schemas import (
    AnswerRequest,
    ClassroomPlan,
    ClassroomSession,
    ControllerResult,
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
    async def create_session(plan_id: str) -> ClassroomSession:
        return classrooms.create_session(plan_id)

    @router.get("/classroom-sessions/{session_id}", response_model=ClassroomSession)
    async def get_session(session_id: str) -> ClassroomSession:
        return classrooms.get_session(session_id)

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

    return router
