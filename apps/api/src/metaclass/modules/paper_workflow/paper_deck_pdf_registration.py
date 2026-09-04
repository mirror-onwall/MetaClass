from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
from PIL import Image, ImageChops, ImageStat
from pydantic import Field

from metaclass.core.schemas import SchemaModel
from metaclass.modules.materials.schemas import Material
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
)
from metaclass.modules.paper_workflow.schemas import PaperPresentationArtifact
from metaclass.modules.presentation.repository import PresentationRepository
from metaclass.modules.presentation.schemas import (
    PresentationResource,
    PresentationSlideResource,
)


class PaperDeckPDFRegistrationError(ValueError):
    """Raised when a PDF cannot safely become the classroom authority."""


class PaperDeckPageComparison(SchemaModel):
    page_no: int = Field(ge=1)
    aspect_ratio_delta: float = Field(ge=0)
    mean_absolute_error: float = Field(ge=0)
    perceptual_distance: int = Field(ge=0)
    black_border_delta: float = Field(ge=0)


class PaperDeckSlideImage(SchemaModel):
    slide_no: int = Field(ge=1)
    image_path: str = Field(min_length=1)
    source_page_no: int = Field(ge=1)


class PaperDeckPDFRegistrationResult(SchemaModel):
    material: Material
    presentation_resource: PresentationResource
    page_count: int = Field(ge=1)
    slide_images: list[PaperDeckSlideImage] = Field(min_length=1)
    comparisons: list[PaperDeckPageComparison] = Field(min_length=1)


