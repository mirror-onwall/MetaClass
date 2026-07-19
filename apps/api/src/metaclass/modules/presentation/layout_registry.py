from __future__ import annotations

from dataclasses import dataclass
import re

from metaclass.modules.presentation.schemas import SlideElement, SlidePlan
from metaclass.modules.presentation.layout_constraints import DEFAULT_LAYOUT_CONSTRAINTS


@dataclass(frozen=True)
class LayoutSpec:
    id: str
    family: str
    uses: tuple[str, ...]
    density: str
    max_points: int
    description: str


# Project-owned constrained layout vocabulary.  These are semantic skeletons,
# not copies of any external template implementation.
LAYOUT_REGISTRY: tuple[LayoutSpec, ...] = (
    LayoutSpec("hero_minimal", "hero", ("cover", "opening"), "sparse", 1, "Minimal title and subtitle"),
    LayoutSpec("hero_statement", "hero", ("opening", "conclusion"), "sparse", 2, "Dominant claim with supporting line"),
    LayoutSpec("split_left", "split", ("concept", "example"), "medium", 4, "Dominant statement left, evidence right"),
    LayoutSpec("split_right", "split", ("concept", "example"), "medium", 4, "Evidence left, dominant statement right"),
    LayoutSpec("focus_rail", "spotlight", ("concept", "formula", "conclusion"), "sparse", 3, "Large focal statement with interpretation rail"),
    LayoutSpec("quote_field", "spotlight", ("definition", "conclusion"), "sparse", 3, "Large definition with small implications"),
    LayoutSpec("sequence_horizontal", "process", ("process", "method"), "medium", 5, "Horizontal sequenced flow"),
    LayoutSpec("sequence_vertical", "process", ("process", "method"), "medium", 4, "Vertical sequenced flow"),
    LayoutSpec("comparison_split", "comparison", ("comparison", "misconception"), "medium", 6, "Two flat comparison fields"),
    LayoutSpec("before_after", "comparison", ("comparison", "example"), "medium", 4, "Before and after contrast"),
    LayoutSpec("timeline_alternating", "timeline", ("timeline", "history", "process"), "medium", 5, "Alternating timeline"),
    LayoutSpec("ladder", "hierarchy", ("hierarchy", "method", "process"), "medium", 5, "Progressive ladder"),
    LayoutSpec("constellation", "relationship", ("concept", "relationship"), "medium", 5, "Central concept and satellites"),
    LayoutSpec("matrix", "comparison", ("comparison", "classification"), "dense", 4, "Four-quadrant classification"),
    LayoutSpec("evidence_strip", "evidence", ("example", "evidence", "image"), "medium", 4, "Wide evidence region with interpretation strip"),
    LayoutSpec("summary_path", "summary", ("summary", "conclusion"), "medium", 5, "Synthesis path rather than repeated cards"),
)


def registry_prompt_payload() -> list[dict]:
    return [
        {
            "layout_id": item.id,
            "family": item.family,
            "uses": list(item.uses),
            "density": item.density,
            "max_points": item.max_points,
            "description": item.description,
        }
        for item in LAYOUT_REGISTRY
    ]


