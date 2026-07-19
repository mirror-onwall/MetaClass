from __future__ import annotations

from dataclasses import dataclass

from metaclass.modules.presentation.schemas import SlideElement


@dataclass(frozen=True)
class PresentationBrandPalette:
    # Restrained academic blue/white system.  Legacy token names are retained
    # because they are part of the scene contract, but all values now belong to
    # one coherent blue family.
    wall: str = "163A63"
    wall_deep: str = "0E2742"
    board: str = "12365A"
    chalk: str = "FFFFFF"
    muted: str = "6F8299"
    amber: str = "2E75B6"
    amber_soft: str = "DCEBFA"
    red: str = "C94B5B"
    mint: str = "4F8FCB"
    paper: str = "FFFFFF"
    ink: str = "17324D"

    @property
    def allowed_colors(self) -> tuple[str, ...]:
        return (
            self.wall,
            self.wall_deep,
            self.board,
            self.chalk,
            self.muted,
            self.amber,
            self.amber_soft,
            self.red,
            self.mint,
            self.paper,
            self.ink,
        )

    def prompt_payload(self) -> dict:
        return {
            "name": "academic_blue_white",
            "usage": "navy cover; white content pages; blue only for hierarchy and emphasis",
            "tokens": {
                "board": self.board,
                "chalk": self.chalk,
                "paper": self.paper,
                "ink": self.ink,
                "amber": self.amber,
                "amber_soft": self.amber_soft,
                "muted": self.muted,
                "mint": self.mint,
                "red": self.red,
            },
            "rules": [
                "Use white for every content-slide background.",
                "Use navy for titles and primary text, and mid-blue for emphasis.",
                "Use pale blue for cards; reserve red only for warnings or misconceptions.",
                "Do not introduce orange, yellow, purple, green, or unrelated colors.",
            ],
        }


BRAND_PALETTE = PresentationBrandPalette()


def nearest_brand_color(value: str | None, *, fallback: str) -> str | None:
    if value is None:
        return None
    try:
        source = tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except (TypeError, ValueError):
        return fallback

    def distance(candidate: str) -> int:
        rgb = tuple(int(candidate[index : index + 2], 16) for index in (0, 2, 4))
        return sum((left - right) ** 2 for left, right in zip(source, rgb, strict=True))

    return min(BRAND_PALETTE.allowed_colors, key=distance)


def apply_brand_palette(element: SlideElement) -> SlideElement:
    style = element.style
    normalized = style.model_copy(
        update={
            "color": nearest_brand_color(style.color, fallback=BRAND_PALETTE.ink),
            "fill": nearest_brand_color(style.fill, fallback=BRAND_PALETTE.paper),
            "line_color": nearest_brand_color(
                style.line_color, fallback=BRAND_PALETTE.amber
            ),
        }
    )
    return element.model_copy(update={"style": normalized})
