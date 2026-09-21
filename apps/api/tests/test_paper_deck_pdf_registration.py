import json
from pathlib import Path
from unittest.mock import Mock

import fitz
import pytest
from PIL import Image, ImageDraw

from metaclass.infrastructure.database import Database
from metaclass.modules.materials.repository import SqlAlchemyMaterialRepository
from metaclass.modules.materials.schemas import Material, MaterialSourceRole
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
    NativePaperDeckSlide,
)
from metaclass.modules.paper_workflow.paper_deck_pdf_registration import (
    PaperDeckPDFRegistrationError,
    PaperDeckPDFRegistrationService,
)
from metaclass.modules.paper_workflow.schemas import PaperPresentationArtifact


def _write_slide(path: Path, color: str, label: str) -> None:
    image = Image.new("RGB", (640, 360), color)
    ImageDraw.Draw(image).text((80, 80), label, fill="white")
    image.save(path)


def _fixture(tmp_path: Path) -> tuple[PaperPresentationArtifact, NativePaperDeckManifest]:
    output = tmp_path / "provider_output"
    images = output / "rendered"
    prompts = output / "prompts"
    images.mkdir(parents=True)
    prompts.mkdir()
    image_paths = []
    for number, color in enumerate(("#284b8f", "#a33b35"), start=1):
        image_path = images / f"{number:02d}-slide.png"
        _write_slide(image_path, color, f"Slide {number}")
        (prompts / f"{number:02d}-slide.md").write_text("prompt", encoding="utf-8")
        image_paths.append(image_path)
    pdf = fitz.open()
    for image_path in image_paths:
        page = pdf.new_page(width=640, height=360)
        page.insert_image(page.rect, filename=str(image_path))
    pdf.save(output / "presentation.pdf")
    pdf.close()
    for name in ("analysis.md", "deck-brief.md", "outline.md", "generation-log.md"):
        (output / name).write_text("content", encoding="utf-8")
    (output / "source-visual-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "slides": [
                    {
                        "slide_id": f"slide_{number:03d}",
                        "order": number,
                        "render_mode": "native-raster",
                        "background_path": f"images/{number:02d}-slide.png",
                        "assets": [],
                        "annotations": [],
                    }
                    for number in (1, 2)
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact = PaperPresentationArtifact(
        provider="native_paper_deck",
        presentation_pdf_path="provider_output/presentation.pdf",
        source_images_dir="provider_output/rendered",
        analysis_path="provider_output/analysis.md",
        deck_brief_path="provider_output/deck-brief.md",
        outline_path="provider_output/outline.md",
        prompts_dir="provider_output/prompts",
        generation_log_path="provider_output/generation-log.md",
        source_visual_manifest_path="provider_output/source-visual-manifest.json",
    )
    slides = [
        NativePaperDeckSlide(
            id=f"paper_deck_slide_{number:03d}",
            order=number,
            image_path=f"provider_output/rendered/{number:02d}-slide.png",
            image_hash="a" * 64 if number == 1 else "b" * 64,
            pdf_page_no=number,
            title_hint=f"Slide {number}",
            role="method",
            message="message",
            visual_intent="visual",
            planned_text=["text"],
            evidence_hint="paper",
            prompt_path=f"provider_output/prompts/{number:02d}-slide.md",
        )
        for number in (1, 2)
    ]
    return artifact, NativePaperDeckManifest(
        provider="native_paper_deck",
        style_preset="journal-minimal",
        language="zh-CN",
        slide_count=2,
        pdf_path="provider_output/presentation.pdf",
        slides=slides,
    )


def _service(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "materials.db")
    database.create_schema()
    repository = SqlAlchemyMaterialRepository(database)
    source_pdf = tmp_path / "paper.pdf"
    document = fitz.open()
    document.new_page()
    document.save(source_pdf)
    document.close()
    repository.save_material(
        Material(
            id="mat_paper",
            filename="paper.pdf",
            file_type="pdf",
            file_hash="f" * 64,
            storage_path=str(source_pdf),
        )
    )
    presentations = Mock()
    service = PaperDeckPDFRegistrationService(
        materials=MaterialService(tmp_path / "data", repository),
        presentations=presentations,
    )
    return database, repository, presentations, service


def test_registers_pdf_material_and_reuses_native_images(tmp_path: Path) -> None:
    artifact, manifest = _fixture(tmp_path)
    database, repository, presentations, service = _service(tmp_path)
    try:
        kwargs = {
            "artifact": artifact,
            "manifest": manifest,
            "workspace": tmp_path,
            "original_paper_material_id": "mat_paper",
            "presentation_plan_id": "plan_paper",
            "paper_pdf_hash": "f" * 64,
            "paper_deck_skill_version": "paper-deck@test",
            "resolved_request_hash": "r" * 64,
            "generation_output_hash": "g" * 64,
        }
        first = service.register(**kwargs)
        second = service.register(**kwargs)

        assert first.material.id == second.material.id
        assert first.material.source == "paper_deck"
        assert first.material.source_role == MaterialSourceRole.PRESENTATION_DECK
        assert first.material.parent_material_id == "mat_paper"
        assert first.material.page_count == 2
        assert repository.get_material("mat_paper").source_role == MaterialSourceRole.PAPER_SOURCE
        assert [page.image_path for page in repository.list_pages(first.material.id)] == [
            str(tmp_path / "data/processed" / first.material.id / "pages/page_001.png"),
            str(tmp_path / "data/processed" / first.material.id / "pages/page_002.png"),
        ]
        assert first.presentation_resource.kind == "paper_deck"
        assert first.presentation_resource.source_material_id == first.material.id
        assert [slide.source_page_no for slide in first.presentation_resource.slides] == [1, 2]
        assert len(first.comparisons) == 2
        assert presentations.save_resource.call_count == 2
    finally:
        database.dispose()


def test_rejects_pdf_page_order_mismatch_before_registration(tmp_path: Path) -> None:
    artifact, manifest = _fixture(tmp_path)
    pdf_path = tmp_path / artifact.presentation_pdf_path
    images = [tmp_path / slide.image_path for slide in reversed(manifest.slides)]
    document = fitz.open()
    for image_path in images:
        page = document.new_page(width=640, height=360)
        page.insert_image(page.rect, filename=str(image_path))
    replacement = tmp_path / "replacement.pdf"
    document.save(replacement)
    document.close()
    pdf_path.write_bytes(replacement.read_bytes())
    database, repository, _, service = _service(tmp_path)
    try:
        with pytest.raises(PaperDeckPDFRegistrationError, match="differs"):
            service.register(
                artifact=artifact,
                manifest=manifest,
                workspace=tmp_path,
                original_paper_material_id="mat_paper",
                presentation_plan_id="plan_paper",
                paper_pdf_hash="f" * 64,
                paper_deck_skill_version="paper-deck@test",
                resolved_request_hash="r" * 64,
                generation_output_hash="g" * 64,
            )
        assert len(repository.list_materials()) == 1
    finally:
        database.dispose()
