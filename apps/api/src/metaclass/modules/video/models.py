from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class VideoJobRecord(Base):
    __tablename__ = "video_jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 1", name="ck_video_progress_range"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    progress: Mapped[float] = mapped_column(Float)
    result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VideoResultRecord(Base):
    # New table name avoids silently reusing the pre-split MVP table.
    __tablename__ = "video_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"), unique=True, index=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    video_path: Mapped[str] = mapped_column(Text)
    subtitles_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TTSArtifactRecord(Base):
    __tablename__ = "tts_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    ref_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    voice: Mapped[str | None] = mapped_column(String(80), nullable=True)
    audio_path: Mapped[str] = mapped_column(Text)
    audio_url: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
