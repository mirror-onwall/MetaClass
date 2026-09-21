from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import ClassVar

import fitz
from PIL import Image, UnidentifiedImageError

from metaclass.core.schemas import SchemaModel
from metaclass.modules.paper_workflow.schemas import PaperPresentationArtifact
from metaclass.modules.paper_workflow.source_visual_validator import (
    SourceVisualValidationError,
    SourceVisualValidator,
)


class PaperDeckArtifactError(ValueError):
    """Raised when native paper-deck output cannot be normalized safely."""


class NativePaperDeckSlide(SchemaModel):
    id: str
    order: int
    image_path: str
    image_hash: str
    pdf_page_no: int
    title_hint: str
    role: str
    message: str
    visual_intent: str
    planned_text: list[str]
    evidence_hint: str
    source_visual_hint: str | None = None
    prompt_path: str


class NativePaperDeckManifest(SchemaModel):
    provider: str
    style_preset: str
    language: str
    slide_count: int
    pdf_path: str
    slides: list[NativePaperDeckSlide]


class PaperDeckArtifactAdapter:
    """Normalize native paper-deck files without authoring new paper facts."""

    _numbered_file = re.compile(r"^(\d+)[-_].+")
    _outline_heading = re.compile(r"^##\s+(\d+)[.)]?\s*(.+?)\s*$")
    _outline_field = re.compile(r"^-\s*([^:：]+)[:：]\s*(.*?)\s*$")
    _real_backend_tokens: ClassVar[tuple[str, ...]] = (
        "imagegen",
        "image_gen",
        "openai",
        "dall-e",
        "gemini",
        "seedream",
        "baoyu-imagine",
        "stable diffusion",
        "flux",
    )
    _forbidden_backend_tokens: ClassVar[tuple[str, ...]] = (
        "pillow",
        "matplotlib",
        "mermaid",
        "html/css",
        "canvas",
        "ppt shapes",
        "placeholder",
        "template renderer",
    )

    def adapt(
        self,
        artifact: PaperPresentationArtifact,
        *,
        workspace: Path,
    ) -> NativePaperDeckManifest:
        root = workspace.resolve()
        pdf = self._resolve(root, artifact.presentation_pdf_path, file=True)
        images_dir = self._resolve(root, artifact.source_images_dir, directory=True)
        backgrounds_dir = self._resolve(
            root, str(Path(artifact.presentation_pdf_path).parent / "images"), directory=True
        )
        analysis = self._resolve(root, artifact.analysis_path, file=True)
        deck_brief = self._resolve(root, artifact.deck_brief_path, file=True)
        outline = self._resolve(root, artifact.outline_path, file=True)
        prompts_dir = self._resolve(root, artifact.prompts_dir, directory=True)
        generation_log = self._resolve(root, artifact.generation_log_path, file=True)
        source_visual_manifest = self._resolve(
            root, artifact.source_visual_manifest_path, file=True
        )
        for path in (
            pdf,
            analysis,
            deck_brief,
            outline,
            generation_log,
            source_visual_manifest,
        ):
            if path.stat().st_size == 0:
                raise PaperDeckArtifactError(f"required artifact is empty: {path.name}")
        try:
            SourceVisualValidator().validate(artifact, workspace=root)
        except SourceVisualValidationError as exc:
            raise PaperDeckArtifactError(str(exc)) from exc

        prompt_files = self._numbered_files(prompts_dir, {".md"})
        image_files = self._numbered_files(
            images_dir,
            {".png", ".jpg", ".jpeg", ".webp"},
        )
        background_files = self._numbered_files(
            backgrounds_dir,
            {".png", ".jpg", ".jpeg", ".webp"},
        )
        if not prompt_files or not image_files:
            raise PaperDeckArtifactError("prompts/ and images/ must both be non-empty")
        if len(prompt_files) != len(image_files):
            raise PaperDeckArtifactError("outline, prompt, image, and PDF page counts must match")
        if len(background_files) != len(image_files):
            raise PaperDeckArtifactError("background and rendered page counts must match")
        expected = list(range(1, len(image_files) + 1))
        if [number for number, _ in prompt_files] != expected:
            raise PaperDeckArtifactError("prompt numbering must be continuous from 1")
        if [number for number, _ in image_files] != expected:
            raise PaperDeckArtifactError("image numbering must be continuous from 1")

        outline_slides = self._parse_outline(outline)
        if [item["order"] for item in outline_slides] != expected:
            raise PaperDeckArtifactError("outline numbering must be continuous from 1")
        if len(outline_slides) != len(image_files):
            raise PaperDeckArtifactError("outline, prompt, and image counts must match")

        self._validate_generation_log(generation_log, [path for _, path in background_files])
        self._validate_pdf(pdf, expected_pages=len(image_files))
        image_hashes = [self._validate_image(path) for _, path in image_files]
        if len(set(image_hashes)) != len(image_hashes):
            raise PaperDeckArtifactError("generated slide image hashes must be unique")

        brief = self._parse_deck_brief(deck_brief)
        slides = []
        for index, outline_slide in enumerate(outline_slides):
            _, prompt_path = prompt_files[index]
            _, image_path = image_files[index]
            slides.append(
                NativePaperDeckSlide(
                    id=f"paper_deck_slide_{index + 1:03d}",
                    order=index + 1,
                    image_path=str(image_path.relative_to(root)),
                    image_hash=image_hashes[index],
                    pdf_page_no=index + 1,
                    title_hint=outline_slide["title"],
                    role=outline_slide["role"],
                    message=outline_slide["message"],
                    visual_intent=outline_slide["visual"],
                    planned_text=self._planned_text(outline_slide["text"]),
                    evidence_hint=outline_slide["evidence"],
                    source_visual_hint=outline_slide.get("source visual") or None,
                    prompt_path=str(prompt_path.relative_to(root)),
                )
            )
        ids = [slide.id for slide in slides]
        if len(ids) != len(set(ids)):
            raise PaperDeckArtifactError("slide ids must be unique")
        return NativePaperDeckManifest(
            provider=artifact.provider,
            style_preset=brief["style_preset"],
            language=brief["language"],
            slide_count=len(slides),
            pdf_path=str(pdf.relative_to(root)),
            slides=slides,
        )

    @classmethod
    def _parse_outline(cls, path: Path) -> list[dict[str, str | int]]:
        slides: list[dict[str, str | int]] = []
        current: dict[str, str | int] | None = None
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            heading = cls._outline_heading.match(raw_line.strip())
            if heading:
                if current:
                    slides.append(current)
                current = {"order": int(heading.group(1)), "title": heading.group(2).strip()}
                continue
            field = cls._outline_field.match(raw_line.strip())
            if current is not None and field:
                current[field.group(1).strip().casefold()] = field.group(2).strip()
        if current:
            slides.append(current)
        required = {"title", "role", "message", "visual", "text", "evidence"}
        for slide in slides:
            missing = required - slide.keys()
            if missing:
                raise PaperDeckArtifactError(
                    f"outline slide {slide.get('order')} is missing fields: {sorted(missing)}"
                )
            if any(not str(slide[key]).strip() for key in required):
                raise PaperDeckArtifactError(
                    f"outline slide {slide.get('order')} contains empty required fields"
                )
        if not slides:
            raise PaperDeckArtifactError("outline.md contains no numbered slides")
        return slides

    @staticmethod
    def _parse_deck_brief(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = re.match(r"^-\s*([^:：]+)[:：]\s*(.*?)\s*$", line.strip())
            if matched:
                key = matched.group(1).strip().strip("`*_ ").casefold()
                values[key] = matched.group(2).strip(" `")
        missing = {"style_preset", "language"} - values.keys()
        if missing:
            raise PaperDeckArtifactError(
                f"deck-brief.md is missing fields: {sorted(missing)}"
            )
        return values

    @classmethod
    def _numbered_files(
        cls,
        directory: Path,
        suffixes: set[str],
    ) -> list[tuple[int, Path]]:
        numbered: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            matched = cls._numbered_file.match(path.name)
            if path.is_file() and path.suffix.casefold() in suffixes and matched:
                if path.stat().st_size == 0:
                    raise PaperDeckArtifactError(f"numbered artifact is empty: {path.name}")
                numbered.append((int(matched.group(1)), path))
        numbered.sort(key=lambda item: item[0])
        numbers = [number for number, _ in numbered]
        if len(numbers) != len(set(numbers)):
            raise PaperDeckArtifactError(f"duplicate page numbers in {directory.name}/")
        return numbered

    @classmethod
    def _validate_generation_log(cls, path: Path, images: list[Path]) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        for image in images:
            matching = [line.casefold() for line in lines if image.name in line]
            if not matching:
                raise PaperDeckArtifactError(
                    f"generation-log.md does not cover image: {image.name}"
                )
            if any(token in line for line in matching for token in cls._forbidden_backend_tokens):
                raise PaperDeckArtifactError(
                    f"generation log records a non-raster backend for {image.name}"
                )
            if not any(token in line for line in matching for token in cls._real_backend_tokens):
                raise PaperDeckArtifactError(
                    f"generation log lacks a recognized raster backend for {image.name}"
                )

    @staticmethod
    def _validate_pdf(path: Path, *, expected_pages: int) -> None:
        try:
            page_sizes = []
            with fitz.open(path) as document:
                if document.page_count != expected_pages:
                    raise PaperDeckArtifactError(
                        f"PDF has {document.page_count} pages for {expected_pages} images"
                    )
                for page in document:
                    page_sizes.append((round(page.rect.width, 3), round(page.rect.height, 3)))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.15, 0.15), alpha=False)
                    if pixmap.width < 1 or pixmap.height < 1 or not pixmap.samples:
                        raise PaperDeckArtifactError(
                            f"PDF page {page.number + 1} could not be rendered"
                        )
        except PaperDeckArtifactError:
            raise
        except (OSError, RuntimeError, ValueError, fitz.FileDataError) as exc:
            raise PaperDeckArtifactError("presentation PDF is unreadable") from exc
        if len(set(page_sizes)) != 1:
            raise PaperDeckArtifactError("all PDF pages must have the same dimensions")

    @staticmethod
    def _validate_image(path: Path) -> str:
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError) as exc:
            raise PaperDeckArtifactError(f"slide image is unreadable: {path.name}") from exc
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _planned_text(value: str | int) -> list[str]:
        return [item.strip() for item in re.split(r"\s*[;；]\s*", str(value)) if item.strip()]

    @staticmethod
    def _resolve(
        workspace: Path,
        value: str,
        *,
        file: bool = False,
        directory: bool = False,
    ) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise PaperDeckArtifactError("artifact paths must be safe workspace-relative paths")
        resolved = (workspace / relative).resolve()
        resolved.relative_to(workspace)
        if file and not resolved.is_file():
            raise PaperDeckArtifactError(f"artifact file is missing: {value}")
        if directory and not resolved.is_dir():
            raise PaperDeckArtifactError(f"artifact directory is missing: {value}")
        return resolved