def select_fallback_layout(slide: SlidePlan, index: int) -> LayoutSpec:
    text = " ".join([slide.title, *slide.key_points, slide.suggested_visual]).lower()
    if index == 0:
        preferred = {"hero"}
    elif re.search(r"总结|结论|回顾|迁移|summary|conclusion", text):
        preferred = {"summary", "spotlight"}
    elif re.search(r"对比|区别|异同|优缺点|误区|versus|compare", text):
        preferred = {"comparison"}
    elif re.search(r"步骤|流程|阶段|算法|迭代|过程|process|step", text):
        preferred = {"process", "timeline", "hierarchy"}
    elif re.search(r"关系|联系|构成|系统|机制|relationship", text):
        preferred = {"relationship", "split"}
    elif re.search(r"例|案例|证据|图像|实验|example|evidence", text):
        preferred = {"evidence", "split"}
    elif re.search(r"公式|定义|核心|关键|formula|definition", text):
        preferred = {"spotlight", "split"}
    else:
        preferred = {"split", "spotlight", "relationship"}

    candidates = [item for item in LAYOUT_REGISTRY if item.family in preferred]
    if not candidates:
        candidates = list(LAYOUT_REGISTRY)
    point_count = len(slide.key_points or slide.visual_payload or [slide.title])
    average_length = sum(
        len(item) for item in (slide.key_points or slide.visual_payload or [slide.title])
    ) / max(1, point_count)
    if average_length > 32:
        roomy = [item for item in LAYOUT_REGISTRY if item.family in {"split", "evidence"}]
        candidates = [*candidates, *[item for item in roomy if item not in candidates]]

    def fit_score(item: LayoutSpec) -> tuple[float, int]:
        overflow_penalty = max(0, point_count - item.max_points) * 100
        narrow_layout_penalty = (
            45
            if average_length > 32
            and item.family in {"process", "timeline", "hierarchy", "relationship", "summary"}
            else 0
        )
        unused_capacity = max(0, item.max_points - point_count) * 2
        variety_tie_break = (LAYOUT_REGISTRY.index(item) - index) % len(LAYOUT_REGISTRY)
        return overflow_penalty + narrow_layout_penalty + unused_capacity, variety_tie_break

    return min(candidates, key=fit_score)


