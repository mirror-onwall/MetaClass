from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class PaperWorkflowJobRecord(Base):
    __tablename__ = "paper_workflow_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    request_payload: Mapped[dict] = mapped_column(JSON)
    strategy_requested: Mapped[str] = mapped_column(String(40))
    strategy_selected: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    progress: Mapped[float] = mapped_column(Float)
    provider_attempts: Mapped[list[str]] = mapped_column(JSON, default=list)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifact_bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    derived_material_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaperArtifactBundleRecord(Base):
    __tablename__ = "paper_artifact_bundles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("paper_workflow_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(100))
    root_path: Mapped[str] = mapped_column(Text)
    files: Mapped[list[dict]] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(20))
    derived_material_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
