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
            point = (
                understanding.knowledge_points[0] if understanding.knowledge_points else page.title
            )
            quiz = QuizItem(
                id=f"quiz_{page.page_no:03d}",
                question="本页主要讲解的内容是？",
                options=[point, "以上内容均未出现"],
                correct_index=0,
                explanation=f"本页的核心知识点是：{point}",
                knowledge_point=point,
                source_refs=understanding.source_refs,
            )
            sections.append(
                LearningSection(
                    id=f"section_{page.page_no:03d}",
                    title=page.title or f"第 {page.page_no} 页",
                    summary=understanding.summary,
                    knowledge_points=understanding.knowledge_points,
                    source_refs=understanding.source_refs,
                    quiz_items=[quiz],
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
