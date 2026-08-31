from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    KnowledgeCanonicalizationDraft,
    KnowledgeUnit,
    LearningContentDraft,
    PageRef,
    PageUnderstandingDraft,
    SourceDeckLearningContentDraft,
    SourceDeckOutlineDraft,
    SourceDeckPageFlowBatch,
    SourceDeckPageFlowDraft,
    SourceDeckSectionDraft,
    SourceDeckTeachingStructureDraft,
)
from metaclass.modules.materials.schemas import PageMetadata


logger = logging.getLogger(__name__)
SOURCE_DECK_PAGE_ROLES = {
    "cover",
    "agenda",
    "section",
    "transition",
    "concept",
    "method",
    "formula",
    "example",
    "data",
    "summary",
    "exercise",
    "reference",
    "appendix",
}

LANGUAGE_RULE = """
Language policy for display-facing fields: source evidence may be Chinese, English, or mixed,
but write every generated title, summary, explanation, teaching goal, question, option, label,
and recommendation in natural Simplified Chinese so the product UI is consistently Chinese.
Preserve necessary English technical terms, proper nouns, acronyms, formulas, and symbols after
or alongside their Chinese name. Source excerpts must remain faithful to the source language.
Do not turn an English source sentence into an English display title; summarize its meaning in
Chinese instead.
"""


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Response does not contain a JSON object")
    return json.loads(text[start : end + 1])


