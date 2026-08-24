from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base
from metaclass.modules.presentation.schemas import DEFAULT_PPT_THEME_ID


class PresentationPlanRecord(Base):
    __tablename__ = "presentation_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_material_id: Mapped[str | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )
    source_paper_material_id: Mapped[str | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )
    paper_artifact_bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    presentation_resource_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
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
    theme_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_PPT_THEME_ID
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


class PresentationResourceRecord(Base):
    __tablename__ = "presentation_resources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    presentation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("presentation_plans.id"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    source_material_id: Mapped[str | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("ppt_artifacts.id"), nullable=True
    )
    source_file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slides: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
