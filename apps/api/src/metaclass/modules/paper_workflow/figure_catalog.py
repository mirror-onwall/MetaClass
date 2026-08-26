import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import fitz
from PIL import Image, UnidentifiedImageError

from metaclass.modules.paper_workflow.runtime import CodexSkillInvocation, SkillRuntime
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    FigureAsset,
    FigureCatalog,
    FigureQuality,
    PaperAnalysis,
    PaperSourceBundle,
    StageExecutionReport,
    StageStatus,
)

MIN_FIGURE_WIDTH = 320
MIN_FIGURE_HEIGHT = 200
_DECORATIVE = re.compile(
    r"(?:^|[_\W])(logo|icon|avatar|qrcode|qr[-_ ]?code|decorative)(?:$|[_\W])",
    re.IGNORECASE,
)
_BAD_CROP = re.compile(r"\b(missing|removed|truncated|clipped|cut off)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FigureSelectionDecision:
    slide_target: int
    required: int
    usable: int
    needs_extraction: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


class MinerUFigureCatalogBuilder:
    """Project usable source assets into the canonical Stage 2 catalog."""

    def __init__(
        self,
        *,
        min_width: int = MIN_FIGURE_WIDTH,
        min_height: int = MIN_FIGURE_HEIGHT,
    ) -> None:
        self.min_width = min_width
        self.min_height = min_height

    @staticmethod
    def slide_target(duration_minutes: int) -> int:
        return max(1, round(duration_minutes * 0.75))

    @staticmethod
    def required_asset_count(slide_target: int) -> int:
        return max(4, min(round(slide_target * 0.45), 10))

    def build(
        self,
        *,
        workspace: Path,
        source: PaperSourceBundle,
        analysis: PaperAnalysis,
        output_assets: Path,
    ) -> tuple[FigureCatalog, FigureSelectionDecision]:
        output_assets.mkdir(parents=True, exist_ok=True)
        figures: list[FigureAsset] = []
        low_resolution = False
        source_assets = {item.id: item for item in source.assets}
        claims_by_asset, claims_by_page = self._claim_support(analysis)
        usable_asset_ids: set[str] = set()

        for asset in source.assets:
            if asset.type not in {"figure", "table"} or self._decorative(asset.path, asset.caption):
                continue
            source_path = self._source_path(workspace, asset.path)
            dimensions = self._dimensions(source_path)
            if not asset.caption or not source_path.is_file() or dimensions is None:
                continue
            width, height = dimensions
            if width < self.min_width or height < self.min_height:
                low_resolution = True
                continue
            destination = self._copy_or_render(source_path, output_assets, asset.id)
            if destination is None:
                continue
            usable_asset_ids.add(asset.id)
            figures.append(
                FigureAsset(
                    id=f"fig_{asset.id.removeprefix('asset_')}",
                    path=f"assets/{destination.name}",
                    original_figure=self._figure_label(asset.caption),
                    page_no=asset.page_no,
                    caption=asset.caption,
                    source_method="mineru" if asset.source == "mineru" else "pdf_crop",
                    supports_claim_ids=sorted(
                        claims_by_asset.get(asset.id, set())
                        | claims_by_page.get(asset.page_no, set())
                    ),
                    quality=FigureQuality(width=width, height=height, readable=True),
                    crop_notes="uncropped source asset; axes, legend, caption, and panel labels preserved",
                )
            )

        slide_target = self.slide_target(15)
        required = self.required_asset_count(slide_target)
        reasons: list[str] = []
        if len(figures) < required:
            reasons.append("insufficient_usable_mineru_assets")
        if low_resolution:
            reasons.append("low_resolution")
        core_claims = {item.id for item in analysis.claims if item.importance == "core"}
        covered_core = {
            claim for item in figures for claim in item.supports_claim_ids
        } & core_claims
        has_core_candidate = any(
            set(candidate.claim_ids) & core_claims for candidate in analysis.figure_candidates
        )
        if has_core_candidate and covered_core != core_claims:
            reasons.append("missing_core_figure")
        if any(
            ref.asset_id and ref.asset_id in source_assets and ref.asset_id not in usable_asset_ids
            for candidate in analysis.figure_candidates
            for ref in candidate.source_refs
        ):
            reasons.append("missing_required_panel")
        return FigureCatalog(figures=figures), FigureSelectionDecision(
            slide_target=slide_target,
            required=required,
            usable=len(figures),
            needs_extraction=bool(reasons),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def decision_for_duration(
        self, decision: FigureSelectionDecision, duration_minutes: int
    ) -> FigureSelectionDecision:
        target = self.slide_target(duration_minutes)
        required = self.required_asset_count(target)
        reasons = [item for item in decision.reasons if item != "insufficient_usable_mineru_assets"]
        if decision.usable < required:
            reasons.insert(0, "insufficient_usable_mineru_assets")
        return FigureSelectionDecision(
            slide_target=target,
            required=required,
            usable=decision.usable,
            needs_extraction=bool(reasons),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _source_path(workspace: Path, relative: str) -> Path:
        source_root = (workspace / "source").resolve()
        path = (source_root / relative).resolve()
        path.relative_to(source_root)
        return path

    @staticmethod
    def _dimensions(path: Path) -> tuple[int, int] | None:
        try:
            with Image.open(path) as image:
                image.load()
                return image.width, image.height
        except (OSError, UnidentifiedImageError):
            try:
                document = fitz.open(path)
                page = document[0]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                result = (pixmap.width, pixmap.height)
                document.close()
                return result
            except (OSError, RuntimeError, ValueError, fitz.FileDataError, IndexError):
                return None

    @staticmethod
    def _copy_or_render(source: Path, output: Path, asset_id: str) -> Path | None:
        try:
            with Image.open(source) as image:
                image.load()
            destination = output / f"{asset_id}{source.suffix.lower()}"
            shutil.copyfile(source, destination)
            return destination
        except (OSError, UnidentifiedImageError):
            try:
                document = fitz.open(source)
                pixmap = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                destination = output / f"{asset_id}.png"
                pixmap.save(destination)
                document.close()
                return destination
            except (OSError, RuntimeError, ValueError, fitz.FileDataError, IndexError):
                return None

    @staticmethod
    def _decorative(path: str, caption: str | None) -> bool:
        return bool(_DECORATIVE.search(f"{path} {caption or ''}"))

    @staticmethod
    def _figure_label(caption: str) -> str | None:
        matched = re.search(r"\b(?:fig(?:ure)?\.?|table)\s*\d+[A-Za-z]?", caption, re.IGNORECASE)
        return matched.group(0) if matched else None

    @staticmethod
    def _claim_support(
        analysis: PaperAnalysis,
    ) -> tuple[dict[str, set[str]], dict[int, set[str]]]:
        by_asset: dict[str, set[str]] = {}
        by_page: dict[int, set[str]] = {}
        for candidate in analysis.figure_candidates:
            for ref in candidate.source_refs:
                by_page.setdefault(ref.page_no, set()).update(candidate.claim_ids)
                if ref.asset_id:
                    by_asset.setdefault(ref.asset_id, set()).update(candidate.claim_ids)
        return by_asset, by_page


class ExtractPaperImagesAdapter:
    """Invoke the original extraction Skill and parse its canonical catalog output."""

    skill_name = "extract-paper-images"
    prompt_version = "extract-paper-images-v1"

    def __init__(self, runtime: SkillRuntime, skill_directory: Path) -> None:
        self.runtime = runtime
        self.skill_directory = skill_directory.resolve()

    def extract(
        self,
        *,
        workspace: Path,
        stage_root: Path,
        input_hash: str,
        skill_version: str,
        attempt: int,
    ) -> tuple[FigureCatalog, StageExecutionReport, Path]:
        extraction_output = stage_root / "skill_output"
        extraction_output.mkdir(parents=True, exist_ok=True)
        prompt = stage_root / "extract_prompt.md"
        prompt.write_text(
            (Path(__file__).resolve().parent / "prompts" / "extract_figures_contract.md").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        runtime_schema = stage_root / "runtime_result_schema.json"
        catalog_schema = stage_root / "figure_catalog_schema.json"
        catalog_schema.write_text(
            json.dumps(FigureCatalog.model_json_schema(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        runtime_schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["status", "summary", "warnings"],
                    "properties": {
                        "status": {"type": "string", "enum": ["completed", "blocked"]},
                        "summary": {"type": "string"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        report = self.runtime.run(
            CodexSkillInvocation(
                stage=ComposedStage.FIGURES,
                skill_name=self.skill_name,
                skill_directory=self.skill_directory,
                skill_version=skill_version,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                workspace=workspace,
                prompt_path=prompt,
                input_paths=(
                    workspace / "source" / "paper.pdf",
                    workspace / "source" / "paper_source.json",
                    workspace / "stages" / "01_analysis" / "output" / "paper_analysis.json",
                    workspace / "resolved_request.json",
                    catalog_schema,
                ),
                output_directory=extraction_output,
                expected_outputs=("figures.json",),
                output_schema_path=runtime_schema,
                attempt=attempt,
                network_enabled=True,
            )
        )
        if report.status != StageStatus.SUCCEEDED:
            raise RuntimeError("extract-paper-images runtime execution failed")
        try:
            catalog = FigureCatalog.model_validate_json(
                (extraction_output / "figures.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError("extract-paper-images returned an invalid figures.json") from exc
        return catalog, report, extraction_output


class FigureCatalogMerger:
    """Merge MinerU and Skill assets into one deterministic downstream contract."""

    _SOURCE_RANK: ClassVar[dict[str, int]] = {
        "arxiv_source": 4,
        "mineru": 3,
        "pdf_crop": 2,
        "other": 1,
    }

    def merge(
        self,
        *,
        workspace: Path,
        output: Path,
        catalogs: list[tuple[FigureCatalog, Path]],
    ) -> FigureCatalog:
        assets = output / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        selected: dict[str, FigureAsset] = {}
        for catalog, catalog_root in catalogs:
            for figure in catalog.figures:
                source = self._resolve_catalog_path(workspace, catalog_root, figure.path)
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                current = selected.get(digest)
                if current and self._score(current) >= self._score(figure):
                    continue
                suffix = source.suffix.lower() or ".png"
                destination = assets / f"figure_{digest[:16]}{suffix}"
                if not destination.exists():
                    shutil.copyfile(source, destination)
                selected[digest] = FigureAsset.model_validate(
                    {
                        **figure.model_dump(mode="python"),
                        "path": f"assets/{destination.name}",
                    }
                )
        figures = sorted(
            selected.values(),
            key=lambda item: (item.page_no, item.original_figure or "", item.id),
        )
        used_ids: set[str] = set()
        normalized: list[FigureAsset] = []
        for index, figure in enumerate(figures, start=1):
            figure_id = figure.id if figure.id not in used_ids else f"{figure.id}_{index:02d}"
            used_ids.add(figure_id)
            normalized.append(
                FigureAsset.model_validate({**figure.model_dump(mode="python"), "id": figure_id})
            )
        return FigureCatalog(figures=normalized)

    @staticmethod
    def _resolve_catalog_path(workspace: Path, root: Path, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("figure path must be relative")
        candidates = [(root / path).resolve(), (workspace / path).resolve()]
        for candidate in candidates:
            try:
                candidate.relative_to(workspace.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        raise ValueError(f"figure file does not exist: {relative}")

    def _score(self, figure: FigureAsset) -> tuple[int, int]:
        return (
            self._SOURCE_RANK.get(figure.source_method, 0),
            figure.quality.width * figure.quality.height,
        )


def validate_figure_catalog(
    catalog: FigureCatalog,
    *,
    output: Path,
    analysis: PaperAnalysis,
    min_width: int = MIN_FIGURE_WIDTH,
    min_height: int = MIN_FIGURE_HEIGHT,
) -> None:
    claim_ids = {item.id for item in analysis.claims}
    core_claim_ids = {item.id for item in analysis.claims if item.importance == "core"}
    required_core_claims = {
        claim_id
        for candidate in analysis.figure_candidates
        for claim_id in candidate.claim_ids
        if claim_id in core_claim_ids
    }
    supported_claim_ids = {
        claim_id for figure in catalog.figures for claim_id in figure.supports_claim_ids
    }
    output_root = output.resolve()
    for figure in catalog.figures:
        if _DECORATIVE.search(f"{figure.path} {figure.caption}"):
            raise ValueError(f"decorative asset is not allowed: {figure.id}")
        unknown = set(figure.supports_claim_ids) - claim_ids
        if unknown:
            raise ValueError(f"figure {figure.id} references unknown claims: {sorted(unknown)}")
        path = (output_root / figure.path).resolve()
        path.relative_to(output_root)
        if not path.is_file():
            raise ValueError(f"figure file does not exist: {figure.path}")
        try:
            with Image.open(path) as image:
                image.load()
                actual = (image.width, image.height)
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError(f"figure cannot be opened: {figure.path}") from exc
        if actual != (figure.quality.width, figure.quality.height):
            raise ValueError(f"figure dimensions do not match catalog: {figure.id}")
        if actual[0] < min_width or actual[1] < min_height or not figure.quality.readable:
            raise ValueError(f"figure resolution is too low: {figure.id}")
        if not figure.caption.strip() or figure.page_no < 1 or not figure.source_method:
            raise ValueError(f"figure provenance is incomplete: {figure.id}")
        notes = figure.crop_notes or ""
        if figure.source_method in {"pdf_crop", "arxiv_source"} and (
            not notes or _BAD_CROP.search(notes)
        ):
            raise ValueError(f"figure crop integrity is not attested: {figure.id}")
        normalized_notes = notes.lower()
        is_table = bool(
            re.search(r"\btable\b", figure.original_figure or "", re.IGNORECASE)
            or figure.id.lower().startswith("table")
        )
        mentions_caption = any(
            token in normalized_notes for token in ("caption", "图注", "标题")
        )
        # Crop attestations are natural-language output and may be Chinese. A
        # scientific crop must mention its caption plus at least one applicable
        # visual frame component; tables legitimately have neither axes nor a
        # legend. Requiring the three literal English words rejected valid
        # Chinese reports and non-chart figures such as architecture diagrams.
        mentions_visual_frame = bool(
            re.search(r"\bax(?:is|es)\b", notes, re.IGNORECASE)
            or any(
                token in normalized_notes
                for token in (
                    "legend",
                    "chart",
                    "plot",
                    "diagram",
                    "坐标轴",
                    "横轴",
                    "纵轴",
                    "图例",
                    "panel",
                    "比例尺",
                    "条形图",
                    "柱状图",
                    "折线图",
                    "曲线图",
                    "流程图",
                    "架构图",
                )
            )
        )
        crop_notes_complete = mentions_caption and (is_table or mentions_visual_frame)
        # An arXiv source asset is the author's uncropped original and normally
        # does not contain the typeset caption. Only PDF crops must attest that
        # the surrounding scientific frame survived the crop operation.
        if figure.source_method == "pdf_crop" and not crop_notes_complete:
            raise ValueError(
                "figure crop notes must attest the caption and applicable visual frame: "
                f"{figure.id}"
            )
    missing_core_claims = required_core_claims - supported_claim_ids
    if missing_core_claims:
        raise ValueError(
            f"core figure candidates remain unsupported: {sorted(missing_core_claims)}"
        )


def write_figure_catalog(path: Path, catalog: FigureCatalog) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
