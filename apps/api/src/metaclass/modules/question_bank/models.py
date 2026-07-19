from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class ClassroomQARecord(Base):
    __tablename__ = "classroom_qa_items"
    __table_args__ = (
        Index("ix_classroom_qa_plan_slide", "presentation_plan_id", "slide_id"),
        Index("ix_classroom_qa_plan_agent", "presentation_plan_id", "agent_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    presentation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("presentation_plans.id"), index=True
    )
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    slide_id: Mapped[str] = mapped_column(String(64), index=True)
    slide_order: Mapped[int] = mapped_column(Integer)
    agent_type: Mapped[str] = mapped_column(String(64), index=True)
    student_profile_id: Mapped[str] = mapped_column(String(96))
    knowledge_point: Mapped[str] = mapped_column(String(500))
    canonical_question: Mapped[str] = mapped_column(Text)
    student_question: Mapped[str] = mapped_column(Text)
    canonical_answer: Mapped[str] = mapped_column(Text)
    teacher_answer: Mapped[str] = mapped_column(Text)
    moment: Mapped[str] = mapped_column(String(40), index=True)
    placement_reason: Mapped[str] = mapped_column(Text)
    source_refs: Mapped[list[dict]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), index=True)
    search_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
