import re

from fastapi import HTTPException

from metaclass.modules.content.service import ContentService
from metaclass.modules.presentation.service import PresentationService
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.repository import QuestionBankRepository
from metaclass.modules.question_bank.schemas import ClassroomQA, QuestionBank, QuestionSearchResult


class QuestionBankService:
    def __init__(
        self,
        repository: QuestionBankRepository,
        contents: ContentService,
        presentations: PresentationService,
        generator: QuestionBankGenerator,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.presentations = presentations
        self.generator = generator

    def generate_for_plan(self, plan_id: str) -> QuestionBank:
        plan = self.presentations.get_plan(plan_id)
        content = self.contents.get(plan.content_id)
        items = self.generator.generate(content, plan)
        self.repository.replace_for_plan(plan.id, items)
        return QuestionBank(
            presentation_plan_id=plan.id,
            content_id=content.id,
            items=items,
        )

    def get_for_plan(self, plan_id: str) -> QuestionBank:
        plan = self.presentations.get_plan(plan_id)
        return QuestionBank(
            presentation_plan_id=plan.id,
            content_id=plan.content_id,
            items=self.repository.list_for_plan(plan.id),
        )

    def get_item(self, qa_id: str) -> ClassroomQA:
        item = self.repository.get_item(qa_id)
        if not item:
            raise HTTPException(404, "Prepared classroom question not found")
        return item

    def search(self, plan_id: str, query: str, limit: int = 5) -> list[QuestionSearchResult]:
        query = query.strip().lower()
        if not query:
            raise HTTPException(422, "Search query must not be empty")
        tokens = self._search_tokens(query)
        scored = []
        for item in self.repository.list_for_plan(plan_id):
            fields = [
                item.knowledge_point.lower(),
                item.canonical_question.lower(),
                item.student_question.lower(),
                item.canonical_answer.lower(),
            ]
            exact = 2.0 if any(query in field for field in fields) else 0.0
            overlap = sum(1 for token in tokens if any(token in field for field in fields))
            score = exact + float(overlap)
            if score:
                scored.append(QuestionSearchResult(item=item, score=score))
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    @staticmethod
    def _search_tokens(text: str) -> set[str]:
        latin_tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
        chinese_chunks = re.findall(r"[\u4e00-\u9fff]+", text)
        chinese_tokens: set[str] = set()
        for chunk in chinese_chunks:
            chinese_tokens.update(chunk)
            chinese_tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return latin_tokens | chinese_tokens
