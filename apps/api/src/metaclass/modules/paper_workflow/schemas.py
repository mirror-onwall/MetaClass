"""Versioned contracts shared by the composed paper-workflow stages.

Stage boundaries are deliberately data-only: a stage may change its internal Skill
implementation without changing the contract consumed by the following stage.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from metaclass.core.schemas import SchemaModel, utc_now

SCHEMA_VERSION = "1.0"


class PaperWorkflowStrategy(StrEnum):
    AUTO = "auto"
    COMPOSED_SKILLS = "composed_skills"
    NATURE_PAPER2PPT = "nature_paper2ppt"


class PaperWorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_REVIEW = "waiting_for_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class PaperWorkflowStage(StrEnum):
    PREPARE_SOURCE = "prepare_source"
    ANALYZE_PAPER = "analyze_paper"
    PREPARE_FIGURES = "prepare_figures"
    PLAN_PRESENTATION = "plan_presentation"
    GENERATE_DECK = "generate_deck"
    NORMALIZE_ARTIFACTS = "normalize_artifacts"
    VALIDATE_ARTIFACTS = "validate_artifacts"
    REGISTER_MATERIAL = "register_material"
    COMPLETED = "completed"


class ComposedStage(StrEnum):
    ANALYSIS = "analysis"
    FIGURES = "figures"
    OUTLINE = "outline"
    GENERATION = "generation"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


class ValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SourceReference(SchemaModel):
    page_no: int = Field(ge=1)
    block_id: str | None = None
    asset_id: str | None = None
    quote: str | None = None


class PaperWorkflowRequest(SchemaModel):
    """Stable user intent. Runtime state belongs in :class:`PaperWorkflowJob`."""

    schema_version: str = SCHEMA_VERSION
    material_id: str = Field(min_length=1)
    strategy: PaperWorkflowStrategy = PaperWorkflowStrategy.AUTO
    duration_minutes: int | None = Field(default=None, ge=5, le=120)
    audience: str | None = None
    language: str = Field(default="zh-CN", min_length=2)
    depth: Literal["introductory", "standard", "advanced"] = "standard"


class PaperWorkflowSettings(SchemaModel):
    """The minimal deferred context required by the Nature provider."""

    duration_minutes: int = Field(ge=5, le=120)
    audience: str = Field(min_length=1)


class PaperDeckCourseResult(SchemaModel):
    paper_job_id: str = Field(min_length=1)
    derived_material_id: str = Field(min_length=1)
    source_paper_material_id: str = Field(min_length=1)
    artifact_bundle_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    presentation_plan_id: str = Field(min_length=1)


class RequiredWorkflowInput(SchemaModel):
    reason: str = Field(min_length=1)
    fields: list[Literal["duration_minutes", "audience"]] = Field(min_length=1)


class PaperWorkflowJob(SchemaModel):
    """API-facing lifecycle state for one paper workflow execution."""

    schema_version: str = SCHEMA_VERSION
    id: str = Field(min_length=1)
    source_material_id: str = Field(min_length=1)
    strategy_requested: PaperWorkflowStrategy = PaperWorkflowStrategy.AUTO
    strategy_selected: PaperWorkflowStrategy | None = None
    status: PaperWorkflowStatus = PaperWorkflowStatus.QUEUED
    stage: PaperWorkflowStage = PaperWorkflowStage.PREPARE_SOURCE
    progress: float = Field(default=0, ge=0, le=1)
    provider_attempts: list[str] = Field(default_factory=list)
    checkpoint_version: int = Field(default=0, ge=0)
    artifact_bundle_id: str | None = None
    derived_material_id: str | None = None
    fallback_reason: str | None = None
    required_input: RequiredWorkflowInput | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_state_payload(self) -> "PaperWorkflowJob":
        if self.status == PaperWorkflowStatus.SUCCEEDED and (
            not self.artifact_bundle_id or not self.derived_material_id
        ):
            raise ValueError("succeeded job requires artifact_bundle_id and derived_material_id")
        if self.status == PaperWorkflowStatus.FAILED and not self.error:
            raise ValueError("failed job requires error")
        if self.status == PaperWorkflowStatus.WAITING_FOR_INPUT and not self.required_input:
            raise ValueError("waiting_for_input job requires required_input")
        return self


class SourceSection(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    level: int = Field(ge=1)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_page_range(self) -> "SourceSection":
        if self.end_page < self.start_page:
            raise ValueError("section end_page must not precede start_page")
        return self


class SourceBlock(SchemaModel):
    id: str = Field(min_length=1)
    type: Literal[
        "title", "paragraph", "figure", "table", "equation", "algorithm", "code", "reference"
    ]
    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    section_path: list[str] = Field(default_factory=list)
    text: str = ""


class SourceAsset(SchemaModel):
    id: str = Field(min_length=1)
    type: Literal["figure", "table", "equation", "other"]
    page_no: int = Field(ge=1)
    path: str = Field(min_length=1)
    caption: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    source: Literal["mineru", "pdf", "arxiv_source", "other"] = "mineru"


class BBoxCoordinateSystem(SchemaModel):
    origin: Literal["top_left"] = "top_left"
    unit: Literal["normalized_1000"] = "normalized_1000"
    bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)


class PaperSourceBundle(SchemaModel):
    """Read-only, normalized Stage 1/2 input projected from the source PDF."""

    schema_version: str = SCHEMA_VERSION
    material_id: str = Field(min_length=1)
    file_hash: str = Field(pattern=r"^(sha256:)?[0-9a-fA-F]{64}$")
    page_count: int = Field(ge=1)
    pdf_path: str = Field(min_length=1)
    paper_source_path: str = Field(min_length=1)
    paper_content_path: str = Field(min_length=1)
    asset_directory: str = Field(min_length=1)
    parser_source: Literal["content_list_v2", "content_list", "middle", "page_metadata"] = (
        "page_metadata"
    )
    bbox_coordinate_system: BBoxCoordinateSystem = Field(default_factory=BBoxCoordinateSystem)
    metadata_candidates: dict[str, object] = Field(default_factory=dict)
    sections: list[SourceSection] = Field(default_factory=list)
    blocks: list[SourceBlock] = Field(default_factory=list)
    assets: list[SourceAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_identity(self) -> "PaperSourceBundle":
        block_ids = [item.id for item in self.blocks]
        asset_ids = [item.id for item in self.assets]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("source block ids must be unique")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("source asset ids must be unique")
        if any(item.page_no > self.page_count for item in [*self.blocks, *self.assets]):
            raise ValueError("source item page_no exceeds page_count")
        return self


class PaperClaim(SchemaModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    importance: Literal["core", "supporting", "context"] = "supporting"
    confidence: float = Field(ge=0, le=1)
    source_refs: list[SourceReference] = Field(min_length=1)


class QuantitativeResult(SchemaModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    metric: str | None = None
    value: str | float | int | None = None
    source_refs: list[SourceReference] = Field(min_length=1)


class PaperLimitation(SchemaModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    source_refs: list[SourceReference] = Field(min_length=1)


class FigureCandidate(SchemaModel):
    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(min_length=1)


class PaperAnalysis(SchemaModel):
    """Stage 1 output and the authority for paper facts used downstream."""

    schema_version: str = SCHEMA_VERSION
    paper: dict[str, object] = Field(default_factory=dict)
    paper_type: Literal["methods", "dataset", "experimental", "review", "clinical", "other"]
    central_question: str = Field(min_length=1)
    knowledge_gap: str = Field(min_length=1)
    main_claim: str = Field(min_length=1)
    method_summary: dict[str, object] = Field(default_factory=dict)
    claims: list[PaperClaim] = Field(min_length=1)
    experiments: list[dict[str, object]] = Field(default_factory=list)
    quantitative_results: list[QuantitativeResult] = Field(default_factory=list)
    limitations: list[PaperLimitation] = Field(default_factory=list)
    figure_candidates: list[FigureCandidate] = Field(default_factory=list)
    terminology: list[dict[str, object]] = Field(default_factory=list)
    critical_assessment: dict[str, object] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claims(self) -> "PaperAnalysis":
        claim_ids = [item.id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim ids must be unique")
        if not any(item.importance == "core" for item in self.claims):
            raise ValueError("paper analysis requires at least one core claim")
        unknown = {
            claim_id
            for candidate in self.figure_candidates
            for claim_id in candidate.claim_ids
            if claim_id not in set(claim_ids)
        }
        if unknown:
            raise ValueError(f"figure candidates reference unknown claims: {sorted(unknown)}")
        return self


class FigureQuality(SchemaModel):
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    readable: bool


class FigureAsset(SchemaModel):
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    original_figure: str | None = None
    panel: str | None = None
    page_no: int = Field(ge=1)
    caption: str = Field(min_length=1)
    source_method: Literal["mineru", "pdf_crop", "arxiv_source", "other"]
    supports_claim_ids: list[str] = Field(default_factory=list)
    quality: FigureQuality
    crop_notes: str | None = None


class FigureCatalog(SchemaModel):
    """Stage 2 output; the only visual-asset authority for Stages 3 and 4."""

    schema_version: str = SCHEMA_VERSION
    figures: list[FigureAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_figure_ids(self) -> "FigureCatalog":
        ids = [item.id for item in self.figures]
        if len(ids) != len(set(ids)):
            raise ValueError("figure ids must be unique")
        return self


class OutlineSection(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: str = Field(min_length=1)
    content_goal: str = Field(min_length=1)
    slide_ids: list[str] = Field(min_length=1)


class OutlineSlide(SchemaModel):
    id: str = Field(min_length=1)
    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    speaker_note: str = ""
    layout_intent: str = Field(min_length=1)


class PresentationOutline(SchemaModel):
    """Stage 3 narrative contract; Stage 4 may render but not reinterpret it."""

    schema_version: str = SCHEMA_VERSION
    title: str = Field(min_length=1)
    subtitle: str | None = None
    paper_type: str = Field(min_length=1)
    narrative_arc: str = Field(min_length=1)
    objectives: list[str] = Field(default_factory=list)
    structure_summary: str = Field(min_length=1)
    sections: list[OutlineSection] = Field(min_length=1)
    slides: list[OutlineSlide] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_slide_order_and_sections(self) -> "PresentationOutline":
        ids = [slide.id for slide in self.slides]
        orders = [slide.order for slide in self.slides]
        if len(ids) != len(set(ids)):
            raise ValueError("outline slide ids must be unique")
        if orders != list(range(1, len(self.slides) + 1)):
            raise ValueError("outline slide order must be continuous and start at 1")
        referenced = [slide_id for section in self.sections for slide_id in section.slide_ids]
        if referenced != ids:
            raise ValueError("sections must cover all slides once and in presentation order")
        return self


class SlideEvidenceEntry(SchemaModel):
    slide_id: str = Field(min_length=1)
    page_no: int | None = Field(default=None, ge=1)
    claim_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_strength: Literal["direct", "derived", "contextual"] = "direct"

    @model_validator(mode="after")
    def require_evidence(self) -> "SlideEvidenceEntry":
        if not self.claim_ids and not self.source_refs and not self.asset_ids:
            raise ValueError("slide evidence entry must contain at least one evidence reference")
        return self


class SlideEvidence(SchemaModel):
    """Stage 3 evidence contract mapping every outline slide back to the paper."""

    schema_version: str = SCHEMA_VERSION
    slides: list[SlideEvidenceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_slide_ids(self) -> "SlideEvidence":
        ids = [item.slide_id for item in self.slides]
        if len(ids) != len(set(ids)):
            raise ValueError("slide evidence ids must be unique")
        return self


class ValidationIssue(SchemaModel):
    severity: ValidationSeverity
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None


class StageExecutionReport(SchemaModel):
    """Auditable runtime result for one attempt of one composed stage."""

    schema_version: str = SCHEMA_VERSION
    stage: ComposedStage
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    skill_commit: str | None = None
    model: str | None = None
    status: StageStatus
    attempt: int = Field(default=1, ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    input_hash: str = Field(pattern=r"^(sha256:)?[0-9a-fA-F]{64}$")
    exit_code: int | None = None
    timed_out: bool = False
    outputs: list[str] = Field(default_factory=list)
    validation_passed: bool | None = None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    stdout_path: str | None = None
    stderr_path: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_terminal_report(self) -> "StageExecutionReport":
        terminal = {
            StageStatus.SUCCEEDED,
            StageStatus.FAILED,
            StageStatus.SKIPPED,
            StageStatus.CANCELED,
        }
        if self.status in terminal and self.finished_at is None:
            raise ValueError("terminal stage report requires finished_at")
        if self.status == StageStatus.SUCCEEDED:
            if not self.outputs:
                raise ValueError("succeeded stage report requires outputs")
            if self.validation_passed is not True:
                raise ValueError("succeeded stage report requires validation_passed=true")
        if self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class StageCheckpoint(SchemaModel):
    stage: ComposedStage
    status: StageStatus = StageStatus.PENDING
    input_hash: str = Field(pattern=r"^(sha256:)?[0-9a-fA-F]{64}$")
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    attempt: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    validation: dict[str, object] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_stage_timestamps(self) -> "StageCheckpoint":
        if self.status == StageStatus.RUNNING and self.started_at is None:
            raise ValueError("running stage checkpoint requires started_at")
        if self.status in {StageStatus.SUCCEEDED, StageStatus.FAILED, StageStatus.SKIPPED} and (
            self.started_at is None or self.finished_at is None
        ):
            raise ValueError("terminal stage checkpoint requires start and finish timestamps")
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            raise ValueError("stage checkpoint finished_at must not precede started_at")
        if self.status == StageStatus.SUCCEEDED and self.validation.get("passed") is not True:
            raise ValueError("succeeded stage checkpoint requires validation.passed=true")
        return self


class PaperWorkflowCheckpoint(SchemaModel):
    """Durable recovery/cache state. It is more detailed than the API Job view."""

    schema_version: str = SCHEMA_VERSION
    job_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    request_hash: str = Field(pattern=r"^(sha256:)?[0-9a-fA-F]{64}$")
    source_bundle_hash: str | None = Field(default=None, pattern=r"^(sha256:)?[0-9a-fA-F]{64}$")
    stages: dict[ComposedStage, StageCheckpoint] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_stage_keys(self) -> "PaperWorkflowCheckpoint":
        mismatched = [key for key, item in self.stages.items() if key != item.stage]
        if mismatched:
            raise ValueError(f"checkpoint stage keys do not match payloads: {mismatched}")
        return self


class ArtifactFile(SchemaModel):
    role: Literal[
        "presentation",
        "analysis",
        "outline",
        "slide_evidence",
        "asset_manifest",
        "speaker_notes",
        "generation_report",
        "qa_report",
        "asset",
    ]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    media_type: str = Field(min_length=1)
    required: bool = True


class PaperArtifactBundle(SchemaModel):
    """Validated final delivery shared by every paper presentation provider."""

    schema_version: str = SCHEMA_VERSION
    id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    source_material_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    root_path: str = Field(min_length=1)
    files: list[ArtifactFile] = Field(min_length=1)
    validation_status: Literal["pending", "passed", "failed"] = "pending"
    derived_material_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_required_roles(self) -> "PaperArtifactBundle":
        roles = {item.role for item in self.files if item.required}
        required = {
            "presentation",
            "analysis",
            "outline",
            "slide_evidence",
            "asset_manifest",
            "speaker_notes",
            "generation_report",
            "qa_report",
        }
        missing = required - roles
        if missing:
            raise ValueError(f"artifact bundle is missing required roles: {sorted(missing)}")
        return self


class StageBoundary(SchemaModel):
    stage: ComposedStage
    skill_name: str
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    forbidden_responsibilities: tuple[str, ...]


COMPOSED_STAGE_BOUNDARIES: tuple[StageBoundary, ...] = (
    StageBoundary(
        stage=ComposedStage.ANALYSIS,
        skill_name="paper-analyze",
        consumes=("PaperSourceBundle", "PaperWorkflowRequest"),
        produces=("PaperAnalysis", "StageExecutionReport"),
        forbidden_responsibilities=("plan_slides", "generate_pptx", "external_research"),
    ),
    StageBoundary(
        stage=ComposedStage.FIGURES,
        skill_name="extract-paper-images",
        consumes=("PaperSourceBundle", "PaperAnalysis"),
        produces=("FigureCatalog", "StageExecutionReport"),
        forbidden_responsibilities=("reinterpret_paper", "plan_slides", "generate_pptx"),
    ),
    StageBoundary(
        stage=ComposedStage.OUTLINE,
        skill_name="academic-pptx",
        consumes=("PaperAnalysis", "FigureCatalog", "PaperWorkflowRequest"),
        produces=("PresentationOutline", "SlideEvidence", "StageExecutionReport"),
        forbidden_responsibilities=("invent_claims", "extract_figures", "generate_pptx"),
    ),
    StageBoundary(
        stage=ComposedStage.GENERATION,
        skill_name="academic-pptx-generate",
        consumes=("PresentationOutline", "SlideEvidence", "FigureCatalog"),
        produces=("PaperArtifactBundle", "StageExecutionReport"),
        forbidden_responsibilities=("reanalyze_paper", "invent_claims", "change_outline_semantics"),
    ),
)
