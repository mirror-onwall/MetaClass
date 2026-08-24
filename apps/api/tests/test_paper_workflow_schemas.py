from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from metaclass.modules.paper_workflow.schemas import (
    COMPOSED_STAGE_BOUNDARIES,
    ArtifactFile,
    ComposedStage,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperWorkflowJob,
    PaperWorkflowRequest,
    PaperWorkflowStatus,
    PresentationOutline,
    StageExecutionReport,
    StageStatus,
)

HASH = "a" * 64
NOW = datetime(2026, 8, 19, tzinfo=UTC)


def test_composed_stage_boundaries_are_frozen_in_order() -> None:
    assert [item.stage for item in COMPOSED_STAGE_BOUNDARIES] == [
        ComposedStage.ANALYSIS,
        ComposedStage.FIGURES,
        ComposedStage.OUTLINE,
        ComposedStage.GENERATION,
    ]
    assert COMPOSED_STAGE_BOUNDARIES[0].produces[0] == "PaperAnalysis"
    assert COMPOSED_STAGE_BOUNDARIES[-1].produces[0] == "PaperArtifactBundle"


def test_composed_request_can_use_default_presentation_profile() -> None:
    request = PaperWorkflowRequest(material_id="mat_shapefile_gpt", strategy="composed_skills")
    assert request.duration_minutes is None
    assert request.language == "zh-CN"


def test_nature_request_allows_deferred_presentation_context() -> None:
    request = PaperWorkflowRequest(material_id="mat_kimi_k3", strategy="nature_paper2ppt")

    assert request.duration_minutes is None
    assert request.audience is None


def test_succeeded_job_requires_bundle_and_derived_material() -> None:
    with pytest.raises(ValidationError, match="requires artifact_bundle_id"):
        PaperWorkflowJob(
            id="paper_job_001",
            source_material_id="mat_shapefile_gpt",
            status=PaperWorkflowStatus.SUCCEEDED,
        )


def test_paper_analysis_requires_core_claim_and_evidence() -> None:
    analysis = PaperAnalysis.model_validate(
        {
            "paper_type": "methods",
            "central_question": "How can an agent operate on shapefiles?",
            "knowledge_gap": "Existing GIS workflows require manual tool composition.",
            "main_claim": "The agent composes GIS tools from natural language.",
            "claims": [
                {
                    "id": "claim_01",
                    "statement": "The system composes a GIS workflow.",
                    "importance": "core",
                    "confidence": 0.9,
                    "source_refs": [{"page_no": 3, "block_id": "block_003_001"}],
                }
            ],
        }
    )
    assert analysis.claims[0].source_refs[0].page_no == 3


def test_outline_requires_continuous_order_and_exact_section_coverage() -> None:
    with pytest.raises(ValidationError, match="continuous"):
        PresentationOutline.model_validate(
            {
                "title": "Kimi K3",
                "paper_type": "methods",
                "narrative_arc": "problem_to_solution",
                "structure_summary": "Architecture followed by evaluation.",
                "sections": [
                    {
                        "id": "section_01",
                        "title": "Method",
                        "role": "method",
                        "content_goal": "Explain the model.",
                        "slide_ids": ["slide_01"],
                    }
                ],
                "slides": [
                    {
                        "id": "slide_01",
                        "order": 2,
                        "title": "Architecture",
                        "purpose": "Explain the model.",
                        "layout_intent": "hero_figure",
                    }
                ],
            }
        )


def test_successful_stage_report_requires_validated_outputs() -> None:
    with pytest.raises(ValidationError, match="requires outputs"):
        StageExecutionReport(
            stage="analysis",
            skill_name="paper-analyze",
            skill_version="test",
            prompt_version="v1",
            runtime_version="v1",
            status=StageStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
            input_hash=HASH,
            validation_passed=True,
        )


def test_artifact_bundle_requires_all_canonical_roles() -> None:
    def artifact(role: str) -> ArtifactFile:
        return ArtifactFile(
            role=role,
            path=f"{role}.json",
            sha256=HASH,
            media_type="application/json",
        )

    roles = [
        "presentation",
        "analysis",
        "outline",
        "slide_evidence",
        "asset_manifest",
        "speaker_notes",
        "generation_report",
        "qa_report",
    ]
    bundle = PaperArtifactBundle(
        id="paper_bundle_001",
        job_id="paper_job_001",
        source_material_id="mat_shapefile_gpt",
        provider="composed_skills",
        root_path="final",
        files=[artifact(role) for role in roles],
        validation_status="passed",
    )
    assert len(bundle.files) == 8
