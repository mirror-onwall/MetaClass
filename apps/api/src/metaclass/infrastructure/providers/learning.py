from __future__ import annotations

import json
import logging
from pathlib import Path

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    KnowledgeCanonicalizationDraft,
    KnowledgeUnit,
    LearningContentDraft,
    PageUnderstandingDraft,
)
from metaclass.modules.materials.schemas import PageMetadata


logger = logging.getLogger(__name__)

LANGUAGE_RULE = """
Language policy: inspect the substantive source material, not isolated English terms.
If the material is Chinese or mixes Chinese and English, write every explanatory field,
title, summary, teaching script, question, option, label, and recommendation in natural
Simplified Chinese. Preserve necessary formulas, symbols, proper nouns, and technical terms.
Use English output only when the substantive source material is entirely or overwhelmingly
English and contains no meaningful Chinese teaching content. Never mix English UI-style
labels or instructions into an otherwise Chinese result.
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
    prompt_version = "contextual-page-understanding-v3"

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
        response = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content="""You are a teaching-content analyst for MetaClass.

Read one parsed PDF/PPT page with its neighboring page context and optional visual description.
Return only valid JSON. Do not use markdown.

The JSON object must contain:
{
  "page_role": "cover | agenda | concept | method | formula | example | data | summary | reference | appendix",
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
- If the current page has little text, infer its teaching role from neighboring pages and visual cues.
- Do not invent unsupported facts, data, formulas, results, or citations.
- expanded_explanation should help a teacher explain image-heavy pages using visual_description.
- Extract only selected key_excerpts, not the whole page.
- Put formulas into formulas instead of burying them in summary.
- Formula variables must be objects with symbol and meaning fields, not bare strings.
- depends_on_pages and leads_to_pages should only include page numbers provided in the input context.

Quiz design rules:
- Generate 0 to 2 quiz_items. Use [] if the page is a title/agenda/transition page or lacks enough content.
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
            ],
            temperature=0.2,
        )
        return PageUnderstandingDraft.model_validate_json(response, extra="ignore")

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
        return LearningContentDraft.model_validate_json(response, extra="ignore")

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
                        "prerequisite_node_ids. Build 3 to 8 coherent top-level teaching chapters "
                        "when the material supports them. Use child topic nodes below chapters, and "
                        "assign one to three closely related knowledge units to each topic node. "
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

    @staticmethod
    def _page_context(page: PageMetadata | None) -> dict | None:
        if not page:
            return None
        return {
            "page_no": page.page_no,
            "title": page.title,
            "raw_text_excerpt": page.raw_text[:1200],
        }
