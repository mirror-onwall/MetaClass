from datetime import timezone
from threading import RLock
from typing import Protocol

from sqlalchemy import select

from metaclass.infrastructure.database import Database
from metaclass.modules.content.models import LearningContentRecord, PageUnderstandingRecord
from metaclass.modules.content.schemas import (
    LearningContent,
    MaterialLearningContentSummary,
    PageUnderstanding,
)


class ContentRepository(Protocol):
    def save_understandings(self, items: list[PageUnderstanding]) -> None: ...

    def list_understandings(self, material_id: str) -> list[PageUnderstanding]: ...

    def save(self, content: LearningContent) -> LearningContent: ...

    def get(self, content_id: str) -> LearningContent | None: ...

    def get_for_material_version(
        self,
        material_id: str,
        version: int,
    ) -> LearningContent | None: ...

    def list_material_summaries(self) -> list[MaterialLearningContentSummary]: ...


class SqlAlchemyContentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._save_lock = RLock()

    def save(self, content: LearningContent) -> LearningContent:
        # The database treats (material_id, version) as the stable identity. Older
        # records may have an ID that differs from today's deterministic ID, so an
        # ID-only merge would attempt an INSERT and violate that unique constraint.
        # Serialize the lookup/update pair to avoid the same race between local jobs.
        with self._save_lock, self.database.session() as session:
            existing = session.scalar(
                select(LearningContentRecord).where(
                    LearningContentRecord.material_id == content.material_id,
                    LearningContentRecord.version == content.version,
                )
            )
            persisted = content
            if existing and existing.id != content.id:
                created_at = existing.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                persisted = content.model_copy(
                    update={"id": existing.id, "created_at": created_at}
                )
            session.merge(
                LearningContentRecord(
                    id=persisted.id,
                    material_id=persisted.material_id,
                    material_ids=persisted.material_ids or [persisted.material_id],
                    collection_id=persisted.collection_id,
                    organization_mode=persisted.organization_mode,
                    title=persisted.title,
                    subtitle=persisted.subtitle,
                    audience=persisted.audience,
                    teaching_intent=persisted.teaching_intent,
                    material_overview=persisted.material_overview,
                    global_concepts=[
                        item.model_dump(mode="json") for item in persisted.global_concepts
                    ],
                    knowledge_units=[
                        item.model_dump(mode="json") for item in persisted.knowledge_units
                    ],
                    knowledge_tree=(
                        persisted.knowledge_tree.model_dump(mode="json")
                        if persisted.knowledge_tree
                        else None
                    ),
                    objectives=persisted.objectives,
                    sections=[item.model_dump(mode="json") for item in persisted.sections],
                    generation_guidance=persisted.generation_guidance,
                    quality=persisted.quality,
                    version=persisted.version,
                    created_at=persisted.created_at,
                    updated_at=persisted.updated_at,
                )
            )
            return persisted

    def get(self, content_id: str) -> LearningContent | None:
        with self.database.session() as session:
            record = session.get(LearningContentRecord, content_id)
            if not record:
                return None
            return LearningContent.model_validate(
                {
                    "id": record.id,
                    "material_id": record.material_id,
                    "material_ids": record.material_ids or [record.material_id],
                    "collection_id": record.collection_id,
                    "organization_mode": record.organization_mode or "knowledge",
                    "title": record.title,
                    "subtitle": record.subtitle or "",
                    "audience": record.audience or {},
                    "teaching_intent": record.teaching_intent or {},
                    "material_overview": record.material_overview or {},
                    "global_concepts": record.global_concepts or [],
                    "knowledge_units": record.knowledge_units or [],
                    "knowledge_tree": record.knowledge_tree,
                    "objectives": record.objectives,
                    "sections": record.sections,
                    "generation_guidance": record.generation_guidance or {},
                    "quality": record.quality or {},
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

    def get_for_material_version(
        self,
        material_id: str,
        version: int,
    ) -> LearningContent | None:
        with self.database.session() as session:
            content_id = session.scalar(
                select(LearningContentRecord.id)
                .where(
                    LearningContentRecord.material_id == material_id,
                    LearningContentRecord.version == version,
                )
                .order_by(LearningContentRecord.updated_at.desc())
            )
        return self.get(content_id) if content_id else None

    def list_material_summaries(self) -> list[MaterialLearningContentSummary]:
        with self.database.session() as session:
            records = session.scalars(
                select(LearningContentRecord).order_by(LearningContentRecord.updated_at.desc())
            ).all()
            seen: set[str] = set()
            summaries: list[MaterialLearningContentSummary] = []
            for record in records:
                material_ids = list(record.material_ids or [record.material_id])
                fresh_ids = [material_id for material_id in material_ids if material_id not in seen]
                if not fresh_ids:
                    continue
                seen.update(fresh_ids)
                summaries.append(
                    MaterialLearningContentSummary(
                        content_id=record.id,
                        material_ids=fresh_ids,
                        title=record.title,
                        subtitle=record.subtitle or "",
                        updated_at=(
                            record.updated_at.replace(tzinfo=timezone.utc)
                            if record.updated_at.tzinfo is None
                            else record.updated_at
                        ),
                    )
                )
            return summaries

    def save_understandings(self, items: list[PageUnderstanding]) -> None:
        with self.database.session() as session:
            for item in items:
                session.merge(
                    PageUnderstandingRecord(
                        id=item.id,
                        material_id=item.material_id,
                        page_id=item.page_id,
                        page_no=item.page_no,
                        page_role=item.page_role,
                        title=item.title,
                        summary=item.summary,
                        teachable_points=[
                            point.model_dump(mode="json") for point in item.teachable_points
                        ],
                        key_excerpts=[
                            excerpt.model_dump(mode="json") for excerpt in item.key_excerpts
                        ],
                        concepts=[concept.model_dump(mode="json") for concept in item.concepts],
                        formulas=[formula.model_dump(mode="json") for formula in item.formulas],
                        visual_analysis=item.visual_analysis,
                        knowledge_points=item.knowledge_points,
                        teaching_focus=item.teaching_focus,
                        misconceptions=[
                            misconception.model_dump(mode="json")
                            for misconception in item.misconceptions
                        ],
                        possible_questions=item.possible_questions,
                        quiz_items=[quiz.model_dump(mode="json") for quiz in item.quiz_items],
                        relations=item.relations,
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
                        "page_role": record.page_role or "concept",
                        "title": record.title or "",
                        "summary": record.summary,
                        "teachable_points": record.teachable_points or [],
                        "key_excerpts": record.key_excerpts or [],
                        "concepts": record.concepts or [],
                        "formulas": record.formulas or [],
                        "visual_analysis": record.visual_analysis or {},
                        "knowledge_points": record.knowledge_points,
                        "teaching_focus": record.teaching_focus,
                        "misconceptions": record.misconceptions or [],
                        "possible_questions": record.possible_questions,
                        "quiz_items": record.quiz_items or [],
                        "relations": record.relations or {},
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
