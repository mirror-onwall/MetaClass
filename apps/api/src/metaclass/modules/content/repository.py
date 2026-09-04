from datetime import timezone
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

    def save(self, content: LearningContent) -> None: ...

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

    def save(self, content: LearningContent) -> None:
        with self.database.session() as session:
            session.merge(
                LearningContentRecord(
                    id=content.id,
                    material_id=content.material_id,
                    material_ids=content.material_ids or [content.material_id],
                    collection_id=content.collection_id,
                    organization_mode=content.organization_mode,
                    title=content.title,
                    subtitle=content.subtitle,
                    audience=content.audience,
                    teaching_intent=content.teaching_intent,
                    material_overview=content.material_overview,
                    global_concepts=[
                        item.model_dump(mode="json") for item in content.global_concepts
                    ],
                    knowledge_units=[
                        item.model_dump(mode="json") for item in content.knowledge_units
                    ],
                    knowledge_tree=(
                        content.knowledge_tree.model_dump(mode="json")
                        if content.knowledge_tree
                        else None
                    ),
                    objectives=content.objectives,
                    sections=[item.model_dump(mode="json") for item in content.sections],
                    generation_guidance=content.generation_guidance,
                    quality=content.quality,
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
