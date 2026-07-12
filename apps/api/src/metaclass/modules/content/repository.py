from datetime import timezone
from typing import Protocol

from sqlalchemy import select

from metaclass.infrastructure.database import Database
from metaclass.modules.content.models import LearningContentRecord, PageUnderstandingRecord
from metaclass.modules.content.schemas import LearningContent, PageUnderstanding


class ContentRepository(Protocol):
    def save_understandings(self, items: list[PageUnderstanding]) -> None: ...

    def list_understandings(self, material_id: str) -> list[PageUnderstanding]: ...

    def save(self, content: LearningContent) -> None: ...

    def get(self, content_id: str) -> LearningContent | None: ...


class SqlAlchemyContentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, content: LearningContent) -> None:
        with self.database.session() as session:
            session.merge(
                LearningContentRecord(
                    id=content.id,
                    material_id=content.material_id,
                    title=content.title,
                    objectives=content.objectives,
                    sections=[item.model_dump(mode="json") for item in content.sections],
                    version=content.version,
                    created_at=content.created_at,
                    updated_at=content.updated_at,
                )
            )

    def get(self, content_id: str) -> LearningContent | None:
        with self.database.session() as session:
            record = session.get(LearningContentRecord, content_id)
            if not record:
                return None
            return LearningContent.model_validate(
                {
                    "id": record.id,
                    "material_id": record.material_id,
                    "title": record.title,
                    "objectives": record.objectives,
                    "sections": record.sections,
                    "version": record.version,
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

    def save_understandings(self, items: list[PageUnderstanding]) -> None:
        with self.database.session() as session:
            for item in items:
                session.merge(
                    PageUnderstandingRecord(
                        id=item.id,
                        material_id=item.material_id,
                        page_id=item.page_id,
                        page_no=item.page_no,
                        summary=item.summary,
                        knowledge_points=item.knowledge_points,
                        teaching_focus=item.teaching_focus,
                        possible_questions=item.possible_questions,
                        quiz_items=[quiz.model_dump(mode="json") for quiz in item.quiz_items],
                        source_refs=[ref.model_dump(mode="json") for ref in item.source_refs],
                        provider=item.provider,
                        model=item.model,
                        prompt_version=item.prompt_version,
                        created_at=item.created_at,
                    )
                )

    def list_understandings(self, material_id: str) -> list[PageUnderstanding]:
        with self.database.session() as session:
            records = session.scalars(
                select(PageUnderstandingRecord)
                .where(PageUnderstandingRecord.material_id == material_id)
                .order_by(PageUnderstandingRecord.page_no)
            ).all()
            return [
                PageUnderstanding.model_validate(
                    {
                        "id": record.id,
                        "material_id": record.material_id,
                        "page_id": record.page_id,
                        "page_no": record.page_no,
                        "summary": record.summary,
                        "knowledge_points": record.knowledge_points,
                        "teaching_focus": record.teaching_focus,
                        "possible_questions": record.possible_questions,
                        "quiz_items": record.quiz_items or [],
                        "source_refs": record.source_refs,
                        "provider": record.provider,
                        "model": record.model,
                        "prompt_version": record.prompt_version,
                        "created_at": (
                            record.created_at.replace(tzinfo=timezone.utc)
                            if record.created_at.tzinfo is None
                            else record.created_at
                        ),
                    }
                )
                for record in records
            ]
