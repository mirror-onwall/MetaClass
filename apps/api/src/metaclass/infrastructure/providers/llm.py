from __future__ import annotations

import base64
import json
import mimetypes
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib import error, request

import certifi


LLMRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: LLMRole
    content: str


class LLMProvider(Protocol):
    name: str
    model: str | None

    def complete_json(self, messages: list[LLMMessage], *, temperature: float = 0.2) -> str: ...


class FakeLLMProvider:
    """Deterministic local provider so tests and demos do not require an API key."""

    name = "fake"
    model = None

    def complete_json(self, messages: list[LLMMessage], *, temperature: float = 0.2) -> str:
        system_text = messages[0].content if messages else ""
        user_text = messages[-1].content if messages else ""
        if "MetaClass 的 ClassroomPlan planner" in system_text:
            request_payload = json.loads(user_text)
            scenes = [
                {
                    "section_id": section["id"],
                    "include_probe": index % 3 == 0,
                    "probe_question": f"你能用自己的话解释{section['title']}的核心意思吗？"
                    if index % 3 == 0
                    else None,
                    "include_quiz": bool(section.get("has_quiz")),
                    "include_review": index % 5 == 0,
                    "teaching_note": f"围绕{section['title']}做一个阶段性回顾。"
                    if index % 5 == 0
                    else None,
                }
                for index, section in enumerate(request_payload["sections"], start=1)
            ]
            return json.dumps(
                {
                    "scenes": scenes,
                },
                ensure_ascii=False,
            )
        if "MetaClass 的 PresentationPlan planner" in system_text:
            request_payload = json.loads(user_text)
            slides = [
                {
                    "source_section_ids": [section["id"]],
                    "title": section["title"],
                    "key_points": section.get("knowledge_points") or [section["title"]],
                    "speaker_script": (
                        f"这一页我们讲{section['title']}。"
                        f"{section.get('summary', '')} "
                        "讲解时可以先给出直观解释，再补充一个例子帮助理解。"
                    ),
                    "suggested_visual": (
                        "Use the source page image with a highlighted callout for "
                        f"{section['title']}."
                    ),
                }
                for section in request_payload["sections"]
            ]
            return json.dumps(
                {
                    "title": request_payload["title"],
                    "slides": slides,
                },
                ensure_ascii=False,
            )
        if "MetaClass 课堂调度器" in system_text:
            if '"mode": "lecture"' in user_text:
                return json.dumps(
                    {
                        "next_role": "teacher",
                        "next_agent_id": "teacher",
                        "reason": "连续讲解模式下不安排学生智能体插嘴，由教师继续讲解",
                        "prompt": "请作为老师继续按课程计划讲解当前内容，不安排学生插话。",
                    },
                    ensure_ascii=False,
                )
            if (
                '"waiting_for": "quiz_answer"' in user_text
                or '"current_action_type": "ASK_QUIZ"' in user_text
            ):
                return json.dumps(
                    {
                        "next_role": "student",
                        "next_agent_id": "student_agent_002",
                        "reason": "当前课堂需要学生先回应小测或追问",
                        "prompt": "请你简短说出自己的判断，并说明一个理由或困惑点。",
                    },
                    ensure_ascii=False,
                )
            if '"current_action_type": "SUMMARIZE"' in user_text:
                return json.dumps(
                    {
                        "next_role": "student",
                        "next_agent_id": "student_agent_003",
                        "reason": "当前进入总结阶段，适合让课堂笔记员提炼重点",
                        "prompt": "请你用一句话总结刚才的重点。",
                    },
                    ensure_ascii=False,
                )
            if '"status": "completed"' in user_text:
                return json.dumps(
                    {
                        "next_role": "end",
                        "next_agent_id": None,
                        "reason": "课堂已经完成",
                        "prompt": "本轮课堂已经结束。",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "next_role": "teacher",
                    "next_agent_id": "teacher",
                    "reason": "默认由教师保持课堂节奏并推进讲解",
                    "prompt": "请自然推进下一步课堂互动，保持简洁。",
                },
                ensure_ascii=False,
            )
        speech = "我会根据当前课堂状态，用简短方式回应并推进互动。"
        if "深度思考者" in user_text:
            speech = "如果条件变化，这个结论还成立吗？我想追问一下它背后的原因。"
        elif "课堂笔记员" in user_text:
            speech = "我先记三个关键词：概念、例子、易错点，方便大家课后复习。"
        elif "课堂气氛调节者" in user_text:
            speech = "这个点有点像生活里的路线规划，先别紧张，我们拆开看。"
        elif "研究型同学" in user_text:
            speech = "这个知识能不能迁移到真实项目里？比如换一个场景还怎么用？"
        return json.dumps(
            {
                "speech": speech,
                "actions": [],
                "intent": "fake_llm_turn",
            },
            ensure_ascii=False,
        )

    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str:
        return json.dumps({"visual_description": ""}, ensure_ascii=False)


class OpenAICompatibleLLMProvider:
    """Minimal OpenAI-compatible chat/completions client.

    It intentionally uses the Python standard library so the project can keep its
    current lightweight dependency set. Works with OpenAI-compatible services
    that expose POST /chat/completions.
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        default_temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.default_temperature = default_temperature
        self.max_tokens = max_tokens
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def complete_json(self, messages: list[LLMMessage], *, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [message.__dict__ for message in messages],
            "temperature": temperature if temperature is not None else self.default_temperature,
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                req, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM request failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {body}") from exc

    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        data_url = (
            f"data:{mime_type};base64,"
            f"{base64.b64encode(path.read_bytes()).decode('ascii')}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": temperature if temperature is not None else self.default_temperature,
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                req, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Vision request failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Vision request failed: {exc.reason}") from exc

        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected vision response shape: {body}") from exc


class GeminiVisionProvider:
    """Gemini native REST provider for image understanding."""

    name = "gemini"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def complete_json(self, messages: list[LLMMessage], *, temperature: float = 0.2) -> str:
        prompt = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }
        return self._generate_content(payload)

    def complete_image_json(
        self,
        prompt: str,
        image_path: str | Path,
        *,
        temperature: float = 0.2,
    ) -> str:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }
        return self._generate_content(payload)

    def _generate_content(self, payload: dict) -> str:
        req = request.Request(
            f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(
                req, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini request failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc

        try:
            return str(body["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Gemini response shape: {body}") from exc


def build_llm_provider(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
    timeout_seconds: float,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> LLMProvider:
    normalized = provider.strip().lower()
    if normalized in {"", "fake", "none"}:
        return FakeLLMProvider()
    if normalized in {"openai", "openai-compatible", "compatible"}:
        if not api_key:
            raise RuntimeError(
                "METACLASS_LLM_API_KEY is required when METACLASS_LLM_PROVIDER=openai-compatible"
            )
        return OpenAICompatibleLLMProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            default_temperature=temperature,
            max_tokens=max_tokens,
        )
    if normalized in {"gemini", "google", "google-gemini"}:
        if not api_key:
            raise RuntimeError("METACLASS_VISION_API_KEY is required when provider=gemini")
        return GeminiVisionProvider(
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta",
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    raise RuntimeError(f"Unsupported METACLASS_LLM_PROVIDER: {provider}")
