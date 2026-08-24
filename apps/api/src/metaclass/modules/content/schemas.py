from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.materials.schemas import SourceRef


class ContentGenerationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MaterialLearningContentSummary(SchemaModel):
    """Lightweight library label derived from the latest organized content."""

    content_id: str = Field(min_length=1)
    material_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    subtitle: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class TeachingPoint(SchemaModel):
    point: str = Field(min_length=1)
    importance: str = "supporting"
    difficulty: str = "medium"


class SourceExcerpt(SchemaModel):
    id: str = Field(default="", max_length=100)
    text: str = Field(min_length=1)
    type: str = "claim"
    reason: str = ""
    importance: str = "supporting"
    usage: str = "reference_only"
    source_refs: list[SourceRef] = Field(default_factory=list)


class ConceptNote(SchemaModel):
    id: str = ""
    name: str = Field(min_length=1)
    definition: str = ""
    plain_explanation: str = ""
    why_it_matters: str = ""
    related_concept_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class FormulaNote(SchemaModel):
    id: str = ""
    latex: str = ""
    name: str = ""
    meaning: str = ""
    variables: list[dict] = Field(default_factory=list)
    when_to_use: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)

    @field_validator("variables", mode="before")
    @classmethod
    def normalize_variables(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [{"symbol": value, "meaning": ""}]
        if isinstance(value, dict):
            if "symbol" in value:
                return [value]
            return [
                {"symbol": str(symbol), "meaning": "" if meaning is None else str(meaning)}
                for symbol, meaning in value.items()
            ]
        if isinstance(value, list):
            return [
                {"symbol": item, "meaning": ""} if isinstance(item, str) else item for item in value
            ]
        return value


class ExampleNote(SchemaModel):
    id: str = ""
    title: str = ""
    scenario: str = ""
    explanation: str = ""
    takeaway: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)


class VisualOpportunity(SchemaModel):
    id: str = ""
    type: str = "diagram"
    description: str = ""
    image_path: str | None = None
    image_description: str = ""
    usage_hint: str = ""
    priority: str = "medium"
    source_refs: list[SourceRef] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, value):
        if isinstance(value, int | float):
            if value <= 1:
                return "high"
            if value == 2:
                return "medium"
            return "low"
        return "" if value is None else str(value)


class Misconception(SchemaModel):
    mistake: str = ""
    correction: str = ""


class InteractionOpportunity(SchemaModel):
    type: str = "probe"
    prompt: str = ""
    expected_answer: str = ""
    target_concept_ids: list[str] = Field(default_factory=list)
    difficulty: str = "medium"


class PageRef(SchemaModel):
    material_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    reason: str = ""


class TeachingSegment(SchemaModel):
    """A contiguous, teachable beat inside one source-deck section.

    The fields are optional-by-default at the containing section level so legacy
    LearningContent and the knowledge-organized pipeline remain compatible.
    """

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: str = "concept"
    teaching_goal: str = ""
    summary: str = ""
    page_refs: list[PageRef] = Field(default_factory=list)
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    prerequisite_segment_ids: list[str] = Field(default_factory=list)
    transition_to_next: str = ""
    suggested_delivery: str = ""
    interaction_opportunities: list[InteractionOpportunity] = Field(default_factory=list)
    order: int = Field(default=1, ge=1)


