from pathlib import Path

import pytest
from PIL import Image

from metaclass.modules.paper_workflow.figure_catalog import (
    MinerUFigureCatalogBuilder,
    validate_figure_catalog,
)
from metaclass.modules.paper_workflow.schemas import (
    FigureAsset,
    FigureCatalog,
    FigureQuality,
    PaperAnalysis,
    PaperSourceBundle,
    SourceAsset,
)

HASH = "a" * 64


def _analysis() -> PaperAnalysis:
    return PaperAnalysis.model_validate(
        {
            "paper_type": "methods",
            "central_question": "What works?",
            "knowledge_gap": "Existing systems are limited.",
            "main_claim": "The method works.",
            "claims": [
                {
                    "id": "claim_core",
                    "statement": "The method works.",
                    "importance": "core",
                    "confidence": 0.9,
                    "source_refs": [{"page_no": 1}],
                }
            ],
            "figure_candidates": [
                {
                    "id": "candidate_1",
                    "reason": "Core method figure",
                    "claim_ids": ["claim_core"],
                    "source_refs": [{"page_no": 1, "asset_id": "asset_figure_001_001"}],
                }
            ],
        }
    )


def _source(count: int) -> PaperSourceBundle:
    return PaperSourceBundle(
        material_id="mat_1",
        file_hash=HASH,
        page_count=count,
        pdf_path="paper.pdf",
        paper_source_path="paper_source.json",
        paper_content_path="paper_content.md",
        asset_directory="existing_assets",
        parser_source="content_list_v2",
        assets=[
            SourceAsset(
                id=f"asset_figure_{index:03d}_001",
                type="figure",
                page_no=index,
                path=f"existing_assets/figure_{index}.png",
                caption=f"Figure {index}. Experimental evidence.",
                source="mineru",
            )
            for index in range(1, count + 1)
        ],
    )


def test_required_asset_formula_is_clamped() -> None:
    builder = MinerUFigureCatalogBuilder()

    assert builder.required_asset_count(1) == 4
    assert builder.required_asset_count(20) == 9
    assert builder.required_asset_count(100) == 10


def test_usable_mineru_assets_skip_skill_extraction(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    source_dir = workspace / "source" / "existing_assets"
    source_dir.mkdir(parents=True)
    for index in range(1, 6):
        Image.new("RGB", (900, 600), "white").save(source_dir / f"figure_{index}.png")

    builder = MinerUFigureCatalogBuilder()
    catalog, initial = builder.build(
        workspace=workspace,
        source=_source(5),
        analysis=_analysis(),
        output_assets=workspace / "stages" / "02_figures" / "output" / "assets",
    )
    decision = builder.decision_for_duration(initial, 15)

    assert len(catalog.figures) == 5
    assert decision.required == 5
    assert decision.needs_extraction is False
    assert catalog.figures[0].supports_claim_ids == ["claim_core"]


def test_validator_rejects_unknown_claim_and_decorative_asset(tmp_path: Path) -> None:
    output = tmp_path / "output"
    assets = output / "assets"
    assets.mkdir(parents=True)
    Image.new("RGB", (800, 500), "white").save(assets / "logo.png")
    catalog = FigureCatalog(
        figures=[
            FigureAsset(
                id="fig_logo",
                path="assets/logo.png",
                page_no=1,
                caption="Publisher logo",
                source_method="pdf_crop",
                supports_claim_ids=["missing_claim"],
                quality=FigureQuality(width=800, height=500, readable=True),
                crop_notes="axes, legend, and caption preserved",
            )
        ]
    )

    with pytest.raises(ValueError, match="decorative asset"):
        validate_figure_catalog(catalog, output=output, analysis=_analysis())


@pytest.mark.parametrize(
    ("figure_id", "original_figure", "notes"),
    [
        ("fig_results", "Figure 1", "完整保留图例、模型标签及 Figure 1 caption。"),
        ("fig_plot", "Figure 7", "保留横轴、纵轴、图例和 caption。"),
        ("table_results", "Table 2", "完整保留 Table 2 caption、列标题及所有数值。"),
    ],
)
def test_validator_accepts_multilingual_applicable_crop_attestations(
    tmp_path: Path,
    figure_id: str,
    original_figure: str,
    notes: str,
) -> None:
    output = tmp_path / figure_id
    assets = output / "assets"
    assets.mkdir(parents=True)
    Image.new("RGB", (800, 500), "white").save(assets / "evidence.png")
    catalog = FigureCatalog(
        figures=[
            FigureAsset(
                id=figure_id,
                path="assets/evidence.png",
                original_figure=original_figure,
                page_no=1,
                caption=f"{original_figure}. Experimental evidence.",
                source_method="pdf_crop",
                supports_claim_ids=["claim_core"],
                quality=FigureQuality(width=800, height=500, readable=True),
                crop_notes=notes,
            )
        ]
    )

    validate_figure_catalog(catalog, output=output, analysis=_analysis())


def test_validator_does_not_require_typeset_caption_inside_uncropped_arxiv_asset(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    assets = output / "assets"
    assets.mkdir(parents=True)
    Image.new("RGB", (800, 500), "white").save(assets / "source.png")
    catalog = FigureCatalog(
        figures=[
            FigureAsset(
                id="fig_source",
                path="assets/source.png",
                original_figure="Figure 1",
                page_no=1,
                caption="Figure 1. Experimental evidence.",
                source_method="arxiv_source",
                supports_claim_ids=["claim_core"],
                quality=FigureQuality(width=800, height=500, readable=True),
                crop_notes="Uncropped author-supplied source figure; all labels preserved.",
            )
        ]
    )

    validate_figure_catalog(catalog, output=output, analysis=_analysis())
