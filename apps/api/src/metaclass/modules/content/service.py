from fastapi import HTTPException

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.base import LearningProvider
from metaclass.modules.content.repository import ContentRepository
from metaclass.modules.content.schemas import (
    LearningContent,
    LearningSection,
    PageUnderstanding,
    QuizItem,
)
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
        pages = {page.id: page for page in self.materials.pages(material_id)}
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
