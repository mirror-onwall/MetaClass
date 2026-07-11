from metaclass.infrastructure.providers.llm import LLMProvider
from metaclass.modules.classroom.agent_schemas import (
    AgentTurn,
    StudentAgentState,
    StudentAgentType,
    get_default_student_agent_profiles,
    get_default_student_agent_states,
    get_student_agent_states,
)
from metaclass.modules.classroom.agents.prompts import (
    build_student_messages,
    parse_agent_turn_json,
)
from metaclass.modules.classroom.schemas import ClassroomState


class StudentRosterAgent:
    """Creates the default student agents attached to a classroom session."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    def create_default_states(self) -> list[StudentAgentState]:
        return get_default_student_agent_states()

    def create_states(
        self, selected_types: list[StudentAgentType] | None = None
    ) -> list[StudentAgentState]:
        return get_student_agent_states(selected_types)

    def generate_turn(
        self,
        student_state: StudentAgentState,
        classroom_state: ClassroomState,
        prompt: str,
    ) -> AgentTurn:
        profile = next(
            item
            for item in get_default_student_agent_profiles()
            if item.id == student_state.profile_id
        )
        if not self.llm:
            speech_by_type = {
                StudentAgentType.ATMOSPHERE_REGULATOR: "这个点可以类比成生活里的路线选择，感觉一下子没那么紧张了。",
                StudentAgentType.DEEP_THINKER: "我想追问一下：如果前提条件变了，这个结论还成立吗？",
                StudentAgentType.NOTE_TAKER: "我先记下来：定义、例子、易错点，这三个是本页重点。",
                StudentAgentType.RESEARCHER: "这个知识能不能放到真实项目里试一下？应用场景会不会变？",
            }
            return AgentTurn(
                agent_id=student_state.id,
                role="student",
                speech=speech_by_type.get(student_state.agent_type, "我这里有一点没完全听懂。"),
                actions=[],
                intent="fake_student_turn",
            )

        raw = self.llm.complete_json(
            build_student_messages(
                profile=profile,
                state=student_state,
                classroom_state=classroom_state,
                prompt=prompt,
                allowed_actions=["PROBE", "SUMMARIZE"],
            )
        )
        return parse_agent_turn_json(raw, agent_id=student_state.id, role="student")
