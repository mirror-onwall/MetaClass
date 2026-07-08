from datetime import datetime
from enum import StrEnum

from pydantic import Field

from metaclass.core.schemas import SchemaModel, utc_now


class EvidenceType(StrEnum):
    QUIZ = "QUIZ"
    FREE_ANSWER = "FREE_ANSWER"
    SELF_REPORT = "SELF_REPORT"


class Evidence(SchemaModel):
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    type: EvidenceType
    knowledge_point: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    weight: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class MasteryEstimate(SchemaModel):
    session_id: str = Field(min_length=1)
    knowledge_point: str = Field(min_length=1)
    value: float = Field(default=0.5, ge=0, le=1)
    evidence_count: int = Field(ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
