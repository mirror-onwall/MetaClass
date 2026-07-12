from __future__ import annotations

import json

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import PageUnderstandingDraft


class LLMLearningProvider:
    """Build page-level learning metadata from parsed material text."""

    name = "llm"
    prompt_version = "page-understanding-v2"

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
                    content="""You are a teaching-content analyst for MetaClass.

Read one parsed PDF/PPT page and return only valid JSON. Do not use markdown.

The JSON object must contain:
{
  "summary": "a concise teaching summary grounded in the page",
  "knowledge_points": ["3 to 5 short, teachable concepts"],
  "teaching_focus": ["1 to 3 important, difficult, or easily confused points"],
  "possible_questions": ["2 to 4 open questions a teacher can ask"],
  "quiz_items": [
    {
      "question": "one meaningful multiple-choice question",
      "options": ["2 to 4 options"],
      "correct_index": 0,
      "explanation": "why the correct option follows from the page",
      "knowledge_point": "the concept being checked"
    }
  ]
}

Quiz design rules:
- Generate 0 to 2 quiz_items. Use [] if the page is a title/agenda/transition page or lacks enough content.
- Do not ask "what is the main content of this page".
- Prefer questions that diagnose understanding: concept distinction, cause/effect, condition, implication, common misconception, or simple application.
- Distractors should be plausible misunderstandings, not obviously irrelevant filler.
- Every question and explanation must be answerable from the page text only.
- Keep questions concise and suitable for a classroom checkpoint.
""",
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(payload, ensure_ascii=False),
                ),
            ],
            temperature=0.2,
        )
        return PageUnderstandingDraft.model_validate_json(response)
