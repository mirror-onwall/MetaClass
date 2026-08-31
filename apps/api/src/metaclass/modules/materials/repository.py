from dataclasses import dataclass
from datetime import UTC
from typing import Protocol

from sqlalchemy import delete, select

from metaclass.infrastructure.database import Database
from metaclass.modules.classroom.models import (
    ClassroomPlanGenerationMetaRecord,
    ClassroomPlanJobRecord,
    ClassroomPlanRecord,
    ClassroomRequestRecord,
    ClassroomSessionRecord,
)
from metaclass.modules.content.models import LearningContentRecord, PageUnderstandingRecord
from metaclass.modules.materials.models import (
    MaterialCollectionRecord,
    MaterialRecord,
    PageRecord,
)
from metaclass.modules.materials.schemas import Material, MaterialCollection, PageMetadata
from metaclass.modules.presentation.models import (
    PPTArtifactRecord,
    PPTGenerationJobRecord,
    PresentationPlanRecord,
    PresentationResourceRecord,
)
from metaclass.modules.question_bank.models import ClassroomQARecord
from metaclass.modules.video.models import VideoJobRecord, VideoResultRecord


@dataclass(frozen=True)
class MaterialProjectCleanup:
    file_paths: list[str]
    presentation_job_ids: list[str]


class MaterialRepository(Protocol):
    def save_material(self, material: Material) -> None: ...

    def get_material(self, material_id: str) -> Material | None: ...

    def list_materials(self) -> list[Material]: ...

    def delete_material(self, material_id: str) -> None: ...

    def delete_material_project(self, material_id: str) -> MaterialProjectCleanup: ...

    def save_collection(self, collection: MaterialCollection) -> None: ...

    def get_collection(self, collection_id: str) -> MaterialCollection | None: ...

    def list_collections(self) -> list[MaterialCollection]: ...

    def delete_collection(self, collection_id: str) -> None: ...

    def replace_pages(self, material_id: str, pages: list[PageMetadata]) -> None: ...

    def list_pages(self, material_id: str) -> list[PageMetadata]: ...


class SqlAlchemyMaterialRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_material(self, material: Material) -> None:
        with self.database.session() as session:
            session.merge(
                MaterialRecord(
                    id=material.id,
                    filename=material.filename,
                    file_type=material.file_type.value,
                    file_hash=material.file_hash,
                    status=material.status.value,
                    storage_path=material.storage_path,
                    source=material.source,
                    source_role=material.source_role.value,
                    parent_material_id=material.parent_material_id,
                    derivation_key=material.derivation_key,
                    page_count=material.page_count,
                    error=material.error,
                    created_at=material.created_at,
                    updated_at=material.updated_at,
                )
            )

    def get_material(self, material_id: str) -> Material | None:
        with self.database.session() as session:
            record = session.get(MaterialRecord, material_id)
            if not record:
                return None
            return Material.model_validate(
                {
                    "id": record.id,
                    "filename": record.filename,
                    "file_type": record.file_type,
                    "file_hash": record.file_hash,
                    "status": record.status,
                    "storage_path": record.storage_path,
                    "source": record.source or "upload",
                    "source_role": record.source_role or "uploaded",
                    "parent_material_id": record.parent_material_id,
                    "derivation_key": record.derivation_key,
                    "page_count": record.page_count,
                    "error": record.error,
                    "created_at": (
                        record.created_at.replace(tzinfo=UTC)
                        if record.created_at.tzinfo is None
                        else record.created_at
                    ),
                    "updated_at": (
                        record.updated_at.replace(tzinfo=UTC)
                        if record.updated_at.tzinfo is None
                        else record.updated_at
                    ),
                }
            )

    def list_materials(self) -> list[Material]:
        with self.database.session() as session:
            records = session.scalars(
                select(MaterialRecord).order_by(MaterialRecord.created_at.desc())
            ).all()
            return [self._material_from_record(record) for record in records]

    def delete_material(self, material_id: str) -> None:
        with self.database.session() as session:
            session.execute(delete(PageRecord).where(PageRecord.material_id == material_id))
            session.execute(delete(MaterialRecord).where(MaterialRecord.id == material_id))

    def delete_material_project(self, material_id: str) -> MaterialProjectCleanup:
        with self.database.session() as session:
            # `material_ids` is currently stored as JSON, so the database cannot
            # enforce a foreign key for every member. Inspect all content records
            # here to prevent deleting a secondary material that a multi-material
            # LearningContent still references.
            contents = [
                item
                for item in session.scalars(select(LearningContentRecord)).all()
                if item.material_id == material_id
                or material_id in (item.material_ids or [])
            ]
            shared = [item for item in contents if len(item.material_ids or []) > 1]
            if shared:
                raise ValueError(
                    "该材料仍属于多资料 LearningContent，不能单独删除；请先删除或拆分共享项目。"
                )
            content_ids = [item.id for item in contents]
            plans = session.scalars(
                select(PresentationPlanRecord).where(
                    PresentationPlanRecord.content_id.in_(content_ids)
                )
            ).all() if content_ids else []
            plan_ids = [item.id for item in plans]
            classroom_plan_ids = list(
                session.scalars(
                    select(ClassroomPlanRecord.id).where(
                        ClassroomPlanRecord.content_id.in_(content_ids)
                    )
                ).all()
            ) if content_ids else []
            ppt_jobs = session.scalars(
                select(PPTGenerationJobRecord).where(
                    PPTGenerationJobRecord.presentation_plan_id.in_(plan_ids)
                )
            ).all() if plan_ids else []
            ppt_job_ids = [item.id for item in ppt_jobs]
            artifacts = session.scalars(
                select(PPTArtifactRecord).where(
                    PPTArtifactRecord.presentation_plan_id.in_(plan_ids)
                )
            ).all() if plan_ids else []
            video_results = session.scalars(
                select(VideoResultRecord).where(
                    VideoResultRecord.content_id.in_(content_ids)
                )
            ).all() if content_ids else []
            file_paths = [
                path
                for item in artifacts
                for path in [
                    item.pptx_path,
                    item.skill_request_path,
                    *[
                        slide.get("image_path")
                        for slide in (item.slide_images or [])
                        if isinstance(slide, dict)
                    ],
                ]
                if path
            ] + [
                path
                for item in video_results
                for path in [item.video_path, item.subtitles_path]
                if path
            ]

            if classroom_plan_ids:
                session_ids = list(
                    session.scalars(
                        select(ClassroomSessionRecord.id).where(
                            ClassroomSessionRecord.plan_id.in_(classroom_plan_ids)
                        )
                    ).all()
                )
                if session_ids:
                    session.execute(
                        delete(ClassroomRequestRecord).where(
                            ClassroomRequestRecord.session_id.in_(session_ids)
                        )
                    )
                session.execute(
                    delete(ClassroomSessionRecord).where(
                        ClassroomSessionRecord.plan_id.in_(classroom_plan_ids)
                    )
                )
                session.execute(
                    delete(ClassroomPlanGenerationMetaRecord).where(
                        ClassroomPlanGenerationMetaRecord.plan_id.in_(classroom_plan_ids)
                    )
                )
            if content_ids:
                session.execute(
                    delete(ClassroomPlanJobRecord).where(
                        ClassroomPlanJobRecord.content_id.in_(content_ids)
                    )
                )
                session.execute(
                    delete(ClassroomPlanRecord).where(
                        ClassroomPlanRecord.content_id.in_(content_ids)
                    )
                )
                session.execute(
                    delete(ClassroomQARecord).where(
                        ClassroomQARecord.content_id.in_(content_ids)
                    )
                )
                session.execute(
                    delete(VideoResultRecord).where(
                        VideoResultRecord.content_id.in_(content_ids)
                    )
                )
                session.execute(
                    delete(VideoJobRecord).where(VideoJobRecord.content_id.in_(content_ids))
                )
            if plan_ids:
                session.execute(
                    delete(PresentationResourceRecord).where(
                        PresentationResourceRecord.presentation_plan_id.in_(plan_ids)
                    )
                )
                session.execute(
                    delete(PPTArtifactRecord).where(
                        PPTArtifactRecord.presentation_plan_id.in_(plan_ids)
                    )
                )
            if ppt_job_ids:
                session.execute(
                    delete(PPTGenerationJobRecord).where(
                        PPTGenerationJobRecord.id.in_(ppt_job_ids)
                    )
                )
            if plan_ids:
                session.execute(
                    delete(PresentationPlanRecord).where(PresentationPlanRecord.id.in_(plan_ids))
                )
            if content_ids:
                session.execute(
                    delete(LearningContentRecord).where(
                        LearningContentRecord.id.in_(content_ids)
                    )
                )
            session.execute(
                delete(PageUnderstandingRecord).where(
                    PageUnderstandingRecord.material_id == material_id
                )
            )
            session.execute(delete(PageRecord).where(PageRecord.material_id == material_id))
            collections = session.scalars(select(MaterialCollectionRecord)).all()
            for collection in collections:
                if material_id not in (collection.material_ids or []):
                    continue
                remaining = [item for item in collection.material_ids if item != material_id]
                if remaining:
                    collection.material_ids = remaining
                    if collection.primary_material_id == material_id:
                        collection.primary_material_id = remaining[0]
                else:
                    session.delete(collection)
            session.execute(delete(MaterialRecord).where(MaterialRecord.id == material_id))
            return MaterialProjectCleanup(
                file_paths=file_paths,
                presentation_job_ids=ppt_job_ids,
            )

    def save_collection(self, collection: MaterialCollection) -> None:
        with self.database.session() as session:
            session.merge(
                MaterialCollectionRecord(
                    id=collection.id,
                    title=collection.title,
                    material_ids=collection.material_ids,
                    primary_material_id=collection.primary_material_id,
                    created_at=collection.created_at,
                    updated_at=collection.updated_at,
                )
            )

    def get_collection(self, collection_id: str) -> MaterialCollection | None:
        with self.database.session() as session:
            record = session.get(MaterialCollectionRecord, collection_id)
            return self._collection_from_record(record) if record else None

    def list_collections(self) -> list[MaterialCollection]:
        with self.database.session() as session:
            records = session.scalars(
                select(MaterialCollectionRecord).order_by(
                    MaterialCollectionRecord.created_at.desc()
                )
            ).all()
            return [self._collection_from_record(record) for record in records]

    def delete_collection(self, collection_id: str) -> None:
        with self.database.session() as session:
            session.execute(
                delete(MaterialCollectionRecord).where(
                    MaterialCollectionRecord.id == collection_id
                )
            )

    def replace_pages(self, material_id: str, pages: list[PageMetadata]) -> None:
        if any(page.material_id != material_id for page in pages):
            raise ValueError("Every page must belong to the material being replaced")
        with self.database.session() as session:
            session.execute(delete(PageRecord).where(PageRecord.material_id == material_id))
            for page in pages:
                session.add(
                    PageRecord(
                        id=page.id,
                        material_id=page.material_id,
                        page_no=page.page_no,
                        title=page.title,
                        raw_text=page.raw_text,
                        image_path=page.image_path,
                        embedded_images=[
                            image.model_dump(mode="json") for image in page.embedded_images
                        ],
                        source_refs=[ref.model_dump(mode="json") for ref in page.source_refs],
                    )
                )

    def list_pages(self, material_id: str) -> list[PageMetadata]:
        with self.database.session() as session:
            records = session.scalars(
                select(PageRecord)
                .where(PageRecord.material_id == material_id)
                .order_by(PageRecord.page_no)
            ).all()
            return [
                PageMetadata.model_validate(
                    {
                        "id": record.id,
                        "material_id": record.material_id,
                        "page_no": record.page_no,
                        "title": record.title,
                        "raw_text": record.raw_text,
                        "image_path": record.image_path,
                        "embedded_images": record.embedded_images or [],
                        "source_refs": record.source_refs,
                    }
                )
                for record in records
            ]

    @staticmethod
    def _material_from_record(record: MaterialRecord) -> Material:
        return Material.model_validate(
            {
                "id": record.id,
                "filename": record.filename,
                "file_type": record.file_type,
                "file_hash": record.file_hash,
                "status": record.status,
                "storage_path": record.storage_path,
                "source": record.source or "upload",
                "source_role": record.source_role or "uploaded",
                "parent_material_id": record.parent_material_id,
                "derivation_key": record.derivation_key,
                "page_count": record.page_count,
                "error": record.error,
                "created_at": (
                    record.created_at.replace(tzinfo=UTC)
                    if record.created_at.tzinfo is None
                    else record.created_at
                ),
                "updated_at": (
                    record.updated_at.replace(tzinfo=UTC)
                    if record.updated_at.tzinfo is None
                    else record.updated_at
                ),
            }
        )

    @staticmethod
    def _collection_from_record(record: MaterialCollectionRecord) -> MaterialCollection:
        return MaterialCollection.model_validate(
            {
                "id": record.id,
                "title": record.title,
                "material_ids": record.material_ids,
                "primary_material_id": record.primary_material_id,
                "created_at": (
                    record.created_at.replace(tzinfo=UTC)
                    if record.created_at.tzinfo is None
                    else record.created_at
                ),
                "updated_at": (
                    record.updated_at.replace(tzinfo=UTC)
                    if record.updated_at.tzinfo is None
                    else record.updated_at
                ),
            }
        )
