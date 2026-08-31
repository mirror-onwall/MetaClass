import re
from uuid import uuid4

from fastapi import HTTPException

from metaclass.infrastructure.providers.embedding import EmbeddingProvider, cosine_similarity
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
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.repository = repository
        self.contents = contents
        self.presentations = presentations
        self.generator = generator
        self.embedding_provider = embedding_provider

    def generate_for_plan(self, plan_id: str) -> QuestionBank:
        plan = self.presentations.get_plan(plan_id)
        content = self.contents.get(plan.content_id)
        existing = self.repository.list_for_plan(plan.id)
        completed_slide_ids = {item.slide_id for item in existing}
        target_slide_ids = set(plan.interaction_node_ids) or {
            slide.id for slide in plan.slides
        }
        for batch in self.generator.generate_node_batches(
            content,
            plan,
            target_slide_ids=target_slide_ids,
            completed_slide_ids=completed_slide_ids,
        ):
            self.repository.append_for_plan(batch)
        items = self.repository.list_for_plan(plan.id)
        return QuestionBank(
            presentation_plan_id=plan.id,
            content_id=content.id,
            generation_id=items[0].generation_id if items else "legacy",
            items=items,
        )

    def regenerate_for_plan(self, plan_id: str) -> QuestionBank:
        """Build and append a fresh bank generation without touching history."""
        plan = self.presentations.get_plan(plan_id)
        content = self.contents.get(plan.content_id)
        target_slide_ids = set(plan.interaction_node_ids) or {
            slide.id for slide in plan.slides
        }
        generation_id = f"qa_bank_{uuid4().hex[:12]}"
        fresh_items = [
            item.model_copy(update={"generation_id": generation_id})
            for batch in self.generator.generate_node_batches(
                content,
                plan,
                target_slide_ids=target_slide_ids,
                completed_slide_ids=set(),
            )
            for item in batch
        ]
        if target_slide_ids and not fresh_items:
            raise HTTPException(502, "Question bank regeneration returned no questions")
        self.repository.append_for_plan(fresh_items)
        return QuestionBank(
            presentation_plan_id=plan.id,
            content_id=content.id,
            generation_id=generation_id,
            items=fresh_items,
        )

    def get_for_plan(self, plan_id: str) -> QuestionBank:
        plan = self.presentations.get_plan(plan_id)
        return QuestionBank(
            presentation_plan_id=plan.id,
            content_id=plan.content_id,
            generation_id=(items[0].generation_id if (items := self.repository.list_for_plan(plan.id)) else "legacy"),
            items=items,
        )

    def list_versions_for_plan(self, plan_id: str) -> list[QuestionBank]:
        plan = self.presentations.get_plan(plan_id)
        return [
            QuestionBank(
                presentation_plan_id=plan.id,
                content_id=plan.content_id,
                generation_id=items[0].generation_id,
                items=items,
            )
            for items in self.repository.list_versions_for_plan(plan.id)
            if items
        ]

    def archive_generation(self, plan_id: str, generation_id: str) -> None:
        self.presentations.get_plan(plan_id)
        self.repository.archive_generation(plan_id, generation_id)

    def get_item(self, qa_id: str) -> ClassroomQA:
        item = self.repository.get_item(qa_id)
        if not item:
            raise HTTPException(404, "Prepared classroom question not found")
        return item

    def search(self, plan_id: str, query: str, limit: int = 5) -> list[QuestionSearchResult]:
        query = query.strip().lower()
        if not query:
            raise HTTPException(422, "Search query must not be empty")
        items = self.repository.list_for_plan(plan_id)
        if self.embedding_provider:
            try:
                self._ensure_embeddings(items)
                query_embedding = self.embedding_provider.embed([query])[0]
                scored = [
                    QuestionSearchResult(
                        item=item,
                        # Preserve the existing caller's 3.0 acceptance threshold:
                        # cosine >= 0.75 is considered a confident semantic match.
                        score=max(0.0, cosine_similarity(query_embedding, item.embedding or []))
                        * 4.0,
                    )
                    for item in items
                    if item.embedding
                ]
                return sorted(scored, key=lambda result: result.score, reverse=True)[:limit]
            except (RuntimeError, IndexError):
                # Keep classrooms usable if the configured embedding endpoint is down.
                pass
        return self._keyword_search(items, query, limit)

    def _ensure_embeddings(self, items: list[ClassroomQA]) -> None:
        missing = [item for item in items if not item.embedding]
        if not missing or not self.embedding_provider:
            return
        vectors: list[list[float]] = []
        batch_size = 64
        texts = [self._embedding_text(item) for item in missing]
        for start in range(0, len(texts), batch_size):
            vectors.extend(self.embedding_provider.embed(texts[start : start + batch_size]))
        if len(vectors) != len(missing):
            raise RuntimeError("Embedding response count does not match QA count")
        saved = {}
        for item, vector in zip(missing, vectors, strict=True):
            item.embedding = vector
            saved[item.id] = vector
        self.repository.save_embeddings(saved)

    @staticmethod
    def _embedding_text(item: ClassroomQA) -> str:
        return "\n".join(
            [
                f"知识点：{item.knowledge_point}",
                f"标准问题：{item.canonical_question}",
                f"学生问法：{item.student_question}",
                f"标准答案：{item.canonical_answer}",
            ]
        )

    def _keyword_search(
        self, items: list[ClassroomQA], query: str, limit: int
    ) -> list[QuestionSearchResult]:
        tokens = self._search_tokens(query)
        scored = []
        for item in items:
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