def split_points_for_layout(points: list[str], spec: LayoutSpec) -> list[list[str]]:
    """Preserve every point while respecting both slot and reading capacity."""
    if not points:
        return [[]]
    char_budget = {"sparse": 180, "medium": 300, "dense": 380}[spec.density]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for point in points:
        clean = re.sub(r"\s+", " ", point).strip()
        if current and (
            len(current) >= spec.max_points or current_chars + len(clean) > char_budget
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(clean)
        current_chars += len(clean)
    if current:
        chunks.append(current)
    return chunks


def build_fallback_elements(slide: SlidePlan, spec: LayoutSpec, palette: tuple[str, str, str, str]) -> list[SlideElement]:
    _background, ink, accent, paper = palette
    points = (slide.key_points or slide.visual_payload or [slide.title])[: spec.max_points]
    title = SlideElement(type="text", x=0.065, y=0.05, w=0.87, h=0.14, z=10, text=slide.title,
                         style={"font_size": 28, "bold": True, "color": ink})
    elements = [title]

    def box(x: float, y: float, w: float, h: float, text: str, *, strong: bool = False, align: str = "left") -> None:
        clean_text = re.sub(r"\s+", " ", text).strip()
        base_size = 22 if strong else 20
        estimated_lines = max(1, len(clean_text) / max(6, w * 48))
        available_lines = max(1, h * 7.5 * 72 / (base_size * 1.35))
        font_size = max(
            DEFAULT_LAYOUT_CONSTRAINTS.body_min_font_size,
            min(base_size, base_size * available_lines / estimated_lines),
        )
        elements.append(SlideElement(type="shape", x=x, y=y, w=w, h=h, z=0, shape="rectangle",
                                     style={"fill": accent if strong else paper, "line_color": accent, "line_width": 1}))
        elements.append(SlideElement(type="text", x=x + 0.025, y=y + 0.025, w=w - 0.05, h=h - 0.05, z=2, text=clean_text,
                                     style={"font_size": round(font_size, 1), "bold": strong, "color": "FFFFFF" if strong else ink,
                                            "align": align, "valign": "middle"}))

    family = spec.family
    if family == "hero":
        elements[0] = title.model_copy(update={"x": 0.09, "y": 0.22, "w": 0.82, "h": 0.2,
                                                "style": title.style.model_copy(update={
                                                    "font_size": 42,
                                                    "bold": True,
                                                    "color": ink,
                                                    "align": "center",
                                                })})
        if points:
            elements.append(SlideElement(type="text", x=0.2, y=0.5, w=0.6, h=0.13, z=2, text=points[0],
                                         style={"font_size": 22, "color": accent, "align": "center"}))
    elif family == "split":
        dominant_right = spec.id.endswith("right")
        secondary = points[1:4]
        primary_chars = len(points[0])
        secondary_chars = sum(len(item) for item in secondary)
        primary_w = 0.44 if primary_chars > secondary_chars * 0.75 else 0.36
        secondary_w = 0.82 - primary_w - 0.06
        if dominant_right:
            secondary_x, primary_x = 0.08, 0.08 + secondary_w + 0.06
        else:
            primary_x, secondary_x = 0.08, 0.08 + primary_w + 0.06
        box(primary_x, 0.25, primary_w, 0.55, points[0], strong=True)
        secondary_h = (0.55 - 0.025 * max(0, len(secondary) - 1)) / max(1, len(secondary))
        for i, point in enumerate(secondary):
            box(secondary_x, 0.25 + i * (secondary_h + 0.025), secondary_w, secondary_h, point)
    elif family in {"process", "timeline", "hierarchy", "summary"}:
        vertical = spec.id in {"sequence_vertical", "ladder"}
        count = max(1, len(points))
        for i, point in enumerate(points):
            if vertical:
                x, y, w, h = 0.12 + i * 0.055, 0.23 + i * 0.135, 0.62, 0.115
            else:
                w, h = min(0.17, 0.76 / count), 0.30
                gap = (0.82 - w * count) / max(1, count - 1)
                x, y = 0.09 + i * (w + gap), 0.35 + (0.08 if family == "timeline" and i % 2 else 0)
            if i < count - 1 and not vertical:
                elements.append(SlideElement(type="line", x=x + w, y=y + h / 2, w=max(gap, 0.01), h=0, z=0,
                                             style={"line_color": accent, "line_width": 2}))
            box(x, y, w, h, point, strong=i == 0, align="center")
    elif family == "comparison":
        if spec.id == "matrix":
            for i, point in enumerate(points[:4]):
                row, col = divmod(i, 2)
                box(0.09 + col * 0.42, 0.25 + row * 0.27, 0.37, 0.21, point, strong=i == 0)
        else:
            midpoint = max(1, (len(points) + 1) // 2)
            left = "\n".join(points[:midpoint])
            right = "\n".join(points[midpoint:]) or "另一种视角"
            box(0.08, 0.26, 0.39, 0.5, left, strong=True)
            box(0.53, 0.26, 0.39, 0.5, right)
    elif family == "relationship":
        center = points[0]
        elements.append(SlideElement(type="shape", x=0.38, y=0.36, w=0.24, h=0.24, z=1, shape="oval",
                                     style={"fill": accent, "line_color": accent}))
        elements.append(SlideElement(type="text", x=0.405, y=0.405, w=0.19, h=0.14, z=3, text=center,
                                     style={"font_size": 20, "bold": True, "color": "FFFFFF", "align": "center", "valign": "middle"}))
        positions = [(0.08, 0.27), (0.7, 0.27), (0.08, 0.64), (0.7, 0.64)]
        for point, (x, y) in zip(points[1:], positions, strict=False):
            box(x, y, 0.22, 0.17, point, align="center")
    elif family == "evidence":
        box(0.08, 0.24, 0.58, 0.52, points[0], strong=True)
        box(0.7, 0.24, 0.22, 0.52, "\n".join(points[1:]) or slide.suggested_visual)
    else:  # spotlight
        box(0.09, 0.27, 0.55, 0.44, points[0], strong=True, align="center")
        box(0.69, 0.27, 0.23, 0.44, "\n".join(points[1:]) or slide.suggested_visual)
    return elements
