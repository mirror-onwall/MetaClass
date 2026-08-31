from __future__ import annotations

import hashlib
from typing import Literal

from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.paper_workflow.grounded_slide_packet import GroundedSlidePacket
from metaclass.modules.paper_workflow.paper_classroom import SlideNarration
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan


class PaperDeckPlaybackPlanError(ValueError):
    """Raised when grounded paper artifacts cannot form a safe playback plan."""


class PaperDeckPlaybackPlanBuilder:
    """Project an authoritative PDF deck into PresentationPlan without authoring slides."""

    def build(
        self,
        *,
        content_id: str,
        title: str,
        generated_pdf_material_id: str,
        original_paper_material_id: str,
        paper_artifact_bundle_id: str,
        presentation_resource_id: str,
        packets: list[GroundedSlidePacket],
        narrations: list[SlideNarration],
        knowledge_units: list[KnowledgeUnit],
        interaction_intensity: Literal["none", "light", "standard", "rich"] = "standard",
        plan_id: str | None = None,
    ) -> PresentationPlan:
        if not packets or [item.order for item in packets] != list(range(1, len(packets) + 1)):
            raise PaperDeckPlaybackPlanError("grounded packets must be continuous and non-empty")
        if len({item.slide_id for item in packets}) != len(packets):
            raise PaperDeckPlaybackPlanError("grounded packet slide ids must be unique")
        if any(item.repair_directive is not None for item in packets):
            raise PaperDeckPlaybackPlanError(
                "unverified final pages must be repaired before playback plan construction"
            )
        narration_by_id = {item.slide_id: item for item in narrations}
        if len(narration_by_id) != len(narrations) or list(narration_by_id) != [
            item.slide_id for item in packets
        ]:
            raise PaperDeckPlaybackPlanError("narrations must cover packets once and in order")
        if any(item.validation_status != "validated" for item in narrations):
            raise PaperDeckPlaybackPlanError("all narrations must be evidence-validated")
        unit_ids = {item.id for item in knowledge_units}
        slides: list[SlidePlan] = []
        for packet in packets:
            narration = narration_by_id[packet.slide_id]
            resolved_units = list(
                dict.fromkeys(
                    [
                        *packet.knowledge_unit_ids,
                        *(
                            unit.id
                            for unit in knowledge_units
                            if packet.slide_id in unit.source_unit_ids
                            or any(
                                ref.material_id == generated_pdf_material_id
                                and ref.page_no == packet.order
                                for ref in unit.page_refs
                            )
                        ),
                    ]
                )
            )
            unknown_units = set(resolved_units) - unit_ids
            if unknown_units:
                raise PaperDeckPlaybackPlanError(
                    f"playback slide references unknown knowledge units: {sorted(unknown_units)}"
                )
            slides.append(
                SlidePlan(
                    id=packet.slide_id,
                    order=packet.order,
                    source_section_ids=[packet.slide_id],
                    source_page_no=packet.order,
                    source_kind="source",
                    title=(
                        packet.final_page.visible_title
                        or packet.authoring_intent.title_hint
                    ),
                    key_points=packet.authoring_intent.planned_text,
                    speaker_script=narration.speaker_script,
                    suggested_visual=(
                        packet.final_page.visual_summary
                        or packet.authoring_intent.visual_intent
                    ),
                    layout="source",
                    knowledge_unit_ids=resolved_units,
                    paper_claim_ids=packet.claim_ids,
                    paper_asset_ids=packet.asset_ids,
                    paper_source_refs=[
                        item.model_dump(mode="json") for item in packet.source_refs
                    ],
                    evidence_strength=packet.evidence_strength,
                    authoring_note=packet.authoring_intent.message,
                    speaker_script_source="paper_classroom_llm",
                    paper_evidence_packet=packet.model_dump(mode="json"),
                )
            )
        identity = hashlib.sha256(
            "\0".join(
                [
                    content_id,
                    generated_pdf_material_id,
                    presentation_resource_id,
                    *(item.slide_id for item in packets),
                ]
            ).encode()
        ).hexdigest()
        return PresentationPlan(
            id=plan_id or f"presentation_plan_{identity[:24]}",
            content_id=content_id,
            title=title,
            mode="paper_deck",
            source_material_id=generated_pdf_material_id,
            source_paper_material_id=original_paper_material_id,
            paper_artifact_bundle_id=paper_artifact_bundle_id,
            presentation_resource_id=presentation_resource_id,
            slides=slides,
            generation_source="unknown",
            generation_provider="native_paper_deck",
            interaction_intensity=interaction_intensity,
        )
