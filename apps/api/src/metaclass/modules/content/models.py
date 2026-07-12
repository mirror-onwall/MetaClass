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
    summary: Mapped[str] = mapped_column(Text)
    knowledge_points: Mapped[list[str]] = mapped_column(JSON)
    teaching_focus: Mapped[list[str]] = mapped_column(JSON)
    possible_questions: Mapped[list[str]] = mapped_column(JSON)
    quiz_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
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
    title: Mapped[str] = mapped_column(String(500))
    objectives: Mapped[list[str]] = mapped_column(JSON)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