class PaperDeckPDFRegistrationService:
    """Register a native paper-deck PDF and expose its raster pages to classrooms."""

    def __init__(
        self,
        *,
        materials: MaterialService,
        presentations: PresentationRepository,
    ) -> None:
        self.materials = materials
        self.presentations = presentations

    def register(
        self,
        *,
        artifact: PaperPresentationArtifact,
        manifest: NativePaperDeckManifest,
        workspace: Path,
        original_paper_material_id: str,
        presentation_plan_id: str,
        paper_pdf_hash: str,
        paper_deck_skill_version: str,
        resolved_request_hash: str,
        generation_output_hash: str,
        persist_resource: bool = True,
    ) -> PaperDeckPDFRegistrationResult:
        root = workspace.resolve()
        pdf_path = self._resolve(root, artifact.presentation_pdf_path)
        image_paths = [self._resolve(root, slide.image_path) for slide in manifest.slides]
        if manifest.slide_count != len(image_paths):
            raise PaperDeckPDFRegistrationError("manifest slide count does not match images")

        comparisons = self._compare_pdf_to_source_images(pdf_path, image_paths)
        derivation_key = self.materials.paper_deck_derivation_key(
            paper_pdf_hash=paper_pdf_hash,
            paper_deck_skill_version=paper_deck_skill_version,
            resolved_request_hash=resolved_request_hash,
            generation_output_hash=generation_output_hash,
        )
        material = self.materials.register_paper_deck_pdf(
            pdf_path,
            filename="paper-deck-presentation.pdf",
            derivation_key=derivation_key,
            parent_material_id=original_paper_material_id,
        )
        # Native images remain the highest-quality preview/repair source. The PDF
        # comparison above makes it safe to use them for page previews.
        material = self.materials.install_page_images(material.id, image_paths)

        resource_identity = hashlib.sha256(
            f"{presentation_plan_id}\0{material.id}\0{generation_output_hash}".encode()
        ).hexdigest()
        resource = PresentationResource(
            id=f"pres_resource_{resource_identity[:24]}",
            presentation_plan_id=presentation_plan_id,
            kind="paper_deck",
            source_material_id=material.id,
            source_file_hash=material.file_hash,
            source_page_count=len(image_paths),
            slides=[
                PresentationSlideResource(
                    slide_id=slide.id,
                    order=slide.order,
                    kind="source",
                    source_page_no=slide.pdf_page_no,
                )
                for slide in manifest.slides
            ],
        )
        if persist_resource:
            self.presentations.save_resource(resource)
        return PaperDeckPDFRegistrationResult(
            material=material,
            presentation_resource=resource,
            page_count=len(image_paths),
            slide_images=[
                PaperDeckSlideImage(
                    slide_no=index,
                    image_path=str(path),
                    source_page_no=index,
                )
                for index, path in enumerate(image_paths, start=1)
            ],
            comparisons=comparisons,
        )

    @staticmethod
    def _resolve(root: Path, value: str) -> Path:
        path = Path(value)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise PaperDeckPDFRegistrationError(f"artifact file is invalid: {value}")
        return resolved

    @classmethod
    def _compare_pdf_to_source_images(
        cls,
        pdf_path: Path,
        images: list[Path],
    ) -> list[PaperDeckPageComparison]:
        comparisons: list[PaperDeckPageComparison] = []
        try:
            with fitz.open(pdf_path) as document:
                if document.page_count != len(images):
                    raise PaperDeckPDFRegistrationError("PDF and source image counts differ")
                for index, image_path in enumerate(images):
                    with Image.open(image_path) as opened:
                        source = opened.convert("RGB")
                    page = document[index]
                    aspect_delta = abs(
                        source.width / source.height - page.rect.width / page.rect.height
                    )
                    matrix = fitz.Matrix(
                        source.width / page.rect.width,
                        source.height / page.rect.height,
                    )
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
                    rendered = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    rendered = rendered.resize(source.size, Image.Resampling.LANCZOS)
                    source_small = source.resize((320, 180), Image.Resampling.LANCZOS)
                    rendered_small = rendered.resize((320, 180), Image.Resampling.LANCZOS)
                    mae = sum(ImageStat.Stat(ImageChops.difference(source_small, rendered_small)).mean) / 3
                    perceptual_distance = cls._dhash(source_small) ^ cls._dhash(rendered_small)
                    perceptual_distance = perceptual_distance.bit_count()
                    black_delta = max(
                        0.0,
                        cls._black_border_fraction(rendered_small)
                        - cls._black_border_fraction(source_small),
                    )
                    comparison = PaperDeckPageComparison(
                        page_no=index + 1,
                        aspect_ratio_delta=aspect_delta,
                        mean_absolute_error=mae,
                        perceptual_distance=perceptual_distance,
                        black_border_delta=black_delta,
                    )
                    if (
                        aspect_delta > 0.01
                        or mae > 20
                        or perceptual_distance > 18
                        or black_delta > 0.04
                    ):
                        raise PaperDeckPDFRegistrationError(
                            f"PDF page {index + 1} differs from its source raster image"
                        )
                    comparisons.append(comparison)
        except PaperDeckPDFRegistrationError:
            raise
        except (OSError, RuntimeError, ValueError, fitz.FileDataError) as exc:
            raise PaperDeckPDFRegistrationError("presentation PDF comparison failed") from exc
        return comparisons

    @staticmethod
    def _dhash(image: Image.Image) -> int:
        grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(grayscale.get_flattened_data())
        value = 0
        for row in range(8):
            for column in range(8):
                value = (value << 1) | int(
                    pixels[row * 9 + column] > pixels[row * 9 + column + 1]
                )
        return value

    @staticmethod
    def _black_border_fraction(image: Image.Image) -> float:
        width, height = image.size
        band_x = max(1, width // 50)
        band_y = max(1, height // 50)
        pixels = list(image.crop((0, 0, width, band_y)).get_flattened_data())
        pixels += list(
            image.crop((0, height - band_y, width, height)).get_flattened_data()
        )
        pixels += list(image.crop((0, band_y, band_x, height - band_y)).get_flattened_data())
        pixels += list(
            image.crop((width - band_x, band_y, width, height - band_y)).get_flattened_data()
        )
        return sum(max(pixel) < 16 for pixel in pixels) / len(pixels)
