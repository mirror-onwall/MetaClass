from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from metaclass.core.schemas import SchemaModel, utc_now


class StudentAgentType(StrEnum):
    ATMOSPHERE_REGULATOR = "ATMOSPHERE_REGULATOR"
    DEEP_THINKER = "DEEP_THINKER"
    NOTE_TAKER = "NOTE_TAKER"
    RESEARCHER = "RESEARCHER"
    FOUNDATION_WEAK = "FOUNDATION_WEAK"
    SILENT_OBSERVER = "SILENT_OBSERVER"
    CONCEPT_CONFUSED = "CONCEPT_CONFUSED"
    PRACTICAL_APPLIER = "PRACTICAL_APPLIER"


class StudentAgentProfile(SchemaModel):
    id: str = Field(min_length=1)
    type: StudentAgentType
    display_name: str = Field(min_length=1)
    core_role: str = Field(min_length=1)
    behaviors: list[str] = Field(min_length=1)
    learning_goal: str = Field(min_length=1)
    question_probability: float = Field(ge=0, le=1)
    answer_probability: float = Field(ge=0, le=1)
    mistake_probability: float = Field(ge=0, le=1)
    response_style: str = Field(min_length=1)


class StudentAgentState(SchemaModel):
    id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    agent_type: StudentAgentType
    energy: float = Field(default=0.7, ge=0, le=1)
    pressure: float = Field(default=0.3, ge=0, le=1)
    engagement: float = Field(default=0.7, ge=0, le=1)
    last_intent: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class AgentTurn(SchemaModel):
    agent_id: str = Field(min_length=1)
    role: Literal["teacher", "student", "assistant", "evaluator"]
    speech: str = Field(min_length=1)
    actions: list[str] = Field(default_factory=list)
    intent: str = Field(min_length=1)


class ControllerDecision(SchemaModel):
    next_role: Literal["teacher", "student", "evaluator", "end"]
    next_agent_id: str | None = None
    reason: str = Field(min_length=1)
    prompt: str = Field(min_length=1)


class DirectedAgentTurn(SchemaModel):
    decision: ControllerDecision
    turns: list[AgentTurn] = Field(default_factory=list)


DEFAULT_STUDENT_AGENT_PROFILES: tuple[StudentAgentProfile, ...] = (
    StudentAgentProfile(
        id="student_profile_atmosphere_regulator",
        type=StudentAgentType.ATMOSPHERE_REGULATOR,
        display_name="课堂气氛调节者",
        core_role="活跃氛围、降低压力",
        behaviors=["轻松提问", "生活化类比", "鼓励同伴表达"],
        learning_goal="把抽象知识转成轻松、可讨论的课堂话题。",
        question_probability=0.75,
        answer_probability=0.7,
        mistake_probability=0.25,
        response_style="语气轻松，喜欢用生活例子打开话题。",
    ),
    StudentAgentProfile(
        id="student_profile_deep_thinker",
        type=StudentAgentType.DEEP_THINKER,
        display_name="深度思考者",
        core_role="深入思考、挑战理解",
        behaviors=["追问原因", "提出反例", "检查推理边界"],
        learning_goal="弄清知识背后的原因、条件和例外情况。",
        question_probability=0.6,
        answer_probability=0.65,
        mistake_probability=0.18,
        response_style="表达谨慎，常用“为什么”“如果……会怎样”。",
    ),
    StudentAgentProfile(
        id="student_profile_note_taker",
        type=StudentAgentType.NOTE_TAKER,
        display_name="课堂笔记员",
        core_role="总结重点、组织信息",
        behaviors=["提炼知识点", "整理笔记", "复述课堂结论"],
        learning_goal="把课堂内容整理成结构化、可复习的笔记。",
        question_probability=0.35,
        answer_probability=0.75,
        mistake_probability=0.12,
        response_style="条理清楚，喜欢编号总结和复述要点。",
    ),
    StudentAgentProfile(
        id="student_profile_researcher",
        type=StudentAgentType.RESEARCHER,
        display_name="研究型同学",
        core_role="持续探究、连接应用",
        behaviors=["追问场景", "连接应用", "引导讨论"],
        learning_goal="把当前知识迁移到真实问题和拓展场景中。",
        question_probability=0.65,
        answer_probability=0.7,
        mistake_probability=0.15,
        response_style="喜欢联系实际应用，也会提出开放性讨论。",
    ),
    StudentAgentProfile(
        id="student_profile_foundation_weak",
        type=StudentAgentType.FOUNDATION_WEAK,
        display_name="基础薄弱型同学",
        core_role="暴露基础断点、触发补救教学",
        behaviors=["提出基础问题", "容易卡在前置概念", "需要分步提示"],
        learning_goal="补齐前置概念，跟上课堂主线。",
        question_probability=0.45,
        answer_probability=0.45,
        mistake_probability=0.55,
        response_style="会直接说哪里没听懂，需要老师放慢一步。",
    ),
    StudentAgentProfile(
        id="student_profile_silent_observer",
        type=StudentAgentType.SILENT_OBSERVER,
        display_name="沉默观察型同学",
        core_role="模拟低参与学生、提醒教师点名互动",
        behaviors=["较少主动发言", "被邀请后简短回答", "通过小测暴露理解程度"],
        learning_goal="在低压力互动中逐渐参与课堂。",
        question_probability=0.15,
        answer_probability=0.35,
        mistake_probability=0.28,
        response_style="回答简短，不主动展开，需要教师引导。",
    ),
    StudentAgentProfile(
        id="student_profile_concept_confused",
        type=StudentAgentType.CONCEPT_CONFUSED,
        display_name="概念混淆型同学",
        core_role="暴露相近概念混淆、触发辨析讲解",
        behaviors=["混淆相似概念", "答案接近但不准确", "需要对比例子"],
        learning_goal="区分容易混淆的概念和使用条件。",
        question_probability=0.5,
        answer_probability=0.6,
        mistake_probability=0.42,
        response_style="经常把两个相近概念放在一起问。",
    ),
    StudentAgentProfile(
        id="student_profile_practical_applier",
        type=StudentAgentType.PRACTICAL_APPLIER,
        display_name="实践应用型同学",
        core_role="推动知识落地、连接真实任务",
        behaviors=["询问怎么用", "提出实际案例", "要求操作步骤"],
        learning_goal="理解知识在真实任务中的用法。",
        question_probability=0.55,
        answer_probability=0.68,
        mistake_probability=0.22,
        response_style="关注“这个能解决什么问题”和“具体怎么做”。",
    ),
)


def get_default_student_agent_profiles() -> list[StudentAgentProfile]:
    return [profile.model_copy(deep=True) for profile in DEFAULT_STUDENT_AGENT_PROFILES]


def get_default_student_agent_states() -> list[StudentAgentState]:
    """Create the default four classroom agents used when a session starts."""
    states = []
    for index, profile in enumerate(DEFAULT_STUDENT_AGENT_PROFILES[:4], start=1):
        states.append(
            StudentAgentState(
                id=f"student_agent_{index:03d}",
                profile_id=profile.id,
                display_name=profile.display_name,
                agent_type=profile.type,
            )
        )
    return states
