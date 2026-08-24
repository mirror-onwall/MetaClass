import json
from pathlib import Path

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.llm import LLMProvider
from metaclass.modules.content.schemas import (
    LearningContent,
    LearningSection,
    PageRef,
)
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.paper_workflow.schemas import (
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PaperSourceBundle,
    PresentationOutline,
    SlideEvidence,
)
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan

from .paper_classroom import (
    LLMPaperClassroomComposer,
    PaperClassroomComposer,
    PaperNarrationValidator,
    PaperSlideEvidencePacket,
    PaperSlideNarration,
    SlideEvidencePacketBuilder,
)
from .paper_knowledge import PaperKnowledgeTreeBuilder


class PaperDeckContractError(ValueError):
    pass


class PaperDeckBuilder:
    """Reconcile validated authoring artifacts with the parsed final PPT.

    This first paper-deck slice is deliberately deterministic. The final deck is
    authoritative for page existence/order, while the authoring outline, notes,
    and evidence retain the paper-specific teaching intent.
    """

    def __init__(self, narration_provider: LLMProvider | None = None) -> None:
        self.narration_provider = narration_provider

    def build(
        self,
        *,
        job_id: str,
        bundle: PaperArtifactBundle,
        deck_material_id: str,
        source_paper_material_id: str,
        pages: list[PageMetadata],
        analysis: PaperAnalysis,
        figures: FigureCatalog,
        source_bundle: PaperSourceBundle,
        outline: PresentationOutline,
        evidence: SlideEvidence,
        speaker_notes_path: Path,
        audience: str,
        duration_minutes: int | None = None,
        language: str = "zh-CN",
    ) -> tuple[LearningContent, PresentationPlan]:
        ordered_pages = sorted(pages, key=lambda item: item.page_no)
        expected_pages = list(range(1, len(outline.slides) + 1))
        if [page.page_no for page in ordered_pages] != expected_pages:
            raise PaperDeckContractError(
                "final PPT pages must be continuous and match the authoring outline"
            )
        evidence_by_slide = {item.slide_id: item for item in evidence.slides}
        if list(evidence_by_slide) != [item.id for item in outline.slides]:
            raise PaperDeckContractError(
                "slide evidence must match every authoring slide in presentation order"
            )
        notes = self._speaker_notes(speaker_notes_path)
        outline_ids = {item.id for item in outline.slides}
        unknown_notes = set(notes) - outline_ids
        if unknown_notes:
            raise PaperDeckContractError(
                f"speaker notes reference unknown slides: {sorted(unknown_notes)}"
            )

        knowledge = PaperKnowledgeTreeBuilder().build(
            job_id=job_id,
            source_paper_material_id=source_paper_material_id,
            deck_material_id=deck_material_id,
            analysis=analysis,
            outline=outline,
            evidence=evidence,
        )
        packets = SlideEvidencePacketBuilder().build(
            pages=ordered_pages,
            analysis=analysis,
            outline=outline,
            evidence=evidence,
            figures=figures,
            source_bundle=source_bundle,
            knowledge_units=knowledge.knowledge_units,
            authoring_notes=notes,
            duration_minutes=duration_minutes,
        )
        narrations, narration_source, narration_warnings = self._compose_narrations(
            packets=packets,
            audience=audience,
            language=language,
        )
        packet_by_slide = {packet.slide_id: packet for packet in packets}
        narration_by_slide = {item.slide_id: item for item in narrations}

        section_by_slide = {
            slide_id: section for section in outline.sections for slide_id in section.slide_ids
        }
        learning_sections: list[LearningSection] = []
        for section_index, section in enumerate(outline.sections, start=1):
            section_slides = [item for item in outline.slides if item.id in set(section.slide_ids)]
            page_nos = [item.order for item in section_slides]
            page_items = [ordered_pages[number - 1] for number in page_nos]
            learning_sections.append(
                LearningSection(
                    id=section.id,
                    title=section.title,
                    role=section.role,
                    content_goal=section.content_goal,
                    summary=" ".join(item.purpose for item in section_slides),
                    key_points=[point for item in section_slides for point in item.key_points],
                    knowledge_points=[
                        point for item in section_slides for point in item.key_points
                    ],
                    teaching_narrative="\n\n".join(
                        narration_by_slide[item.id].speaker_script for item in section_slides
                    ),
                    teaching_script="\n\n".join(
                        narration_by_slide[item.id].speaker_script for item in section_slides
                    ),
                    source_refs=[ref for page in page_items for ref in page.source_refs],
                    page_refs=[
                        PageRef(
                            material_id=deck_material_id,
                            page_no=number,
                            reason="Final paper presentation page",
                        )
                        for number in page_nos
                    ],
                    page_nos=page_nos,
                    tree_node_ids=knowledge.section_node_ids.get(section.id, []),
                    outline_level=1,
                    transition_to_next=(
                        f"接下来进入 {outline.sections[section_index].title}。"
                        if section_index < len(outline.sections)
                        else ""
                    ),
                )
            )

        identity = job_id.removeprefix("paper_job_")
        now = utc_now()
        content = LearningContent(
            id=f"content_paper_{identity}",
            material_id=deck_material_id,
            material_ids=[deck_material_id, source_paper_material_id],
            organization_mode="paper_deck",
            title=outline.title,
            subtitle=outline.subtitle or "",
            audience={"description": audience},
            teaching_intent={
                "organization_mode": "paper_deck",
                "paper_type": outline.paper_type,
                "narrative_arc": outline.narrative_arc,
            },
            material_overview={
                "origin_paper_job_id": job_id,
                "paper_artifact_bundle_id": bundle.id,
                "source_paper_material_id": source_paper_material_id,
                "derived_deck_material_id": deck_material_id,
                "outline_status": "authoring_hint_reconciled",
            },
            knowledge_units=knowledge.knowledge_units,
            knowledge_tree=knowledge.knowledge_tree,
            objectives=outline.objectives,
            sections=learning_sections,
            generation_guidance={
                "preserve_final_deck_order": True,
                "use_authoring_notes": True,
                "use_paper_evidence": True,
            },
            quality={"validation_status": "passed", "warnings": narration_warnings},
            version=3,
            created_at=now,
            updated_at=now,
        )

        slides = []
        for page, authoring in zip(ordered_pages, outline.slides, strict=True):
            slide_evidence = evidence_by_slide[authoring.id]
            packet = packet_by_slide[authoring.id]
            narration = narration_by_slide[authoring.id]
            slides.append(
                SlidePlan(
                    id=authoring.id,
                    order=page.page_no,
                    source_section_ids=[section_by_slide[authoring.id].id],
                    source_page_no=page.page_no,
                    source_kind="source",
                    title=authoring.title,
                    key_points=authoring.key_points,
                    speaker_script=narration.speaker_script,
                    suggested_visual=authoring.layout_intent,
                    layout="source",
                    layout_id=authoring.layout_intent,
                    visual_payload=authoring.asset_ids,
                    paper_claim_ids=slide_evidence.claim_ids,
                    paper_asset_ids=slide_evidence.asset_ids,
                    paper_source_refs=[
                        item.model_dump(mode="json") for item in slide_evidence.source_refs
                    ],
                    evidence_strength=slide_evidence.evidence_strength,
                    authoring_note=packet.authoring_note,
                    speaker_script_source=narration_source,
                    paper_evidence_packet=packet.model_dump(mode="json"),
                )
            )
        plan = PresentationPlan(
            id=f"plan_paper_{identity}",
            content_id=content.id,
            title=outline.title,
            mode="paper_deck",
            source_material_id=deck_material_id,
            source_paper_material_id=source_paper_material_id,
            paper_artifact_bundle_id=bundle.id,
            slides=slides,
            generation_source="llm",
            generation_provider=bundle.provider,
            created_at=now,
            updated_at=now,
        )
        return content, plan

    def _compose_narrations(
        self,
        *,
        packets: list[PaperSlideEvidencePacket],
        audience: str,
        language: str,
    ) -> tuple[list[PaperSlideNarration], str, list[str]]:
        validator = PaperNarrationValidator()
        if self.narration_provider is not None:
            try:
                narrations = LLMPaperClassroomComposer(self.narration_provider).compose(
                    packets,
                    audience=audience,
                    language=language,
                )
                issues = validator.validate(packets=packets, narrations=narrations)
                if not issues:
                    return narrations, "paper_classroom_llm", []
                warning = "llm_narration_validation_failed:" + ",".join(
                    f"{issue.slide_id}:{issue.code}" for issue in issues
                )
            except (OSError, RuntimeError, ValueError) as exc:
                warning = f"llm_narration_failed:{type(exc).__name__}"
        else:
            warning = "llm_narration_provider_unavailable"

        fallback = PaperClassroomComposer()
        narrations = [
            fallback.compose(packet, audience=audience, language=language) for packet in packets
        ]
        issues = validator.validate(packets=packets, narrations=narrations)
        if issues:
            summary = "; ".join(f"{issue.slide_id}:{issue.code}" for issue in issues)
            raise PaperDeckContractError(f"paper classroom narration is invalid: {summary}")
        return narrations, "paper_classroom_fallback", [warning]

    @staticmethod
    def _speaker_notes(path: Path) -> dict[str, str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PaperDeckContractError("speaker_notes.json is invalid") from exc
        if isinstance(payload, dict) and "slides" not in payload:
            items = [
                (
                    {"slide_id": slide_id, **value}
                    if isinstance(value, dict)
                    else {"slide_id": slide_id, "note": value}
                )
                for slide_id, value in payload.items()
            ]
        else:
            items = payload.get("slides", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise PaperDeckContractError("speaker_notes.json must contain a slide list")
        notes: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            slide_id = str(item.get("slide_id") or item.get("id") or "").strip()
            note = str(
                item.get("note") or item.get("speaker_note") or item.get("speaker_script") or ""
            ).strip()
            if slide_id and note:
                notes[slide_id] = note
        return notes