class LLMLearningProvider:
    """Build page-level learning metadata from parsed material text."""

    name = "llm"
    prompt_version = "contextual-page-understanding-v5"

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
            payload = _parse_json_object(response)
        except Exception as exc:
            logger.warning("Vision description failed for %s: %s", page.id, exc)
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
        messages = [
            LLMMessage(
                role="system",
                content="""You are a teaching-content analyst for MetaClass.

Read one parsed PDF/PPT page with its neighboring page context and optional visual description.
Return only valid JSON. Do not use markdown.

The JSON object must contain:
{
  "page_role": "cover | agenda | section | transition | concept | method | formula | example | data | summary | exercise | reference | appendix",
  "summary": "a concise teaching summary grounded in the current page",
  "expanded_explanation": "a teacher-facing explanation script for this page",
  "visual_description": "useful visual/layout/chart/formula observations, or empty string",
  "teachable_points": [{"point": "one teachable point", "importance": "core | supporting | optional", "difficulty": "easy | medium | hard"}],
  "key_excerpts": [{"text": "short important source quote or formula text", "type": "definition | claim | formula | example | data", "reason": "why it matters"}],
  "concepts": [{"name": "concept name", "definition": "definition from this page"}],
  "formulas": [{"latex": "formula if any", "meaning": "what it means", "variables": [{"symbol": "u", "meaning": "meaning of u"}]}],
  "visual_analysis": {"has_useful_visual": false, "description": "", "candidate_visual_use": "source_image | redraw_flowchart | table | ignore"},
  "knowledge_points": ["3 to 5 short, teachable concepts"],
  "teaching_focus": ["1 to 3 important, difficult, or easily confused points"],
  "misconceptions": [{"mistake": "likely misunderstanding", "correction": "how to correct it"}],
  "possible_questions": ["2 to 4 open questions a teacher can ask"],
  "depends_on_pages": [page numbers this page depends on],
  "leads_to_pages": [page numbers this page prepares for],
  "same_topic_pages": [page numbers with the same topic],
  "transition_to_next": "a short teaching transition to the next page, or empty string",
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

Page-understanding rules:
- Use the exact snake_case field names shown above. In particular, output `page_role`, not
  `page-role`, and always include a non-empty `summary` string.
- If the current page has little text, infer whether it is a cover, agenda, section divider, transition, exercise, reference, or appendix page from neighboring pages and visual cues.
- Do not invent unsupported facts, data, formulas, results, or citations.
- expanded_explanation should help a teacher explain image-heavy pages using visual_description.
- Extract only selected key_excerpts, not the whole page.
- Put formulas into formulas instead of burying them in summary.
- Formula variables must be objects with symbol and meaning fields, not bare strings.
- depends_on_pages and leads_to_pages should only include page numbers provided in the input context.
- Every teachable_points item must contain a non-empty, specific `point`. Never output
  `{\"point\": \"\"}` or a whitespace-only point. If this page has no independently teachable
  point, return `teachable_points: []` instead of an empty placeholder object.

Quiz design rules:
- Generate 0 to 2 quiz_items. Use [] if the page is a cover, agenda, section, transition, reference, or appendix page, or lacks enough content.
- Do not ask "what is the main content of this page".
- Prefer questions that diagnose understanding: concept distinction, cause/effect, condition, implication, common misconception, or simple application.
- Distractors should be plausible misunderstandings, not obviously irrelevant filler.
- Every question and explanation must be answerable from the page text only.
- Keep questions concise and suitable for a classroom checkpoint.
"""
                + LANGUAGE_RULE,
            ),
            LLMMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False),
            ),
        ]
        for attempt in range(2):
            try:
                response = self.llm.complete_json(messages, temperature=0.2)
                response_payload = _parse_json_object(response)
                normalized = {
                    str(key).strip().replace("-", "_"): value
                    for key, value in response_payload.items()
                }
                return PageUnderstandingDraft.model_validate(normalized, extra="ignore")
            except (RuntimeError, TimeoutError, ValueError, ValidationError) as exc:
                if not attempt:
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=(
                                "Your previous response failed PageUnderstandingDraft validation: "
                                f"{exc}. Return the complete corrected JSON object using the exact "
                                "snake_case fields from the schema. `summary` is required and must "
                                "be a non-empty string grounded in the current page."
                            ),
                        )
                    )
                    continue
                logger.warning(
                    "Page %s understanding used deterministic fallback after invalid LLM output: %s",
                    page_no,
                    exc,
                )
        return self._fallback_page_understanding(
            title=title,
            raw_text=raw_text,
            visual_description=visual_description,
        )

    @staticmethod
    def _fallback_page_understanding(
        *, title: str, raw_text: str, visual_description: str
    ) -> PageUnderstandingDraft:
        text = " ".join(raw_text.split())
        summary = text[:600] or title.strip() or "该页用于承接课程内容。"
        point = title.strip() or text[:120]
        return PageUnderstandingDraft(
            summary=summary,
            expanded_explanation=summary,
            visual_description=visual_description,
            knowledge_points=[point] if point else [],
            teaching_focus=[point] if point else [],
            page_role="concept",
        )

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
                "page_image_path": page.image_path,
                "visual_candidates": [
                    image.model_dump(mode="json") for image in page.embedded_images[:8]
                ],
                "summary": understanding.summary,
                "expanded_explanation": understanding.expanded_explanation,
                "visual_description": understanding.visual_description,
                "visual_analysis": understanding.visual_analysis,
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
                        "knowledge_points, visual_opportunities, visual_summary, "
                        "transition_to_next, quiz_items. "
                        "When a source page contains an embedded image that should be inserted "
                        "into PPT, add a visual_opportunities item with description, image_path "
                        "copied from the relevant visual_candidates item, image_description, "
                        "usage_hint, priority, and source_refs if available. Use [] when no "
                        "embedded source visual is useful. Do not use page_image_path for "
                        "visual_opportunities because it is a full-page render. "
                        "Each quiz item must contain question, options, correct_index, "
                        "explanation, knowledge_point. The teaching_script should connect pages "
                        "logically and explain image-heavy pages using visual_description. "
                        "Write teaching_script as natural spoken classroom language, not a textbook "
                        "chapter abstract. Do not repeatedly begin with 本章, 本单元, 本节, or 本文; "
                        "start directly from the idea, question, evidence, or prior-page connection."
                        + LANGUAGE_RULE
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
        return LearningContentDraft.model_validate_json(response, extra="ignore")

    @staticmethod
    def _fallback_collection_draft_payload(
        *,
        collection_id: str,
        material_ids: list[str],
        pages: list[PageMetadata],
        understandings: list,
        knowledge_units: list[KnowledgeUnit],
        knowledge_tree: CourseKnowledgeTree,
    ) -> dict:
        unit_by_id = {unit.id: unit for unit in knowledge_units}
        understanding_by_source = {
            (item.material_id, item.page_no): item for item in understandings
        }
        page_by_source = {(page.material_id, page.page_no): page for page in pages}
        teaching_nodes = [
            node for node in knowledge_tree.nodes if node.knowledge_unit_ids
        ] or knowledge_tree.nodes[:1]
        sections = []
        for index, node in enumerate(teaching_nodes, start=1):
            units = [
                unit_by_id[unit_id] for unit_id in node.knowledge_unit_ids if unit_id in unit_by_id
            ]
            source_refs = [ref for unit in units for ref in unit.source_refs]
            page_refs = [ref for unit in units for ref in unit.page_refs] or [
                {"material_id": ref.material_id, "page_no": ref.page_no} for ref in source_refs
            ]
            quiz_items = []
            seen_questions = set()
            for page_ref in page_refs:
                material_id = (
                    page_ref.material_id
                    if hasattr(page_ref, "material_id")
                    else page_ref.get("material_id")
                )
                page_no = (
                    page_ref.page_no if hasattr(page_ref, "page_no") else page_ref.get("page_no")
                )
                understanding = understanding_by_source.get((material_id, page_no))
                for quiz in getattr(understanding, "quiz_items", [])[:2]:
                    question = quiz.question.strip()
                    if question and question not in seen_questions:
                        seen_questions.add(question)
                        quiz_items.append(quiz.model_dump(mode="json"))
                if len(quiz_items) >= 2:
                    break
            keywords = []
            for unit in units:
                keywords.extend(unit.keywords)
            if not quiz_items and keywords:
                point = keywords[0]
                quiz_items = [
                    {
                        "question": f"关于“{point}”，下列哪一项最能体现理解到位？",
                        "options": [
                            f"能够解释 {point} 的含义、适用条件或具体例子",
                            f"只记住 {point} 在材料中出现过",
                            f"把 {point} 与所有相关概念无条件混用",
                        ],
                        "correct_index": 0,
                        "explanation": "理解一个知识点，需要能说明它解决的问题、适用条件和使用边界。",
                        "knowledge_point": point,
                    }
                ]
            section_pages = [
                page_by_source.get(
                    (
                        ref.material_id if hasattr(ref, "material_id") else ref.get("material_id"),
                        ref.page_no if hasattr(ref, "page_no") else ref.get("page_no"),
                    )
                )
                for ref in page_refs
            ]
            page_nos = sorted({page.page_no for page in section_pages if page})
            summary = node.summary or " ".join(unit.summary for unit in units)[:1200]
            teaching_script = (
                "先围绕材料中的核心问题建立背景，再解释关键概念、方法条件和结果解读。"
                f"本节重点是：{summary}"
            )[:2500]
            sections.append(
                {
                    "title": node.title,
                    "role": node.role,
                    "content_goal": f"组织并讲清 {node.title} 的核心知识。",
                    "page_nos": page_nos,
                    "page_refs": [
                        ref.model_dump(mode="json") if hasattr(ref, "model_dump") else ref
                        for ref in page_refs
                    ],
                    "summary": summary or node.title,
                    "key_points": list(dict.fromkeys(keywords))[:8],
                    "tree_node_ids": [node.id],
                    "teaching_narrative": teaching_script,
                    "teaching_script": teaching_script,
                    "knowledge_points": list(dict.fromkeys(keywords))[:10],
                    "source_excerpts": [
                        excerpt.model_dump(mode="json")
                        for unit in units
                        for excerpt in unit.source_excerpts[:2]
                    ][:8],
                    "formulas": [
                        formula.model_dump(mode="json")
                        for unit in units
                        for formula in unit.formulas
                    ][:6],
                    "examples": [
                        example.model_dump(mode="json")
                        for unit in units
                        for example in unit.examples
                    ][:6],
                    "visual_opportunities": [],
                    "misconceptions": [
                        item.model_dump(mode="json")
                        for unit in units
                        for item in unit.misconceptions
                    ][:6],
                    "interaction_opportunities": [],
                    "visual_summary": "",
                    "transition_to_next": "",
                    "quiz_items": quiz_items[:2],
                }
            )
        return {
            "title": knowledge_tree.title,
            "subtitle": "Repaired LearningContent draft from course knowledge tree",
            "objectives": [f"理解{section['title']}的核心知识" for section in sections[:3]],
            "outline": [section["title"] for section in sections],
            "audience": {"level": "undergraduate"},
            "teaching_intent": {
                "goal": "全局 organizer 返回空 sections 后，由知识树自动修复生成可用 LearningContent。"
            },
            "material_overview": {
                "collection_id": collection_id,
                "material_ids": material_ids,
                "page_count": len(pages),
            },
            "global_concepts": [],
            "generation_guidance": {},
            "quality": {
                "warnings": ["LLM organizer returned empty sections; repaired from knowledge tree."]
            },
            "sections": sections,
        }

    def organize_collection_learning_content(
        self,
        *,
        collection_id: str,
        material_ids: list[str],
        pages: list[PageMetadata],
        understandings: list,
        knowledge_units: list[KnowledgeUnit],
        knowledge_tree: CourseKnowledgeTree,
    ) -> LearningContentDraft:
        packets = []
        understanding_by_page = {(item.material_id, item.page_no): item for item in understandings}
        for page in pages:
            understanding = understanding_by_page.get((page.material_id, page.page_no))
            packets.append(
                {
                    "material_id": page.material_id,
                    "page_no": page.page_no,
                    "title": page.title,
                    "raw_text_excerpt": page.raw_text[:1000],
                    "summary": getattr(understanding, "summary", ""),
                    "page_role": getattr(understanding, "page_role", ""),
                    "knowledge_points": getattr(understanding, "knowledge_points", []),
                    "key_excerpts": [
                        excerpt.model_dump(mode="json")
                        for excerpt in getattr(understanding, "key_excerpts", [])
                    ],
                    "concepts": [
                        concept.model_dump(mode="json")
                        for concept in getattr(understanding, "concepts", [])
                    ],
                    "formulas": [
                        formula.model_dump(mode="json")
                        for formula in getattr(understanding, "formulas", [])
                    ],
                    "visual_description": getattr(understanding, "visual_description", ""),
                    "visual_analysis": getattr(understanding, "visual_analysis", {}),
                    "page_image_path": page.image_path,
                    "visual_candidates": [
                        image.model_dump(mode="json") for image in page.embedded_images[:8]
                    ],
                    "misconceptions": [
                        item.model_dump(mode="json")
                        for item in getattr(understanding, "misconceptions", [])
                    ],
                }
            )
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You are an instructional designer building LearningContent from "
                        "multiple teaching materials. Do not organize by source page or file. "
                        "The supplied course_knowledge_tree is authoritative. Root nodes are chapter "
                        "containers; build sections from knowledge-bearing topic nodes whose "
                        "knowledge_unit_ids are non-empty. Cover every such topic node exactly once. "
                        "A section should normally reference one topic node and must not cover more "
                        "than three knowledge units. Do not bypass the tree or create sections from "
                        "source pages. Remove duplicates across documents. "
                        "Return only valid JSON with keys: title, subtitle, objectives, outline, "
                        "audience, teaching_intent, material_overview, global_concepts, "
                        "generation_guidance, quality, sections. Each section must include: "
                        "title, role, content_goal, page_nos, page_refs, summary, key_points, "
                        "tree_node_ids, "
                        "teaching_narrative, teaching_script, knowledge_points, source_excerpts, "
                        "formulas, examples, visual_opportunities, misconceptions, "
                        "interaction_opportunities, visual_summary, transition_to_next, quiz_items. "
                        "Use page_refs with material_id and page_no for traceability. "
                        "For visual_opportunities, only recommend embedded source images when "
                        "they help the PPT explain the section. Each item should include "
                        "description, image_path copied from a supplied visual_candidates item, "
                        "image_description, usage_hint, priority, and source_refs/page refs when "
                        "possible. Do not invent image paths and do not use page_image_path, "
                        "which is only the full-page render. "
                        "Do not create one section per page unless pedagogically necessary. "
                        "Write teaching_narrative and teaching_script as natural spoken classroom "
                        "language, not chapter summaries. Do not repeatedly use 本章, 本单元, 本节, "
                        "or 本文 as sentence openings; vary transitions and enter the substance directly."
                        + LANGUAGE_RULE
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "collection_id": collection_id,
                            "material_ids": material_ids,
                            "knowledge_units": [
                                unit.model_dump(mode="json") for unit in knowledge_units
                            ],
                            "course_knowledge_tree": knowledge_tree.model_dump(mode="json"),
                            "pages": packets,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.2,
        )
        payload = _parse_json_object(response)
        if not payload.get("sections"):
            logger.warning(
                "Collection learning-content organizer returned no sections; "
                "using knowledge-tree draft repair."
            )
            payload = self._fallback_collection_draft_payload(
                collection_id=collection_id,
                material_ids=material_ids,
                pages=pages,
                understandings=understandings,
                knowledge_units=knowledge_units,
                knowledge_tree=knowledge_tree,
            )
        return LearningContentDraft.model_validate(payload, extra="ignore")

    def organize_source_deck_learning_content(
        self,
        *,
        material_id: str,
        pages: list[PageMetadata],
        understandings: list,
        knowledge_units: list[KnowledgeUnit],
    ) -> SourceDeckLearningContentDraft:
        """Preserve the authored deck structure instead of regrouping by knowledge topic."""
        pages = sorted(pages, key=lambda item: item.page_no)
        understanding_by_page = {item.page_no: item for item in understandings}
        packets = []
        for page in pages:
            understanding = understanding_by_page.get(page.page_no)
            packets.append(
                {
                    "material_id": material_id,
                    "page_no": page.page_no,
                    "title": page.title,
                    "raw_text_excerpt": page.raw_text[:1800],
                    "page_role": getattr(understanding, "page_role", "concept"),
                    "summary": getattr(understanding, "summary", ""),
                    "knowledge_points": getattr(understanding, "knowledge_points", []),
                    "teaching_focus": getattr(understanding, "teaching_focus", []),
                    "relations": getattr(understanding, "relations", {}),
                    "visual_analysis": getattr(understanding, "visual_analysis", {}),
                }
            )
        prompt_path = (
            Path(__file__).parents[2]
            / "modules"
            / "content"
            / ("source_deck_learning_content_prompt.md")
        )
        outline_packets = [
            {
                "material_id": item["material_id"],
                "page_no": item["page_no"],
                "title": item["title"],
                "page_role": item["page_role"],
                "summary": item["summary"][:500],
                "knowledge_points": item["knowledge_points"][:5],
                "relations": item["relations"],
            }
            for item in packets
        ]
        outline_messages = [
            LLMMessage(
                role="system",
                content=prompt_path.read_text(encoding="utf-8"),
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "material_id": material_id,
                        "page_count": len(outline_packets),
                        "first_page_no": outline_packets[0]["page_no"],
                        "last_page_no": outline_packets[-1]["page_no"],
                        "expected_page_nos": [item["page_no"] for item in outline_packets],
                        "pages": outline_packets,
                        "canonical_knowledge_units": [
                            {
                                "id": unit.id,
                                "title": unit.title,
                                "summary": unit.summary[:400],
                                "page_refs": [
                                    ref.model_dump(mode="json") for ref in unit.page_refs
                                ],
                            }
                            for unit in knowledge_units
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        expected_pages = [page.page_no for page in pages]
        outline = None
        for attempt in range(2):
            outline_response = self.llm.complete_json(outline_messages, temperature=0.15)
            candidate = SourceDeckOutlineDraft.model_validate(
                _parse_json_object(outline_response), extra="ignore"
            )
            outline_pages = [
                ref.page_no for section in candidate.sections for ref in section.page_refs
            ]
            if outline_pages == expected_pages:
                outline = candidate
                break
            if not attempt:
                missing_pages = [
                    page_no for page_no in expected_pages if page_no not in outline_pages
                ]
                duplicate_pages = sorted(
                    {page_no for page_no in outline_pages if outline_pages.count(page_no) > 1}
                )
                outline_messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Your previous outline was invalid. Return the complete corrected JSON. "
                            f"Expected flattened page_nos exactly {expected_pages}; received "
                            f"{outline_pages}. Missing pages: {missing_pages}. Duplicate pages: "
                            f"{duplicate_pages}. Every page must appear once, in order. Rebuild all "
                            "section page_refs, then internally flatten and compare them item by item "
                            "with expected_page_nos before responding."
                        ),
                    )
                )
        if outline is None:
            outline = self._repair_source_deck_outline(
                candidate,
                material_id=material_id,
                expected_pages=expected_pages,
            )

        chapter_by_page = {
            ref.page_no: section.title for section in outline.sections for ref in section.page_refs
        }
        page_flow: list[SourceDeckPageFlowDraft] = []
        flow_prompt_path = (
            Path(__file__).parents[2] / "modules" / "content" / ("source_deck_page_flow_prompt.md")
        )
        batch_size = 12
        for start in range(0, len(packets), batch_size):
            batch = packets[start : start + batch_size]
            flow_messages = [
                LLMMessage(
                    role="system",
                    content=flow_prompt_path.read_text(encoding="utf-8"),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "course_title": outline.title,
                            "structure_summary": outline.structure_summary,
                            "detected_agenda": outline.detected_agenda,
                            "previous_page": packets[start - 1] if start else None,
                            "pages": [
                                {
                                    **item,
                                    "chapter_title": chapter_by_page[item["page_no"]],
                                }
                                for item in batch
                            ],
                            "next_page": (
                                packets[start + batch_size]
                                if start + batch_size < len(packets)
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
            batch_flow = None
            for attempt in range(2):
                try:
                    raw = self.llm.complete_json(flow_messages, temperature=0.15)
                    candidate = SourceDeckPageFlowBatch.model_validate(
                        _parse_json_object(raw), extra="ignore"
                    )
                    expected_batch = [item["page_no"] for item in batch]
                    if [item.page_no for item in candidate.page_flow] != expected_batch:
                        raise ValueError("Source-deck page-flow batch page mismatch")
                    batch_flow = candidate.page_flow
                    break
                except (RuntimeError, TimeoutError, ValueError, ValidationError) as exc:
                    if attempt:
                        logger.warning(
                            "Source-deck page-flow batch %s-%s fell back: %s",
                            batch[0]["page_no"],
                            batch[-1]["page_no"],
                            exc,
                        )
            if batch_flow is None:
                batch_flow = [
                    SourceDeckPageFlowDraft(
                        page_no=item["page_no"],
                        page_role=item["page_role"]
                        if item["page_role"] in SOURCE_DECK_PAGE_ROLES
                        else "concept",
                        chapter_title=chapter_by_page[item["page_no"]],
                        content_summary=item["summary"] or item["title"],
                        teaching_purpose=(item["teaching_focus"] or [item["summary"]])[0],
                        logic_from_previous="",
                        leads_to_next=str(item["relations"].get("transition_to_next", "")),
                    )
                    for item in batch
                ]
            page_flow.extend(batch_flow)

        return SourceDeckLearningContentDraft(
            title=outline.title,
            subtitle=outline.subtitle,
            objectives=outline.objectives,
            structure_summary=outline.structure_summary,
            detected_agenda=outline.detected_agenda,
            page_flow=page_flow,
            sections=outline.sections,
        )

    @staticmethod
    def _repair_source_deck_outline(
        outline: SourceDeckOutlineDraft,
        *,
        material_id: str,
        expected_pages: list[int],
    ) -> SourceDeckOutlineDraft:
        """Preserve proposed section semantics while repairing page coverage deterministically."""
        if not expected_pages or not outline.sections:
            raise ValueError("Cannot repair an empty source-deck outline")
        valid_pages = set(expected_pages)
        starts_and_sections = []
        for section in outline.sections:
            referenced = sorted(
                {ref.page_no for ref in section.page_refs if ref.page_no in valid_pages}
            )
            if referenced:
                starts_and_sections.append((referenced[0], section))
        if not starts_and_sections:
            raise ValueError("Source-deck outline has no valid section boundary")
        starts_and_sections.sort(key=lambda item: item[0])
        deduped = []
        for start, section in starts_and_sections:
            if deduped and deduped[-1][0] == start:
                continue
            deduped.append((start, section))
        if deduped[0][0] != expected_pages[0]:
            deduped[0] = (expected_pages[0], deduped[0][1])
        repaired_sections = []
        for index, (start, section) in enumerate(deduped):
            end = deduped[index + 1][0] - 1 if index + 1 < len(deduped) else expected_pages[-1]
            repaired_sections.append(
                section.model_copy(
                    update={
                        "page_refs": [
                            PageRef(material_id=material_id, page_no=page_no)
                            for page_no in expected_pages
                            if start <= page_no <= end
                        ]
                    }
                )
            )
        logger.warning(
            "Source-deck outline page coverage was repaired deterministically using section starts"
        )
        return outline.model_copy(update={"sections": repaired_sections})

    def build_course_knowledge_tree(
        self,
        *,
        tree_id: str,
        title: str,
        units: list[KnowledgeUnit],
    ) -> CourseKnowledgeTree:
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You build a hierarchical course knowledge tree from canonical knowledge "
                        "units. Return only JSON with keys id, title, nodes, root_node_ids, "
                        "teaching_sequence, orphan_unit_ids, warnings. Each node must contain id, "
                        "title, role, summary, parent_id, knowledge_unit_ids, order, and "
                        "prerequisite_node_ids. Build coherent top-level teaching chapters when "
                        "the material supports them. Use child topic nodes below chapters, and try "
                        "to keep each topic focused on one clear, teachable idea. Group knowledge "
                        "units by semantic and pedagogical coherence rather than a fixed count. "
                        "Every knowledge unit id must appear in exactly one node. Chapter container "
                        "nodes should normally have empty knowledge_unit_ids. Parent and prerequisite "
                        "ids must reference existing "
                        "nodes. teaching_sequence must contain node ids in pedagogical order. "
                        "Organize by teaching logic, not source file or page order." + LANGUAGE_RULE
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "tree_id": tree_id,
                            "title": title,
                            "knowledge_units": [unit.model_dump(mode="json") for unit in units],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.1,
        )
        return CourseKnowledgeTree.model_validate_json(response, extra="ignore")

    def canonicalize_knowledge_units(
        self, units: list[KnowledgeUnit]
    ) -> KnowledgeCanonicalizationDraft:
        payload = [
            {
                "id": unit.id,
                "title": unit.title,
                "unit_type": unit.unit_type,
                "summary": unit.summary,
                "keywords": unit.keywords,
                "concept_names": [concept.name for concept in unit.concepts],
                "page_refs": [ref.model_dump(mode="json") for ref in unit.page_refs],
            }
            for unit in units
        ]
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You normalize knowledge units extracted from multiple teaching documents. "
                        "Return only JSON with keys groups and relations. Every input unit id must "
                        "appear in exactly one group.unit_ids. Merge units only when they express the "
                        "same teachable concept; do not merge prerequisites, examples, methods, or "
                        "different levels of detail merely because they share keywords. Each group "
                        "must contain id, title, unit_ids, unit_type, summary, aliases, confidence. "
                        "Relations must contain source_group_id, target_group_id, relation_type, "
                        "reason, confidence. Allowed relation types are prerequisite_of, extends, "
                        "example_of, contrasts_with, and related_to. Group ids must be unique."
                        + LANGUAGE_RULE
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps({"knowledge_units": payload}, ensure_ascii=False),
                ),
            ],
            temperature=0.1,
        )
        return KnowledgeCanonicalizationDraft.model_validate_json(response, extra="ignore")

    def canonicalize_source_deck_knowledge_units(
        self, units: list[KnowledgeUnit]
    ) -> KnowledgeCanonicalizationDraft:
        payload = [
            {
                "id": unit.id,
                "title": unit.title,
                "unit_type": unit.unit_type,
                "summary": unit.summary,
                "keywords": unit.keywords,
                "page_refs": [ref.model_dump(mode="json") for ref in unit.page_refs],
            }
            for unit in units
        ]
        prompt_path = (
            Path(__file__).parents[2]
            / "modules"
            / "content"
            / ("source_deck_knowledge_unit_prompt.md")
        )
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=prompt_path.read_text(encoding="utf-8") + LANGUAGE_RULE,
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps({"knowledge_units": payload}, ensure_ascii=False),
                ),
            ],
            temperature=0.1,
        )
        return KnowledgeCanonicalizationDraft.model_validate_json(response, extra="ignore")

    def plan_source_deck_teaching_segments(
        self,
        *,
        section: SourceDeckSectionDraft,
        page_flow: list[SourceDeckPageFlowDraft],
        knowledge_units: list[KnowledgeUnit],
        previous_section_title: str = "",
        next_section_title: str = "",
    ) -> SourceDeckTeachingStructureDraft:
        prompt_path = (
            Path(__file__).parents[2]
            / "modules"
            / "content"
            / ("source_deck_teaching_structure_prompt.md")
        )
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=prompt_path.read_text(encoding="utf-8"),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "section": section.model_dump(mode="json"),
                            "previous_section_title": previous_section_title,
                            "next_section_title": next_section_title,
                            "page_flow": [item.model_dump(mode="json") for item in page_flow],
                            "candidate_knowledge_units": [
                                unit.model_dump(mode="json") for unit in knowledge_units
                            ],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.15,
        )
        return SourceDeckTeachingStructureDraft.model_validate_json(response, extra="ignore")

    @staticmethod
    def _page_context(page: PageMetadata | None) -> dict | None:
        if not page:
            return None
        return {
            "page_no": page.page_no,
            "title": page.title,
            "raw_text_excerpt": page.raw_text[:1200],
        }
