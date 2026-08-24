from fastapi import APIRouter

from metaclass.modules.paper_workflow.schemas import (
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperDeckCourseResult,
    PaperWorkflowJob,
    PaperWorkflowRequest,
    PaperWorkflowSettings,
    PresentationOutline,
    SlideEvidence,
)
from metaclass.modules.paper_workflow.service import PaperWorkflowService


def create_router(workflows: PaperWorkflowService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/paper-workflows", tags=["paper-workflows"])

    @router.post("", response_model=PaperWorkflowJob, status_code=201)
    def create_paper_workflow(request: PaperWorkflowRequest) -> PaperWorkflowJob:
        return workflows.create(request)

    @router.get("/{job_id}", response_model=PaperWorkflowJob)
    def get_paper_workflow(job_id: str) -> PaperWorkflowJob:
        return workflows.get(job_id)

    @router.patch("/{job_id}/request", response_model=PaperWorkflowJob)
    def provide_paper_workflow_input(
        job_id: str,
        settings: PaperWorkflowSettings,
    ) -> PaperWorkflowJob:
        return workflows.provide_input(job_id, settings)

    @router.post("/{job_id}/run", response_model=PaperWorkflowJob)
    def run_paper_workflow(job_id: str) -> PaperWorkflowJob:
        return workflows.run(job_id)

    @router.post("/{job_id}/pause", response_model=PaperWorkflowJob)
    def pause_paper_workflow(job_id: str) -> PaperWorkflowJob:
        return workflows.pause(job_id)

    @router.post("/{job_id}/resume", response_model=PaperWorkflowJob)
    def resume_paper_workflow(job_id: str) -> PaperWorkflowJob:
        return workflows.resume(job_id)

    @router.get("/{job_id}/result", response_model=PaperArtifactBundle)
    def get_paper_workflow_result(job_id: str) -> PaperArtifactBundle:
        return workflows.result(job_id)

    @router.post(
        "/{job_id}/create-paper-deck-course",
        response_model=PaperDeckCourseResult,
        status_code=201,
    )
    def create_paper_deck_course(job_id: str) -> PaperDeckCourseResult:
        return workflows.create_paper_deck_course(job_id)

    @router.get("/{job_id}/analysis", response_model=PaperAnalysis)
    def get_paper_workflow_analysis(job_id: str) -> PaperAnalysis:
        return workflows.analysis(job_id)

    @router.get("/{job_id}/figures", response_model=FigureCatalog)
    def get_paper_workflow_figures(job_id: str) -> FigureCatalog:
        return workflows.figures(job_id)

    @router.get("/{job_id}/outline", response_model=PresentationOutline)
    def get_paper_workflow_outline(job_id: str) -> PresentationOutline:
        return workflows.outline(job_id)

    @router.get("/{job_id}/slide-evidence", response_model=SlideEvidence)
    def get_paper_workflow_slide_evidence(job_id: str) -> SlideEvidence:
        return workflows.slide_evidence(job_id)

    return router
