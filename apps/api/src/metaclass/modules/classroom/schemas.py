from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from metaclass.core.schemas import SchemaModel, utc_now
from metaclass.modules.assessment.schemas import Evidence, MasteryEstimate
from metaclass.modules.classroom.agent_schemas import (
    AgentTurn,
    DirectedAgentTurn,
    StudentAgentState,
    StudentAgentType,
)
from metaclass.modules.content.schemas import QuizItem
from metaclass.modules.materials.schemas import SourceRef


class LearningMode(StrEnum):
    LECTURE = "lecture"
    INTERACTIVE = "interactive"


class ActionType(StrEnum):
    SHOW_PAGE = "SHOW_PAGE"
    EXPLAIN = "EXPLAIN"
    ASK_QUIZ = "ASK_QUIZ"
    PROBE = "PROBE"
    WAIT_STUDENT = "WAIT_STUDENT"
    GIVE_FEEDBACK = "GIVE_FEEDBACK"
    REMEDIATE = "REMEDIATE"
    SUMMARIZE = "SUMMARIZE"
    REVIEW = "REVIEW"
    END = "END"


class ShowPagePayload(SchemaModel):
    source_ref: SourceRef
    slide_no: int | None = Field(default=None, ge=1)


class ExplainPayload(SchemaModel):
    text: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)


class AskQuizPayload(SchemaModel):
    quiz: QuizItem


class ProbePayload(SchemaModel):
    question: str = Field(min_length=1)
    target_knowledge_point: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)


class WaitStudentPayload(SchemaModel):
    prompt: str = Field(min_length=1)
    expected_event: Literal["quiz_answer", "free_answer"]


class GiveFeedbackPayload(SchemaModel):
    quiz_action_id: str = Field(min_length=1)


class RemediatePayload(SchemaModel):
    text: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)


class SummarizePayload(SchemaModel):
    text: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)


class ReviewPayload(SchemaModel):
    text: str = Field(min_length=1)
    knowledge_points: list[str] = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)


class EndPayload(SchemaModel):
    summary: str = Field(min_length=1)


class ShowPageAction(SchemaModel):
    id: str
    type: Literal["SHOW_PAGE"]
    actor: Literal["system"]
    payload: ShowPagePayload


class ExplainAction(SchemaModel):
    id: str
    type: Literal["EXPLAIN"]
    actor: Literal["teacher"]
    payload: ExplainPayload


class AskQuizAction(SchemaModel):
    id: str
    type: Literal["ASK_QUIZ"]
    actor: Literal["teacher"]
    payload: AskQuizPayload


class ProbeAction(SchemaModel):
    id: str
    type: Literal["PROBE"]
    actor: Literal["teacher"]
    payload: ProbePayload


class WaitStudentAction(SchemaModel):
    id: str
    type: Literal["WAIT_STUDENT"]
    actor: Literal["system"]
    payload: WaitStudentPayload


class GiveFeedbackAction(SchemaModel):
    id: str
    type: Literal["GIVE_FEEDBACK"]
    actor: Literal["evaluator"]
    payload: GiveFeedbackPayload


class RemediateAction(SchemaModel):
    id: str
    type: Literal["REMEDIATE"]
    actor: Literal["teacher"]
    payload: RemediatePayload


class SummarizeAction(SchemaModel):
    id: str
    type: Literal["SUMMARIZE"]
    actor: Literal["teacher"]
    payload: SummarizePayload


class ReviewAction(SchemaModel):
    id: str
    type: Literal["REVIEW"]
    actor: Literal["teacher"]
    payload: ReviewPayload


class EndAction(SchemaModel):
    id: str
    type: Literal["END"]
    actor: Literal["system"]
    payload: EndPayload


TeachingAction = Annotated[
    ShowPageAction
    | ExplainAction
    | AskQuizAction
    | ProbeAction
    | WaitStudentAction
    | GiveFeedbackAction
    | RemediateAction
    | SummarizeAction
    | ReviewAction
    | EndAction,
    Field(discriminator="type"),
]


