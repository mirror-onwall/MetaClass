import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event, Lock
from typing import Any, Callable, ClassVar

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from metaclass.modules.presentation.brand_palette import apply_brand_palette
from metaclass.modules.presentation.layout_registry import (
    LAYOUT_REGISTRY,
    LayoutSpec,
    select_fallback_layout,
)
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.providers import validate_deck_against_plan
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PresentationPlan,
    SlideElement,
    SlidePlan,
)
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.presentation.themes import (
    PresentationTheme,
    get_presentation_theme,
)


class CodexGenerationError(RuntimeError):
    """A Codex generation failure that is eligible for provider fallback."""


class CodexPPTProvider:
    """Generate an editable PPTX with a pinned Codex non-interactive runtime."""

    PROMPT_VERSION = "2026-09-02-v19-paper-deck-layout-rhythm"
    PAPER_DECK_STYLE_PRESET = "journal-minimal"
    PAPER_DECK_MANIFEST_VERSION = 3
    PAPER_DECK_STYLE_SIGNATURE_VERSION = "v3"
    PAPER_DECK_MAX_VISUAL_PLACEHOLDERS = 1
    PAPER_DECK_MAX_VISUAL_MODULES = 16
    PAPER_DECK_MAX_VISUAL_ASSETS = 2
    PAPER_DECK_MIN_MODULE_GAP = 0.015
    PAPER_DECK_VISUAL_COLUMN_MIN_WIDTH = 0.32
    PAPER_DECK_VISUAL_COLUMN_MAX_WIDTH = 0.44
    PAPER_DECK_VISUAL_COLUMN_MIN_HEIGHT = 0.44
    PAPER_DECK_VISUAL_COLUMN_MAX_HEIGHT = 0.74
    PAPER_DECK_LOCAL_VISUAL_MIN_WIDTH = 0.14
    PAPER_DECK_LOCAL_VISUAL_MIN_HEIGHT = 0.18
    PAPER_DECK_VISUAL_REGION_MIN_WIDTH = 0.24
    PAPER_DECK_VISUAL_REGION_MIN_HEIGHT = 0.18
    PAPER_DECK_VISUAL_REGION_MIN_AREA = 0.08
    PAPER_DECK_VISUAL_REGION_MAX_WIDTH = 0.82
    PAPER_DECK_VISUAL_REGION_MAX_HEIGHT = 0.76
    PAPER_DECK_VISUAL_COLUMN_GAP = 0.03
    PAPER_DECK_VISUAL_COLUMN_LEFT_MAX_X = 0.10
    PAPER_DECK_VISUAL_COLUMN_RIGHT_MIN_EDGE = 0.90
    PAPER_DECK_STYLE_PROMPT = (
        "Nature / IEEE-inspired minimal academic presentation slide, "
        "publication-quality scientific figure aesthetic, clean white or light gray "
        "content background, precise method diagrams, compact readable labels, "
        "credible paper-submission visual style, restrained and professional"
    )
    PAPER_CRAFT_SOURCE = "https://github.com/zsyggg/paper-craft-skills"
    PAPER_CRAFT_COMMIT = "3be47a2a53cc35a411c587bca5231a08de57287a"
    PAPER_CRAFT_SKILLS = ("paper-deck", "paper-comic")
    PAPER_CRAFT_EXPECTED_SHA256: ClassVar[dict[str, str]] = {
        "paper-deck": "3ca4baef8071e41939c7672d4dfe284b55091e9a094c871eba35fd78f1e6efdb",
        "paper-comic": "56d605c5d5fc005086c1574fc640554752580fc2d6b523be30e7739c0abe2fcb",
    }
    PAPER_CRAFT_MAX_FILE_BYTES = 20 * 1024 * 1024
    PAPER_CRAFT_MAX_TOTAL_BYTES = 120 * 1024 * 1024
    PAPER_CRAFT_MAX_PIXELS = 16_777_216
    PAPER_CRAFT_MAX_GUIDANCE_BYTES = 40_000
    PAPER_CRAFT_RUNTIME_GUIDANCE_FILES = {
        "paper-deck": {
            "SKILL.md",
            "references/guidance.md",
            "references/layouts.md",
            "references/notes.txt",
            "references/source-visuals.md",
            "references/style-system.md",
        },
        "paper-comic": {
            "SKILL.md",
            "references/guidance.md",
            "references/notes.txt",
            "references/styles/paper-figure.md",
        },
    }
    PAPER_CRAFT_BACKGROUND_SIZE = (1920, 1080)
    PAPER_CRAFT_BACKGROUND_MIN_SIZE = (640, 360)
    PAPER_CRAFT_BACKGROUND_ASPECT_RANGE = (1.4, 2.0)
    PAPER_CRAFT_SAFE_MARGIN_X = 0.045
    PAPER_CRAFT_SAFE_MARGIN_Y = 0.04
    PAPER_CRAFT_SAFE_TOLERANCE_PX = 1

    def __init__(
        self,
        *,
        adapter: PPTSkillAdapter,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 900.0,
        repair_attempts: int = 1,
        codex_bin: str | None = None,
        paper_craft_enabled: bool = False,
        paper_craft_max_images: int = 24,
        paper_craft_concurrency: int = 1,
        paper_craft_skills_dir: str | Path | None = None,
    ) -> None:
        if not 0 <= paper_craft_max_images <= 64:
            raise ValueError("paper_craft_max_images must be between 0 and 64")
        if not 1 <= paper_craft_concurrency <= 4:
            raise ValueError("paper_craft_concurrency must be between 1 and 4")
        self.adapter = adapter
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.repair_attempts = repair_attempts
        self.codex_bin = codex_bin
        self.paper_craft_enabled = paper_craft_enabled
        self.paper_craft_max_images = paper_craft_max_images
        self.paper_craft_concurrency = paper_craft_concurrency
        self.paper_craft_skills_dir = (
            Path(paper_craft_skills_dir).expanduser().resolve()
            if paper_craft_skills_dir is not None
            else None
        )
        self._paper_craft_capability: bool | None = None
        self._paper_craft_capability_reason: str | None = None

    def prepare_request(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
        theme: PresentationTheme | None = None,
        progress_callback: Callable[[PPTArtifact, int, int], None] | None = None,
    ) -> PPTArtifact:
        selected_theme = theme or get_presentation_theme()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            pptx_path, metadata, preview_plan = self._generate_validated_deck(
                plan=plan,
                job_id=job_id,
                output_dir=output_dir,
                theme=selected_theme,
                progress_callback=progress_callback,
            )
        except CodexGenerationError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise CodexGenerationError(f"Codex PPT generation failed: {exc}") from exc

        # Preview rendering is shared infrastructure. Let its error propagate instead
        # of spending Presenton credits on a fallback that would use the same renderer.
        artifact = self.adapter.prepare_external_pptx(
            plan=plan,
            job_id=job_id,
            output_dir=output_dir,
            pptx_path=pptx_path,
            provider_name="codex",
            provider_metadata=metadata,
            external_slide_images=None,
            preview_plan=preview_plan,
        )
        if progress_callback is not None:
            artifact = artifact.model_copy(update={"id": self._artifact_id_for_job(job_id)})
        return artifact

    def _generate_validated_deck(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
        theme: PresentationTheme,
        progress_callback: Callable[[PPTArtifact, int, int], None] | None = None,
    ) -> tuple[Path, dict[str, Any], PresentationPlan | None]:
        instructions_source = Path(__file__).with_name("codex_ppt_instructions.md")
        if not instructions_source.exists():
            raise CodexGenerationError("Codex PPT instructions are missing")

        plan_json = json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        plan_hash = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        request_path = output_dir / "codex_request.json"
        request_path.write_text(
            json.dumps(
                {
                    "provider": "codex",
                    "prompt_version": self.PROMPT_VERSION,
                    "model": self.model,
                    "theme": theme.prompt_payload(),
                    "plan_sha256": plan_hash,
                    "paper_craft": {
                        "enabled": self.paper_craft_enabled,
                        "mode": "skill-directed-editable-layers-plus-exact-plan-copy",
                        "max_images": self.paper_craft_max_images,
                        "maximum_source_images": len(plan.slides),
                        "concurrency": self.paper_craft_concurrency,
                        "source": self.PAPER_CRAFT_SOURCE,
                        "upstream_commit": self.PAPER_CRAFT_COMMIT,
                    },
                    "presentation_plan": plan.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        run_records: list[dict[str, Any]] = []
        validation_errors: list[str] = []
        validation: dict[str, Any] | None = None
        preview_plan: PresentationPlan | None = None
        generation_mode = "unknown"
        generated_image_count = 0
        paper_craft_attempt_count = 0
        vector_attempt_count = 0
        vector_repairs_used = 0
        paper_craft_fallback_reason: str | None = None
        staged_skills: list[dict[str, Any]] = []
        paper_craft_guidance = ""
        run_index = 0
        with tempfile.TemporaryDirectory(prefix=f"metaclass-{job_id}-") as temp_dir:
            workspace = Path(temp_dir)
            plan_path = workspace / "presentation_plan.json"
            plan_path.write_bytes(plan_json.encode("utf-8"))
            (workspace / "INSTRUCTIONS.md").write_text(
                instructions_source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self._write_output_schema(
                workspace / "result_schema.json",
                slide_count=len(plan.slides),
            )
            deck_path = workspace / "deck.pptx"

            if self.paper_craft_enabled and self.paper_craft_max_images == 0:
                paper_craft_fallback_reason = "paper-deck per-slide visual budget is zero"
            elif self.paper_craft_enabled and len(plan.slides) > self.paper_craft_max_images:
                paper_craft_fallback_reason = (
                    "paper-deck per-slide mode exceeds the isolated-slide safety limit: "
                    f"{len(plan.slides)} slides requested, safety limit is "
                    f"{self.paper_craft_max_images}"
                )
            elif self.paper_craft_enabled:
                try:
                    staged_skills = self._stage_paper_craft_skills(workspace)
                    paper_craft_guidance = self._embedded_paper_craft_guidance(
                        workspace,
                        staged_skills,
                    )
                except (OSError, ValueError) as exc:
                    staged_skills = []
                    paper_craft_guidance = ""
                    paper_craft_fallback_reason = f"paper-craft skills unavailable: {exc}"

            if staged_skills:
                first_attempt = run_index + 1
                try:
                    (
                        result,
                        paper_craft_records,
                        paper_craft_attempts_used,
                    ) = self._generate_paper_deck_hybrid_result(
                        plan=plan,
                        job_id=job_id,
                        plan_hash=plan_hash,
                        theme=theme,
                        workspace=workspace,
                        output_dir=output_dir,
                        instructions_text=instructions_source.read_text(encoding="utf-8"),
                        skill_guidance=paper_craft_guidance,
                        first_attempt=first_attempt,
                        progress_callback=progress_callback,
                    )
                except (CodexGenerationError, OSError, ValueError) as exc:
                    error = str(exc)
                else:
                    paper_craft_attempt_count += paper_craft_attempts_used
                    run_index += paper_craft_attempts_used
                    run_records.extend(paper_craft_records)
                    (
                        error,
                        validation,
                        preview_plan,
                        generation_mode,
                        generated_image_count,
                    ) = self._validate_attempt(
                        plan=plan,
                        plan_path=plan_path,
                        plan_json=plan_json,
                        plan_hash=plan_hash,
                        result=result,
                        deck_path=deck_path,
                        theme=theme,
                        workspace=workspace,
                        output_dir=output_dir,
                        allow_images=True,
                        require_image=True,
                    )
                if error:
                    paper_craft_fallback_reason = error
                    if self._is_paper_craft_capability_failure(error):
                        # Capability errors can be transient (login state, sandbox startup,
                        # or image service availability). Never poison the whole backend
                        # process after one job; the next request must be allowed to probe again.
                        self._paper_craft_capability = None
                        self._paper_craft_capability_reason = error
                    validation_errors.append(f"paper-craft hybrid: {error}")
                    validation = None
                    preview_plan = None
                    generated_image_count = 0
                    if not (output_dir / "partial_status.json").is_file():
                        self._clear_paper_craft_assets(output_dir)
                else:
                    self._paper_craft_capability = True
                    self._paper_craft_capability_reason = None

            if validation is None and self.paper_craft_enabled:
                detail = paper_craft_fallback_reason or "paper-deck per-slide generation failed"
                raise CodexGenerationError(
                    "Codex Paper Deck per-slide generation failed; refusing silent vector "
                    f"degradation: {detail}"
                )

            if validation is None:
                vector_errors: list[str] = []
                for repair_index in range(self.repair_attempts + 1):
                    run_index += 1
                    vector_attempt_count += 1
                    if staged_skills:
                        prompt = self._paper_craft_vector_prompt(
                            plan,
                            plan_hash,
                            theme,
                            validation_error=(vector_errors[-1] if repair_index > 0 else None),
                            skill_guidance=paper_craft_guidance,
                        )
                    else:
                        prompt = (
                            self._initial_prompt(plan, plan_hash, theme)
                            if repair_index == 0
                            else self._repair_prompt(
                                plan,
                                plan_hash,
                                vector_errors[-1],
                                theme,
                            )
                        )
                    try:
                        if staged_skills:
                            result = self._execute_codex(
                                workspace=workspace,
                                output_dir=output_dir,
                                prompt=prompt,
                                attempt=run_index,
                                run_mode="paper-craft-vector-fallback",
                            )
                        else:
                            result = self._execute_codex(
                                workspace=workspace,
                                output_dir=output_dir,
                                prompt=prompt,
                                attempt=run_index,
                            )
                    except CodexGenerationError as exc:
                        if paper_craft_fallback_reason:
                            raise CodexGenerationError(
                                "Codex paper-craft mode failed and the structured Codex fallback "
                                f"also failed: {exc}"
                            ) from exc
                        raise
                    run_records.append(
                        {
                            "mode": (
                                "paper-craft-vector-fallback"
                                if staged_skills
                                else "structured-vector"
                            ),
                            "result": result,
                        }
                    )
                    (
                        error,
                        validation,
                        preview_plan,
                        generation_mode,
                        generated_image_count,
                    ) = self._validate_attempt(
                        plan=plan,
                        plan_path=plan_path,
                        plan_json=plan_json,
                        plan_hash=plan_hash,
                        result=result,
                        deck_path=deck_path,
                        theme=theme,
                        workspace=workspace,
                        output_dir=output_dir,
                        allow_images=False,
                        require_image=False,
                    )
                    if not error:
                        vector_repairs_used = repair_index
                        break
                    vector_errors.append(error)
                    validation_errors.append(f"structured vector: {error}")
                    validation = None
                    preview_plan = None
                    if repair_index >= self.repair_attempts:
                        raise CodexGenerationError(
                            "Codex output violated the immutable PresentationPlan contract "
                            f"after {vector_attempt_count} structured attempt(s): {error}"
                        )

            if validation is None:
                raise CodexGenerationError("Codex did not produce a validated PPTX")
            if progress_callback is None:
                final_pptx_path = output_dir / "deck.pptx"
                staged_final_path = output_dir / "deck.finalizing.pptx"
            else:
                # Never replace a PPTX that the browser or PowerPoint may still
                # have open on Windows. A new published file is immutable.
                publication_id = time.time_ns()
                final_pptx_path = output_dir / f"deck.complete.{publication_id}.pptx"
                staged_final_path = output_dir / f"deck.complete.{publication_id}.building.pptx"
            shutil.copy2(deck_path, staged_final_path)
            staged_final_path.replace(final_pptx_path)

        return (
            final_pptx_path,
            {
                "runtime": "openai-codex-cli-bin",
                "prompt_version": self.PROMPT_VERSION,
                "model": self.model,
                "theme": theme.prompt_payload(),
                "plan_sha256": plan_hash,
                "attempt_count": run_index,
                "paper_craft_attempt_count": paper_craft_attempt_count,
                "vector_attempt_count": vector_attempt_count,
                "repair_attempts_used": vector_repairs_used,
                "validation_errors": validation_errors,
                "validation": validation,
                "responses": [
                    {
                        "run_mode": record["mode"],
                        **({"slide_id": record["slide_id"]} if record.get("slide_id") else {}),
                        **self._public_result(record["result"]),
                    }
                    for record in run_records
                ],
                "generation_mode": generation_mode,
                "paper_craft": {
                    "enabled": self.paper_craft_enabled,
                    "mode": "skill-directed-editable-layers-plus-exact-plan-copy",
                    "attempted": paper_craft_attempt_count > 0,
                    "max_images": self.paper_craft_max_images,
                    "required_image_count": sum(
                        int(self._paper_deck_visual_requirement(slide)["min_assets"])
                        for slide in plan.slides
                    ),
                    "maximum_generated_image_count": sum(
                        int(self._paper_deck_visual_requirement(slide)["max_assets"])
                        for slide in plan.slides
                    ),
                    "generated_image_count": generated_image_count,
                    "concurrency": self.paper_craft_concurrency,
                    "vector_guidance_used": bool(staged_skills and vector_attempt_count),
                    "capability_cached": self._paper_craft_capability,
                    "fallback_reason": paper_craft_fallback_reason,
                    "source": self.PAPER_CRAFT_SOURCE,
                    "upstream_commit": self.PAPER_CRAFT_COMMIT,
                    "staged_skills": staged_skills,
                },
                "content_contract": (
                    "exact-visible-title-and-key-points-plus-authorized-visual-placeholders"
                ),
                "speaker_script_binding": "presentation-plan-slide-id",
                "preview_source": (
                    "native-render-or-validated-slide-elements"
                    if preview_plan is not None
                    else "native-pptx-render"
                ),
            },
            preview_plan,
        )

    def _validate_attempt(
        self,
        *,
        plan: PresentationPlan,
        plan_path: Path,
        plan_json: str,
        plan_hash: str,
        result: dict[str, Any],
        deck_path: Path,
        theme: PresentationTheme,
        workspace: Path,
        output_dir: Path,
        allow_images: bool,
        require_image: bool,
    ) -> tuple[
        str,
        dict[str, Any] | None,
        PresentationPlan | None,
        str,
        int,
    ]:
        try:
            current_plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        except OSError:
            current_plan_hash = ""
        if current_plan_hash != plan_hash:
            plan_path.write_bytes(plan_json.encode("utf-8"))
            return (
                "Codex modified or removed the immutable presentation_plan.json",
                None,
                None,
                "unknown",
                0,
            )

        try:
            materialized_plan, generation_mode = self._materialize_result(
                plan=plan,
                result=result,
                deck_path=deck_path,
                theme=theme,
                workspace=workspace,
                asset_output_dir=output_dir / "codex_assets",
                allow_images=allow_images,
                require_image=require_image,
            )
            image_count = sum(
                element.type == "image"
                for slide in materialized_plan.slides
                for element in slide.elements
            )
            validation = validate_deck_against_plan(
                plan,
                deck_path,
                provider_name="Codex",
                materialized_plan=materialized_plan,
            )
        except (RuntimeError, ValueError) as exc:
            return str(exc), None, None, "unknown", 0
        preview_plan = materialized_plan if generation_mode != "direct-pptx" else None
        return "", validation, preview_plan, generation_mode, image_count

    def _generate_paper_deck_hybrid_result(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        plan_hash: str,
        theme: PresentationTheme,
        workspace: Path,
        output_dir: Path,
        instructions_text: str,
        skill_guidance: str,
        first_attempt: int,
        progress_callback: Callable[[PPTArtifact, int, int], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        """Generate one editable layered slide design in an isolated session per slide."""

        if len(plan.slides) > self.paper_craft_max_images:
            raise ValueError("paper-deck per-slide mode exceeds the configured image safety limit")

        slide_workspaces = workspace / "paper_deck_slides"
        slide_workspaces.mkdir()
        generated_visuals = workspace / "generated_visuals"
        generated_visuals.mkdir(exist_ok=True)
        prompt_dir = output_dir / "paper_deck_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        composition_plan = self._paper_deck_composition_plan(plan)
        deck_context = self._paper_deck_deck_context(
            plan,
            theme,
            composition_plan=composition_plan,
        )

        work_items: list[dict[str, Any]] = []
        for slide_index, slide in enumerate(plan.slides):
            visual_requirement = self._paper_deck_visual_requirement(slide)
            composition = composition_plan[slide_index]
            slide_workspace_root = slide_workspaces / f"{slide_index + 1:03d}"
            slide_workspace_root.mkdir()
            single_plan = plan.model_copy(update={"slides": [slide]})
            single_plan_json = json.dumps(
                single_plan.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            single_plan_hash = hashlib.sha256(single_plan_json.encode("utf-8")).hexdigest()
            prompt = self._paper_craft_prompt(
                single_plan,
                plan_hash,
                theme,
                max_images=int(visual_requirement["max_assets"]),
                skill_guidance=skill_guidance,
                slide_index=slide_index,
                deck_slide_count=len(plan.slides),
                slide_plan_hash=single_plan_hash,
                deck_context=deck_context,
                visual_requirement=visual_requirement,
                composition=composition,
            )
            safe_slide_id = re.sub(r"[^A-Za-z0-9_-]+", "-", slide.id).strip("-") or "slide"
            work_items.append(
                {
                    "index": slide_index,
                    "slide": slide,
                    "single_plan": single_plan,
                    "single_plan_json": single_plan_json,
                    "plan_hash": single_plan_hash,
                    "prompt": prompt,
                    "prompt_stem": f"{slide_index + 1:02d}-{safe_slide_id}",
                    "workspace_root": slide_workspace_root,
                    "attempt": first_attempt + slide_index,
                    "visual_requirement": visual_requirement,
                    "composition": composition,
                }
            )

        counter_lock = Lock()
        stop_event = Event()
        next_retry_attempt = first_attempt + len(work_items)
        attempts_used = 0
        style_manifest_references: dict[str, dict[str, Any]] = {}

        def reserve_attempt(item: dict[str, Any], repair_round: int) -> int:
            nonlocal attempts_used, next_retry_attempt
            with counter_lock:
                attempts_used += 1
                if repair_round == 0:
                    return int(item["attempt"])
                attempt = next_retry_attempt
                next_retry_attempt += 1
                return attempt

        def execute_slide(item: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
            repair_errors: list[str] = []
            for repair_round in range(self.repair_attempts + 1):
                if stop_event.is_set():
                    raise CodexGenerationError(
                        "paper-deck generation stopped after a non-retryable Codex failure"
                    )
                attempt = reserve_attempt(item, repair_round)
                attempt_workspace = item["workspace_root"] / f"attempt-{repair_round + 1:02d}"
                attempt_workspace.mkdir()
                single_plan_path = attempt_workspace / "presentation_plan.json"
                single_plan_path.write_bytes(item["single_plan_json"].encode("utf-8"))
                (attempt_workspace / "INSTRUCTIONS.md").write_text(
                    instructions_text,
                    encoding="utf-8",
                )
                self._write_paper_deck_output_schema(
                    attempt_workspace / "result_schema.json",
                    text_block_count=1 + len(item["slide"].key_points),
                    style_signature=(
                        f"{self._paper_deck_style_preset(theme)}:{theme.id}:"
                        f"{self.PAPER_DECK_STYLE_SIGNATURE_VERSION}"
                    ),
                    visual_placeholder_refs=tuple(
                        self._visual_placeholder_reference_map(item["slide"])
                    ),
                    min_visual_assets=int(item["visual_requirement"]["min_assets"]),
                    max_visual_assets=int(item["visual_requirement"]["max_assets"]),
                    min_visual_placeholders=int(item["visual_requirement"]["min_placeholders"]),
                    max_visual_placeholders=int(item["visual_requirement"]["max_placeholders"]),
                    require_dominant_visual_column=False,
                )
                (attempt_workspace / "generated_visuals").mkdir()
                prompt = str(item["prompt"])
                if style_manifest_references:
                    prompt += (
                        "\nDECK_STYLE_MANIFEST_REFERENCES:\nThe backend provides verified Paper "
                        "Deck anchor manifests below. Match their typography, module style tokens, "
                        "line character, and spacing rhythm without copying their literal geometry. "
                        "Once a content anchor exists, later pages must keep its "
                        "title font_role and bold value, keep title font_size within 6 pt, and use "
                        "only its body font roles (mono remains allowed for formulas). Keep fitted "
                        "body sizes within 6 pt of the content-anchor body range.\n"
                    )
                    for reference_name, reference_manifest in style_manifest_references.items():
                        prompt += (
                            f"- {reference_name}_layout_manifest="
                            + json.dumps(
                                reference_manifest,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                if repair_errors:
                    prompt += (
                        "\nRETRY_CORRECTION:\nPrevious attempts for this same slide failed "
                        "image, provenance, or Skill layout-manifest validation. Keep the exact "
                        "Plan, journal-minimal style, and every already-valid visual relationship. "
                        "Correct the failed graphic treatment and matching text block, then re-audit "
                        "the entire slide so fixing one issue cannot create another. Cumulative "
                        "failures:\n- "
                        + "\n- ".join(self._tail(error, 800) for error in repair_errors)
                        + "\nFinal re-audit: every exact string fits at min_font_size; normal text "
                        "has at least 4.5:1 contrast and large/bold text at least 3.0:1; calculate "
                        "x+w and y+h for every block and keep them at or below 0.95; keep x and y "
                        "at or above 0.05; keep blocks non-overlapping."
                        " Keep every module as an independent object, keep different semantic refs "
                        "separated, and never return a full-slide raster or grouped mega-panel."
                        " visual_assets and visual_placeholders are mutually exclusive. Preserve "
                        f"the assigned {item['composition']['layout_id']} composition instead of "
                        "falling back to a generic left/right split. A visual may be central, "
                        "full-width within safe margins, horizontal, stepped, or lateral when that "
                        "matches the assigned composition. Keep every image separate and keep at "
                        f"least {self.PAPER_DECK_MIN_MODULE_GAP:.3f} clearance from unrelated Plan "
                        "copy and modules."
                    )
                prompt_path = prompt_dir / (
                    f"{item['prompt_stem']}-attempt-{repair_round + 1:02d}.md"
                )
                prompt_path.write_text(prompt, encoding="utf-8")

                try:
                    result = self._execute_codex(
                        workspace=attempt_workspace,
                        output_dir=output_dir,
                        prompt=prompt,
                        attempt=attempt,
                        sandbox_mode="workspace-write",
                        run_mode="paper-deck-slide-hybrid",
                        ignore_user_config=False,
                    )
                    try:
                        current_hash = hashlib.sha256(single_plan_path.read_bytes()).hexdigest()
                    except OSError:
                        current_hash = ""
                    if current_hash != item["plan_hash"]:
                        raise ValueError(
                            f"Codex modified or removed the immutable plan for {item['slide'].id}"
                        )
                    prepared_slide = self._prepare_paper_deck_slide_result(
                        result=result,
                        slide=item["slide"],
                        slide_index=item["index"],
                        theme=theme,
                        slide_workspace=attempt_workspace,
                        generated_visuals=generated_visuals,
                        visual_requirement=item["visual_requirement"],
                    )
                    if item["index"] >= 2 and "content" in style_manifest_references:
                        prepared_slide = self._normalize_paper_deck_typography_to_anchor(
                            prepared_slide=prepared_slide,
                            anchor_manifest=style_manifest_references["content"],
                        )
                        self._validate_paper_deck_typography_continuity(
                            prepared_slide=prepared_slide,
                            anchor_manifest=style_manifest_references["content"],
                            slide_id=item["slide"].id,
                        )
                    prepared_slide = self._validate_or_repair_prepared_paper_deck_slide(
                        prepared_slide=prepared_slide,
                        single_plan=item["single_plan"],
                        theme=theme,
                    )
                except (CodexGenerationError, OSError, RuntimeError, ValueError) as exc:
                    repair_errors.append(str(exc))
                    if self._is_codex_usage_limit(repair_errors[-1]):
                        stop_event.set()
                        raise
                    if self._is_paper_craft_capability_failure(repair_errors[-1]):
                        raise
                    if repair_round >= self.repair_attempts:
                        raise
                    continue

                record = {
                    "mode": "paper-deck-slide-hybrid",
                    "slide_id": item["slide"].id,
                    "result": result,
                }
                return item["index"], prepared_slide, record

            raise CodexGenerationError(f"paper-deck exhausted retries for {item['slide'].id}")

        prepared_slides: list[dict[str, Any] | None] = [None] * len(work_items)
        records: list[dict[str, Any] | None] = [None] * len(work_items)
        errors: list[tuple[int, str, str]] = []
        published_prefix = 0

        def publish_contiguous_prefix() -> None:
            """Atomically expose every validated prefix as a downloadable partial deck."""

            nonlocal published_prefix
            prefix_count = 0
            for prepared in prepared_slides:
                if prepared is None:
                    break
                prefix_count += 1
            persistent_visuals = output_dir / "paper_deck_generated_visuals"
            persistent_visuals.mkdir(parents=True, exist_ok=True)
            for source in generated_visuals.glob("*.png"):
                if source.is_file():
                    shutil.copy2(source, persistent_visuals / source.name)
            checkpoint_path = output_dir / "paper_deck_checkpoint.json"
            staged_checkpoint_path = output_dir / "paper_deck_checkpoint.tmp.json"
            staged_checkpoint_path.write_text(
                json.dumps(
                    {
                        "prompt_version": self.PROMPT_VERSION,
                        "plan_sha256": plan_hash,
                        "theme_id": theme.id,
                        "completed_slides": sum(
                            prepared is not None for prepared in prepared_slides
                        ),
                        "prepared_slides": prepared_slides,
                        "style_manifest_references": style_manifest_references,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            staged_checkpoint_path.replace(checkpoint_path)
            # A progress callback is the publication sink used by the application
            # service. Direct/internal provider calls still keep a resumable
            # checkpoint, but do not spend time rendering intermediate decks that
            # nobody can observe.
            if progress_callback is None:
                return
            if prefix_count <= published_prefix:
                return

            partial_plan = plan.model_copy(update={"slides": plan.slides[:prefix_count]})
            partial_result = {
                "status": "completed",
                "summary": "Validated incremental Paper Deck prefix",
                "slides": prepared_slides[:prefix_count],
            }
            partial_designed_plan = self._design_plan_from_result(
                partial_plan,
                partial_result,
                theme,
                workspace=workspace,
                asset_output_dir=output_dir / "codex_assets",
                allow_images=True,
                max_images=self.paper_craft_max_images,
                require_editable_layers_per_slide=True,
            )
            previous_partial_paths = list(output_dir.glob("deck.partial.*.pptx"))
            publication_id = time.time_ns()
            partial_pptx_path = (
                output_dir / f"deck.partial.{prefix_count:03d}.{publication_id}.pptx"
            )
            staged_partial_path = (
                output_dir / f"deck.partial.{prefix_count:03d}.{publication_id}.building.pptx"
            )
            self.adapter.render_declarative_pptx(
                partial_designed_plan,
                staged_partial_path,
            )
            staged_partial_path.replace(partial_pptx_path)
            slide_images = self.adapter.render_incremental_previews(
                partial_designed_plan,
                output_dir / "slides",
                previously_rendered=published_prefix,
            )
            status_path = output_dir / "partial_status.json"
            staged_status_path = output_dir / "partial_status.tmp.json"
            staged_status_path.write_text(
                json.dumps(
                    {
                        "provider": "codex",
                        "status": "partial",
                        "presentation_plan_id": plan.id,
                        "completed_slides": prefix_count,
                        "total_slides": len(plan.slides),
                        "pptx_path": str(partial_pptx_path),
                        "prompt_version": self.PROMPT_VERSION,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            staged_status_path.replace(status_path)
            artifact = PPTArtifact(
                id=self._artifact_id_for_job(job_id),
                job_id=job_id,
                presentation_plan_id=plan.id,
                pptx_path=str(partial_pptx_path),
                skill_request_path=str(status_path),
                slide_images=slide_images,
            )
            published_prefix = prefix_count
            if progress_callback is not None:
                progress_callback(artifact, prefix_count, len(plan.slides))
            # The repository now points at the new immutable publication. Remove
            # older versions when Windows allows it; an open/downloaded version
            # may remain until a later publication without blocking generation.
            for previous_path in previous_partial_paths:
                if previous_path == partial_pptx_path:
                    continue
                try:
                    previous_path.unlink(missing_ok=True)
                except OSError:
                    pass

        def store_success(
            index: int,
            prepared_slide: dict[str, Any],
            record: dict[str, Any],
        ) -> None:
            prepared_slides[index] = prepared_slide
            records[index] = record
            if index in {0, 1}:
                reference_name = "cover" if index == 0 else "content"
                raw_result_slide = record.get("result", {}).get("slides", [{}])[0]
                if isinstance(raw_result_slide, dict):
                    raw_blocks = raw_result_slide.get("text_blocks", [])
                    prepared_text = [
                        element
                        for element in prepared_slide.get("elements", [])
                        if isinstance(element, dict) and element.get("contract_role") == "plan_copy"
                    ]
                    compiled_blocks: list[dict[str, Any]] = []
                    if isinstance(raw_blocks, list) and len(raw_blocks) == len(prepared_text):
                        for raw_block, prepared_element in zip(
                            raw_blocks,
                            prepared_text,
                            strict=True,
                        ):
                            if not isinstance(raw_block, dict) or not isinstance(
                                prepared_element,
                                dict,
                            ):
                                continue
                            compiled_block = dict(raw_block)
                            compiled_block.update(
                                {key: prepared_element[key] for key in ("x", "y", "w", "h")}
                            )
                            prepared_style = prepared_element.get("style", {})
                            if isinstance(prepared_style, dict):
                                compiled_block.update(
                                    {
                                        key: prepared_style[key]
                                        for key in (
                                            "font_size",
                                            "font_role",
                                            "bold",
                                            "color",
                                            "align",
                                            "valign",
                                        )
                                        if key in prepared_style
                                    }
                                )
                            compiled_blocks.append(compiled_block)
                    style_manifest_references[reference_name] = {
                        "style_signature": raw_result_slide.get("style_signature"),
                        "background": raw_result_slide.get("background"),
                        "text_blocks": compiled_blocks,
                        "module_style_tokens": [
                            module.get("style_token")
                            for module in raw_result_slide.get("modules", [])
                            if isinstance(module, dict)
                            and isinstance(module.get("style_token"), str)
                        ],
                    }
            publish_contiguous_prefix()

        checkpoint_path = output_dir / "paper_deck_checkpoint.json"
        if checkpoint_path.is_file():
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                checkpoint = None
            if (
                isinstance(checkpoint, dict)
                and checkpoint.get("prompt_version") == self.PROMPT_VERSION
                and checkpoint.get("plan_sha256") == plan_hash
                and checkpoint.get("theme_id") == theme.id
                and isinstance(checkpoint.get("prepared_slides"), list)
            ):
                persistent_visuals = output_dir / "paper_deck_generated_visuals"
                for source in persistent_visuals.glob("*.png"):
                    if source.is_file():
                        shutil.copy2(source, generated_visuals / source.name)
                restored = checkpoint["prepared_slides"][: len(prepared_slides)]
                for index, prepared in enumerate(restored):
                    if not isinstance(prepared, dict):
                        continue
                    restored_slide = self._validate_or_repair_prepared_paper_deck_slide(
                        prepared_slide=prepared,
                        single_plan=work_items[index]["single_plan"],
                        theme=theme,
                    )
                    prepared_slides[index] = restored_slide
                    records[index] = {
                        "mode": "paper-deck-slide-checkpoint",
                        "slide_id": work_items[index]["slide"].id,
                        "result": {
                            "status": "completed",
                            "summary": "Restored validated slide from incremental checkpoint",
                            "slides": [restored_slide],
                            "_metaclass_staged_image_count": sum(
                                element.get("type") == "image"
                                for element in restored_slide.get("elements", [])
                                if isinstance(element, dict)
                            ),
                        },
                    }
                restored_references = checkpoint.get("style_manifest_references")
                if isinstance(restored_references, dict):
                    style_manifest_references.update(restored_references)
                publish_contiguous_prefix()

        # Paper Deck explicitly uses the first page as a visual anchor. Generate the cover and
        # first content page behind a barrier, then fan out the remaining isolated slide jobs.
        anchor_count = min(2, len(work_items))
        for item in work_items[:anchor_count]:
            if prepared_slides[item["index"]] is not None:
                continue
            try:
                index, prepared_slide, record = execute_slide(item)
            except (CodexGenerationError, OSError, RuntimeError, ValueError) as exc:
                errors.append((item["index"], item["slide"].id, str(exc)))
                break
            else:
                store_success(index, prepared_slide, record)

        remaining_items = (
            [item for item in work_items[anchor_count:] if prepared_slides[item["index"]] is None]
            if not errors
            else []
        )
        worker_count = min(self.paper_craft_concurrency, len(remaining_items))
        if worker_count == 1:
            for item in remaining_items:
                try:
                    index, prepared_slide, record = execute_slide(item)
                except (CodexGenerationError, OSError, RuntimeError, ValueError) as exc:
                    errors.append((item["index"], item["slide"].id, str(exc)))
                else:
                    store_success(index, prepared_slide, record)
        elif worker_count > 1:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="paper-deck-slide",
            ) as executor:
                futures = {executor.submit(execute_slide, item): item for item in remaining_items}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        index, prepared_slide, record = future.result()
                    except (CodexGenerationError, OSError, RuntimeError, ValueError) as exc:
                        errors.append((item["index"], item["slide"].id, str(exc)))
                    else:
                        store_success(index, prepared_slide, record)

        if errors:
            errors.sort(key=lambda item: item[0])
            details = "; ".join(f"{slide_id}: {message}" for _, slide_id, message in errors[:3])
            if len(errors) > 3:
                details += f"; and {len(errors) - 3} more slide failure(s)"
            raise CodexGenerationError(details)

        total_bytes = sum(
            path.stat().st_size for path in generated_visuals.glob("*.png") if path.is_file()
        )
        if total_bytes > self.PAPER_CRAFT_MAX_TOTAL_BYTES:
            raise ValueError("paper-deck generated visual assets exceed the deck asset budget")
        if any(slide is None for slide in prepared_slides) or any(
            record is None for record in records
        ):
            raise ValueError("paper-deck per-slide generation returned an incomplete deck")

        return (
            {
                "status": "completed",
                "summary": (
                    "Paper Deck-directed editable visual objects and typography blocks with "
                    "exact backend-pinned PresentationPlan copy"
                ),
                "slides": prepared_slides,
            },
            records,
            attempts_used,
        )

    @staticmethod
    def _normalize_paper_deck_typography_to_anchor(
        *,
        prepared_slide: dict[str, Any],
        anchor_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply deterministic deck typography continuity without another model call."""

        anchor_blocks = anchor_manifest.get("text_blocks")
        if not isinstance(anchor_blocks, list) or not anchor_blocks:
            return prepared_slide
        normalized = json.loads(json.dumps(prepared_slide, ensure_ascii=False))
        candidate_blocks = [
            element
            for element in normalized.get("elements", [])
            if isinstance(element, dict) and element.get("contract_role") == "plan_copy"
        ]
        if not candidate_blocks:
            return prepared_slide
        title_style = candidate_blocks[0].get("style")
        anchor_title = anchor_blocks[0]
        if isinstance(title_style, dict) and isinstance(anchor_title, dict):
            title_style["font_role"] = anchor_title.get(
                "font_role",
                title_style.get("font_role"),
            )
            title_style["bold"] = anchor_title.get("bold", title_style.get("bold"))
            anchor_size = anchor_title.get("font_size")
            candidate_size = title_style.get("font_size")
            if isinstance(anchor_size, (int, float)) and isinstance(
                candidate_size,
                (int, float),
            ):
                title_style["font_size"] = min(
                    max(float(candidate_size), float(anchor_size) - 6),
                    float(anchor_size) + 6,
                )

        anchor_body_roles = [
            block.get("font_role")
            for block in anchor_blocks[1:]
            if isinstance(block, dict) and isinstance(block.get("font_role"), str)
        ]
        anchor_body_sizes = [
            float(block["font_size"])
            for block in anchor_blocks[1:]
            if isinstance(block, dict)
            and isinstance(block.get("font_size"), (int, float))
            and not isinstance(block.get("font_size"), bool)
        ]
        for block in candidate_blocks[1:]:
            style = block.get("style")
            if not isinstance(style, dict):
                continue
            if anchor_body_roles and style.get("font_role") not in {
                *anchor_body_roles,
                "mono",
            }:
                style["font_role"] = anchor_body_roles[0]
            size = style.get("font_size")
            if anchor_body_sizes and isinstance(size, (int, float)):
                style["font_size"] = min(
                    max(float(size), min(anchor_body_sizes) - 6),
                    max(anchor_body_sizes) + 6,
                )
        return normalized

    @classmethod
    def _shift_paper_deck_semantic_group(
        cls,
        *,
        raw_elements: list[dict[str, Any]],
        parsed: list[SlideElement],
        moving: SlideElement,
        fixed: SlideElement,
        required_gap: float,
    ) -> bool:
        """Move one editable semantic island by the smallest safe translation."""

        moving_indices = [
            index
            for index, element in enumerate(parsed)
            if element.semantic_ref == moving.semantic_ref
            and element.contract_role in {"plan_copy", "visual_module"}
        ]
        if not moving_indices:
            moving_indices = [
                index for index, element in enumerate(parsed) if element is moving
            ]

        moving_group = [parsed[index] for index in moving_indices]
        moving_left = min(element.x for element in moving_group)
        moving_top = min(element.y for element in moving_group)
        moving_right = max(element.x + element.w for element in moving_group)
        moving_bottom = max(element.y + element.h for element in moving_group)
        fixed_group = [fixed]
        if fixed.semantic_ref is not None and fixed.contract_role in {
            "plan_copy",
            "visual_module",
        }:
            fixed_group = [
                element
                for element in parsed
                if element.semantic_ref == fixed.semantic_ref
                and element.contract_role in {"plan_copy", "visual_module"}
            ]
        fixed_left = min(element.x for element in fixed_group)
        fixed_top = min(element.y for element in fixed_group)
        fixed_right = max(element.x + element.w for element in fixed_group)
        fixed_bottom = max(element.y + element.h for element in fixed_group)

        translations = [
            (fixed_right + required_gap - moving_left, 0.0),
            (fixed_left - required_gap - moving_right, 0.0),
            (0.0, fixed_bottom + required_gap - moving_top),
            (0.0, fixed_top - required_gap - moving_bottom),
        ]
        translations.sort(key=lambda delta: abs(delta[0]) + abs(delta[1]))
        for delta_x, delta_y in translations:
            if abs(delta_x) <= 1e-9 and abs(delta_y) <= 1e-9:
                continue
            shifted: list[tuple[int, float, float]] = []
            for index in moving_indices:
                element = parsed[index]
                new_x, new_y = element.x + delta_x, element.y + delta_y
                if (
                    new_x < cls.PAPER_CRAFT_SAFE_MARGIN_X - 1e-9
                    or new_x + element.w
                    > 1 - cls.PAPER_CRAFT_SAFE_MARGIN_X + 1e-9
                    or new_y < cls.PAPER_CRAFT_SAFE_MARGIN_Y - 1e-9
                    or new_y + element.h
                    > 1 - cls.PAPER_CRAFT_SAFE_MARGIN_Y + 1e-9
                ):
                    break
                shifted.append((index, new_x, new_y))
            else:
                for index, new_x, new_y in shifted:
                    raw_elements[index]["x"] = round(new_x, 5)
                    raw_elements[index]["y"] = round(new_y, 5)
                return True
        return False

    @classmethod
    def _repair_paper_deck_connector(
        cls,
        *,
        raw_elements: list[dict[str, Any]],
        parsed: list[SlideElement],
        object_id: str,
    ) -> bool:
        """Replace a crossing connector with a short, independently editable text anchor."""

        connector_index = next(
            (
                index
                for index, element in enumerate(parsed)
                if element.type == "line" and element.object_id == object_id
            ),
            None,
        )
        if connector_index is None:
            return False
        connector = parsed[connector_index]
        text = next(
            (
                element
                for element in parsed
                if element.contract_role == "plan_copy"
                and element.semantic_ref == connector.semantic_ref
                and element.semantic_ref != "title"
            ),
            None,
        )
        if text is None:
            raw_elements.pop(connector_index)
            return True

        visual_regions = [
            element
            for element in parsed
            if element.contract_role in {"visual_asset", "visual_placeholder"}
        ]
        anchor_gap = max(0.008, cls.PAPER_DECK_MIN_MODULE_GAP)
        candidates = [
            (text.x - anchor_gap, text.y + 0.01, 0.0, max(0.01, text.h - 0.02)),
            (text.x + text.w + anchor_gap, text.y + 0.01, 0.0, max(0.01, text.h - 0.02)),
            (text.x + 0.01, text.y - anchor_gap, max(0.01, text.w - 0.02), 0.0),
            (text.x + 0.01, text.y + text.h + anchor_gap, max(0.01, text.w - 0.02), 0.0),
        ]
        for x, y, w, h in candidates:
            replacement = connector.model_copy(update={"x": x, "y": y, "w": w, "h": h})
            if not cls._is_paper_deck_text_within_safe_canvas(replacement):
                continue
            if any(
                cls._paper_deck_line_intersects_element(replacement, plan_text)
                for plan_text in parsed
                if plan_text.type == "text" and plan_text.contract_role == "plan_copy"
            ):
                continue
            if any(
                max(
                    visual.x - (replacement.x + replacement.w),
                    replacement.x - (visual.x + visual.w),
                )
                < cls.PAPER_DECK_MIN_MODULE_GAP
                and max(
                    visual.y - (replacement.y + replacement.h),
                    replacement.y - (visual.y + visual.h),
                )
                < cls.PAPER_DECK_MIN_MODULE_GAP
                for visual in visual_regions
            ):
                continue
            raw = raw_elements[connector_index]
            raw.update(
                {
                    "x": round(x, 5),
                    "y": round(y, 5),
                    "w": round(w, 5),
                    "h": round(h, 5),
                    "z": max(1, text.z - 1),
                }
            )
            return True

        # Removing the unsafe line is lossless: the next local-validation pass will add a
        # deterministic anchor when this connector was the point's only editorial treatment.
        raw_elements.pop(connector_index)
        return True

    @classmethod
    def _validate_or_repair_prepared_paper_deck_slide(
        cls,
        *,
        prepared_slide: dict[str, Any],
        single_plan: PresentationPlan,
        theme: PresentationTheme,
    ) -> dict[str, Any]:
        """Repair mechanical geometry/contrast defects locally before spending a retry."""

        candidate = json.loads(json.dumps(prepared_slide, ensure_ascii=False))
        for _ in range(12):
            try:
                cls._validate_prepared_paper_deck_slide(
                    prepared_slide=candidate,
                    single_plan=single_plan,
                    theme=theme,
                )
                return candidate
            except ValueError as exc:
                message = str(exc)
                raw_elements = candidate.get("elements")
                if not isinstance(raw_elements, list):
                    raise
                parsed = [SlideElement.model_validate(element) for element in raw_elements]

                if "contrast is too low" in message:
                    modules = [
                        element for element in parsed if element.contract_role == "visual_module"
                    ]
                    changed = False
                    for raw, element in zip(raw_elements, parsed, strict=True):
                        if element.contract_role != "plan_copy" or element.type != "text":
                            continue
                        large_text = element.style.font_size >= 24 or (
                            element.style.bold and element.style.font_size >= 18.66
                        )
                        minimum = 3.0 if large_text else 4.5
                        current = cls._paper_deck_layered_text_contrast(
                            element,
                            visual_modules=modules,
                            slide_background=str(candidate.get("background", theme.palette.paper)),
                        )
                        if current + 1e-9 >= minimum:
                            continue
                        best_color = element.style.color
                        best_ratio = current
                        for color in dict.fromkeys(theme.palette.allowed_colors):
                            tested = element.model_copy(
                                update={"style": element.style.model_copy(update={"color": color})}
                            )
                            ratio = cls._paper_deck_layered_text_contrast(
                                tested,
                                visual_modules=modules,
                                slide_background=str(
                                    candidate.get("background", theme.palette.paper)
                                ),
                            )
                            if ratio > best_ratio:
                                best_color, best_ratio = color, ratio
                        if best_ratio + 1e-9 < minimum:
                            continue
                        raw["style"]["color"] = best_color
                        changed = True
                    if changed:
                        continue

                if "leaves the safe canvas" in message:
                    changed = False
                    for raw, element in zip(raw_elements, parsed, strict=True):
                        min_x, max_x = (
                            cls.PAPER_CRAFT_SAFE_MARGIN_X,
                            (1 - cls.PAPER_CRAFT_SAFE_MARGIN_X - element.w),
                        )
                        min_y, max_y = (
                            cls.PAPER_CRAFT_SAFE_MARGIN_Y,
                            (1 - cls.PAPER_CRAFT_SAFE_MARGIN_Y - element.h),
                        )
                        if max_x < min_x or max_y < min_y:
                            continue
                        new_x = min(max(element.x, min_x), max_x)
                        new_y = min(max(element.y, min_y), max_y)
                        if abs(new_x - element.x) > 1e-9 or abs(new_y - element.y) > 1e-9:
                            raw["x"], raw["y"] = new_x, new_y
                            changed = True
                    if changed:
                        continue

                connector_crossing = re.search(
                    r"connector crosses a Plan text writing area on [^:]+: ([^ ]+)",
                    message,
                )
                if connector_crossing and cls._repair_paper_deck_connector(
                    raw_elements=raw_elements,
                    parsed=parsed,
                    object_id=connector_crossing.group(1),
                ):
                    continue

                clearance = re.search(
                    r"(?:reserved visual column|visual region) has less than "
                    r"([0-9.]+) clearance from ([^ ]+) on ",
                    message,
                )
                if clearance:
                    required_gap = float(clearance.group(1))
                    object_id = clearance.group(2)
                    visual_regions = [
                        element
                        for element in parsed
                        if element.contract_role in {"visual_asset", "visual_placeholder"}
                    ]
                    offender_index = next(
                        (
                            index
                            for index, element in enumerate(parsed)
                            if (element.object_id or element.semantic_ref or element.type)
                            == object_id
                        ),
                        None,
                    )
                    if visual_regions and offender_index is not None:
                        offender = parsed[offender_index]
                        if offender.type == "line" and cls._repair_paper_deck_connector(
                            raw_elements=raw_elements,
                            parsed=parsed,
                            object_id=offender.object_id or object_id,
                        ):
                            continue
                        left = min(element.x for element in visual_regions)
                        right = max(element.x + element.w for element in visual_regions)
                        top = min(element.y for element in visual_regions)
                        bottom = max(element.y + element.h for element in visual_regions)
                        visual_region = SlideElement(
                            type="shape",
                            contract_role="visual_asset",
                            x=left,
                            y=top,
                            w=right - left,
                            h=bottom - top,
                            shape="rectangle",
                        )
                        if cls._shift_paper_deck_semantic_group(
                            raw_elements=raw_elements,
                            parsed=parsed,
                            moving=offender,
                            fixed=visual_region,
                            required_gap=required_gap,
                        ):
                            continue

                if "visual modules are fused or too tightly clustered" in message:
                    body_text = [
                        element
                        for element in parsed
                        if element.type == "text"
                        and not (
                            element.contract_role == "plan_copy"
                            and element.semantic_ref == "title"
                        )
                    ]
                    repaired = False
                    for index, left in enumerate(body_text):
                        for right in body_text[index + 1 :]:
                            horizontal_gap = max(
                                right.x - (left.x + left.w),
                                left.x - (right.x + right.w),
                            )
                            vertical_gap = max(
                                right.y - (left.y + left.h),
                                left.y - (right.y + right.h),
                            )
                            if (
                                horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                                and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                                and cls._shift_paper_deck_semantic_group(
                                    raw_elements=raw_elements,
                                    parsed=parsed,
                                    moving=right,
                                    fixed=left,
                                    required_gap=cls.PAPER_DECK_MIN_MODULE_GAP,
                                )
                            ):
                                repaired = True
                                break
                        if repaired:
                            break
                    if repaired:
                        continue

                if (
                    "local illustrations overlap or fuse" in message
                    or "independent visual objects overlap or form one merged cluster" in message
                ):
                    if "local illustrations" in message:
                        visual_islands = [
                            element
                            for element in parsed
                            if element.contract_role == "visual_asset"
                        ]
                    else:
                        visual_islands = [
                            element
                            for element in parsed
                            if (
                                element.contract_role in {"visual_module", "visual_asset"}
                                and element.type != "line"
                            )
                            or element.contract_role == "visual_placeholder"
                        ]
                    repaired = False
                    for index, left in enumerate(visual_islands):
                        for right in visual_islands[index + 1 :]:
                            if left.semantic_ref == right.semantic_ref:
                                continue
                            horizontal_gap = max(
                                right.x - (left.x + left.w),
                                left.x - (right.x + right.w),
                            )
                            vertical_gap = max(
                                right.y - (left.y + left.h),
                                left.y - (right.y + right.h),
                            )
                            if (
                                horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                                and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                                and cls._shift_paper_deck_semantic_group(
                                    raw_elements=raw_elements,
                                    parsed=parsed,
                                    moving=right,
                                    fixed=left,
                                    required_gap=cls.PAPER_DECK_MIN_MODULE_GAP,
                                )
                            ):
                                repaired = True
                                break
                        if repaired:
                            break
                    if repaired:
                        continue

                missing_anchor = re.search(
                    r"no independent editorial anchor for (key_points\.[0-9]+) on ",
                    message,
                )
                if missing_anchor:
                    content_ref = missing_anchor.group(1)
                    text = next(
                        (
                            element
                            for element in parsed
                            if element.contract_role == "plan_copy"
                            and element.semantic_ref == content_ref
                        ),
                        None,
                    )
                    if text is not None:
                        existing_ids = {
                            element.object_id for element in parsed if element.object_id
                        }
                        suffix = content_ref.replace(".", "_")
                        object_id = f"local_{suffix}_anchor"
                        counter = 2
                        while object_id in existing_ids:
                            object_id = f"local_{suffix}_anchor_{counter}"
                            counter += 1
                        anchor_x = max(
                            cls.PAPER_CRAFT_SAFE_MARGIN_X,
                            text.x - 0.012,
                        )
                        raw_elements.append(
                            {
                                "type": "shape",
                                "contract_role": "visual_module",
                                "object_id": object_id,
                                "semantic_ref": content_ref,
                                "x": anchor_x,
                                "y": text.y,
                                "w": 0.006,
                                "h": text.h,
                                "z": max(1, text.z - 1),
                                "text": None,
                                "items": [],
                                "shape": "rectangle",
                                "image_path": None,
                                "image_fit": "cover",
                                "style": {
                                    "font_size": 18,
                                    "font_role": "sans",
                                    "text_margin_x": 0,
                                    "text_margin_y": 0,
                                    "bold": False,
                                    "color": theme.palette.ink,
                                    "fill": theme.palette.amber,
                                    "line_color": theme.palette.amber,
                                    "line_width": 0,
                                    "align": "left",
                                    "valign": "top",
                                    "opacity": 100,
                                },
                            }
                        )
                        continue
                raise
        raise ValueError(f"paper-deck local repair did not converge on {single_plan.slides[0].id}")

    @staticmethod
    def _validate_paper_deck_typography_continuity(
        *,
        prepared_slide: dict[str, Any],
        anchor_manifest: dict[str, Any],
        slide_id: str,
    ) -> None:
        """Enforce the first content page as the deck typography anchor."""

        raw_candidate_elements = prepared_slide.get("elements")
        candidate_blocks = (
            [
                element
                for element in raw_candidate_elements
                if isinstance(element, dict) and element.get("contract_role") == "plan_copy"
            ]
            if isinstance(raw_candidate_elements, list)
            else None
        )
        anchor_blocks = anchor_manifest.get("text_blocks")
        if not isinstance(candidate_blocks, list) or not candidate_blocks:
            raise ValueError(f"paper-deck typography manifest is missing on {slide_id}")
        if not isinstance(anchor_blocks, list) or not anchor_blocks:
            raise ValueError("paper-deck content typography anchor is missing")
        candidate_title_element = candidate_blocks[0]
        anchor_title = anchor_blocks[0]
        if not isinstance(candidate_title_element, dict) or not isinstance(anchor_title, dict):
            raise ValueError(f"paper-deck title typography manifest is invalid on {slide_id}")
        candidate_title = candidate_title_element.get("style")
        if not isinstance(candidate_title, dict):
            raise ValueError(f"paper-deck title typography style is invalid on {slide_id}")
        if candidate_title.get("font_role") != anchor_title.get("font_role"):
            raise ValueError(
                f"paper-deck title font role drifted from the content anchor on {slide_id}"
            )
        if candidate_title.get("bold") is not anchor_title.get("bold"):
            raise ValueError(
                f"paper-deck title weight drifted from the content anchor on {slide_id}"
            )
        try:
            size_delta = abs(
                float(candidate_title.get("font_size")) - float(anchor_title.get("font_size"))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"paper-deck title size is invalid on {slide_id}") from exc
        if size_delta > 6:
            raise ValueError(f"paper-deck title size drifted from the content anchor on {slide_id}")

        anchor_body_roles = {
            block.get("font_role")
            for block in anchor_blocks[1:]
            if isinstance(block, dict) and isinstance(block.get("font_role"), str)
        }
        anchor_body_sizes = [
            float(block["font_size"])
            for block in anchor_blocks[1:]
            if isinstance(block, dict)
            and isinstance(block.get("font_size"), (int, float))
            and not isinstance(block.get("font_size"), bool)
        ]
        allowed_body_roles = anchor_body_roles | {"mono"} if anchor_body_roles else set()
        for element in candidate_blocks[1:]:
            if not isinstance(element, dict) or not isinstance(element.get("style"), dict):
                continue
            block = element["style"]
            if allowed_body_roles and block.get("font_role") not in allowed_body_roles:
                raise ValueError(
                    f"paper-deck body font role drifted from the content anchor on {slide_id}"
                )
            if anchor_body_sizes:
                try:
                    candidate_size = float(block.get("font_size"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"paper-deck body font size is invalid on {slide_id}") from exc
                if not min(anchor_body_sizes) - 6 <= candidate_size <= max(anchor_body_sizes) + 6:
                    raise ValueError(
                        f"paper-deck body font size drifted from the content anchor on {slide_id}"
                    )

    @classmethod
    def _validate_prepared_paper_deck_slide(
        cls,
        *,
        prepared_slide: dict[str, Any],
        single_plan: PresentationPlan,
        theme: PresentationTheme,
    ) -> None:
        """Validate independent editable objects before they are compiled into PPTX."""

        expected = single_plan.slides[0]
        raw_elements = prepared_slide.get("elements")
        if not isinstance(raw_elements, list) or not raw_elements:
            raise ValueError(f"paper-deck prepared no elements for {expected.id}")
        expected_background = theme.palette.board if expected.order == 1 else theme.palette.paper
        if str(prepared_slide.get("background", "")).upper() != expected_background:
            raise ValueError(f"paper-deck prepared the wrong page background on {expected.id}")
        parsed_elements = [SlideElement.model_validate(raw) for raw in raw_elements]
        object_ids = [element.object_id for element in parsed_elements]
        if any(object_id is None for object_id in object_ids) or len(object_ids) != len(
            set(object_ids)
        ):
            raise ValueError(
                f"paper-deck editable object ids are missing or duplicated on {expected.id}"
            )
        visual_modules = [
            element for element in parsed_elements if element.contract_role == "visual_module"
        ]
        visual_assets = [
            element for element in parsed_elements if element.contract_role == "visual_asset"
        ]
        if not visual_modules:
            raise ValueError(f"paper-deck prepared no editable visual modules for {expected.id}")
        if len(visual_assets) > cls.PAPER_DECK_MAX_VISUAL_ASSETS:
            raise ValueError(f"paper-deck prepared too many local illustrations on {expected.id}")
        if any(cls._is_full_bleed_background_image(element) for element in visual_assets):
            raise ValueError(
                f"paper-deck cannot flatten editable layers into a full-slide image on {expected.id}"
            )
        text_elements: list[SlideElement] = []
        placeholder_elements: list[SlideElement] = []
        visible_texts: list[str] = []
        for element in parsed_elements:
            if element.contract_role == "visual_module":
                if element.type not in {"shape", "line"} or element.text or element.items:
                    raise ValueError(
                        f"paper-deck prepared an invalid editable module on {expected.id}"
                    )
                continue
            if element.contract_role == "visual_asset":
                if element.type != "image" or element.text or element.items:
                    raise ValueError(
                        f"paper-deck prepared an invalid local illustration on {expected.id}"
                    )
                continue
            if element.type != "text":
                raise ValueError(f"paper-deck prepared an unauthorized object on {expected.id}")
            text = element.text or ""
            if element.contract_role == "plan_copy":
                visible_texts.append(text)
                if (
                    element.style.fill is not None
                    or element.style.line_color is not None
                    or element.style.line_width != 0
                ):
                    raise ValueError(
                        f"paper-deck Plan text must use invisible text boxes on {expected.id}"
                    )
                if element.style.opacity != 100:
                    raise ValueError(f"paper-deck Plan text must be fully opaque on {expected.id}")
                ratio = cls._paper_deck_layered_text_contrast(
                    element,
                    visual_modules=visual_modules,
                    slide_background=expected_background,
                )
                large_text = element.style.font_size >= 24 or (
                    element.style.bold and element.style.font_size >= 18.66
                )
                minimum_ratio = 3.0 if large_text else 4.5
                if ratio + 1e-9 < minimum_ratio:
                    raise ValueError(
                        f"paper-deck Skill text block contrast is too low on {expected.id}: "
                        f"{ratio:.2f}:1 for {(element.text or '')[:80]}"
                    )
            elif element.contract_role == "visual_placeholder":
                if (
                    element.style.fill is None
                    or element.style.line_color is None
                    or element.style.line_width <= 0
                ):
                    raise ValueError(
                        f"paper-deck visual placeholder must keep its visible frame on {expected.id}"
                    )
                if element.style.opacity != 100:
                    raise ValueError(
                        f"paper-deck visual placeholder must be opaque on {expected.id}"
                    )
                ratio = cls._contrast_ratio(element.style.color, element.style.fill)
                if ratio + 1e-9 < 4.5:
                    raise ValueError(
                        f"paper-deck visual placeholder contrast is too low on {expected.id}: "
                        f"{ratio:.2f}:1"
                    )
                placeholder_elements.append(element)
            else:
                raise ValueError(
                    f"paper-deck prepared an unauthorized text overlay on {expected.id}"
                )
            text_elements.append(element)

        expected_texts = [expected.title, *expected.key_points]
        if visible_texts != expected_texts:
            raise ValueError(
                f"Codex visible text mismatch on {expected.id}: "
                f"expected {expected_texts!r}, got {visible_texts!r}"
            )
        allowed_placeholder_texts = {
            cls._visual_placeholder_label(expected, content_ref)
            for content_ref in cls._visual_placeholder_reference_map(expected)
        }
        if len(placeholder_elements) > cls.PAPER_DECK_MAX_VISUAL_PLACEHOLDERS or any(
            (element.text or "") not in allowed_placeholder_texts
            for element in placeholder_elements
        ):
            raise ValueError(
                f"paper-deck visual placeholder text is not authorized on {expected.id}"
            )
        allowed_visual_refs = set(cls._visual_placeholder_reference_map(expected))
        if any(element.semantic_ref not in allowed_visual_refs for element in visual_assets):
            raise ValueError(f"paper-deck local illustration is not Plan-bound on {expected.id}")
        requirement = cls._paper_deck_visual_requirement(expected)
        if not (
            int(requirement["min_assets"]) <= len(visual_assets) <= int(requirement["max_assets"])
        ):
            raise ValueError(
                f"paper-deck local illustration count violates the slide visual requirement on "
                f"{expected.id}"
            )
        if not (
            int(requirement["min_placeholders"])
            <= len(placeholder_elements)
            <= int(requirement["max_placeholders"])
        ):
            raise ValueError(
                f"paper-deck placeholder count violates the slide visual requirement on "
                f"{expected.id}"
            )
        if {element.semantic_ref for element in visual_assets} & {
            element.semantic_ref for element in placeholder_elements
        }:
            raise ValueError(
                f"paper-deck cannot generate and reserve the same visual on {expected.id}"
            )
        for element in parsed_elements:
            if not cls._is_paper_deck_text_within_safe_canvas(element):
                raise ValueError(
                    f"paper-deck editable object leaves the safe canvas on {expected.id}"
                )
        if PresentationPlanGenerator._has_unsafe_scene_collisions(
            [*text_elements, *visual_assets],
            expected.title,
        ):
            raise ValueError(f"Codex design contains overlapping content on {expected.id}")
        cls._validate_distributed_module_spacing(parsed_elements, slide=expected)

    @classmethod
    def _paper_deck_layered_text_contrast(
        cls,
        element: SlideElement,
        *,
        visual_modules: list[SlideElement],
        slide_background: str,
    ) -> float:
        """Resolve text contrast from the topmost editable shape beneath its text box."""

        containing_shapes = [
            module
            for module in visual_modules
            if module.type == "shape"
            and module.z < element.z
            and cls._paper_deck_element_contains(module, element)
        ]
        background = slide_background
        if containing_shapes:
            topmost = max(containing_shapes, key=lambda module: module.z)
            background = topmost.style.fill or slide_background
        return cls._contrast_ratio(element.style.color, background)

    @staticmethod
    def _paper_deck_element_contains(
        outer: SlideElement,
        inner: SlideElement,
        *,
        tolerance: float = 0.004,
    ) -> bool:
        return (
            inner.x >= outer.x - tolerance
            and inner.y >= outer.y - tolerance
            and inner.x + inner.w <= outer.x + outer.w + tolerance
            and inner.y + inner.h <= outer.y + outer.h + tolerance
        )

    @staticmethod
    def _paper_deck_elements_intersect(
        left: SlideElement,
        right: SlideElement,
        *,
        tolerance: float = 1e-9,
    ) -> bool:
        """Return true only when two element rectangles overlap with positive area."""

        return (
            left.x < right.x + right.w - tolerance
            and left.x + left.w > right.x + tolerance
            and left.y < right.y + right.h - tolerance
            and left.y + left.h > right.y + tolerance
        )

    @staticmethod
    def _paper_deck_line_intersects_element(
        line: SlideElement,
        element: SlideElement,
        *,
        padding: float = 0.003,
    ) -> bool:
        """Use Liang-Barsky clipping to keep connectors out of writing areas."""

        x0, y0 = line.x, line.y
        x1, y1 = line.x + line.w, line.y + line.h
        left = max(0.0, element.x - padding)
        right = min(1.0, element.x + element.w + padding)
        top = max(0.0, element.y - padding)
        bottom = min(1.0, element.y + element.h + padding)
        dx, dy = x1 - x0, y1 - y0
        entering, leaving = 0.0, 1.0
        for coefficient, distance in (
            (-dx, x0 - left),
            (dx, right - x0),
            (-dy, y0 - top),
            (dy, bottom - y0),
        ):
            if abs(coefficient) <= 1e-12:
                if distance < 0:
                    return False
                continue
            ratio = distance / coefficient
            if coefficient < 0:
                entering = max(entering, ratio)
            else:
                leaving = min(leaving, ratio)
            if entering > leaving:
                return False
        return True

    @classmethod
    def _validate_paper_deck_visual_region(
        cls,
        elements: list[SlideElement],
        *,
        slide: SlidePlan,
    ) -> None:
        """Keep visual assets useful and separate without prescribing one page silhouette."""

        visual_regions = [
            element
            for element in elements
            if element.contract_role in {"visual_asset", "visual_placeholder"}
        ]
        assets = [element for element in visual_regions if element.contract_role == "visual_asset"]
        placeholders = [
            element for element in visual_regions if element.contract_role == "visual_placeholder"
        ]
        if len(assets) > cls.PAPER_DECK_MAX_VISUAL_ASSETS or len(placeholders) > 1:
            raise ValueError(f"paper-deck returned too many visual objects on {slide.id}")
        if assets and placeholders:
            raise ValueError(
                f"paper-deck cannot combine local illustrations and a placeholder on {slide.id}"
            )
        if not visual_regions or slide.order == 1:
            return

        left = min(element.x for element in visual_regions)
        top = min(element.y for element in visual_regions)
        right = max(element.x + element.w for element in visual_regions)
        bottom = max(element.y + element.h for element in visual_regions)
        region = SlideElement(
            type="shape",
            contract_role="visual_module",
            x=left,
            y=top,
            w=right - left,
            h=bottom - top,
            shape="rectangle",
        )
        minimum_width = (
            cls.PAPER_DECK_VISUAL_REGION_MIN_WIDTH
            if placeholders
            else cls.PAPER_DECK_LOCAL_VISUAL_MIN_WIDTH
        )
        minimum_height = (
            cls.PAPER_DECK_VISUAL_REGION_MIN_HEIGHT
            if placeholders
            else cls.PAPER_DECK_LOCAL_VISUAL_MIN_HEIGHT
        )
        minimum_area = cls.PAPER_DECK_VISUAL_REGION_MIN_AREA if placeholders else 0.04
        if not (
            minimum_width - 1e-9 <= region.w <= cls.PAPER_DECK_VISUAL_REGION_MAX_WIDTH + 1e-9
            and minimum_height - 1e-9 <= region.h <= cls.PAPER_DECK_VISUAL_REGION_MAX_HEIGHT + 1e-9
            and region.w * region.h >= minimum_area - 1e-9
        ):
            raise ValueError(
                f"paper-deck visual region is too small or too large on {slide.id}: "
                f"w={region.w:.3f}, h={region.h:.3f}; expected "
                f"w={minimum_width:.2f}-{cls.PAPER_DECK_VISUAL_REGION_MAX_WIDTH:.2f}, "
                f"h={minimum_height:.2f}-{cls.PAPER_DECK_VISUAL_REGION_MAX_HEIGHT:.2f}"
            )
        if len(assets) == 2:
            first, second = assets
            horizontal_gap = max(
                second.x - (first.x + first.w),
                first.x - (second.x + second.w),
            )
            vertical_gap = max(
                second.y - (first.y + first.h),
                first.y - (second.y + second.h),
            )
            if (
                horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
            ):
                raise ValueError(f"paper-deck local illustrations overlap or fuse on {slide.id}")
        protected_elements = [
            element
            for element in elements
            if element.contract_role in {"plan_copy", "visual_module"}
        ]
        for visual in visual_regions:
            for protected in protected_elements:
                if (
                    protected.contract_role == "visual_module"
                    and protected.semantic_ref == visual.semantic_ref
                    and cls._paper_deck_element_contains(protected, visual)
                ):
                    # A separately movable figure frame may deliberately contain its matching image.
                    continue
                horizontal_gap = max(
                    protected.x - (visual.x + visual.w),
                    visual.x - (protected.x + protected.w),
                )
                vertical_gap = max(
                    protected.y - (visual.y + visual.h),
                    visual.y - (protected.y + protected.h),
                )
                if (
                    horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                    and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                ):
                    raise ValueError(
                        "paper-deck visual region has less than "
                        f"{cls.PAPER_DECK_MIN_MODULE_GAP:.3f} clearance from "
                        f"{protected.object_id or protected.semantic_ref or protected.type} on "
                        f"{slide.id}"
                    )

    @classmethod
    def _is_paper_deck_text_within_safe_canvas(cls, element: SlideElement) -> bool:
        """Allow only raster-normalization drift of at most one target pixel."""

        target_width, target_height = cls.PAPER_CRAFT_BACKGROUND_SIZE
        tolerance_x = cls.PAPER_CRAFT_SAFE_TOLERANCE_PX / target_width
        tolerance_y = cls.PAPER_CRAFT_SAFE_TOLERANCE_PX / target_height
        return (
            element.x >= cls.PAPER_CRAFT_SAFE_MARGIN_X - tolerance_x
            and element.x + element.w <= 1 - cls.PAPER_CRAFT_SAFE_MARGIN_X + tolerance_x
            and element.y >= cls.PAPER_CRAFT_SAFE_MARGIN_Y - tolerance_y
            and element.y + element.h <= 1 - cls.PAPER_CRAFT_SAFE_MARGIN_Y + tolerance_y
        )

    def _prepare_paper_deck_slide_result(
        self,
        *,
        result: dict[str, Any],
        slide: SlidePlan,
        slide_index: int,
        theme: PresentationTheme,
        slide_workspace: Path,
        generated_visuals: Path,
        visual_requirement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(result.get("status", "unknown"))
        if status != "completed":
            summary = str(result.get("summary", "Codex did not complete the slide"))
            raise ValueError(f"Codex returned {status}: {summary[:1000]}")
        raw_slides = result.get("slides")
        if not isinstance(raw_slides, list) or len(raw_slides) != 1:
            raise ValueError("paper-deck slide session must return exactly one slide")
        raw_slide = raw_slides[0]
        if not isinstance(raw_slide, dict) or raw_slide.get("slide_id") != slide.id:
            raise ValueError(f"paper-deck slide-id mismatch for {slide.id}")
        expected_background = theme.palette.board if slide_index == 0 else theme.palette.paper
        expected_style_signature = (
            f"{self._paper_deck_style_preset(theme)}:{theme.id}:"
            f"{self.PAPER_DECK_STYLE_SIGNATURE_VERSION}"
        )
        if (
            raw_slide.get("manifest_version") != self.PAPER_DECK_MANIFEST_VERSION
            or raw_slide.get("layout_mode") != "editable-layered-objects"
            or raw_slide.get("style_signature") != expected_style_signature
            or raw_slide.get("raster_audit") != "inspected-local-assets-text-free"
        ):
            raise ValueError(f"paper-deck Skill manifest identity mismatch for {slide.id}")
        if str(raw_slide.get("background", "")).upper() != expected_background:
            raise ValueError(f"paper-deck Skill background mismatch for {slide.id}")
        raw_modules = raw_slide.get("modules")
        if not isinstance(raw_modules, list):
            raise ValueError(f"paper-deck returned no editable modules for {slide.id}")
        raw_visual_assets = raw_slide.get("visual_assets")
        if not isinstance(raw_visual_assets, list):
            raise ValueError(f"paper-deck returned no visual-asset manifest for {slide.id}")
        requirement = visual_requirement or self._paper_deck_visual_requirement(slide)
        if not (
            int(requirement["min_assets"])
            <= len(raw_visual_assets)
            <= int(requirement["max_assets"])
        ):
            raise ValueError(
                f"paper-deck returned {len(raw_visual_assets)} local illustrations on {slide.id}; "
                f"required {requirement['min_assets']}-{requirement['max_assets']}"
            )
        image_stage_error = str(result.get("_metaclass_image_stage_error", "")).strip()
        try:
            staged_image_count = int(result.get("_metaclass_staged_image_count", 0))
        except (TypeError, ValueError):
            staged_image_count = -1
        if image_stage_error or staged_image_count != len(raw_visual_assets):
            detail = image_stage_error or (
                "Codex trusted image count does not match the visual_assets manifest"
            )
            raise ValueError(
                f"paper-deck image-generation provenance failed for {slide.id}: {detail}"
            )
        raw_text_blocks = raw_slide.get("text_blocks")
        expected_texts = [slide.title, *slide.key_points]
        if not isinstance(raw_text_blocks, list) or len(raw_text_blocks) != len(expected_texts):
            raise ValueError(
                f"paper-deck Skill text-block count mismatch on {slide.id}: "
                f"expected {len(expected_texts)}"
            )
        raw_visual_placeholders = raw_slide.get("visual_placeholders")
        if not isinstance(raw_visual_placeholders, list):
            raise ValueError(
                f"paper-deck Skill returned no visual-placeholder manifest on {slide.id}"
            )
        minimum_placeholders = int(requirement["min_placeholders"])
        maximum_placeholders = int(requirement["max_placeholders"])
        if not minimum_placeholders <= len(raw_visual_placeholders) <= maximum_placeholders:
            raise ValueError(
                f"paper-deck returned {len(raw_visual_placeholders)} visual placeholders on "
                f"{slide.id}; required {minimum_placeholders}-{maximum_placeholders}"
            )

        visual_module_elements = self._build_visual_module_elements(
            slide=slide,
            raw_modules=raw_modules,
            theme=theme,
        )
        visual_asset_elements = self._build_visual_asset_elements(
            slide=slide,
            raw_assets=raw_visual_assets,
            slide_workspace=slide_workspace,
            generated_visuals=generated_visuals,
        )
        pinned_text_elements = self._build_skill_text_block_overlay(
            slide=slide,
            raw_text_blocks=raw_text_blocks,
        )
        visual_placeholder_elements = self._build_visual_placeholder_overlay(
            slide=slide,
            raw_placeholders=raw_visual_placeholders,
            theme=theme,
        )

        return {
            "slide_id": slide.id,
            "background": expected_background,
            "elements": [
                *visual_module_elements,
                *visual_asset_elements,
                *pinned_text_elements,
                *visual_placeholder_elements,
            ],
        }

    @staticmethod
    def _transform_paper_deck_text_blocks_for_crop(
        raw_text_blocks: list[Any],
        *,
        source_path: Path,
        target_size: tuple[int, int],
    ) -> list[Any]:
        """Keep Skill-authored writing areas attached when a source image is center-cropped."""

        try:
            with Image.open(source_path) as source:
                source_width, source_height = ImageOps.exif_transpose(source).size
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("Codex generated background is unreadable") from exc
        target_width, target_height = target_size
        source_ratio = source_width / source_height
        target_ratio = target_width / target_height
        if math.isclose(source_ratio, target_ratio, rel_tol=1e-6, abs_tol=1e-6):
            return [dict(block) if isinstance(block, dict) else block for block in raw_text_blocks]

        transformed: list[Any] = []
        for raw_block in raw_text_blocks:
            if not isinstance(raw_block, dict):
                transformed.append(raw_block)
                continue
            block = dict(raw_block)
            if source_ratio > target_ratio:
                visible_width = source_height * target_ratio
                crop_left = (source_width - visible_width) / 2
                x = block.get("x")
                width = block.get("w")
                if (
                    isinstance(x, (int, float))
                    and not isinstance(x, bool)
                    and isinstance(width, (int, float))
                    and not isinstance(width, bool)
                ):
                    block["x"] = (float(x) * source_width - crop_left) / visible_width
                    block["w"] = float(width) * source_width / visible_width
            else:
                visible_height = source_width / target_ratio
                crop_top = (source_height - visible_height) / 2
                y = block.get("y")
                height = block.get("h")
                if (
                    isinstance(y, (int, float))
                    and not isinstance(y, bool)
                    and isinstance(height, (int, float))
                    and not isinstance(height, bool)
                ):
                    block["y"] = (float(y) * source_height - crop_top) / visible_height
                    block["h"] = float(height) * source_height / visible_height
            transformed.append(block)
        return transformed

    @classmethod
    def _build_skill_text_block_overlay(
        cls,
        *,
        slide: SlidePlan,
        raw_text_blocks: list[Any],
    ) -> list[dict[str, Any]]:
        """Compile exact Plan strings into the Skill-authored graphic-block manifest."""

        exact_texts = [slide.title, *slide.key_points]
        expected_refs = [
            "title",
            *[f"key_points.{index}" for index in range(len(slide.key_points))],
        ]
        elements: list[dict[str, Any]] = []
        for content_index, (raw_block, content_ref, exact_text) in enumerate(
            zip(raw_text_blocks, expected_refs, exact_texts, strict=True)
        ):
            if not isinstance(raw_block, dict):
                raise ValueError(f"paper-deck Skill returned an invalid text block on {slide.id}")
            if "text" in raw_block:
                raise ValueError(
                    f"paper-deck Skill manifest must reference, not author, text on {slide.id}"
                )
            if raw_block.get("content_ref") != content_ref:
                raise ValueError(
                    f"paper-deck Skill content_ref mismatch on {slide.id}: expected {content_ref}"
                )
            numeric: dict[str, float] = {}
            for key in ("x", "y", "w", "h", "font_size", "min_font_size"):
                value = raw_block.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"paper-deck Skill text block has invalid {key} on {slide.id}")
                numeric[key] = float(value)
            font_role = raw_block.get("font_role")
            if font_role not in {"sans", "serif", "handwritten", "display", "mono"}:
                raise ValueError(f"paper-deck Skill font role is invalid on {slide.id}")
            bold = raw_block.get("bold")
            if not isinstance(bold, bool):
                raise ValueError(f"paper-deck Skill font weight is invalid on {slide.id}")
            color = raw_block.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
                raise ValueError(f"paper-deck Skill text color is invalid on {slide.id}")
            align = raw_block.get("align")
            valign = raw_block.get("valign")
            if align not in {"left", "center", "right"} or valign not in {
                "top",
                "middle",
                "bottom",
            }:
                raise ValueError(f"paper-deck Skill text alignment is invalid on {slide.id}")
            max_lines = raw_block.get("max_lines")
            if isinstance(max_lines, bool) or not isinstance(max_lines, int):
                raise ValueError(f"paper-deck Skill max_lines is invalid on {slide.id}")

            quality_minimum = 24.0 if content_index == 0 else 14.0
            minimum_size = max(quality_minimum, numeric["min_font_size"])
            preferred_size = numeric["font_size"]
            if minimum_size > preferred_size:
                raise ValueError(
                    f"paper-deck Skill font range is invalid for {content_ref} on {slide.id}"
                )
            fitted_size = preferred_size
            physical_fit_size: float | None = None
            minimum_line_count = 0
            minimum_required_height = 0.0
            while fitted_size + 1e-9 >= minimum_size:
                line_count, required_height = cls._paper_deck_overlay_metrics(
                    exact_text,
                    numeric["w"],
                    fitted_size,
                    font_role=font_role,
                    bold=bold,
                )
                minimum_line_count = line_count
                minimum_required_height = required_height
                if required_height <= numeric["h"] + 0.003:
                    if physical_fit_size is None:
                        physical_fit_size = fitted_size
                    if line_count <= max_lines:
                        break
                fitted_size -= 1
            if fitted_size + 1e-9 < minimum_size:
                # max_lines is a visual preference from the Skill manifest, not a renderer
                # clipping instruction.  Keep it when possible, but do not reject exact Plan
                # copy that the real font metrics prove fits safely inside the authored block.
                if physical_fit_size is not None:
                    fitted_size = physical_fit_size
                else:
                    raise ValueError(
                        f"paper-deck Skill text block cannot fit exact Plan copy for "
                        f"{content_ref} on {slide.id}: at {minimum_size:g}pt it requires "
                        f"{minimum_line_count} lines and height {minimum_required_height:.3f}; "
                        f"the block is w={numeric['w']:.3f}, h={numeric['h']:.3f}, with "
                        f"preferred max_lines={max_lines}"
                    )
            elements.append(
                cls._paper_deck_overlay_element(
                    text=exact_text,
                    content_ref=content_ref,
                    x=numeric["x"],
                    y=numeric["y"],
                    width=numeric["w"],
                    height=numeric["h"],
                    z=20 + content_index,
                    font_size=fitted_size,
                    font_role=font_role,
                    bold=bold,
                    color=color.upper(),
                    align=align,
                    valign=valign,
                )
            )
        return elements

    @staticmethod
    def _paper_deck_module_reference_map(slide: SlidePlan) -> dict[str, str]:
        """Expose immutable semantic anchors that may own an editable visual object."""

        references = {"title": slide.title, "decoration": "deck decoration"}
        references.update(
            {f"key_points.{index}": value for index, value in enumerate(slide.key_points)}
        )
        references.update(CodexPPTProvider._visual_placeholder_reference_map(slide))
        return references

    @staticmethod
    def _paper_deck_module_style(
        style_token: str,
        *,
        element_type: str,
        theme: PresentationTheme,
    ) -> dict[str, Any]:
        """Map Skill-selected semantic tokens to deterministic theme-safe DrawingML styles."""

        palette = theme.palette
        shape_tokens: dict[str, tuple[str | None, str | None, float, int]] = {
            "paper_card": (palette.paper, palette.muted, 1.0, 100),
            "soft_panel": (palette.amber_soft, None, 0.0, 100),
            "outline_panel": (None, palette.muted, 1.25, 100),
            "accent_band": (palette.amber, None, 0.0, 100),
            "secondary_band": (palette.mint, None, 0.0, 100),
            "accent_node": (palette.amber, None, 0.0, 100),
            "muted_node": (palette.muted, None, 0.0, 100),
            "warning_mark": (palette.red, None, 0.0, 100),
            "tint_wash": (palette.amber_soft, None, 0.0, 58),
            "hairline_frame": (None, palette.muted, 0.75, 100),
            "figure_frame": (palette.paper, palette.ink, 0.75, 100),
            "ink_node": (palette.ink, None, 0.0, 100),
        }
        line_tokens: dict[str, tuple[str, float, int]] = {
            "ink_rule": (palette.ink, 1.5, 100),
            "accent_rule": (palette.amber, 2.0, 100),
            "muted_rule": (palette.muted, 1.25, 100),
            "caption_rule": (palette.muted, 0.75, 100),
        }
        if element_type == "line":
            if style_token not in line_tokens:
                raise ValueError(f"paper-deck line cannot use module style token {style_token!r}")
            line_color, line_width, opacity = line_tokens[style_token]
            fill = None
        else:
            if style_token not in shape_tokens:
                raise ValueError(f"paper-deck shape cannot use module style token {style_token!r}")
            fill, line_color, line_width, opacity = shape_tokens[style_token]
        return {
            "font_size": 18,
            "font_role": "sans",
            "text_margin_x": 0,
            "text_margin_y": 0,
            "bold": False,
            "color": palette.ink,
            "fill": fill,
            "line_color": line_color,
            "line_width": line_width,
            "align": "left",
            "valign": "top",
            "opacity": opacity,
        }

    @classmethod
    def _build_visual_module_elements(
        cls,
        *,
        slide: SlidePlan,
        raw_modules: list[Any],
        theme: PresentationTheme,
    ) -> list[dict[str, Any]]:
        """Compile every Paper Deck frame, rule, and node as its own PPT object."""

        if not raw_modules or len(raw_modules) > cls.PAPER_DECK_MAX_VISUAL_MODULES:
            raise ValueError(
                f"paper-deck must return 1-{cls.PAPER_DECK_MAX_VISUAL_MODULES} "
                f"editable modules on {slide.id}"
            )
        allowed_refs = cls._paper_deck_module_reference_map(slide)
        seen_ids: set[str] = set()
        elements: list[dict[str, Any]] = []
        for raw_module in raw_modules:
            if not isinstance(raw_module, dict):
                raise ValueError(f"paper-deck returned an invalid module on {slide.id}")
            if any(key in raw_module for key in ("text", "items", "image_path")):
                raise ValueError(
                    f"paper-deck visual module contains authored content on {slide.id}"
                )
            object_id = raw_module.get("object_id")
            content_ref = raw_module.get("content_ref")
            element_type = raw_module.get("type")
            if not isinstance(object_id, str) or object_id in seen_ids:
                raise ValueError(
                    f"paper-deck visual module object_id is missing or duplicated on {slide.id}"
                )
            if content_ref not in allowed_refs:
                raise ValueError(
                    f"paper-deck visual module references unknown Plan content on {slide.id}: "
                    f"{content_ref!r}"
                )
            if element_type not in {"shape", "line"}:
                raise ValueError(
                    f"paper-deck visual module type is invalid on {slide.id}: {element_type!r}"
                )
            seen_ids.add(object_id)
            payload = {
                "type": element_type,
                "contract_role": "visual_module",
                "object_id": object_id,
                "semantic_ref": content_ref,
                "x": raw_module.get("x"),
                "y": raw_module.get("y"),
                "w": raw_module.get("w"),
                "h": raw_module.get("h"),
                "z": raw_module.get("z"),
                "text": None,
                "items": [],
                "shape": raw_module.get("shape", "rectangle"),
                "image_path": None,
                "style": cls._paper_deck_module_style(
                    str(raw_module.get("style_token", "")),
                    element_type=element_type,
                    theme=theme,
                ),
            }
            element = SlideElement.model_validate(payload)
            if element.type == "shape" and element.w * element.h > 0.68:
                raise ValueError(
                    f"paper-deck visual module is effectively a full-slide merged object on "
                    f"{slide.id}: {object_id}"
                )
            elements.append(element.model_dump(mode="json"))
        return elements

    @classmethod
    def _build_visual_asset_elements(
        cls,
        *,
        slide: SlidePlan,
        raw_assets: list[Any],
        slide_workspace: Path,
        generated_visuals: Path,
    ) -> list[dict[str, Any]]:
        """Persist each locally generated illustration as an independent picture."""

        if len(raw_assets) > cls.PAPER_DECK_MAX_VISUAL_ASSETS:
            raise ValueError(f"paper-deck returned too many visual assets on {slide.id}")
        allowed_refs = cls._visual_placeholder_reference_map(slide)
        elements: list[dict[str, Any]] = []
        seen_refs: set[str] = set()
        for index, raw_asset in enumerate(raw_assets):
            if not isinstance(raw_asset, dict):
                raise ValueError(f"paper-deck returned an invalid visual asset on {slide.id}")
            if any(key in raw_asset for key in ("text", "items", "style")):
                raise ValueError(
                    f"paper-deck visual asset contains authored copy or styling on {slide.id}"
                )
            content_ref = raw_asset.get("content_ref")
            if content_ref not in allowed_refs:
                raise ValueError(
                    f"paper-deck visual asset references unknown Plan content on {slide.id}: "
                    f"{content_ref!r}"
                )
            if content_ref in seen_refs:
                raise ValueError(
                    f"paper-deck local illustrations must use distinct Plan visual refs on "
                    f"{slide.id}: {content_ref}"
                )
            seen_refs.add(content_ref)
            persisted = cls._persist_generated_image(
                raw_asset.get("image_path"),
                workspace=slide_workspace,
                asset_output_dir=generated_visuals,
                slide_id=slide.id,
                image_number=index + 1,
                fallback_generated_path=(
                    slide_workspace / ".codex_image_outputs" / f"{index + 1:02d}.png"
                ),
                prefer_fallback=True,
            )
            element = SlideElement.model_validate(
                {
                    "type": "image",
                    "contract_role": "visual_asset",
                    "object_id": raw_asset.get("object_id"),
                    "semantic_ref": content_ref,
                    "x": raw_asset.get("x"),
                    "y": raw_asset.get("y"),
                    "w": raw_asset.get("w"),
                    "h": raw_asset.get("h"),
                    "z": raw_asset.get("z"),
                    "text": None,
                    "items": [],
                    "shape": "rectangle",
                    "image_path": f"generated_visuals/{persisted.name}",
                    "image_fit": raw_asset.get("image_fit", "contain"),
                    "style": {
                        "font_size": 18,
                        "font_role": "sans",
                        "text_margin_x": 0,
                        "text_margin_y": 0,
                        "bold": False,
                        "color": "000000",
                        "fill": None,
                        "line_color": None,
                        "line_width": 0,
                        "align": "left",
                        "valign": "top",
                        "opacity": 100,
                    },
                }
            )
            if cls._is_full_bleed_background_image(element):
                raise ValueError(
                    f"paper-deck visual asset cannot flatten the entire slide on {slide.id}"
                )
            elements.append(element.model_dump(mode="json"))
        return elements

    @staticmethod
    def _visual_placeholder_reference_map(slide: SlidePlan) -> dict[str, str]:
        """Expose only immutable Plan visual directions as placeholder subjects."""

        references = {"suggested_visual": slide.suggested_visual}
        for index, value in enumerate(slide.visual_payload[:8]):
            if value.strip():
                references[f"visual_payload.{index}"] = value
        return references

    @classmethod
    def _paper_deck_visual_requirement(cls, slide: SlidePlan) -> dict[str, Any]:
        """Choose local illustration versus a real/source-specific asset gap deterministically."""

        references = cls._visual_placeholder_reference_map(slide)
        visual_direction = " ".join(
            [
                slide.title,
                slide.suggested_visual,
                slide.layout_id or "",
                *slide.visual_payload,
            ]
        ).lower()
        source_specific = bool(
            re.search(
                r"(?:截图|界面|软件面板|真实照片|实景|卫星影像|遥感影像|原始影像|"
                r"原论文(?:图|表)|论文原图|实验(?:截图|曲线|图表)|文档页面|pdf页面|"
                r"screenshot|software\s+ui|user\s+interface|photograph|photo|"
                r"satellite\s+image|remote[- ]sensing\s+image|original\s+paper\s+figure|"
                r"source[- ]specific|document\s+page|experimental\s+(?:chart|plot))",
                visual_direction,
                flags=re.IGNORECASE,
            )
        )
        if slide.order > 1 and source_specific:
            return {
                "mode": "source-placeholder",
                "min_assets": 0,
                "max_assets": 0,
                "min_placeholders": 1,
                "max_placeholders": 1,
                "reason": "the Plan asks for unavailable real or source-specific evidence",
            }

        high_visual_value = slide.order == 1 or bool(
            re.search(
                r"(?:机制|机理|原理|流程|步骤|算法|模型|结构|框架|案例|应用|对比|比较|"
                r"评估|结果|实验|证据|演化|路径|局部放大|method|mechanism|process|"
                r"workflow|pipeline|algorithm|model|architecture|framework|case|"
                r"application|comparison|evaluation|result|experiment|evidence|zoom)",
                visual_direction,
                flags=re.IGNORECASE,
            )
        )
        max_assets = min(cls.PAPER_DECK_MAX_VISUAL_ASSETS, len(references))
        if high_visual_value:
            return {
                "mode": "required-local-illustration",
                "min_assets": 1,
                "max_assets": max(1, max_assets),
                "min_placeholders": 0,
                "max_placeholders": 0,
                "reason": "this mechanism, method, case, evidence, or cover page needs a visual anchor",
            }
        return {
            "mode": "optional-local-illustration",
            "min_assets": 0,
            "max_assets": min(1, max_assets),
            "min_placeholders": 0,
            "max_placeholders": 0,
            "reason": "this text-led page may use one local illustration when it improves the editorial composition",
        }

    @classmethod
    def _paper_deck_composition_plan(
        cls,
        plan: PresentationPlan,
    ) -> list[dict[str, str]]:
        """Assign a deterministic Paper Deck rhythm without changing any Plan copy."""

        registry = {spec.id: spec for spec in LAYOUT_REGISTRY}
        role_candidates: dict[str, tuple[str, ...]] = {
            "cover": ("hero_minimal",),
            "section": ("hero_statement", "quote_field"),
            "concept": ("constellation", "focus_rail", "split_left", "split_right"),
            "method": ("sequence_horizontal", "ladder", "sequence_vertical"),
            "formula": ("focus_rail", "constellation", "evidence_strip"),
            "comparison": ("comparison_split", "matrix", "before_after"),
            "case": ("evidence_strip", "before_after", "split_right"),
            "practice": ("sequence_vertical", "timeline_alternating", "evidence_strip"),
            "summary": ("summary_path", "hero_statement", "focus_rail"),
            "other": (
                "focus_rail",
                "evidence_strip",
                "constellation",
                "sequence_horizontal",
                "split_left",
                "split_right",
            ),
        }
        planned: list[dict[str, str]] = []
        previous_layout_id = ""
        previous_family = ""
        role_occurrences: dict[str, int] = {}
        for index, slide in enumerate(plan.slides):
            if index == 0:
                candidates = [registry["hero_minimal"]]
            else:
                candidate_ids = list(
                    role_candidates.get(slide.slide_role, role_candidates["other"])
                )
                fallback = select_fallback_layout(slide, index)
                if slide.slide_role == "other" and slide.layout_id in registry:
                    candidate_ids.insert(0, str(slide.layout_id))
                elif slide.layout_id in registry:
                    candidate_ids.append(str(slide.layout_id))
                candidate_ids.append(fallback.id)
                candidates = []
                seen: set[str] = set()
                for candidate_id in candidate_ids:
                    if candidate_id in registry and candidate_id not in seen:
                        candidates.append(registry[candidate_id])
                        seen.add(candidate_id)

            point_count = len(slide.key_points)
            average_length = sum(len(point) for point in slide.key_points) / max(1, point_count)
            if average_length > 42 or point_count > 5:
                roomy_ids = ("evidence_strip", "split_left", "split_right", "focus_rail")
                roomy = [registry[item] for item in roomy_ids if item in registry]
                candidates = [
                    *[item for item in roomy if item in candidates],
                    *[item for item in candidates if item not in roomy],
                    *[item for item in roomy if item not in candidates],
                ]

            occurrence = role_occurrences.get(slide.slide_role, 0)
            role_occurrences[slide.slide_role] = occurrence + 1
            selected = candidates[occurrence % len(candidates)]
            if len(candidates) > 1 and (
                selected.id == previous_layout_id
                or (selected.family == previous_family and selected.family == "split")
            ):
                selected = next(
                    (
                        item
                        for item in candidates
                        if item.id != previous_layout_id and item.family != previous_family
                    ),
                    next(item for item in candidates if item.id != previous_layout_id),
                )
            planned.append(cls._paper_deck_composition_payload(selected))
            previous_layout_id = selected.id
            previous_family = selected.family
        return planned

    @staticmethod
    def _paper_deck_composition_payload(spec: LayoutSpec) -> dict[str, str]:
        directions = {
            "hero_minimal": (
                "Editorial hero: place the title as the dominant field with one large visual anchor "
                "offset from center and only sparse supporting copy."
            ),
            "hero_statement": (
                "Full-width statement page: use one oversized claim, a restrained secondary line, "
                "and an asymmetric visual accent; do not turn it into two columns."
            ),
            "split_left": (
                "Asymmetric split: give the left editorial field more weight and place evidence or a "
                "local visual on the right, with unequal widths and a deliberate gutter."
            ),
            "split_right": (
                "Asymmetric split: stage evidence or a local visual on the left and place the main "
                "copy field on the right, with unequal widths and a deliberate gutter."
            ),
            "focus_rail": (
                "Focal field plus interpretation rail: use a large central or off-center concept, "
                "with independent notes arranged on a narrow top, bottom, or side rail."
            ),
            "quote_field": (
                "Definition field: use a large typographic statement across the canvas with two or "
                "three small marginal annotations, not a card grid."
            ),
            "sequence_horizontal": (
                "Horizontal process: build a wide left-to-right path through the middle of the page; "
                "place exact-copy annotations above and below its independent nodes."
            ),
            "sequence_vertical": (
                "Vertical process: build a top-to-bottom method spine with alternating annotations on "
                "both sides; keep every node, connector, and text anchor independent."
            ),
            "comparison_split": (
                "Comparison spread: create two balanced evidence fields separated by a clear axis, "
                "with differences emphasized through restrained rules and labels."
            ),
            "before_after": (
                "Before/after narrative: use two staged states connected by one directional transition; "
                "reserve a separate conclusion strip instead of stacking cards."
            ),
            "timeline_alternating": (
                "Alternating timeline: run a strong time or reasoning axis across the page and alternate "
                "independent annotations around it."
            ),
            "ladder": (
                "Progressive ladder: arrange method stages diagonally or as stepped bands, with a clear "
                "start, escalation, and outcome."
            ),
            "constellation": (
                "Annotated mechanism: place one central scientific figure or concept and distribute "
                "independent callouts around it with short semantic connectors."
            ),
            "matrix": (
                "Analytical matrix: use a two-axis field or four distinct quadrants, keeping each region "
                "flat, independent, and evidence-led rather than UI-like."
            ),
            "evidence_strip": (
                "Evidence stage: give a wide central or lower visual/evidence region 60-75% of the useful "
                "canvas and place interpretation in a separate top, bottom, or marginal strip."
            ),
            "summary_path": (
                "Synthesis path: arrange the final ideas as a curved or stepped reading path ending in "
                "one dominant takeaway, without introducing new content."
            ),
        }
        return {
            "layout_id": spec.id,
            "family": spec.family,
            "description": spec.description,
            "direction": directions[spec.id],
        }

    @classmethod
    def _visual_placeholder_label(cls, slide: SlidePlan, content_ref: str) -> str:
        references = cls._visual_placeholder_reference_map(slide)
        if content_ref not in references:
            raise ValueError(
                f"paper-deck visual placeholder references unknown Plan content on {slide.id}: "
                f"{content_ref}"
            )
        subject = " ".join(references[content_ref].split())
        subject = re.sub(r"[`*_#]+", "", subject)
        subject = re.sub(
            r"^(?:(?:建议|请)\s*)?(?:使用|插入|展示|采用|放置|呈现)\s*",
            "",
            subject,
        )
        subject = re.sub(r"^无文字(?:的)?\s*", "", subject)
        subject = subject.strip(" ：:，,。.;；") or "相关图片"
        if len(subject) > 44:
            subject = subject[:43].rstrip() + "…"
        if not re.search(
            r"(?:图|照片|截图|地图|曲线|图表|表格|影像|页面|figure|chart)$",
            subject,
            flags=re.IGNORECASE,
        ):
            subject += "相关图片"
        return f"（此处建议插入：{subject}）"

    @classmethod
    def _build_visual_placeholder_overlay(
        cls,
        *,
        slide: SlidePlan,
        raw_placeholders: list[Any],
        theme: PresentationTheme,
    ) -> list[dict[str, Any]]:
        """Compile a model-selected asset gap into one deterministic editable frame."""

        if len(raw_placeholders) > cls.PAPER_DECK_MAX_VISUAL_PLACEHOLDERS:
            raise ValueError(f"paper-deck returned too many visual placeholders on {slide.id}")
        elements: list[dict[str, Any]] = []
        for index, raw_placeholder in enumerate(raw_placeholders):
            if not isinstance(raw_placeholder, dict):
                raise ValueError(
                    f"paper-deck Skill returned an invalid visual placeholder on {slide.id}"
                )
            if "text" in raw_placeholder:
                raise ValueError(
                    f"paper-deck Skill must reference, not author, placeholder text on {slide.id}"
                )
            content_ref = raw_placeholder.get("content_ref")
            if not isinstance(content_ref, str):
                raise ValueError(f"paper-deck visual placeholder has no content_ref on {slide.id}")
            numeric: dict[str, float] = {}
            for key in ("x", "y", "w", "h"):
                value = raw_placeholder.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"paper-deck visual placeholder has invalid {key} on {slide.id}"
                    )
                numeric[key] = float(value)

            label = cls._visual_placeholder_label(slide, content_ref)
            preferred_size = 17.0
            minimum_size = 13.0
            fitted_size: float | None = None
            usable_width = max(0.05, numeric["w"] - 0.022)
            usable_height = max(0.04, numeric["h"] - 0.024)
            candidate_size = preferred_size
            while candidate_size + 1e-9 >= minimum_size:
                line_count, required_height = cls._paper_deck_overlay_metrics(
                    label,
                    usable_width,
                    candidate_size,
                    font_role="sans",
                    bold=False,
                )
                if required_height <= usable_height + 0.003 and line_count <= 4:
                    fitted_size = candidate_size
                    break
                candidate_size -= 1
            if fitted_size is None:
                raise ValueError(
                    f"paper-deck visual placeholder label does not fit on {slide.id}: {label}"
                )

            elements.append(
                {
                    "type": "text",
                    "contract_role": "visual_placeholder",
                    "object_id": f"placeholder-{index + 1}",
                    "semantic_ref": content_ref,
                    "x": round(numeric["x"], 5),
                    "y": round(numeric["y"], 5),
                    "w": round(numeric["w"], 5),
                    "h": round(numeric["h"], 5),
                    "z": 45 + index,
                    "text": label,
                    "image_path": "",
                    "shape": "rounded_rectangle",
                    "style": {
                        "font_size": fitted_size,
                        "font_role": "sans",
                        "text_margin_x": 0.1,
                        "text_margin_y": 0.08,
                        "bold": False,
                        "color": theme.palette.ink,
                        "fill": theme.palette.paper,
                        "line_color": theme.palette.muted,
                        "line_width": 1.5,
                        "align": "center",
                        "valign": "middle",
                        "opacity": 100,
                    },
                }
            )
        return elements

    @classmethod
    def _validate_distributed_module_spacing(
        cls,
        elements: list[SlideElement],
        *,
        slide: SlidePlan,
    ) -> None:
        """Keep body treatments as separate visual islands instead of one fused cluster."""

        cls._validate_paper_deck_visual_region(elements, slide=slide)

        body_text = [
            element
            for element in elements
            if element.type == "text"
            and not (element.contract_role == "plan_copy" and element.semantic_ref == "title")
        ]
        for index, left in enumerate(body_text):
            for right in body_text[index + 1 :]:
                horizontal_gap = max(
                    right.x - (left.x + left.w),
                    left.x - (right.x + right.w),
                )
                vertical_gap = max(
                    right.y - (left.y + left.h),
                    left.y - (right.y + right.h),
                )
                if (
                    horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                    and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                ):
                    raise ValueError(
                        "paper-deck visual modules are fused or too tightly clustered on "
                        f"{slide.id}"
                    )

        plan_text = [
            element
            for element in elements
            if element.type == "text" and element.contract_role == "plan_copy"
        ]
        visual_modules = [
            element for element in elements if element.contract_role == "visual_module"
        ]
        for module in visual_modules:
            if module.type == "line":
                for text in plan_text:
                    if cls._paper_deck_line_intersects_element(module, text):
                        raise ValueError(
                            f"paper-deck connector crosses a Plan text writing area on "
                            f"{slide.id}: {module.object_id}"
                        )
                continue
            if module.type != "shape":
                continue
            for text in plan_text:
                if not cls._paper_deck_elements_intersect(module, text):
                    continue
                if (
                    module.z >= text.z
                    or not cls._paper_deck_element_contains(module, text)
                    or module.semantic_ref not in {text.semantic_ref, "decoration"}
                ):
                    raise ValueError(
                        f"paper-deck visual module partially overlaps or occludes Plan text "
                        f"on {slide.id}: {module.object_id}"
                    )

        plan_text_by_ref = {
            element.semantic_ref: element
            for element in plan_text
            if element.semantic_ref is not None
        }
        for point_index in range(len(slide.key_points)):
            content_ref = f"key_points.{point_index}"
            text = plan_text_by_ref.get(content_ref)
            if text is None or not any(
                cls._paper_deck_module_anchors_text(module, text)
                for module in visual_modules
                if module.semantic_ref == content_ref and module.z < text.z
            ):
                raise ValueError(
                    f"paper-deck has no independent editorial anchor for {content_ref} on "
                    f"{slide.id}"
                )

        visual_islands = [
            element
            for element in elements
            if (
                element.contract_role in {"visual_module", "visual_asset"}
                and element.type != "line"
            )
            or element.contract_role == "visual_placeholder"
        ]
        for module in (
            element
            for element in visual_islands
            if element.contract_role == "visual_module" and element.type == "shape"
        ):
            contained_body = [
                text
                for text in body_text
                if text.contract_role == "plan_copy"
                and cls._paper_deck_element_contains(module, text)
            ]
            if len(contained_body) > 1:
                raise ValueError(
                    f"paper-deck merged multiple Plan points into one visual module on "
                    f"{slide.id}: {module.object_id}"
                )
            if contained_body and module.semantic_ref not in {
                contained_body[0].semantic_ref,
                "decoration",
            }:
                raise ValueError(
                    f"paper-deck visual module is bound to the wrong Plan point on {slide.id}: "
                    f"{module.object_id}"
                )

        for index, left in enumerate(visual_islands):
            for right in visual_islands[index + 1 :]:
                if left.semantic_ref == right.semantic_ref:
                    continue
                horizontal_gap = max(
                    right.x - (left.x + left.w),
                    left.x - (right.x + right.w),
                )
                vertical_gap = max(
                    right.y - (left.y + left.h),
                    left.y - (right.y + right.h),
                )
                if (
                    horizontal_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                    and vertical_gap < cls.PAPER_DECK_MIN_MODULE_GAP
                ):
                    raise ValueError(
                        "paper-deck independent visual objects overlap or form one merged "
                        f"cluster on {slide.id}"
                    )

    @classmethod
    def _paper_deck_module_anchors_text(
        cls,
        module: SlideElement,
        text: SlideElement,
    ) -> bool:
        """Accept a restrained frame or a nearby editorial rule/marker for one Plan point."""

        # Paper Deck commonly leaves a deliberate 50-80 px gutter between a side band and
        # exact-copy text on a 1920 px canvas.  Keep the validation tolerant enough for that
        # editorial whitespace while still requiring the module to be visibly associated with
        # the text block and bound to the same immutable semantic_ref.
        maximum_anchor_gap = 0.05

        if module.type == "shape" and cls._paper_deck_element_contains(module, text):
            return True
        if module.type not in {"shape", "line"}:
            return False
        if module.type == "shape" and cls._paper_deck_elements_intersect(module, text):
            return False
        horizontal_gap = max(
            text.x - (module.x + module.w),
            module.x - (text.x + text.w),
            0.0,
        )
        vertical_gap = max(
            text.y - (module.y + module.h),
            module.y - (text.y + text.h),
            0.0,
        )
        x_overlap = max(
            0.0,
            min(module.x + module.w, text.x + text.w) - max(module.x, text.x),
        )
        y_overlap = max(
            0.0,
            min(module.y + module.h, text.y + text.h) - max(module.y, text.y),
        )
        if module.type == "line":
            mostly_horizontal = abs(module.w) >= abs(module.h)
            if mostly_horizontal:
                line_y = module.y + module.h / 2
                beside_text = (
                    horizontal_gap <= maximum_anchor_gap
                    and text.y - 0.01 <= line_y <= text.y + text.h + 0.01
                )
                under_or_over_text = vertical_gap <= maximum_anchor_gap and x_overlap >= min(
                    0.08, text.w * 0.25
                )
                return beside_text or under_or_over_text
            beside_text = horizontal_gap <= maximum_anchor_gap and y_overlap >= min(
                0.06, text.h * 0.35
            )
            above_or_below_text = vertical_gap <= maximum_anchor_gap and x_overlap >= min(
                0.06, text.w * 0.2
            )
            return beside_text or above_or_below_text
        marker_area = module.w * module.h
        return (
            marker_area <= 0.025
            and horizontal_gap <= maximum_anchor_gap
            and vertical_gap <= maximum_anchor_gap
            and (x_overlap > 0 or y_overlap > 0)
        )

    @staticmethod
    def _paper_deck_overlay_line_count(text: str, width: float, font_size: float) -> int:
        return CodexPPTProvider._paper_deck_overlay_metrics(
            text,
            width,
            font_size,
            font_role="sans",
            bold=False,
        )[0]

    @staticmethod
    def _paper_deck_overlay_text_height(text: str, width: float, font_size: float) -> float:
        return CodexPPTProvider._paper_deck_overlay_metrics(
            text,
            width,
            font_size,
            font_role="sans",
            bold=False,
        )[1]

    @staticmethod
    def _paper_deck_overlay_metrics(
        text: str,
        width: float,
        font_size: float,
        *,
        font_role: str,
        bold: bool,
    ) -> tuple[int, float]:
        """Measure exact copy with the same font, margins, and wrap engine as previews."""

        canvas = Image.new("L", (1280, 720), 0)
        draw = ImageDraw.Draw(canvas)
        pixel_size = max(1, round(font_size * 96 / 72))
        font = PPTSkillAdapter._load_preview_font(
            pixel_size,
            font_role,
            bold=bold,
        )
        font_path = str(getattr(font, "path", "")).lower()
        has_bold_face = any(
            marker in Path(font_path).stem for marker in ("bold", "black", "heavy", "bd")
        )
        stroke_width = 1 if bold and not has_bold_face else 0
        wrapped = PPTSkillAdapter._wrap_preview_text(
            draw,
            text,
            font=font,
            max_width=max(1, round(width * 1280)),
            stroke_width=stroke_width,
        )
        spacing = max(1, round(pixel_size * 0.18))
        bounds = draw.multiline_textbbox(
            (0, 0),
            wrapped,
            font=font,
            spacing=spacing,
            stroke_width=stroke_width,
        )
        line_count = wrapped.count("\n") + 1
        height_pixels = max(1, bounds[3] - bounds[1])
        return line_count, max(0.045, height_pixels / 720)

    @staticmethod
    def _paper_deck_overlay_element(
        *,
        text: str,
        content_ref: str,
        x: float,
        y: float,
        width: float,
        height: float,
        z: int,
        font_size: float,
        font_role: str,
        bold: bool,
        color: str,
        align: str,
        valign: str,
    ) -> dict[str, Any]:
        return {
            "type": "text",
            "contract_role": "plan_copy",
            "object_id": f"copy-{content_ref.replace('.', '-')}",
            "semantic_ref": content_ref,
            "x": round(x, 5),
            "y": round(y, 5),
            "w": round(width, 5),
            "h": round(height, 5),
            "z": z,
            "text": text,
            "image_path": "",
            "shape": "rectangle",
            "style": {
                "font_size": round(font_size, 1),
                "font_role": font_role,
                "text_margin_x": 0,
                "text_margin_y": 0,
                "bold": bold,
                "color": color,
                "fill": None,
                "line_color": None,
                "line_width": 0,
                "align": align,
                "valign": valign,
                "opacity": 100,
            },
        }

    @classmethod
    def _paper_deck_glyph_contrast(
        cls,
        image_path: Path,
        element: SlideElement,
    ) -> float:
        """Measure contrast only where the final aligned glyphs cover background pixels."""

        try:
            with Image.open(image_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                left = max(0, min(image.width - 1, round(element.x * image.width)))
                top = max(0, min(image.height - 1, round(element.y * image.height)))
                right = max(
                    left + 1,
                    min(image.width, round((element.x + element.w) * image.width)),
                )
                bottom = max(
                    top + 1,
                    min(image.height, round((element.y + element.h) * image.height)),
                )
                sample = image.crop((left, top, right, bottom))
        except (OSError, UnidentifiedImageError):
            return 0.0

        style = element.style
        render_dpi = image.width / 13.333
        pixel_size = max(1, round(style.font_size * render_dpi / 72))
        font = PPTSkillAdapter._load_preview_font(
            pixel_size,
            style.font_role,
            bold=style.bold,
        )
        font_path = str(getattr(font, "path", "")).lower()
        has_bold_face = any(
            marker in Path(font_path).stem for marker in ("bold", "black", "heavy", "bd")
        )
        stroke_width = 1 if style.bold and not has_bold_face else 0
        mask = Image.new("L", sample.size, 0)
        draw = ImageDraw.Draw(mask)
        wrapped = PPTSkillAdapter._wrap_preview_text(
            draw,
            element.text or "",
            font=font,
            max_width=max(1, sample.width),
            stroke_width=stroke_width,
        )
        spacing = max(1, round(pixel_size * 0.18))
        bounds = draw.multiline_textbbox(
            (0, 0),
            wrapped,
            font=font,
            spacing=spacing,
            align=style.align,
            stroke_width=stroke_width,
        )
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        if style.align == "center":
            target_x = (sample.width - text_width) / 2
        elif style.align == "right":
            target_x = sample.width - text_width
        else:
            target_x = 0
        if style.valign == "middle":
            target_y = (sample.height - text_height) / 2
        elif style.valign == "bottom":
            target_y = sample.height - text_height
        else:
            target_y = 0
        draw.multiline_text(
            (target_x - bounds[0], target_y - bounds[1]),
            wrapped,
            fill=255,
            font=font,
            spacing=spacing,
            align=style.align,
            stroke_width=stroke_width,
            stroke_fill=255,
        )
        mask_flattened = getattr(mask, "get_flattened_data", None)
        sample_flattened = getattr(sample, "get_flattened_data", None)
        mask_pixels = list(mask_flattened() if mask_flattened is not None else mask.getdata())
        sample_pixels = list(
            sample_flattened() if sample_flattened is not None else sample.getdata()
        )
        pixels = [
            pixel for pixel, alpha in zip(sample_pixels, mask_pixels, strict=True) if alpha >= 32
        ]
        if not pixels:
            return 0.0

        pixel_luminances: list[float] = []
        for red, green, blue in pixels:
            channels = [value / 255 for value in (red, green, blue)]
            linear = [
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            pixel_luminances.append(0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])

        foreground = cls._relative_luminance(style.color)
        ratios = sorted(
            (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
            for background in pixel_luminances
        )
        percentile_index = max(0, int((len(pixel_luminances) - 1) * 0.10))
        return ratios[percentile_index]

    def _materialize_result(
        self,
        *,
        plan: PresentationPlan,
        result: dict[str, Any],
        deck_path: Path,
        theme: PresentationTheme,
        workspace: Path | None = None,
        asset_output_dir: Path | None = None,
        allow_images: bool = False,
        require_image: bool = False,
    ) -> tuple[PresentationPlan, str]:
        status = str(result.get("status", "unknown"))
        if status != "completed":
            summary = str(result.get("summary", "Codex did not complete the design"))
            raise ValueError(f"Codex returned {status}: {summary[:1000]}")

        # Retain compatibility with a future runtime that can safely create the
        # binary itself. The normal local-account path uses structured design.
        if str(result.get("deck_path", "")) == "deck.pptx" and deck_path.is_file():
            if allow_images:
                raise ValueError(
                    "paper-craft hybrid must return audited image elements, not a binary deck"
                )
            return plan, "direct-pptx"

        designed_plan = self._design_plan_from_result(
            plan,
            result,
            theme,
            workspace=workspace,
            asset_output_dir=asset_output_dir,
            allow_images=allow_images,
            max_images=self.paper_craft_max_images if allow_images else 0,
            require_editable_layers_per_slide=require_image,
        )
        if require_image and any(
            not any(element.contract_role == "visual_module" for element in slide.elements)
            for slide in designed_plan.slides
        ):
            raise ValueError("paper-deck must provide editable visual modules on every slide")
        self.adapter.render_declarative_pptx(designed_plan, deck_path)
        return designed_plan, (
            "paper-craft-layered-safe-render" if require_image else "structured-design-safe-render"
        )

    @classmethod
    def _design_plan_from_result(
        cls,
        plan: PresentationPlan,
        result: dict[str, Any],
        theme: PresentationTheme,
        *,
        workspace: Path | None = None,
        asset_output_dir: Path | None = None,
        allow_images: bool = False,
        max_images: int = 0,
        require_editable_layers_per_slide: bool = False,
    ) -> PresentationPlan:
        raw_slides = result.get("slides")
        if not isinstance(raw_slides, list):
            raise ValueError("Codex returned no structured slide designs")
        if len(raw_slides) != len(plan.slides):
            raise ValueError(
                "Codex design slide-count mismatch: "
                f"expected {len(plan.slides)}, got {len(raw_slides)}"
            )

        designed_slides = []
        image_count = 0
        for index, (expected, raw_slide) in enumerate(zip(plan.slides, raw_slides, strict=True)):
            if not isinstance(raw_slide, dict):
                raise ValueError(f"Codex design slide {index + 1} is not an object")
            if raw_slide.get("slide_id") != expected.id:
                raise ValueError(
                    f"Codex design slide-id mismatch at position {index + 1}: "
                    f"expected {expected.id}, got {raw_slide.get('slide_id')}"
                )

            expected_background = theme.palette.board if index == 0 else theme.palette.paper
            background = str(raw_slide.get("background", "")).upper()
            if background != expected_background:
                raise ValueError(
                    f"Codex design background mismatch on {expected.id}: "
                    f"expected {expected_background}, got {background or '<empty>'}"
                )

            raw_elements = raw_slide.get("elements")
            if not isinstance(raw_elements, list) or not raw_elements:
                raise ValueError(f"Codex design contains no elements on {expected.id}")
            if len(raw_elements) > 40:
                raise ValueError(f"Codex design contains too many elements on {expected.id}")

            elements: list[SlideElement] = []
            visible_texts: list[str] = []
            placeholder_texts: list[str] = []
            has_visual = False
            for raw_element in raw_elements:
                if (
                    isinstance(raw_element, dict)
                    and raw_element.get("type") == "image"
                    and image_count >= max_images
                ):
                    raise ValueError(
                        "Codex design exceeds the configured paper-craft image budget: "
                        f"more than {max_images}"
                    )
                element = cls._parse_design_element(
                    raw_element,
                    workspace=workspace,
                    asset_output_dir=asset_output_dir,
                    slide_id=expected.id,
                    image_number=image_count + 1,
                    allow_images=allow_images,
                )
                if element.type == "image":
                    image_count += 1
                if element.type == "text":
                    text = element.text or ""
                    if element.contract_role == "visual_placeholder":
                        placeholder_texts.append(text)
                    else:
                        visible_texts.append(text)
                    fitted_size = element.style.font_size
                    if not require_editable_layers_per_slide:
                        is_title = text == expected.title
                        min_font_size = 28 if is_title else 18
                        fitted_size = PresentationPlanGenerator._fit_font_size(
                            text=text,
                            width=element.w,
                            height=element.h,
                            font_size=element.style.font_size,
                            min_font_size=min_font_size,
                            is_title=is_title,
                        )
                        if fitted_size is None:
                            raise ValueError(
                                f"Codex text does not fit on {expected.id}: {text[:80]}"
                            )
                    element = element.model_copy(
                        update={
                            "z": 50 + len(visible_texts) + len(placeholder_texts),
                            "style": element.style.model_copy(update={"font_size": fitted_size}),
                        }
                    )
                else:
                    if element.text:
                        raise ValueError(f"Non-text element contains visible copy on {expected.id}")
                    has_visual = True
                    element = element.model_copy(update={"z": min(element.z, 40)})
                elements.append(
                    element
                    if require_editable_layers_per_slide
                    else apply_brand_palette(element, theme.palette)
                )

            background_images = [
                element for element in elements if cls._is_full_bleed_background_image(element)
            ]
            slide_images = [element for element in elements if element.type == "image"]
            if require_editable_layers_per_slide:
                if background_images:
                    raise ValueError(
                        "paper-deck layered mode forbids a full-slide merged image "
                        f"on {expected.id}"
                    )
                if len(slide_images) > cls.PAPER_DECK_MAX_VISUAL_ASSETS:
                    raise ValueError(
                        f"paper-deck layered mode allows at most "
                        f"{cls.PAPER_DECK_MAX_VISUAL_ASSETS} local illustrations on "
                        f"{expected.id}"
                    )
                editable_modules = [
                    element for element in elements if element.contract_role == "visual_module"
                ]
                if (
                    not editable_modules
                    or len(editable_modules) > cls.PAPER_DECK_MAX_VISUAL_MODULES
                ):
                    raise ValueError(
                        f"paper-deck layered mode has an invalid module count on {expected.id}"
                    )
                object_ids = [element.object_id for element in elements]
                if any(object_id is None for object_id in object_ids) or len(object_ids) != len(
                    set(object_ids)
                ):
                    raise ValueError(
                        f"paper-deck layered object ids are missing or duplicated on {expected.id}"
                    )

            expected_texts = [expected.title, *expected.key_points]
            if visible_texts != expected_texts:
                raise ValueError(
                    f"Codex visible text mismatch on {expected.id}: "
                    f"expected {expected_texts!r}, got {visible_texts!r}"
                )
            allowed_placeholder_texts = {
                cls._visual_placeholder_label(expected, content_ref)
                for content_ref in cls._visual_placeholder_reference_map(expected)
            }
            maximum_placeholders = 0 if index == 0 else cls.PAPER_DECK_MAX_VISUAL_PLACEHOLDERS
            if len(placeholder_texts) > maximum_placeholders or any(
                text not in allowed_placeholder_texts for text in placeholder_texts
            ):
                raise ValueError(
                    f"Codex visual placeholder text is not authorized on {expected.id}"
                )
            if not has_visual:
                raise ValueError(f"Codex design contains no visual structure on {expected.id}")

            foreground_elements = [
                element for element in elements if element not in background_images
            ]
            if require_editable_layers_per_slide:
                for element in foreground_elements:
                    if element.contract_role == "visual_module":
                        if element.type not in {"shape", "line"} or element.text:
                            raise ValueError(
                                f"paper-deck prepared an invalid module on {expected.id}"
                            )
                        continue
                    if element.contract_role == "visual_asset":
                        if element.type != "image" or element.text:
                            raise ValueError(
                                f"paper-deck prepared an invalid illustration on {expected.id}"
                            )
                        continue
                    if element.type != "text":
                        raise ValueError(
                            f"paper-deck prepared an unauthorized object for {expected.id}"
                        )
                    if element.contract_role == "visual_placeholder":
                        if (
                            element.style.fill is None
                            or element.style.line_color is None
                            or element.style.line_width <= 0
                        ):
                            raise ValueError(
                                "paper-deck visual placeholder must keep its editable frame "
                                f"on {expected.id}"
                            )
                        ratio = cls._contrast_ratio(
                            element.style.color,
                            element.style.fill,
                        )
                        if ratio + 1e-9 < 4.5:
                            raise ValueError(
                                "paper-deck visual placeholder contrast is too low on "
                                f"{expected.id}: {ratio:.2f}:1"
                            )
                    else:
                        if element.contract_role != "plan_copy":
                            raise ValueError(
                                f"paper-deck prepared unauthorized copy on {expected.id}"
                            )
                        if (
                            element.style.fill is not None
                            or element.style.line_color is not None
                            or element.style.line_width != 0
                        ):
                            raise ValueError(
                                "paper-deck exact Plan text must remain transparent and "
                                f"borderless on {expected.id}"
                            )
                    if element.style.opacity != 100:
                        raise ValueError(
                            f"paper-deck foreground text must be opaque on {expected.id}"
                        )
                    if not cls._is_paper_deck_text_within_safe_canvas(element):
                        raise ValueError(
                            f"paper-deck foreground text leaves the safe canvas on {expected.id}"
                        )
            else:
                cls._validate_text_legibility(
                    foreground_elements,
                    expected_background,
                    expected.id,
                )
                PresentationPlanGenerator._validate_scene_safe_zones(
                    foreground_elements,
                    expected.title,
                    allow_centered_title=index == 0,
                )
            if PresentationPlanGenerator._has_unsafe_scene_collisions(
                [
                    element
                    for element in foreground_elements
                    if element.contract_role != "visual_module"
                ],
                expected.title,
            ):
                raise ValueError(f"Codex design contains overlapping content on {expected.id}")
            if require_editable_layers_per_slide:
                cls._validate_distributed_module_spacing(
                    foreground_elements,
                    slide=expected,
                )

            designed_slides.append(
                expected.model_copy(
                    update={
                        "layout": "freeform",
                        "layout_id": (
                            "paper_deck_editable_layers"
                            if require_editable_layers_per_slide
                            else "codex_structured_design"
                        ),
                        "background": expected_background,
                        "elements": elements,
                    }
                )
            )
        return plan.model_copy(update={"slides": designed_slides})

    @staticmethod
    def _is_full_bleed_background_image(element: SlideElement) -> bool:
        tolerance = 1e-9
        return (
            element.type == "image"
            and element.z == 0
            and abs(element.x) <= tolerance
            and abs(element.y) <= tolerance
            and abs(element.w - 1) <= tolerance
            and abs(element.h - 1) <= tolerance
        )

    @classmethod
    def _parse_design_element(
        cls,
        raw_element: Any,
        *,
        workspace: Path | None = None,
        asset_output_dir: Path | None = None,
        slide_id: str = "slide",
        image_number: int = 1,
        allow_images: bool = False,
    ) -> SlideElement:
        if not isinstance(raw_element, dict):
            raise ValueError("Codex design element is not an object")
        element_type = raw_element.get("type")
        if element_type not in {"text", "shape", "line", "image"}:
            raise ValueError(f"Unsupported Codex design element type: {element_type}")
        raw_image_path = raw_element.get("image_path", "")
        if element_type == "image":
            if not allow_images:
                raise ValueError("Codex image elements are disabled for structured fallback mode")
            if workspace is None or asset_output_dir is None:
                raise ValueError("Codex image element has no controlled asset workspace")
            image_path = cls._persist_generated_image(
                raw_image_path,
                workspace=workspace,
                asset_output_dir=asset_output_dir,
                slide_id=slide_id,
                image_number=image_number,
                fallback_generated_path=(
                    workspace / ".codex_image_outputs" / f"{image_number:02d}.png"
                ),
            )
        else:
            if raw_image_path not in ("", None):
                raise ValueError("Non-image Codex element contains an image path")
            image_path = None
        style = raw_element.get("style")
        if not isinstance(style, dict):
            raise ValueError("Codex design element has no style object")
        color = style.get("color")
        if not isinstance(color, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            raise ValueError(f"Invalid Codex design color: color={color!r}")
        for color_key in ("fill", "line_color"):
            optional_color = style.get(color_key)
            if optional_color is None:
                continue
            if not isinstance(optional_color, str) or not re.fullmatch(
                r"[0-9A-Fa-f]{6}", optional_color
            ):
                raise ValueError(f"Invalid Codex design color: {color_key}={optional_color!r}")
        payload = {
            "type": element_type,
            "contract_role": raw_element.get("contract_role", "content"),
            "object_id": raw_element.get("object_id"),
            "semantic_ref": raw_element.get("semantic_ref"),
            "x": raw_element.get("x"),
            "y": raw_element.get("y"),
            "w": raw_element.get("w"),
            "h": raw_element.get("h"),
            "z": raw_element.get("z"),
            "text": raw_element.get("text"),
            "shape": raw_element.get("shape", "rectangle"),
            "image_path": str(image_path) if image_path is not None else None,
            "image_fit": raw_element.get("image_fit", "cover"),
            "style": style,
        }
        return SlideElement.model_validate(payload)

    @classmethod
    def _persist_generated_image(
        cls,
        raw_path: Any,
        *,
        workspace: Path,
        asset_output_dir: Path,
        slide_id: str,
        image_number: int,
        fallback_generated_path: Path | None = None,
        target_size: tuple[int, int] | None = None,
        prefer_fallback: bool = False,
        require_slide_background: bool = False,
        background_color: str | None = None,
    ) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Codex image element has no generated image path")
        value = raw_path.strip()
        if "\x00" in value:
            raise ValueError("Codex image path contains a null byte")
        posix_path = PurePosixPath(value.replace("\\", "/"))
        windows_path = PureWindowsPath(value)
        if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise ValueError("Codex image path must be relative to generated_visuals")
        if (
            not posix_path.parts
            or posix_path.parts[0] != "generated_visuals"
            or len(posix_path.parts) != 2
            or any(part in {"", ".", ".."} for part in posix_path.parts)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", posix_path.name)
        ):
            raise ValueError("Codex image path must stay inside generated_visuals")
        if posix_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Codex image must be PNG, JPEG, or WebP")

        workspace_root = workspace.resolve()
        visual_root_entry = workspace / "generated_visuals"
        if cls._is_link_or_reparse_point(visual_root_entry):
            raise ValueError("Codex generated_visuals directory cannot be a link or junction")
        visual_root = visual_root_entry.resolve()
        try:
            visual_root.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError("Codex generated_visuals directory escaped the workspace") from exc

        source_entry = workspace / Path(*posix_path.parts)
        if cls._is_link_or_reparse_point(source_entry):
            raise ValueError("Codex generated image cannot be a link or junction")
        source = source_entry.resolve()
        try:
            source.relative_to(visual_root)
        except ValueError as exc:
            raise ValueError("Codex image escaped generated_visuals") from exc
        safe_slide_id = re.sub(r"[^A-Za-z0-9_-]+", "-", slide_id).strip("-") or "slide"
        destination = asset_output_dir / f"{safe_slide_id}-{image_number:02d}.png"

        def persist_fallback() -> Path | None:
            if fallback_generated_path is None or not fallback_generated_path.is_file():
                return None
            fallback_root_entry = workspace / ".codex_image_outputs"
            if cls._is_link_or_reparse_point(fallback_root_entry):
                raise ValueError("Codex staged-image directory cannot be a link or junction")
            fallback_root = fallback_root_entry.resolve()
            fallback_source_entry = fallback_generated_path
            if cls._is_link_or_reparse_point(fallback_source_entry):
                raise ValueError("Codex staged image cannot be a link or junction")
            fallback_source = fallback_source_entry.resolve()
            try:
                fallback_root.relative_to(workspace_root)
                fallback_source.relative_to(fallback_root)
            except ValueError as exc:
                raise ValueError("Codex staged image escaped its controlled directory") from exc
            cls._validate_image_source_entry(fallback_source_entry, fallback_source)
            cls._normalize_generated_image(
                fallback_source,
                destination,
                target_size=target_size,
                require_slide_background=require_slide_background,
                background_color=background_color,
            )
            return destination.resolve()

        if prefer_fallback:
            persisted_fallback = persist_fallback()
            if persisted_fallback is None:
                raise ValueError("Codex trusted staged visual asset is missing")
            return persisted_fallback

        primary_error: ValueError | None = None
        if source.is_file():
            try:
                cls._validate_image_source_entry(source_entry, source)
                cls._normalize_generated_image(
                    source,
                    destination,
                    target_size=target_size,
                    require_slide_background=require_slide_background,
                    background_color=background_color,
                )
                return destination.resolve()
            except ValueError as exc:
                primary_error = exc

        persisted_fallback = persist_fallback()
        if persisted_fallback is not None:
            return persisted_fallback

        if primary_error is not None:
            raise primary_error
        raise ValueError(f"Codex generated image is missing: {posix_path.as_posix()}")

    @classmethod
    def _validate_image_source_entry(cls, entry: Path, source: Path) -> None:
        if cls._is_link_or_reparse_point(entry):
            raise ValueError("Codex generated image cannot be a link or junction")
        source_stat = source.stat()
        if source_stat.st_nlink > 1:
            raise ValueError("Codex generated image cannot be a hard link")
        file_size = source_stat.st_size
        if file_size <= 0 or file_size > cls.PAPER_CRAFT_MAX_FILE_BYTES:
            raise ValueError("Codex generated image has an unsafe file size")

    @staticmethod
    def _is_link_or_reparse_point(path: Path) -> bool:
        """Reject links and Windows reparse points on Python 3.11 and newer."""

        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction):
            try:
                if is_junction():
                    return True
            except OSError:
                return True
        if os.name != "nt":
            return False
        try:
            attributes = path.lstat().st_file_attributes
        except (AttributeError, OSError):
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        return bool(attributes & reparse_flag)

    @classmethod
    def _normalize_generated_image(
        cls,
        source: Path,
        destination: Path,
        *,
        target_size: tuple[int, int] | None = None,
        require_slide_background: bool = False,
        background_color: str | None = None,
    ) -> None:
        if background_color is not None and not re.fullmatch(r"[0-9A-Fa-f]{6}", background_color):
            raise ValueError("Codex generated-image background color is invalid")
        try:
            with Image.open(source) as candidate:
                cls._validate_image_dimensions(candidate.size)
                if require_slide_background:
                    cls._validate_slide_background_dimensions(candidate.size)
                candidate.verify()
            with Image.open(source) as candidate:
                candidate = ImageOps.exif_transpose(candidate)
                cls._validate_image_dimensions(candidate.size)
                if require_slide_background:
                    cls._validate_slide_background_dimensions(candidate.size)
                if background_color is not None:
                    background_rgb = tuple(
                        int(background_color[index : index + 2], 16) for index in (0, 2, 4)
                    )
                    canvas = Image.new(
                        "RGBA",
                        candidate.size,
                        (*background_rgb, 255),
                    )
                    normalized = Image.alpha_composite(
                        canvas,
                        candidate.convert("RGBA"),
                    ).convert("RGB")
                else:
                    normalized = candidate.convert("RGBA" if "A" in candidate.getbands() else "RGB")
                if target_size is not None:
                    cls._validate_image_dimensions(target_size)
                    normalized = ImageOps.fit(
                        normalized,
                        target_size,
                        method=Image.Resampling.LANCZOS,
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                normalized.save(destination, format="PNG", optimize=True)
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ValueError("Codex generated image is unreadable") from exc

    @classmethod
    def _validate_image_dimensions(cls, size: tuple[int, int]) -> None:
        width, height = size
        if (
            width <= 0
            or height <= 0
            or width > 4096
            or height > 4096
            or width * height > cls.PAPER_CRAFT_MAX_PIXELS
        ):
            raise ValueError("Codex generated image has unsafe dimensions")

    @classmethod
    def _validate_slide_background_dimensions(cls, size: tuple[int, int]) -> None:
        width, height = size
        minimum_width, minimum_height = cls.PAPER_CRAFT_BACKGROUND_MIN_SIZE
        minimum_aspect, maximum_aspect = cls.PAPER_CRAFT_BACKGROUND_ASPECT_RANGE
        aspect_ratio = width / height if height else 0
        if width < minimum_width or height < minimum_height:
            raise ValueError(
                "Codex Paper Deck background resolution is too low: "
                f"{width}x{height}, requires at least {minimum_width}x{minimum_height}"
            )
        if not minimum_aspect <= aspect_ratio <= maximum_aspect:
            raise ValueError(
                "Codex Paper Deck background must be a landscape image close to 16:9: "
                f"{width}x{height}"
            )

    @staticmethod
    def _canonical_skill_guidance_bytes(content: bytes) -> bytes:
        """Normalize checkout-only whitespace before hashing pinned text guidance."""

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("paper-craft skill guidance must be valid UTF-8 text") from exc
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
        return normalized.encode("utf-8")

    def _stage_paper_craft_skills(self, workspace: Path) -> list[dict[str, Any]]:
        source_root = self.paper_craft_skills_dir
        if source_root is None or not source_root.is_dir():
            raise ValueError("configured paper-craft skills directory does not exist")
        if self._is_link_or_reparse_point(source_root):
            raise ValueError("configured paper-craft skills directory cannot be a link or junction")

        manifest: list[dict[str, Any]] = []
        verified_files: list[tuple[str, Path, bytes]] = []
        target_root = workspace / ".agents" / "skills"
        for skill_name in self.PAPER_CRAFT_SKILLS:
            source_entry = source_root / skill_name
            if self._is_link_or_reparse_point(source_entry):
                raise ValueError(f"skill directory is an unsupported link: {skill_name}")
            source_skill = source_entry.resolve()
            try:
                source_skill.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"skill directory escaped its source root: {skill_name}") from exc
            skill_file = source_skill / "SKILL.md"
            if not skill_file.is_file():
                raise ValueError(f"required skill is missing: {skill_name}")
            sources = [skill_file]
            references = source_skill / "references"
            if references.is_dir():
                sources.extend(
                    path
                    for path in sorted(references.rglob("*"))
                    if path.is_file() and path.suffix.lower() in {".md", ".txt"}
                )

            digest = hashlib.sha256()
            staged_files: list[str] = []
            for source in sources:
                if self._is_link_or_reparse_point(source):
                    raise ValueError(f"skill contains an unsupported link: {skill_name}")
                try:
                    relative = source.resolve().relative_to(source_skill)
                except ValueError as exc:
                    raise ValueError(f"skill file escaped its directory: {skill_name}") from exc
                content = self._canonical_skill_guidance_bytes(source.read_bytes())
                if len(content) > 1_000_000:
                    raise ValueError(f"skill guidance file is too large: {relative}")
                relative_name = relative.as_posix()
                staged_files.append(relative_name)
                verified_files.append((skill_name, relative, content))
                digest.update(relative_name.encode("utf-8"))
                digest.update(b"\x00")
                digest.update(content)
            manifest.append(
                {
                    "name": skill_name,
                    "sha256": digest.hexdigest(),
                    "files": staged_files,
                }
            )
            expected_digest = self.PAPER_CRAFT_EXPECTED_SHA256[skill_name]
            if manifest[-1]["sha256"] != expected_digest:
                raise ValueError(
                    f"pinned skill digest mismatch for {skill_name}; refusing unverified guidance"
                )

        try:
            for skill_name, relative, content in verified_files:
                target = target_root / skill_name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        except OSError:
            shutil.rmtree(target_root, ignore_errors=True)
            raise
        return manifest

    def _embedded_paper_craft_guidance(
        self,
        workspace: Path,
        manifest: list[dict[str, Any]],
    ) -> str:
        skill_root = workspace / ".agents" / "skills"
        blocks: list[str] = []
        total_bytes = 0
        for skill in manifest:
            skill_name = str(skill.get("name", ""))
            if skill_name not in self.PAPER_CRAFT_SKILLS:
                raise ValueError("paper-craft manifest contains an unexpected skill")
            files = skill.get("files")
            if not isinstance(files, list):
                raise ValueError(f"paper-craft manifest has no files for {skill_name}")
            staged_skill_root = (skill_root / skill_name).resolve()
            for relative_name in files:
                if not isinstance(relative_name, str):
                    raise ValueError("paper-craft manifest contains an invalid file name")
                relative = PurePosixPath(relative_name)
                if relative.is_absolute() or any(
                    part in {"", ".", ".."} for part in relative.parts
                ):
                    raise ValueError("paper-craft guidance path escaped its skill directory")
                if relative.as_posix() not in self.PAPER_CRAFT_RUNTIME_GUIDANCE_FILES.get(
                    skill_name,
                    set(),
                ):
                    continue
                source_entry = skill_root / skill_name / Path(*relative.parts)
                if self._is_link_or_reparse_point(source_entry):
                    raise ValueError("paper-craft guidance cannot be a link or junction")
                source = source_entry.resolve()
                try:
                    source.relative_to(staged_skill_root)
                except ValueError as exc:
                    raise ValueError("paper-craft guidance escaped its skill directory") from exc
                content = source.read_bytes()
                total_bytes += len(content)
                if total_bytes > self.PAPER_CRAFT_MAX_GUIDANCE_BYTES:
                    raise ValueError("paper-craft embedded guidance is too large")
                blocks.append(
                    f"\n--- BEGIN {skill_name}/{relative.as_posix()} ---\n"
                    + content.decode("utf-8")
                    + f"\n--- END {skill_name}/{relative.as_posix()} ---\n"
                )
        if not blocks:
            raise ValueError("paper-craft embedded guidance is empty")
        return "".join(blocks)

    def _stage_codex_generated_images(
        self,
        *,
        workspace: Path,
        environment: dict[str, str],
        stderr: str,
        reported_image_count: int = 1,
        reported_image_paths: tuple[str, ...] | None = None,
    ) -> tuple[int, str | None]:
        if reported_image_paths is not None:
            reported_image_count = len(reported_image_paths)
        if reported_image_count == 0:
            return 0, None
        if not 1 <= reported_image_count <= self.PAPER_DECK_MAX_VISUAL_ASSETS:
            return 0, "Codex image recovery exceeds the per-slide asset limit"

        session_id = self._extract_codex_session_id(stderr)
        if session_id is None:
            return 0, "Codex image session id was not reported"
        generated_root = Path(environment["CODEX_HOME"]) / "generated_images"
        session_entry = generated_root / session_id
        if self._is_link_or_reparse_point(generated_root):
            return 0, "Codex generated-image root cannot be a link or junction"
        try:
            generated_root_resolved = generated_root.resolve()
            session_root = session_entry.resolve()
            session_root.relative_to(generated_root_resolved)
        except (OSError, ValueError):
            return 0, "Codex generated-image session escaped its controlled root"
        if not session_entry.is_dir() or self._is_link_or_reparse_point(session_entry):
            return 0, "Codex generated-image session directory is unavailable"

        candidates = sorted(
            (
                path
                for path in session_entry.iterdir()
                if path.is_file()
                and not self._is_link_or_reparse_point(path)
                and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        if not candidates:
            return 0, "Codex image generation produced no local raster asset"

        selected_sources: list[Path | None] = [None] * reported_image_count
        used_sources: set[Path] = set()
        if reported_image_paths is not None:
            for index, reported_path in enumerate(reported_image_paths):
                reported_name = PurePosixPath(reported_path.replace("\\", "/")).name
                exact_matches = [
                    candidate
                    for candidate in candidates
                    if candidate.name == reported_name and candidate not in used_sources
                ]
                if len(exact_matches) == 1:
                    selected_sources[index] = exact_matches[0]
                    used_sources.add(exact_matches[0])

        unmatched_indices = [
            index for index, source in enumerate(selected_sources) if source is None
        ]
        remaining_sources = [
            candidate for candidate in candidates if candidate not in used_sources
        ]
        if len(remaining_sources) < len(unmatched_indices):
            return 0, "Codex image recovery produced fewer sources than the returned manifest"
        # The image tool stores every regeneration in the trusted session directory. When the
        # manifest uses a logical safe name rather than the generated UUID, the newest N unused
        # sources are the final audited outputs; earlier files are rejected drafts.
        fallback_sources = remaining_sources[-len(unmatched_indices) :] if unmatched_indices else []
        for index, source in zip(unmatched_indices, fallback_sources, strict=True):
            selected_sources[index] = source

        staged_root = workspace / ".codex_image_outputs"
        if os.path.lexists(staged_root):
            return 0, "Codex controlled image-recovery directory was pre-created"
        try:
            staged_root.mkdir()
            staged_root.resolve().relative_to(workspace.resolve())
            for index, source in enumerate(selected_sources, start=1):
                if source is None:
                    raise ValueError("Codex generated image mapping is incomplete")
                source_root = source.resolve()
                source_root.relative_to(session_root)
                self._validate_image_source_entry(source, source_root)
                self._normalize_generated_image(
                    source_root,
                    staged_root / f"{index:02d}.png",
                )
        except (OSError, ValueError):
            return 0, "Codex generated raster assets were unreadable"
        return len(selected_sources), None

    @staticmethod
    def _extract_codex_session_id(stderr: str) -> str | None:
        trusted_header = re.split(r"(?m)^\s*user\s*$", stderr, maxsplit=1)[0]
        match = re.search(
            r"(?mi)^\s*session id:\s*"
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*$",
            trusted_header,
        )
        return match.group(1).lower() if match else None

    @staticmethod
    def _clear_paper_craft_assets(output_dir: Path) -> None:
        asset_dir = output_dir / "codex_assets"
        if not asset_dir.is_dir():
            return
        for path in asset_dir.glob("*.png"):
            if path.is_file():
                path.unlink()
        try:
            asset_dir.rmdir()
        except OSError:
            pass

    @staticmethod
    def _is_paper_craft_capability_failure(error: str) -> bool:
        normalized = error.casefold()
        return any(
            marker in normalized
            for marker in (
                "built-in image generation",
                "image generation is unavailable",
                "image-generation capability",
                "does not support image generation",
                "image generation tool is unavailable",
            )
        )

    @staticmethod
    def _relative_luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    @classmethod
    def _contrast_ratio(cls, foreground: str, background: str) -> float:
        lighter, darker = sorted(
            (
                cls._relative_luminance(foreground),
                cls._relative_luminance(background),
            ),
            reverse=True,
        )
        return (lighter + 0.05) / (darker + 0.05)

    @classmethod
    def _validate_text_legibility(
        cls,
        elements: list[SlideElement],
        slide_background: str,
        slide_id: str,
    ) -> None:
        """Reject technically valid scenes that would render unreadable text."""

        for element in elements:
            if element.type != "text":
                continue
            style = element.style
            if style.opacity != 100:
                raise ValueError(
                    f"Codex text must be fully opaque on {slide_id}: {(element.text or '')[:80]}"
                )
            background = style.fill or slide_background
            ratio = cls._contrast_ratio(style.color, background)
            large_text = style.font_size >= 24 or (style.bold and style.font_size >= 18.66)
            minimum = 3.0 if large_text else 4.5
            if ratio + 1e-9 < minimum:
                raise ValueError(
                    f"Codex text contrast is too low on {slide_id}: "
                    f"{ratio:.2f}:1, requires {minimum:.1f}:1 for "
                    f"{(element.text or '')[:80]}"
                )

    def _execute_codex(
        self,
        *,
        workspace: Path,
        output_dir: Path,
        prompt: str,
        attempt: int,
        sandbox_mode: str = "read-only",
        run_mode: str = "structured-vector",
        ignore_user_config: bool = True,
    ) -> dict[str, Any]:
        if sandbox_mode not in {"read-only", "workspace-write"}:
            raise ValueError(f"Unsupported Codex sandbox mode: {sandbox_mode}")
        codex_bin = self._resolve_codex_bin()
        environment = self._codex_environment()
        if not self.api_key:
            self._ensure_authenticated(codex_bin, environment=environment)
        result_path = workspace / f"codex_result_{attempt}.json"
        schema_path = workspace / "result_schema.json"
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--sandbox",
            sandbox_mode,
            "--skip-git-repo-check",
            "--ignore-rules",
            "--config",
            'shell_environment_policy.inherit="core"',
            "--config",
            "shell_environment_policy.ignore_default_excludes=false",
            "--config",
            "sandbox_workspace_write.network_access=false",
            "--config",
            'web_search="disabled"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
            "--color",
            "never",
            "-C",
            str(workspace),
        ]
        if ignore_user_config:
            command.insert(command.index("--ignore-rules"), "--ignore-user-config")
        else:
            schema_index = command.index("--output-schema")
            controlled_user_config = [
                "--config",
                'model_reasoning_effort="low"',
            ]
            if os.name == "nt":
                controlled_user_config.extend(["--config", 'windows.sandbox="unelevated"'])
            command[schema_index:schema_index] = controlled_user_config
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")

        if self.api_key:
            environment["CODEX_API_KEY"] = self.api_key

        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        started_at = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        try:
            stdout, stderr = process.communicate(prompt, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()
            self._write_run_log(
                output_dir,
                attempt,
                returncode=process.returncode,
                duration_seconds=time.monotonic() - started_at,
                stdout=stdout,
                stderr=stderr,
                result=None,
                run_mode=run_mode,
                sandbox_mode=sandbox_mode,
                ignore_user_config=ignore_user_config,
            )
            raise CodexGenerationError(
                f"Codex generation timed out after {self.timeout_seconds:g} seconds"
            ) from exc

        staged_image_count = 0
        staged_image_error: str | None = None
        result = self._read_result(result_path)
        if run_mode in {"paper-craft-hybrid", "paper-deck-slide-hybrid"}:
            reported_slides = result.get("slides")
            reported_image_paths = (
                tuple(
                    str(element.get("image_path", ""))
                    for slide in reported_slides
                    if isinstance(slide, dict)
                    for element in (
                        slide.get("visual_assets", [])
                        if isinstance(slide.get("visual_assets"), list)
                        else slide.get("elements", [])
                    )
                    if isinstance(element, dict)
                    and (element.get("type") == "image" or "image_path" in element)
                )
                if isinstance(reported_slides, list)
                else ()
            )
            staged_image_count, staged_image_error = self._stage_codex_generated_images(
                workspace=workspace,
                environment=environment,
                stderr=stderr,
                reported_image_paths=reported_image_paths,
            )
        result["_metaclass_staged_image_count"] = staged_image_count
        if staged_image_error:
            result["_metaclass_image_stage_error"] = staged_image_error
        self._write_run_log(
            output_dir,
            attempt,
            returncode=process.returncode,
            duration_seconds=time.monotonic() - started_at,
            stdout=stdout,
            stderr=stderr,
            result=result,
            run_mode=run_mode,
            sandbox_mode=sandbox_mode,
            ignore_user_config=ignore_user_config,
        )
        if process.returncode != 0:
            detail = self._tail(stderr or stdout, 1500)
            raise CodexGenerationError(
                f"Codex process exited with code {process.returncode}: {detail}"
            )
        return result

    def _resolve_codex_bin(self) -> str:
        if self.codex_bin:
            path = Path(self.codex_bin)
            if not path.exists():
                raise CodexGenerationError(f"Configured Codex binary was not found: {path}")
            return str(path)
        try:
            from codex_cli_bin import bundled_codex_path
        except ImportError as exc:
            raise CodexGenerationError(
                "openai-codex is not installed; the pinned Codex runtime is unavailable"
            ) from exc
        return str(bundled_codex_path())

    @staticmethod
    def _codex_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        if not environment.get("CODEX_HOME", "").strip():
            environment["CODEX_HOME"] = str(Path.home() / ".codex")
        return environment

    @staticmethod
    def _ensure_authenticated(
        codex_bin: str,
        *,
        environment: dict[str, str] | None = None,
    ) -> None:
        try:
            status = subprocess.run(
                [codex_bin, "login", "status"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodexGenerationError("Could not verify Codex authentication") from exc
        if status.returncode != 0:
            raise CodexGenerationError(
                "Codex is not authenticated. Configure CODEX_API_KEY or run "
                "`codex login` for the backend system account"
            )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        if process.poll() is None:
            process.kill()

    @staticmethod
    def _read_result(result_path: Path) -> dict[str, Any]:
        if not result_path.exists():
            return {"status": "unknown", "summary": "Codex returned no final result file"}
        raw = result_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return {"status": "unknown", "summary": raw[:1000]}
        return result if isinstance(result, dict) else {"status": "unknown", "summary": raw[:1000]}

    @staticmethod
    def _write_run_log(
        output_dir: Path,
        attempt: int,
        *,
        returncode: int | None,
        duration_seconds: float,
        stdout: str,
        stderr: str,
        result: dict[str, Any] | None,
        run_mode: str = "structured-vector",
        sandbox_mode: str = "read-only",
        ignore_user_config: bool = True,
    ) -> None:
        (output_dir / f"codex_attempt_{attempt}.json").write_text(
            json.dumps(
                {
                    "attempt": attempt,
                    "run_mode": run_mode,
                    "sandbox_mode": sandbox_mode,
                    "ignore_user_config": ignore_user_config,
                    "returncode": returncode,
                    "duration_seconds": round(duration_seconds, 3),
                    "stdout_tail": CodexPPTProvider._tail(stdout, 12000),
                    "stderr_tail": CodexPPTProvider._tail(stderr, 12000),
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _tail(value: str, limit: int) -> str:
        return value[-limit:] if len(value) > limit else value

    @staticmethod
    def _artifact_id_for_job(job_id: str) -> str:
        safe_job_id = re.sub(r"[^A-Za-z0-9_-]+", "-", job_id).strip("-")
        return f"ppt_artifact_{safe_job_id[-32:]}"

    @staticmethod
    def _public_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(result.get("status", "unknown"))[:100],
            "deck_path": str(result.get("deck_path", ""))[:500],
            "summary": str(result.get("summary", ""))[:1000],
            "slide_design_count": (
                len(result["slides"]) if isinstance(result.get("slides"), list) else 0
            ),
            "staged_image_count": int(result.get("_metaclass_staged_image_count", 0)),
        }

    @staticmethod
    def _write_output_schema(destination: Path, *, slide_count: int) -> None:
        color = {"type": "string", "pattern": "^[0-9A-Fa-f]{6}$"}
        destination.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["completed", "blocked"]},
                        "summary": {"type": "string"},
                        "slides": {
                            "type": "array",
                            "minItems": 0,
                            "maxItems": slide_count,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "slide_id": {"type": "string"},
                                    "background": color,
                                    "elements": {
                                        "type": "array",
                                        "minItems": 0,
                                        "maxItems": 40,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "type": "string",
                                                    "enum": ["text", "shape", "line", "image"],
                                                },
                                                "x": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                    "maximum": 1,
                                                },
                                                "y": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                    "maximum": 1,
                                                },
                                                "w": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                    "maximum": 1,
                                                },
                                                "h": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                    "maximum": 1,
                                                },
                                                "z": {
                                                    "type": "integer",
                                                    "minimum": 0,
                                                    "maximum": 40,
                                                },
                                                "text": {"type": "string"},
                                                "image_path": {
                                                    "type": "string",
                                                    "maxLength": 240,
                                                },
                                                "shape": {
                                                    "type": "string",
                                                    "enum": [
                                                        "rectangle",
                                                        "rounded_rectangle",
                                                        "oval",
                                                        "chevron",
                                                    ],
                                                },
                                                "style": {
                                                    "type": "object",
                                                    "properties": {
                                                        "font_size": {
                                                            "type": "number",
                                                            "minimum": 18,
                                                            "maximum": 60,
                                                        },
                                                        "bold": {"type": "boolean"},
                                                        "color": color,
                                                        "fill": color,
                                                        "line_color": color,
                                                        "line_width": {
                                                            "type": "number",
                                                            "minimum": 0,
                                                            "maximum": 8,
                                                        },
                                                        "align": {
                                                            "type": "string",
                                                            "enum": ["left", "center", "right"],
                                                        },
                                                        "valign": {
                                                            "type": "string",
                                                            "enum": ["top", "middle", "bottom"],
                                                        },
                                                        "opacity": {
                                                            "type": "integer",
                                                            "minimum": 0,
                                                            "maximum": 100,
                                                        },
                                                    },
                                                    "required": [
                                                        "font_size",
                                                        "bold",
                                                        "color",
                                                        "fill",
                                                        "line_color",
                                                        "line_width",
                                                        "align",
                                                        "valign",
                                                        "opacity",
                                                    ],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "required": [
                                                "type",
                                                "x",
                                                "y",
                                                "w",
                                                "h",
                                                "z",
                                                "text",
                                                "image_path",
                                                "shape",
                                                "style",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["slide_id", "background", "elements"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["status", "summary", "slides"],
                    "additionalProperties": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _is_codex_usage_limit(error: str) -> bool:
        normalized = error.casefold()
        return any(
            marker in normalized
            for marker in (
                "you've hit your usage limit",
                "usage limit",
                "purchase more credits",
                "insufficient_quota",
                "rate_limit_exceeded",
            )
        )

    @staticmethod
    def _write_paper_deck_output_schema(
        destination: Path,
        *,
        text_block_count: int,
        style_signature: str,
        visual_placeholder_refs: tuple[str, ...] = ("suggested_visual",),
        min_visual_assets: int = 0,
        max_visual_assets: int = 1,
        min_visual_placeholders: int = 0,
        max_visual_placeholders: int = 1,
        require_dominant_visual_column: bool = False,
    ) -> None:
        """Write the Skill-owned contract for independent PPT objects and exact-copy slots."""

        if not visual_placeholder_refs:
            raise ValueError("paper-deck placeholder schema requires at least one Plan reference")
        if not 0 <= min_visual_assets <= max_visual_assets <= 2:
            raise ValueError("paper-deck visual-asset schema supports zero to two local images")
        if not 0 <= min_visual_placeholders <= max_visual_placeholders <= 1:
            raise ValueError("paper-deck placeholder schema supports at most one asset gap")
        if max_visual_assets and max_visual_placeholders:
            raise ValueError("paper-deck schema cannot permit generated assets and a placeholder")

        color = {"type": "string", "pattern": "^[0-9A-Fa-f]{6}$"}
        module_refs = [
            "title",
            *[f"key_points.{index}" for index in range(max(0, text_block_count - 1))],
            *visual_placeholder_refs,
            "decoration",
        ]
        module_common_properties = {
            "object_id": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$",
            },
            "content_ref": {"type": "string", "enum": module_refs},
            "x": {"type": "number", "minimum": 0.045, "maximum": 0.955},
            "y": {"type": "number", "minimum": 0.04, "maximum": 0.96},
            "w": {"type": "number", "minimum": 0, "maximum": 0.88},
            "h": {"type": "number", "minimum": 0, "maximum": 0.84},
            "z": {"type": "integer", "minimum": 1, "maximum": 19},
        }
        module_required = [
            "object_id",
            "content_ref",
            "type",
            "shape",
            "x",
            "y",
            "w",
            "h",
            "z",
            "style_token",
        ]
        visual_module = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        **module_common_properties,
                        "type": {"type": "string", "enum": ["shape"]},
                        "shape": {
                            "type": "string",
                            "enum": [
                                "rectangle",
                                "rounded_rectangle",
                                "oval",
                                "chevron",
                            ],
                        },
                        "style_token": {
                            "type": "string",
                            "enum": [
                                "paper_card",
                                "soft_panel",
                                "outline_panel",
                                "accent_band",
                                "secondary_band",
                                "accent_node",
                                "muted_node",
                                "warning_mark",
                                "tint_wash",
                                "hairline_frame",
                                "figure_frame",
                                "ink_node",
                            ],
                        },
                    },
                    "required": module_required,
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        **module_common_properties,
                        "type": {"type": "string", "enum": ["line"]},
                        "shape": {"type": "string", "enum": ["rectangle"]},
                        "style_token": {
                            "type": "string",
                            "enum": [
                                "ink_rule",
                                "accent_rule",
                                "muted_rule",
                                "caption_rule",
                            ],
                        },
                    },
                    "required": module_required,
                    "additionalProperties": False,
                },
            ]
        }
        visual_min_width = (
            CodexPPTProvider.PAPER_DECK_LOCAL_VISUAL_MIN_WIDTH
            if require_dominant_visual_column
            else 0.16
        )
        visual_max_width = (
            CodexPPTProvider.PAPER_DECK_VISUAL_COLUMN_MAX_WIDTH
            if require_dominant_visual_column
            else CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MAX_WIDTH
        )
        visual_min_height = (
            CodexPPTProvider.PAPER_DECK_LOCAL_VISUAL_MIN_HEIGHT
            if require_dominant_visual_column
            else 0.12
        )
        visual_max_height = (
            CodexPPTProvider.PAPER_DECK_VISUAL_COLUMN_MAX_HEIGHT
            if require_dominant_visual_column
            else CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MAX_HEIGHT
        )
        visual_asset = {
            "type": "object",
            "properties": {
                "object_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$",
                },
                "content_ref": {
                    "type": "string",
                    "enum": list(visual_placeholder_refs),
                },
                "x": {"type": "number", "minimum": 0.05, "maximum": 0.78},
                "y": {"type": "number", "minimum": 0.08, "maximum": 0.78},
                "w": {
                    "type": "number",
                    "minimum": visual_min_width,
                    "maximum": visual_max_width,
                },
                "h": {
                    "type": "number",
                    "minimum": visual_min_height,
                    "maximum": visual_max_height,
                },
                "z": {"type": "integer", "minimum": 1, "maximum": 19},
                "image_path": {
                    "type": "string",
                    "pattern": "^generated_visuals/[A-Za-z0-9._-]+\\.(png|jpg|jpeg|webp)$",
                    "maxLength": 240,
                },
                "image_fit": {"type": "string", "enum": ["contain", "cover"]},
            },
            "required": [
                "object_id",
                "content_ref",
                "x",
                "y",
                "w",
                "h",
                "z",
                "image_path",
                "image_fit",
            ],
            "additionalProperties": False,
        }
        text_block = {
            "type": "object",
            "properties": {
                "content_ref": {
                    "type": "string",
                    "pattern": "^(title|key_points\\.[0-9]+)$",
                },
                "x": {"type": "number", "minimum": 0.045, "maximum": 0.955},
                "y": {"type": "number", "minimum": 0.04, "maximum": 0.96},
                "w": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.91},
                "h": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.92},
                "font_size": {"type": "number", "minimum": 14, "maximum": 60},
                "min_font_size": {"type": "number", "minimum": 14, "maximum": 60},
                "font_role": {
                    "type": "string",
                    "enum": ["sans", "serif", "handwritten", "display", "mono"],
                },
                "bold": {"type": "boolean"},
                "color": color,
                "align": {"type": "string", "enum": ["left", "center", "right"]},
                "valign": {"type": "string", "enum": ["top", "middle", "bottom"]},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": [
                "content_ref",
                "x",
                "y",
                "w",
                "h",
                "font_size",
                "min_font_size",
                "font_role",
                "bold",
                "color",
                "align",
                "valign",
                "max_lines",
            ],
            "additionalProperties": False,
        }
        visual_placeholder = {
            "type": "object",
            "properties": {
                "content_ref": {
                    "type": "string",
                    "enum": list(visual_placeholder_refs),
                },
                "x": {"type": "number", "minimum": 0.05, "maximum": 0.73},
                "y": {"type": "number", "minimum": 0.16, "maximum": 0.76},
                "w": {
                    "type": "number",
                    "minimum": (
                        CodexPPTProvider.PAPER_DECK_VISUAL_COLUMN_MIN_WIDTH
                        if require_dominant_visual_column
                        else CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MIN_WIDTH
                    ),
                    "maximum": visual_max_width,
                },
                "h": {
                    "type": "number",
                    "minimum": (
                        CodexPPTProvider.PAPER_DECK_VISUAL_COLUMN_MIN_HEIGHT
                        if require_dominant_visual_column
                        else CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MIN_HEIGHT
                    ),
                    "maximum": (
                        visual_max_height
                        if require_dominant_visual_column
                        else CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MAX_HEIGHT
                    ),
                },
            },
            "required": ["content_ref", "x", "y", "w", "h"],
            "additionalProperties": False,
        }
        destination.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["completed", "blocked"]},
                        "summary": {"type": "string"},
                        "slides": {
                            "type": "array",
                            "minItems": 0,
                            "maxItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "slide_id": {"type": "string"},
                                    "background": color,
                                    "manifest_version": {
                                        "type": "integer",
                                        "minimum": CodexPPTProvider.PAPER_DECK_MANIFEST_VERSION,
                                        "maximum": CodexPPTProvider.PAPER_DECK_MANIFEST_VERSION,
                                    },
                                    "layout_mode": {
                                        "type": "string",
                                        "enum": ["editable-layered-objects"],
                                    },
                                    "style_signature": {
                                        "type": "string",
                                        "enum": [style_signature],
                                    },
                                    "raster_audit": {
                                        "type": "string",
                                        "enum": ["inspected-local-assets-text-free"],
                                    },
                                    "modules": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": CodexPPTProvider.PAPER_DECK_MAX_VISUAL_MODULES,
                                        "items": visual_module,
                                    },
                                    "visual_assets": {
                                        "type": "array",
                                        "minItems": min_visual_assets,
                                        "maxItems": max_visual_assets,
                                        "items": visual_asset,
                                    },
                                    "text_blocks": {
                                        "type": "array",
                                        "minItems": text_block_count,
                                        "maxItems": text_block_count,
                                        "items": text_block,
                                    },
                                    "visual_placeholders": {
                                        "type": "array",
                                        "minItems": min_visual_placeholders,
                                        "maxItems": max_visual_placeholders,
                                        "items": visual_placeholder,
                                    },
                                },
                                "required": [
                                    "slide_id",
                                    "background",
                                    "manifest_version",
                                    "layout_mode",
                                    "style_signature",
                                    "raster_audit",
                                    "modules",
                                    "visual_assets",
                                    "text_blocks",
                                    "visual_placeholders",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["status", "summary", "slides"],
                    "additionalProperties": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _paper_craft_prompt(
        plan: PresentationPlan,
        plan_hash: str,
        theme: PresentationTheme,
        *,
        max_images: int,
        skill_guidance: str = "",
        slide_index: int = 0,
        deck_slide_count: int | None = None,
        slide_plan_hash: str | None = None,
        deck_context: str = "",
        visual_requirement: dict[str, Any] | None = None,
        composition: dict[str, str] | None = None,
    ) -> str:
        slide_count = deck_slide_count or len(plan.slides)
        slide_number = slide_index + 1
        expected_background = theme.palette.board if slide_index == 0 else theme.palette.paper
        style_preset = CodexPPTProvider._paper_deck_style_preset(theme)
        style_signature = (
            f"{style_preset}:{theme.id}:{CodexPPTProvider.PAPER_DECK_STYLE_SIGNATURE_VERSION}"
        )
        requirement = visual_requirement or CodexPPTProvider._paper_deck_visual_requirement(
            plan.slides[0]
        )
        min_assets = int(requirement["min_assets"])
        max_assets = int(requirement["max_assets"])
        min_placeholders = int(requirement["min_placeholders"])
        max_placeholders = int(requirement["max_placeholders"])
        planned_composition = composition or CodexPPTProvider._paper_deck_composition_plan(plan)[0]
        visual_instruction = (
            f"This slide is classified as {requirement['mode']}: {requirement['reason']}. "
            f"Return {min_assets}-{max_assets} visual_assets and "
            f"{min_placeholders}-{max_placeholders} visual_placeholders. These counts are a hard "
            "backend contract."
        )
        composition_instruction = (
            "PAPER_DECK_COMPOSITION_DIRECTIVE:\n"
            f"- layout_id={planned_composition['layout_id']}\n"
            f"- family={planned_composition['family']}\n"
            f"- spatial direction: {planned_composition['direction']}\n"
            "This directive controls geometry only. Preserve every exact Plan string. Do not replace "
            "it with a generic left/right split unless the assigned layout_id is split_left or "
            "split_right. Keep the same journal-minimal identity while changing the page silhouette."
        )
        return (
            "Use $paper-deck as the required presentation visual-director skill. Apply the "
            "embedded pinned Paper Deck workflow and $paper-comic's compact verified "
            "paper-figure guidance "
            "when an explanatory figure genuinely helps. The relevant verified guidance "
            "is embedded below, so apply it "
            "directly and do not try to read the staged skill files with shell commands. The "
            "user has already authorized this generation, so do not pause for confirmation. "
            "This is MetaClass's editable-layer Paper Deck mode: the supplied PresentationPlan is "
            "the approved outline, so skip outline approval and deck merging. This isolated "
            f"session creates slide {slide_number} of {slide_count}. Use the immutable deck-wide "
            f"style preset {style_preset} on every slide, regardless of the selected color theme. "
            "The selected theme supplies palette tokens only; it must not change composition, "
            "typography mood, material, illustration language, or decorative treatment. "
            f"Style anchor: {CodexPPTProvider.PAPER_DECK_STYLE_PROMPT}.\n"
            + visual_instruction
            + "\n"
            + composition_instruction
            + "\n"
            "The user explicitly requires independently movable PowerPoint objects, so adapt the "
            "skill's art direction to a layered DrawingML manifest instead of its usual full-page "
            "raster-first delivery. Art-direct the entire 16:9 composition, but return every card, "
            "frame, node, band, divider, underline, and connector as a separate modules entry. "
            "Never return one full-slide image, one full-slide shape, a group, a mega-card, a "
            "shared panel, a nested container, a collage, or a central pile-up. Each module needs "
            "a stable object_id and exactly one immutable content_ref. Use style_token values from "
            "the schema so the backend, not the model, applies theme-safe colors. Vary the page "
            "silhouette by slide role and avoid a repetitive UI card grid. Use a distributed "
            "editorial composition with clear whitespace between unrelated objects. Thin separate "
            "line modules may connect islands only when a real semantic relationship requires it.\n"
            "Return a matching text_blocks manifest. Each block is the writing area associated "
            "with an editable module or quiet page region, and specifies content_ref, "
            "geometry, font role, preferred/minimum size, weight, color, alignment, and max lines. "
            "Size every block using the exact immutable string length. The first ref is title; the "
            "remaining refs are key_points.0, key_points.1, and so on in order. Never return model-"
            f"written slide copy. Use style_signature={style_signature} exactly.\n"
            "When suggested_visual requires an unavailable real or source-specific asset such as "
            "a photograph, screenshot, map, remote-sensing image, experimental chart, original "
            "paper figure, or document page, do not hallucinate it. On non-cover slides, reserve "
            "one substantial independent visual region that follows the assigned composition and "
            "return one visual_placeholders "
            "manifest entry "
            "that references suggested_visual or the most specific visual_payload.N. Do not write "
            "placeholder copy or return its frame as a module; MetaClass will add one "
            "editable academic frame with deterministic text. Do not return a placeholder for an "
            "explanatory diagram that can be truthfully constructed from visual_payload. Cover "
            "slides must return visual_placeholders=[]. On content slides, visual_assets and "
            "visual_placeholders are mutually exclusive. If either is returned, it may occupy a "
            "central figure stage, a wide evidence strip, a top/bottom band, an inset, or an "
            "asymmetric side field as required by the assigned composition. It does not have to be "
            "a vertical left/right column. Keep at least "
            f"{CodexPPTProvider.PAPER_DECK_MIN_MODULE_GAP:.3f} clearance from unrelated Plan copy "
            "and modules. When there are two pictures, keep visible separation and preserve each as "
            "an independent Picture object. Arrange exact Plan copy around the visual without "
            "shortening or rewriting it.\n"
            "Use Codex's built-in image-generation capability for the required count of truthfully "
            "generatable local scientific illustrations. Each illustration must follow "
            "suggested_visual and visual_payload without inventing facts, and it must remain a "
            "local independent picture rather than a slide background. "
            "Do not run shell commands or local file operations. The built-in image tool stores "
            "its result in the current Codex session. In visual_assets, return generated_visuals/"
            "<exact-generated-filename> for the final accepted image, preserving the UUID-style "
            "basename emitted by the image tool. If you reject a draft and regenerate it, reference "
            "only the final accepted image; the backend will recover that exact auditable source. "
            "Do not execute skill scripts or other programs, install "
            "packages, use web search, make network requests, or write files. "
            "If the semantic visual needs unavailable real/source-specific evidence, do not call "
            "image generation; return visual_assets=[] and use the authorized placeholder. An "
            "optional text-led slide may return visual_assets=[] only when its required minimum is "
            "zero.\n"
            f"Create {min_assets}-{max_assets} images in this session; the session hard limit is "
            f"{max_images}. Every image "
            "must be entirely text-free: no letters, words, numbers, formulas, labels, captions, "
            "watermarks, signatures, UI text, logos, or pseudo-text. Generate a cropped local "
            "scientific figure, mechanism fragment, material cutout, evidence motif, or zoomed "
            "detail—not a slide, card, screenshot mock-up, or decorative wallpaper. Use a "
            "transparent background whenever technically reliable, otherwise a clean solid "
            f"#{expected_background} background. When two assets are allowed, give them different "
            "immutable visual refs and different semantic roles. Return each local asset in "
            "visual_assets with a safe generated_visuals/<name> path, geometry, "
            "z, image_fit, object_id, and immutable visual content_ref. Never generate an entire "
            "16:9 slide image.\n"
            "After image generation, inspect the local asset at original resolution. Regenerate it "
            "if it contains glyphs, pseudo-text, a watermark, invented evidence, or accidental "
            'slide chrome. Set raster_audit="inspected-local-assets-text-free" after inspecting '
            "all local assets, or directly when visual_assets is empty.\n"
            "Return no visible copy in modules or visual_assets; text_blocks and "
            "visual_placeholders are layout metadata, while modules and visual_assets become "
            "independent top-level PowerPoint objects. "
            "not visible copy. The backend will place the exact PresentationPlan title and every "
            "key point as transparent, borderless editable text in your blocks. It will not choose "
            "a layout, move a block, or rewrite content. It deterministically maps module style "
            "tokens to the selected palette and draws any authorized missing-asset frame. It may only "
            "reduce a font within your declared preferred-to-minimum range for renderer parity.\n"
            "Return only the JSON object required by the output schema. The backend will validate "
            "every independent object's bounds, identity, spacing, semantic reference, text fit, "
            "and contrast before compiling the exact PresentationPlan into PPTX.\n"
            f"Immutable deck plan SHA-256: {plan_hash}\n"
            f"Immutable current-slide plan SHA-256: {slide_plan_hash or plan_hash}\n"
            "\nDECK_DIRECTOR_CONTEXT_BEGIN\n" + deck_context + "\nDECK_DIRECTOR_CONTEXT_END\n"
            "\nVERIFIED_PAPER_CRAFT_GUIDANCE_BEGIN\n"
            + skill_guidance
            + "\nVERIFIED_PAPER_CRAFT_GUIDANCE_END\n"
            "Mandatory MetaClass instructions above and the immutable contract below override "
            "any conflicting workflow step in the embedded guidance.\n"
            + CodexPPTProvider._paper_deck_image_contract(
                theme,
                expected_background=expected_background,
                style_signature=style_signature,
                visual_requirement=requirement,
                composition=planned_composition,
            )
            + "\nIMMUTABLE_SLIDE_CONTENT:\n"
            + CodexPPTProvider._design_payload_json(plan)
        )

    @staticmethod
    def _paper_deck_deck_context(
        plan: PresentationPlan,
        theme: PresentationTheme,
        *,
        composition_plan: list[dict[str, str]] | None = None,
    ) -> str:
        style_preset = CodexPPTProvider._paper_deck_style_preset(theme)
        planned_compositions = composition_plan or CodexPPTProvider._paper_deck_composition_plan(
            plan
        )
        return json.dumps(
            {
                "deck_title": plan.title,
                "style_signature": (
                    f"{style_preset}:{theme.id}:"
                    f"{CodexPPTProvider.PAPER_DECK_STYLE_SIGNATURE_VERSION}"
                ),
                "style_preset": style_preset,
                "direction": CodexPPTProvider.PAPER_DECK_STYLE_PROMPT,
                "color_theme_id": theme.id,
                "color_theme": CodexPPTProvider._paper_deck_color_payload(theme),
                "continuity_rules": [
                    "Keep one typography hierarchy and graphic-block language across the deck.",
                    "Use at most two recurring accent motifs.",
                    "Vary composition by slide role without changing the visual identity.",
                    "Do not repeat the same silhouette on adjacent content slides; follow the "
                    "assigned layout_director_plan instead of defaulting to left/right columns.",
                    "Treat cover and first content page as style anchors for later pages.",
                    "Keep journal-minimal visual language even when the selected palette changes.",
                    "Keep every key point and missing-asset placeholder as a separate visual island; "
                    "never merge them into one shared mega-card or crowded central cluster.",
                    "Give every key_points.N text block its own independent editorial anchor with "
                    "the same content_ref: a restrained frame, adjacent rule, accent band, marker, "
                    "or callout edge. Keep a non-containing anchor's closest edge within 0.04 of "
                    "its text block. Do not default every point to a rounded card.",
                    "Return every frame, rule, node, and local illustration as a separately movable "
                    "top-level PowerPoint object; never use a full-slide raster or an all-page group.",
                ],
                "slide_sequence": [
                    {
                        "slide_id": slide.id,
                        "order": slide.order,
                        "title": slide.title,
                        "key_point_count": len(slide.key_points),
                        "layout_id": slide.layout_id,
                        "directed_layout_id": planned_compositions[index]["layout_id"],
                        "directed_layout_family": planned_compositions[index]["family"],
                        "composition_direction": planned_compositions[index]["direction"],
                        "suggested_visual": slide.suggested_visual,
                    }
                    for index, slide in enumerate(plan.slides)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _paper_deck_image_contract(
        theme: PresentationTheme,
        *,
        expected_background: str,
        style_signature: str,
        visual_requirement: dict[str, Any],
        composition: dict[str, str] | None = None,
    ) -> str:
        min_assets = int(visual_requirement["min_assets"])
        max_assets = int(visual_requirement["max_assets"])
        min_placeholders = int(visual_requirement["min_placeholders"])
        max_placeholders = int(visual_requirement["max_placeholders"])
        planned_composition = composition or {
            "layout_id": "focus_rail",
            "family": "spotlight",
            "direction": ("Use a focal scientific field with an independent interpretation rail."),
        }
        placeholder_contract = (
            f"- visual_placeholders must contain {min_placeholders}-{max_placeholders} item(s). "
            "Use it only when the requested "
            "visual needs an unavailable real/source-specific asset. Reference immutable "
            "suggested_visual or visual_payload.N; return geometry only and no authored text. "
            "Reserve one useful empty visual region at that geometry. It may be central, wide, "
            "horizontal, inset, or lateral according to the assigned composition, with width at "
            f"least {CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MIN_WIDTH:.2f}, height at least "
            f"{CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MIN_HEIGHT:.2f}, and area at least "
            f"{CodexPPTProvider.PAPER_DECK_VISUAL_REGION_MIN_AREA:.2f}. Keep at least "
            f"{CodexPPTProvider.PAPER_DECK_MIN_MODULE_GAP:.3f} clearance from every unrelated text "
            "block and substantive visual island. visual_assets and "
            "visual_placeholders are mutually exclusive. "
            "Do not return a competing module there; MetaClass will add "
            "the editable frame and deterministic Chinese label."
            if max_placeholders
            else "- Return visual_placeholders=[] exactly for this slide."
        )
        return f"""
Hard content contract:
- Treat the supplied title, key points, suggested_visual, and visual_payload as immutable
  semantic direction. Never summarize, merge, split, translate, delete, or invent content.
- Modules and local raster assets must remain entirely text-free because the backend owns the
  exact visible copy.
- Speaker scripts are intentionally omitted and must never appear on slides.

Paper Deck editable-layer contract:
- Return status=completed and exactly one slide object for the supplied slide_id.
- Set background={expected_background}, manifest_version={CodexPPTProvider.PAPER_DECK_MANIFEST_VERSION},
  layout_mode="editable-layered-objects", style_signature="{style_signature}", and
  raster_audit="inspected-local-assets-text-free" exactly.
- The page background is a native PowerPoint fill. Never return a full-slide image, a full-slide
  shape, a group, or an object that contains multiple unrelated semantic modules.
- Return 1-{CodexPPTProvider.PAPER_DECK_MAX_VISUAL_MODULES} modules. Every card, frame, node,
  band, divider, underline, and connector is a separate module with a unique object_id, one
  immutable content_ref, geometry, z, and a schema-approved style_token. Modules contain no text.
- style_token is semantic, not a color escape hatch. The backend maps it to the selected palette.
  Use line tokens only on line modules and panel/node/band tokens only on shape modules.
- Return {min_assets}-{max_assets} local image objects. Each must reference suggested_visual or
  visual_payload.N, be smaller than the full canvas, contain no text or invented evidence, and use
  contain/cover deliberately. Each is a top-level movable Picture, never a page background. Prefer
  transparent-background scientific cutouts, mechanism fragments, evidence motifs, and local zooms.
- Assigned composition: {planned_composition["layout_id"]} ({planned_composition["family"]}).
  {planned_composition["direction"]}
- On a non-cover slide, visual_assets and visual_placeholders are mutually exclusive. If either is
  present, place it according to the assigned composition. A visual region may be a central figure,
  wide evidence strip, top/bottom stage, inset, asymmetric side field, or paired small multiple; it
  is not required to be a vertical left/right column. Keep each visual independently movable and at
  least {CodexPPTProvider.PAPER_DECK_MIN_MODULE_GAP:.3f} from unrelated Plan text and substantive
  modules. When two assets are present, separate them and bind them to distinct visual refs.
- Use a distributed editorial composition. Do not combine unrelated key points into one shared
  card, nested container, collage, overlapping cluster, or central pile. Do not put every point in
  a rounded rectangle. Each key_points.N needs its own independent editorial anchor with the same
  content_ref: either a restrained containing frame, or an adjacent rule, accent band, marker, or
  callout edge. Keep the closest edge of a non-containing anchor within 0.04 of its text block.
  Paper-figure composition, side notes, evidence strips, process paths, and local zoom arrangements
  are preferred over a repetitive UI card grid. Keep clear space between refs.
- Return text_blocks in exact content order with refs title, then key_points.0..N. Each block must
  describe the padded inner writing area of its matching module or a quiet page region. font_role
  must be one of
  sans, serif, handwritten, display, or mono. Titles must remain at least 24 pt; body at least
  14 pt. Give the unchanged exact strings enough area at min_font_size. max_lines is the preferred
  line budget, but exact copy may use additional lines when measured text height still fits fully
  inside the declared block; never truncate, shrink below the minimum, or let text leave the block.
- Every inner writing area must satisfy x>=0.045, x+w<=0.955, y>=0.04, and y+h<=0.96
  after visual padding. Text blocks must not overlap; keep a visible gap between neighboring
  treatments. These are hard backend validation limits, not optional style suggestions. To
  remain cross-platform safe, author every block against the stricter x>=0.05, x+w<=0.95,
  y>=0.05, and y+h<=0.95 limits. Before returning, explicitly
  calculate x+w and y+h for every block; never use the backend tolerance as layout space.
{placeholder_contract}
- The selected text color must contrast with the native module fill or page background behind it.
  Do not place text over visual_assets. Normal text requires at least 4.5:1;
  text at least 24pt, or bold text at least 18.66pt, requires at least 3.0:1. Keep each entire
  glyph footprint on a quiet writing surface. No connector, divider, underline, node, or partial
  shape may cross or clip a text block.
- Never fabricate evidence, numbers, formulas, labels, logos, signatures, or watermarks.
- The visual preset is always journal-minimal: {CodexPPTProvider.PAPER_DECK_STYLE_PROMPT}.
- The selected color theme changes palette values only. Never reinterpret its name as warm-notes,
  liquid-glass, business-research, classroom-paper, botanical, cyber, or another visual preset.
- Preserve the Nature/IEEE academic composition, publication-style diagrams, disciplined sans-serif
  typography, restrained graphic blocks, medium information density, and evidence-first hierarchy
  for every palette. Use no more than two accent colors from the supplied palette.

SELECTED_COLOR_THEME:
{json.dumps(CodexPPTProvider._paper_deck_color_payload(theme), ensure_ascii=False, indent=2)}
""".strip()

    @staticmethod
    def _paper_deck_style_preset(theme: PresentationTheme) -> str:
        _ = theme
        return CodexPPTProvider.PAPER_DECK_STYLE_PRESET

    @staticmethod
    def _paper_deck_color_payload(theme: PresentationTheme) -> dict[str, Any]:
        payload = theme.prompt_payload()
        return {
            "id": payload["id"],
            "name": payload["name"],
            "colors": payload["colors"],
            "allowed_colors": payload["allowed_colors"],
        }

    @staticmethod
    def _paper_craft_vector_prompt(
        plan: PresentationPlan,
        plan_hash: str,
        theme: PresentationTheme,
        *,
        validation_error: str | None,
        skill_guidance: str = "",
    ) -> str:
        correction = (
            f"Correct this previous structured-design failure: {validation_error}\n"
            if validation_error
            else ""
        )
        return (
            "Use $paper-deck as required visual-direction guidance. The user has already "
            "authorized generation, so do not request confirmation. Apply its composition, "
            "layout variety, hierarchy, spacing, and quality-gate principles only; skip its "
            "outline, page-image generation, and merge steps. No tool access is available in "
            "this fallback: do not call image generation, run commands, or write files. Return "
            "only JSON matching the output schema, using editable text and vector geometry.\n"
            + correction
            + f"Immutable plan SHA-256: {plan_hash}\n"
            "\nVERIFIED_PAPER_CRAFT_GUIDANCE_BEGIN\n"
            + skill_guidance
            + "\nVERIFIED_PAPER_CRAFT_GUIDANCE_END\n"
            "Mandatory MetaClass instructions above and the immutable contract below override "
            "any conflicting workflow step in the embedded guidance.\n"
            + CodexPPTProvider._structured_design_contract(theme)
            + "\nIMMUTABLE_SLIDE_CONTENT:\n"
            + CodexPPTProvider._design_payload_json(plan)
        )

    @staticmethod
    def _initial_prompt(
        plan: PresentationPlan,
        plan_hash: str,
        theme: PresentationTheme,
    ) -> str:
        return (
            "You are the visual director for an editable academic presentation. "
            "No tool access is available: do not run shell commands, do not call tools, "
            "and do not try to write files. Return only the JSON object required by the "
            "provided output schema. The backend safely compiles that design into PPTX.\n"
            f"Immutable plan SHA-256: {plan_hash}\n"
            + CodexPPTProvider._structured_design_contract(theme)
            + "\nIMMUTABLE_SLIDE_CONTENT:\n"
            + CodexPPTProvider._design_payload_json(plan)
        )

    @staticmethod
    def _repair_prompt(
        plan: PresentationPlan,
        plan_hash: str,
        validation_error: str,
        theme: PresentationTheme,
    ) -> str:
        return (
            "Return a complete corrected structured slide design. No tool access is available; "
            "do not run commands or write files. Return only JSON matching the output schema.\n"
            f"Validation failure: {validation_error}\n"
            f"Immutable plan SHA-256: {plan_hash}\n"
            + CodexPPTProvider._structured_design_contract(theme)
            + "\nIMMUTABLE_SLIDE_CONTENT:\n"
            + CodexPPTProvider._design_payload_json(plan)
        )

    @staticmethod
    def _structured_design_contract(
        theme: PresentationTheme,
        *,
        allow_images: bool = False,
        max_images: int = 0,
        expected_background: str | None = None,
        allow_centered_title: bool = False,
    ) -> str:
        allowed_colors = ", ".join(dict.fromkeys(theme.palette.allowed_colors))
        element_rule = (
            "Use only editable text elements plus exactly one audited full-slide background "
            f"image. This isolated slide may use no more than {max_images} image element."
            if allow_images
            else "Use only text, shape, and line elements."
        )
        image_rule = (
            "- Image elements must reference only files you created under generated_visuals/; "
            "their text must be empty. The single image must use x=0,y=0,w=1,h=1,z=0. It is "
            "allowed to sit behind all text; return no other non-text elements.\n"
            if allow_images
            else "- Do not return image elements in structured fallback mode.\n"
        )
        visual_rule = (
            "- Encode the supplied suggested_visual and visual_payload in the generated, "
            "text-free background. Reserve deliberate negative space for the editable text "
            "boxes instead of covering the illustration with large panels.\n"
            if allow_images
            else "- Every non-text element must communicate grouping, sequence, comparison, "
            "direction, scale, or emphasis. Do not add unlabeled decorative blobs, empty cards, "
            "arbitrary circles, giant empty frames, or ornamental connectors. Follow "
            "suggested_visual and layout_id when feasible with editable geometry, using only "
            "facts available in the supplied visual_payload.\n"
        )
        background_rule = (
            f"Set this slide object's background field to {expected_background}."
            if expected_background
            else (
                f"First background must be {theme.palette.board}; all other backgrounds must "
                f"be {theme.palette.paper}."
            )
        )
        title_zone_rule = (
            "Titles stay within y=0.05..0.65 on this cover slide."
            if allow_centered_title
            else "Titles stay within y=0.05..0.20."
        )
        return f"""
Hard content contract:
- Keep exactly the supplied slide count, slide_id order, title, and every key point.
- For each slide, text elements must appear in this exact array order: title first, then each
  key point exactly once. Do not add, delete, summarize, paraphrase, merge, split, translate,
  or change punctuation, case, numbers, Unicode codepoints, or internal whitespace.
- suggested_visual is direction only and must never become visible text.
- Speaker scripts are intentionally omitted and must never appear on slides.

Safe design contract:
- Produce one slide object per supplied slide and set status to completed.
- {element_rule} Decorative elements have text="".
{image_rule}- Populate image_path on every element: generated_visuals/<name> only for image
  elements, and image_path="" for text, shape, and line elements.
- Use at least one non-text element on every slide, but avoid dense dashboard/card grids.
- Establish one deck-wide visual system first: one title rhythm, one spacing scale, one shape
  language, and no more than two recurring accent motifs. Vary slide compositions without
  making the deck look like unrelated templates.
- Use a 12-column mental grid and a consistent baseline. Align related edges precisely and
  make whitespace intentional. Prefer one dominant exhibit plus supporting copy.
{visual_rule}
- Turn the unchanged key-point strings themselves into meaningful nodes, stages, labels, or
  evidence blocks. Do not merely place bullets beside unrelated decoration.
- Never fake evidence or invent labels, numbers, formulas, or assets.
- Keep all text inside x=0.055..0.945. {title_zone_rule} Other text stays within y=0.22..0.94.
- Give every title box at least h=0.12. Give each body text box enough height for its full
  unchanged sentence; prefer h>=0.16 and wider fields over smaller type.
- Text boxes must not overlap each other. Shapes may sit behind text with lower z values.
- Title font size is at least 28; body font size is at least 18.
- Text-box fill, line, and opacity are rendered exactly. Use line_width=0 and a fill matching
  the slide background for unboxed editorial text. Use a deliberate contrasting fill for a
  card or highlighted node. Text opacity must be 100.
- Text-to-fill contrast must be at least 4.5:1 for normal text and 3:1 for large or bold text.
- Avoid using the same three-card row, vertical timeline, or circle-and-line silhouette on
  adjacent slides. Avoid excessive rounded rectangles and heavy borders.
- Canvas is 16:9. {background_rule}
- Apply the selected theme direction: {theme.style_direction}.
- Use only these selected-theme colors: {allowed_colors}.
- Follow these theme rules: {"; ".join(theme.rules)}
- Populate every required schema field. For non-text elements use text="". For fields that are
  visually irrelevant to an element type, use a valid neutral palette value.

SELECTED_THEME:
{json.dumps(theme.prompt_payload(), ensure_ascii=False, indent=2)}
""".strip()

    @staticmethod
    def _design_payload_json(plan: PresentationPlan) -> str:
        return json.dumps(
            {
                "deck_title": plan.title,
                "slides": [
                    {
                        "slide_id": slide.id,
                        "order": slide.order,
                        "title": slide.title,
                        "key_points": slide.key_points,
                        "suggested_visual": slide.suggested_visual,
                        "layout_id": slide.layout_id,
                        "visual_payload": slide.visual_payload,
                    }
                    for slide in plan.slides
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
