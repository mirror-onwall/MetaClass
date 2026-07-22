from metaclass.modules.classroom.agent_schemas import StudentAgentType
from metaclass.modules.question_bank.schemas import ClassroomQA
from metaclass.modules.question_bank.service import QuestionBankService


class StubRepository:
    def __init__(self, items: list[ClassroomQA]) -> None:
        self.items = items
        self.saved: dict[str, list[float]] = {}

    def list_for_plan(self, plan_id: str) -> list[ClassroomQA]:
        return self.items

    def save_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        self.saved.update(embeddings)


class StubEmbeddingProvider:
    name = "stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "随机森林" in text or "降低方差" in text else [0.0, 1.0]
            for text in texts
        ]


class FailingEmbeddingProvider:
    name = "failing"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("endpoint unavailable")


def qa_item(item_id: str, question: str, answer: str) -> ClassroomQA:
    return ClassroomQA(
        id=item_id,
        presentation_plan_id="presentation_001",
        content_id="content_001",
        slide_id="slide_001",
        slide_order=1,
        agent_type=StudentAgentType.RESEARCHER,
        student_profile_id="profile_001",
        knowledge_point=question,
        canonical_question=question,
        student_question=question,
        canonical_answer=answer,
        teacher_answer=answer,
        moment="after_explanation",
        placement_reason="test",
    )


def service(repository, provider) -> QuestionBankService:
    return QuestionBankService(repository, None, None, None, provider)  # type: ignore[arg-type]


def test_search_uses_embeddings_and_persists_missing_vectors() -> None:
    forest = qa_item("qa_forest", "随机森林为什么有效？", "多个树可以降低方差。")
    cluster = qa_item("qa_cluster", "什么是聚类？", "把相似样本组织为一组。")
    repository = StubRepository([cluster, forest])

    results = service(repository, StubEmbeddingProvider()).search(
        "presentation_001", "集成树模型如何降低方差？", limit=1
    )

    assert results[0].item.id == "qa_forest"
    assert results[0].score == 4.0
    assert set(repository.saved) == {"qa_forest", "qa_cluster"}


def test_search_falls_back_to_keywords_when_embedding_fails() -> None:
    forest = qa_item("qa_forest", "随机森林为什么有效？", "多个树可以降低方差。")
    repository = StubRepository([forest])

    results = service(repository, FailingEmbeddingProvider()).search(
        "presentation_001", "随机森林", limit=1
    )

    assert results[0].item.id == "qa_forest"
    assert results[0].score >= 3.0
