from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel, utc_now


DEFAULT_PPT_THEME_ID = "academic_blue"


class PPTGenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_SKILL = "waiting_for_skill"
    FINISHED = "finished"
    FAILED = "failed"


class PresentationPlanJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SlideElementStyle(SchemaModel):
    font_size: float = Field(default=18, ge=8, le=72)
    font_role: Literal["sans", "serif", "handwritten", "display", "mono"] = "sans"
    text_margin_x: float = Field(default=0.05, ge=0, le=0.3)
    text_margin_y: float = Field(default=0.03, ge=0, le=0.3)
    bold: bool = False
    color: str = Field(default="1F2937", pattern=r"^[0-9A-Fa-f]{6}$")
    fill: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")
    line_color: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")
    line_width: float = Field(default=1, ge=0, le=8)
    align: Literal["left", "center", "right"] = "left"
    valign: Literal["top", "middle", "bottom"] = "top"
    opacity: int = Field(default=100, ge=0, le=100)


class SlideElement(SchemaModel):
    """Safe, declarative element on a normalized 16:9 slide canvas."""

    type: Literal["text", "shape", "line", "image", "table", "chart"]
    contract_role: Literal[
        "content",
        "plan_copy",
        "visual_module",
        "visual_asset",
        "visual_placeholder",
    ] = "content"
    object_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    semantic_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)
    z: int = Field(default=0, ge=0, le=100)
    text: str | None = None
    items: list[str] = Field(default_factory=list, max_length=12)
    shape: Literal["rectangle", "rounded_rectangle", "oval", "chevron"] = "rectangle"
    image_path: str | None = None
    image_fit: Literal["cover", "contain"] = "cover"
    table_rows: list[list[str]] = Field(default_factory=list, max_length=12)
    chart_type: Literal["bar", "line", "pie", "doughnut"] = "bar"
    chart_categories: list[str] = Field(default_factory=list, max_length=12)
    chart_series: list[list[float]] = Field(default_factory=list, max_length=6)
    chart_series_names: list[str] = Field(default_factory=list, max_length=6)
    style: SlideElementStyle = Field(default_factory=SlideElementStyle)

    @model_validator(mode="after")
    def validate_canvas_bounds(self) -> "SlideElement":
        if self.type == "line":
            if self.w == 0 and self.h == 0:
                raise ValueError("line element must have a non-zero span")
        elif self.w == 0 or self.h == 0:
            raise ValueError("slide element width and height must be positive")
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("slide element must stay inside the normalized canvas")
        return self


class SlidePlan(SchemaModel):
    id: str = Field(min_length=1)
    order: int = Field(ge=1)
    source_section_ids: list[str] = Field(min_length=1)
    source_page_no: int | None = Field(default=None, ge=1)
    source_kind: Literal["source", "generated"] = "generated"
    title: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    speaker_script: str = Field(min_length=1)
    suggested_visual: str = Field(min_length=1)
    # Compatibility label only. `elements` defines the actual free-form layout.
    layout: str = "freeform"
    # Optional registry identity used for deck-level variety and diagnostics.
    layout_id: str | None = None
    visual_payload: list[str] = Field(default_factory=list)
    background: str = Field(default="F7F9F7", pattern=r"^[0-9A-Fa-f]{6}$")
    elements: list[SlideElement] = Field(default_factory=list, max_length=40)


class PresentationPlan(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    mode: Literal["generated", "source_deck"] = "generated"
    source_material_id: str | None = None
    presentation_resource_id: str | None = None
    slides: list[SlidePlan] = Field(min_length=1)
    generation_source: Literal["llm", "fallback", "unknown"] = "unknown"
    generation_provider: str | None = None
    generation_model: str | None = None
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PresentationPlanLibrarySummary(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    slide_count: int = Field(ge=1)
    artifact_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SlideScriptDiagnosis(SchemaModel):
    slide_id: str
    title: str
    source_section_id: str | None = None
    source_field: Literal["teaching_script", "teaching_narrative", "summary", "title"] | None = None
    exact_learning_content_copy: bool = False
    fallback_markers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PresentationPlanDiagnosis(SchemaModel):
    presentation_plan_id: str
    content_id: str
    likely_fallback: bool
    fallback_confidence: float = Field(ge=0, le=1)
    llm_configured: bool
    provider: str
    model: str | None = None
    exact_historical_reason: str | None = None
    direct_script_count: int = Field(ge=0)
    body_slide_count: int = Field(ge=0)
    one_body_slide_per_section: bool
    reasons: list[str] = Field(default_factory=list)
    slides: list[SlideScriptDiagnosis] = Field(default_factory=list)


class PresentationPlanJob(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    prepare_question_bank: bool = True
    mode: Literal["generated", "source_deck"] = "generated"
    source_material_id: str | None = None
    status: PresentationPlanJobStatus = PresentationPlanJobStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    step: str = "queued"
    message: str = "Waiting to generate presentation plan"
    plan_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PPTThemeOption(SchemaModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    style_direction: str = Field(min_length=1)
    colors: dict[str, str]


class CreatePPTJobRequest(SchemaModel):
    theme_id: str = Field(
        default=DEFAULT_PPT_THEME_ID,
        pattern=r"^[a-z0-9_]+$",
    )


class PPTGenerationJob(SchemaModel):
    id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
    theme_id: str = Field(
        default=DEFAULT_PPT_THEME_ID,
        pattern=r"^[a-z0-9_]+$",
    )
    status: PPTGenerationStatus = PPTGenerationStatus.QUEUED
    progress: float = Field(default=0, ge=0, le=1)
    artifact_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "PPTGenerationJob":
        if self.status == PPTGenerationStatus.FINISHED and not self.artifact_id:
            raise ValueError("finished ppt generation job must have artifact_id")
        if self.status == PPTGenerationStatus.FAILED and not self.error:
            raise ValueError("failed ppt generation job must have error message")
        return self


class PPTSlideImage(SchemaModel):
    slide_id: str = Field(min_length=1)
    slide_no: int = Field(ge=1)
    image_path: str = Field(min_length=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class PPTArtifact(SchemaModel):
    id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
    pptx_path: str | None = None
    skill_request_path: str = Field(min_length=1)
    slide_images: list[PPTSlideImage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PresentationSlideResource(SchemaModel):
    slide_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    kind: Literal["source", "generated"]
    source_page_no: int | None = Field(default=None, ge=1)
    artifact_slide_no: int | None = Field(default=None, ge=1)
    image_url: str | None = None


class PresentationResource(SchemaModel):
    id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
    kind: Literal["source_deck", "generated_artifact"]
    source_material_id: str | None = None
    artifact_id: str | None = None
    source_file_hash: str | None = None
    source_page_count: int | None = Field(default=None, ge=0)
    slides: list[PresentationSlideResource] = Field(default_factory=list)
    is_stale: bool = False
    stale_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UpdateSlideScriptRequest(SchemaModel):
    speaker_script: str = Field(min_length=1)
