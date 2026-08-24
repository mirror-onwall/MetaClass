import json
import re
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from metaclass.modules.paper_workflow.schemas import (
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperSourceBundle,
    PresentationOutline,
    SlideEvidence,
)


class PaperArtifactValidationError(ValueError):
    pass


class PaperArtifactValidator:
    """Cross-contract validation that cannot be expressed inside one Pydantic model."""

    def validate_analysis(
        self,
        source: PaperSourceBundle,
        analysis: PaperAnalysis,
    ) -> None:
        block_ids = {item.id for item in source.blocks}
        asset_ids = {item.id for item in source.assets}
        refs = [
            ref
            for collection in [
                analysis.claims,
                analysis.quantitative_results,
                analysis.limitations,
                analysis.figure_candidates,
            ]
            for item in collection
            for ref in item.source_refs
        ]
        for ref in refs:
            if ref.page_no > source.page_count:
                raise PaperArtifactValidationError(
                    f"source reference page {ref.page_no} exceeds paper page count"
                )
            if ref.block_id and ref.block_id not in block_ids:
                raise PaperArtifactValidationError(f"unknown source block: {ref.block_id}")
            if ref.asset_id and ref.asset_id not in asset_ids:
                raise PaperArtifactValidationError(f"unknown source asset: {ref.asset_id}")

    def validate_outline(
        self,
        analysis: PaperAnalysis,
        figures: FigureCatalog,
        outline: PresentationOutline,
        evidence: SlideEvidence,
    ) -> None:
        slide_ids = [item.id for item in outline.slides]
        if [item.slide_id for item in evidence.slides] != slide_ids:
            raise PaperArtifactValidationError(
                "slide evidence must cover every outline slide in presentation order"
            )
        claim_ids = {item.id for item in analysis.claims}
        figure_ids = {item.id for item in figures.figures}
        for entry in evidence.slides:
            unknown_claims = set(entry.claim_ids) - claim_ids
            unknown_assets = set(entry.asset_ids) - figure_ids
            if unknown_claims:
                raise PaperArtifactValidationError(
                    f"slide {entry.slide_id} references unknown claims: {sorted(unknown_claims)}"
                )
            if unknown_assets:
                raise PaperArtifactValidationError(
                    f"slide {entry.slide_id} references unknown figures: {sorted(unknown_assets)}"
                )

    def validate_bundle(self, bundle: PaperArtifactBundle) -> None:
        if bundle.validation_status != "passed":
            raise PaperArtifactValidationError("artifact bundle has not passed provider validation")

    def validate_final_directory(
        self,
        root: Path,
        *,
        mode: Literal["strict", "diagnostic"] = "strict",
    ) -> list[str]:
        """Validate a normalized delivery, optionally retaining it for diagnostics."""
        errors: list[str] = []
        required = {
            "presentation.pptx",
            "paper_analysis.json",
            "presentation_outline.json",
            "slide_evidence.json",
            "asset_manifest.json",
            "speaker_notes.json",
            "generation_report.json",
            "qa_report.json",
        }
        for name in sorted(required):
            if not (root / name).is_file():
                errors.append(f"missing required final artifact: {name}")

        parsed: dict[str, object] = {}
        contracts = {
            "paper_analysis.json": PaperAnalysis,
            "presentation_outline.json": PresentationOutline,
            "slide_evidence.json": SlideEvidence,
            "asset_manifest.json": FigureCatalog,
        }
        for name, contract in contracts.items():
            path = root / name
            if not path.is_file():
                continue
            try:
                parsed[name] = contract.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                errors.append(f"invalid {name}: {exc}")

        analysis = parsed.get("paper_analysis.json")
        figures = parsed.get("asset_manifest.json")
        outline = parsed.get("presentation_outline.json")
        evidence = parsed.get("slide_evidence.json")
        if all(item is not None for item in (analysis, figures, outline, evidence)):
            try:
                self.validate_outline(analysis, figures, outline, evidence)  # type: ignore[arg-type]
            except PaperArtifactValidationError as exc:
                errors.append(str(exc))

        if isinstance(figures, FigureCatalog):
            for figure in figures.figures:
                try:
                    asset = self._contained_path(root, figure.path)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if not asset.is_file():
                    errors.append(f"missing normalized asset: {figure.path}")

        for path in sorted(root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"invalid {path.name}: {exc}")
                continue
            for value in self._string_values(payload):
                if self._looks_absolute(value):
                    errors.append(f"absolute path in {path.name}: {value}")

        errors = list(dict.fromkeys(errors))
        if errors and mode == "strict":
            raise PaperArtifactValidationError("; ".join(errors))
        return errors

    @staticmethod
    def _contained_path(root: Path, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise ValueError(f"asset path must be relative: {relative}")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"asset path escapes final directory: {relative}") from exc
        return resolved

    @classmethod
    def _string_values(cls, value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from cls._string_values(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from cls._string_values(item)

    @staticmethod
    def _looks_absolute(value: str) -> bool:
        return bool(re.match(r"^(?:/|[A-Za-z]:[\\/]|file://)", value))

    @staticmethod
    def validate_no_forbidden_paths(
        paths: list[Path],
        *,
        forbidden_roots: list[Path],
    ) -> None:
        forbidden = [str(path.resolve()) for path in forbidden_roots]
        obsidian_absolute = re.compile(
            r"(?:/[^\s\"']*)*[/\\][^\s\"']*obsidian[^\s\"']*",
            re.IGNORECASE,
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            matched = next((root for root in forbidden if root and root in text), None)
            if matched:
                raise PaperArtifactValidationError(
                    f"analysis output contains forbidden absolute path: {matched}"
                )
            if obsidian_absolute.search(text):
                raise PaperArtifactValidationError(
                    "analysis output contains an absolute Obsidian path"
                )
