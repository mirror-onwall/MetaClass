from __future__ import annotations

import hashlib
import json
import re
from typing import ClassVar

from pydantic import TypeAdapter

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider

from .contracts import (
    InteractionBlueprint,
    InteractionPlanningContext,
    InteractionPolicy,
    ScriptedInteraction,
    ScriptedInteractionBank,
)


class TeachingNodeSelector:
    """Route-neutral deterministic selector that emits stable blueprints."""

    _excluded_roles: ClassVar[set[str]] = {
        "cover",
        "agenda",
        "transition",
        "reference",
        "thanks",
    }

    def select(
        self,
        context: InteractionPlanningContext,
        policy: InteractionPolicy,
    ) -> list[InteractionBlueprint]:
        if policy.intensity == "none" or policy.maximum_nodes == 0:
            return []
        ranked = []
        for slide in context.slides:
            if slide.role.casefold() in self._excluded_roles or slide.unverified_numbers:
                continue
            score = min(len(slide.knowledge_unit_ids), 3) * 1.0
            score += min(len(slide.claim_ids) + len(slide.result_ids), 3) * 0.8
            score += 1.2 if slide.asset_ids or slide.visual_labels else 0
            score += 0.8 if slide.evidence_strength == "direct" else 0
            score += min(len(slide.speaker_script) / 300, 1.0)
            ranked.append((score, slide))
        selected = []
        for score, slide in sorted(ranked, key=lambda item: (-item[0], item[1].order)):
            if score < 1.5 or any(abs(slide.order - item.order) <= 1 for item in selected):
                continue
            selected.append(slide)
            if len(selected) >= policy.maximum_nodes:
                break
        selected.sort(key=lambda item: item.order)
        return [
            InteractionBlueprint(
                id=f"interaction_blueprint_{slide.slide_id}",
                slide_id=slide.slide_id,
                direction="student_to_teacher",
                intent=self._intent(slide.role, bool(slide.asset_ids), bool(slide.claim_ids)),
                teaching_goal=slide.message or f"澄清“{slide.title}”的关键理解。",
                knowledge_unit_ids=slide.knowledge_unit_ids,
                claim_ids=slide.claim_ids,
                result_ids=slide.result_ids,
                asset_ids=slide.asset_ids,
                source_refs=slide.source_refs,
                evidence_strength=slide.evidence_strength,
                preferred_agent_types=[],
                importance=min(1.0, score / 6),
            )
            for score, slide in (
                next(item for item in ranked if item[1].slide_id == selected_slide.slide_id)
                for selected_slide in selected
            )
        ]

    @staticmethod
    def _intent(role: str, has_asset: bool, has_claim: bool):
        normalized = role.casefold()
        if "limit" in normalized or "局限" in normalized:
            return "boundary_condition"
        if "comparison" in normalized or "对比" in normalized:
            return "concept_contrast"
        if has_asset:
            return "evidence_reading"
        if "method" in normalized or "mechanism" in normalized or "方法" in normalized:
            return "mechanism_reasoning"
        if has_claim:
            return "concept_clarification"
        return "section_synthesis"


