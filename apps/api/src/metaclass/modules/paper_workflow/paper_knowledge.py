from __future__ import annotations

import json
from dataclasses import dataclass

from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    KnowledgeRelation,
    KnowledgeUnit,
    PageRef,
    SourceExcerpt,
)
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.paper_workflow.schemas import (
    PaperAnalysis,
    PresentationOutline,
    SlideEvidence,
    SourceReference,
)


@dataclass(frozen=True)
class PaperKnowledgeProjection:
    knowledge_units: list[KnowledgeUnit]
    knowledge_tree: CourseKnowledgeTree
    section_node_ids: dict[str, list[str]]


class PaperKnowledgeTreeBuilder:
    """Project evidence-grounded paper analysis into the shared course tree models."""

    def build(
        self,
        *,
        job_id: str,
        source_paper_material_id: str,
        deck_material_id: str,
        analysis: PaperAnalysis,
        outline: PresentationOutline,
        evidence: SlideEvidence,
    ) -> PaperKnowledgeProjection:
        identity = job_id.removeprefix("paper_job_")
        evidence_by_slide = {item.slide_id: item for item in evidence.slides}
        slide_order = {item.id: item.order for item in outline.slides}
        section_by_slide = {
            slide_id: section.id for section in outline.sections for slide_id in section.slide_ids
        }
        section_pages = {
            section.id: [slide_order[slide_id] for slide_id in section.slide_ids]
            for section in outline.sections
        }

        claim_sections: dict[str, str] = {}
        claim_pages: dict[str, list[int]] = {}
        for slide in outline.slides:
            entry = evidence_by_slide[slide.id]
            for claim_id in entry.claim_ids:
                claim_sections.setdefault(claim_id, section_by_slide[slide.id])
                claim_pages.setdefault(claim_id, []).append(slide.order)

        first_section = outline.sections[0].id
        method_section = self._find_section(outline, ("method", "mechanism", "方法", "机制"))
        result_section = self._find_section(
            outline, ("result", "evaluation", "experiment", "结果", "实验", "评测")
        )
        closing_section = self._find_section(
            outline, ("limit", "discussion", "conclusion", "局限", "讨论", "收束", "结论")
        )

        units_by_section: dict[str, list[KnowledgeUnit]] = {
            section.id: [] for section in outline.sections
        }
        core_refs = self._dedupe_source_references(
            ref
            for claim in analysis.claims
            if claim.importance == "core"
            for ref in claim.source_refs
        ) or self._dedupe_source_references(
            ref for claim in analysis.claims for ref in claim.source_refs
        )

        question_id = f"ku_paper_{identity}_question"
        units_by_section[first_section].append(
            self._unit(
                unit_id=question_id,
                title="研究问题与知识缺口",
                unit_type="research_question",
                summary=f"{analysis.central_question} {analysis.knowledge_gap}".strip(),
                refs=core_refs,
                source_material_id=source_paper_material_id,
                deck_material_id=deck_material_id,
                deck_pages=section_pages[first_section],
                importance="core",
                confidence=max(
                    (claim.confidence for claim in analysis.claims if claim.importance == "core"),
                    default=1.0,
                ),
            )
        )

        if analysis.method_summary:
            method_id = f"ku_paper_{identity}_method"
            units_by_section[method_section or first_section].append(
                self._unit(
                    unit_id=method_id,
                    title="论文方法与机制",
                    unit_type="method",
                    summary=self._plain_text(analysis.method_summary),
                    refs=core_refs,
                    source_material_id=source_paper_material_id,
                    deck_material_id=deck_material_id,
                    deck_pages=section_pages[method_section or first_section],
                    importance="core",
                    relations=[
                        KnowledgeRelation(
                            target_unit_id=question_id,
                            relation_type="addresses",
                            reason="该方法用于回答论文提出的研究问题。",
                        )
                    ],
                )
            )

        for claim in analysis.claims:
            section_id = claim_sections.get(claim.id, result_section or first_section)
            unit_id = f"ku_paper_{identity}_claim_{self._safe_id(claim.id)}"
            units_by_section[section_id].append(
                self._unit(
                    unit_id=unit_id,
                    title=claim.statement,
                    unit_type="claim",
                    summary=claim.statement,
                    refs=claim.source_refs,
                    source_material_id=source_paper_material_id,
                    deck_material_id=deck_material_id,
                    deck_pages=claim_pages.get(claim.id, section_pages[section_id]),
                    importance=claim.importance,
                    confidence=claim.confidence,
                    source_unit_ids=[claim.id],
                    relations=[
                        KnowledgeRelation(
                            target_unit_id=question_id,
                            relation_type="answers",
                            reason="该主张构成论文对研究问题的回答。",
                            confidence=claim.confidence,
                        )
                    ],
                )
            )

        for result in analysis.quantitative_results:
            related_claims = [
                claim
                for claim in analysis.claims
                if self._refs_overlap(result.source_refs, claim.source_refs)
            ]
            section_id = next(
                (
                    claim_sections[claim.id]
                    for claim in related_claims
                    if claim.id in claim_sections
                ),
                result_section or first_section,
            )
            units_by_section[section_id].append(
                self._unit(
                    unit_id=f"ku_paper_{identity}_result_{self._safe_id(result.id)}",
                    title=result.statement,
                    unit_type="quantitative_result",
                    summary=result.statement,
                    refs=result.source_refs,
                    source_material_id=source_paper_material_id,
                    deck_material_id=deck_material_id,
                    deck_pages=section_pages[section_id],
                    importance="core" if related_claims else "supporting",
                    source_unit_ids=[result.id],
                    relations=[
                        KnowledgeRelation(
                            target_unit_id=f"ku_paper_{identity}_claim_{self._safe_id(claim.id)}",
                            relation_type="supports",
                            reason="该定量结果为论文主张提供证据。",
                        )
                        for claim in related_claims
                    ],
                )
            )

        if analysis.limitations:
            section_id = closing_section or outline.sections[-1].id
            refs = self._dedupe_source_references(
                ref for limitation in analysis.limitations for ref in limitation.source_refs
            )
            units_by_section[section_id].append(
                self._unit(
                    unit_id=f"ku_paper_{identity}_limitations",
                    title="适用边界与局限",
                    unit_type="limitation",
                    summary="；".join(item.statement for item in analysis.limitations),
                    refs=refs,
                    source_material_id=source_paper_material_id,
                    deck_material_id=deck_material_id,
                    deck_pages=section_pages[section_id],
                    importance="supporting",
                    source_unit_ids=[item.id for item in analysis.limitations],
                )
            )

        for section in outline.sections:
            if units_by_section[section.id]:
                continue
            section_slides = [
                slide for slide in outline.slides if slide.id in set(section.slide_ids)
            ]
            refs = self._dedupe_source_references(
                ref for slide in section_slides for ref in evidence_by_slide[slide.id].source_refs
            )
            units_by_section[section.id].append(
                self._unit(
                    unit_id=f"ku_paper_{identity}_context_{self._safe_id(section.id)}",
                    title=section.title,
                    unit_type="context",
                    summary=" ".join(
                        [section.content_goal, *(slide.purpose for slide in section_slides)]
                    ).strip(),
                    refs=refs,
                    source_material_id=source_paper_material_id,
                    deck_material_id=deck_material_id,
                    deck_pages=section_pages[section.id],
                    importance="context",
                    source_unit_ids=[slide.id for slide in section_slides],
                    relations=(
                        [
                            KnowledgeRelation(
                                target_unit_id=question_id,
                                relation_type="contextualizes",
                                reason="该章节为论文研究问题提供汇报上下文。",
                            )
                        ]
                        if section.id != first_section
                        else []
                    ),
                )
            )

        return self._tree(
            identity=identity,
            title=outline.title,
            outline=outline,
            units_by_section=units_by_section,
            deck_material_id=deck_material_id,
            section_pages=section_pages,
        )

    def _tree(
        self,
        *,
        identity: str,
        title: str,
        outline: PresentationOutline,
        units_by_section: dict[str, list[KnowledgeUnit]],
        deck_material_id: str,
        section_pages: dict[str, list[int]],
    ) -> PaperKnowledgeProjection:
        nodes: list[CourseKnowledgeTreeNode] = []
        root_ids: list[str] = []
        teaching_sequence: list[str] = []
        section_node_ids: dict[str, list[str]] = {}
        all_units: list[KnowledgeUnit] = []
        previous_root: str | None = None
        for section_order, section in enumerate(outline.sections, start=1):
            root_id = f"kt_paper_{identity}_{self._safe_id(section.id)}"
            root_ids.append(root_id)
            section_units = units_by_section[section.id]
            all_units.extend(section_units)
            semantic_groups: dict[str, list[KnowledgeUnit]] = {}
            for unit in section_units:
                semantic_groups.setdefault(unit.unit_type, []).append(unit)
            if len(semantic_groups) <= 1:
                nodes.append(
                    CourseKnowledgeTreeNode(
                        id=root_id,
                        title=section.title,
                        role=section.role,
                        summary=section.content_goal,
                        knowledge_unit_ids=[unit.id for unit in section_units],
                        order=section_order,
                        prerequisite_node_ids=[previous_root] if previous_root else [],
                        node_type="section",
                        ref_id=section.id,
                        page_refs=self._page_refs(deck_material_id, section_pages[section.id]),
                    )
                )
                section_node_ids[section.id] = [root_id] if section_units else []
                if section_units:
                    teaching_sequence.append(root_id)
            else:
                nodes.append(
                    CourseKnowledgeTreeNode(
                        id=root_id,
                        title=section.title,
                        role=section.role,
                        summary=section.content_goal,
                        order=section_order,
                        prerequisite_node_ids=[previous_root] if previous_root else [],
                        node_type="section",
                        ref_id=section.id,
                        page_refs=self._page_refs(deck_material_id, section_pages[section.id]),
                    )
                )
                child_ids: list[str] = []
                previous_child: str | None = None
                for group_order, (unit_type, group) in enumerate(semantic_groups.items(), start=1):
                    child_id = f"{root_id}_{self._safe_id(unit_type)}"
                    child_ids.append(child_id)
                    nodes.append(
                        CourseKnowledgeTreeNode(
                            id=child_id,
                            title=self._group_title(unit_type, section.title),
                            role=section.role,
                            summary="；".join(unit.title for unit in group),
                            parent_id=root_id,
                            knowledge_unit_ids=[unit.id for unit in group],
                            order=group_order,
                            prerequisite_node_ids=[previous_child] if previous_child else [],
                            node_type="knowledge_unit",
                            page_refs=self._page_refs(
                                deck_material_id,
                                sorted({ref.page_no for unit in group for ref in unit.page_refs}),
                            ),
                        )
                    )
                    teaching_sequence.append(child_id)
                    previous_child = child_id
                section_node_ids[section.id] = child_ids
            previous_root = root_id

        return PaperKnowledgeProjection(
            knowledge_units=all_units,
            knowledge_tree=CourseKnowledgeTree(
                id=f"tree_paper_{identity}",
                title=title,
                nodes=nodes,
                root_node_ids=root_ids,
                teaching_sequence=teaching_sequence,
            ),
            section_node_ids=section_node_ids,
        )

    def _unit(
        self,
        *,
        unit_id: str,
        title: str,
        unit_type: str,
        summary: str,
        refs: list[SourceReference],
        source_material_id: str,
        deck_material_id: str,
        deck_pages: list[int],
        importance: str,
        confidence: float = 1.0,
        source_unit_ids: list[str] | None = None,
        relations: list[KnowledgeRelation] | None = None,
    ) -> KnowledgeUnit:
        source_refs = self._source_refs(refs, source_material_id)
        excerpts = [
            SourceExcerpt(
                id=f"excerpt_{unit_id}_{index:02d}",
                text=ref.quote or summary,
                type="evidence",
                reason="Evidence retained from the paper analysis.",
                importance=importance,
                usage="reference_only",
                source_refs=[source_ref],
            )
            for index, (ref, source_ref) in enumerate(zip(refs, source_refs, strict=True), start=1)
        ]
        return KnowledgeUnit(
            id=unit_id,
            title=title,
            unit_type=unit_type,
            summary=summary,
            keywords=[unit_type],
            source_excerpts=excerpts,
            source_refs=source_refs,
            page_refs=self._page_refs(deck_material_id, sorted(set(deck_pages))),
            source_unit_ids=source_unit_ids or [],
            relations=relations or [],
            importance=importance,
            confidence=confidence,
        )

    @staticmethod
    def _source_refs(refs: list[SourceReference], material_id: str) -> list[SourceRef]:
        return [
            SourceRef(
                material_id=material_id,
                page_id=ref.block_id or ref.asset_id or f"paper_page_{ref.page_no:03d}",
                page_no=ref.page_no,
                text_span=ref.quote,
            )
            for ref in refs
        ]

    @staticmethod
    def _page_refs(material_id: str, page_nos: list[int]) -> list[PageRef]:
        return [
            PageRef(
                material_id=material_id,
                page_no=page_no,
                reason="Teaching page containing this paper knowledge unit.",
            )
            for page_no in page_nos
        ]

    @staticmethod
    def _find_section(outline: PresentationOutline, terms: tuple[str, ...]) -> str | None:
        for section in outline.sections:
            text = f"{section.role} {section.title}".lower()
            if any(term.lower() in text for term in terms):
                return section.id
        return None

    @staticmethod
    def _plain_text(value: dict[str, object]) -> str:
        parts = [str(item).strip() for item in value.values() if str(item).strip()]
        return "；".join(parts) or json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _safe_id(value: str) -> str:
        return "".join(character if character.isalnum() else "_" for character in value).strip("_")

    @staticmethod
    def _group_title(unit_type: str, fallback: str) -> str:
        return {
            "research_question": "研究问题与知识缺口",
            "method": "方法与机制",
            "claim": "论文主张",
            "quantitative_result": "定量证据",
            "limitation": "适用边界与局限",
            "context": "背景与上下文",
        }.get(unit_type, fallback)

    @staticmethod
    def _refs_overlap(left: list[SourceReference], right: list[SourceReference]) -> bool:
        left_keys = {(item.page_no, item.block_id, item.asset_id) for item in left}
        right_keys = {(item.page_no, item.block_id, item.asset_id) for item in right}
        return bool(left_keys & right_keys) or bool(
            {item.page_no for item in left} & {item.page_no for item in right}
        )

    @staticmethod
    def _dedupe_source_references(refs) -> list[SourceReference]:
        unique: dict[tuple[int, str | None, str | None, str | None], SourceReference] = {}
        for ref in refs:
            unique[(ref.page_no, ref.block_id, ref.asset_id, ref.quote)] = ref
        return list(unique.values())
