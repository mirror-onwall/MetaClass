from __future__ import annotations

import json

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import PageUnderstandingDraft


class LLMLearningProvider:
    """Build page-level learning metadata from parsed material text."""

    name = "llm"
    prompt_version = "page-understanding-v1"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        self.model = llm.model

    def understand_page(self, title: str, raw_text: str, page_no: int) -> PageUnderstandingDraft:
        payload = {
            "page_no": page_no,
            "title": title,
            "raw_text": raw_text[:8000],
        }
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a teaching-content analyst for MetaClass. "
                        "Read one parsed PDF/PPT page and return only valid JSON. "
                        "The JSON object must contain: summary as a concise teaching summary; "
                        "knowledge_points as 3 to 5 short strings; teaching_focus as 1 to 3 "
                        "important or difficult points; possible_questions as 2 to 4 questions "
                        "a teacher can ask students. Do not invent facts outside the page text."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(payload, ensure_ascii=False),
                ),
            ],
            temperature=0.2,
        )
        return PageUnderstandingDraft.model_validate_json(response)
