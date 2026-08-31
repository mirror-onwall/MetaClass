"""Run a real planning-only Paper Deck smoke test against one PDF.

The script deliberately stops before image or PPTX generation. It creates a
normalized PaperSourceBundle, invokes the repository-pinned paper-deck Skill,
and audits the five planning artifacts for traceability and narrative coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile

from metaclass.core.application import build_services
from metaclass.core.config import settings
from metaclass.modules.paper_workflow.paper_deck_planning import (
    PaperDeckPlan,
    PaperDeckPlanningAudit,
    PaperDeckPlanningAuditor,
    PaperDeckSlideEvidenceDraft,
)
from metaclass.modules.paper_workflow.runtime import CodexSkillInvocation, CodexSkillRuntime
from metaclass.modules.paper_workflow.schemas import ComposedStage, StageStatus
from metaclass.modules.paper_workflow.source_bundle import PaperSourceBundleBuilder

EXPECTED_OUTPUTS = (
    "analysis.md",
    "deck-brief.md",
    "outline.md",
    "paper_deck_plan.json",
    "slide_evidence_draft.json",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path, help="Local PDF to analyze")
    parser.add_argument("--data-dir", type=Path, required=True, help="Isolated MetaClass data dir")
    parser.add_argument("--output-dir", type=Path, required=True, help="New smoke-test workspace")
    parser.add_argument("--duration", type=int, default=30, choices=range(5, 121), metavar="5-120")
    parser.add_argument("--audience", default="具备基础专业背景的高校学生和研究生")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument(
        "--depth", choices=("introductory", "standard", "advanced"), default="standard"
    )
    parser.add_argument("--minimum-slides", type=int)
    parser.add_argument("--maximum-slides", type=int)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--model", default=settings.codex_model)
    return parser.parse_args()


def slide_range(duration: int, minimum: int | None, maximum: int | None) -> tuple[int, int]:
    lower = minimum if minimum is not None else max(6, math.ceil(duration * 0.35))
    upper = maximum if maximum is not None else max(lower, math.ceil(duration * 0.6))
    if lower < 1 or upper < lower:
        raise ValueError("invalid slide-count range")
    return lower, upper


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def input_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


async def upload_pdf(services, paper: Path):
    with paper.open("rb") as stream:
        material = await services.materials.create(UploadFile(stream, filename=paper.name))
    services.materials.parse(material.id)
    return material


def markdown_audit(audit: PaperDeckPlanningAudit) -> str:
    lines = [
        "# Paper Deck planning smoke audit",
        "",
        f"- Automated traceability result: **{'PASS' if audit.passed else 'FAIL'}**",
        f"- Slides: `{audit.slide_count}`",
        f"- Grounded claims: `{audit.grounded_claim_count}`",
        f"- Selected source figures/tables: `{audit.selected_figure_count}`",
        "- Human semantic review required: **YES**",
        "",
        "The automated gate proves reference existence, quote containment, number grounding,",
        "slide/evidence identity, section accounting, and basic narrative structure. A human must",
        "still judge whether the contribution wording is faithful, the selected figures are the",
        "best choices, and the model inventory omitted no important method or experiment.",
        "",
        "## Findings",
        "",
    ]
    lines.extend(
        f"- `{item.severity.upper()}` `{item.code}` — {item.message}"
        + (f" (`{item.path}`)" if item.path else "")
        for item in audit.findings
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    paper = args.paper.resolve()
    data_dir = args.data_dir.resolve()
    workspace = args.output_dir.resolve()
    if not paper.is_file() or paper.suffix.lower() != ".pdf":
        raise ValueError("paper must be an existing local PDF")
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(
            "output-dir must be absent or empty; existing smoke artifacts are preserved"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    minimum_slides, maximum_slides = slide_range(
        args.duration, args.minimum_slides, args.maximum_slides
    )

    services = build_services(data_dir, force_fake_llm=False)
    try:
        material = asyncio.run(upload_pdf(services, paper))
        bundle = PaperSourceBundleBuilder(services.materials).build(
            material_id=material.id,
            workspace=workspace,
        )
    finally:
        services.database.dispose()

    stage_root = workspace / "planning_stage"
    output = workspace / "output"
    stage_root.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    request = {
        "audience": args.audience,
        "duration_minutes": args.duration,
        "language": args.language,
        "depth": args.depth,
        "style_preset": "journal-minimal",
        "minimum_slides": minimum_slides,
        "maximum_slides": maximum_slides,
        "generation_scope": "planning_only_no_images_no_pptx",
    }
    request_path = workspace / "resolved_request.json"
    plan_schema = stage_root / "paper_deck_plan_schema.json"
    evidence_schema = stage_root / "slide_evidence_draft_schema.json"
    runtime_schema = stage_root / "runtime_result_schema.json"
    prompt_path = stage_root / "prompt.md"
    write_json(request_path, request)
    write_json(plan_schema, PaperDeckPlan.model_json_schema())
    write_json(evidence_schema, PaperDeckSlideEvidenceDraft.model_json_schema())
    write_json(
        runtime_schema,
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
    )
    prompt_path.write_text(
        (
            Path(__file__).parents[1]
            / "src/metaclass/modules/paper_workflow/prompts/paper_deck_planning_smoke.md"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    inputs = [
        workspace / "source/paper.pdf",
        workspace / "source/paper_source.json",
        workspace / "source/paper_content.md",
        request_path,
        plan_schema,
        evidence_schema,
    ]
    skill = (settings.codex_paper_craft_skills_dir / "paper-deck").resolve()
    report = CodexSkillRuntime(model=args.model).run(
        CodexSkillInvocation(
            stage=ComposedStage.OUTLINE,
            skill_name="paper-deck",
            skill_directory=skill,
            skill_version="paper-craft-3be47a2",
            prompt_version="paper-deck-planning-smoke-v1",
            input_hash=input_hash(inputs),
            workspace=workspace,
            prompt_path=prompt_path,
            input_paths=tuple(inputs),
            output_directory=output,
            expected_outputs=EXPECTED_OUTPUTS,
            output_schema_path=runtime_schema,
            timeout_seconds=args.timeout,
            network_enabled=False,
        )
    )
    (stage_root / "execution_report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    if report.status != StageStatus.SUCCEEDED:
        print(
            json.dumps(
                {
                    "passed": False,
                    "stage": "runtime",
                    "report": str(stage_root / "execution_report.json"),
                },
                ensure_ascii=False,
            )
        )
        return 2

    actual_outputs = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
    unexpected_outputs = sorted(actual_outputs - set(EXPECTED_OUTPUTS))
    if unexpected_outputs:
        print(
            json.dumps(
                {
                    "passed": False,
                    "stage": "planning_scope",
                    "error": "paper-deck created files outside the planning-only contract",
                    "unexpected_outputs": unexpected_outputs,
                },
                ensure_ascii=False,
            )
        )
        return 2

    plan = PaperDeckPlan.model_validate_json(
        (output / "paper_deck_plan.json").read_text(encoding="utf-8")
    )
    evidence = PaperDeckSlideEvidenceDraft.model_validate_json(
        (output / "slide_evidence_draft.json").read_text(encoding="utf-8")
    )
    audit = PaperDeckPlanningAuditor().audit(
        bundle,
        plan,
        evidence,
        minimum_slides=minimum_slides,
        maximum_slides=maximum_slides,
    )
    write_json(output / "planning_audit.json", audit.model_dump(mode="json"))
    (output / "planning_audit.md").write_text(markdown_audit(audit), encoding="utf-8")
    write_json(
        output / "smoke_metadata.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "paper": str(paper),
            "material_id": material.id,
            "source_file_hash": bundle.file_hash,
            "skill_commit": report.skill_commit,
            "model": report.model,
            "minimum_slides": minimum_slides,
            "maximum_slides": maximum_slides,
        },
    )
    print(
        json.dumps(
            {
                "passed": audit.passed,
                "requires_human_review": audit.requires_human_review,
                "output": str(output),
                "audit": str(output / "planning_audit.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if audit.passed else 1


def main() -> int:
    try:
        return run(arguments())
    except Exception as exc:  # noqa: BLE001 - CLI must emit an auditable fatal result
        print(
            json.dumps(
                {"passed": False, "fatal_error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
