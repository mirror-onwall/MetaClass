from __future__ import annotations

from dataclasses import dataclass

from metaclass.modules.presentation.brand_palette import (
    BRAND_PALETTE,
    PresentationBrandPalette,
    nearest_brand_color,
)
from metaclass.modules.presentation.schemas import (
    DEFAULT_PPT_THEME_ID,
    PPTThemeOption,
    PresentationPlan,
)


@dataclass(frozen=True)
class PresentationTheme:
    id: str
    name: str
    description: str
    style_direction: str
    palette: PresentationBrandPalette
    rules: tuple[str, ...]

    def prompt_payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "style_direction": self.style_direction,
            "colors": {
                "cover_background": self.palette.board,
                "content_background": self.palette.paper,
                "primary": self.palette.ink,
                "accent": self.palette.amber,
                "accent_soft": self.palette.amber_soft,
                "secondary": self.palette.mint,
                "muted": self.palette.muted,
                "warning": self.palette.red,
                "cover_text": self.palette.chalk,
            },
            "allowed_colors": list(dict.fromkeys(self.palette.allowed_colors)),
            "design_system": {
                "grid": "12-column editorial grid with consistent outer margins and baseline rhythm",
                "typography": "one display hierarchy and one body hierarchy; large titles, readable body, restrained bold",
                "composition": "one dominant semantic exhibit per slide with intentional whitespace",
                "shape_language": "few purposeful shapes; avoid UI dashboards, empty cards, and decorative blobs",
                "continuity": "reuse title rhythm, spacing scale, and at most two accent motifs across the deck",
            },
            "rules": list(self.rules),
        }

    def public_option(self) -> PPTThemeOption:
        return PPTThemeOption(
            id=self.id,
            name=self.name,
            description=self.description,
            style_direction=self.style_direction,
            colors={
                "cover": self.palette.board,
                "background": self.palette.paper,
                "text": self.palette.ink,
                "accent": self.palette.amber,
                "soft": self.palette.amber_soft,
                "secondary": self.palette.mint,
            },
        )


PRESENTATION_THEMES: tuple[PresentationTheme, ...] = (
    PresentationTheme(
        id=DEFAULT_PPT_THEME_ID,
        name="学术蓝白",
        description="清晰、克制，适合课程讲解与研究汇报",
        style_direction="restrained academic editorial with crisp blue hierarchy",
        palette=PresentationBrandPalette(),
        rules=(
            "Use navy for titles and primary hierarchy.",
            "Use pale blue fields sparingly for grouping and emphasis.",
            "Reserve red only for warnings or misconceptions.",
        ),
    ),
    PresentationTheme(
        id="scholar_green",
        name="书院青绿",
        description="沉静自然，适合人文、地理与通识课程",
        style_direction="quiet scholarly field notes with botanical green structure",
        palette=PresentationBrandPalette(
            wall="315C4C",
            wall_deep="183B32",
            board="244B3D",
            chalk="FFFDF7",
            muted="71827A",
            amber="B5863B",
            amber_soft="EAF1E7",
            red="B85A54",
            mint="5F8F79",
            paper="FFFDF8",
            ink="21392F",
        ),
        rules=(
            "Use forest green for hierarchy and warm brass only as a small accent.",
            "Favor calm open compositions inspired by scholarly field notes.",
            "Reserve muted red only for warnings or misconceptions.",
        ),
    ),
    PresentationTheme(
        id="warm_classroom",
        name="暖调课堂",
        description="亲切、有温度，适合教学活动与案例分享",
        style_direction="warm classroom editorial with terracotta and parchment cues",
        palette=PresentationBrandPalette(
            wall="8A513B",
            wall_deep="582F25",
            board="713C2D",
            chalk="FFF9F1",
            muted="8C776C",
            amber="D48743",
            amber_soft="F6E5D2",
            red="B7473E",
            mint="C46C4A",
            paper="FFFBF5",
            ink="4A2D25",
        ),
        rules=(
            "Use terracotta as the main hierarchy color and amber for emphasis.",
            "Keep content pages bright and generous, like annotated classroom paper.",
            "Avoid excessive decorative cards or playful stickers.",
        ),
    ),
    PresentationTheme(
        id="editorial_ink",
        name="墨色编辑",
        description="理性、极简，适合论文答辩与正式陈述",
        style_direction="precise monochrome editorial with one cobalt signal color",
        palette=PresentationBrandPalette(
            wall="33383F",
            wall_deep="171A1F",
            board="23272D",
            chalk="FFFFFF",
            muted="747B85",
            amber="315E9E",
            amber_soft="E7EBF0",
            red="B84D55",
            mint="5F7896",
            paper="FFFFFF",
            ink="22262B",
        ),
        rules=(
            "Use near-black typography with one disciplined cobalt accent.",
            "Favor strong alignment, thin rules, and editorial whitespace.",
            "Do not turn the deck into a dashboard or a grid of UI cards.",
        ),
    ),
    PresentationTheme(
        id="deep_technology",
        name="深海科技",
        description="冷静、前沿，适合技术原理与工程方案",
        style_direction="deep technical atmosphere with cyan signal paths and precise geometry",
        palette=PresentationBrandPalette(
            wall="183A4A",
            wall_deep="081B25",
            board="0C2836",
            chalk="F3FBFF",
            muted="6E8793",
            amber="168FB5",
            amber_soft="DDF2F7",
            red="C55360",
            mint="48A8B7",
            paper="F7FBFD",
            ink="12313F",
        ),
        rules=(
            "Use dark ocean blue on the cover and cyan for signal paths and emphasis.",
            "Favor precise geometry and directional flow over decorative panels.",
            "Keep content-slide backgrounds bright for classroom readability.",
        ),
    ),
    PresentationTheme(
        id="creative_coral",
        name="珊瑚创意",
        description="鲜明、有活力，适合创意表达与成果展示",
        style_direction="confident creative editorial with coral gestures and deep plum anchors",
        palette=PresentationBrandPalette(
            wall="744354",
            wall_deep="3D2330",
            board="563141",
            chalk="FFF8F5",
            muted="8A747D",
            amber="E36F5C",
            amber_soft="FBE5DF",
            red="B9464D",
            mint="C98A76",
            paper="FFFAF8",
            ink="452B35",
        ),
        rules=(
            "Use coral as a confident gesture, not as a full-slide fill on content pages.",
            "Anchor layouts with deep plum typography and asymmetric composition.",
            "Keep decorative elements sparse so the exact plan content remains dominant.",
        ),
    ),
)

