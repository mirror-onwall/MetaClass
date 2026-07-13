from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class ClassroomPlanRecord(Base):
    __tablename__ = "classroom_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    scenes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)


class ClassroomPlanGenerationMetaRecord(Base):
    __tablename__ = "classroom_plan_generation_meta"

    plan_id: Mapped[str] = mapped_column(
        ForeignKey("classroom_plans.id"), primary_key=True
    )
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_blueprint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClassroomPlanJobRecord(Base):
    __tablename__ = "classroom_plan_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    step: Mapped[str] = mapped_column(String(40))
    progress: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(500))
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClassroomSessionRecord(Base):
    __tablename__ = "classroom_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("classroom_plans.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="lecture")
    status: Mapped[str] = mapped_column(String(20), index=True)
    scene_index: Mapped[int] = mapped_column(Integer)
    action_index: Mapped[int] = mapped_column(Integer)
    waiting_for: Mapped[str | None] = mapped_column(String(30), nullable=True)
    student_states: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    mastery: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
