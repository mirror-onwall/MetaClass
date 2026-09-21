from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from pydantic import Field

from metaclass.core.schemas import SchemaModel
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
    NativePaperDeckSlide,
)


class FinalPaperDeckPageAnalysisError(RuntimeError):
    """Raised when final raster pages cannot be observed safely."""


class ImageJSONProvider(Protocol):
    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str: ...


class OCRProvider(Protocol):
    def extract_text(self, image_path: Path) -> str: ...


class TesseractOCRProvider:
    """Small deployment-friendly OCR adapter with no Python package dependency."""

    def __init__(self, *, command: str = "tesseract", languages: str = "eng+snum") -> None:
        self.command = command
        self.languages = languages

    def extract_text(self, image_path: Path) -> str:
        try:
            process = subprocess.run(
                [self.command, str(image_path), "stdout", "-l", self.languages, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FinalPaperDeckPageAnalysisError(
                f"OCR could not run for {image_path.name}"
            ) from exc
        if process.returncode:
            raise FinalPaperDeckPageAnalysisError(
                f"OCR failed for {image_path.name}: {process.stderr.strip()}"
            )
        return process.stdout.strip()


class FinalSlideObservation(SchemaModel):
    """What appears on a final slide; never a source of paper facts."""

    slide_id: str = Field(min_length=1)
    visible_title: str
    visible_text: list[str] = Field(default_factory=list)
    visual_summary: str
    figure_labels: list[str] = Field(default_factory=list)
    quantitative_mentions: list[str] = Field(default_factory=list)
    formula_mentions: list[str] = Field(default_factory=list)
    page_type: Literal[
        "title",
        "agenda",
        "background",
        "method",
        "experiment",
        "result",
        "limitation",
        "conclusion",
        "reference",
        "other",
    ] = "other"
    detected_warnings: list[str] = Field(default_factory=list)


class FinalPaperDeckPageAnalyzer:
    """Observe native raster slides and compare them with planning artifacts."""

    _number = re.compile(
        r"(?<![\w.])(?:[<>~=±≤≥]\s*)?-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?"
    )
    _figure = re.compile(
        r"\b(?:fig(?:ure)?|table|图|表)\s*[.:：]?\s*[A-Za-z]?\d+[A-Za-z]?\b",
        re.IGNORECASE,
    )
    _formula = re.compile(
        r"(?:[A-Za-zα-ωΑ-Ω][A-Za-z0-9_{}^]*\s*[=≈≤≥<>]\s*[^,，。;；]{1,60}|"
        r"(?:argmax|argmin|softmax|log|exp)\s*\([^)]{1,80}\))",
        re.IGNORECASE,
    )
    _garbled = re.compile(r"(?:�|□|■|\?{3,}|[\x00-\x08\x0b\x0c\x0e-\x1f])")
    _valid_page_types: ClassVar[set[str]] = {
        "title",
        "agenda",
        "background",
        "method",
        "experiment",
        "result",
        "limitation",
        "conclusion",
        "reference",
        "other",
    }

    def __init__(
        self,
        *,
        vision: ImageJSONProvider,
        ocr: OCRProvider | None = None,
    ) -> None:
        self.vision = vision
        self.ocr = ocr or TesseractOCRProvider()

    def analyze(
        self,
        manifest: NativePaperDeckManifest,
        *,
        workspace: Path,
        paper_content_path: Path,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[FinalSlideObservation]:
        root = workspace.resolve()
        paper_path = paper_content_path.resolve()
        if not paper_path.is_file():
            raise FinalPaperDeckPageAnalysisError("paper_content.md is required for validation")
        paper_text = paper_path.read_text(encoding="utf-8")
        observations = []
        for slide in manifest.slides:
            if check_cancelled:
                check_cancelled()
            observations.append(self._analyze_slide(slide, root=root, paper_text=paper_text))
        if [slide.slide_id for slide in observations] != [
            slide.id for slide in manifest.slides
        ]:
            raise FinalPaperDeckPageAnalysisError("final slide observations lost page order")
        return observations

    def _analyze_slide(
        self,
        slide: NativePaperDeckSlide,
        *,
        root: Path,
        paper_text: str,
    ) -> FinalSlideObservation:
        image_path = self._resolve(root, slide.image_path)
        prompt_path = self._resolve(root, slide.prompt_path)
        ocr_text = self.ocr.extract_text(image_path)
        page_prompt = prompt_path.read_text(encoding="utf-8")
        response = self.vision.complete_image_json(
            self._vision_prompt(slide, ocr_text=ocr_text, page_prompt=page_prompt),
            image_path,
            temperature=0.0,
        )
        try:
            payload = self._parse_json(response)
        except FinalPaperDeckPageAnalysisError:
            repaired = self.vision.complete_image_json(
                self._vision_repair_prompt(
                    slide,
                    ocr_text=ocr_text,
                    invalid_response=response,
                ),
                image_path,
                temperature=0.0,
            )
            payload = self._parse_json(repaired)
        visible_title = str(payload.get("visible_title") or "").strip()
        model_visible_text = self._string_list(payload, "visible_text")
        visible_text = self._unique_strings(
            [*self._ocr_lines(ocr_text), *model_visible_text]
        )
        combined_visible = "\n".join([visible_title, *visible_text])
        figure_labels = self._unique_strings(
            [
                *self._string_list(payload, "figure_labels"),
                *self._figure.findall(combined_visible),
            ]
        )
        model_quantitative_mentions = self._string_list(payload, "quantitative_mentions")
        quantitative_mentions = self._unique_strings(
            model_quantitative_mentions
            or [
                number
                for line in [visible_title, *model_visible_text]
                if self._quantitative_tokens(line)
                for number in self._number.findall(line)
            ]
        )
        formula_mentions = self._unique_strings(
            [
                *self._string_list(payload, "formula_mentions"),
                *[match.group(0).strip() for match in self._formula.finditer(combined_visible)],
            ]
        )
        page_type = str(payload.get("page_type") or "other").strip().casefold()
        if page_type not in self._valid_page_types:
            page_type = "other"
        warnings = self._warnings(
            slide,
            visible_title=visible_title,
            visible_text=visible_text,
            visual_summary=str(payload.get("visual_summary") or "").strip(),
            quantitative_mentions=quantitative_mentions,
            paper_text=paper_text,
            model_warnings=self._string_list(payload, "detected_warnings"),
            page_prompt=page_prompt,
        )
        return FinalSlideObservation(
            slide_id=slide.id,
            visible_title=visible_title,
            visible_text=visible_text,
            visual_summary=str(payload.get("visual_summary") or "").strip(),
            figure_labels=figure_labels,
            quantitative_mentions=quantitative_mentions,
            formula_mentions=formula_mentions,
            page_type=page_type,
            detected_warnings=warnings,
        )

    @staticmethod
    def _vision_prompt(slide: NativePaperDeckSlide, *, ocr_text: str, page_prompt: str) -> str:
        planned = json.dumps(
            {
                "title": slide.title_hint,
                "role": slide.role,
                "message": slide.message,
                "visual_intent": slide.visual_intent,
                "planned_text": slide.planned_text,
                "evidence_hint": slide.evidence_hint,
                "generation_prompt": page_prompt[:8000],
                "ocr_candidate": ocr_text[:8000],
            },
            ensure_ascii=False,
        )
        return f"""FINAL_PAPER_DECK_PAGE_OBSERVATION_V1
Observe only what is visibly present in this slide image. OCR text is an imperfect hint.
Do not infer paper facts and do not repair, embellish, or add claims that are not visible.
Compare the visible page with its plan only to identify possible drift.
List a quantitative mention only when you can visually confirm the digits in the image;
ignore digit-like OCR gibberish and omit uncertain numbers.

Return one JSON object with exactly these keys:
visible_title (string), visible_text (array of strings), visual_summary (string),
figure_labels (array), quantitative_mentions (array), formula_mentions (array),
page_type (title|agenda|background|method|experiment|result|limitation|conclusion|reference|other),
detected_warnings (array using only suspected_garbled_text, suspected_content_drift,
suspected_missing_planned_content, suspected_unreadable_text, or suspected_visual_mismatch).

Planning context (not factual evidence):
{planned}
"""

    @staticmethod
    def _vision_repair_prompt(
        slide: NativePaperDeckSlide,
        *,
        ocr_text: str,
        invalid_response: str,
    ) -> str:
        context = json.dumps(
            {
                "slide_id": slide.id,
                "planned_title": slide.title_hint,
                "ocr_candidate": ocr_text[:6000],
                "invalid_response": invalid_response[:6000],
            },
            ensure_ascii=False,
        )
        return f"""REPAIR_FINAL_PAPER_DECK_PAGE_OBSERVATION_V1
Observe the attached slide again and return JSON only. Do not wrap it in markdown and do not
explain your answer. OCR and the previous response are imperfect hints, not factual evidence.
Only include digits that are visually confirmed in the image; ignore OCR-only numeric gibberish.

Required JSON object keys:
visible_title (string), visible_text (array of strings), visual_summary (string),
figure_labels (array of strings), quantitative_mentions (array of strings),
formula_mentions (array of strings),
page_type (title|agenda|background|method|experiment|result|limitation|conclusion|reference|other),
detected_warnings (array of strings).

Context:
{context}
"""

    def _warnings(
        self,
        slide: NativePaperDeckSlide,
        *,
        visible_title: str,
        visible_text: list[str],
        visual_summary: str,
        quantitative_mentions: list[str],
        paper_text: str,
        model_warnings: list[str],
        page_prompt: str,
    ) -> list[str]:
        allowed_model_warnings = {
            "suspected_garbled_text",
            "suspected_content_drift",
            "suspected_missing_planned_content",
            "suspected_unreadable_text",
            "suspected_visual_mismatch",
        }
        warnings = [item for item in model_warnings if item in allowed_model_warnings]
        visible = "\n".join([visible_title, *visible_text])
        if self._garbled.search(visible):
            warnings.append("suspected_garbled_text")
        if visible_title and self._similarity(visible_title, slide.title_hint) < 0.28:
            warnings.append("suspected_outline_title_drift")
        planned_terms = " ".join([slide.message, slide.visual_intent, *slide.planned_text])
        observed_terms = f"{visible} {visual_summary}"
        if planned_terms and observed_terms and self._token_overlap(planned_terms, observed_terms) < 0.08:
            warnings.append("suspected_outline_content_drift")
        if page_prompt.strip() and visual_summary and self._token_overlap(
            page_prompt, visual_summary
        ) < 0.03:
            warnings.append("suspected_prompt_visual_drift")
        source_numbers = {self._normalize_number(item) for item in self._number.findall(paper_text)}
        for mention in quantitative_mentions:
            mention_numbers = self._quantitative_tokens(mention)
            if mention_numbers and not all(item in source_numbers for item in mention_numbers):
                warnings.append(f"unverified_quantitative_mention:{mention}")
        return self._unique_strings(warnings)

    @classmethod
    def _quantitative_tokens(cls, value: str) -> list[str]:
        """Return numeric claims, excluding figure labels and named identifiers.

        Vision models occasionally report ``Figure 6`` or the zero in ``SKILL0`` as a
        quantitative result. Those are navigation/identity labels, not claims that a
        targeted raster repair can or should remove.
        """
        text = value.strip()
        if not text or cls._figure.fullmatch(text):
            return []
        if "名称中的数字" in text or "digit in the name" in text.casefold():
            return []
        matches = cls._number.findall(text)
        if not matches:
            return []
        without_numbers = cls._number.sub("", text).strip(" \t\r\n:：,，;；()（）[]【】'\"“”‘’")
        if len(matches) == 1 and without_numbers.casefold() in {"skill", "skillo"}:
            return []
        return [cls._normalize_number(item) for item in matches]

    @staticmethod
    def _parse_json(response: str) -> dict[str, object]:
        text = response.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise FinalPaperDeckPageAnalysisError("VLM response is not a JSON object")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise FinalPaperDeckPageAnalysisError("VLM response contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise FinalPaperDeckPageAnalysisError("VLM response must be a JSON object")
        return payload

    @staticmethod
    def _string_list(payload: dict[str, object], key: str) -> list[str]:
        value = payload.get(key, [])
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _ocr_lines(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _unique_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @staticmethod
    def _normalize_number(value: str) -> str:
        return re.sub(r"[\s,]", "", value).casefold().replace("×", "x")

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return SequenceMatcher(None, left.casefold(), right.casefold()).ratio()

    @staticmethod
    def _token_overlap(left: str, right: str) -> float:
        def tokens(value: str) -> set[str]:
            latin = re.findall(r"[A-Za-z0-9]{2,}", value.casefold())
            chinese = re.findall(r"[\u4e00-\u9fff]{2}", value)
            return {*latin, *chinese}

        left_tokens = tokens(left)
        if not left_tokens:
            return 1.0
        return len(left_tokens & tokens(right)) / len(left_tokens)

    @staticmethod
    def _resolve(root: Path, value: str) -> Path:
        path = Path(value)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise FinalPaperDeckPageAnalysisError(f"slide artifact is invalid: {value}")
        return resolved
