from fastapi import APIRouter, HTTPException

from metaclass.modules.interaction_planning.jobs import (
    InteractionPlanningJob,
    InteractionPlanningJobService,
)


def create_router(jobs: InteractionPlanningJobService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["interaction-planning"])

    @router.get("/interaction-planning-jobs/{job_id}", response_model=InteractionPlanningJob)
    async def get_job(job_id: str) -> InteractionPlanningJob:
        return jobs.get(job_id)

    @router.get(
        "/presentation-plans/{plan_id}/interaction-planning-job",
        response_model=InteractionPlanningJob,
    )
    async def latest_job(plan_id: str) -> InteractionPlanningJob:
        job = jobs.latest_for_plan(plan_id)
        if not job:
            raise HTTPException(404, "InteractionPlanningJob not found")
        return job

    @router.post(
        "/interaction-planning-jobs/{job_id}/retry",
        response_model=InteractionPlanningJob,
    )
    async def retry_job(job_id: str) -> InteractionPlanningJob:
        return jobs.retry(job_id)

    return router
