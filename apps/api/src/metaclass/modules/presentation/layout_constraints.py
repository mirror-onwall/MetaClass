from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LayoutConstraints:
    canvas_margin_x: float = 0.055
    canvas_margin_y: float = 0.055
    title_top: float = 0.05
    title_bottom: float = 0.20
    content_top: float = 0.22
    content_bottom: float = 0.94
    min_content_gap: float = 0.025
    title_min_font_size: float = 28
    body_min_font_size: float = 18
    citation_min_font_size: float = 11
    max_body_points: int = 5
    max_body_characters: int = 120

    def prompt_payload(self) -> dict:
        return asdict(self)


DEFAULT_LAYOUT_CONSTRAINTS = LayoutConstraints()


ACADEMIC_LAYOUT_GUIDANCE = {
    "title_style": "action_title",
    "argument_per_slide": 1,
    "primary_exhibits_per_slide": 1,
    "evidence_position": "left",
    "interpretation_position": "right",
    "prefer_charts_over_tables_for_trends": True,
    "require_so_what_annotation": True,
    "visual_style": "white_canvas_single_font_restrained_color",
}
