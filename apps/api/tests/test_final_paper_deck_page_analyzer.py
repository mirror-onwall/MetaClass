import json
from pathlib import Path

import pytest
from PIL import Image

from metaclass.modules.paper_workflow.final_paper_deck_page_analyzer import (
    FinalPaperDeckPageAnalysisError,
    FinalPaperDeckPageAnalyzer,
)
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    NativePaperDeckManifest,
    NativePaperDeckSlide,
)


class FakeOCR:
    def extract_text(self, image_path: Path) -> str:
        assert image_path.name == "01-method.png"
        return "Instruction Backtranslation\nFigure 2\nAccuracy 81.4%\nClaimed 72.1%\nx = y + 1"


class FakeVision:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str:
        self.prompts.append(prompt)
        assert Path(image_path).name == "01-method.png"
        assert temperature == 0.0
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


class RepairingVision(FakeVision):
    def __init__(self, repaired: dict[str, object]) -> None:
        super().__init__("not json")
        self.repaired = repaired

    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str:
        self.prompts.append(prompt)
        assert Path(image_path).name == "01-method.png"
        assert temperature == 0.0
        return "not json" if len(self.prompts) == 1 else json.dumps(self.repaired)


def _manifest(tmp_path: Path) -> NativePaperDeckManifest:
    images = tmp_path / "images"
    prompts = tmp_path / "prompts"
    images.mkdir()
    prompts.mkdir()
    Image.new("RGB", (640, 360), "white").save(images / "01-method.png")
    (prompts / "01-method.md").write_text(
        "Show Instruction Backtranslation and Figure 2.", encoding="utf-8"
    )
    slide = NativePaperDeckSlide(
        id="paper_deck_slide_001",
        order=1,
        image_path="images/01-method.png",
        image_hash="a" * 64,
        pdf_page_no=1,
        title_hint="Instruction Backtranslation",
        role="method",
        message="Explain Instruction Backtranslation",
        visual_intent="Show Figure 2 method pipeline",
        planned_text=["Accuracy 81.4%"],
        evidence_hint="Paper Figure 2",
        source_visual_hint="Figure 2",
        prompt_path="prompts/01-method.md",
    )
    return NativePaperDeckManifest(
        provider="native_paper_deck",
        style_preset="journal-minimal",
        language="en",
        slide_count=1,
        pdf_path="presentation.pdf",
        slides=[slide],
    )


def test_observes_final_slide_without_promoting_observations_to_facts(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    paper_content = tmp_path / "paper_content.md"
    paper_content.write_text(
        "Figure 2 reports Accuracy 81.4%. The method is Instruction Backtranslation.",
        encoding="utf-8",
    )
    vision = FakeVision(
        {
            "visible_title": "Instruction Backtranslation",
            "visible_text": ["A method pipeline", "Accuracy 81.4%", "Claimed 72.1%"],
            "visual_summary": "Figure 2 shows the Instruction Backtranslation pipeline.",
            "figure_labels": ["Figure 2"],
            "quantitative_mentions": ["81.4%", "72.1%"],
            "formula_mentions": ["x = y + 1"],
            "page_type": "method",
            "detected_warnings": [],
        }
    )

    observations = FinalPaperDeckPageAnalyzer(vision=vision, ocr=FakeOCR()).analyze(
        manifest,
        workspace=tmp_path,
        paper_content_path=paper_content,
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.slide_id == "paper_deck_slide_001"
    assert observation.page_type == "method"
    assert "Figure 2" in observation.figure_labels
    assert "81.4%" in observation.quantitative_mentions
    assert "x = y + 1" in observation.formula_mentions
    assert "unverified_quantitative_mention:72.1%" in observation.detected_warnings
    assert "unverified_quantitative_mention:81.4%" not in observation.detected_warnings
    assert "not factual evidence" in vision.prompts[0]


def test_rejects_non_json_vision_observation(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    paper_content = tmp_path / "paper_content.md"
    paper_content.write_text("paper", encoding="utf-8")
    analyzer = FinalPaperDeckPageAnalyzer(vision=FakeVision("not json"), ocr=FakeOCR())

    with pytest.raises(FinalPaperDeckPageAnalysisError, match="not a JSON"):
        analyzer.analyze(
            manifest,
            workspace=tmp_path,
            paper_content_path=paper_content,
        )
    assert len(analyzer.vision.prompts) == 2


def test_repairs_non_json_vision_observation_once(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    paper_content = tmp_path / "paper_content.md"
    paper_content.write_text("Figure 2 reports Accuracy 81.4%.", encoding="utf-8")
    vision = RepairingVision(
        {
            "visible_title": "Instruction Backtranslation",
            "visible_text": ["Accuracy 81.4%"],
            "visual_summary": "A method pipeline with Figure 2.",
            "figure_labels": ["Figure 2"],
            "quantitative_mentions": ["81.4%"],
            "formula_mentions": [],
            "page_type": "method",
            "detected_warnings": [],
        }
    )

    observations = FinalPaperDeckPageAnalyzer(vision=vision, ocr=FakeOCR()).analyze(
        manifest,
        workspace=tmp_path,
        paper_content_path=paper_content,
    )

    assert len(vision.prompts) == 2
    assert "REPAIR_FINAL_PAPER_DECK_PAGE_OBSERVATION_V1" in vision.prompts[1]
    assert observations[0].page_type == "method"


def test_ignores_numeric_gibberish_found_only_by_ocr(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    paper_content = tmp_path / "paper_content.md"
    paper_content.write_text("The slide describes a qualitative method.", encoding="utf-8")

    class NumericGibberishOCR:
        def extract_text(self, image_path: Path) -> str:
            return "自蒸馏：分解—执行—组合—验证\n538 HUT\nSFT"

    vision = FakeVision(
        {
            "visible_title": "自蒸馏：分解—执行—组合—验证",
            "visible_text": ["分解", "执行", "组合", "验证", "SFT"],
            "visual_summary": "A qualitative process diagram with no numbers visible.",
            "figure_labels": [],
            "quantitative_mentions": [],
            "formula_mentions": [],
            "page_type": "method",
            "detected_warnings": [],
        }
    )

    observations = FinalPaperDeckPageAnalyzer(
        vision=vision,
        ocr=NumericGibberishOCR(),
    ).analyze(
        manifest,
        workspace=tmp_path,
        paper_content_path=paper_content,
    )

    assert observations[0].quantitative_mentions == []
    assert "538 HUT" in observations[0].visible_text
    assert not any(
        warning.startswith("unverified_quantitative_mention:")
        for warning in observations[0].detected_warnings
    )
