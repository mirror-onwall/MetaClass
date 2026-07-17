from fastapi import APIRouter, Query

from metaclass.modules.question_bank.schemas import QuestionBank, QuestionSearchResult
from metaclass.modules.question_bank.service import QuestionBankService


def create_router(question_banks: QuestionBankService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["question-bank"])

    @router.post(
        "/presentation-plans/{plan_id}/question-bank",
        response_model=QuestionBank,
        status_code=201,
    )
    async def generate_question_bank(plan_id: str) -> QuestionBank:
        return question_banks.generate_for_plan(plan_id)

    @router.get(
        "/presentation-plans/{plan_id}/question-bank",
        response_model=QuestionBank,
    )
    async def get_question_bank(plan_id: str) -> QuestionBank:
        return question_banks.get_for_plan(plan_id)

    @router.get(
        "/presentation-plans/{plan_id}/question-bank/search",
        response_model=list[QuestionSearchResult],
    )
    async def search_question_bank(
        plan_id: str,
        q: str = Query(min_length=1),
        limit: int = Query(default=5, ge=1, le=20),
    ) -> list[QuestionSearchResult]:
        return question_banks.search(plan_id, q, limit)

    return router
