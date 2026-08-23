from datetime import datetime
from enum import StrEnum

from pydantic import Field

from metaclass.core.schemas import SchemaModel, utc_now


class MaterialType(StrEnum):
    PDF = "pdf"
    PPTX = "pptx"


class MaterialStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class MaterialProcessingJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELED = "canceled"


class SourceRef(SchemaModel):
    material_id: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    text_span: str | None = None
    image_path: str | None = None


class PageImage(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    image_path: str = Field(min_length=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    description: str = ""


class Material(SchemaModel):
    id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    file_type: MaterialType
    file_hash: str | None = None
    status: MaterialStatus = MaterialStatus.UPLOADED
    storage_path: str = Field(min_length=1)
    page_count: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PageMetadata(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    raw_text: str = ""
    image_path: str = Field(min_length=1)
    embedded_images: list[PageImage] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(min_length=1)


class ProcessedMaterial(SchemaModel):
    material: Material
    pages: list[PageMetadata]


class MaterialCollection(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    material_ids: list[str] = Field(default_factory=list)
    primary_material_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MaterialCollectionCreate(SchemaModel):
    title: str = Field(default="未命名课程资料集", min_length=1, max_length=200)
    material_ids: list[str] = Field(min_length=1)
    primary_material_id: str | None = None


class MaterialDeletionResult(SchemaModel):
    material_id: str = Field(min_length=1)
    deleted: bool = True


class MaterialProcessingJob(SchemaModel):
    id: str = Field(min_length=1)
    status: MaterialProcessingJobStatus = MaterialProcessingJobStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    step: str = "queued"
    message: str = "Waiting to process materials"
    material_ids: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessedMaterials(SchemaModel):
    items: list[ProcessedMaterial]
    collection: MaterialCollection | None = None
