import json
from pathlib import Path

import pytest

from metaclass.modules.materials.schemas import PageMetadata, SourceRef
from metaclass.modules.paper_workflow.paper_deck import (
    PaperDeckBuilder,
    PaperDeckContractError,
)
from metaclass.modules.paper_workflow.schemas import (
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperSourceBundle,
    PresentationOutline,
    SlideEvidence,
)


def _page(number: int) -> PageMetadata:
    return PageMetadata(
        id=f"deck_page_{number:03d}",
        material_id="mat_deck",
        page_no=number,
        title=f"Slide {number}",
        raw_text=f"Visible slide {number}",
        image_path=f"/tmp/page_{number:03d}.png",
        source_refs=[
            SourceRef(
                material_id="mat_deck",
                page_id=f"deck_page_{number:03d}",
                page_no=number,
            )
        ],
    )


def _outline() -> PresentationOutline:
    return PresentationOutline.model_validate(
        {
            "title": "Evidence-aware paper class",
            "subtitle": "A reconciled deck",
            "paper_type": "methods",
            "narrative_arc": "problem-to-solution",
            "objectives": ["Explain the core claim"],
            "structure_summary": "Motivation followed by evidence.",
            "sections": [
                {
                    "id": "section_main",
                    "title": "Main argument",
                    "role": "method",
                    "content_goal": "Explain and assess the method.",
                    "slide_ids": ["slide_01", "slide_02"],
                }
            ],
            "slides": [
                {
                    "id": "slide_01",
                    "order": 1,
                    "title": "The paper asks a concrete question",
                    "purpose": "Introduce the problem.",
                    "key_points": ["Problem"],
                    "speaker_note": "Outline note one.",
                    "layout_intent": "title_and_body",
                },
                {
                    "id": "slide_02",
                    "order": 2,
                    "title": "The evidence supports the core claim",
                    "purpose": "Interpret the result.",
                    "key_points": ["Evidence"],
                    "asset_ids": ["fig_01"],
                    "speaker_note": "Outline note two.",
                    "layout_intent": "hero_figure",
                },
            ],
        }
    )


def _evidence() -> SlideEvidence:
    return SlideEvidence.model_validate(
        {
            "slides": [
                {
                    "slide_id": "slide_01",
                    "claim_ids": ["claim_01"],
                    "source_refs": [{"page_no": 1, "block_id": "block_001"}],
                },
                {
                    "slide_id": "slide_02",
                    "claim_ids": ["claim_02"],
                    "asset_ids": ["fig_01"],
                    "source_refs": [{"page_no": 2, "asset_id": "asset_figure_01"}],
                },
            ]
        }
    )


def _analysis() -> PaperAnalysis:
    return PaperAnalysis.model_validate(
        {
            "paper_type": "methods",
            "central_question": "Can the method solve the target task?",
            "knowledge_gap": "Existing methods lack grounded evidence.",
            "main_claim": "The method improves the target task.",
            "method_summary": {"approach": "An evidence-aware pipeline."},
            "claims": [
                {
                    "id": "claim_01",
                    "statement": "The method addresses the target problem.",
                    "importance": "core",
                    "confidence": 0.9,
                    "source_refs": [{"page_no": 1, "block_id": "block_001", "quote": "Core claim"}],
                },
                {
                    "id": "claim_02",
                    "statement": "The evaluation supports the method.",
                    "importance": "supporting",
                    "confidence": 0.8,
                    "source_refs": [
                        {"page_no": 2, "asset_id": "asset_figure_01", "quote": "Result"}
                    ],
                },
            ],
            "quantitative_results": [
                {
                    "id": "result_01",
                    "statement": "Accuracy improves by 5 points.",
                    "metric": "accuracy",
                    "value": 5,
                    "source_refs": [{"page_no": 2, "asset_id": "asset_figure_01"}],
                }
            ],
            "limitations": [
                {
                    "id": "limitation_01",
                    "statement": "The evaluation covers one benchmark.",
                    "source_refs": [{"page_no": 2, "block_id": "block_002"}],
                }
            ],
        }
    )


def _figures() -> FigureCatalog:
    return FigureCatalog.model_validate(
        {
            "figures": [
                {
                    "id": "fig_01",
                    "path": "assets/fig_01.png",
                    "page_no": 2,
                    "caption": "Accuracy comparison across methods",
                    "source_method": "mineru",
                    "supports_claim_ids": ["claim_02"],
                    "quality": {"width": 1200, "height": 800, "readable": True},
                }
            ]
        }
    )


