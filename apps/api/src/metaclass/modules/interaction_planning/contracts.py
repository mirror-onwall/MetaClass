from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel
from metaclass.modules.classroom.agent_schemas import StudentAgentType
from metaclass.modules.materials.schemas import SourceRef


class InteractionPolicy(SchemaModel):
    intensity: Literal["none", "light", "standard", "rich"] = "standard"
    minimum_nodes: int = Field(default=1, ge=0)
    maximum_nodes: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def validate_budget(self) -> InteractionPolicy:
        if self.maximum_nodes < self.minimum_nodes:
            raise ValueError("interaction maximum must not be below minimum")
        return self


class InteractionSlide(SchemaModel):
    slide_id: str
    order: int = Field(ge=1)
    title: str
    message: str = ""
    role: str = "concept"
    speaker_script: str = ""
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"] = "contextual"
    visual_labels: list[str] = Field(default_factory=list)
    verified_numbers: list[str] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)


class InteractionSection(SchemaModel):
    id: str
    title: str
    slide_ids: list[str] = Field(default_factory=list)


class InteractionKnowledgeUnit(SchemaModel):
    id: str
    title: str
    summary: str = ""
    importance: str = "supporting"
    source_refs: list[SourceRef] = Field(default_factory=list)


class InteractionEvidenceEntry(SchemaModel):
    slide_id: str
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    verified_numbers: list[str] = Field(default_factory=list)


class EvidenceIndex(SchemaModel):
    entries: list[InteractionEvidenceEntry] = Field(default_factory=list)


class InteractionPlanningContext(SchemaModel):
    content_id: str
    presentation_plan_id: str
    mode: Literal["generated", "source_deck", "paper_deck"]
    duration_minutes: int = Field(ge=1)
    slides: list[InteractionSlide] = Field(min_length=1)
    sections: list[InteractionSection] = Field(default_factory=list)
    knowledge_units: list[InteractionKnowledgeUnit] = Field(default_factory=list)
    evidence_index: EvidenceIndex = Field(default_factory=EvidenceIndex)


class InteractionBlueprint(SchemaModel):
    id: str
    slide_id: str
    direction: Literal["student_to_teacher", "teacher_to_student"]
    intent: Literal[
        "concept_clarification",
        "concept_contrast",
        "mechanism_reasoning",
        "evidence_reading",
        "boundary_condition",
        "procedure_check",
        "application_transfer",
        "section_synthesis",
    ]
    teaching_goal: str
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"] = "contextual"
    preferred_agent_types: list[StudentAgentType] = Field(default_factory=list)
    importance: float = Field(ge=0, le=1)


class ScriptedInteraction(SchemaModel):
    id: str
    blueprint_id: str
    slide_id: str
    question: str
    answer: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class ScriptedInteractionBank(SchemaModel):
    presentation_plan_id: str
    items: list[ScriptedInteraction] = Field(default_factory=list)
    validation_status: Literal["pending", "validated", "invalid"] = "pending"
    generation_source: Literal["llm", "fallback", "none", "unknown"] = "unknown"
    fallback_reason: str | None = None
