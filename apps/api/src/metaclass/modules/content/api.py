from fastapi import APIRouter, BackgroundTasks

from metaclass.modules.content.schemas import (
    ContentGenerationJob,
    CourseKnowledgeTree,
    LearningContent,
    LearningContentDiagnostics,
    MaterialLearningContentSummary,
    PageUnderstanding,
)
from metaclass.modules.content.service import ContentService


def create_router(contents: ContentService) -> APIRouter:
    router = APIRouter(tags=["content"])

    @router.get(
        "/api/v1/material-learning-content-summaries",
        response_model=list[MaterialLearningContentSummary],
    )
    async def list_material_learning_content_summaries(
    ) -> list[MaterialLearningContentSummary]:
        return contents.list_material_summaries()

    @router.post(
        "/api/v1/materials/{material_id}/learning-content",
        response_model=LearningContent,
        status_code=201,
    )
    async def build_content(material_id: str) -> LearningContent:
        return contents.build(material_id)

    @router.post(
        "/api/v1/materials/{material_id}/learning-content-jobs",
        response_model=ContentGenerationJob,
        status_code=202,
    )
    async def create_content_job(
        material_id: str,
        background_tasks: BackgroundTasks,
    ) -> ContentGenerationJob:
        job = contents.create_generation_job(material_id)
        background_tasks.add_task(contents.run_generation_job, job.id)
        return job

    @router.post(
        "/api/v1/materials/{material_id}/source-deck-learning-content-jobs",
        response_model=ContentGenerationJob,
        status_code=202,
    )
    async def create_source_deck_content_job(
        material_id: str,
        background_tasks: BackgroundTasks,
    ) -> ContentGenerationJob:
        job = contents.create_source_deck_generation_job(material_id)
        background_tasks.add_task(contents.run_generation_job, job.id)
        return job

    @router.post(
        "/api/v1/material-collections/{collection_id}/learning-content-jobs",
        response_model=ContentGenerationJob,
        status_code=202,
    )
    async def create_collection_content_job(
        collection_id: str,
        background_tasks: BackgroundTasks,
    ) -> ContentGenerationJob:
        job = contents.create_collection_generation_job(collection_id)
        background_tasks.add_task(contents.run_generation_job, job.id)
        return job

    @router.get(
        "/api/v1/learning-content-jobs/{job_id}",
        response_model=ContentGenerationJob,
    )
    async def get_content_job(job_id: str) -> ContentGenerationJob:
        return contents.get_generation_job(job_id)

    @router.get("/api/v1/learning-content-jobs", response_model=list[ContentGenerationJob])
    async def list_content_jobs() -> list[ContentGenerationJob]:
        return contents.list_generation_jobs()

    @router.post("/api/v1/learning-content-jobs/{job_id}/pause", response_model=ContentGenerationJob)
    async def pause_content_job(job_id: str) -> ContentGenerationJob:
        return contents.pause_generation_job(job_id)

    @router.post("/api/v1/learning-content-jobs/{job_id}/resume", response_model=ContentGenerationJob)
    async def resume_content_job(job_id: str, background_tasks: BackgroundTasks) -> ContentGenerationJob:
        job = contents.resume_generation_job(job_id)
        background_tasks.add_task(contents.run_generation_job, job.id)
        return job

    @router.delete("/api/v1/learning-content-jobs/{job_id}", status_code=204)
    async def discard_content_job(job_id: str) -> None:
        contents.discard_generation_job(job_id)

    @router.get(
        "/api/v1/learning-content-jobs/{job_id}/result",
        response_model=LearningContent,
    )
    async def get_content_job_result(job_id: str) -> LearningContent:
        return contents.generation_job_result(job_id)

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

    @router.get(
        "/api/v1/learning-contents/{content_id}/knowledge-tree",
        response_model=CourseKnowledgeTree,
    )
    async def get_content_knowledge_tree(content_id: str) -> CourseKnowledgeTree:
        return contents.get_knowledge_tree(content_id)

    @router.get(
        "/api/v1/learning-contents/{content_id}/diagnostics",
        response_model=LearningContentDiagnostics,
    )
    async def get_content_diagnostics(content_id: str) -> LearningContentDiagnostics:
        return contents.get_diagnostics(content_id)

    return router
