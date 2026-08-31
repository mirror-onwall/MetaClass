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
        if "PAPER_CLASSROOM_NARRATION_V1" in system_text:
            request_payload = json.loads(user_text)
            slides = []
            input_slides = request_payload["slides"]
            for index, slide in enumerate(input_slides):
                previous_title = slide.get("previous_slide_title")
                next_title = slide.get("next_slide_title")
                opening = (
                    f"承接前面对“{previous_title}”的讨论，现在来看“{slide['title']}”。"
                    if previous_title
                    else f"我们先从“{slide['title']}”进入论文真正要回答的问题。"
                )
                claims = [item["statement"] for item in slide.get("claims", [])]
                results = [item["statement"] for item in slide.get("quantitative_results", [])]
                contexts = [item["exact_text"] for item in slide.get("evidence_contexts", [])]
                main = (
                    f"这部分的讲解目标是{slide['purpose']}。"
                    f"页面上的信息需要结合论文论证来理解，而不是只记住结论标签。"
                )
                evidence = (
                    "论文的相关主张可以概括为："
                    + ("；".join(claims) if claims else "这一页主要承担背景和衔接作用")
                    + "。"
                    + ("相应结果是：" + "；".join(results) + "。" if results else "")
                    + (
                        "结合原文上下文，作者是在明确条件下讨论这一点，因此讲解时需要保留结论边界。"
                        if contexts
                        else ""
                    )
                )
                emphasis = (
                    "这里最重要的是区分作者直接报告的事实与我们对事实意义的解释，"
                    "这样才能判断证据是否真正支持研究问题。"
                )
                transition = (
                    f"理解这一层关系之后，下一步转向“{next_title}”。"
                    if next_title
                    else "最后把这些证据重新放回研究问题，评估论文贡献及其适用边界。"
                )
                script = f"{opening}\n\n{main}\n\n{evidence}\n\n{emphasis}\n\n{transition}"
                slides.append(
                    {
                        "slide_id": slide["slide_id"],
                        "opening": opening,
                        "main_explanation": main,
                        "evidence_interpretation": evidence,
                        "teaching_emphasis": emphasis,
                        "transition": transition,
                        "speaker_script": script,
                        "used_claim_ids": [item["id"] for item in slide.get("claims", [])],
                        "used_result_ids": [
                            item["id"] for item in slide.get("quantitative_results", [])
                        ],
                        "used_source_refs": slide.get("source_refs", []),
                    }
                )
            return json.dumps({"slides": slides}, ensure_ascii=False)
        if "INTERACTION_NODE_SELECTOR_V1" in system_text:
            request_payload = json.loads(user_text)
            maximum = request_payload["budget"]["maximum"]
            ranked = sorted(
                request_payload["candidates"],
                key=lambda item: (-item["deterministic_score"], item["order"]),
            )
            selected = ranked[:maximum]
            return json.dumps(
                {
                    "selected_slide_ids": [item["slide_id"] for item in selected],
                    "reasons": [
                        {
                            "slide_id": item["slide_id"],
                            "interaction_focus": "concept",
                            "reason": "候选页具有较高的确定性教学价值。",
                        }
                        for item in selected
                    ],
                },
                ensure_ascii=False,
            )
        if "INTERACTION_NODE_QUESTION_GENERATOR_V1" in system_text:
            request_payload = json.loads(user_text)
            questions = []
            agent_types = [
                "deep_thinker",
                "concept_confused",
                "practical_applier",
                "researcher",
            ]
            for index, node in enumerate(request_payload["nodes"]):
                point = (node.get("key_points") or [node["title"]])[0]
                questions.append(
                    {
                        "slide_id": node["slide_id"],
                        "agent_type": agent_types[index % len(agent_types)],
                        "compatible_agent_types": [
                            "researcher",
                            "concept_confused",
                        ],
                        "knowledge_point": point,
                        "canonical_question": f"{point}的关键条件和实际含义是什么？",
                        "student_question": f"老师，{point}到底要满足什么条件，实际该怎么理解？",
                        "canonical_answer": (
                            f"理解{point}需要结合当前页面给出的定义、条件和上下文。"
                        ),
                        "teacher_answer": f"关键是把{point}放回这一页的条件和上下文中理解。",
                        "placement_reason": "适合在本页讲解后检查学生是否形成准确理解。",
                    }
                )
            return json.dumps({"questions": questions}, ensure_ascii=False)
        if "MetaClass 的 ClassroomPlan planner" in system_text:
            request_payload = json.loads(user_text)
            section_count = len(request_payload["sections"])
            scenes = [
                {
                    "section_id": section["id"],
                    "include_probe": index == 1 or index % 3 == 0,
                    "probe_question": f"你能用自己的话解释{section['title']}的核心意思吗？"
                    if index == 1 or index % 3 == 0
                    else None,
                    "include_quiz": bool(section.get("has_quiz"))
                    and (section_count == 1 or index % 2 == 0),
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
        if "PPT_SCENE_ONLY" in system_text:
            request_payload = json.loads(user_text)
            slide = request_payload["slide"]
            index = request_payload["deck"]["slide_index"] - 1
            points = (slide.get("key_points") or [slide["title"]])[:4]
            palettes = [
                ("F7F3EA", "6B3F2A", "D97757"),
                ("EEF5F2", "173F3A", "45A08A"),
                ("F2F1F8", "302B63", "7165A8"),
            ]
            background, ink, accent = palettes[index % len(palettes)]
            elements = [
                {
                    "type": "text",
                    "x": 0.07,
                    "y": 0.07,
                    "w": 0.82,
                    "h": 0.12,
                    "z": 4,
                    "text": slide["title"],
                    "style": {"font_size": 34, "bold": True, "color": ink},
                }
            ]
            for point_index, point in enumerate(points):
                row, column = divmod(point_index, 2)
                x, y = 0.08 + column * 0.44, 0.27 + row * 0.27
                elements.extend(
                    [
                        {
                            "type": "shape",
                            "x": x,
                            "y": y,
                            "w": 0.39,
                            "h": 0.21,
                            "z": 0,
                            "shape": "rounded_rectangle",
                            "style": {"fill": "FFFFFF", "line_color": accent},
                        },
                        {
                            "type": "text",
                            "x": x + 0.035,
                            "y": y + 0.045,
                            "w": 0.32,
                            "h": 0.12,
                            "z": 2,
                            "text": point,
                            "style": {"font_size": 18, "color": ink},
                        },
                    ]
                )
            return json.dumps(
                {"background": background, "elements": elements},
                ensure_ascii=False,
            )
        if "MetaClass 的 PresentationPlan planner" in system_text:
            request_payload = json.loads(user_text)
            slides = []
            for index, section in enumerate(request_payload["sections"]):
                points = (section.get("knowledge_points") or [section["title"]])[:4]
                elements = [
                    {
                        "type": "shape",
                        "x": 0.04,
                        "y": 0.08,
                        "w": 0.012,
                        "h": 0.82,
                        "z": 0,
                        "shape": "rectangle",
                        "style": {"fill": ["D1495B", "66A182", "F7B801"][index % 3]},
                    },
                    {
                        "type": "text",
                        "x": 0.08,
                        "y": 0.08,
                        "w": 0.78,
                        "h": 0.14,
                        "z": 3,
                        "text": section["title"],
                        "style": {"font_size": 34, "bold": True, "color": "243B53"},
                    },
                ]
                for point_index, point in enumerate(points):
                    columns = 2 if len(points) == 4 else max(len(points), 1)
                    row, column = divmod(point_index, columns)
                    card_w = 0.38 if columns == 2 else 0.78 / columns
                    card_x = 0.09 + column * (card_w + 0.04)
                    card_y = 0.29 + row * 0.27
                    elements.extend(
                        [
                            {
                                "type": "shape",
                                "x": card_x,
                                "y": card_y,
                                "w": card_w,
                                "h": 0.2,
                                "z": 1,
                                "shape": "rounded_rectangle",
                                "style": {"fill": "FFFFFF", "line_color": "CBD5E1"},
                            },
                            {
                                "type": "text",
                                "x": card_x + 0.025,
                                "y": card_y + 0.035,
                                "w": card_w - 0.05,
                                "h": 0.13,
                                "z": 2,
                                "text": point,
                                "style": {"font_size": 17, "color": "334E68"},
                            },
                        ]
                    )
                slides.append(
                    {
                        "source_section_ids": [section["id"]],
                        "title": section["title"],
                        "key_points": points,
                        "speaker_script": (
                            f"这一页我们讲{section['title']}。"
                            f"{section.get('summary', '')} "
                            "讲解时可以先给出直观解释，再补充一个例子帮助理解。"
                        ),
                        "suggested_visual": (
                            f"使用原材料页面图片作为主视觉，并突出说明{section['title']}。"
                        ),
                        "layout": "freeform",
                        "visual_payload": points,
                        "background": ["F7F9F7", "F8F5F0", "F4F7FB"][index % 3],
                        "elements": elements,
                    }
                )
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
        if "正在回答真实用户刚刚输入的问题" in system_text:
            request_payload = json.loads(user_text)
            question = request_payload.get("user_question", "")
            explanation = request_payload.get("current_explanation", "")
            return json.dumps(
                {
                    "answer": (
                        f"你问的是“{question}”。结合当前页，我会先抓住材料里的主线："
                        f"{explanation} 如果你愿意，我们可以再把这个问题拆成一个更具体的例子。"
                    )
                },
                ensure_ascii=False,
            )
        speech = "我这里有个小困惑，想听老师用当前页的例子再落一下。"
        if "课堂气氛调节者" in user_text:
            speech = "老师我刚刚有点走神，但如果把这个点想成选外卖排序，好像又能跟上了。"
        elif "深度思考者" in user_text:
            speech = "如果条件变化，这个结论还成立吗？我想追问一下它背后的原因。"
        elif "课堂笔记员" in user_text:
            speech = "我先记一句：这页重点是分清条件和结论，不是只背名词。"
        elif "研究型同学" in user_text:
            speech = "这个知识能不能迁移到真实项目里？比如换一个场景，最容易在哪一步用错？"
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
        self._requires_temperature_one = self._model_requires_temperature_one(model)

    def complete_json(self, messages: list[LLMMessage], *, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [message.__dict__ for message in messages],
            "temperature": self._compatible_temperature(temperature),
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        body = self._read_chat_json_with_temperature_fallback(payload, "LLM")

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
        data_url = f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
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
            "temperature": self._compatible_temperature(temperature),
            "response_format": {"type": "json_object"},
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        body = self._read_chat_json_with_temperature_fallback(payload, "Vision")

        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected vision response shape: {body}") from exc

    def _compatible_temperature(self, temperature: float | None) -> float:
        requested = temperature if temperature is not None else self.default_temperature
        if self._requires_temperature_one:
            return 1.0
        return requested

    @staticmethod
    def _model_requires_temperature_one(model: str) -> bool:
        """Return known model capabilities without forcing unrelated models.

        Hosted model names often include an owner prefix, such as
        ``moonshotai/kimi-k3``. Unknown models are discovered from an explicit
        upstream validation error and cached on this provider instance instead.
        """
        model_name = model.strip().lower().rsplit("/", 1)[-1]
        return model_name.startswith(("gpt-5", "o1", "o3", "o4", "kimi-k2", "kimi-k3"))

    def _read_chat_json_with_temperature_fallback(self, payload: dict, label: str) -> dict:
        try:
            return self._read_json_with_retry(self._chat_request(payload), label)
        except RuntimeError as exc:
            detail = str(exc).lower()
            if (
                payload.get("temperature") != 1
                and "invalid temperature" in detail
                and "only 1 is allowed" in detail
            ):
                # Remember the capability so later requests for an unknown or
                # newly released model do not repeat the same failed probe.
                self._requires_temperature_one = True
                retry_payload = {**payload, "temperature": 1.0}
                return self._read_json_with_retry(
                    self._chat_request(retry_payload), f"{label} temperature-compatible retry"
                )
            raise

    def _chat_request(self, payload: dict) -> request.Request:
        return request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _read_json_with_retry(self, req: request.Request, label: str) -> dict:
        """Retry one transient connection/read timeout before failing a job."""
        for attempt in range(2):
            try:
                with request.urlopen(
                    req,
                    timeout=self.timeout_seconds,
                    context=self.ssl_context,
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"{label} request failed: HTTP {exc.code} {detail}") from exc
            except (TimeoutError, error.URLError, ConnectionError) as exc:
                if attempt == 0:
                    continue
                reason = exc.reason if isinstance(exc, error.URLError) else str(exc)
                raise RuntimeError(
                    f"{label} transient request failed after 2 attempts: {reason}"
                ) from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{label} returned invalid JSON") from exc
        raise RuntimeError(f"{label} request failed")


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
