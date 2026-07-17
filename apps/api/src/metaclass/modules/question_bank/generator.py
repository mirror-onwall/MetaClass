from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider
from metaclass.modules.classroom.agent_schemas import (
    StudentAgentProfile,
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
        profiles = get_default_student_agent_profiles()
        candidates = self._student_candidates_in_parallel(plan, profiles)
        candidates = self._deduplicate(candidates)
        candidates = self._limit_candidates_for_teacher(candidates, plan)
        # The teacher receives and answers the complete candidate set in one call.
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
    ) -> list[QuestionCandidate]:
        if not self.llm:
            return [
                self._fallback_candidate(slide, profile)
                for profile in profiles
                for slide in plan.slides
            ]

        # One model request per student profile. The eight independent profiles
        # run concurrently, so latency is close to the slowest profile request.
        results: dict[str, list[QuestionCandidate]] = {}
        with ThreadPoolExecutor(
            max_workers=min(self.student_concurrency, len(profiles))
        ) as executor:
            futures = {
                executor.submit(self._student_batch_candidates, plan, profile): profile
                for profile in profiles
            }
            for future in as_completed(futures):
                profile = futures[future]
                try:
                    results[profile.id] = future.result()
                except Exception:
                    results[profile.id] = [
                        self._fallback_candidate(slide, profile) for slide in plan.slides
                    ]
        # Preserve stable profile order despite concurrent completion order.
        return [item for profile in profiles for item in results.get(profile.id, [])]

    def _student_batch_candidates(
        self,
        plan: PresentationPlan,
        profile: StudentAgentProfile,
    ) -> list[QuestionCandidate]:
        if not self.llm:
            return [self._fallback_candidate(slide, profile) for slide in plan.slides]
        slides_by_id = {slide.id: slide for slide in plan.slides}
        try:
            raw = self.llm.complete_json(
                self._student_batch_messages(plan, profile), temperature=0.45
            )
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
                raise ValueError("Student batch response must contain a questions list")
            questions = payload["questions"]
            counts: dict[str, int] = {}
            items = []
            for question in questions:
                slide_id = str(question["slide_id"])
                slide = slides_by_id.get(slide_id)
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
            return items
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return [self._fallback_candidate(slide, profile) for slide in plan.slides]

    @staticmethod
    def _student_batch_messages(
        plan: PresentationPlan,
        profile: StudentAgentProfile,
    ) -> list[LLMMessage]:
        system = f"""你是备课阶段的学生智能体，不是在课堂现场自由聊天。

你的画像：
- 名称：{profile.display_name}
- 核心作用：{profile.core_role}
- 主要行为：{'；'.join(profile.behaviors)}
- 学习目标：{profile.learning_goal}
- 表达风格：{profile.response_style}

你需要一次性完成整节课各阶段的提问准备。输入中的每个 checkpoint 都是一个独立的听课时刻；生成该 checkpoint 的问题时，只能使用它自己的 course_so_far，其中只包含当前页及此前页面和讲稿。严禁把其他 checkpoint 中更晚页面的信息用于较早页面的问题。
每个 checkpoint 提出 0-2 个在当时进度下真实仍会产生、且能推动理解的问题。可以联系此前页面与当前页面的关系，但不能使用未来知识，不要重复已经直接回答的内容，也不要考无关冷知识。
canonical_question 使用中性标准问法；student_question 保持同一含义并符合你的画像。没有值得问的问题可以不输出该页。

只输出 JSON：
{{"questions":[{{"slide_id":"问题所属当前页 ID","knowledge_point":"知识点","canonical_question":"标准问题","student_question":"画像化问法","reason":"当时为什么会产生疑问"}}]}}
"""
        checkpoints = []
        for index, slide in enumerate(plan.slides):
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
                        for item in plan.slides[: index + 1]
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
        question = f"{point.rstrip('。')}为什么成立？"
        return QuestionCandidate(
            candidate_id=f"candidate_{slide.id}_{profile.type.value}_1",
            slide_id=slide.id,
            slide_order=slide.order,
            agent_type=profile.type,
            student_profile_id=profile.id,
            knowledge_point=point,
            canonical_question=question,
            student_question=question,
            reason="该问题检查页面核心结论背后的原因。",
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
canonical_answer 是准确完整的标准答案；teacher_answer 是课堂上自然、简洁、直接针对该问题的回答。必须先回应学生具体问了什么，不能复述整页讲稿、不能重新做课程开场、不能用与不同问题相同的通用段落代替答案。
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
                temperature=0.2,
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
        answer = (
            f"针对“{candidate.canonical_question}”，可以先抓住"
            f"{candidate.knowledge_point}这个关键点：{evidence}。"
        )
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