class QuestionGenerator:
    """Shared question/answer generator; contains no route-specific branches."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    def generate(
        self,
        context: InteractionPlanningContext,
        blueprints: list[InteractionBlueprint],
    ) -> ScriptedInteractionBank:
        if not blueprints:
            return ScriptedInteractionBank(presentation_plan_id=context.presentation_plan_id)
        if self.llm is None:
            return ScriptedInteractionBank(
                presentation_plan_id=context.presentation_plan_id,
                items=[self._fallback(context, item) for item in blueprints],
            )
        slides = {item.slide_id: item for item in context.slides}
        payload = {
            "lesson": {
                "content_id": context.content_id,
                "presentation_plan_id": context.presentation_plan_id,
                "mode": context.mode,
                "duration_minutes": context.duration_minutes,
            },
            "nodes": [
                {
                    "blueprint": item.model_dump(mode="json"),
                    "current_slide": slides[item.slide_id].model_dump(mode="json"),
                    "available_slides": [
                        slide.model_dump(mode="json")
                        for slide in context.slides
                        if slide.order <= slides[item.slide_id].order
                    ],
                    "knowledge_units": [
                        unit.model_dump(mode="json")
                        for unit in context.knowledge_units
                        if unit.id in item.knowledge_unit_ids
                    ],
                }
                for item in blueprints
            ],
            "output_schema": TypeAdapter(list[ScriptedInteraction]).json_schema(),
        }
        raw = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "SHARED_INTERACTION_QUESTION_GENERATOR_V1\n"
                        "Generate one grounded classroom interaction per blueprint. Use only "
                        "knowledge and evidence available at or before that slide; never use future "
                        "slides. Copy only supplied claim/result/asset IDs and source refs. Return "
                        "JSON {items:[...]}."
                    ),
                ),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.25,
        )
        parsed = json.loads(raw)
        items = TypeAdapter(list[ScriptedInteraction]).validate_python(parsed.get("items", []))
        return ScriptedInteractionBank(
            presentation_plan_id=context.presentation_plan_id,
            items=items,
        )

    @staticmethod
    def _fallback(
        context: InteractionPlanningContext,
        blueprint: InteractionBlueprint,
    ) -> ScriptedInteraction:
        slide = next(item for item in context.slides if item.slide_id == blueprint.slide_id)
        units = {item.id: item for item in context.knowledge_units}
        summaries = [
            units[item].summary for item in blueprint.knowledge_unit_ids if item in units
        ]
        answer = " ".join(item for item in summaries if item).strip() or slide.message
        if not answer or not blueprint.source_refs:
            raise ValueError(f"blueprint lacks sufficient grounded evidence: {blueprint.id}")
        identity = hashlib.sha256(blueprint.id.encode()).hexdigest()[:16]
        return ScriptedInteraction(
            id=f"scripted_interaction_{identity}",
            blueprint_id=blueprint.id,
            slide_id=blueprint.slide_id,
            question="这一页的核心证据或概念应该怎样理解？",
            answer=answer,
            source_refs=blueprint.source_refs,
            claim_ids=blueprint.claim_ids,
            result_ids=blueprint.result_ids,
            asset_ids=blueprint.asset_ids,
        )


class InteractionValidator:
    """Shared evidence, chronology, duplication, and identifier validator."""

    _number = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?")

    def validate(
        self,
        context: InteractionPlanningContext,
        blueprints: list[InteractionBlueprint],
        bank: ScriptedInteractionBank,
    ) -> ScriptedInteractionBank:
        if bank.presentation_plan_id != context.presentation_plan_id:
            raise ValueError("interaction bank belongs to another presentation")
        blueprint_by_id = {item.id: item for item in blueprints}
        if len(blueprint_by_id) != len(blueprints):
            raise ValueError("interaction blueprint ids must be unique")
        slide_by_id = {item.slide_id: item for item in context.slides}
        seen_questions: set[str] = set()
        for item in bank.items:
            blueprint = blueprint_by_id.get(item.blueprint_id)
            if blueprint is None or item.slide_id != blueprint.slide_id:
                raise ValueError("interaction references an unknown blueprint or slide")
            if not set(item.claim_ids).issubset(blueprint.claim_ids):
                raise ValueError("interaction uses unauthorized claims")
            if not set(item.result_ids).issubset(blueprint.result_ids):
                raise ValueError("interaction uses unauthorized results")
            if not set(item.asset_ids).issubset(blueprint.asset_ids):
                raise ValueError("interaction uses unauthorized assets")
            allowed_refs = {self._ref_key(ref) for ref in blueprint.source_refs}
            if not {self._ref_key(ref) for ref in item.source_refs}.issubset(allowed_refs):
                raise ValueError("interaction uses unauthorized source refs")
            slide = slide_by_id[item.slide_id]
            authorized_numbers = {
                self._normalize_number(number)
                for candidate in context.slides
                if candidate.order <= slide.order
                for number in [
                    *candidate.verified_numbers,
                    *self._number.findall(candidate.speaker_script),
                ]
            }
            used_numbers = {
                self._normalize_number(number)
                for number in self._number.findall(f"{item.question} {item.answer}")
            }
            if used_numbers - authorized_numbers:
                raise ValueError("interaction contains unverified or future numbers")
            normalized_question = "".join(item.question.casefold().split())
            if normalized_question in seen_questions:
                raise ValueError("interaction bank contains duplicate questions")
            seen_questions.add(normalized_question)
        if {item.blueprint_id for item in bank.items} != set(blueprint_by_id):
            raise ValueError("interaction bank must cover every selected blueprint once")
        return bank.model_copy(update={"validation_status": "validated"})

    @staticmethod
    def _ref_key(ref):
        return (ref.material_id, ref.page_id, ref.page_no, ref.text_span, ref.image_path)

    @staticmethod
    def _normalize_number(value: str) -> str:
        return re.sub(r"[\s,]", "", value).casefold().replace("×", "x")


class UnifiedInteractionPlanningPipeline:
    """One route-neutral path from context to a validated scripted bank."""

    def __init__(
        self,
        *,
        selector: TeachingNodeSelector | None = None,
        generator: QuestionGenerator | None = None,
        validator: InteractionValidator | None = None,
    ) -> None:
        self.selector = selector or TeachingNodeSelector()
        self.generator = generator or QuestionGenerator()
        self.validator = validator or InteractionValidator()

    def plan(
        self,
        context: InteractionPlanningContext,
        policy: InteractionPolicy,
    ) -> tuple[list[InteractionBlueprint], ScriptedInteractionBank]:
        blueprints = self.selector.select(context, policy)
        pending = self.generator.generate(context, blueprints)
        try:
            validated = self.validator.validate(context, blueprints, pending)
        except ValueError:
            safe_items = [
                self._safe_fallback(context, item, variant=index)
                for index, item in enumerate(blueprints)
            ]
            validated = self.validator.validate(
                context,
                blueprints,
                ScriptedInteractionBank(
                    presentation_plan_id=context.presentation_plan_id,
                    items=safe_items,
                ),
            )
        return blueprints, validated

    @staticmethod
    def _safe_fallback(
        context: InteractionPlanningContext,
        blueprint: InteractionBlueprint,
        *,
        variant: int,
    ) -> ScriptedInteraction:
        slide = next(item for item in context.slides if item.slide_id == blueprint.slide_id)
        identity = hashlib.sha256(f"safe:{blueprint.id}".encode()).hexdigest()[:16]
        prompts = (
            "核心机制、证据关系或适用边界应该怎样理解？",
            "最容易被误解的关系是什么，应如何结合证据澄清？",
            "论文直接支持了什么，又有哪些结论不能扩大？",
            "这一部分在整套方法中承担什么作用？",
            "应当怎样从页面证据推导出受限定的结论？",
        )
        return ScriptedInteraction(
            id=f"scripted_interaction_{identity}",
            blueprint_id=blueprint.id,
            slide_id=blueprint.slide_id,
            question=f"围绕“{slide.title}”，{prompts[variant % len(prompts)]}",
            answer=(
                "请结合当前页面与论文证据理解这一部分：先说明页面呈现的核心关系，"
                "再区分论文直接支持的结论与需要谨慎解释的边界，不引入页面之外的数值。"
            ),
            source_refs=blueprint.source_refs,
            claim_ids=blueprint.claim_ids,
            result_ids=blueprint.result_ids,
            asset_ids=blueprint.asset_ids,
        )
