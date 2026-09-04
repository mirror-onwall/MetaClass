from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.classroom.agent_schemas import (
    StudentAgentProfile,
    StudentAgentType,
    get_default_student_agent_profiles,
)
from metaclass.modules.content.schemas import LearningContent
from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan
from metaclass.modules.question_bank.schemas import (
    ClassroomQA,
    ControllerPlacement,
    QuestionCandidate,
    TeacherPreparedAnswer,
)


class QuestionBankGenerator:
    """Three-stage lesson preparation: students ask, teacher answers, controller places."""

    STUDENT_SLIDES_PER_BATCH = 8
    STUDENT_CONTEXT_WINDOW = 3
    NODE_BATCH_SIZE = 6

    def __init__(
        self,
        llm: LLMProvider | None = None,
        *,
        student_concurrency: int = 3,
        candidates_per_slide: int = 4,
    ) -> None:
        self.llm = llm
        self.student_concurrency = max(1, min(8, student_concurrency))
        self.candidates_per_slide = max(1, candidates_per_slide)

    def generate(self, content: LearningContent, plan: PresentationPlan) -> list[ClassroomQA]:
        return [item for batch in self.generate_batches(content, plan) for item in batch]

    def generate_batches(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        *,
        completed_slide_ids: set[str] | None = None,
        target_slide_ids: set[str] | None = None,
    ):
        """Yield complete QA records in durable slide batches.

        Callers can commit every yielded batch and pass the already persisted
        slide IDs when resuming, so a later provider failure does not discard
        earlier questions and answers.
        """
        profiles = get_default_student_agent_profiles()
        completed = completed_slide_ids or set()
        pending = [
            slide
            for slide in plan.slides
            if slide.id not in completed
            and (target_slide_ids is None or slide.id in target_slide_ids)
        ]
        for start in range(0, len(pending), self.STUDENT_SLIDES_PER_BATCH):
            slides = pending[start : start + self.STUDENT_SLIDES_PER_BATCH]
            yield self._generate_slide_batch(content, plan, profiles, slides)

    def generate_node_batches(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        *,
        target_slide_ids: set[str],
        completed_slide_ids: set[str] | None = None,
    ):
        """Generate exactly one primary classroom QA for each selected node."""
        completed = completed_slide_ids or set()
        selected = [
            slide
            for slide in plan.slides
            if slide.id in target_slide_ids and slide.id not in completed
        ]
        for start in range(0, len(selected), self.NODE_BATCH_SIZE):
            slides = selected[start : start + self.NODE_BATCH_SIZE]
            yield self._generate_node_batch(content, plan, slides)

    def _generate_node_batch(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        slides: list[SlidePlan],
    ) -> list[ClassroomQA]:
        fallback = {slide.id: self._fallback_node_qa(content, plan, slide) for slide in slides}
        if not self.llm:
            return list(fallback.values())
        try:
            raw = self.llm.complete_json(
                self._node_batch_messages(content, plan, slides), temperature=0.3
            )
            payload = json.loads(raw)
            questions = payload.get("questions") if isinstance(payload, dict) else None
            if not isinstance(questions, list):
                raise TypeError("questions must be a list")
            profiles = {
                profile.type.value: profile
                for profile in get_default_student_agent_profiles()
            }
            slide_by_id = {slide.id: slide for slide in slides}
            generated: dict[str, ClassroomQA] = {}
            sections = {section.id: section for section in content.sections}
            for item in questions:
                if not isinstance(item, dict):
                    continue
                slide_id = str(item.get("slide_id") or "")
                slide = slide_by_id.get(slide_id)
                profile = profiles.get(str(item.get("agent_type") or ""))
                if not slide or not profile or slide_id in generated:
                    continue
                required = {
                    key: str(item.get(key) or "").strip()
                    for key in (
                        "knowledge_point",
                        "canonical_question",
                        "student_question",
                        "canonical_answer",
                        "teacher_answer",
                    )
                }
                if not all(required.values()):
                    continue
                compatible_types = []
                for value in item.get("compatible_agent_types") or []:
                    compatible = profiles.get(str(value))
                    if (
                        compatible
                        and compatible.type != profile.type
                        and compatible.type not in compatible_types
                    ):
                        compatible_types.append(compatible.type)
                generated[slide_id] = ClassroomQA(
                    id=f"qa_{uuid4().hex[:12]}",
                    presentation_plan_id=plan.id,
                    content_id=content.id,
                    slide_id=slide.id,
                    slide_order=slide.order,
                    agent_type=profile.type,
                    compatible_agent_types=compatible_types[:3],
                    student_profile_id=profile.id,
                    **required,
                    moment="after_explanation",
                    placement_reason=(
                        str(item.get("placement_reason") or "").strip()
                        or f"在第 {slide.order} 页讲解后提出该节点的主问题。"
                    ),
                    source_refs=self._source_refs_for_slide(slide, sections),
                )
            return [generated.get(slide.id, fallback[slide.id]) for slide in slides]
        except (RuntimeError, TimeoutError, TypeError, ValueError, json.JSONDecodeError):
            return list(fallback.values())

    @staticmethod
    def _node_batch_messages(
        content: LearningContent,
        plan: PresentationPlan,
        slides: list[SlidePlan],
    ) -> list[LLMMessage]:
        profiles = get_default_student_agent_profiles()
        system = """INTERACTION_NODE_QUESTION_GENERATOR_V1
你是课堂备课问答生成器。输入页面已经由全局规划器选为最终互动节点，不要重新选页。

对每个节点必须且只能生成一道中性的主要问题，并同时完成首选学生类型、1-3 个兼容学生类型、自然学生问法、标准答案和教师课堂回答。
问题应针对该页最有教学价值的概念、证据、机制、对比、条件、局限、图表、公式、步骤、实验或案例。
避免复述页面已经直接说清楚的句子。只能使用截至当前页已经出现的内容，不得提前使用未来页面知识。
canonical_answer 应准确完整；teacher_answer 应自然、简洁、直接回应学生疑问。
不得虚构数据、实验结果、论文结论或来源。每个 slide_id 只能出现一次，不能遗漏或增加页面。

只输出 JSON：
{"questions":[{"slide_id":"节点ID","agent_type":"首选学生类型","compatible_agent_types":["兼容学生类型"],"knowledge_point":"知识点","canonical_question":"中性标准问题","student_question":"自然学生问法","canonical_answer":"标准答案","teacher_answer":"教师课堂回答","placement_reason":"为什么适合在本页讲解后互动"}]}
"""
        user = {
            "lesson_title": plan.title,
            "student_profiles": [
                {
                    "agent_type": profile.type.value,
                    "role": profile.core_role,
                    "learning_goal": profile.learning_goal,
                    "response_style": profile.response_style,
                }
                for profile in profiles
            ],
            "nodes": [
                {
                    "slide_id": slide.id,
                    "order": slide.order,
                    "title": slide.title,
                    "key_points": slide.key_points,
                    "visual_content": slide.visual_payload,
                    "speaker_script": slide.speaker_script,
                    "source_sections": [
                        {
                            "title": section.title,
                            "summary": section.summary,
                            "knowledge_points": section.knowledge_points,
                            "source_excerpts": [
                                excerpt.model_dump(mode="json")
                                for excerpt in section.source_excerpts
                            ],
                        }
                        for section in content.sections
                        if section.id in slide.source_section_ids
                    ],
                }
                for slide in slides
            ],
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]

    def _fallback_node_qa(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        slide: SlidePlan,
    ) -> ClassroomQA:
        text = self._normalize_for_match(
            " ".join([slide.title, *slide.key_points, slide.speaker_script])
        )
        if any(term in text for term in ("机制", "条件", "局限", "证据", "mechanism")):
            agent_type = StudentAgentType.DEEP_THINKER
        elif any(term in text for term in ("案例", "应用", "实践", "case", "application")):
            agent_type = StudentAgentType.PRACTICAL_APPLIER
        elif any(term in text for term in ("对比", "区别", "概念", "compare")):
            agent_type = StudentAgentType.CONCEPT_CONFUSED
        else:
            agent_type = StudentAgentType.FOUNDATION_WEAK
        profiles = {profile.type: profile for profile in get_default_student_agent_profiles()}
        profile = profiles[agent_type]
        candidate = self._fallback_candidate(slide, profile)
        answer = self._fallback_answer(plan, candidate)
        sections = {section.id: section for section in content.sections}
        return ClassroomQA(
            id=f"qa_{uuid4().hex[:12]}",
            presentation_plan_id=plan.id,
            content_id=content.id,
            slide_id=slide.id,
            slide_order=slide.order,
            agent_type=profile.type,
            compatible_agent_types=self._fallback_compatible_types(profile.type),
            student_profile_id=profile.id,
            knowledge_point=candidate.knowledge_point,
            canonical_question=candidate.canonical_question,
            student_question=candidate.student_question,
            canonical_answer=answer.canonical_answer,
            teacher_answer=answer.teacher_answer,
            moment="after_explanation",
            placement_reason=f"在第 {slide.order} 页讲解后提出该节点的主问题。",
            source_refs=self._source_refs_for_slide(slide, sections),
        )

    @classmethod
    def _source_refs_for_slide(cls, slide: SlidePlan, sections) -> list:
        source_refs = []
        for section_id in slide.source_section_ids:
            if section_id in sections:
                source_refs.extend(sections[section_id].source_refs)
        return cls._unique_source_refs(source_refs)

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        return " ".join(text.lower().split())

    @staticmethod
    def _fallback_compatible_types(agent_type: StudentAgentType) -> list[StudentAgentType]:
        similarities = {
            StudentAgentType.DEEP_THINKER: [
                StudentAgentType.RESEARCHER,
                StudentAgentType.CONCEPT_CONFUSED,
            ],
            StudentAgentType.RESEARCHER: [
                StudentAgentType.DEEP_THINKER,
                StudentAgentType.PRACTICAL_APPLIER,
            ],
            StudentAgentType.FOUNDATION_WEAK: [
                StudentAgentType.CONCEPT_CONFUSED,
                StudentAgentType.SILENT_OBSERVER,
            ],
            StudentAgentType.CONCEPT_CONFUSED: [
                StudentAgentType.FOUNDATION_WEAK,
                StudentAgentType.DEEP_THINKER,
            ],
            StudentAgentType.PRACTICAL_APPLIER: [
                StudentAgentType.RESEARCHER,
                StudentAgentType.ATMOSPHERE_REGULATOR,
            ],
            StudentAgentType.NOTE_TAKER: [
                StudentAgentType.SILENT_OBSERVER,
                StudentAgentType.FOUNDATION_WEAK,
            ],
        }
        return similarities.get(
            agent_type,
            [StudentAgentType.RESEARCHER, StudentAgentType.NOTE_TAKER],
        )

    def _generate_slide_batch(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        profiles: list[StudentAgentProfile],
        slides: list[SlidePlan],
    ) -> list[ClassroomQA]:
        candidates = self._student_candidates_in_parallel(
            plan, profiles, checkpoint_slides=slides
        )
        candidates = self._deduplicate(candidates)
        candidates = self._limit_candidates_for_teacher(candidates, plan)
        answers = self._teacher_answers(content, plan, candidates)
        placements = self._controller_placements(plan, candidates, answers)
        answers_by_id = {item.candidate_id: item for item in answers}
        placements_by_id = {item.candidate_id: item for item in placements}
        slides = {slide.id: slide for slide in plan.slides}
        sections = {section.id: section for section in content.sections}
        result: list[ClassroomQA] = []
        for candidate in candidates:
            answer = answers_by_id.get(candidate.candidate_id)
            placement = placements_by_id.get(candidate.candidate_id)
            if not answer or not answer.answerable or not placement or not placement.approved:
                continue
            slide = slides[candidate.slide_id]
            source_refs = []
            for section_id in slide.source_section_ids:
                if section_id in sections:
                    source_refs.extend(sections[section_id].source_refs)
            result.append(
                ClassroomQA(
                    id=f"qa_{uuid4().hex[:12]}",
                    presentation_plan_id=plan.id,
                    content_id=content.id,
                    slide_id=candidate.slide_id,
                    slide_order=candidate.slide_order,
                    agent_type=candidate.agent_type,
                    student_profile_id=candidate.student_profile_id,
                    knowledge_point=candidate.knowledge_point,
                    canonical_question=candidate.canonical_question,
                    student_question=candidate.student_question,
                    canonical_answer=answer.canonical_answer,
                    teacher_answer=answer.teacher_answer,
                    moment=placement.moment,
                    placement_reason=placement.placement_reason,
                    source_refs=self._unique_source_refs(source_refs),
                )
            )
        return result

    def _student_candidates_in_parallel(
        self,
        plan: PresentationPlan,
        profiles: list[StudentAgentProfile],
        checkpoint_slides: list[SlidePlan] | None = None,
    ) -> list[QuestionCandidate]:
        slides = checkpoint_slides if checkpoint_slides is not None else plan.slides
        if not self.llm:
            return [
                self._fallback_candidate(slide, profile)
                for profile in profiles
                for slide in slides
            ]

        # Profiles run concurrently. Each profile splits a long deck into small,
        # sequential requests so prompt size stays bounded.
        results: dict[str, list[QuestionCandidate]] = {}
        with ThreadPoolExecutor(
            max_workers=min(self.student_concurrency, len(profiles))
        ) as executor:
            futures = {
                executor.submit(
                    self._student_batch_candidates, plan, profile, slides
                ): profile
                for profile in profiles
            }
            for future in as_completed(futures):
                profile = futures[future]
                try:
                    results[profile.id] = future.result()
                except Exception:
                    results[profile.id] = [
                        self._fallback_candidate(slide, profile) for slide in slides
                    ]
        # Preserve stable profile order despite concurrent completion order.
        return [item for profile in profiles for item in results.get(profile.id, [])]

    def _student_batch_candidates(
        self,
        plan: PresentationPlan,
        profile: StudentAgentProfile,
        checkpoint_slides: list[SlidePlan] | None = None,
    ) -> list[QuestionCandidate]:
        slides = checkpoint_slides if checkpoint_slides is not None else plan.slides
        if not self.llm:
            return [self._fallback_candidate(slide, profile) for slide in slides]
        items = []
        for start in range(0, len(slides), self.STUDENT_SLIDES_PER_BATCH):
            batch = slides[start : start + self.STUDENT_SLIDES_PER_BATCH]
            batch_slides = {slide.id: slide for slide in batch}
            try:
                raw = self.llm.complete_json(
                    self._student_batch_messages(plan, profile, batch), temperature=0.45
                )
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
                    raise ValueError("Student batch response must contain a questions list")
                counts: dict[str, int] = {}
                for question in payload["questions"]:
                    slide_id = str(question["slide_id"])
                    slide = batch_slides.get(slide_id)
                    if not slide or counts.get(slide_id, 0) >= 2:
                        continue
                    counts[slide_id] = counts.get(slide_id, 0) + 1
                    items.append(
                        QuestionCandidate(
                            candidate_id=(
                                f"candidate_{slide.id}_{profile.type.value}_"
                                f"{counts[slide_id]}"
                            ),
                            slide_id=slide.id,
                            slide_order=slide.order,
                            agent_type=profile.type,
                            student_profile_id=profile.id,
                            knowledge_point=str(question["knowledge_point"]),
                            canonical_question=str(question["canonical_question"]),
                            student_question=str(question["student_question"]),
                            reason=str(question["reason"]),
                        )
                    )
            except (
                RuntimeError,
                TimeoutError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                ValidationError,
            ):
                # Degrade only the failed page batch. Later batches still get a
                # chance to use the model and produce profile-specific questions.
                items.extend(self._fallback_candidate(slide, profile) for slide in batch)
        return items

    @staticmethod
    def _student_batch_messages(
        plan: PresentationPlan,
        profile: StudentAgentProfile,
        checkpoint_slides: list[SlidePlan] | None = None,
    ) -> list[LLMMessage]:
        system = f"""你是备课阶段的学生智能体，不是在课堂现场自由聊天。

你的画像：
- 名称：{profile.display_name}
- 核心作用：{profile.core_role}
- 主要行为：{'；'.join(profile.behaviors)}
- 学习目标：{profile.learning_goal}
- 表达风格：{profile.response_style}

你需要完成一小批课堂页面的提问准备。输入中的每个 checkpoint 都是一个独立的听课时刻；course_so_far 只提供当前页和最近几页的必要上下文。只能依据该 checkpoint 的内容提问，严禁使用其他 checkpoint 中更晚页面的信息。
每个 checkpoint 提出 0-2 个在当时进度下真实仍会产生、且能推动理解的问题。可以联系此前页面与当前页面的关系，但不能使用未来知识，不要重复已经直接回答的内容，也不要考无关冷知识。
先判断这个学生在当前页最可能出现哪一种真实困惑，再决定是否提问以及如何提问。问题可以用于澄清概念、比较区别、索要例子、理解条件与边界、梳理步骤、检查图表、联系实际应用或衔接前后内容；应体现学生画像，不要把所有知识点都改写成追问原因或“为什么成立”。
同一个学生跨页面的问题也应随页面内容变化。定义页更适合澄清与辨析，流程页更适合追问步骤和关键节点，案例页更适合迁移与应用，图表页更适合询问读图方式，结论页才可能追问依据、条件或例外。
canonical_question 使用中性标准问法；student_question 保持同一含义并符合你的画像。没有值得问的问题可以不输出该页。

只输出 JSON：
{{"questions":[{{"slide_id":"问题所属当前页 ID","knowledge_point":"知识点","canonical_question":"标准问题","student_question":"画像化问法","reason":"当时为什么会产生疑问"}}]}}
"""
        checkpoints = []
        slides = checkpoint_slides if checkpoint_slides is not None else plan.slides
        slide_indexes = {slide.id: index for index, slide in enumerate(plan.slides)}
        for slide in slides:
            index = slide_indexes[slide.id]
            context_start = max(0, index - QuestionBankGenerator.STUDENT_CONTEXT_WINDOW + 1)
            checkpoints.append(
                {
                    "current_slide_id": slide.id,
                    "current_slide_order": slide.order,
                    "course_so_far": [
                        {
                            "slide_id": item.id,
                            "slide_order": item.order,
                            "title": item.title,
                            "page_content": item.key_points,
                            "visual_content": item.visual_payload,
                            "speaker_script": item.speaker_script,
                        }
                        for item in plan.slides[context_start : index + 1]
                    ],
                }
            )
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(
                role="user",
                content=json.dumps({"checkpoints": checkpoints}, ensure_ascii=False),
            ),
        ]

    def _student_candidates(
        self,
        slide: SlidePlan,
        profile: StudentAgentProfile,
        slides_so_far: list[SlidePlan],
    ) -> list[QuestionCandidate]:
        fallback = self._fallback_candidate(slide, profile)
        if not self.llm:
            return [fallback]
        messages = self._student_messages(slide, profile, slides_so_far)
        try:
            raw = self.llm.complete_json(messages, temperature=0.45)
            payload = json.loads(raw)
            questions = payload.get("questions", []) if isinstance(payload, dict) else []
            items = []
            for index, question in enumerate(questions[:2], start=1):
                items.append(
                    QuestionCandidate(
                        candidate_id=f"candidate_{slide.id}_{profile.type.value}_{index}",
                        slide_id=slide.id,
                        slide_order=slide.order,
                        agent_type=profile.type,
                        student_profile_id=profile.id,
                        knowledge_point=str(question["knowledge_point"]),
                        canonical_question=str(question["canonical_question"]),
                        student_question=str(question["student_question"]),
                        reason=str(question["reason"]),
                    )
                )
            return items or [fallback]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return [fallback]

    @staticmethod
    def _student_messages(
        slide: SlidePlan,
        profile: StudentAgentProfile,
        slides_so_far: list[SlidePlan],
    ) -> list[LLMMessage]:
        system = f"""你是备课阶段的学生智能体，不是在课堂现场自由聊天。

你的画像：
- 名称：{profile.display_name}
- 核心作用：{profile.core_role}
- 主要行为：{'；'.join(profile.behaviors)}
- 学习目标：{profile.learning_goal}
- 表达风格：{profile.response_style}

请假设你在上课前不知道这部分知识，现在已经按顺序看完从课程开始到当前页的全部 PPT，并听完这些页面对应的老师讲稿。提出 0-2 个你在当前进度下真实仍会产生、且能推动理解的问题。
你可以联系此前页面与当前页面的关系，追问概念衔接、前后结论、因果机制或潜在矛盾；但问题必须以截至当前页已经讲过的内容为基础，不能使用未来页面的信息，也不要考老师无关的冷知识。
先识别当前内容最适合产生的疑问类型，再自然提问。可选择概念澄清、相近概念辨析、具体例子、条件与边界、操作步骤、图表解读、实际应用或前后衔接，不要默认把每个知识点都写成追问原因。
不要重复此前页面或当前页面已经直接回答的问题。问题原则上归属当前页；只有当疑问来自前后内容的联系时，才在 reason 中说明关联了哪些此前页面。
canonical_question 使用中性、清晰的标准问法；student_question 保持同一含义，但按你的学生画像自然表达。
如果本页内容已经非常清楚、没有值得问的问题，返回空数组。

只输出 JSON：
{{"questions":[{{"knowledge_point":"知识点","canonical_question":"标准问题","student_question":"画像化问法","reason":"为什么学生会产生这个疑问"}}]}}
"""
        user = {
            "current_slide_id": slide.id,
            "current_slide_order": slide.order,
            "current_slide": {
                "title": slide.title,
                "page_content": slide.key_points,
                "visual_content": slide.visual_payload,
                "speaker_script": slide.speaker_script,
            },
            "course_so_far": [
                {
                    "slide_id": item.id,
                    "slide_order": item.order,
                    "title": item.title,
                    "page_content": item.key_points,
                    "visual_content": item.visual_payload,
                    "speaker_script": item.speaker_script,
                }
                for item in slides_so_far
            ],
        }
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
        ]

    @staticmethod
    def _fallback_candidate(
        slide: SlidePlan, profile: StudentAgentProfile
    ) -> QuestionCandidate:
        point = (slide.key_points or [slide.title])[0]
        point = point.rstrip("。")
        questions = {
            StudentAgentType.ATMOSPHERE_REGULATOR: f"能不能用一个生活化的例子说明{point}？",
            StudentAgentType.DEEP_THINKER: f"{point}需要满足哪些条件，遇到什么情况会不适用？",
            StudentAgentType.NOTE_TAKER: f"如果把{point}整理成笔记，最关键的要点有哪些？",
            StudentAgentType.RESEARCHER: f"{point}在真实研究或业务场景中通常怎么应用？",
            StudentAgentType.FOUNDATION_WEAK: f"{point}能不能拆开一步一步解释？",
            StudentAgentType.SILENT_OBSERVER: f"可以用一个具体例子再说明一下{point}吗？",
            StudentAgentType.CONCEPT_CONFUSED: f"{point}和前面相近的概念应该怎么区分？",
            StudentAgentType.PRACTICAL_APPLIER: f"实际操作时，{point}具体要怎么做？",
        }
        question = questions[profile.type]
        return QuestionCandidate(
            candidate_id=f"candidate_{slide.id}_{profile.type.value}_1",
            slide_id=slide.id,
            slide_order=slide.order,
            agent_type=profile.type,
            student_profile_id=profile.id,
            knowledge_point=point,
            canonical_question=question,
            student_question=question,
            reason=f"该问题体现{profile.display_name}在当前知识点上的典型学习需求。",
        )

    def _teacher_answers(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        candidates: list[QuestionCandidate],
    ) -> list[TeacherPreparedAnswer]:
        if not candidates:
            return []
        if not self.llm:
            return [self._fallback_answer(plan, item) for item in candidates]
        prepared = []
        # Keep each response bounded. A single all-course request with dozens of
        # answers tends to time out or return truncated JSON on proxy providers.
        for index in range(0, len(candidates), 8):
            batch = candidates[index : index + 8]
            prepared.extend(self._teacher_answer_batch(content, plan, batch))
        return prepared

    def _teacher_answer_batch(
        self,
        content: LearningContent,
        plan: PresentationPlan,
        candidates: list[QuestionCandidate],
    ) -> list[TeacherPreparedAnswer]:
        system = """你是备课阶段的教师智能体。请为学生候选问题准备答案。
你会获得整节课的 PPT 页面内容、全部讲稿和 LearningContent。请先综合课程结构、当前页、前后页面和已有材料，再结合你作为教师掌握的可靠通用知识准备答案。
你可以补充材料没有展开、但对解释问题必要且稳定可靠的定义、原因、机制、例子或辨析；不得虚构具体数据、实验结果、文献观点、人物言论或不确定事实。
回答不能与 LearningContent 或整套 PPT 的课程口径冲突。若问题依赖无法确认的特定事实，设置 answerable=false；不要为了回答而猜测。
canonical_answer 是准确完整的标准答案；teacher_answer 是课堂上自然、简洁、直接回应该问题的口语回答。先判断学生真正卡住的是概念、原因、区别、步骤还是应用，再像现场教师一样顺着这个困惑作答，而不是套用统一的答题格式。
回答的第一句话就进入实质内容。根据问题最合适的解释路径自然组织语言：可以直接给结论后解释原因，可以从学生容易混淆的地方澄清，可以借一个贴切例子说明，也可以通过对比或分步骤推导帮助理解。是否承接学生的原话、是否给予肯定，都由具体语境决定。
一批回答读起来应像老师分别听完不同学生的问题后作出的真实回应：语气连贯、有交流感，详略和节奏随问题变化。不要复述整页讲稿、重新做课程开场，也不要把不同问题填进同一段模板。
只输出 JSON：{"answers":[{"candidate_id":"...","canonical_answer":"...","teacher_answer":"...","answerable":true}]}"""
        user = {
            "course_presentation": [
                {
                    "id": slide.id,
                    "order": slide.order,
                    "title": slide.title,
                    "page_content": slide.key_points,
                    "visual_content": slide.visual_payload,
                    "speaker_script": slide.speaker_script,
                }
                for slide in plan.slides
            ],
            "learning_content": self._learning_content_payload(content),
            "questions": [item.model_dump(mode="json") for item in candidates],
        }
        try:
            raw = self.llm.complete_json(
                [
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
                ],
                temperature=0.45,
            )
            payload = json.loads(raw)
            prepared = TypeAdapter(list[TeacherPreparedAnswer]).validate_python(
                payload.get("answers", [])
            )
            prepared_ids = {item.candidate_id for item in prepared}
            prepared.extend(
                self._fallback_answer(plan, item)
                for item in candidates
                if item.candidate_id not in prepared_ids
            )
            return prepared
        except (RuntimeError, TimeoutError, ValueError, json.JSONDecodeError, ValidationError):
            return [self._fallback_answer(plan, item) for item in candidates]

    @staticmethod
    def _fallback_answer(
        plan: PresentationPlan, candidate: QuestionCandidate
    ) -> TeacherPreparedAnswer:
        slide = next(item for item in plan.slides if item.id == candidate.slide_id)
        evidence = "；".join(slide.key_points[:2]).strip() or slide.title
        openings = (
            f"先看最关键的一点：{candidate.knowledge_point}。",
            f"这里可以从{candidate.knowledge_point}入手理解。",
            f"把{candidate.knowledge_point}抓住，这个疑问就清楚了。",
            f"简单说，关键就在{candidate.knowledge_point}。",
        )
        opening = openings[sum(map(ord, candidate.candidate_id)) % len(openings)]
        answer = f"{opening}{evidence}。"
        return TeacherPreparedAnswer(
            candidate_id=candidate.candidate_id,
            canonical_answer=answer,
            teacher_answer=answer,
            answerable=bool(answer),
        )

    def _controller_placements(
        self,
        plan: PresentationPlan,
        candidates: list[QuestionCandidate],
        answers: list[TeacherPreparedAnswer],
    ) -> list[ControllerPlacement]:
        if not self.llm:
            return [
                ControllerPlacement(
                    candidate_id=item.candidate_id,
                    moment="after_explanation",
                    placement_reason=f"在第 {item.slide_order} 页讲解后追问对应知识点。",
                )
                for item in candidates
            ]
        system = """你是备课阶段的课堂 Controller。审核每个问答是否适合进入课堂，并标明插入时机。
问题必须放在它所属 slide_id，不得移动到无关页面。moment 只能是 before_explanation、during_explanation、after_explanation、before_next_slide。
优先 after_explanation；只有用于引入的问题才 before_explanation。重复、材料无法回答或打断节奏的问题应 rejected。
只输出 JSON：{"placements":[{"candidate_id":"...","approved":true,"moment":"after_explanation","placement_reason":"..."}]}"""
        user = {
            "slides": [{"id": item.id, "order": item.order, "title": item.title} for item in plan.slides],
            "questions": [item.model_dump(mode="json") for item in candidates],
            "answers": [item.model_dump(mode="json") for item in answers],
        }
        try:
            raw = self.llm.complete_json(
                [
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
                ],
                temperature=0.0,
            )
            payload = json.loads(raw)
            placements = TypeAdapter(list[ControllerPlacement]).validate_python(
                payload.get("placements", [])
            )
            placement_ids = {item.candidate_id for item in placements}
            placements.extend(
                ControllerPlacement(
                    candidate_id=item.candidate_id,
                    moment="after_explanation",
                    placement_reason=f"在第 {item.slide_order} 页讲解后追问对应知识点。",
                )
                for item in candidates
                if item.candidate_id not in placement_ids
            )
            return placements
        except (RuntimeError, TimeoutError, ValueError, json.JSONDecodeError, ValidationError):
            return [
                ControllerPlacement(
                    candidate_id=item.candidate_id,
                    moment="after_explanation",
                    placement_reason=f"在第 {item.slide_order} 页讲解后追问对应知识点。",
                )
                for item in candidates
            ]

    def _limit_candidates_for_teacher(
        self,
        items: list[QuestionCandidate],
        plan: PresentationPlan,
    ) -> list[QuestionCandidate]:
        """Bound the teacher batch while retaining different student personas."""
        result = []
        for slide in plan.slides:
            slide_items = [item for item in items if item.slide_id == slide.id]
            selected = []
            used_profiles = set()
            for item in slide_items:
                if item.student_profile_id in used_profiles:
                    continue
                selected.append(item)
                used_profiles.add(item.student_profile_id)
                if len(selected) >= self.candidates_per_slide:
                    break
            result.extend(selected)
        return result

    @staticmethod
    def _deduplicate(items: list[QuestionCandidate]) -> list[QuestionCandidate]:
        seen: set[tuple[str, str]] = set()
        result = []
        for item in items:
            normalized = re.sub(r"[\W_]+", "", item.canonical_question.lower())
            key = (item.slide_id, normalized)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    @staticmethod
    def _unique_source_refs(source_refs):
        unique = []
        seen = set()
        for ref in source_refs:
            key = (ref.material_id, ref.page_id, ref.page_no, ref.text_span, ref.image_path)
            if key not in seen:
                seen.add(key)
                unique.append(ref)
        return unique

    @staticmethod
    def _learning_content_payload(content: LearningContent) -> dict:
        return {
            "title": content.title,
            "subtitle": content.subtitle,
            "objectives": content.objectives,
            "audience": content.audience,
            "teaching_intent": content.teaching_intent,
            "material_overview": content.material_overview,
            "global_concepts": [
                item.model_dump(mode="json") for item in content.global_concepts
            ],
            "knowledge_units": [
                item.model_dump(mode="json") for item in content.knowledge_units
            ],
            "knowledge_tree": (
                content.knowledge_tree.model_dump(mode="json")
                if content.knowledge_tree
                else None
            ),
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "role": section.role,
                    "content_goal": section.content_goal,
                    "summary": section.summary,
                    "key_points": section.key_points,
                    "knowledge_points": section.knowledge_points,
                    "teaching_narrative": section.teaching_narrative,
                    "teaching_script": section.teaching_script,
                    "source_excerpts": [
                        item.model_dump(mode="json")
                        for item in section.source_excerpts
                    ],
                    "formulas": [
                        item.model_dump(mode="json") for item in section.formulas
                    ],
                    "examples": [
                        item.model_dump(mode="json") for item in section.examples
                    ],
                    "misconceptions": [
                        item.model_dump(mode="json")
                        for item in section.misconceptions
                    ],
                }
                for section in content.sections
            ],
        }
