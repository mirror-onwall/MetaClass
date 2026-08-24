import hashlib
from pathlib import Path
from threading import Event, RLock
from typing import Literal, TypeVar
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel

from metaclass.core.schemas import utc_now
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.schemas import MaterialType
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.paper_workflow.orchestrator import PaperWorkflowOrchestrator
from metaclass.modules.paper_workflow.paper_deck import PaperDeckBuilder
from metaclass.modules.paper_workflow.providers.base import PaperWorkflowPaused
from metaclass.modules.paper_workflow.repository import PaperWorkflowRepository
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperDeckCourseResult,
    PaperWorkflowCheckpoint,
    PaperWorkflowJob,
    PaperWorkflowRequest,
    PaperWorkflowSettings,
    PaperWorkflowStage,
    PaperWorkflowStatus,
    PaperWorkflowStrategy,
    PresentationOutline,
    RequiredWorkflowInput,
    SlideEvidence,
)
from metaclass.modules.paper_workflow.source_bundle import PaperSourceBundleBuilder
from metaclass.modules.paper_workflow.stage_cache import PaperWorkflowCheckpointStore
from metaclass.modules.presentation.service import PresentationService

ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)


class PaperWorkflowService:
    """Synchronous application service for the first paper-workflow API slice."""

    def __init__(
        self,
        data_dir: Path,
        repository: PaperWorkflowRepository,
        materials: MaterialService,
        orchestrator: PaperWorkflowOrchestrator,
        contents: ContentService | None = None,
        presentations: PresentationService | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.materials = materials
        self.orchestrator = orchestrator
        self.contents = contents
        self.presentations = presentations
        self.source_bundles = PaperSourceBundleBuilder(materials)
        self._lock = RLock()
        self._pause_events: dict[str, Event] = {}
        self.checkpoints = PaperWorkflowCheckpointStore(data_dir)
        self._restore_interrupted_jobs()

    def _restore_interrupted_jobs(self) -> None:
        for job in self.repository.list_jobs():
            self._pause_events[job.id] = Event()
            if job.status != PaperWorkflowStatus.RUNNING:
                continue
            request = self._require_request(job.id)
            checkpoint = self._load_checkpoint(job.id, request)
            job.status = PaperWorkflowStatus.PAUSED
            job.error = None
            job.updated_at = utc_now()
            self._persist(job, request, checkpoint)

    def create(self, request: PaperWorkflowRequest) -> PaperWorkflowJob:
        material = self.materials.get(request.material_id)
        if material.file_type != MaterialType.PDF:
            raise HTTPException(422, "Paper workflow requires a PDF material")
        selected = self._select_strategy(request.strategy)
        missing_nature_fields = self._missing_nature_fields(request, selected)
        job = PaperWorkflowJob(
            id=f"paper_job_{uuid4().hex[:12]}",
            source_material_id=request.material_id,
            strategy_requested=request.strategy,
            strategy_selected=selected,
            status=(
                PaperWorkflowStatus.WAITING_FOR_INPUT
                if missing_nature_fields
                else PaperWorkflowStatus.QUEUED
            ),
            required_input=(
                RequiredWorkflowInput(
                    reason="nature_paper2ppt_requires_presentation_context",
                    fields=missing_nature_fields,
                )
                if missing_nature_fields
                else None
            ),
        )
        checkpoint = PaperWorkflowCheckpoint(
            job_id=job.id,
            request_hash=self._request_hash(request),
        )
        with self._lock:
            self._pause_events[job.id] = Event()
            self._persist(job, request, checkpoint)
        return job

    def get(self, job_id: str) -> PaperWorkflowJob:
        job = self.repository.get_job(job_id)
        if not job:
            raise HTTPException(404, "Paper workflow job not found")
        return job

    def provide_input(
        self,
        job_id: str,
        settings: PaperWorkflowSettings,
    ) -> PaperWorkflowJob:
        """Complete a deferred Nature request and queue the same durable Job."""
        with self._lock:
            job = self.get(job_id)
            request = self._require_request(job.id)
            if job.strategy_selected != PaperWorkflowStrategy.NATURE_PAPER2PPT:
                raise HTTPException(409, "Paper workflow does not require Nature settings")
            unchanged = (
                request.duration_minutes == settings.duration_minutes
                and request.audience == settings.audience
            )
            if job.status != PaperWorkflowStatus.WAITING_FOR_INPUT:
                if unchanged:
                    return job
                raise HTTPException(
                    409,
                    f"Cannot update paper workflow input in {job.status.value} state",
                )
            updated_request = PaperWorkflowRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "duration_minutes": settings.duration_minutes,
                    "audience": settings.audience,
                }
            )
            checkpoint = self._load_checkpoint(job.id, request)
            checkpoint.request_hash = self._request_hash(updated_request)
            checkpoint.version += 1
            checkpoint.updated_at = utc_now()
            job.status = PaperWorkflowStatus.QUEUED
            job.required_input = None
            job.error = None
            job.updated_at = utc_now()
            self._pause_events[job.id] = Event()
            self._persist(job, updated_request, checkpoint)
            return job

    def run(self, job_id: str) -> PaperWorkflowJob:
        with self._lock:
            job = self.get(job_id)
            if job.status == PaperWorkflowStatus.SUCCEEDED:
                return job
            if job.status == PaperWorkflowStatus.RUNNING:
                return job
            if job.status == PaperWorkflowStatus.WAITING_FOR_REVIEW:
                return job
            if job.status in {
                PaperWorkflowStatus.CANCELED,
                PaperWorkflowStatus.PAUSED,
                PaperWorkflowStatus.WAITING_FOR_INPUT,
            }:
                raise HTTPException(409, f"Cannot run paper workflow in {job.status.value} state")
            request = self._require_request(job.id)
            checkpoint = self._load_checkpoint(job.id, request)
            pause_event = self._pause_events.setdefault(job.id, Event())
            pause_event.clear()
            job.status = PaperWorkflowStatus.RUNNING
            job.stage = PaperWorkflowStage.PREPARE_SOURCE
            job.progress = max(job.progress, 0.01)
            job.error = None
            job.updated_at = utc_now()
            self._persist(job, request, checkpoint)

        try:
            workspace = self.data_dir / "runtime" / "paper_workflows" / job.id
            resolved_request = self._resolved_request(request)
            self._write_resolved_request(workspace, resolved_request)
            source_bundle = self.source_bundles.build(
                material_id=request.material_id,
                workspace=workspace,
            )
            checkpoint.source_bundle_hash = self.source_bundles.bundle_hash(
                source_bundle,
                workspace,
            )
            checkpoint.version += 1
            checkpoint.updated_at = utc_now()
            self._persist(job, request, checkpoint)
            provider_name = self._provider_name(job)
            if provider_name not in job.provider_attempts:
                job.provider_attempts.append(provider_name)
                self._persist(job, request, checkpoint)

            def report_progress(progress: float, stage: str) -> None:
                current = self.get(job.id)
                if pause_event.is_set():
                    raise PaperWorkflowPaused("Paper workflow paused")
                current.progress = min(max(progress, current.progress), 0.99)
                try:
                    current.stage = PaperWorkflowStage(stage)
                except ValueError:
                    pass
                current.updated_at = utc_now()
                self._persist(current, request, checkpoint)

            def persist_checkpoint(updated: PaperWorkflowCheckpoint) -> None:
                current = self.get(job.id)
                current.updated_at = utc_now()
                self._persist(current, request, updated)

            provider_result = self.orchestrator.run(
                job_id=job.id,
                provider_name=provider_name,
                request=request,
                source_bundle=source_bundle,
                checkpoint=checkpoint,
                report_progress=report_progress,
                is_pause_requested=pause_event.is_set,
                persist_checkpoint=persist_checkpoint,
            )
            if pause_event.is_set():
                raise PaperWorkflowPaused("Paper workflow paused")
            if isinstance(provider_result, FigureCatalog):
                self._mark_figures_ready(job.id, request, checkpoint)
            elif isinstance(provider_result, PaperAnalysis):
                self._mark_analysis_ready(job.id, request, checkpoint)
            else:
                self._complete(job.id, request, checkpoint, provider_result)
        except PaperWorkflowPaused:
            self._mark_paused(job.id, request, checkpoint)
        except Exception as exc:  # noqa: BLE001 - provider failures become durable job state
            self._mark_failed(job.id, request, checkpoint, exc)
        return self.get(job.id)

    def pause(self, job_id: str) -> PaperWorkflowJob:
        with self._lock:
            job = self.get(job_id)
            if job.status not in {PaperWorkflowStatus.QUEUED, PaperWorkflowStatus.RUNNING}:
                raise HTTPException(409, "Only queued or running paper workflows can be paused")
            self._pause_events.setdefault(job.id, Event()).set()
            request = self._require_request(job.id)
            job.status = PaperWorkflowStatus.PAUSED
            job.updated_at = utc_now()
            self._persist(job, request, self._load_checkpoint(job.id, request))
            return job

    def resume(self, job_id: str) -> PaperWorkflowJob:
        with self._lock:
            job = self.get(job_id)
            if job.status == PaperWorkflowStatus.QUEUED:
                return job
            if job.status not in {PaperWorkflowStatus.PAUSED, PaperWorkflowStatus.FAILED}:
                raise HTTPException(409, "Only paused or failed paper workflows can be resumed")
            self._pause_events[job.id] = Event()
            request = self._require_request(job.id)
            job.status = PaperWorkflowStatus.QUEUED
            job.error = None
            job.updated_at = utc_now()
            self._persist(job, request, self._load_checkpoint(job.id, request))
            return job

    def result(self, job_id: str) -> PaperArtifactBundle:
        job = self.get(job_id)
        if job.status == PaperWorkflowStatus.FAILED:
            raise HTTPException(422, job.error or "Paper workflow failed")
        if job.status != PaperWorkflowStatus.SUCCEEDED:
            raise HTTPException(409, "Paper workflow is not finished")
        bundle = self.repository.get_bundle_for_job(job.id)
        if not bundle:
            raise HTTPException(500, "Paper workflow result metadata is missing")
        return bundle

    def create_paper_deck_course(self, job_id: str) -> PaperDeckCourseResult:
        """Parse the final deck and create its evidence-aware classroom plan."""
        if self.contents is None or self.presentations is None:
            raise HTTPException(503, "Paper deck classroom services are unavailable")
        job = self.get(job_id)
        if job.status != PaperWorkflowStatus.SUCCEEDED:
            raise HTTPException(409, "Paper workflow must succeed before creating a paper deck")
        bundle = self.result(job_id)
        if not bundle.derived_material_id:
            raise HTTPException(500, "Paper artifact bundle has no derived material")
        deck_material = self.materials.get(bundle.derived_material_id)
        pages = self.materials.pages(deck_material.id)
        if not pages:
            pages = self.materials.parse(deck_material.id)

        root = (self.data_dir / bundle.root_path).resolve()
        root.relative_to(self.data_dir.resolve())
        outline = PresentationOutline.model_validate_json(
            (root / "presentation_outline.json").read_text(encoding="utf-8")
        )
        analysis = PaperAnalysis.model_validate_json(
            (root / "paper_analysis.json").read_text(encoding="utf-8")
        )
        evidence = SlideEvidence.model_validate_json(
            (root / "slide_evidence.json").read_text(encoding="utf-8")
        )
        request = self._require_request(job.id)
        content, plan = PaperDeckBuilder().build(
            job_id=job.id,
            bundle=bundle,
            deck_material_id=deck_material.id,
            source_paper_material_id=job.source_material_id,
            pages=pages,
            analysis=analysis,
            outline=outline,
            evidence=evidence,
            speaker_notes_path=root / "speaker_notes.json",
            audience=request.audience or "具备基础专业背景的高校学生和研究生",
        )
        persisted_content = self.contents.save_paper_deck_content(content)
        if persisted_content.id != plan.content_id:
            plan = plan.model_copy(update={"content_id": persisted_content.id})
        persisted_plan = self.presentations.save_paper_deck_plan(plan)
        return PaperDeckCourseResult(
            paper_job_id=job.id,
            derived_material_id=deck_material.id,
            source_paper_material_id=job.source_material_id,
            artifact_bundle_id=bundle.id,
            content_id=persisted_content.id,
            presentation_plan_id=persisted_plan.id,
        )

    def analysis(self, job_id: str) -> PaperAnalysis:
        job = self.get(job_id)
        checkpoint = self.checkpoints.load(job.id) or self.repository.get_checkpoint(job.id)
        stage = checkpoint.stages.get(ComposedStage.ANALYSIS) if checkpoint else None
        if not stage or stage.status.value != "succeeded":
            raise HTTPException(409, "Paper analysis is not finished")
        relative = stage.outputs.get("analysis_json")
        if not relative:
            raise HTTPException(500, "Paper analysis checkpoint output is missing")
        workspace = (self.data_dir / "runtime" / "paper_workflows" / job.id).resolve()
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace)
            return PaperAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "Paper analysis artifact is invalid") from exc

    def figures(self, job_id: str) -> FigureCatalog:
        job = self.get(job_id)
        checkpoint = self.checkpoints.load(job.id) or self.repository.get_checkpoint(job.id)
        stage = checkpoint.stages.get(ComposedStage.FIGURES) if checkpoint else None
        if not stage or stage.status.value != "succeeded":
            raise HTTPException(409, "Paper figure catalog is not finished")
        relative = stage.outputs.get("figures_json")
        if not relative:
            raise HTTPException(500, "Paper figure checkpoint output is missing")
        workspace = (self.data_dir / "runtime" / "paper_workflows" / job.id).resolve()
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace)
            return FigureCatalog.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "Paper figure catalog is invalid") from exc

    def outline(self, job_id: str) -> PresentationOutline:
        return self._stage_artifact(
            job_id,
            stage_name=ComposedStage.OUTLINE,
            output_name="presentation_outline",
            model=PresentationOutline,
            label="Paper presentation outline",
        )

    def slide_evidence(self, job_id: str) -> SlideEvidence:
        return self._stage_artifact(
            job_id,
            stage_name=ComposedStage.OUTLINE,
            output_name="slide_evidence",
            model=SlideEvidence,
            label="Paper slide evidence",
        )

    def _stage_artifact(
        self,
        job_id: str,
        *,
        stage_name: ComposedStage,
        output_name: str,
        model: type[ArtifactModel],
        label: str,
    ) -> ArtifactModel:
        job = self.get(job_id)
        checkpoint = self.checkpoints.load(job.id) or self.repository.get_checkpoint(job.id)
        stage = checkpoint.stages.get(stage_name) if checkpoint else None
        if not stage or stage.status.value != "succeeded":
            raise HTTPException(409, f"{label} is not finished")
        relative = stage.outputs.get(output_name)
        if not relative:
            raise HTTPException(500, f"{label} checkpoint output is missing")
        workspace = (self.data_dir / "runtime" / "paper_workflows" / job.id).resolve()
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace)
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"{label} artifact is invalid") from exc

    def _mark_figures_ready(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
    ) -> None:
        job = self.get(job_id)
        job.status = PaperWorkflowStatus.WAITING_FOR_REVIEW
        job.stage = PaperWorkflowStage.PREPARE_FIGURES
        job.progress = max(job.progress, 0.59)
        job.error = None
        job.updated_at = utc_now()
        self._persist(job, request, checkpoint)

    def _mark_analysis_ready(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
    ) -> None:
        job = self.get(job_id)
        job.status = PaperWorkflowStatus.WAITING_FOR_REVIEW
        job.stage = PaperWorkflowStage.ANALYZE_PAPER
        job.progress = max(job.progress, 0.34)
        job.error = None
        job.updated_at = utc_now()
        self._persist(job, request, checkpoint)

    def _complete(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
        bundle: PaperArtifactBundle,
    ) -> None:
        if bundle.job_id != job_id or bundle.source_material_id != request.material_id:
            raise ValueError("Provider returned an artifact bundle for a different workflow")
        if bundle.validation_status != "passed":
            raise ValueError("Provider artifact bundle must pass validation before completion")
        if not bundle.derived_material_id:
            raise ValueError("Provider artifact bundle requires derived_material_id")
        self.repository.save_bundle(bundle)
        job = self.get(job_id)
        job = PaperWorkflowJob.model_validate(
            {
                **job.model_dump(mode="python"),
                "status": PaperWorkflowStatus.SUCCEEDED,
                "stage": PaperWorkflowStage.COMPLETED,
                "progress": 1,
                "artifact_bundle_id": bundle.id,
                "derived_material_id": bundle.derived_material_id,
                "checkpoint_version": checkpoint.version,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        self._persist(job, request, checkpoint)

    def _mark_paused(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
    ) -> None:
        job = self.get(job_id)
        job.status = PaperWorkflowStatus.PAUSED
        job.error = None
        job.updated_at = utc_now()
        self._persist(job, request, checkpoint)

    def _mark_failed(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
        error: Exception,
    ) -> None:
        job = self.get(job_id)
        job = PaperWorkflowJob.model_validate(
            {
                **job.model_dump(mode="python"),
                "status": PaperWorkflowStatus.FAILED,
                "error": str(error) or error.__class__.__name__,
                "updated_at": utc_now(),
            }
        )
        self._persist(job, request, checkpoint)

    def _load_checkpoint(
        self,
        job_id: str,
        request: PaperWorkflowRequest,
    ) -> PaperWorkflowCheckpoint:
        checkpoint = self.checkpoints.load(job_id) or self.repository.get_checkpoint(job_id)
        if checkpoint is None:
            checkpoint = PaperWorkflowCheckpoint(
                job_id=job_id,
                request_hash=self._request_hash(request),
            )
        return checkpoint

    def _persist(
        self,
        job: PaperWorkflowJob,
        request: PaperWorkflowRequest,
        checkpoint: PaperWorkflowCheckpoint,
    ) -> None:
        job.checkpoint_version = checkpoint.version
        self.checkpoints.save(checkpoint)
        self.repository.save_job(job, request, checkpoint)

    def _require_request(self, job_id: str) -> PaperWorkflowRequest:
        request = self.repository.get_request(job_id)
        if not request:
            raise HTTPException(500, "Paper workflow request metadata is missing")
        return request

    @staticmethod
    def _select_strategy(strategy: PaperWorkflowStrategy) -> PaperWorkflowStrategy:
        return (
            PaperWorkflowStrategy.COMPOSED_SKILLS
            if strategy == PaperWorkflowStrategy.AUTO
            else strategy
        )

    @staticmethod
    def _provider_name(job: PaperWorkflowJob) -> str:
        if not job.strategy_selected:
            raise ValueError("Paper workflow strategy has not been selected")
        return job.strategy_selected.value

    @staticmethod
    def _missing_nature_fields(
        request: PaperWorkflowRequest,
        selected: PaperWorkflowStrategy,
    ) -> list[Literal["duration_minutes", "audience"]]:
        if selected != PaperWorkflowStrategy.NATURE_PAPER2PPT:
            return []
        missing: list[Literal["duration_minutes", "audience"]] = []
        if request.duration_minutes is None:
            missing.append("duration_minutes")
        if not request.audience:
            missing.append("audience")
        return missing

    @staticmethod
    def _request_hash(request: PaperWorkflowRequest) -> str:
        payload = request.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolved_request(request: PaperWorkflowRequest) -> PaperWorkflowRequest:
        return PaperWorkflowRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "duration_minutes": request.duration_minutes or 15,
                "audience": request.audience or "具备基础专业背景的高校学生和研究生",
            }
        )

    @staticmethod
    def _write_resolved_request(
        workspace: Path,
        request: PaperWorkflowRequest,
    ) -> Path:
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "resolved_request.json"
        temporary = workspace / ".resolved_request.json.tmp"
        temporary.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return path
