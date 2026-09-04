from __future__ import annotations

from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.presentation.schemas import PresentationPlan

from .contracts import (
    EvidenceIndex,
    InteractionEvidenceEntry,
    InteractionKnowledgeUnit,
    InteractionPlanningContext,
    InteractionSection,
    InteractionSlide,
)


class PresentationInteractionAdapter:
    """Adapter shared by generated and source-deck presentations."""

    def adapt(
        self,
        *,
        plan: PresentationPlan,
        knowledge_units: list[KnowledgeUnit],
        duration_minutes: int,
    ) -> InteractionPlanningContext:
        if plan.mode not in {"generated", "source_deck"}:
            raise ValueError("PresentationInteractionAdapter supports generated/source_deck only")
        units = {item.id: item for item in knowledge_units}
        slides: list[InteractionSlide] = []
        entries: list[InteractionEvidenceEntry] = []
        for slide in plan.slides:
            unknown = set(slide.knowledge_unit_ids) - units.keys()
            if unknown:
                raise ValueError(f"interaction slide references unknown knowledge units: {unknown}")
            refs = self._source_refs(plan, slide.source_page_no)
            interaction_slide = InteractionSlide(
                slide_id=slide.id,
                order=slide.order,
                title=slide.title,
                message=" ".join(slide.key_points),
                role=self._role(slide),
                speaker_script=slide.speaker_script,
                knowledge_unit_ids=slide.knowledge_unit_ids,
                source_refs=refs,
                evidence_strength="direct" if refs else "contextual",
                visual_labels=slide.visual_payload,
            )
            slides.append(interaction_slide)
            entries.append(InteractionEvidenceEntry(slide_id=slide.id, source_refs=refs))
        used = {unit_id for slide in slides for unit_id in slide.knowledge_unit_ids}
        return InteractionPlanningContext(
            content_id=plan.content_id,
            presentation_plan_id=plan.id,
            mode=plan.mode,
            duration_minutes=duration_minutes,
            slides=slides,
            sections=[
                InteractionSection(
                    id=f"interaction_section_{slide.slide_id}",
                    title=slide.title,
                    slide_ids=[slide.slide_id],
                )
                for slide in slides
            ],
            knowledge_units=[
                InteractionKnowledgeUnit(
                    id=unit.id,
                    title=unit.title,
                    summary=unit.summary,
                    importance=unit.importance,
                    source_refs=unit.source_refs,
                )
                for unit in knowledge_units
                if unit.id in used
            ],
            evidence_index=EvidenceIndex(entries=entries),
        )

    @staticmethod
    def _source_refs(plan: PresentationPlan, page_no: int | None) -> list[SourceRef]:
        if not plan.source_material_id or page_no is None:
            return []
        return [
            SourceRef(
                material_id=plan.source_material_id,
                page_id=f"page_{page_no:03d}",
                page_no=page_no,
            )
        ]

    @staticmethod
    def _role(slide) -> str:
        text = f"{slide.title} {' '.join(slide.key_points)}".casefold()
        if slide.order == 1:
            return "cover"
        if any(value in text for value in ("方法", "流程", "method", "process")):
            return "method"
        if any(value in text for value in ("实验", "结果", "result", "evidence")):
            return "evidence"
        if any(value in text for value in ("局限", "limitation")):
            return "limitation"
        return "concept"
