from pathlib import Path
from threading import Lock
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
    PresentationPlanJob,
    PresentationPlanJobStatus,
    PresentationPlan,
)
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.repository import QuestionBankRepository


class PresentationService:
    def __init__(
        self,
        data_dir: Path,
        repository: PresentationRepository,
        contents: ContentService,
        planner: PresentationPlanGenerator | None = None,
        ppt_adapter: PPTSkillAdapter | None = None,
        question_bank_generator: QuestionBankGenerator | None = None,
        question_bank_repository: QuestionBankRepository | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.contents = contents
        self.planner = planner or PresentationPlanGenerator()
        self.ppt_adapter = ppt_adapter or PPTSkillAdapter()
        self.question_bank_generator = question_bank_generator
        self.question_bank_repository = question_bank_repository
        self._plan_jobs: dict[str, PresentationPlanJob] = {}
        self._plan_job_lock = Lock()

    def create_plan(self, content_id: str) -> PresentationPlan:
        content = self.contents.get(content_id)
        plan = self.planner.generate(content)
        self.repository.save_plan(plan)
        self._prepare_question_bank(content, plan)
        return plan

    def create_plan_job(
        self, content_id: str, *, prepare_question_bank: bool = True
    ) -> PresentationPlanJob:
        self.contents.get(content_id)
        job = PresentationPlanJob(
            id=f"presentation_plan_job_{uuid4().hex[:12]}",
            content_id=content_id,
            prepare_question_bank=prepare_question_bank,
            status=PresentationPlanJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to generate presentation plan",
        )
        self._save_plan_job(job)
        return job

    def get_plan_job(self, job_id: str) -> PresentationPlanJob:
        with self._plan_job_lock:
            job = self._plan_jobs.get(job_id)
            if not job:
                raise HTTPException(404, "Presentation plan job not found")
            return job.model_copy(deep=True)

    def plan_job_result(self, job_id: str) -> PresentationPlan:
        job = self.get_plan_job(job_id)
        if job.status == PresentationPlanJobStatus.FAILED:
            raise HTTPException(422, job.error or "Presentation plan job failed")
        if job.status != PresentationPlanJobStatus.SUCCEEDED or not job.plan_id:
            raise HTTPException(409, "Presentation plan job is not finished")
        return self.get_plan(job.plan_id)

    def run_plan_job(self, job_id: str) -> None:
        job = self.get_plan_job(job_id)
        try:
            job.status = PresentationPlanJobStatus.RUNNING
            job.progress = 5
            job.step = "starting"
            job.message = "Starting presentation planning"
            job.updated_at = utc_now()
            self._save_plan_job(job)

            def report_progress(progress: int, step: str, message: str) -> None:
                self._update_plan_job_progress(job_id, progress, step, message)

            content = self.contents.get(job.content_id)
            plan = self.planner.generate(content, progress_callback=report_progress)

            self._update_plan_job_progress(
                job_id,
                75,
                "saving",
                "Saving presentation plan",
            )
            self.repository.save_plan(plan)

            if job.prepare_question_bank:
                self._update_plan_job_progress(
                    job_id,
                    80,
                    "preparing_questions",
                    "Preparing student questions and teacher answers",
                )
                self._prepare_question_bank(content, plan)

            job.status = PresentationPlanJobStatus.SUCCEEDED
            job.progress = 100
            job.step = "completed"
            job.message = "Presentation plan generation completed"
            job.plan_id = plan.id
            job.updated_at = utc_now()
            self._save_plan_job(job)
        except Exception as exc:
            job.status = PresentationPlanJobStatus.FAILED
            job.progress = 100
            job.step = "failed"
            job.message = "Presentation plan generation failed"
            job.error = str(exc)
            job.updated_at = utc_now()
            self._save_plan_job(job)

    def _prepare_question_bank(self, content, plan: PresentationPlan) -> None:
        if not self.question_bank_generator or not self.question_bank_repository:
            return
        items = self.question_bank_generator.generate(content, plan)
        self.question_bank_repository.replace_for_plan(plan.id, items)

    def _save_plan_job(self, job: PresentationPlanJob) -> None:
        with self._plan_job_lock:
            self._plan_jobs[job.id] = job.model_copy(deep=True)

    def _update_plan_job_progress(
        self,
        job_id: str,
        progress: int,
        step: str,
        message: str,
    ) -> None:
        with self._plan_job_lock:
            job = self._plan_jobs.get(job_id)
            if not job or job.status != PresentationPlanJobStatus.RUNNING:
                return
            progress = min(progress, 99)
            if progress < job.progress:
                return
            job.progress = progress
            job.step = step
            job.message = message
            job.updated_at = utc_now()
            self._plan_jobs[job_id] = job.model_copy(deep=True)

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

    def get_artifact_for_plan(self, plan_id: str) -> PPTArtifact:
        self.get_plan(plan_id)
        artifact = self.repository.get_artifact_for_plan(plan_id)
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
