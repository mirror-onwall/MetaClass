import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.content.schemas import LearningContent
from metaclass.modules.presentation.schemas import PresentationPlan
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.repository import QuestionBankRepository

InteractionIntensity: TypeAlias = Literal["none", "light", "standard", "rich"]


@dataclass(frozen=True)
class InteractionBudget:
    minimum: int
    maximum: int


@dataclass(frozen=True)
class SlideEligibility:
    slide_id: str
    eligible: bool
    reason: str


@dataclass(frozen=True)
class SlideCandidate:
    slide_id: str
    slide_order: int
    score: float
    signals: tuple[str, ...]
    content_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class InteractionPlanningResult:
    intensity: InteractionIntensity
    effective_slide_ids: tuple[str, ...]
    effective_slide_count: int
    budget: InteractionBudget
    assessments: tuple[SlideEligibility, ...]
    candidates: tuple[SlideCandidate, ...] = ()
    selected_slide_ids: tuple[str, ...] = ()
    selection_source: Literal["none", "llm", "deterministic", "persisted"] = "none"


class InteractionPlanningService:
    """The single post-PresentationPlan entry point for scripted interactions."""

    def __init__(
        self,
        generator: QuestionBankGenerator,
        repository: QuestionBankRepository,
        llm: LLMProvider | None = None,
        presentation_repository=None,
    ) -> None:
        self.generator = generator
        self.repository = repository
        self.llm = llm
        self.presentation_repository = presentation_repository

    def plan(
        self,
        content: LearningContent,
        presentation: PresentationPlan,
        intensity: InteractionIntensity,
        progress_callback: Callable[[int, str, str], None] | None = None,
    ) -> InteractionPlanningResult:
        report = progress_callback or (lambda _progress, _step, _message: None)
        report(10, "reading_context", "正在读取课件结构和已保存进度")
        if intensity == "none":
            self._save_planning_state(presentation, intensity, [], "complete")
            report(100, "completed", "无互动模式，无需生成互动")
            return InteractionPlanningResult(
                intensity=intensity,
                effective_slide_ids=(),
                effective_slide_count=0,
                budget=InteractionBudget(0, 0),
                assessments=(),
            )

        valid_slide_ids = {slide.id for slide in presentation.slides}
        can_resume = (
            presentation.interaction_intensity == intensity
            and presentation.interaction_planning_status
            in {"nodes_selected", "complete"}
        )
        saved_node_ids = (
            tuple(
                slide_id
                for slide_id in presentation.interaction_node_ids
                if slide_id in valid_slide_ids
            )
            if can_resume
            else ()
        )
        result = self.assess(
            content,
            presentation,
            intensity,
            selected_slide_ids=saved_node_ids or None,
        )
        report(35, "nodes_selected", f"已选择 {len(result.selected_slide_ids)} 个互动节点")
        if not result.selected_slide_ids:
            self._save_planning_state(presentation, intensity, [], "complete")
            return result

        if not saved_node_ids:
            self._save_planning_state(
                presentation,
                intensity,
                list(result.selected_slide_ids),
                "nodes_selected",
            )

        existing = self.repository.list_for_plan(presentation.id)
        completed_slide_ids = {item.slide_id for item in existing}
        target_slide_ids = set(result.selected_slide_ids)
        if target_slide_ids.issubset(completed_slide_ids):
            self._save_planning_state(
                presentation, intensity, list(result.selected_slide_ids), "complete"
            )
            return result

        pending_count = len(target_slide_ids - completed_slide_ids)
        generated_count = 0
        for batch in self.generator.generate_node_batches(
            content,
            presentation,
            target_slide_ids=target_slide_ids,
            completed_slide_ids=completed_slide_ids,
        ):
            self.repository.append_for_plan(batch)
            generated_count += len({item.slide_id for item in batch})
            progress = 40 + round(45 * min(generated_count, pending_count) / pending_count)
            report(
                progress,
                "generating_questions",
                f"正在生成互动问答 {min(generated_count, pending_count)}/{pending_count}",
            )
        report(90, "validating", "正在验证互动节点和问答完整性")
        completed_slide_ids = {
            item.slide_id for item in self.repository.list_for_plan(presentation.id)
        }
        if target_slide_ids.issubset(completed_slide_ids):
            self._save_planning_state(
                presentation, intensity, list(result.selected_slide_ids), "complete"
            )
            report(100, "completed", "互动规划已完成")
        return result

    def assess(
        self,
        content: LearningContent,
        presentation: PresentationPlan,
        intensity: InteractionIntensity,
        selected_slide_ids: tuple[str, ...] | None = None,
    ) -> InteractionPlanningResult:
        sections = {section.id: section for section in content.sections}
        assessments = tuple(
            self._assess_slide(slide, presentation, sections)
            for slide in presentation.slides
        )
        effective_ids = tuple(
            item.slide_id for item in assessments if item.eligible
        )
        count = len(effective_ids)
        budget = self.calculate_budget(count, intensity)
        candidates = self._rank_candidates(content, presentation, effective_ids)[
            : budget.maximum * 2
        ]
        if selected_slide_ids is None:
            selected_ids, selection_source = self._select_nodes(
                presentation, candidates, budget
            )
        else:
            selected_ids = list(selected_slide_ids)
            selection_source = "persisted"
        return InteractionPlanningResult(
            intensity=intensity,
            effective_slide_ids=effective_ids,
            effective_slide_count=count,
            budget=budget,
            assessments=assessments,
            candidates=tuple(candidates),
            selected_slide_ids=tuple(selected_ids),
            selection_source=selection_source,
        )

    def _save_planning_state(
        self,
        presentation: PresentationPlan,
        intensity: InteractionIntensity,
        node_ids: list[str],
        status: Literal["nodes_selected", "complete"],
    ) -> None:
        presentation.interaction_intensity = intensity
        presentation.interaction_node_ids = node_ids
        presentation.interaction_planning_status = status
        if self.presentation_repository:
            self.presentation_repository.save_plan(presentation)

    @classmethod
    def _rank_candidates(
        cls,
        content: LearningContent,
        presentation: PresentationPlan,
        effective_slide_ids: tuple[str, ...],
    ) -> list[SlideCandidate]:
        sections = {section.id: section for section in content.sections}
        effective = set(effective_slide_ids)
        candidates: list[SlideCandidate] = []
        previous_tokens: set[str] = set()
        for slide in presentation.slides:
            if slide.id not in effective:
                continue
            linked = [sections[item] for item in slide.source_section_ids if item in sections]
            text = " ".join([slide.title, *slide.key_points, slide.speaker_script])
            normalized = cls._normalize(text)
            tokens = cls._tokens(normalized)
            signals: list[str] = []
            score = 0.0
            has_knowledge = bool(
                linked
                and any(
                    section.tree_node_ids
                    or section.knowledge_points
                    or section.key_points
                    or section.source_excerpts
                    for section in linked
                )
            )
            if has_knowledge:
                score += 3.0
                signals.append("core_knowledge")
            point_count = len([point for point in slide.key_points if point.strip()])
            score += min(point_count, 4) * 0.6
            if point_count >= 2:
                signals.append("substantial_key_points")
            script_length = len(cls._normalize(slide.speaker_script))
            if script_length >= 80:
                score += 2.0
                signals.append("substantial_script")
            elif script_length >= 40:
                score += 1.0
            structured_terms = {
                "图", "表", "公式", "步骤", "流程", "实验", "案例", "chart", "table",
                "formula", "step", "experiment", "case",
            }
            reasoning_terms = {
                "机制", "对比", "比较", "条件", "局限", "边界", "证据", "原因", "结果",
                "mechanism", "compare", "condition", "limitation", "evidence",
            }
            if slide.visual_payload or slide.elements or slide.paper_asset_ids or any(
                term in normalized for term in structured_terms
            ):
                score += 2.0
                signals.append("structured_or_visual")
            if slide.paper_claim_ids or any(term in normalized for term in reasoning_terms):
                score += 2.0
                signals.append("reasoning_value")
            overlap = cls._jaccard(tokens, previous_tokens)
            if overlap >= 0.72:
                score -= 3.0
                signals.append("adjacent_repetition")
            vague_summary = bool(
                re.fullmatch(
                    r"(?:本页|这一页)?(?:总结|小结|回顾|概述).{0,12}", normalized
                )
            )
            if vague_summary or ("总结" in normalized and script_length < 50):
                score -= 2.0
                signals.append("thin_summary")
            candidates.append(
                SlideCandidate(
                    slide.id,
                    slide.order,
                    score,
                    tuple(signals),
                    tuple(sorted(tokens)),
                )
            )
            previous_tokens = tokens
        return sorted(candidates, key=lambda item: (-item.score, item.slide_order))

    def _select_nodes(
        self,
        presentation: PresentationPlan,
        candidates: list[SlideCandidate],
        budget: InteractionBudget,
    ) -> tuple[list[str], Literal["none", "llm", "deterministic"]]:
        if not candidates or budget.maximum == 0:
            return [], "none"
        fallback = [
            item.slide_id
            for item in self._constrain_selection(candidates, budget.maximum)
        ]
        if not self.llm:
            return fallback, "deterministic"
        slides = {slide.id: slide for slide in presentation.slides}
        payload = {
            "lesson_title": presentation.title,
            "budget": {"suggested_minimum": budget.minimum, "maximum": budget.maximum},
            "candidates": [
                {
                    "slide_id": item.slide_id,
                    "order": item.slide_order,
                    "title": slides[item.slide_id].title,
                    "key_points": slides[item.slide_id].key_points,
                    "speaker_script": slides[item.slide_id].speaker_script,
                    "section_ids": slides[item.slide_id].source_section_ids,
                    "deterministic_score": item.score,
                    "signals": item.signals,
                }
                for item in sorted(candidates, key=lambda candidate: candidate.slide_order)
            ],
        }
        try:
            raw = self.llm.complete_json(
                self._selection_messages(payload), temperature=0.15
            )
            parsed = json.loads(raw)
            selected = parsed.get("selected_slide_ids") if isinstance(parsed, dict) else None
            allowed = {item.slide_id for item in candidates}
            if not isinstance(selected, list):
                raise TypeError("selected_slide_ids must be a list")
            unique = []
            for slide_id in selected:
                slide_id = str(slide_id)
                if slide_id in allowed and slide_id not in unique:
                    unique.append(slide_id)
            if len(unique) > budget.maximum or not unique:
                raise ValueError("invalid selected node count")
            selected_candidates = [
                item for item in candidates if item.slide_id in unique
            ]
            constrained = self._constrain_selection(
                selected_candidates, budget.maximum
            )
            if not constrained:
                raise ValueError("selected nodes have insufficient value")
            return (
                sorted(
                    [item.slide_id for item in constrained],
                    key=lambda slide_id: slides[slide_id].order,
                ),
                "llm",
            )
        except (RuntimeError, TimeoutError, TypeError, ValueError, json.JSONDecodeError):
            return fallback, "deterministic"

    @classmethod
    def _constrain_selection(
        cls,
        candidates: list[SlideCandidate],
        maximum: int,
    ) -> list[SlideCandidate]:
        """Apply hard quality, repetition and adjacency limits to final nodes."""
        selected: list[SlideCandidate] = []
        for candidate in sorted(candidates, key=lambda item: (-item.score, item.slide_order)):
            if candidate.score < 3.0:
                continue
            if any(abs(candidate.slide_order - item.slide_order) <= 1 for item in selected):
                continue
            candidate_tokens = set(candidate.content_tokens)
            if any(
                cls._jaccard(candidate_tokens, set(item.content_tokens)) >= 0.9
                for item in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= maximum:
                break
        return selected

    @staticmethod
    def _selection_messages(payload: dict) -> list[LLMMessage]:
        system = """INTERACTION_NODE_SELECTOR_V1
你是课堂互动节点规划器。候选页已由确定性规则初筛，请从整节课角度选择真正值得暂停讲解的页面，而不是生成问题。

选择原则：优先能澄清核心概念、分析证据、理解机制/条件/局限、读图表公式、梳理步骤或迁移案例的页面；
去除语义重复页面；避免节点集中在同一章节；除非连续两页各有不可替代的价值，否则不要选择相邻页面；保持互动目的多样。
数量规则：不得超过 maximum；suggested_minimum 只是建议，页面价值不足时允许更少；
达到建议数量后，新增节点必须带来明显额外教学价值。只能选择输入中的 slide_id。

只输出 JSON：
{"selected_slide_ids":["slide_id"],"reasons":[{"slide_id":"slide_id","interaction_focus":"concept|evidence|mechanism|comparison|condition|limitation|visual|procedure|case","reason":"选择理由"}]}
"""
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        latin = re.findall(r"[a-z0-9]+", text)
        chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        return set(latin + chinese)

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @staticmethod
    def calculate_budget(
        effective_slide_count: int,
        intensity: InteractionIntensity,
    ) -> InteractionBudget:
        count = max(0, effective_slide_count)
        if intensity == "none" or count == 0:
            return InteractionBudget(0, 0)

        short_deck_limit = math.ceil(count / 2)
        if intensity == "light":
            raw_minimum = max(1, math.floor(count / 15))
            maximum = min(6, math.ceil(count / 10), short_deck_limit)
        elif intensity == "standard":
            raw_minimum = max(2, math.floor(count / 10))
            maximum = min(9, math.ceil(count / 7), short_deck_limit)
        else:
            raw_minimum = max(3, math.floor(count / 7))
            maximum = min(12, math.ceil(count / 5), short_deck_limit)
        return InteractionBudget(min(raw_minimum, maximum), maximum)

    @classmethod
    def _assess_slide(cls, slide, presentation, sections) -> SlideEligibility:
        title = cls._normalize(slide.title)
        section_roles = {
            cls._normalize(sections[section_id].role)
            for section_id in slide.source_section_ids
            if section_id in sections
        }
        excluded_roles = {
            "cover",
            "agenda",
            "section",
            "transition",
            "reference",
            "appendix",
            "封面",
            "目录",
            "章节",
            "过渡",
            "参考文献",
            "附录",
        }
        if section_roles and section_roles.issubset(excluded_roles):
            return SlideEligibility(slide.id, False, "non_body_section_role")

        if cls._matches_non_body_title(title):
            return SlideEligibility(slide.id, False, "non_body_title")

        first_slide = slide.order == min(item.order for item in presentation.slides)
        title_matches_deck = title == cls._normalize(presentation.title)
        if first_slide and title_matches_deck and len(slide.key_points) <= 1:
            return SlideEligibility(slide.id, False, "cover_like_first_slide")

        point_text = cls._normalize(" ".join(slide.key_points))
        script_text = cls._normalize(slide.speaker_script)
        has_structured_evidence = bool(
            slide.paper_claim_ids
            or slide.paper_asset_ids
            or slide.visual_payload
            or slide.elements
        )
        if len(point_text) < 12 and len(script_text) < 24 and not has_structured_evidence:
            return SlideEligibility(slide.id, False, "insufficient_content")

        return SlideEligibility(slide.id, True, "body_content")

    @staticmethod
    def _matches_non_body_title(title: str) -> bool:
        exact_titles = {
            "目录",
            "课程目录",
            "内容目录",
            "agenda",
            "outline",
            "table of contents",
            "致谢",
            "感谢聆听",
            "谢谢",
            "thanks",
            "thank you",
            "参考文献",
            "references",
            "bibliography",
            "附录",
            "appendix",
        }
        if title in exact_titles:
            return True
        return bool(
            re.fullmatch(r"(?:第\s*)?[一二三四五六七八九十百0-9]+\s*[章节篇部分]", title)
            or re.fullmatch(r"(?:chapter|part|section)\s+[a-z0-9ivx]+", title)
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.strip().lower().split())
