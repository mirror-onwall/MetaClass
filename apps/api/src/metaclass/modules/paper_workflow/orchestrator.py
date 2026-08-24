from pathlib import Path

from metaclass.modules.paper_workflow.providers.base import (
    PaperPresentationProvider,
    PaperProviderContext,
)
from metaclass.modules.paper_workflow.schemas import (
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperSourceBundle,
    PaperWorkflowCheckpoint,
    PaperWorkflowRequest,
)


class UnsupportedPaperProvider(ValueError):
    pass


class PaperWorkflowOrchestrator:
    def __init__(
        self,
        data_dir: Path,
        providers: list[PaperPresentationProvider],
    ) -> None:
        self.data_dir = data_dir
        self.providers = {provider.name: provider for provider in providers}

    def run(
        self,
        *,
        job_id: str,
        provider_name: str,
        request: PaperWorkflowRequest,
        source_bundle: PaperSourceBundle,
        checkpoint: PaperWorkflowCheckpoint,
        report_progress,
        is_pause_requested,
        persist_checkpoint,
    ) -> PaperArtifactBundle | PaperAnalysis | FigureCatalog:
        provider = self.providers.get(provider_name)
        if not provider:
            raise UnsupportedPaperProvider(f"Paper provider is not configured: {provider_name}")
        workspace = self.data_dir / "runtime" / "paper_workflows" / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        context = PaperProviderContext(
            job_id=job_id,
            workspace=workspace,
            request=request,
            source_bundle=source_bundle,
            checkpoint=checkpoint,
            report_progress=report_progress,
            is_pause_requested=is_pause_requested,
            persist_checkpoint=persist_checkpoint,
        )
        return provider.run(context)
