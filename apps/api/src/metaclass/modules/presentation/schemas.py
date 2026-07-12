from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel, utc_now


class PPTGenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_SKILL = "waiting_for_skill"
    FINISHED = "finished"
    FAILED = "failed"


class SlidePlan(SchemaModel):
    id: str = Field(min_length=1)
    order: int = Field(ge=1)
    source_section_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    speaker_script: str = Field(min_length=1)
    suggested_visual: str = Field(min_length=1)


class PresentationPlan(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    slides: list[SlidePlan] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PPTGenerationJob(SchemaModel):
    id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
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
