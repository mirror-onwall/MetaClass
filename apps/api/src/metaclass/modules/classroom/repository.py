from datetime import timezone
from typing import Protocol

from metaclass.infrastructure.database import Database
from metaclass.modules.classroom.models import ClassroomPlanRecord, ClassroomSessionRecord
from metaclass.modules.classroom.schemas import ClassroomPlan, ClassroomSession


class ClassroomRepository(Protocol):
    def save_plan(self, plan: ClassroomPlan) -> None: ...

    def get_plan(self, plan_id: str) -> ClassroomPlan | None: ...

    def save_session(self, session_model: ClassroomSession) -> None: ...

    def get_session(self, session_id: str) -> ClassroomSession | None: ...


class SqlAlchemyClassroomRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_plan(self, plan: ClassroomPlan) -> None:
        with self.database.session() as session:
            session.merge(
                ClassroomPlanRecord(
                    id=plan.id,
                    content_id=plan.content_id,
                    scenes=[scene.model_dump(mode="json") for scene in plan.scenes],
                    version=plan.version,
                )
            )

    def get_plan(self, plan_id: str) -> ClassroomPlan | None:
        with self.database.session() as session:
            record = session.get(ClassroomPlanRecord, plan_id)
            if not record:
                return None
            return ClassroomPlan.model_validate(
                {
                    "id": record.id,
                    "content_id": record.content_id,
                    "scenes": record.scenes,
                    "version": record.version,
                }
            )

    def save_session(self, session_model: ClassroomSession) -> None:
        with self.database.session() as session:
            session.merge(
                ClassroomSessionRecord(
                    id=session_model.id,
                    plan_id=session_model.plan_id,
                    status=session_model.status,
                    scene_index=session_model.scene_index,
                    action_index=session_model.action_index,
                    waiting_for=session_model.waiting_for,
                    evidence=[item.model_dump(mode="json") for item in session_model.evidence],
                    mastery=[item.model_dump(mode="json") for item in session_model.mastery],
                    events=[item.model_dump(mode="json") for item in session_model.events],
                    created_at=session_model.created_at,
                    updated_at=session_model.updated_at,
                )
            )

    def get_session(self, session_id: str) -> ClassroomSession | None:
        with self.database.session() as session:
            record = session.get(ClassroomSessionRecord, session_id)
            if not record:
                return None
            return ClassroomSession.model_validate(
                {
                    "id": record.id,
                    "plan_id": record.plan_id,
                    "status": record.status,
                    "scene_index": record.scene_index,
                    "action_index": record.action_index,
                    "waiting_for": record.waiting_for,
                    "evidence": record.evidence,
                    "mastery": record.mastery,
                    "events": record.events,
                    "created_at": (
                        record.created_at.replace(tzinfo=timezone.utc)
                        if record.created_at.tzinfo is None
                        else record.created_at
                    ),
                    "updated_at": (
                        record.updated_at.replace(tzinfo=timezone.utc)
                        if record.updated_at.tzinfo is None
                        else record.updated_at
                    ),
                }
            )
