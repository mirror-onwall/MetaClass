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

    @router.post(
        "/presentation-plans/{plan_id}/question-bank/regenerate",
        response_model=QuestionBank,
        status_code=201,
    )
    async def regenerate_question_bank(plan_id: str) -> QuestionBank:
        return question_banks.regenerate_for_plan(plan_id)

    @router.get(
        "/presentation-plans/{plan_id}/question-bank",
        response_model=QuestionBank,
    )
    async def get_question_bank(plan_id: str) -> QuestionBank:
        return question_banks.get_for_plan(plan_id)

    @router.get(
        "/presentation-plans/{plan_id}/question-bank/versions",
        response_model=list[QuestionBank],
    )
    async def list_question_bank_versions(plan_id: str) -> list[QuestionBank]:
        return question_banks.list_versions_for_plan(plan_id)

    @router.delete(
        "/presentation-plans/{plan_id}/question-bank/versions/{generation_id}",
    )
    async def archive_question_bank_version(plan_id: str, generation_id: str) -> dict[str, bool]:
        question_banks.archive_generation(plan_id, generation_id)
        return {"archived": True}

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
