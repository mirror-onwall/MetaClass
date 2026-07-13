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


class SourceRef(SchemaModel):
    material_id: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    text_span: str | None = None
    image_path: str | None = None


class Material(SchemaModel):
    id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    file_type: MaterialType
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
    source_refs: list[SourceRef] = Field(min_length=1)


class ProcessedMaterial(SchemaModel):
    material: Material
    pages: list[PageMetadata]


class ProcessedMaterials(SchemaModel):
    items: list[ProcessedMaterial]
