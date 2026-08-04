import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pptx import Presentation
from pptx.util import Inches

from metaclass.modules.presentation.codex_provider import (
    CodexGenerationError,
    CodexPPTProvider,
)
from metaclass.modules.presentation.providers import (
    FallbackPPTProvider,
    PresentonPPTProvider,
)
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PresentationPlan,
    SlideElement,
    SlideElementStyle,
    SlidePlan,
)
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.presentation.themes import get_presentation_theme


def make_plan() -> PresentationPlan:
    return PresentationPlan(
        id="plan_codex",
        content_id="content_codex",
        title="空间分析",
        slides=[
            SlidePlan(
                id="slide_001",
                order=1,
                source_section_ids=["section_001"],
                title="空间自相关保留地理位置关系",
                key_points=[
                    "传统相关分析适用于非空间观测。",
                    "空间自相关同时考虑观测值与地理位置。",
                ],
                speaker_script="解释两类相关分析之间的区别。",
                suggested_visual="使用左右对照和空间点阵。",
                visual_payload=["空间点阵关系示意"],
            )
        ],
    )


def write_deck(path: Path, values: list[str]) -> None:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    for index, value in enumerate(values):
        textbox = slide.shapes.add_textbox(
            Inches(0.8), Inches(0.5 + index), Inches(11.5), Inches(0.7)
        )
        textbox.text_frame.text = value
    deck.save(path)


def test_codex_provider_accepts_only_an_exact_plan_deck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_plan()
    adapter = Mock()
    adapter.prepare_external_pptx.return_value = Mock(id="artifact_codex")
    provider = CodexPPTProvider(adapter=adapter, repair_attempts=1)

    def fake_execute(*, workspace, output_dir, prompt, attempt):
        write_deck(
            workspace / "deck.pptx",
            [plan.slides[0].title, *plan.slides[0].key_points],
        )
        return {
            "status": "completed",
            "deck_path": "deck.pptx",
            "summary": "generated",
        }

    monkeypatch.setattr(provider, "_execute_codex", fake_execute)

    artifact = provider.prepare_request(
        plan=plan,
        job_id="job_codex",
        output_dir=tmp_path,
    )

    assert artifact.id == "artifact_codex"
    assert (tmp_path / "deck.pptx").exists()
    call = adapter.prepare_external_pptx.call_args.kwargs
    assert call["provider_name"] == "codex"
    assert call["provider_metadata"]["validation"]["status"] == "passed"
    assert call["provider_metadata"]["attempt_count"] == 1
    assert call["provider_metadata"]["content_contract"].startswith("exact")


def test_codex_structured_design_is_safely_compiled_to_pptx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_plan()
    adapter = PPTSkillAdapter()
    monkeypatch.setattr(adapter, "_render_slide_images", lambda *args, **kwargs: [])
    package_spy = Mock(wraps=adapter.prepare_external_pptx)
    monkeypatch.setattr(adapter, "prepare_external_pptx", package_spy)
    provider = CodexPPTProvider(adapter=adapter, repair_attempts=1)
    attempts = []

    def style(*, color: str, fill: str, bold: bool = False, size: int = 20):
        return {
            "font_size": size,
            "bold": bold,
            "color": color,
            "fill": fill,
            "line_color": "2E75B6",
            "line_width": 1,
            "align": "left",
            "valign": "middle",
            "opacity": 100,
        }

    def fake_execute(*, workspace, output_dir, prompt, attempt):
        attempts.append(attempt)
        return {
            "status": "completed",
            "summary": "structured design",
            "slides": [
                {
                    "slide_id": plan.slides[0].id,
                    "background": "12365A",
                    "elements": [
                        {
                            "type": "text",
                            "x": 0.1,
                            "y": 0.1,
                            "w": 0.8,
                            "h": 0.03 if attempt == 1 else 0.16,
                            "z": 10,
                            "text": plan.slides[0].title,
                            "shape": "rectangle",
                            "style": style(color="FFFFFF", fill="12365A", bold=True, size=32),
                        },
                        {
                            "type": "text",
                            "x": 0.1,
                            "y": 0.36,
                            "w": 0.36,
                            "h": 0.24,
                            "z": 11,
                            "text": plan.slides[0].key_points[0],
                            "shape": "rectangle",
                            "style": style(color="17324D", fill="DCEBFA"),
                        },
                        {
                            "type": "text",
                            "x": 0.54,
                            "y": 0.36,
                            "w": 0.36,
                            "h": 0.24,
                            "z": 12,
                            "text": plan.slides[0].key_points[1],
                            "shape": "rectangle",
                            "style": style(color="17324D", fill="DCEBFA"),
                        },
                        {
                            "type": "shape",
                            "x": 0.08,
                            "y": 0.33,
                            "w": 0.4,
                            "h": 0.3,
                            "z": 1,
                            "text": "",
                            "shape": "rounded_rectangle",
                            "style": style(color="17324D", fill="DCEBFA"),
                        },
                        {
                            "type": "shape",
                            "x": 0.52,
                            "y": 0.33,
                            "w": 0.4,
                            "h": 0.3,
                            "z": 1,
                            "text": "",
                            "shape": "rounded_rectangle",
                            "style": style(color="17324D", fill="DCEBFA"),
                        },
                    ],
                }
            ],
        }

    monkeypatch.setattr(provider, "_execute_codex", fake_execute)

    artifact = provider.prepare_request(
        plan=plan,
        job_id="job_codex_structured",
        output_dir=tmp_path,
    )

    assert Path(artifact.pptx_path).exists()
    request = json.loads(Path(artifact.skill_request_path).read_text(encoding="utf-8"))
    assert request["provider"] == "codex"
    assert request["provider_metadata"]["generation_mode"] == ("structured-design-safe-render")
    assert attempts == [1, 2]
    assert request["provider_metadata"]["repair_attempts_used"] == 1
    preview_plan = package_spy.call_args.kwargs["preview_plan"]
    assert preview_plan is not None
    assert preview_plan.slides[0].speaker_script == plan.slides[0].speaker_script
    assert [
        element.text for element in preview_plan.slides[0].elements if element.type == "text"
    ] == [plan.slides[0].title, *plan.slides[0].key_points]

    deck = Presentation(artifact.pptx_path)
    text_shapes = {
        shape.text_frame.text: shape for shape in deck.slides[0].shapes if shape.has_text_frame
    }
    point_box = text_shapes[plan.slides[0].key_points[0]]
    assert str(point_box.fill.fore_color.rgb) == "DCEBFA"
    assert str(point_box.line.color.rgb) == "2E75B6"


