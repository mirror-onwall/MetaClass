from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from metaclass.core.schemas import utc_now
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperSourceBundle,
    PaperWorkflowCheckpoint,
    PaperWorkflowRequest,
    StageCheckpoint,
    StageStatus,
)

T = TypeVar("T")

STAGE_PROGRESS = {
    ComposedStage.ANALYSIS: (0.10, "analyze_paper"),
    ComposedStage.FIGURES: (0.35, "prepare_figures"),
    ComposedStage.OUTLINE: (0.60, "plan_presentation"),
    ComposedStage.GENERATION: (0.80, "generate_deck"),
}


class PaperProviderNotReady(RuntimeError):
    """Raised when a provider is registered but its execution stages are not installed."""


class PaperWorkflowPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperProviderContext:
    job_id: str
    workspace: Path
    request: PaperWorkflowRequest
    source_bundle: PaperSourceBundle
    checkpoint: PaperWorkflowCheckpoint
    report_progress: Callable[[float, str], None]
    is_pause_requested: Callable[[], bool]
    persist_checkpoint: Callable[[PaperWorkflowCheckpoint], None]

    def raise_if_paused(self) -> None:
        if self.is_pause_requested():
            raise PaperWorkflowPaused("Paper workflow paused")

    def current_attempt(self, stage: ComposedStage) -> int:
        """Return the attempt already assigned by ``run_stage`` to the running stage."""
        checkpoint = self.checkpoint.stages.get(stage)
        if not checkpoint or checkpoint.status != StageStatus.RUNNING:
            raise RuntimeError(f"stage {stage.value} has no running checkpoint attempt")
        return checkpoint.attempt

    def run_stage(
        self,
        *,
        stage: ComposedStage,
        input_hash: str,
        skill_name: str,
        skill_version: str,
        prompt_version: str,
        execute: Callable[[], tuple[T, dict[str, str]]],
        validate_cached: Callable[[dict[str, str]], bool],
        restore: Callable[[dict[str, str]], T],
    ) -> T:
        """Run one checkpointed stage, or reuse its successful matching output."""
        existing = self.checkpoint.stages.get(stage)
        if (
            existing
            and existing.status == StageStatus.SUCCEEDED
            and existing.input_hash == input_hash
        ):
            try:
                if validate_cached(existing.outputs):
                    restored = restore(existing.outputs)
                    progress, workflow_stage = STAGE_PROGRESS[stage]
                    self.report_progress(progress, workflow_stage)
                    return restored
            except (KeyError, OSError, TypeError, ValueError):
                pass

        self.raise_if_paused()
        progress, workflow_stage = STAGE_PROGRESS[stage]
        self.report_progress(progress, workflow_stage)
        started_at = utc_now()
        running = StageCheckpoint(
            stage=stage,
            status=StageStatus.RUNNING,
            input_hash=input_hash,
            skill_name=skill_name,
            skill_version=skill_version,
            prompt_version=prompt_version,
            attempt=(existing.attempt if existing else 0) + 1,
            started_at=started_at,
        )
        self._save_stage(running)
        try:
            value, outputs = execute()
            self.raise_if_paused()
        except PaperWorkflowPaused:
            raise
        except Exception as exc:
            failed = StageCheckpoint(
                **{
                    **running.model_dump(mode="python"),
                    "status": StageStatus.FAILED,
                    "finished_at": utc_now(),
                    "validation": {"passed": False, "errors": [str(exc)]},
                    "updated_at": utc_now(),
                }
            )
            self._save_stage(failed)
            raise
        succeeded = StageCheckpoint(
            **{
                **running.model_dump(mode="python"),
                "status": StageStatus.SUCCEEDED,
                "finished_at": utc_now(),
                "outputs": outputs,
                "validation": {"passed": True, "errors": []},
                "updated_at": utc_now(),
            }
        )
        self._save_stage(succeeded)
        return value

    def _save_stage(self, stage: StageCheckpoint) -> None:
        self.checkpoint.stages[stage.stage] = stage
        self.checkpoint.version += 1
        self.checkpoint.updated_at = utc_now()
        self.persist_checkpoint(self.checkpoint)


class PaperPresentationProvider(Protocol):
    name: str

    def run(
        self, context: PaperProviderContext
    ) -> PaperArtifactBundle | PaperAnalysis | FigureCatalog: ...
