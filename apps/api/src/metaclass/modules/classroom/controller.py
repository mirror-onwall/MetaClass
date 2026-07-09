from __future__ import annotations

import json

from pydantic import ValidationError

from metaclass.infrastructure.providers.llm import FakeLLMProvider, LLMMessage, LLMProvider
from metaclass.modules.classroom.agent_schemas import ControllerDecision
from metaclass.modules.classroom.schemas import ClassroomState


class ClassroomController:
    """LLM director for deciding the next classroom speaker.

    This is the MetaClass equivalent of OpenMAIC's director: it does not speak
    for agents. It only decides who should take the next turn and gives that
    agent a concise prompt.
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm or FakeLLMProvider()

    def decide(self, state: ClassroomState) -> ControllerDecision:
        raw = self.llm.complete_json(self._build_messages(state), temperature=0.0)
        return self._parse_decision(raw, state)

    @staticmethod
    def _build_messages(state: ClassroomState) -> list[LLMMessage]:
        system = """你是 MetaClass 课堂调度器，也就是多智能体课堂里的 director/controller。

# 你的职责
你只负责决定“下一轮应该由谁发言或接管”，不要替任何角色发言。
你需要根据 ClassroomState 判断：
- teacher 是否应该继续讲解、追问、总结、回顾或补救；
- student 是否应该回答、提问、表达困惑或做简短总结；
- evaluator 是否应该介入评估；
- 课堂是否应该结束。

# 可选 next_role
- "teacher"：教师接管下一轮。
- "student"：某个学生智能体接管下一轮。
- "evaluator"：评估器接管下一轮。
- "end"：课堂已经结束。

# 学生选择规则
如果选择 student，必须尽量填写 next_agent_id：
- 小测、追问、挑战理解：优先 student_agent_002（深度思考者）。
- 总结、整理重点：优先 student_agent_003（课堂笔记员）。
- 生活化类比、降低压力：优先 student_agent_001（课堂气氛调节者）。
- 应用迁移、研究讨论：优先 student_agent_004（研究型同学）。

# 决策原则
1. 如果 status 是 completed，选择 end。
2. 如果 mode 是 "lecture"，这是连续讲解模式：只能选择 teacher 或 end，不允许选择 student，不允许同学插嘴。
3. 如果 mode 是 "interactive"，这是互动课堂模式：可以选择 student 插嘴、回答或总结。
4. 如果 waiting_for 是 quiz_answer 且 mode 是 interactive，选择 student。
5. 如果 current_action_type 是 ASK_QUIZ 且 mode 是 interactive，选择 student。
6. 如果 current_action_type 是 EXPLAIN、REVIEW、REMEDIATE，通常选择 teacher。
7. 如果 current_action_type 是 SUMMARIZE 且 mode 是 interactive，优先选择课堂笔记员；lecture 模式仍选择 teacher。
8. 不要让学生长篇授课；学生只负责短反馈、短问题、短总结。
9. prompt 应该是一句给下一个 agent 的具体指令，不要太长。

# 输出格式
你必须只输出一个 JSON object，不要 markdown，不要代码块，不要解释。
格式：
{
  "next_role": "teacher|student|evaluator|end",
  "next_agent_id": "teacher 或 student_agent_xxx 或 evaluator；end 时为 null",
  "reason": "为什么这么调度",
  "prompt": "给下一位 agent 的一句具体指令"
}
"""
        user = {
            "classroom_state": state.model_dump(mode="json"),
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]

    @staticmethod
    def _parse_decision(raw: str, state: ClassroomState) -> ControllerDecision:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM controller did not return valid JSON: {raw[:300]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"LLM controller JSON must be an object: {payload}")
        if state.mode == "lecture" and payload.get("next_role") == "student":
            payload = {
                "next_role": "teacher",
                "next_agent_id": "teacher",
                "reason": "连续讲解模式禁止学生智能体插嘴，已转为教师继续讲解",
                "prompt": "请作为老师继续按课程计划讲解当前内容，不安排学生插话。",
            }
        if payload.get("next_role") == "student" and not payload.get("next_agent_id"):
            payload["next_agent_id"] = state.students[0].id if state.students else None
        try:
            return ControllerDecision.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(f"LLM controller decision failed validation: {payload}") from exc
