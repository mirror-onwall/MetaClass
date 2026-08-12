from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class PageUnderstandingRecord(Base):
    __tablename__ = "page_understandings"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("page_metadata.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    page_role: Mapped[str] = mapped_column(String(50), default="concept")
    title: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(Text)
    teachable_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    key_excerpts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    concepts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    formulas: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    visual_analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge_points: Mapped[list[str]] = mapped_column(JSON)
    teaching_focus: Mapped[list[str]] = mapped_column(JSON)
    misconceptions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    possible_questions: Mapped[list[str]] = mapped_column(JSON)
    quiz_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    relations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningContentRecord(Base):
    __tablename__ = "learning_contents"
    __table_args__ = (
        UniqueConstraint("material_id", "version", name="uq_learning_content_material_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    material_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    collection_id: Mapped[str | None] = mapped_column(
        ForeignKey("material_collections.id"), nullable=True, index=True
    )
    organization_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    subtitle: Mapped[str] = mapped_column(String(500), default="")
    audience: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    teaching_intent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    material_overview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    global_concepts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    knowledge_units: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    knowledge_tree: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    objectives: Mapped[list[str]] = mapped_column(JSON)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    generation_guidance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
