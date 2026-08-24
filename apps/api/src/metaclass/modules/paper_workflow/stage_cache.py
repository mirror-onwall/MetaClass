import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from metaclass.modules.paper_workflow.schemas import PaperWorkflowCheckpoint


class StageCache:
    """Deterministic hashes and safe cache-path resolution for composed stages."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def input_hash(
        self,
        *,
        skill_version: str,
        prompt_version: str,
        schema_version: str,
        request_subset: dict[str, Any],
        input_files: list[Path],
    ) -> str:
        digest = hashlib.sha256()
        metadata = {
            "skill_version": skill_version,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "request_subset": request_subset,
        }
        digest.update(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for path in sorted(input_files, key=lambda item: str(item)):
            resolved = path.resolve()
            resolved.relative_to(self.workspace)
            digest.update(str(resolved.relative_to(self.workspace)).encode("utf-8"))
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def stage_directory(self, stage_name: str) -> Path:
        if not stage_name or not stage_name.replace("_", "").isalnum():
            raise ValueError("stage_name must contain only letters, numbers, and underscores")
        path = (self.workspace / "stages" / stage_name).resolve()
        path.relative_to(self.workspace)
        return path

    def outputs_exist(self, outputs: dict[str, str]) -> bool:
        """Return true only when every declared output is a file inside this workspace."""
        if not outputs:
            return False
        try:
            for path_value in outputs.values():
                resolved = (self.workspace / path_value).resolve()
                resolved.relative_to(self.workspace)
                if not resolved.is_file():
                    return False
        except (OSError, ValueError):
            return False
        return True


class PaperWorkflowCheckpointStore:
    """Durable per-job checkpoint files written with an atomic same-directory rename."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "runtime" / "paper_workflows"
        self._lock = RLock()

    def path_for(self, job_id: str) -> Path:
        if not job_id or not job_id.replace("_", "").isalnum():
            raise ValueError("invalid paper workflow job id")
        return self.root / job_id / "checkpoint.json"

    def load(self, job_id: str) -> PaperWorkflowCheckpoint | None:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        return PaperWorkflowCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, checkpoint: PaperWorkflowCheckpoint) -> Path:
        path = self.path_for(checkpoint.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = checkpoint.model_dump_json(indent=2)
        temporary_path: Path | None = None
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix=".checkpoint-",
                    suffix=".tmp",
                    dir=path.parent,
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, path)
            finally:
                if temporary_path and temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)
        return path
