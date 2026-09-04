import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from metaclass.modules.paper_workflow.schemas import (
    ArtifactFile,
    FigureCatalog,
    PaperArtifactBundle,
    PaperWorkflowRequest,
)
from metaclass.modules.paper_workflow.validators import PaperArtifactValidator

ValidationMode = Literal["strict", "diagnostic"]


@dataclass(frozen=True)
class StagedPaperArtifacts:
    """The Stage outputs consumed by the provider-independent normalizer."""

    provider: str
    root_path: Path


class PaperArtifactNormalizer:
    """Copy Stage outputs into the canonical, portable final artifact tree.

    This component only adapts formats and paths. It never asks a model to add,
    summarize, repair, or otherwise create paper content.
    """

    _FILES = (
        (
            "presentation",
            "stages/04_generation/output/presentation.pptx",
            "presentation.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("analysis", "stages/01_analysis/output/paper_analysis.json", "paper_analysis.json", "application/json"),
        (
            "outline",
            "stages/03_outline/output/presentation_outline.json",
            "presentation_outline.json",
            "application/json",
        ),
        (
            "slide_evidence",
            "stages/04_generation/output/slide_evidence.json",
            "slide_evidence.json",
            "application/json",
        ),
        (
            "speaker_notes",
            "stages/04_generation/output/speaker_notes.json",
            "speaker_notes.json",
            "application/json",
        ),
        (
            "generation_report",
            "stages/04_generation/output/execution_report.json",
            "generation_report.json",
            "application/json",
        ),
        ("qa_report", "stages/04_generation/output/qa_report.json", "qa_report.json", "application/json"),
    )

    def __init__(self, validator: PaperArtifactValidator | None = None) -> None:
        self.validator = validator or PaperArtifactValidator()

    def normalize(
        self,
        *,
        job_id: str,
        source_material_id: str,
        request: PaperWorkflowRequest,
        raw: StagedPaperArtifacts,
        mode: ValidationMode = "strict",
    ) -> PaperArtifactBundle:
        del request  # normalization must not reinterpret user intent or paper facts
        workspace = raw.root_path.resolve()
        final = workspace / "final"
        temporary = workspace / f".final.{uuid4().hex}.tmp"
        temporary.mkdir(parents=True)
        try:
            for _, source_name, destination_name, _ in self._FILES:
                self._copy_file(workspace, source_name, temporary / destination_name)
            self._normalize_asset_manifest(workspace, temporary)

            errors = self.validator.validate_final_directory(temporary, mode="diagnostic")
            if errors and mode == "strict":
                raise ValueError("final artifact validation failed: " + "; ".join(errors))

            if final.exists():
                shutil.rmtree(final)
            os.replace(temporary, final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        file_specs = [
            (role, final / destination, media_type)
            for role, _, destination, media_type in self._FILES
        ]
        file_specs.insert(4, ("asset_manifest", final / "asset_manifest.json", "application/json"))
        catalog = FigureCatalog.model_validate_json(
            (final / "asset_manifest.json").read_text(encoding="utf-8")
        )
        file_specs.extend(
            ("asset", self._resolve_relative(final, figure.path), self._media_type(figure.path))
            for figure in catalog.figures
        )
        files = [
            ArtifactFile(
                role=role,
                path=path.relative_to(final).as_posix(),
                sha256=self._sha256(path),
                media_type=media_type,
            )
            for role, path, media_type in file_specs
        ]
        identity = hashlib.sha256(
            (job_id + "\0" + "\0".join(item.sha256 for item in files)).encode("utf-8")
        ).hexdigest()
        return PaperArtifactBundle(
            id=f"paper_bundle_{identity[:24]}",
            job_id=job_id,
            source_material_id=source_material_id,
            provider=raw.provider,
            root_path=final.relative_to(workspace.parent.parent.parent).as_posix(),
            files=files,
            validation_status="failed" if errors else "passed",
        )

    def _normalize_asset_manifest(self, workspace: Path, destination: Path) -> None:
        source_root = workspace / "stages/02_figures/output"
        catalog = FigureCatalog.model_validate_json(
            (source_root / "figures.json").read_text(encoding="utf-8")
        )
        assets = destination / "assets"
        assets.mkdir()
        normalized = []
        for figure in catalog.figures:
            source = self._resolve_relative(source_root, figure.path)
            target = assets / source.name
            shutil.copyfile(source, target)
            normalized.append(
                figure.model_copy(update={"path": f"assets/{target.name}"})
            )
        normalized_catalog = FigureCatalog(
            schema_version=catalog.schema_version,
            figures=normalized,
        )
        (destination / "asset_manifest.json").write_text(
            normalized_catalog.model_dump_json(indent=2), encoding="utf-8"
        )

    @classmethod
    def _copy_file(cls, root: Path, relative: str, destination: Path) -> None:
        source = cls._resolve_relative(root, relative)
        if not source.is_file():
            raise FileNotFoundError(f"required Stage artifact does not exist: {relative}")
        shutil.copyfile(source, destination)

    @staticmethod
    def _resolve_relative(root: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"artifact path must be relative and contained: {relative}")
        resolved = (root / Path(*pure.parts)).resolve()
        resolved.relative_to(root.resolve())
        return resolved

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _media_type(path: str) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
        }.get(Path(path).suffix.lower(), "application/octet-stream")
