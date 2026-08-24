import hashlib
import json
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.schemas import MaterialType
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.presentation.diagnostics import diagnose_presentation_plan
from metaclass.modules.presentation.planner import (
    PresentationPlanGenerator,
    SourceSlideNarrationBatch,
)
from metaclass.modules.presentation.providers import PPTProvider
from metaclass.modules.presentation.repository import PresentationRepository
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PPTGenerationJob,
    PPTGenerationStatus,
    PPTSlideImage,
    PresentationPlan,
    PresentationPlanDiagnosis,
    PresentationPlanJob,
    PresentationPlanJobStatus,
    PresentationResource,
    PresentationSlideResource,
)
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.presentation.themes import (
    get_presentation_theme,
    list_presentation_themes,
)
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.repository import QuestionBankRepository


class PresentationPlanPaused(RuntimeError):
    pass


class PresentationService:
    def __init__(
        self,
        data_dir: Path,
        repository: PresentationRepository,
        contents: ContentService,
        materials: MaterialService,
        planner: PresentationPlanGenerator | None = None,
        ppt_adapter: PPTProvider | None = None,
        question_bank_generator: QuestionBankGenerator | None = None,
        question_bank_repository: QuestionBankRepository | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.contents = contents
        self.materials = materials
        self.planner = planner or PresentationPlanGenerator()
        self.ppt_adapter = ppt_adapter or PPTSkillAdapter()
        self.question_bank_generator = question_bank_generator
        self.question_bank_repository = question_bank_repository
        self._plan_jobs: dict[str, PresentationPlanJob] = {}
        self._plan_job_lock = Lock()
        self._plan_pause_events: dict[str, Event] = {}
        self._ppt_pause_events: dict[str, Event] = {}
        self._plan_jobs_dir = self.data_dir / "runtime" / "presentation_plan" / "jobs"
        self._restore_plan_jobs()

    def _restore_plan_jobs(self) -> None:
        if not self._plan_jobs_dir.exists():
            return
        for path in self._plan_jobs_dir.glob("*.json"):
            try:
                job = PresentationPlanJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.status == PresentationPlanJobStatus.RUNNING:
                job.status = PresentationPlanJobStatus.PAUSED
                job.step = "paused"
                job.message = "服务中断，Segment 讲稿进度已保存"
                job.error = None
                self._write_json_atomic(path, job.model_dump(mode="json"))
            self._plan_jobs[job.id] = job
            self._plan_pause_events[job.id] = Event()

    def pause_plan_job(self, job_id: str) -> PresentationPlanJob:
        job = self.get_plan_job(job_id)
        if job.status not in {PresentationPlanJobStatus.QUEUED, PresentationPlanJobStatus.RUNNING}:
            raise HTTPException(409, "Only queued or running jobs can be paused")
        with self._plan_job_lock:
            self._plan_pause_events.setdefault(job_id, Event()).set()
        job.status = PresentationPlanJobStatus.PAUSED
        job.step = "paused"
        job.message = "已请求暂停；当前 Segment 讲稿保存后停止"
        job.updated_at = utc_now()
        self._save_plan_job(job)
        return job

    def resume_plan_job(self, job_id: str) -> PresentationPlanJob:
        job = self.get_plan_job(job_id)
        if job.status != PresentationPlanJobStatus.PAUSED:
            raise HTTPException(409, "Only paused jobs can be resumed")
        with self._plan_job_lock:
            self._plan_pause_events[job_id] = Event()
        job.status = PresentationPlanJobStatus.QUEUED
        job.step = "queued"
        job.message = "等待从已保存 Segment 继续"
        job.updated_at = utc_now()
        self._save_plan_job(job)
        return job

    def discard_plan_job(self, job_id: str) -> None:
        job = self.get_plan_job(job_id)
        if job.status == PresentationPlanJobStatus.RUNNING:
            raise HTTPException(409, "Pause the job before discarding it")
        with self._plan_job_lock:
            self._plan_jobs.pop(job_id, None)
            self._plan_pause_events.pop(job_id, None)
        (self._plan_jobs_dir / f"{job_id}.json").unlink(missing_ok=True)
        if job.mode == "source_deck" and job.source_material_id:
            self._narration_checkpoint_path(job.content_id, job.source_material_id).unlink(
                missing_ok=True
            )

    def create_plan(self, content_id: str) -> PresentationPlan:
        content = self.contents.get(content_id)
        plan = self.planner.generate(content)
        plan = self._attach_resource(plan)
        self.repository.save_plan(plan)
        self._save_resource(plan)
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

    def create_source_deck_plan_job(
        self,
        content_id: str,
        source_material_id: str,
        *,
        prepare_question_bank: bool = True,
    ) -> PresentationPlanJob:
        content = self.contents.get(content_id)
        allowed_material_ids = set(content.material_ids or [content.material_id])
        if source_material_id not in allowed_material_ids:
            raise HTTPException(409, "Source material does not belong to LearningContent")
        material = self.materials.get(source_material_id)
        if material.file_type not in {MaterialType.PPTX, MaterialType.PDF}:
            raise HTTPException(422, "Source presentation mode requires PPTX or PDF")
        pages = self.materials.pages(source_material_id)
        if not pages:
            raise HTTPException(409, "Source presentation must be parsed before creating a plan")
        job = PresentationPlanJob(
            id=f"presentation_plan_job_{uuid4().hex[:12]}",
            content_id=content_id,
            prepare_question_bank=prepare_question_bank,
            mode="source_deck",
            source_material_id=source_material_id,
            status=PresentationPlanJobStatus.QUEUED,
            progress=0,
            step="queued",
            message="Waiting to map the uploaded PPT",
        )
        self._save_plan_job(job)
        return job

    def save_paper_deck_plan(self, plan: PresentationPlan) -> PresentationPlan:
        """Persist a reconciled plan that teaches from an already generated deck."""
        if plan.mode != "paper_deck":
            raise ValueError("Paper deck plan must use paper_deck mode")
        if not (
            plan.source_material_id
            and plan.source_paper_material_id
            and plan.paper_artifact_bundle_id
        ):
            raise ValueError("Paper deck plan requires deck, paper, and artifact identities")
        pages = self.materials.pages(plan.source_material_id)
        expected = [page.page_no for page in sorted(pages, key=lambda item: item.page_no)]
        actual = [slide.source_page_no for slide in plan.slides]
        if actual != expected or any(slide.source_kind != "source" for slide in plan.slides):
            raise ValueError(f"Paper deck page mapping mismatch: expected {expected}, got {actual}")
        existing = self.repository.get_plan(plan.id)
        if existing:
            existing_slides = {slide.id: slide for slide in existing.slides}
            slides = [
                slide.model_copy(
                    update={
                        "speaker_script": existing_slides[slide.id].speaker_script,
                        "speaker_script_source": "teacher_override",
                    }
                )
                if slide.id in existing_slides
                and existing_slides[slide.id].speaker_script_source == "teacher_override"
                else slide
                for slide in plan.slides
            ]
            plan = plan.model_copy(
                update={
                    "presentation_resource_id": existing.presentation_resource_id,
                    "created_at": existing.created_at,
                    "slides": slides,
                }
            )
        plan = self._attach_resource(plan)
        self.repository.save_plan(plan)
        self._save_resource(plan)
        return plan

    def get_plan_job(self, job_id: str) -> PresentationPlanJob:
        with self._plan_job_lock:
            job = self._plan_jobs.get(job_id)
            if not job:
                raise HTTPException(404, "Presentation plan job not found")
            return job.model_copy(deep=True)

    def list_plan_jobs(self) -> list[PresentationPlanJob]:
        with self._plan_job_lock:
            return sorted(
                (job.model_copy(deep=True) for job in self._plan_jobs.values()),
                key=lambda job: job.updated_at,
                reverse=True,
            )

    def list_ppt_jobs(self) -> list[PPTGenerationJob]:
        return self.repository.list_jobs()

    def plan_job_result(self, job_id: str) -> PresentationPlan:
        job = self.get_plan_job(job_id)
        if job.status == PresentationPlanJobStatus.FAILED:
            raise HTTPException(422, job.error or "Presentation plan job failed")
        if job.status != PresentationPlanJobStatus.SUCCEEDED or not job.plan_id:
            raise HTTPException(409, "Presentation plan job is not finished")
        return self.get_plan(job.plan_id)

    def run_plan_job(self, job_id: str) -> None:
        job = self.get_plan_job(job_id)
        with self._plan_job_lock:
            pause_event = self._plan_pause_events.setdefault(job_id, Event())
        try:
            job.status = PresentationPlanJobStatus.RUNNING
            job.progress = 5
            job.step = "starting"
            job.message = "Starting presentation planning"
            job.updated_at = utc_now()
            self._save_plan_job(job)

            if job.plan_id:
                plan = self.get_plan(job.plan_id)
                content = self.contents.get(job.content_id)
                if (
                    job.prepare_question_bank
                    and self.question_bank_repository
                    and not self.question_bank_repository.list_for_plan(plan.id)
                ):
                    self._prepare_question_bank(content, plan)
                if pause_event.is_set():
                    raise PresentationPlanPaused("Presentation planning paused")
                job.status = PresentationPlanJobStatus.SUCCEEDED
                job.progress = 100
                job.step = "completed"
                job.message = "Presentation plan generation completed"
                job.updated_at = utc_now()
                self._save_plan_job(job)
                return

            def report_progress(progress: int, step: str, message: str) -> None:
                self._update_plan_job_progress(job_id, progress, step, message)
                if pause_event.is_set():
                    raise PresentationPlanPaused("Presentation planning paused")

            content = self.contents.get(job.content_id)
            if job.mode == "source_deck":
                if not job.source_material_id:
                    raise ValueError("Source material is required for source deck mode")
                pages = self.materials.pages(job.source_material_id)
                narration, checkpoint_state = self._load_narration_checkpoint(
                    content, pages, job.source_material_id
                )

                def save_narration_segment(
                    segment_id: str, draft: SourceSlideNarrationBatch
                ) -> None:
                    narration[segment_id] = draft
                    checkpoint_state["segments"] = {
                        key: value.model_dump(mode="json") for key, value in narration.items()
                    }
                    self._write_json_atomic(
                        self._narration_checkpoint_path(content.id, job.source_material_id or ""),
                        checkpoint_state,
                    )
                    if pause_event.is_set():
                        raise PresentationPlanPaused("Presentation narration paused")

                plan = self.planner.generate_from_source_deck(
                    content,
                    pages,
                    job.source_material_id,
                    progress_callback=report_progress,
                    completed_narration=narration,
                    narration_checkpoint_callback=save_narration_segment,
                )
                self._validate_source_deck_plan(plan, pages)
            else:
                plan = self.planner.generate(content, progress_callback=report_progress)
            plan = self._attach_resource(plan)

            self._update_plan_job_progress(
                job_id,
                75,
                "saving",
                "Saving presentation plan",
            )
            self.repository.save_plan(plan)
            self._save_resource(plan)
            job.plan_id = plan.id
            self._save_plan_job(job)
            if job.mode == "source_deck" and job.source_material_id and not plan.fallback_reason:
                self._narration_checkpoint_path(content.id, job.source_material_id).unlink(
                    missing_ok=True
                )

            if job.prepare_question_bank:
                if pause_event.is_set():
                    raise PresentationPlanPaused("Presentation planning paused")
                self._update_plan_job_progress(
                    job_id,
                    80,
                    "preparing_questions",
                    "Preparing student questions and teacher answers",
                )
                self._prepare_question_bank(content, plan)
                if pause_event.is_set():
                    raise PresentationPlanPaused("Presentation planning paused")

            job.status = PresentationPlanJobStatus.SUCCEEDED
            job.progress = 100
            job.step = "completed"
            job.message = "Presentation plan generation completed"
            job.plan_id = plan.id
            job.updated_at = utc_now()
            self._save_plan_job(job)
        except PresentationPlanPaused:
            job = self.get_plan_job(job_id)
            job.status = PresentationPlanJobStatus.PAUSED
            job.step = "paused"
            job.message = "已暂停，完成的 Segment 讲稿已保存"
            job.error = None
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
        existing = self.question_bank_repository.list_for_plan(plan.id)
        completed_slide_ids = {item.slide_id for item in existing}
        if len(completed_slide_ids) >= len(plan.slides):
            return
        for items in self.question_bank_generator.generate_batches(
            content,
            plan,
            completed_slide_ids=completed_slide_ids,
        ):
            self.question_bank_repository.append_for_plan(items)

    def _save_plan_job(self, job: PresentationPlanJob) -> None:
        with self._plan_job_lock:
            self._plan_jobs[job.id] = job.model_copy(deep=True)
        self._write_json_atomic(
            self._plan_jobs_dir / f"{job.id}.json",
            job.model_dump(mode="json"),
        )

    def _narration_checkpoint_path(self, content_id: str, material_id: str) -> Path:
        return (
            self.data_dir
            / "runtime"
            / "presentation_narration"
            / f"{content_id}_{material_id}.json"
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _load_narration_checkpoint(self, content, pages, material_id: str):
        path = self._narration_checkpoint_path(content.id, material_id)
        expected = {
            "version": 1,
            "content_id": content.id,
            "content_updated_at": content.updated_at.isoformat(),
            "material_id": material_id,
            "pages": [page.page_no for page in pages],
            "provider": getattr(self.planner.llm, "name", None),
            "model": getattr(self.planner.llm, "model", None),
            "narration_prompt_sha256": hashlib.sha256(
                self.planner.source_narration_prompt_path.read_bytes()
            ).hexdigest(),
        }
        payload = {**expected, "segments": {}}
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if all(stored.get(key) == value for key, value in expected.items()):
                    payload = stored
            except (OSError, ValueError):
                pass
        completed = {}
        for key, value in payload.get("segments", {}).items():
            try:
                completed[key] = SourceSlideNarrationBatch.model_validate(value)
            except ValueError:
                continue
        payload["segments"] = {
            key: value.model_dump(mode="json") for key, value in completed.items()
        }
        return completed, payload

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

    def update_slide_script(
        self, plan_id: str, slide_id: str, speaker_script: str
    ) -> PresentationPlan:
        plan = self.get_plan(plan_id)
        if not any(slide.id == slide_id for slide in plan.slides):
            raise HTTPException(404, "Presentation slide not found")
        slides = [
            slide.model_copy(
                update={
                    "speaker_script": speaker_script,
                    "speaker_script_source": "teacher_override",
                }
            )
            if slide.id == slide_id
            else slide
            for slide in plan.slides
        ]
        plan = plan.model_copy(update={"slides": slides, "updated_at": utc_now()})
        self.repository.save_plan(plan)
        return plan

    def get_resource(self, plan_id: str) -> PresentationResource:
        plan = self.get_plan(plan_id)
        resource = self.repository.get_resource_for_plan(plan_id)
        if not resource:
            plan = self._attach_resource(plan)
            self.repository.save_plan(plan)
            self._save_resource(plan)
            resource = self.repository.get_resource_for_plan(plan_id)
        if not resource:
            raise HTTPException(404, "Presentation resource not found")
        if resource.source_material_id:
            material = self.materials.get(resource.source_material_id)
            reasons = []
            if resource.source_file_hash != material.file_hash:
                reasons.append("source_file_hash_changed")
            if resource.source_page_count != material.page_count:
                reasons.append("source_page_count_changed")
            if reasons:
                resource = resource.model_copy(
                    update={
                        "is_stale": True,
                        "stale_reason": ",".join(reasons),
                    }
                )
        slides = []
        for slide in resource.slides:
            image_url = None
            if slide.kind == "source" and resource.source_material_id and slide.source_page_no:
                image_url = (
                    f"/api/v1/materials/{resource.source_material_id}/pages/"
                    f"{slide.source_page_no}/image"
                )
            elif slide.kind == "generated" and resource.artifact_id and slide.artifact_slide_no:
                image_url = (
                    f"/api/v1/ppt-artifacts/{resource.artifact_id}/slides/"
                    f"{slide.artifact_slide_no}/image"
                )
            slides.append(slide.model_copy(update={"image_url": image_url}))
        return resource.model_copy(update={"slides": slides})

    def _attach_resource(self, plan: PresentationPlan) -> PresentationPlan:
        return plan.model_copy(
            update={
                "presentation_resource_id": (
                    plan.presentation_resource_id or f"presentation_resource_{uuid4().hex[:12]}"
                )
            }
        )

    @staticmethod
    def _validate_source_deck_plan(plan: PresentationPlan, pages: list) -> None:
        expected = [page.page_no for page in sorted(pages, key=lambda item: item.page_no)]
        actual = [slide.source_page_no for slide in plan.slides]
        if plan.mode != "source_deck":
            raise ValueError("Source deck plan must use source_deck mode")
        if actual != expected:
            raise ValueError(
                f"Source deck page mapping mismatch: expected {expected}, got {actual}"
            )
        if any(slide.source_kind != "source" for slide in plan.slides):
            raise ValueError("Every source deck slide must reference a source page")

    def _save_resource(self, plan: PresentationPlan, artifact: PPTArtifact | None = None) -> None:
        material = self.materials.get(plan.source_material_id) if plan.source_material_id else None
        kind = plan.mode if plan.mode in {"source_deck", "paper_deck"} else "generated_artifact"
        existing = self.repository.get_resource_for_plan(plan.id)
        resource = PresentationResource(
            id=plan.presentation_resource_id
            or (existing.id if existing else f"presentation_resource_{uuid4().hex[:12]}"),
            presentation_plan_id=plan.id,
            kind=kind,
            source_material_id=plan.source_material_id,
            artifact_id=artifact.id if artifact else (existing.artifact_id if existing else None),
            source_file_hash=(
                existing.source_file_hash if existing else material.file_hash if material else None
            ),
            source_page_count=(
                existing.source_page_count
                if existing
                else material.page_count
                if material
                else None
            ),
            slides=[
                PresentationSlideResource(
                    slide_id=slide.id,
                    order=slide.order,
                    kind=slide.source_kind,
                    source_page_no=slide.source_page_no,
                    artifact_slide_no=slide.order if slide.source_kind == "generated" else None,
                )
                for slide in plan.slides
            ],
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
        )
        self.repository.save_resource(resource)

    def diagnose_plan(self, plan_id: str) -> PresentationPlanDiagnosis:
        plan = self.get_plan(plan_id)
        content = self.contents.get(plan.content_id)
        llm = self.planner.llm
        return diagnose_presentation_plan(
            plan,
            content,
            llm_configured=llm is not None,
            provider=getattr(llm, "name", "none") if llm else "none",
            model=getattr(llm, "model", None) if llm else None,
        )

    def get_plan_for_content(self, content_id: str) -> PresentationPlan:
        self.contents.get(content_id)
        plan = self.repository.get_plan_for_content(content_id)
        if not plan:
            raise HTTPException(404, "Presentation plan not found")
        return plan

    def list_ppt_themes(self):
        return list_presentation_themes()

    def list_plan_summaries(self):
        return self.repository.list_plan_summaries()

    def create_ppt_job(
        self,
        presentation_plan_id: str,
        *,
        theme_id: str | None = None,
    ) -> PPTGenerationJob:
        plan = self.get_plan(presentation_plan_id)
        if plan.mode == "source_deck":
            raise HTTPException(409, "Source deck plans use the uploaded PPT directly")
        resource = self.get_resource(plan.id)
        if resource.is_stale:
            raise HTTPException(
                409,
                f"Presentation source is stale: {resource.stale_reason}",
            )
        try:
            theme = get_presentation_theme(theme_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = PPTGenerationJob(
            id=f"ppt_job_{uuid4().hex[:12]}",
            presentation_plan_id=plan.id,
            theme_id=theme.id,
        )
        self.repository.save_job(job)
        return job

    def pause_ppt_job(self, job_id: str) -> PPTGenerationJob:
        job = self.get_ppt_job(job_id)
        if job.status not in {
            PPTGenerationStatus.QUEUED,
            PPTGenerationStatus.RUNNING,
            PPTGenerationStatus.WAITING_FOR_SKILL,
        }:
            raise HTTPException(409, "Only active PPT jobs can be paused")
        self._ppt_pause_events.setdefault(job_id, Event()).set()
        job.status = PPTGenerationStatus.PAUSED
        job.error = None
        job.updated_at = utc_now()
        self.repository.save_job(job)
        return job

    def resume_ppt_job(self, job_id: str) -> PPTGenerationJob:
        job = self.get_ppt_job(job_id)
        if job.status != PPTGenerationStatus.PAUSED:
            raise HTTPException(409, "Only paused PPT jobs can be resumed")
        self._ppt_pause_events[job_id] = Event()
        job.status = PPTGenerationStatus.QUEUED
        job.updated_at = utc_now()
        self.repository.save_job(job)
        return job

    def discard_ppt_job(self, job_id: str) -> None:
        job = self.get_ppt_job(job_id)
        if job.status == PPTGenerationStatus.RUNNING:
            raise HTTPException(409, "Pause the job before discarding it")
        output_dir = self.data_dir / "generated" / "presentations" / job.id
        import shutil

        shutil.rmtree(output_dir, ignore_errors=True)
        self.repository.delete_job(job.id)
        self._ppt_pause_events.pop(job.id, None)

    def run_ppt_job(self, job_id: str) -> None:
        pause_event = self._ppt_pause_events.setdefault(job_id, Event())
        job = self.repository.get_job(job_id)
        if not job:
            return
        plan = self.get_plan(job.presentation_plan_id)
        theme = get_presentation_theme(job.theme_id)
        job.status = PPTGenerationStatus.RUNNING
        job.progress = 0.2
        job.updated_at = utc_now()
        self.repository.save_job(job)
        try:
            saved_artifact = self.repository.get_artifact_for_job(job.id)
            if saved_artifact:
                artifact = saved_artifact
            else:
                artifact = self.ppt_adapter.prepare_request(
                    plan=plan,
                    job_id=job.id,
                    output_dir=self.data_dir / "generated" / "presentations" / job.id,
                    theme=theme,
                )
                self.repository.save_artifact(artifact)
                self._save_resource(plan, artifact)
            if pause_event.is_set():
                job.artifact_id = artifact.id
                job.status = PPTGenerationStatus.PAUSED
                job.updated_at = utc_now()
                self.repository.save_job(job)
                return
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
        if job.status == PPTGenerationStatus.RUNNING and job_id not in self._ppt_pause_events:
            job.status = PPTGenerationStatus.PAUSED
            job.updated_at = utc_now()
            self.repository.save_job(job)
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
