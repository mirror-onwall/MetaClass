"""Generate an editable PPTX from a frozen Paper Deck PresentationPlan.

This is a thin workflow smoke entrypoint around main's configured
CodexPPTProvider. The provider remains responsible for paper-deck + paper-comic
skill orchestration, editable-layer generation, validation, rendering, and repair.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metaclass.core.application import build_codex_ppt_provider
from metaclass.core.config import settings
from metaclass.modules.presentation.schemas import PresentationPlan
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.presentation.themes import get_presentation_theme


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("presentation_plan", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-id", default="paper_deck_ppt_smoke")
    parser.add_argument("--theme-id")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    plan_path = args.presentation_plan.resolve()
    output_dir = args.output_dir.resolve()
    if not plan_path.is_file():
        raise ValueError("presentation_plan must be an existing JSON file")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output-dir must be absent or empty; existing artifacts are preserved")
    plan = PresentationPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    if plan.mode != "paper_deck":
        raise ValueError("paper deck generation requires PresentationPlan.mode=paper_deck")
    if any(slide.source_kind != "generated" for slide in plan.slides):
        raise ValueError("pre-generation paper deck slides must use source_kind=generated")

    adapter = PPTSkillAdapter(libreoffice_bin=settings.libreoffice_bin)
    provider = build_codex_ppt_provider(adapter)
    if not provider.paper_craft_enabled:
        raise ValueError("Codex paper-craft orchestration is not enabled")
    artifact = provider.prepare_request(
        plan=plan,
        job_id=args.job_id,
        output_dir=output_dir,
        theme=get_presentation_theme(args.theme_id),
    )
    (output_dir / "artifact.json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "artifact_id": artifact.id,
                "pptx_path": artifact.pptx_path,
                "slide_count": len(artifact.slide_images),
                "provider": "codex",
                "skill_request_path": artifact.skill_request_path,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    try:
        return run(arguments())
    except Exception as exc:  # noqa: BLE001 - CLI must emit an auditable fatal result
        print(
            json.dumps(
                {"passed": False, "fatal_error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
