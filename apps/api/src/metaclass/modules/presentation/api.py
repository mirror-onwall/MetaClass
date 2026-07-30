from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import FileResponse

from metaclass.modules.presentation.schemas import (
    CreatePPTJobRequest,
    PPTArtifact,
    PPTGenerationJob,
    PPTSlideImage,
    PPTThemeOption,
    PresentationPlan,
    PresentationPlanDiagnosis,
    PresentationPlanJob,
    PresentationPlanLibrarySummary,
)
from metaclass.modules.presentation.service import PresentationService


def create_router(presentations: PresentationService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["presentation"])

    @router.post(
        "/learning-contents/{content_id}/presentation-plans",
        response_model=PresentationPlan,
        status_code=201,
    )
    async def create_presentation_plan(content_id: str) -> PresentationPlan:
        return presentations.create_plan(content_id)

    @router.post(
        "/learning-contents/{content_id}/presentation-plan-jobs",
        response_model=PresentationPlanJob,
        status_code=202,
    )
    async def create_presentation_plan_job(
        content_id: str,
        background_tasks: BackgroundTasks,
        prepare_question_bank: bool = True,
    ) -> PresentationPlanJob:
        job = presentations.create_plan_job(
            content_id, prepare_question_bank=prepare_question_bank
        )
        background_tasks.add_task(presentations.run_plan_job, job.id)
        return job

    @router.get(
        "/presentation-plan-jobs/{job_id}",
        response_model=PresentationPlanJob,
    )
    async def get_presentation_plan_job(job_id: str) -> PresentationPlanJob:
        return presentations.get_plan_job(job_id)

    @router.get(
        "/presentation-plan-jobs/{job_id}/result",
        response_model=PresentationPlan,
    )
    async def get_presentation_plan_job_result(job_id: str) -> PresentationPlan:
        return presentations.plan_job_result(job_id)

    @router.get(
        "/learning-contents/{content_id}/presentation-plan",
        response_model=PresentationPlan,
    )
    async def get_latest_presentation_plan(content_id: str) -> PresentationPlan:
        return presentations.get_plan_for_content(content_id)

    @router.get("/presentation-plans/{plan_id}", response_model=PresentationPlan)
    async def get_presentation_plan(plan_id: str) -> PresentationPlan:
        return presentations.get_plan(plan_id)

    @router.get(
        "/presentation-plan-library",
        response_model=list[PresentationPlanLibrarySummary],
    )
    async def list_presentation_plan_library() -> list[PresentationPlanLibrarySummary]:
        return presentations.list_plan_summaries()

    @router.get(
        "/presentation-plans/{plan_id}/diagnosis",
        response_model=PresentationPlanDiagnosis,
    )
    async def diagnose_presentation_plan(plan_id: str) -> PresentationPlanDiagnosis:
        return presentations.diagnose_plan(plan_id)

    @router.post(
        "/presentation-plans/{plan_id}/ppt-jobs",
        response_model=PPTGenerationJob,
        status_code=202,
    )
    async def create_ppt_job(
        plan_id: str,
        background_tasks: BackgroundTasks,
        payload: CreatePPTJobRequest | None = None,
    ) -> PPTGenerationJob:
        job = presentations.create_ppt_job(
            plan_id,
            theme_id=payload.theme_id if payload else None,
        )
        background_tasks.add_task(presentations.run_ppt_job, job.id)
        return job

    @router.get("/ppt-themes", response_model=list[PPTThemeOption])
    async def list_ppt_themes() -> list[PPTThemeOption]:
        return presentations.list_ppt_themes()

    @router.get("/ppt-jobs/{job_id}", response_model=PPTGenerationJob)
    async def get_ppt_job(job_id: str) -> PPTGenerationJob:
        return presentations.get_ppt_job(job_id)

    @router.get("/ppt-jobs/{job_id}/artifact", response_model=PPTArtifact)
    async def get_ppt_artifact_for_job(job_id: str) -> PPTArtifact:
        return presentations.get_artifact_for_job(job_id)

    @router.get("/ppt-artifacts/{artifact_id}", response_model=PPTArtifact)
    async def get_ppt_artifact(artifact_id: str) -> PPTArtifact:
        return presentations.get_artifact(artifact_id)

    @router.get(
        "/ppt-artifacts/{artifact_id}/slides",
        response_model=list[PPTSlideImage],
    )
    async def list_ppt_slide_images(artifact_id: str) -> list[PPTSlideImage]:
        return presentations.list_slide_images(artifact_id)

    @router.get("/ppt-artifacts/{artifact_id}/slides/{slide_no}/image")
    async def get_ppt_slide_image(artifact_id: str, slide_no: int) -> FileResponse:
        slide_image = presentations.get_slide_image(artifact_id, slide_no)
        return FileResponse(slide_image.image_path, media_type="image/png")

    @router.get("/ppt-artifacts/{artifact_id}/skill-request")
    async def download_skill_request(artifact_id: str) -> FileResponse:
        artifact = presentations.get_artifact(artifact_id)
        return FileResponse(
            artifact.skill_request_path,
            media_type="application/json",
            filename=f"{artifact.id}_skill_request.json",
        )

    @router.get("/ppt-artifacts/{artifact_id}/download")
    async def download_pptx(artifact_id: str) -> FileResponse:
        artifact = presentations.get_artifact(artifact_id)
        if not artifact.pptx_path:
            from fastapi import HTTPException

            raise HTTPException(409, "PPTX artifact is not available")
        return FileResponse(
            artifact.pptx_path,
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            filename=f"{artifact.id}.pptx",
        )

    return router