_THEMES_BY_ID = {theme.id: theme for theme in PRESENTATION_THEMES}


def get_presentation_theme(theme_id: str | None = None) -> PresentationTheme:
    resolved_id = theme_id or DEFAULT_PPT_THEME_ID
    try:
        return _THEMES_BY_ID[resolved_id]
    except KeyError as exc:
        raise ValueError(f"Unknown PPT theme: {resolved_id}") from exc


def list_presentation_themes() -> list[PPTThemeOption]:
    return [theme.public_option() for theme in PRESENTATION_THEMES]


def apply_presentation_theme(
    plan: PresentationPlan,
    theme: PresentationTheme,
) -> PresentationPlan:
    """Retarget an existing declarative scene without touching visible copy."""

    token_names = (
        "wall",
        "wall_deep",
        "board",
        "chalk",
        "muted",
        "amber",
        "amber_soft",
        "red",
        "mint",
        "paper",
        "ink",
    )
    base_token_map = {
        getattr(BRAND_PALETTE, name): getattr(theme.palette, name) for name in token_names
    }
    color_token_map = {
        **base_token_map,
        BRAND_PALETTE.chalk: theme.palette.chalk,
    }
    fill_token_map = {
        **base_token_map,
        BRAND_PALETTE.paper: theme.palette.paper,
    }

    def retarget(
        value: str | None,
        fallback: str,
        token_map: dict[str, str],
    ) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        return token_map.get(
            normalized,
            nearest_brand_color(
                normalized,
                fallback=fallback,
                palette=theme.palette,
            ),
        )

    slides = []
    for index, slide in enumerate(plan.slides):
        elements = []
        for element in slide.elements:
            style = element.style.model_copy(
                update={
                    "color": retarget(
                        element.style.color,
                        theme.palette.ink,
                        color_token_map,
                    ),
                    "fill": retarget(
                        element.style.fill,
                        theme.palette.paper,
                        fill_token_map,
                    ),
                    "line_color": retarget(
                        element.style.line_color,
                        theme.palette.amber,
                        base_token_map,
                    ),
                }
            )
            elements.append(element.model_copy(update={"style": style}))
        slides.append(
            slide.model_copy(
                update={
                    "background": (theme.palette.board if index == 0 else theme.palette.paper),
                    "elements": elements,
                }
            )
        )
    return plan.model_copy(update={"slides": slides})
