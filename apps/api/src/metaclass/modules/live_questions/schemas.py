from __future__ import annotations

from typing import Literal

from pydantic import Field

from metaclass.core.schemas import SchemaModel
from metaclass.modules.materials.schemas import SourceRef


class LiveEvidenceDocument(SchemaModel):
    id: str
    kind: Literal["paper_block", "claim", "result", "figure", "knowledge", "slide"]
    title: str
    text: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    slide_ids: list[str] = Field(default_factory=list)
    earliest_slide_order: int | None = Field(default=None, ge=1)
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class LiveQuestionIndex(SchemaModel):
    presentation_plan_id: str
    content_id: str
    mode: Literal["generated", "source_deck", "paper_deck"]
    slide_order: list[str] = Field(min_length=1)
    documents: list[LiveEvidenceDocument] = Field(min_length=1)


class RetrievedLiveEvidence(SchemaModel):
    document: LiveEvidenceDocument
    score: float = Field(ge=0)
    classroom_status: Literal["current", "already_taught", "future", "paper_reference"]


class LiveQuestionAnswer(SchemaModel):
    answer: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    used_document_ids: list[str] = Field(default_factory=list)
    future_slide_ids: list[str] = Field(default_factory=list)
    classroom_note: str | None = None
