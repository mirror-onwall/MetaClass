from __future__ import annotations

from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.paper_workflow.grounded_slide_packet import GroundedSlidePacket
from metaclass.modules.presentation.schemas import PresentationPlan

from .contracts import (
    EvidenceIndex,
    InteractionEvidenceEntry,
    InteractionKnowledgeUnit,
    InteractionPlanningContext,
    InteractionSection,
    InteractionSlide,
)


class PaperDeckInteractionAdapter:
    """Project paper-only evidence into the route-neutral interaction contract."""

    def adapt(
        self,
        *,
        plan: PresentationPlan,
        packets: list[GroundedSlidePacket],
        knowledge_units: list[KnowledgeUnit],
        original_paper_material_id: str,
        duration_minutes: int,
    ) -> InteractionPlanningContext:
        if plan.mode != "paper_deck":
            raise ValueError("PaperDeckInteractionAdapter requires paper_deck mode")
        packet_by_id = {item.slide_id: item for item in packets}
        if len(packet_by_id) != len(packets) or list(packet_by_id) != [
            item.id for item in plan.slides
        ]:
            raise ValueError("grounded packets must match playback slides once and in order")
        units = {item.id: item for item in knowledge_units}
        slides: list[InteractionSlide] = []
        entries: list[InteractionEvidenceEntry] = []
        for slide in plan.slides:
            packet = packet_by_id[slide.id]
            resolved_units = list(
                dict.fromkeys([*slide.knowledge_unit_ids, *packet.knowledge_unit_ids])
            )
            unknown = set(resolved_units) - units.keys()
            if unknown:
                raise ValueError(f"interaction slide references unknown knowledge units: {unknown}")
            refs = [
                SourceRef(
                    material_id=original_paper_material_id,
                    page_id=(
                        item.block_id
                        or item.asset_id
                        or f"paper_page_{item.page_no:03d}"
                    ),
                    page_no=item.page_no,
                    text_span=item.quote,
                )
                for item in packet.source_refs
            ]
            verified_numbers = [
                item.mention
                for item in packet.numeric_verifications
                if item.status in {"verified", "figure_verified"}
            ]
            unverified_numbers = [
                item.mention
                for item in packet.numeric_verifications
                if item.status == "unverified"
            ]
            interaction_slide = InteractionSlide(
                slide_id=slide.id,
                order=slide.order,
                title=slide.title,
                message=packet.authoring_intent.message,
                role=packet.authoring_intent.role,
                speaker_script=slide.speaker_script,
                knowledge_unit_ids=resolved_units,
                claim_ids=packet.claim_ids,
                result_ids=packet.result_ids,
                asset_ids=packet.asset_ids,
                source_refs=refs,
                evidence_strength=packet.evidence_strength,
                visual_labels=list(
                    dict.fromkeys(
                        [
                            *packet.final_page.figure_labels,
                            *(
                                [packet.authoring_intent.source_visual_hint]
                                if packet.authoring_intent.source_visual_hint
                                else []
                            ),
                        ]
                    )
                ),
                verified_numbers=verified_numbers,
                unverified_numbers=unverified_numbers,
            )
            slides.append(interaction_slide)
            entries.append(
                InteractionEvidenceEntry(
                    slide_id=slide.id,
                    claim_ids=packet.claim_ids,
                    result_ids=packet.result_ids,
                    asset_ids=packet.asset_ids,
                    source_refs=refs,
                    verified_numbers=verified_numbers,
                )
            )
        used_unit_ids = {item for slide in slides for item in slide.knowledge_unit_ids}
        return InteractionPlanningContext(
            content_id=plan.content_id,
            presentation_plan_id=plan.id,
            mode="paper_deck",
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
                if unit.id in used_unit_ids
            ],
            evidence_index=EvidenceIndex(entries=entries),
        )