class KnowledgeRelation(SchemaModel):
    target_unit_id: str = Field(min_length=1)
    relation_type: str = "related_to"
    reason: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KnowledgeUnit(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    unit_type: str = "concept"
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    concepts: list[ConceptNote] = Field(default_factory=list)
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    formulas: list[FormulaNote] = Field(default_factory=list)
    examples: list[ExampleNote] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    page_refs: list[PageRef] = Field(default_factory=list)
    source_unit_ids: list[str] = Field(default_factory=list)
    relations: list[KnowledgeRelation] = Field(default_factory=list)
    importance: str = "supporting"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KnowledgeMergeGroup(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    unit_type: str = "concept"
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KnowledgeRelationDraft(SchemaModel):
    source_group_id: str = Field(min_length=1)
    target_group_id: str = Field(min_length=1)
    relation_type: str = "related_to"
    reason: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KnowledgeCanonicalizationDraft(SchemaModel):
    groups: list[KnowledgeMergeGroup] = Field(min_length=1)
    relations: list[KnowledgeRelationDraft] = Field(default_factory=list)


class CourseKnowledgeTreeNode(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: str = "concept"
    summary: str = ""
    parent_id: str | None = None
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    order: int = Field(default=1, ge=1)
    prerequisite_node_ids: list[str] = Field(default_factory=list)
    node_type: Literal["section", "segment", "knowledge_unit"] | None = None
    ref_id: str | None = None
    page_refs: list[PageRef] = Field(default_factory=list)


class CourseKnowledgeTree(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    nodes: list[CourseKnowledgeTreeNode] = Field(min_length=1)
    root_node_ids: list[str] = Field(min_length=1)
    teaching_sequence: list[str] = Field(default_factory=list)
    orphan_unit_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PageUnderstandingDraft(SchemaModel):
    summary: str
    knowledge_points: list[str] = Field(default_factory=list)
    teaching_focus: list[str] = Field(default_factory=list)
    possible_questions: list[str] = Field(default_factory=list)
    quiz_items: list["QuizItemDraft"] = Field(default_factory=list)
    expanded_explanation: str = ""
    visual_description: str = ""
    depends_on_pages: list[int] = Field(default_factory=list)
    leads_to_pages: list[int] = Field(default_factory=list)
    transition_to_next: str = ""
    page_role: str = "concept"
    teachable_points: list[TeachingPoint] = Field(default_factory=list)
    key_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    concepts: list[ConceptNote] = Field(default_factory=list)
    formulas: list[FormulaNote] = Field(default_factory=list)
    visual_analysis: dict = Field(default_factory=dict)
    misconceptions: list[Misconception] = Field(default_factory=list)
    same_topic_pages: list[int] = Field(default_factory=list)

    @field_validator("teachable_points", mode="before")
    @classmethod
    def discard_empty_teachable_points(cls, value):
        if not isinstance(value, list):
            return value
        cleaned = []
        for item in value:
            if isinstance(item, str):
                point = item.strip()
                if point:
                    cleaned.append({"point": point})
                continue
            if not isinstance(item, dict):
                continue
            point = str(item.get("point") or "").strip()
            if point:
                cleaned.append({**item, "point": point})
        return cleaned


class PageUnderstanding(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    page_role: str = "concept"
    title: str = ""
    summary: str
    teachable_points: list[TeachingPoint] = Field(default_factory=list)
    key_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    concepts: list[ConceptNote] = Field(default_factory=list)
    formulas: list[FormulaNote] = Field(default_factory=list)
    visual_analysis: dict = Field(default_factory=dict)
    knowledge_points: list[str] = Field(default_factory=list)
    teaching_focus: list[str] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    possible_questions: list[str] = Field(default_factory=list)
    quiz_items: list["QuizItemDraft"] = Field(default_factory=list)
    relations: dict = Field(default_factory=dict)
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


class QuizItemDraft(SchemaModel):
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    correct_index: int = Field(ge=0)
    explanation: str
    knowledge_point: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_quiz_item(cls, value):
        if not isinstance(value, dict):
            return value
        item = dict(value)
        options = item.get("options") or []
        if isinstance(options, dict):
            options = list(options.values())
            item["options"] = options
        correct_index = item.get("correct_index")
        if correct_index is None and "correct_answer" in item:
            item["correct_index"] = cls._correct_index_from_answer(
                item.get("correct_answer"),
                options,
            )
        item.setdefault("explanation", item.get("reason") or item.get("analysis") or "")
        item.setdefault(
            "knowledge_point",
            item.get("knowledge_point")
            or item.get("concept")
            or item.get("target_concept")
            or item.get("question")
            or "checkpoint",
        )
        for extra_key in (
            "correct_answer",
            "answer",
            "reason",
            "analysis",
            "concept",
            "target_concept",
        ):
            item.pop(extra_key, None)
        return item

    @staticmethod
    def _correct_index_from_answer(answer, options: list) -> int:
        if isinstance(answer, int):
            return max(answer, 0)
        if not isinstance(answer, str):
            return 0
        stripped = answer.strip()
        if len(stripped) == 1 and stripped.isalpha():
            index = ord(stripped.upper()) - ord("A")
            if 0 <= index < len(options):
                return index
        for index, option in enumerate(options):
            if stripped == str(option).strip():
                return index
        return 0

    @model_validator(mode="after")
    def correct_index_must_exist(self) -> "QuizItemDraft":
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index must point to an existing option")
        return self


class LearningSectionDraft(SchemaModel):
    title: str = Field(min_length=1)
    page_nos: list[int] = Field(default_factory=list)
    summary: str
    teaching_script: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    role: str = "concept"
    content_goal: str = ""
    key_points: list[str] = Field(default_factory=list)
    teaching_narrative: str = ""
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    formulas: list[FormulaNote] = Field(default_factory=list)
    examples: list[ExampleNote] = Field(default_factory=list)
    visual_opportunities: list[VisualOpportunity] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    interaction_opportunities: list[InteractionOpportunity] = Field(default_factory=list)
    page_refs: list[PageRef] = Field(default_factory=list)
    tree_node_ids: list[str] = Field(default_factory=list)
    visual_summary: str = ""
    transition_to_next: str = ""
    quiz_items: list[QuizItemDraft] = Field(default_factory=list)
    segments: list[TeachingSegment] = Field(default_factory=list)


class LearningContentDraft(SchemaModel):
    title: str = Field(min_length=1)
    subtitle: str = ""
    objectives: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    audience: dict = Field(default_factory=dict)
    teaching_intent: dict = Field(default_factory=dict)
    material_overview: dict = Field(default_factory=dict)
    global_concepts: list[ConceptNote] = Field(default_factory=list)
    generation_guidance: dict = Field(default_factory=dict)
    quality: dict = Field(default_factory=dict)
    sections: list[LearningSectionDraft] = Field(min_length=1)

    @field_validator("outline", mode="before")
    @classmethod
    def normalize_outline(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, dict):
                    result.append(
                        str(
                            item.get("section_title")
                            or item.get("title")
                            or item.get("name")
                            or item
                        )
                    )
                else:
                    result.append(str(item))
            return result
        return value

    @field_validator("audience", "teaching_intent", "material_overview", "generation_guidance", mode="before")
    @classmethod
    def normalize_dict_field(cls, value):
        if value is None:
            return {}
        if isinstance(value, str):
            return {"description": value}
        return value

    @field_validator("quality", mode="before")
    @classmethod
    def normalize_quality(cls, value):
        if value is None:
            return {}
        if isinstance(value, str):
            return {"rating": value}
        return value

    @field_validator("global_concepts", mode="before")
    @classmethod
    def normalize_global_concepts(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [{"name": value[:80], "plain_explanation": value}]
        if isinstance(value, list):
            result = []
            for index, item in enumerate(value, start=1):
                if isinstance(item, str):
                    result.append(
                        {
                            "id": f"global_concept_{index:02d}",
                            "name": item[:80],
                            "plain_explanation": item,
                        }
                    )
                else:
                    result.append(item)
            return result
        return value


class SourceDeckPageFlowDraft(SchemaModel):
    page_no: int = Field(ge=1)
    page_role: Literal[
        "cover",
        "agenda",
        "section",
        "transition",
        "concept",
        "method",
        "formula",
        "example",
        "data",
        "summary",
        "exercise",
        "reference",
        "appendix",
    ] = "concept"
    chapter_title: str = Field(min_length=1)
    content_summary: str = ""
    teaching_purpose: str = ""
    logic_from_previous: str = ""
    leads_to_next: str = ""

    @field_validator(
        "content_summary",
        "teaching_purpose",
        "logic_from_previous",
        "leads_to_next",
        mode="before",
    )
    @classmethod
    def normalize_nullable_text(cls, value):
        return "" if value is None else value


class SourceDeckSectionDraft(SchemaModel):
    title: str = Field(min_length=1)
    role: str = "concept"
    content_goal: str = ""
    page_refs: list[PageRef] = Field(min_length=1)
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    teaching_approach: str = ""
    transition_to_next: str = ""

    @field_validator(
        "role",
        "content_goal",
        "summary",
        "teaching_approach",
        "transition_to_next",
        mode="before",
    )
    @classmethod
    def normalize_nullable_text(cls, value):
        return "" if value is None else value


class SourceDeckTeachingSegmentDraft(SchemaModel):
    """LLM draft for one contiguous teaching segment inside a source section."""

    title: str = Field(min_length=1)
    role: Literal[
        "orientation",
        "motivation",
        "concept",
        "mechanism",
        "method",
        "derivation",
        "comparison",
        "application",
        "practice",
        "summary",
        "reference",
    ] = "concept"
    teaching_goal: str = Field(min_length=1)
    summary: str = ""
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    prerequisite_segment_titles: list[str] = Field(default_factory=list)
    suggested_delivery: str = ""
    transition_to_next: str = ""

    @field_validator("summary", "suggested_delivery", "transition_to_next", mode="before")
    @classmethod
    def normalize_nullable_text(cls, value):
        return "" if value is None else value


class SourceDeckTeachingStructureDraft(SchemaModel):
    section_title: str = Field(min_length=1)
    segments: list[SourceDeckTeachingSegmentDraft] = Field(min_length=1)


class SourceDeckLearningContentDraft(SchemaModel):
    title: str = Field(min_length=1)
    subtitle: str = ""
    objectives: list[str] = Field(default_factory=list)
    structure_summary: str = ""
    detected_agenda: list[str] = Field(default_factory=list)
    page_flow: list[SourceDeckPageFlowDraft] = Field(min_length=1)
    sections: list[SourceDeckSectionDraft] = Field(min_length=1)

    @field_validator("subtitle", "structure_summary", mode="before")
    @classmethod
    def normalize_nullable_text(cls, value):
        return "" if value is None else value


class SourceDeckOutlineDraft(SchemaModel):
    title: str = Field(min_length=1)
    subtitle: str = ""
    objectives: list[str] = Field(default_factory=list)
    structure_summary: str = ""
    detected_agenda: list[str] = Field(default_factory=list)
    sections: list[SourceDeckSectionDraft] = Field(min_length=1)

    @field_validator("subtitle", "structure_summary", mode="before")
    @classmethod
    def normalize_nullable_text(cls, value):
        return "" if value is None else value


class SourceDeckPageFlowBatch(SchemaModel):
    page_flow: list[SourceDeckPageFlowDraft] = Field(min_length=1)


class LearningSection(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: str = "concept"
    content_goal: str = ""
    summary: str
    key_points: list[str] = Field(default_factory=list)
    teaching_narrative: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    formulas: list[FormulaNote] = Field(default_factory=list)
    examples: list[ExampleNote] = Field(default_factory=list)
    visual_opportunities: list[VisualOpportunity] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    interaction_opportunities: list[InteractionOpportunity] = Field(default_factory=list)
    transition: dict = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(min_length=1)
    page_refs: list[PageRef] = Field(default_factory=list)
    tree_node_ids: list[str] = Field(default_factory=list)
    quiz_items: list[QuizItem] = Field(default_factory=list)
    page_nos: list[int] = Field(default_factory=list)
    outline_level: int = Field(default=1, ge=1)
    teaching_script: str = ""
    visual_summary: str = ""
    transition_to_next: str = ""
    segments: list[TeachingSegment] = Field(default_factory=list)


class LearningContent(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    material_ids: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    organization_mode: Literal["knowledge", "source_deck", "paper_deck"] = "knowledge"
    title: str = Field(min_length=1)
    subtitle: str = ""
    audience: dict = Field(default_factory=dict)
    teaching_intent: dict = Field(default_factory=dict)
    material_overview: dict = Field(default_factory=dict)
    global_concepts: list[ConceptNote] = Field(default_factory=list)
    knowledge_units: list[KnowledgeUnit] = Field(default_factory=list)
    knowledge_tree: CourseKnowledgeTree | None = None
    objectives: list[str] = Field(default_factory=list)
    sections: list[LearningSection] = Field(min_length=1)
    generation_guidance: dict = Field(default_factory=dict)
    quality: dict = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LearningContentDiagnostics(SchemaModel):
    content_id: str = Field(min_length=1)
    knowledge_units: list[KnowledgeUnit] = Field(default_factory=list)
    knowledge_tree: CourseKnowledgeTree | None = None
    quality: dict = Field(default_factory=dict)


class ContentGenerationJob(SchemaModel):
    id: str = Field(min_length=1)
    material_id: str | None = None
    collection_id: str | None = None
    organization_mode: Literal["knowledge", "source_deck", "paper_deck"] = "knowledge"
    status: ContentGenerationJobStatus = ContentGenerationJobStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    step: str = "queued"
    message: str = "Waiting to generate learning content"
    content_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
