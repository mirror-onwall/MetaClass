from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.content.service import ContentService
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.repository import PresentationRepository
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PPTGenerationJob,
    PPTGenerationStatus,
    PPTSlideImage,
    PresentationPlan,
)
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter


class PresentationService:
    def __init__(
        self,
        data_dir: Path,
        repository: PresentationRepository,
        contents: ContentService,
        planner: PresentationPlanGenerator | None = None,
        ppt_adapter: PPTSkillAdapter | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.contents = contents
        self.planner = planner or PresentationPlanGenerator()
        self.ppt_adapter = ppt_adapter or PPTSkillAdapter()

    def create_plan(self, content_id: str) -> PresentationPlan:
        content = self.contents.get(content_id)
        plan = self.planner.generate(content)
        self.repository.save_plan(plan)
        return plan

    def get_plan(self, plan_id: str) -> PresentationPlan:
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Presentation plan not found")
        return plan

    def get_plan_for_content(self, content_id: str) -> PresentationPlan:
        self.contents.get(content_id)
        plan = self.repository.get_plan_for_content(content_id)
        if not plan:
            raise HTTPException(404, "Presentation plan not found")
        return plan

    def create_ppt_job(self, presentation_plan_id: str) -> PPTGenerationJob:
        plan = self.get_plan(presentation_plan_id)
        job = PPTGenerationJob(
            id=f"ppt_job_{uuid4().hex[:12]}",
            presentation_plan_id=plan.id,
        )
        self.repository.save_job(job)
        return job

    def run_ppt_job(self, job_id: str) -> None:
        job = self.get_ppt_job(job_id)
        plan = self.get_plan(job.presentation_plan_id)
        job.status = PPTGenerationStatus.RUNNING
        job.progress = 0.2
        job.updated_at = utc_now()
        self.repository.save_job(job)
        try:
            artifact = self.ppt_adapter.prepare_request(
                plan=plan,
                job_id=job.id,
                output_dir=self.data_dir / "generated" / "presentations" / job.id,
            )
            self.repository.save_artifact(artifact)
            job.artifact_id = artifact.id
            job.status = PPTGenerationStatus.FINISHED
            job.progress = 1.0
        except Exception as exc:
            job.error = str(exc)
            job.status = PPTGenerationStatus.FAILED
            job.progress = 1.0
        job.updated_at = utc_now()
        self.repository.save_job(job)

    def get_ppt_job(self, job_id: str) -> PPTGenerationJob:
        job = self.repository.get_job(job_id)
        if not job:
            raise HTTPException(404, "PPT generation job not found")
        return job

    def get_artifact(self, artifact_id: str) -> PPTArtifact:
        artifact = self.repository.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(404, "PPT artifact not found")
        return artifact

    def get_artifact_for_job(self, job_id: str) -> PPTArtifact:
        self.get_ppt_job(job_id)
        artifact = self.repository.get_artifact_for_job(job_id)
        if not artifact:
            raise HTTPException(409, "PPT artifact is not available")
        return artifact

    def list_slide_images(self, artifact_id: str) -> list[PPTSlideImage]:
        artifact = self.get_artifact(artifact_id)
        return artifact.slide_images

    def get_slide_image(self, artifact_id: str, slide_no: int) -> PPTSlideImage:
        artifact = self.get_artifact(artifact_id)
        slide_image = next(
            (item for item in artifact.slide_images if item.slide_no == slide_no),
            None,
        )
        if not slide_image:
            raise HTTPException(404, "PPT slide image not found")
        return slide_image
