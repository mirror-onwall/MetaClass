from pathlib import Path
from unittest.mock import Mock

from metaclass.modules.content.schemas import LearningContent, LearningSection
from metaclass.infrastructure.providers.learning import LLMLearningProvider
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.video.service import VideoService


def test_video_generation_failure_is_persisted_as_failed_job(tmp_path: Path) -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="page_001",
        page_no=1,
        image_path=str(tmp_path / "page.png"),
    )
    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        title="测试内容",
        sections=[
            LearningSection(
                id="section_001",
                title="第一节",
                summary="测试视频生成失败状态。",
                source_refs=[source_ref],
            )
        ],
    )
    contents = Mock()
    contents.get.return_value = content
    repository = Mock()
    tts = Mock()
    tts.synthesize.side_effect = RuntimeError("TTS unavailable")

    job = VideoService(tmp_path, repository, contents, tts).create_job(content.id)

    assert job.status == "failed"
    assert job.error == "TTS unavailable"
    assert repository.save_job.call_count == 3
    repository.save_result.assert_not_called()


def test_mineru_content_list_is_grouped_by_page(tmp_path: Path) -> None:
    service = MaterialService(tmp_path, Mock())
    content_list = [
        {"type": "text", "page_idx": 0, "text": "Introduction"},
        {"type": "table", "page_idx": 0, "html": "<table><tr><td>A</td></tr></table>"},
        {"type": "equation", "page_idx": 1, "latex": "E=mc^2"},
    ]

    pages = service._mineru_text_by_page(content_list)

    assert pages == {
        1: "Introduction\n<table><tr><td>A</td></tr></table>",
        2: "E=mc^2",
    }


def test_llm_learning_provider_parses_page_understanding() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.return_value = """
    {
      "summary": "Matrix multiplication combines rows and columns.",
      "knowledge_points": ["matrix multiplication", "row by column"],
      "teaching_focus": ["shape compatibility"],
      "possible_questions": ["Why must dimensions match?"],
      "quiz_items": [
        {
          "question": "What must be true before multiplying two matrices?",
          "options": ["The inner dimensions match", "They have the same title"],
          "correct_index": 0,
          "explanation": "Matrix multiplication depends on compatible dimensions.",
          "knowledge_point": "shape compatibility"
        }
      ]
    }
    """

    draft = LLMLearningProvider(llm).understand_page(
        "Matrix Multiplication",
        "Rows are multiplied by columns when dimensions match.",
        1,
    )

    assert draft.summary == "Matrix multiplication combines rows and columns."
    assert draft.knowledge_points == ["matrix multiplication", "row by column"]
    assert draft.quiz_items[0].question == "What must be true before multiplying two matrices?"
    llm.complete_json.assert_called_once()