def _source_bundle() -> PaperSourceBundle:
    return PaperSourceBundle.model_validate(
        {
            "material_id": "mat_paper",
            "file_hash": "sha256:" + "a" * 64,
            "page_count": 2,
            "pdf_path": "paper.pdf",
            "paper_source_path": "paper_source.json",
            "paper_content_path": "paper_content.md",
            "asset_directory": "existing_assets",
            "blocks": [
                {
                    "id": "block_001",
                    "type": "paragraph",
                    "page_no": 1,
                    "section_path": ["Introduction"],
                    "text": "The method addresses the target problem under the stated assumptions.",
                },
                {
                    "id": "block_002",
                    "type": "paragraph",
                    "page_no": 2,
                    "section_path": ["Discussion"],
                    "text": "The evaluation is limited to one benchmark.",
                },
            ],
            "assets": [
                {
                    "id": "asset_figure_01",
                    "type": "figure",
                    "page_no": 2,
                    "path": "existing_assets/figure.png",
                    "caption": "Accuracy comparison across methods",
                }
            ],
        }
    )


def _bundle() -> PaperArtifactBundle:
    return PaperArtifactBundle.model_construct(
        id="paper_bundle_test",
        job_id="paper_job_test",
        source_material_id="mat_paper",
        provider="composed_skills",
        root_path="runtime/paper_workflows/paper_job_test/final",
        files=[],
        validation_status="passed",
        derived_material_id="mat_deck",
    )


def test_paper_deck_builder_preserves_final_pages_authoring_notes_and_evidence(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "speaker_notes.json"
    notes.write_text(
        json.dumps(
            [
                {"slide_id": "slide_01", "note": "Final authoring note one."},
                {"slide_id": "slide_02", "note": "Final authoring note two."},
            ]
        ),
        encoding="utf-8",
    )

    content, plan = PaperDeckBuilder().build(
        job_id="paper_job_test",
        bundle=_bundle(),
        deck_material_id="mat_deck",
        source_paper_material_id="mat_paper",
        pages=[_page(1), _page(2)],
        analysis=_analysis(),
        figures=_figures(),
        source_bundle=_source_bundle(),
        outline=_outline(),
        evidence=_evidence(),
        speaker_notes_path=notes,
        audience="研究生",
    )

    assert content.organization_mode == "paper_deck"
    assert content.material_ids == ["mat_deck", "mat_paper"]
    assert content.sections[0].page_nos == [1, 2]
    assert content.knowledge_units
    assert content.knowledge_tree is not None
    assert content.sections[0].tree_node_ids
    assert content.knowledge_units[0].source_refs[0].material_id == "mat_paper"
    assert content.knowledge_units[0].page_refs[0].material_id == "mat_deck"
    assigned = {
        unit_id for node in content.knowledge_tree.nodes for unit_id in node.knowledge_unit_ids
    }
    assert assigned == {unit.id for unit in content.knowledge_units}
    assert plan.mode == "paper_deck"
    assert plan.source_material_id == "mat_deck"
    assert plan.source_paper_material_id == "mat_paper"
    assert [slide.source_page_no for slide in plan.slides] == [1, 2]
    assert len(plan.slides[0].speaker_script) >= 120
    assert plan.slides[0].speaker_script != "Final authoring note one."
    assert plan.slides[0].authoring_note == "Final authoring note one."
    assert plan.slides[0].speaker_script_source == "paper_classroom_composer"
    assert plan.slides[0].paper_evidence_packet is not None
    assert plan.slides[0].paper_evidence_packet["evidence_contexts"][0]["block_id"] == ("block_001")
    assert "under the stated assumptions" in plan.slides[0].speaker_script
    assert "claim_01" in plan.slides[0].paper_claim_ids
    assert "The method addresses the target problem." in plan.slides[0].speaker_script
    assert plan.slides[1].paper_claim_ids == ["claim_02"]
    assert plan.slides[1].paper_asset_ids == ["fig_01"]
    assert plan.slides[1].paper_source_refs[0]["page_no"] == 2


def test_paper_deck_builder_rejects_final_page_drift(tmp_path: Path) -> None:
    notes = tmp_path / "speaker_notes.json"
    notes.write_text("[]", encoding="utf-8")

    with pytest.raises(PaperDeckContractError, match="continuous"):
        PaperDeckBuilder().build(
            job_id="paper_job_test",
            bundle=_bundle(),
            deck_material_id="mat_deck",
            source_paper_material_id="mat_paper",
            pages=[_page(1)],
            analysis=_analysis(),
            figures=_figures(),
            source_bundle=_source_bundle(),
            outline=_outline(),
            evidence=_evidence(),
            speaker_notes_path=notes,
            audience="研究生",
        )