def test_scene_renderer_applies_text_fill_line_and_opacity(tmp_path: Path) -> None:
    plan = make_plan()
    slide = plan.slides[0].model_copy(
        update={
            "elements": [
                SlideElement(
                    type="text",
                    x=0.1,
                    y=0.1,
                    w=0.8,
                    h=0.2,
                    text=plan.slides[0].title,
                    style=SlideElementStyle(
                        font_size=32,
                        bold=True,
                        color="FFFFFF",
                        fill="12365A",
                        line_color="2E75B6",
                        line_width=2,
                        opacity=40,
                    ),
                )
            ]
        }
    )
    rendered_plan = plan.model_copy(update={"slides": [slide]})
    destination = tmp_path / "scene-style.pptx"

    PPTSkillAdapter().render_declarative_pptx(rendered_plan, destination)

    deck = Presentation(destination)
    textbox = deck.slides[0].shapes[0]
    assert str(textbox.fill.fore_color.rgb) == "12365A"
    assert str(textbox.line.color.rgb) == "2E75B6"
    assert textbox._element.xml.count('a:alpha val="40000"') >= 3


def test_codex_rejects_low_contrast_text_before_render() -> None:
    plan = make_plan()

    def style(color: str, fill: str, *, size: int = 20, bold: bool = False):
        return {
            "font_size": size,
            "bold": bold,
            "color": color,
            "fill": fill,
            "line_color": fill,
            "line_width": 0,
            "align": "left",
            "valign": "middle",
            "opacity": 100,
        }

    result = {
        "status": "completed",
        "summary": "low contrast",
        "slides": [
            {
                "slide_id": plan.slides[0].id,
                "background": "12365A",
                "elements": [
                    {
                        "type": "text",
                        "x": 0.1,
                        "y": 0.08,
                        "w": 0.8,
                        "h": 0.14,
                        "z": 10,
                        "text": plan.slides[0].title,
                        "shape": "rectangle",
                        "style": style("12365A", "12365A", size=32, bold=True),
                    },
                    {
                        "type": "text",
                        "x": 0.1,
                        "y": 0.3,
                        "w": 0.8,
                        "h": 0.18,
                        "z": 11,
                        "text": plan.slides[0].key_points[0],
                        "shape": "rectangle",
                        "style": style("FFFFFF", "12365A"),
                    },
                    {
                        "type": "text",
                        "x": 0.1,
                        "y": 0.58,
                        "w": 0.8,
                        "h": 0.18,
                        "z": 12,
                        "text": plan.slides[0].key_points[1],
                        "shape": "rectangle",
                        "style": style("FFFFFF", "12365A"),
                    },
                    {
                        "type": "shape",
                        "x": 0.07,
                        "y": 0.28,
                        "w": 0.86,
                        "h": 0.5,
                        "z": 1,
                        "text": "",
                        "shape": "rounded_rectangle",
                        "style": style("FFFFFF", "12365A"),
                    },
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="contrast"):
        CodexPPTProvider._design_plan_from_result(
            plan,
            result,
            get_presentation_theme(),
        )


def test_codex_design_payload_includes_visual_plan_but_omits_script() -> None:
    plan = make_plan()

    payload = json.loads(CodexPPTProvider._design_payload_json(plan))

    slide = payload["slides"][0]
    assert slide["layout_id"] == plan.slides[0].layout_id
    assert slide["visual_payload"] == plan.slides[0].visual_payload
    assert slide["suggested_visual"] == plan.slides[0].suggested_visual
    assert "speaker_script" not in slide


def test_codex_provider_repairs_a_contract_violation_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_plan()
    adapter = Mock()
    adapter.prepare_external_pptx.return_value = Mock(id="artifact_codex")
    provider = CodexPPTProvider(adapter=adapter, repair_attempts=1)
    attempts = []

    def fake_execute(*, workspace, output_dir, prompt, attempt):
        attempts.append(attempt)
        title = "被改写的标题" if attempt == 1 else plan.slides[0].title
        write_deck(workspace / "deck.pptx", [title, *plan.slides[0].key_points])
        return {
            "status": "completed",
            "deck_path": "deck.pptx",
            "summary": f"attempt {attempt}",
        }

    monkeypatch.setattr(provider, "_execute_codex", fake_execute)

    provider.prepare_request(
        plan=plan,
        job_id="job_codex_repair",
        output_dir=tmp_path,
    )

    metadata = adapter.prepare_external_pptx.call_args.kwargs["provider_metadata"]
    assert attempts == [1, 2]
    assert metadata["repair_attempts_used"] == 1
    assert "content mismatch" in metadata["validation_errors"][0]


def test_codex_provider_rejects_punctuation_or_case_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_plan()
    provider = CodexPPTProvider(adapter=Mock(), repair_attempts=0)

    def fake_execute(*, workspace, output_dir, prompt, attempt):
        changed_points = [plan.slides[0].key_points[0].rstrip("。")]
        changed_points.extend(plan.slides[0].key_points[1:])
        write_deck(workspace / "deck.pptx", [plan.slides[0].title, *changed_points])
        return {
            "status": "completed",
            "deck_path": "deck.pptx",
            "summary": "changed punctuation",
        }

    monkeypatch.setattr(provider, "_execute_codex", fake_execute)

    with pytest.raises(CodexGenerationError, match="immutable PresentationPlan contract"):
        provider.prepare_request(
            plan=plan,
            job_id="job_codex_invalid",
            output_dir=tmp_path,
        )


def test_codex_authentication_failure_is_reported_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "metaclass.modules.presentation.codex_provider.subprocess.run",
        lambda *args, **kwargs: Mock(returncode=1),
    )

    with pytest.raises(CodexGenerationError, match="CODEX_API_KEY"):
        CodexPPTProvider._ensure_authenticated("codex")


def test_codex_falls_back_to_presenton_and_records_the_reason(tmp_path: Path) -> None:
    plan = make_plan()
    primary = Mock()
    primary.prepare_request.side_effect = CodexGenerationError("Codex timed out")
    request_path = tmp_path / "skill_request.json"
    request_path.write_text(
        json.dumps({"provider": "presenton"}),
        encoding="utf-8",
    )
    fallback = Mock()
    fallback.prepare_request.return_value = PPTArtifact(
        id="artifact_presenton",
        job_id="job_fallback",
        presentation_plan_id=plan.id,
        pptx_path=str(tmp_path / "deck.pptx"),
        skill_request_path=str(request_path),
    )
    provider = FallbackPPTProvider(
        primary=primary,
        fallback=fallback,
        primary_name="codex",
        fallback_name="presenton",
        fallback_exceptions=(CodexGenerationError,),
    )

    artifact = provider.prepare_request(
        plan=plan,
        job_id="job_fallback",
        output_dir=tmp_path,
    )

    assert artifact.id == "artifact_presenton"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["fallback"] == {
        "from": "codex",
        "to": "presenton",
        "reason": "Codex timed out",
    }


def test_external_provider_preserves_speaker_scripts_by_slide_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_plan()
    deck_path = tmp_path / "deck.pptx"
    write_deck(deck_path, [plan.slides[0].title, *plan.slides[0].key_points])
    adapter = PPTSkillAdapter()
    monkeypatch.setattr(adapter, "_render_slide_images", lambda *args, **kwargs: [])

    adapter.prepare_external_pptx(
        plan=plan,
        job_id="job_scripts",
        output_dir=tmp_path,
        pptx_path=deck_path,
        provider_name="codex",
        provider_metadata={"validation": {"status": "passed"}},
    )

    scripts = json.loads((tmp_path / "speaker_scripts.json").read_text(encoding="utf-8"))
    assert scripts["presentation_plan_id"] == plan.id
    assert scripts["slides"] == [
        {
            "slide_id": plan.slides[0].id,
            "order": plan.slides[0].order,
            "title": plan.slides[0].title,
            "speaker_script": plan.slides[0].speaker_script,
        }
    ]


def test_codex_runtime_is_primary_and_presenton_is_the_only_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metaclass.core.application import build_services
    from metaclass.core.config import settings

    monkeypatch.setattr(settings, "ppt_provider", "codex")
    monkeypatch.setattr(settings, "presenton_api_key", "test-key")
    services = build_services(tmp_path, force_fake_llm=False)
    try:
        provider = services.presentations.ppt_adapter
        assert isinstance(provider, FallbackPPTProvider)
        assert isinstance(provider.primary, CodexPPTProvider)
        assert isinstance(provider.fallback, PresentonPPTProvider)
        assert provider.fallback_name == "presenton"
    finally:
        services.database.dispose()
