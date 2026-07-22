import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from metaclass.modules.presentation.brand_palette import apply_brand_palette
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.providers import validate_deck_against_plan
from metaclass.modules.presentation.schemas import PPTArtifact, PresentationPlan, SlideElement
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.presentation.themes import (
    PresentationTheme,
    get_presentation_theme,
)


class CodexGenerationError(RuntimeError):
    """A Codex generation failure that is eligible for provider fallback."""


class CodexPPTProvider:
    """Generate an editable PPTX with a pinned Codex non-interactive runtime."""

    PROMPT_VERSION = "2026-07-21-v3-themed-structured-design"

    def __init__(
        self,
        *,
        adapter: PPTSkillAdapter,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 900.0,
        repair_attempts: int = 1,
        codex_bin: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.repair_attempts = repair_attempts
        self.codex_bin = codex_bin

    def prepare_request(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
        theme: PresentationTheme | None = None,
    ) -> PPTArtifact:
        selected_theme = theme or get_presentation_theme()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            pptx_path, metadata = self._generate_validated_deck(
                plan=plan,
                job_id=job_id,
                output_dir=output_dir,
                theme=selected_theme,
            )
        except CodexGenerationError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise CodexGenerationError(f"Codex PPT generation failed: {exc}") from exc

        # Preview rendering is shared infrastructure. Let its error propagate instead
        # of spending Presenton credits on a fallback that would use the same renderer.
        return self.adapter.prepare_external_pptx(
            plan=plan,
            job_id=job_id,
            output_dir=output_dir,
            pptx_path=pptx_path,
            provider_name="codex",
            provider_metadata=metadata,
            external_slide_images=None,
        )

    def _generate_validated_deck(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
        theme: PresentationTheme,
    ) -> tuple[Path, dict[str, Any]]:
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
                    "presentation_plan": plan.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        run_results: list[dict[str, Any]] = []
        validation_errors: list[str] = []
        validation: dict[str, Any] | None = None
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
            generation_mode = "unknown"

            for attempt in range(1, self.repair_attempts + 2):
                prompt = (
                    self._initial_prompt(plan, plan_hash, theme)
                    if attempt == 1
                    else self._repair_prompt(
                        plan,
                        plan_hash,
                        validation_errors[-1],
                        theme,
                    )
                )
                result = self._execute_codex(
                    workspace=workspace,
                    output_dir=output_dir,
                    prompt=prompt,
                    attempt=attempt,
                )
                run_results.append(result)

                current_plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
                if current_plan_hash != plan_hash:
                    plan_path.write_bytes(plan_json.encode("utf-8"))
                    error = "Codex modified the immutable presentation_plan.json"
                else:
                    try:
                        _, generation_mode = self._materialize_result(
                            plan=plan,
                            result=result,
                            deck_path=deck_path,
                            theme=theme,
                        )
                        validation = validate_deck_against_plan(
                            plan,
                            deck_path,
                            provider_name="Codex",
                        )
                    except (RuntimeError, ValueError) as exc:
                        error = str(exc)
                    else:
                        error = ""

                if not error:
                    break
                validation_errors.append(error)
                if attempt > self.repair_attempts:
                    raise CodexGenerationError(
                        "Codex output violated the immutable PresentationPlan contract "
                        f"after {attempt} attempt(s): {error}"
                    )

            if validation is None:
                raise CodexGenerationError("Codex did not produce a validated PPTX")
            final_pptx_path = output_dir / "deck.pptx"
            shutil.copy2(deck_path, final_pptx_path)

        return final_pptx_path, {
            "runtime": "openai-codex-cli-bin",
            "prompt_version": self.PROMPT_VERSION,
            "model": self.model,
            "theme": theme.prompt_payload(),
            "plan_sha256": plan_hash,
            "attempt_count": len(run_results),
            "repair_attempts_used": max(0, len(run_results) - 1),
            "validation_errors": validation_errors,
            "validation": validation,
            "responses": [self._public_result(item) for item in run_results],
            "generation_mode": generation_mode,
            "content_contract": "exact-visible-title-and-key-points",
            "speaker_script_binding": "presentation-plan-slide-id",
            "preview_source": "real-pptx-render-plan-validated",
        }

    def _materialize_result(
        self,
        *,
        plan: PresentationPlan,
        result: dict[str, Any],
        deck_path: Path,
        theme: PresentationTheme,
    ) -> tuple[PresentationPlan, str]:
        status = str(result.get("status", "unknown"))
        if status != "completed":
            summary = str(result.get("summary", "Codex did not complete the design"))
            raise ValueError(f"Codex returned {status}: {summary[:1000]}")

        # Retain compatibility with a future runtime that can safely create the
        # binary itself. The normal local-account path uses structured design.
        if str(result.get("deck_path", "")) == "deck.pptx" and deck_path.is_file():
            return plan, "direct-pptx"

        designed_plan = self._design_plan_from_result(plan, result, theme)
        self.adapter.render_declarative_pptx(designed_plan, deck_path)
        return designed_plan, "structured-design-safe-render"

    @staticmethod
    def _design_plan_from_result(
        plan: PresentationPlan,
        result: dict[str, Any],
        theme: PresentationTheme,
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
        for index, (expected, raw_slide) in enumerate(
            zip(plan.slides, raw_slides, strict=True)
        ):
            if not isinstance(raw_slide, dict):
                raise ValueError(f"Codex design slide {index + 1} is not an object")
            if raw_slide.get("slide_id") != expected.id:
                raise ValueError(
                    f"Codex design slide-id mismatch at position {index + 1}: "
                    f"expected {expected.id}, got {raw_slide.get('slide_id')}"
                )

            expected_background = (
                theme.palette.board if index == 0 else theme.palette.paper
            )
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
            has_visual = False
            for raw_element in raw_elements:
                element = CodexPPTProvider._parse_design_element(raw_element)
                if element.type == "text":
                    text = element.text or ""
                    visible_texts.append(text)
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
                            "z": 50 + len(visible_texts),
                            "style": element.style.model_copy(
                                update={"font_size": fitted_size}
                            ),
                        }
                    )
                else:
                    if element.text:
                        raise ValueError(
                            f"Non-text element contains visible copy on {expected.id}"
                        )
                    has_visual = True
                    element = element.model_copy(update={"z": min(element.z, 40)})
                elements.append(apply_brand_palette(element, theme.palette))

            expected_texts = [expected.title, *expected.key_points]
            if visible_texts != expected_texts:
                raise ValueError(
                    f"Codex visible text mismatch on {expected.id}: "
                    f"expected {expected_texts!r}, got {visible_texts!r}"
                )
            if not has_visual:
                raise ValueError(f"Codex design contains no visual structure on {expected.id}")

            PresentationPlanGenerator._validate_scene_safe_zones(
                elements,
                expected.title,
                allow_centered_title=index == 0,
            )
            if PresentationPlanGenerator._has_unsafe_scene_collisions(
                elements,
                expected.title,
            ):
                raise ValueError(
                    f"Codex design contains overlapping content on {expected.id}"
                )

            designed_slides.append(
                expected.model_copy(
                    update={
                        "layout": "freeform",
                        "layout_id": "codex_structured_design",
                        "background": expected_background,
                        "elements": elements,
                    }
                )
            )
        return plan.model_copy(update={"slides": designed_slides})

    @staticmethod
    def _parse_design_element(raw_element: Any) -> SlideElement:
        if not isinstance(raw_element, dict):
            raise ValueError("Codex design element is not an object")
        element_type = raw_element.get("type")
        if element_type not in {"text", "shape", "line"}:
            raise ValueError(f"Unsupported Codex design element type: {element_type}")
        style = raw_element.get("style")
        if not isinstance(style, dict):
            raise ValueError("Codex design element has no style object")
        for color_key in ("color", "fill", "line_color"):
            color = style.get(color_key)
            if not isinstance(color, str) or not re.fullmatch(
                r"[0-9A-Fa-f]{6}", color
            ):
                raise ValueError(f"Invalid Codex design color: {color_key}={color!r}")
        payload = {
            "type": element_type,
            "x": raw_element.get("x"),
            "y": raw_element.get("y"),
            "w": raw_element.get("w"),
            "h": raw_element.get("h"),
            "z": raw_element.get("z"),
            "text": raw_element.get("text") if element_type == "text" else None,
            "shape": raw_element.get("shape", "rectangle"),
            "style": style,
        }
        return SlideElement.model_validate(payload)

    def _execute_codex(
        self,
        *,
        workspace: Path,
        output_dir: Path,
        prompt: str,
        attempt: int,
    ) -> dict[str, Any]:
        codex_bin = self._resolve_codex_bin()
        if not self.api_key:
            self._ensure_authenticated(codex_bin)
        result_path = workspace / f"codex_result_{attempt}.json"
        schema_path = workspace / "result_schema.json"
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--config",
            'shell_environment_policy.inherit="core"',
            "--config",
            "shell_environment_policy.ignore_default_excludes=false",
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
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")

        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
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
            )
            raise CodexGenerationError(
                f"Codex generation timed out after {self.timeout_seconds:g} seconds"
            ) from exc

        result = self._read_result(result_path)
        self._write_run_log(
            output_dir,
            attempt,
            returncode=process.returncode,
            duration_seconds=time.monotonic() - started_at,
            stdout=stdout,
            stderr=stderr,
            result=result,
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
    def _ensure_authenticated(codex_bin: str) -> None:
        try:
            status = subprocess.run(
                [codex_bin, "login", "status"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
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
    ) -> None:
        (output_dir / f"codex_attempt_{attempt}.json").write_text(
            json.dumps(
                {
                    "attempt": attempt,
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
    def _public_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(result.get("status", "unknown"))[:100],
            "deck_path": str(result.get("deck_path", ""))[:500],
            "summary": str(result.get("summary", ""))[:1000],
            "slide_design_count": (
                len(result["slides"]) if isinstance(result.get("slides"), list) else 0
            ),
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
                                                    "enum": ["text", "shape", "line"],
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
    def _structured_design_contract(theme: PresentationTheme) -> str:
        allowed_colors = ", ".join(dict.fromkeys(theme.palette.allowed_colors))
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
- Use only text, shape, and line elements. Decorative elements have text="".
- Use at least one non-text element on every slide, but avoid dense dashboard/card grids.
- Use a single clear composition per slide and vary silhouettes across the deck.
- Keep all text inside x=0.055..0.945. Titles stay within y=0.05..0.20, except the first
  slide may center its title down to y=0.65. Other text stays within y=0.22..0.94.
- Give every title box at least h=0.12. Give each body text box enough height for its full
  unchanged sentence; prefer h>=0.16 and wider fields over smaller type.
- Text boxes must not overlap each other. Shapes may sit behind text with lower z values.
- Title font size is at least 28; body font size is at least 18.
- Canvas is 16:9. First background must be {theme.palette.board}; all other backgrounds
  must be {theme.palette.paper}.
- Apply the selected theme direction: {theme.style_direction}.
- Use only these selected-theme colors: {allowed_colors}.
- Follow these theme rules: {'; '.join(theme.rules)}
- Populate every required schema field. For non-text elements use text="". For fields that
  are visually irrelevant to an element type, use a valid neutral palette value.

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
                    }
                    for slide in plan.slides
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
