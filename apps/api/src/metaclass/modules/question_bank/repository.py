from typing import Protocol

from sqlalchemy import delete, select

from metaclass.infrastructure.database import Database
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.question_bank.models import ClassroomQARecord
from metaclass.modules.question_bank.schemas import ClassroomQA


class QuestionBankRepository(Protocol):
    def replace_for_plan(self, plan_id: str, items: list[ClassroomQA]) -> None: ...
    def append_for_plan(self, items: list[ClassroomQA]) -> None: ...
    def list_for_plan(self, plan_id: str) -> list[ClassroomQA]: ...
    def get_item(self, qa_id: str) -> ClassroomQA | None: ...
    def save_embeddings(self, embeddings: dict[str, list[float]]) -> None: ...


class SqlAlchemyQuestionBankRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def replace_for_plan(self, plan_id: str, items: list[ClassroomQA]) -> None:
        with self.database.session() as session:
            session.execute(
                delete(ClassroomQARecord).where(
                    ClassroomQARecord.presentation_plan_id == plan_id
                )
            )
            session.add_all([self._record(item) for item in items])

    def append_for_plan(self, items: list[ClassroomQA]) -> None:
        if not items:
            return
        with self.database.session() as session:
            for item in items:
                session.merge(self._record(item))

    def list_for_plan(self, plan_id: str) -> list[ClassroomQA]:
        with self.database.session() as session:
            records = session.scalars(
                select(ClassroomQARecord)
                .where(ClassroomQARecord.presentation_plan_id == plan_id)
                .order_by(ClassroomQARecord.slide_order, ClassroomQARecord.created_at)
            ).all()
            return [self._item(record) for record in records]

    def get_item(self, qa_id: str) -> ClassroomQA | None:
        with self.database.session() as session:
            record = session.get(ClassroomQARecord, qa_id)
            return self._item(record) if record else None

    def save_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        if not embeddings:
            return
        with self.database.session() as session:
            records = session.scalars(
                select(ClassroomQARecord).where(ClassroomQARecord.id.in_(embeddings))
            ).all()
            for record in records:
                record.embedding = embeddings[record.id]

    @staticmethod
    def _record(item: ClassroomQA) -> ClassroomQARecord:
        return ClassroomQARecord(
            id=item.id,
            presentation_plan_id=item.presentation_plan_id,
            content_id=item.content_id,
            slide_id=item.slide_id,
            slide_order=item.slide_order,
            agent_type=item.agent_type.value,
            student_profile_id=item.student_profile_id,
            knowledge_point=item.knowledge_point,
            canonical_question=item.canonical_question,
            student_question=item.student_question,
            canonical_answer=item.canonical_answer,
            teacher_answer=item.teacher_answer,
            moment=item.moment,
            placement_reason=item.placement_reason,
            source_refs=[ref.model_dump(mode="json") for ref in item.source_refs],
            status=item.status,
            search_text=" ".join(
                [
                    item.knowledge_point,
                    item.canonical_question,
                    item.student_question,
                    item.canonical_answer,
                ]
            ).lower(),
            embedding=item.embedding,
            created_at=item.created_at,
        )

    @staticmethod
    def _item(record: ClassroomQARecord) -> ClassroomQA:
        return ClassroomQA(
            id=record.id,
            presentation_plan_id=record.presentation_plan_id,
            content_id=record.content_id,
            slide_id=record.slide_id,
            slide_order=record.slide_order,
            agent_type=record.agent_type,
            student_profile_id=record.student_profile_id,
            knowledge_point=record.knowledge_point,
            canonical_question=record.canonical_question,
            student_question=record.student_question,
            canonical_answer=record.canonical_answer,
            teacher_answer=record.teacher_answer,
            moment=record.moment,
            placement_reason=record.placement_reason,
            source_refs=[SourceRef.model_validate(ref) for ref in (record.source_refs or [])],
            status=record.status,
            created_at=record.created_at,
            embedding=record.embedding,
        )
