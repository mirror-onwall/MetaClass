from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class PresentationPlanRecord(Base):
    __tablename__ = "presentation_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    slides: Mapped[list[dict]] = mapped_column(JSON)
    generation_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    generation_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PPTGenerationJobRecord(Base):
    __tablename__ = "ppt_generation_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    presentation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("presentation_plans.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    progress: Mapped[float] = mapped_column(Float)
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PPTArtifactRecord(Base):
    __tablename__ = "ppt_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ppt_generation_jobs.id"), unique=True)
    presentation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("presentation_plans.id"), index=True
    )
    pptx_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_request_path: Mapped[str] = mapped_column(Text)
    slide_images: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
