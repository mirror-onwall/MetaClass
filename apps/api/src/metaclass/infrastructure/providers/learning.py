from __future__ import annotations

import json
from pathlib import Path

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import LearningContentDraft, PageUnderstandingDraft
from metaclass.modules.materials.schemas import PageMetadata


class LLMLearningProvider:
    """Build page-level learning metadata from parsed material text."""

    name = "llm"
    prompt_version = "contextual-page-understanding-v2"

    def __init__(
        self,
        llm: LLMProvider,
        *,
        vision_llm: LLMProvider | None = None,
        vision_enabled: bool = False,
    ) -> None:
        self.llm = llm
        self.vision_llm = vision_llm or llm
        self.model = llm.model
        self.vision_enabled = vision_enabled

    def understand_page(self, title: str, raw_text: str, page_no: int) -> PageUnderstandingDraft:
        return self.understand_page_with_context(
            page_no=page_no,
            title=title,
            raw_text=raw_text,
            previous_page=None,
            next_page=None,
            visual_description="",
        )

    def describe_page_visual(self, page: PageMetadata) -> str:
        if not self.vision_enabled or not page.image_path or not Path(page.image_path).is_file():
            return ""
        complete_image_json = getattr(self.vision_llm, "complete_image_json", None)
        if not complete_image_json:
            return ""
        prompt = (
            "You are analyzing one rendered PPT/PDF page for teaching preparation. "
            "Return only JSON with key visual_description. Describe diagrams, charts, "
            "images, formulas, layout cues, and relationships that are useful for a teacher. "
            "If the image is mostly text, summarize the visible structure instead of repeating "
            "every word."
        )
        try:
            response = complete_image_json(prompt, page.image_path, temperature=0.1)
            payload = json.loads(response)
        except Exception:
            return ""
        value = payload.get("visual_description", "")
        return str(value).strip() if value else ""

    def understand_page_with_context(
        self,
        *,
        page_no: int,
        title: str,
        raw_text: str,
        previous_page: PageMetadata | None,
        next_page: PageMetadata | None,
        visual_description: str,
    ) -> PageUnderstandingDraft:
        payload = {
            "page_no": page_no,
            "title": title,
            "raw_text": raw_text[:8000],
            "visual_description": visual_description[:4000],
            "previous_page": self._page_context(previous_page),
            "next_page": self._page_context(next_page),
        }
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a teaching-content analyst for MetaClass. "
                        "Read one parsed PDF/PPT page with its neighboring page context and "
                        "optional visual description. Return only valid JSON. The JSON object "
                        "must contain: summary; expanded_explanation as a teacher-facing script "
                        "for this page; visual_description; knowledge_points as 3 to 5 short "
                        "strings; teaching_focus as 1 to 3 important or difficult points; "
                        "possible_questions as 2 to 4 questions; depends_on_pages; "
                        "leads_to_pages; transition_to_next. If the current page has little "
                        "text, infer its teaching role from neighboring pages and visual cues, "
                        "but do not invent unsupported facts, data, or formulas."
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

    def organize_learning_content(
        self,
        *,
        material_id: str,
        pages: list[PageMetadata],
        understandings: list[PageUnderstandingDraft],
    ) -> LearningContentDraft:
        page_packets = [
            {
                "page_no": page.page_no,
                "title": page.title,
                "raw_text_excerpt": page.raw_text[:1200],
                "summary": understanding.summary,
                "expanded_explanation": understanding.expanded_explanation,
                "visual_description": understanding.visual_description,
                "knowledge_points": understanding.knowledge_points,
                "teaching_focus": understanding.teaching_focus,
                "possible_questions": understanding.possible_questions,
                "depends_on_pages": understanding.depends_on_pages,
                "leads_to_pages": understanding.leads_to_pages,
                "transition_to_next": understanding.transition_to_next,
            }
            for page, understanding in zip(pages, understandings, strict=False)
        ]
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a senior instructional designer. Build a tree-like course "
                        "outline and a unified teaching script from page-level analyses. "
                        "Do not mechanically create one section per page. Merge adjacent pages "
                        "when they serve one concept. Preserve page_nos for traceability. "
                        "Return only valid JSON with keys: title, objectives, outline, sections. "
                        "Each section must contain title, page_nos, summary, teaching_script, "
                        "knowledge_points, visual_summary, transition_to_next, quiz_items. "
                        "Each quiz item must contain question, options, correct_index, "
                        "explanation, knowledge_point. The teaching_script should connect pages "
                        "logically and explain image-heavy pages using visual_description."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {"material_id": material_id, "pages": page_packets},
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.2,
        )
        return LearningContentDraft.model_validate_json(response)

    @staticmethod
    def _page_context(page: PageMetadata | None) -> dict | None:
        if not page:
            return None
        return {
            "page_no": page.page_no,
            "title": page.title,
            "raw_text_excerpt": page.raw_text[:1200],
        }
