from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.base import LearningProvider
from metaclass.modules.content.repository import ContentRepository
from metaclass.modules.content.schemas import (
    LearningContent,
    LearningContentDraft,
    LearningSection,
    PageUnderstanding,
    PageUnderstandingDraft,
    QuizItem,
)
from metaclass.modules.materials.schemas import PageMetadata
from metaclass.modules.materials.service import MaterialService


class ContentService:
    def __init__(
        self,
        repository: ContentRepository,
        materials: MaterialService,
        provider: LearningProvider,
    ) -> None:
        self.repository = repository
        self.materials = materials
        self.provider = provider

    def understand_pages(
        self, material_id: str, *, regenerate: bool = False
    ) -> list[PageUnderstanding]:
        material = self.materials.get(material_id)
        pages = self.materials.pages(material_id)
        if material.status != "parsed" or not pages:
            raise HTTPException(409, "Parse the material before understanding its pages")

        existing = self.repository.list_understandings(material_id)
        if existing and len(existing) == len(pages) and not regenerate:
            return existing

        understandings = []
        for page in pages:
            draft = self.provider.understand_page(page.title, page.raw_text, page.page_no)
            understandings.append(
                PageUnderstanding(
                    id=f"understanding_{page.id}",
                    material_id=material_id,
                    page_id=page.id,
                    page_no=page.page_no,
                    summary=draft.summary,
                    knowledge_points=draft.knowledge_points,
                    teaching_focus=draft.teaching_focus,
                    possible_questions=draft.possible_questions,
                    quiz_items=draft.quiz_items,
                    source_refs=page.source_refs,
                    provider=self.provider.name,
                    model=self.provider.model,
                    prompt_version=getattr(self.provider, "prompt_version", "v1"),
                )
            )
        self.repository.save_understandings(understandings)
        return understandings

    def list_understandings(self, material_id: str) -> list[PageUnderstanding]:
        self.materials.get(material_id)
        return self.repository.list_understandings(material_id)

    def build(self, material_id: str) -> LearningContent:
        page_list = self.materials.pages(material_id)
        if not page_list:
            raise HTTPException(409, "Parse the material before building learning content")

        if hasattr(self.provider, "organize_learning_content"):
            try:
                content = self._build_with_global_organizer(material_id, page_list)
                self.repository.save(content)
                return content
            except Exception:
                pass

        pages = {page.id: page for page in page_list}
        understandings = self.understand_pages(material_id)
        if not understandings:
            raise HTTPException(409, "No page understanding is available")

        sections = []
        for understanding in understandings:
            page = pages[understanding.page_id]
            sections.append(
                LearningSection(
                    id=f"section_{page.page_no:03d}",
                    title=page.title or f"第 {page.page_no} 页",
                    summary=understanding.summary,
                    knowledge_points=understanding.knowledge_points,
                    source_refs=understanding.source_refs,
                    quiz_items=self._build_quiz_items(understanding, page.title),
                )
            )

        first_section = sections[0]
        content_id = f"content_{material_id.removeprefix('mat_')}"
        existing = self.repository.get(content_id)
        content = LearningContent(
            id=content_id,
            material_id=material_id,
            title=first_section.title,
            objectives=[f"理解：{point}" for point in first_section.knowledge_points[:3]],
            sections=sections,
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
        )
        self.repository.save(content)
        return content

    def _build_with_global_organizer(
        self, material_id: str, pages: list[PageMetadata]
    ) -> LearningContent:
        drafts = self._draft_understandings_with_context(pages)
        understandings = [
            self._understanding_from_draft(material_id, page, draft)
            for page, draft in zip(pages, drafts, strict=False)
        ]
        self.repository.save_understandings(understandings)

        organizer = getattr(self.provider, "organize_learning_content")
        draft = organizer(material_id=material_id, pages=pages, understandings=drafts)
        content_id = f"content_{material_id.removeprefix('mat_')}"
        existing = self.repository.get(content_id)
        return self._content_from_draft(
            content_id=content_id,
            material_id=material_id,
            draft=draft,
            pages=pages,
            created_at=existing.created_at if existing else utc_now(),
        )

    def _draft_understandings_with_context(
        self, pages: list[PageMetadata]
    ) -> list[PageUnderstandingDraft]:
        result = []
        for index, page in enumerate(pages):
            visual_description = ""
            describe_page_visual = getattr(self.provider, "describe_page_visual", None)
            if describe_page_visual:
                visual_description = describe_page_visual(page)
            understand_page_with_context = getattr(self.provider, "understand_page_with_context")
            result.append(
                understand_page_with_context(
                    page_no=page.page_no,
                    title=page.title,
                    raw_text=page.raw_text,
                    previous_page=pages[index - 1] if index > 0 else None,
                    next_page=pages[index + 1] if index + 1 < len(pages) else None,
                    visual_description=visual_description,
                )
            )
        return result

    def _understanding_from_draft(
        self, material_id: str, page: PageMetadata, draft: PageUnderstandingDraft
    ) -> PageUnderstanding:
        return PageUnderstanding(
            id=f"understanding_{page.id}",
            material_id=material_id,
            page_id=page.id,
            page_no=page.page_no,
            summary=draft.summary,
            knowledge_points=draft.knowledge_points,
            teaching_focus=draft.teaching_focus,
            possible_questions=draft.possible_questions,
            quiz_items=draft.quiz_items,
            source_refs=page.source_refs,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=getattr(self.provider, "prompt_version", "v1"),
        )

    def _content_from_draft(
        self,
        *,
        content_id: str,
        material_id: str,
        draft: LearningContentDraft,
        pages: list[PageMetadata],
        created_at,
    ) -> LearningContent:
        page_by_no = {page.page_no: page for page in pages}
        sections = []
        for index, section in enumerate(draft.sections, start=1):
            source_refs = [
                ref
                for page_no in section.page_nos
                for ref in page_by_no.get(page_no, page_by_no[pages[0].page_no]).source_refs
            ]
            if not source_refs:
                source_refs = pages[0].source_refs
            quiz_items = [
                QuizItem(
                    id=f"quiz_{index:03d}_{quiz_index:02d}",
                    question=quiz.question,
                    options=quiz.options,
                    correct_index=quiz.correct_index,
                    explanation=quiz.explanation,
                    knowledge_point=quiz.knowledge_point,
                    source_refs=source_refs,
                )
                for quiz_index, quiz in enumerate(section.quiz_items, start=1)
            ]
            sections.append(
                LearningSection(
                    id=f"section_{index:03d}",
                    title=section.title,
                    summary=section.summary,
                    knowledge_points=section.knowledge_points,
                    source_refs=source_refs,
                    quiz_items=quiz_items,
                    page_nos=section.page_nos,
                    outline_level=1,
                    teaching_script=section.teaching_script,
                    visual_summary=section.visual_summary,
                    transition_to_next=section.transition_to_next,
                )
            )
        return LearningContent(
            id=content_id,
            material_id=material_id,
            title=draft.title,
            objectives=draft.objectives or draft.outline,
            sections=sections,
            created_at=created_at,
            updated_at=utc_now(),
        )

    def get(self, content_id: str) -> LearningContent:
        content = self.repository.get(content_id)
        if not content:
            raise HTTPException(404, "Learning content not found")
        return content

    @staticmethod
    def _build_quiz_items(understanding: PageUnderstanding, page_title: str) -> list[QuizItem]:
        if understanding.quiz_items:
            return [
                QuizItem(
                    id=f"quiz_{understanding.page_no:03d}_{index:02d}",
                    question=draft.question,
                    options=draft.options,
                    correct_index=draft.correct_index,
                    explanation=draft.explanation,
                    knowledge_point=draft.knowledge_point,
                    source_refs=understanding.source_refs,
                )
                for index, draft in enumerate(understanding.quiz_items, start=1)
            ]

        point = understanding.knowledge_points[0] if understanding.knowledge_points else page_title
        if not point:
            return []
        return [
            QuizItem(
                id=f"quiz_{understanding.page_no:03d}_01",
                question=f"下面哪一项最能说明你理解了“{point}”？",
                options=[
                    f"能说出{point}的含义、条件或例子",
                    "只记住它在材料中出现过",
                    "把它和其他概念混在一起使用",
                ],
                correct_index=0,
                explanation=f"理解{point}需要能解释它如何成立或如何使用。",
                knowledge_point=point,
                source_refs=understanding.source_refs,
            )
        ]
