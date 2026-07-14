from datetime import timezone
from typing import Protocol

from sqlalchemy import delete, select

from metaclass.infrastructure.database import Database
from metaclass.modules.materials.models import (
    MaterialCollectionRecord,
    MaterialRecord,
    PageRecord,
)
from metaclass.modules.materials.schemas import Material, MaterialCollection, PageMetadata


class MaterialRepository(Protocol):
    def save_material(self, material: Material) -> None: ...

    def get_material(self, material_id: str) -> Material | None: ...

    def list_materials(self) -> list[Material]: ...

    def save_collection(self, collection: MaterialCollection) -> None: ...

    def get_collection(self, collection_id: str) -> MaterialCollection | None: ...

    def list_collections(self) -> list[MaterialCollection]: ...

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
                    "page_count": record.page_count,
                    "error": record.error,
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

    def list_materials(self) -> list[Material]:
        with self.database.session() as session:
            records = session.scalars(
                select(MaterialRecord).order_by(MaterialRecord.created_at.desc())
            ).all()
            return [self._material_from_record(record) for record in records]

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
                "page_count": record.page_count,
                "error": record.error,
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

    @staticmethod
    def _collection_from_record(record: MaterialCollectionRecord) -> MaterialCollection:
        return MaterialCollection.model_validate(
            {
                "id": record.id,
                "title": record.title,
                "material_ids": record.material_ids,
                "primary_material_id": record.primary_material_id,
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
