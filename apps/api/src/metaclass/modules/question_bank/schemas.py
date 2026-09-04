from datetime import datetime
from typing import Literal

from pydantic import Field

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.classroom.agent_schemas import StudentAgentType
from metaclass.modules.materials.schemas import SourceRef

QuestionMoment = Literal[
    "before_explanation",
    "during_explanation",
    "after_explanation",
    "before_next_slide",
]


class QuestionCandidate(SchemaModel):
    candidate_id: str = Field(min_length=1)
    slide_id: str = Field(min_length=1)
    slide_order: int = Field(ge=1)
    agent_type: StudentAgentType
    student_profile_id: str = Field(min_length=1)
    knowledge_point: str = Field(min_length=1)
    canonical_question: str = Field(min_length=1)
    student_question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TeacherPreparedAnswer(SchemaModel):
    candidate_id: str = Field(min_length=1)
    canonical_answer: str = Field(min_length=1)
    teacher_answer: str = Field(min_length=1)
    answerable: bool = True


class ControllerPlacement(SchemaModel):
    candidate_id: str = Field(min_length=1)
    approved: bool = True
    moment: QuestionMoment = "after_explanation"
    placement_reason: str = Field(min_length=1)


class ClassroomQA(SchemaModel):
    id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    slide_id: str = Field(min_length=1)
    slide_order: int = Field(ge=1)
    agent_type: StudentAgentType
    compatible_agent_types: list[StudentAgentType] = Field(default_factory=list)
    student_profile_id: str = Field(min_length=1)
    knowledge_point: str = Field(min_length=1)
    canonical_question: str = Field(min_length=1)
    student_question: str = Field(min_length=1)
    canonical_answer: str = Field(min_length=1)
    teacher_answer: str = Field(min_length=1)
    moment: QuestionMoment
    placement_reason: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    status: Literal["approved", "rejected"] = "approved"
    generation_id: str = Field(default="legacy", min_length=1)
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    embedding: list[float] | None = Field(default=None, exclude=True)


class QuestionBank(SchemaModel):
    presentation_plan_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    generation_id: str = Field(default="legacy", min_length=1)
    items: list[ClassroomQA] = Field(default_factory=list)


class QuestionSearchResult(SchemaModel):
    item: ClassroomQA
    score: float = Field(ge=0)
