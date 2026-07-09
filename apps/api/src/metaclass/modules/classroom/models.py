from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from metaclass.infrastructure.database import Base


class ClassroomPlanRecord(Base):
    __tablename__ = "classroom_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("learning_contents.id"), index=True)
    scenes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)


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
