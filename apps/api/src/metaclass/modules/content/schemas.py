from pydantic import Field, model_validator

from datetime import datetime

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.materials.schemas import SourceRef


class PageUnderstandingDraft(SchemaModel):
    summary: str
    knowledge_points: list[str] = Field(default_factory=list)
    teaching_focus: list[str] = Field(default_factory=list)
    possible_questions: list[str] = Field(default_factory=list)


class PageUnderstanding(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    summary: str
    knowledge_points: list[str] = Field(default_factory=list)
    teaching_focus: list[str] = Field(default_factory=list)
    possible_questions: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str | None = None
    prompt_version: str = Field(default="v1", min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class QuizItem(SchemaModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    correct_index: int = Field(ge=0)
    explanation: str
    knowledge_point: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def correct_index_must_exist(self) -> "QuizItem":
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index must point to an existing option")
        return self


class LearningSection(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str
    knowledge_points: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(min_length=1)
    quiz_items: list[QuizItem] = Field(default_factory=list)


class LearningContent(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objectives: list[str] = Field(default_factory=list)
    sections: list[LearningSection] = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
