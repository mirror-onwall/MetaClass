from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel, utc_now


class VideoStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class VideoJob(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    status: VideoStatus = VideoStatus.PENDING
    progress: float = Field(default=0, ge=0, le=1)
    result_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "VideoJob":
        if self.status == VideoStatus.FINISHED and not self.result_id:
            raise ValueError("finished video job must have result_id")
        if self.status == VideoStatus.FAILED and not self.error:
            raise ValueError("failed video job must have error message")
        return self


class VideoResult(SchemaModel):
    id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    video_path: str = Field(min_length=1)
    subtitles_path: str | None = None
    duration_seconds: float = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
