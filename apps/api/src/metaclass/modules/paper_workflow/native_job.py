"""Durable stage checkpoints for the native paper-deck classroom pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.paper_workflow.schemas import StageStatus


class NativePaperDeckStage(StrEnum):
    PREPARING_SOURCE = "preparing_source"
    ANALYZING_PAPER = "analyzing_paper"
    GENERATING_NATIVE_PAPER_DECK = "generating_native_paper_deck"
    VALIDATING_PDF = "validating_pdf"
    RECONCILING_SLIDES = "reconciling_slides"
    GROUNDING_EVIDENCE = "grounding_evidence"
    BUILDING_KNOWLEDGE_TREE = "building_knowledge_tree"
    GENERATING_NARRATION = "generating_narration"
    PLANNING_INTERACTIONS = "planning_interactions"
    REGISTERING_CLASSROOM = "registering_classroom"


NATIVE_PAPER_DECK_STAGE_ORDER: tuple[NativePaperDeckStage, ...] = tuple(
    NativePaperDeckStage
)


NATIVE_CHECKPOINT_FILENAMES: dict[NativePaperDeckStage, str] = {
    NativePaperDeckStage.PREPARING_SOURCE: "source.json",
    NativePaperDeckStage.ANALYZING_PAPER: "analysis.json",
    NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK: "native_deck.json",
    NativePaperDeckStage.VALIDATING_PDF: "pdf_validation.json",
    NativePaperDeckStage.RECONCILING_SLIDES: "slide_reconciliation.json",
    NativePaperDeckStage.GROUNDING_EVIDENCE: "evidence_grounding.json",
    NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE: "knowledge_tree.json",
    NativePaperDeckStage.GENERATING_NARRATION: "narration.json",
    NativePaperDeckStage.PLANNING_INTERACTIONS: "interaction.json",
    NativePaperDeckStage.REGISTERING_CLASSROOM: "classroom.json",
}


class NativeStageCheckpoint(SchemaModel):
    job_id: str = Field(min_length=1)
    stage: NativePaperDeckStage
    status: StageStatus = StageStatus.PENDING
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt: int = Field(default=0, ge=0)
    outputs: dict[str, str] = Field(default_factory=dict)
    affected_slide_ids: list[str] = Field(default_factory=list)
    validation: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_success(self) -> NativeStageCheckpoint:
        if self.status == StageStatus.SUCCEEDED and (
            self.output_hash is None or self.validation.get("passed") is not True
        ):
            raise ValueError("successful native checkpoint requires validated output hash")
        return self


class NativePaperDeckCheckpointStore:
    """One atomic JSON file per recovery boundary, plus dependency invalidation."""

    _slide_scoped: ClassVar[set[NativePaperDeckStage]] = {
        NativePaperDeckStage.RECONCILING_SLIDES,
        NativePaperDeckStage.GROUNDING_EVIDENCE,
        NativePaperDeckStage.GENERATING_NARRATION,
        NativePaperDeckStage.PLANNING_INTERACTIONS,
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "checkpoints"
        self._lock = RLock()

    def path_for(self, stage: NativePaperDeckStage) -> Path:
        return self.root / NATIVE_CHECKPOINT_FILENAMES[stage]

    def load(self, stage: NativePaperDeckStage) -> NativeStageCheckpoint | None:
        path = self.path_for(stage)
        if not path.is_file():
            return None
        checkpoint = NativeStageCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        if checkpoint.stage != stage:
            raise ValueError(f"checkpoint file {path.name} contains the wrong stage")
        return checkpoint

    def save(self, checkpoint: NativeStageCheckpoint) -> Path:
        path = self.path_for(checkpoint.stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = checkpoint.model_dump_json(indent=2)
        temporary: Path | None = None
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix=f".{path.stem}-",
                    suffix=".tmp", dir=path.parent, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink(missing_ok=True)
        return path

    def can_resume(self, stage: NativePaperDeckStage, *, input_hash: str) -> bool:
        checkpoint = self.load(stage)
        if not (
            checkpoint
            and checkpoint.status == StageStatus.SUCCEEDED
            and checkpoint.input_hash == input_hash
            and self._outputs_exist(checkpoint.outputs)
            and checkpoint.output_hash
        ):
            return False
        try:
            return self.hash_outputs(checkpoint.outputs) == checkpoint.output_hash
        except (OSError, ValueError):
            return False

    def invalidate_from(
        self,
        stage: NativePaperDeckStage,
        *,
        slide_ids: list[str] | None = None,
    ) -> list[NativePaperDeckStage]:
        """Invalidate a dependency suffix; preserve per-slide scope where it is safe."""
        start = NATIVE_PAPER_DECK_STAGE_ORDER.index(stage)
        invalidated: list[NativePaperDeckStage] = []
        for candidate in NATIVE_PAPER_DECK_STAGE_ORDER[start:]:
            checkpoint = self.load(candidate)
            if checkpoint is None:
                continue
            scoped = list(dict.fromkeys(slide_ids or [])) if candidate in self._slide_scoped else []
            self.save(
                checkpoint.model_copy(
                    update={
                        "status": StageStatus.PENDING,
                        "output_hash": None,
                        "affected_slide_ids": scoped,
                        "validation": {
                            "passed": False,
                            "reason": "dependency_invalidated",
                        },
                        "updated_at": utc_now(),
                    }
                )
            )
            invalidated.append(candidate)
        return invalidated

    def invalidate_narration(self, slide_ids: list[str] | None = None) -> list[NativePaperDeckStage]:
        return self.invalidate_from(NativePaperDeckStage.GENERATING_NARRATION, slide_ids=slide_ids)

    def invalidate_slide_regeneration(self, slide_ids: list[str]) -> list[NativePaperDeckStage]:
        if not slide_ids:
            raise ValueError("slide regeneration requires at least one slide id")
        # The native deck checkpoint remains valid: its directory is the source for a
        # targeted page repair. PDF validation and every semantic consumer are stale.
        return self.invalidate_from(NativePaperDeckStage.VALIDATING_PDF, slide_ids=slide_ids)

    @staticmethod
    def hash_payload(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def hash_inputs(
        self,
        *,
        metadata: dict[str, Any],
        paths: list[Path],
    ) -> str:
        """Hash stage metadata and the bytes of every upstream file."""
        digest = hashlib.sha256()
        digest.update(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        for path in sorted((item.resolve() for item in paths), key=str):
            path.relative_to(self.workspace)
            if not path.is_file():
                raise ValueError(f"native checkpoint input is missing: {path}")
            digest.update(str(path.relative_to(self.workspace)).encode("utf-8"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def hash_outputs(self, outputs: dict[str, str]) -> str:
        """Hash declared output identities and their current file bytes."""
        digest = hashlib.sha256()
        for name, value in sorted(outputs.items()):
            path = (self.workspace / value).resolve()
            path.relative_to(self.workspace)
            if not path.is_file():
                raise ValueError(f"native checkpoint output is missing: {value}")
            digest.update(name.encode("utf-8"))
            digest.update(value.encode("utf-8"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def _outputs_exist(self, outputs: dict[str, str]) -> bool:
        if not outputs:
            return False
        try:
            for value in outputs.values():
                path = (self.workspace / value).resolve()
                path.relative_to(self.workspace)
                if not path.is_file():
                    return False
        except (OSError, ValueError):
            return False
        return True
