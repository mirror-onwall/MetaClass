from __future__ import annotations

from metaclass.modules.content.schemas import KnowledgeUnit
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.paper_workflow.grounded_slide_packet import GroundedSlidePacket
from metaclass.modules.paper_workflow.schemas import PaperAnalysis, PaperSourceBundle
from metaclass.modules.presentation.schemas import PresentationPlan

from .schemas import LiveEvidenceDocument, LiveQuestionIndex


class PaperDeckLiveQuestionAdapter:
    """Build the paper route's durable evidence index for free-form questions."""

    def adapt(
        self,
        *,
        plan: PresentationPlan,
        source_bundle: PaperSourceBundle,
        analysis: PaperAnalysis,
        knowledge_units: list[KnowledgeUnit],
        packets: list[GroundedSlidePacket],
    ) -> LiveQuestionIndex:
        if plan.mode != "paper_deck":
            raise ValueError("PaperDeckLiveQuestionAdapter requires paper_deck mode")
        packet_by_id = {item.slide_id: item for item in packets}
        if list(packet_by_id) != [item.id for item in plan.slides]:
            raise ValueError("grounded packets must match playback plan order")
        order_by_slide = {item.id: item.order for item in plan.slides}
        slides_by_source: dict[str, list[str]] = {}
        for packet in packets:
            for source_id in [
                *packet.claim_ids,
                *packet.result_ids,
                *packet.asset_ids,
                *packet.knowledge_unit_ids,
            ]:
                slides_by_source.setdefault(source_id, []).append(packet.slide_id)
        documents: list[LiveEvidenceDocument] = []
        for block in source_bundle.blocks:
            if not block.text.strip():
                continue
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_block_{block.id}",
                    kind="paper_block",
                    title=" / ".join(block.section_path) or f"论文第 {block.page_no} 页",
                    text=block.text,
                    source_refs=[self._ref(source_bundle.material_id, block.page_no, block.id)],
                )
            )
        for claim in analysis.claims:
            slide_ids = slides_by_source.get(claim.id, [])
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_claim_{claim.id}",
                    kind="claim",
                    title="论文主张",
                    text=claim.statement,
                    source_refs=[
                        self._ref(
                            source_bundle.material_id,
                            item.page_no,
                            item.block_id or item.asset_id,
                            item.quote,
                        )
                        for item in claim.source_refs
                    ],
                    slide_ids=slide_ids,
                    earliest_slide_order=self._earliest(slide_ids, order_by_slide),
                    claim_ids=[claim.id],
                )
            )
        for result in analysis.quantitative_results:
            slide_ids = slides_by_source.get(result.id, [])
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_result_{result.id}",
                    kind="result",
                    title=result.metric or "论文结果",
                    text=result.statement,
                    source_refs=[
                        self._ref(
                            source_bundle.material_id,
                            item.page_no,
                            item.block_id or item.asset_id,
                            item.quote,
                        )
                        for item in result.source_refs
                    ],
                    slide_ids=slide_ids,
                    earliest_slide_order=self._earliest(slide_ids, order_by_slide),
                    result_ids=[result.id],
                )
            )
        for asset in source_bundle.assets:
            if not asset.caption:
                continue
            slide_ids = slides_by_source.get(asset.id, [])
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_asset_{asset.id}",
                    kind="figure",
                    title=f"{asset.type.title()} caption",
                    text=asset.caption,
                    source_refs=[
                        self._ref(source_bundle.material_id, asset.page_no, asset.id)
                    ],
                    slide_ids=slide_ids,
                    earliest_slide_order=self._earliest(slide_ids, order_by_slide),
                    asset_ids=[asset.id],
                )
            )
        for unit in knowledge_units:
            slide_ids = list(
                dict.fromkeys(
                    [
                        *slides_by_source.get(unit.id, []),
                        *(item for item in unit.source_unit_ids if item in order_by_slide),
                    ]
                )
            )
            if not unit.summary.strip():
                continue
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_knowledge_{unit.id}",
                    kind="knowledge",
                    title=unit.title,
                    text=unit.summary,
                    source_refs=unit.source_refs,
                    slide_ids=slide_ids,
                    earliest_slide_order=self._earliest(slide_ids, order_by_slide),
                )
            )
        for packet in packets:
            visible = " ".join(
                [
                    packet.final_page.visible_title,
                    *packet.final_page.visible_text,
                    packet.final_page.visual_summary,
                ]
            ).strip()
            documents.append(
                LiveEvidenceDocument(
                    id=f"live_slide_{packet.slide_id}",
                    kind="slide",
                    title=packet.final_page.visible_title or packet.authoring_intent.title_hint,
                    text=visible or packet.authoring_intent.message,
                    source_refs=[
                        self._ref(
                            source_bundle.material_id,
                            item.page_no,
                            item.block_id or item.asset_id,
                            item.quote,
                        )
                        for item in packet.source_refs
                    ],
                    slide_ids=[packet.slide_id],
                    earliest_slide_order=packet.order,
                    claim_ids=packet.claim_ids,
                    result_ids=packet.result_ids,
                    asset_ids=packet.asset_ids,
                )
            )
        ids = [item.id for item in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("live question document ids must be unique")
        return LiveQuestionIndex(
            presentation_plan_id=plan.id,
            content_id=plan.content_id,
            mode="paper_deck",
            slide_order=[item.id for item in plan.slides],
            documents=documents,
        )

    @staticmethod
    def _ref(
        material_id: str,
        page_no: int,
        page_id: str | None,
        quote: str | None = None,
    ) -> SourceRef:
        return SourceRef(
            material_id=material_id,
            page_id=page_id or f"paper_page_{page_no:03d}",
            page_no=page_no,
            text_span=quote,
        )

    @staticmethod
    def _earliest(slide_ids: list[str], order_by_slide: dict[str, int]) -> int | None:
        orders = [order_by_slide[item] for item in slide_ids if item in order_by_slide]
        return min(orders) if orders else None
