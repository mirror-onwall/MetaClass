from metaclass.modules.presentation.codex_provider import CodexPPTProvider
from metaclass.modules.presentation.providers import PresentonPPTProvider
from metaclass.modules.presentation.schemas import (
    PresentationPlan,
    SlideElement,
    SlideElementStyle,
    SlidePlan,
)
from metaclass.modules.presentation.themes import (
    apply_presentation_theme,
    get_presentation_theme,
    list_presentation_themes,
)


def make_plan() -> PresentationPlan:
    return PresentationPlan(
        id="plan_theme",
        content_id="content_theme",
        title="Theme contract",
        slides=[
            SlidePlan(
                id="slide_theme",
                order=1,
                source_section_ids=["section_theme"],
                title="Exact title",
                key_points=["Exact point."],
                speaker_script="Bound speaker script.",
                suggested_visual="A restrained comparison.",
                background="12365A",
                elements=[
                    SlideElement(
                        type="text",
                        x=0.1,
                        y=0.1,
                        w=0.8,
                        h=0.15,
                        text="Exact title",
                        style=SlideElementStyle(
                            color="FFFFFF",
                            fill="12365A",
                            line_color="2E75B6",
                        ),
                    )
                ],
            )
        ],
    )


def test_theme_registry_exposes_distinct_visual_options() -> None:
    options = list_presentation_themes()

    assert len(options) >= 6
    assert len({option.id for option in options}) == len(options)
    assert options[0].id == "academic_blue"
    assert all(option.colors["cover"] for option in options)
    assert all(option.colors["accent"] for option in options)


def test_applying_theme_preserves_immutable_plan_copy() -> None:
    plan = make_plan()
    theme = get_presentation_theme("scholar_green")

    themed = apply_presentation_theme(plan, theme)

    assert themed.slides[0].title == plan.slides[0].title
    assert themed.slides[0].key_points == plan.slides[0].key_points
    assert themed.slides[0].speaker_script == plan.slides[0].speaker_script
    assert themed.slides[0].background == theme.palette.board
    style = themed.slides[0].elements[0].style
    assert style.color == theme.palette.chalk
    assert style.fill == theme.palette.board
    assert style.line_color == theme.palette.amber


def test_codex_contract_uses_only_the_selected_theme() -> None:
    theme = get_presentation_theme("deep_technology")

    contract = CodexPPTProvider._structured_design_contract(theme)

    assert f"First background must be {theme.palette.board}" in contract
    assert f"must be {theme.palette.paper}" in contract
    assert theme.style_direction in contract
    assert '"id": "deep_technology"' in contract
    assert "contrast must be at least 4.5:1" in contract
    assert "Do not add unlabeled decorative blobs" in contract
    assert "supplied visual_payload" in contract


def test_presenton_fallback_receives_the_selected_theme() -> None:
    theme = get_presentation_theme("creative_coral")
    provider = PresentonPPTProvider(
        base_url="https://example.invalid",
        api_key="test-key",
        adapter=object(),
    )

    payload = provider.build_payload(make_plan(), theme)

    assert '"id": "creative_coral"' in payload["instructions"]
    assert theme.style_direction in payload["instructions"]
