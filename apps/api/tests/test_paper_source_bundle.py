import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from metaclass.main import create_app
from metaclass.modules.materials.schemas import PageImage
from metaclass.modules.paper_workflow.schemas import PaperSourceBundle
from metaclass.modules.paper_workflow.source_bundle import PaperSourceBundleBuilder


def _pdf_bytes() -> bytes:
    document = fitz.open()
    for text in ("A Stable Paper", "Results"):
        page = document.new_page()
        page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def _material(client: TestClient) -> str:
    response = client.post(
        "/api/v1/materials/process",
        files={"file": ("paper.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["material"]["id"]


def test_source_bundle_prefers_v2_and_preserves_non_text_assets(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        material_id = _material(client)

    mineru_root = tmp_path / "processed" / material_id / "mineru" / "result"
    (mineru_root / "images").mkdir(parents=True)
    (mineru_root / "images" / "figure.png").write_bytes(b"figure-bytes")
    (mineru_root / "middle.json").write_text(
        json.dumps({"pages": [{"page_idx": 0, "blocks": [{"type": "title", "text": "Middle"}]}]}),
        encoding="utf-8",
    )
    (mineru_root / "content_list.json").write_text(
        json.dumps([{"type": "title", "page_idx": 0, "text": "Legacy"}]),
        encoding="utf-8",
    )
    temporary_absolute = str(mineru_root / "images" / "figure.png")
    content = [
        {"type": "title", "page_idx": 0, "text": "A Stable Paper", "level": 1},
        {
            "type": "text",
            "page_idx": 0,
            "text": "This paragraph is traceable.",
            "bbox": [10, 20, 900, 180],
        },
        {
            "type": "image",
            "page_idx": 1,
            "img_path": temporary_absolute,
            "image_caption": ["Figure 1. Main result."],
            "image_footnote": ["Higher is better."],
            "bbox": [100, 200, 800, 700],
        },
        {
            "type": "table",
            "page_idx": 1,
            "table_body": "| Method | Score |\n|---|---|\n| Ours | 90 |",
            "table_caption": ["Table 1. Accuracy."],
        },
        {"type": "equation", "page_idx": 1, "latex": "E = mc^2"},
    ]
    (mineru_root / "content_list_v2.json").write_text(json.dumps(content), encoding="utf-8")

    builder = PaperSourceBundleBuilder(app.state.services.materials)
    workspace = tmp_path / "runtime" / "paper_workflows" / "paper_job_bundle"
    first = builder.build(material_id=material_id, workspace=workspace)
    first_hash = builder.bundle_hash(first, workspace)
    second = builder.build(material_id=material_id, workspace=workspace)

    source_dir = workspace / "source"
    source_payload = json.loads((source_dir / "paper_source.json").read_text(encoding="utf-8"))
    assert PaperSourceBundle.model_validate(source_payload) == first
    markdown = (source_dir / "paper_content.md").read_text(encoding="utf-8")
    assert source_payload["parser_source"] == "content_list_v2"
    assert source_payload["metadata_candidates"]["title"] == "A Stable Paper"
    assert source_payload["bbox_coordinate_system"]["unit"] == "normalized_1000"
    assert {asset["type"] for asset in source_payload["assets"]} == {
        "figure",
        "table",
        "equation",
    }
    assert all(not Path(asset["path"]).is_absolute() for asset in source_payload["assets"])
    assert temporary_absolute not in json.dumps(source_payload)
    assert temporary_absolute not in (source_dir / "mineru" / "content_list_v2.json").read_text(
        encoding="utf-8"
    )
    for block in source_payload["blocks"]:
        assert f"block={block['id']}" in markdown
        assert 1 <= block["page_no"] <= 2
    assert "Figure 1. Main result." in markdown
    assert "Table 1. Accuracy." in markdown
    assert "E = mc^2" in markdown
    assert first == second
    assert first_hash == builder.bundle_hash(second, workspace)


def test_source_bundle_falls_back_to_page_metadata(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        material_id = _material(client)

    materials = app.state.services.materials
    pages = materials.pages(material_id)
    embedded_path = tmp_path / "processed" / material_id / "embedded_images" / "figure.png"
    embedded_path.parent.mkdir(parents=True, exist_ok=True)
    embedded_path.write_bytes(b"embedded-figure")
    embedded = PageImage(
        id="embedded_figure_1",
        material_id=material_id,
        page_id=pages[0].id,
        page_no=1,
        image_path=str(embedded_path),
        width=640,
        height=480,
        description="Embedded fallback figure",
    )
    pages[0] = pages[0].model_copy(update={"embedded_images": [embedded]})
    materials.repository.replace_pages(material_id, pages)

    builder = PaperSourceBundleBuilder(materials)
    workspace = tmp_path / "runtime" / "paper_workflows" / "paper_job_fallback"
    bundle = builder.build(material_id=material_id, workspace=workspace)
    payload = json.loads(
        (workspace / "source" / bundle.paper_source_path).read_text(encoding="utf-8")
    )

    assert payload["parser_source"] == "page_metadata"
    assert [block["page_no"] for block in payload["blocks"]] == [1, 1, 2]
    assert all(block["id"].startswith("block_") for block in payload["blocks"])
    assert payload["assets"][0]["source"] == "pdf"
    assert payload["assets"][0]["path"].startswith("existing_assets/")
    assert (workspace / "source" / payload["assets"][0]["path"]).read_bytes() == (
        b"embedded-figure"
    )
    markdown = (workspace / "source" / bundle.paper_content_path).read_text(encoding="utf-8")
    assert "Embedded fallback figure" in markdown
