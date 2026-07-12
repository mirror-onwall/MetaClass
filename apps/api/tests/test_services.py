from pathlib import Path
from unittest.mock import Mock

from metaclass.infrastructure.providers.llm import GeminiVisionProvider
from metaclass.infrastructure.providers.learning import LLMLearningProvider
from metaclass.modules.content.schemas import LearningContent, LearningSection
from metaclass.modules.materials.schemas import PageMetadata, SourceRef
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


def test_llm_learning_provider_describes_visual_and_organizes_content(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"fake image payload")
    page = PageMetadata(
        id="mat_001_page_001",
        material_id="mat_001",
        page_no=1,
        title="Matrix Multiplication",
        raw_text="Rows by columns.",
        image_path=str(image),
        source_refs=[
            SourceRef(
                material_id="mat_001",
                page_id="mat_001_page_001",
                page_no=1,
                image_path=str(image),
            )
        ],
    )
    llm = Mock()
    llm.model = "vision-model"
    llm.complete_image_json.return_value = '{"visual_description": "A matrix diagram."}'
    llm.complete_json.side_effect = [
        """
        {
          "summary": "Rows combine with columns.",
          "expanded_explanation": "Start from the diagram.",
          "visual_description": "A matrix diagram.",
          "knowledge_points": ["row-column multiplication"],
          "teaching_focus": ["shape compatibility"],
          "possible_questions": ["What combines?"],
          "depends_on_pages": [],
          "leads_to_pages": [],
          "transition_to_next": "Next, practice the rule."
        }
        """,
        """
        {
          "title": "Matrix Multiplication",
          "objectives": ["Understand row-column multiplication"],
          "outline": ["Concept"],
          "sections": [
            {
              "title": "Concept",
              "page_nos": [1],
              "summary": "Rows combine with columns.",
              "teaching_script": "Start from the diagram, then connect rows to columns.",
              "knowledge_points": ["row-column multiplication"],
              "visual_summary": "A matrix diagram.",
              "transition_to_next": "Next, practice the rule.",
              "quiz_items": [
                {
                  "question": "What combines in matrix multiplication?",
                  "options": ["Rows and columns", "Only rows"],
                  "correct_index": 0,
                  "explanation": "Each output uses a row and a column.",
                  "knowledge_point": "row-column multiplication"
                }
              ]
            }
          ]
        }
        """,
    ]
    provider = LLMLearningProvider(llm, vision_enabled=True)

    visual = provider.describe_page_visual(page)
    draft = provider.organize_learning_content(
        material_id="mat_001",
        pages=[page],
        understandings=[
            provider.understand_page_with_context(
                page_no=1,
                title=page.title,
                raw_text=page.raw_text,
                previous_page=None,
                next_page=None,
                visual_description=visual,
            )
        ],
    )

    assert visual == "A matrix diagram."
    assert draft.sections[0].teaching_script.startswith("Start from the diagram")


def test_gemini_vision_provider_parses_generate_content_response(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"image bytes")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"""
            {
              "candidates": [
                {
                  "content": {
                    "parts": [
                      {"text": "{\\"visual_description\\": \\"A diagram.\\"}"}
                    ]
                  }
                }
              ]
            }
            """

    captured = {}

    def fake_urlopen(req, timeout, context):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        return Response()

    monkeypatch.setattr("metaclass.infrastructure.providers.llm.request.urlopen", fake_urlopen)
    provider = GeminiVisionProvider(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-key",
        model="gemini-2.5-flash",
    )

    result = provider.complete_image_json("Describe this page.", image)

    assert result == '{"visual_description": "A diagram."}'
    assert "/models/gemini-2.5-flash:generateContent?key=gemini-key" in captured["url"]
    assert "inline_data" in captured["body"]