class ClassroomScene(SchemaModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    actions: list[TeachingAction] = Field(min_length=1)


class ClassroomPlan(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    scenes: list[ClassroomScene] = Field(min_length=1)
    version: int = Field(default=1, ge=1)


class ClassroomPlanGenerationMeta(SchemaModel):
    plan_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    source: Literal["llm", "fallback"]
    provider: str = Field(min_length=1)
    model: str | None = None
    fallback_reason: str | None = None
    raw_response: str | None = None
    parsed_blueprint: dict | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ClassroomPlanJob(SchemaModel):
    id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    presentation_plan_id: str | None = None
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    step: Literal["queued", "planning", "persisting", "completed", "failed"] = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = Field(min_length=1)
    plan_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ActionExecutedPayload(SchemaModel):
    action_id: str
    action_type: ActionType


class QuizEvaluatedPayload(SchemaModel):
    action_id: str
    feedback_action_id: str
    selected_index: int = Field(ge=0)
    correct: bool


class UserQuestionPayload(SchemaModel):
    question: str = Field(min_length=1)


class TeacherAnswerPayload(SchemaModel):
    answer: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class AgentTurnPayload(SchemaModel):
    turn: AgentTurn


class ClassroomEventBase(SchemaModel):
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class ActionExecutedEvent(ClassroomEventBase):
    type: Literal["ACTION_EXECUTED"]
    payload: ActionExecutedPayload


class QuizEvaluatedEvent(ClassroomEventBase):
    type: Literal["QUIZ_EVALUATED"]
    payload: QuizEvaluatedPayload


class UserQuestionEvent(ClassroomEventBase):
    type: Literal["USER_QUESTION"]
    payload: UserQuestionPayload


class TeacherAnswerEvent(ClassroomEventBase):
    type: Literal["TEACHER_ANSWER"]
    payload: TeacherAnswerPayload


class AgentTurnEvent(ClassroomEventBase):
    type: Literal["AGENT_TURN"]
    payload: AgentTurnPayload


ClassroomEvent = Annotated[
    ActionExecutedEvent
    | QuizEvaluatedEvent
    | UserQuestionEvent
    | TeacherAnswerEvent
    | AgentTurnEvent,
    Field(discriminator="type"),
]


class ClassroomSession(SchemaModel):
    id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    mode: LearningMode = LearningMode.LECTURE
    status: Literal["running", "completed"] = "running"
    scene_index: int = Field(default=0, ge=0)
    action_index: int = Field(default=0, ge=0)
    waiting_for: Literal["quiz_answer", "free_answer"] | None = None
    student_states: list[StudentAgentState] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    mastery: list[MasteryEstimate] = Field(default_factory=list)
    events: list[ClassroomEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClassroomState(SchemaModel):
    session_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    mode: LearningMode
    status: Literal["running", "completed"]
    scene_index: int = Field(ge=0)
    action_index: int = Field(ge=0)
    current_scene_id: str | None = None
    current_scene_title: str | None = None
    current_action_id: str | None = None
    current_action_type: ActionType | None = None
    waiting_for: Literal["quiz_answer", "free_answer"] | None = None
    students: list[StudentAgentState] = Field(default_factory=list)
    mastery: list[MasteryEstimate] = Field(default_factory=list)
    recent_events: list[ClassroomEvent] = Field(default_factory=list)


class AnswerRequest(SchemaModel):
    selected_index: int = Field(ge=0)


class QuestionRequest(SchemaModel):
    question: str = Field(min_length=1, max_length=1000)


class CreateClassroomSessionRequest(SchemaModel):
    mode: LearningMode = LearningMode.LECTURE
    student_agent_types: list[StudentAgentType] | None = Field(default=None, max_length=8)

    @field_validator("student_agent_types", mode="before")
    @classmethod
    def normalize_legacy_student_agent_types(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            StudentAgentType.__members__[item].value
            if isinstance(item, str) and item in StudentAgentType.__members__
            else item
            for item in value
        ]


class AgentTurnRequest(SchemaModel):
    prompt: str = Field(min_length=1, max_length=2000)


class ControllerResult(SchemaModel):
    status: Literal["action", "waiting", "completed", "evaluated", "answered"]
    action: TeachingAction | None = None
    feedback: str | None = None
    correct: bool | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    session: ClassroomSession


class AutoClassroomStep(SchemaModel):
    status: Literal[
        "action",
        "agent_turn",
        "quiz_answered",
        "waiting",
        "completed",
    ]
    action: TeachingAction | None = None
    directed_turn: DirectedAgentTurn | None = None
    feedback: str | None = None
    correct: bool | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    session: ClassroomSession
