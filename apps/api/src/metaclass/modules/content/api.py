from fastapi import APIRouter

from metaclass.modules.content.schemas import LearningContent, PageUnderstanding
from metaclass.modules.content.service import ContentService


def create_router(contents: ContentService) -> APIRouter:
    router = APIRouter(tags=["content"])

    @router.post(
        "/api/v1/materials/{material_id}/learning-content",
        response_model=LearningContent,
        status_code=201,
    )
    async def build_content(material_id: str) -> LearningContent:
        return contents.build(material_id)

    @router.post(
        "/api/v1/materials/{material_id}/understandings",
        response_model=list[PageUnderstanding],
        status_code=201,
    )
    async def generate_understandings(material_id: str) -> list[PageUnderstanding]:
        return contents.understand_pages(material_id, regenerate=True)

    @router.get(
        "/api/v1/materials/{material_id}/understandings",
        response_model=list[PageUnderstanding],
    )
    async def list_understandings(material_id: str) -> list[PageUnderstanding]:
        return contents.list_understandings(material_id)

    @router.get(
        "/api/v1/learning-contents/{content_id}",
        response_model=LearningContent,
    )
    async def get_content(content_id: str) -> LearningContent:
        return contents.get(content_id)

    return router
