import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from metaclass.infrastructure.providers.fake import FakeLearningProvider
from metaclass.infrastructure.providers.llm import GeminiVisionProvider
from metaclass.infrastructure.providers.learning import LLMLearningProvider
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    KnowledgeCanonicalizationDraft,
    KnowledgeMergeGroup,
    KnowledgeRelationDraft,
    KnowledgeUnit,
    LearningContent,
    LearningContentDraft,
    LearningSection,
    PageUnderstanding,
    PageRef,
    QuizItemDraft,
    VisualOpportunity,
)
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.schemas import PageImage, PageMetadata, SourceRef
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


def test_video_slides_use_ppt_images_and_speaker_scripts(tmp_path: Path) -> None:
    content = SimpleNamespace(id="content_001")
    contents = Mock()
    contents.get.return_value = content
    presentations = Mock()
    presentations.get_artifact.return_value = SimpleNamespace(
        presentation_plan_id="presentation_plan_001",
        slide_images=[
            SimpleNamespace(
                slide_id="slide_001",
                slide_no=1,
                image_path=str(tmp_path / "ppt-1.png"),
            ),
            SimpleNamespace(
                slide_id="slide_002",
                slide_no=2,
                image_path=str(tmp_path / "ppt-2.png"),
            ),
        ],
    )
    presentations.get_plan.return_value = SimpleNamespace(
        content_id=content.id,
        slides=[
            SimpleNamespace(id="slide_002", order=2, speaker_script="Slide two script"),
            SimpleNamespace(id="slide_001", order=1, speaker_script="Slide one script"),
        ],
    )
    service = VideoService(
        tmp_path,
        Mock(),
        contents,
        Mock(),
        presentations=presentations,
    )

    slides = service._video_slides(content.id, "ppt_artifact_001")

    assert slides == [
        ("Slide one script", str(tmp_path / "ppt-1.png")),
        ("Slide two script", str(tmp_path / "ppt-2.png")),
    ]


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


def test_content_service_canonicalizes_units_without_losing_sources() -> None:
    service = ContentService(Mock(), Mock(), Mock())
    first_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
    )
    second_ref = SourceRef(
        material_id="mat_002",
        page_id="mat_002_page_003",
        page_no=3,
    )
    units = [
        KnowledgeUnit(
            id="ku_001",
            title="Matrix multiplication",
            summary="Rows combine with columns.",
            keywords=["matrix", "row-column"],
            source_refs=[first_ref],
            page_refs=[PageRef(material_id="mat_001", page_no=1)],
            source_unit_ids=["raw_001"],
        ),
        KnowledgeUnit(
            id="ku_002",
            title="Matrix multiplication rule",
            summary="Inner dimensions must match.",
            keywords=["matrix", "dimensions"],
            source_refs=[second_ref],
            page_refs=[PageRef(material_id="mat_002", page_no=3)],
            source_unit_ids=["raw_002"],
        ),
        KnowledgeUnit(
            id="ku_003",
            title="Worked example",
            unit_type="example",
            summary="Apply the multiplication rule.",
            source_refs=[second_ref],
            page_refs=[PageRef(material_id="mat_002", page_no=3)],
        ),
    ]

    locally_grouped = service._merge_knowledge_units(units)
    assert len(locally_grouped) == 2
    concept = next(unit for unit in locally_grouped if unit.unit_type == "concept")
    example = next(unit for unit in locally_grouped if unit.unit_type == "example")
    draft = KnowledgeCanonicalizationDraft(
        groups=[
            KnowledgeMergeGroup(
                id="canonical_matrix",
                title="Matrix multiplication",
                unit_ids=[concept.id],
                unit_type="concept",
                confidence=0.94,
            ),
            KnowledgeMergeGroup(
                id="canonical_example",
                title="Matrix multiplication example",
                unit_ids=[example.id],
                unit_type="example",
            ),
        ],
        relations=[
            KnowledgeRelationDraft(
                source_group_id="canonical_example",
                target_group_id="canonical_matrix",
                relation_type="example_of",
                reason="The worked example applies the concept.",
                confidence=0.92,
            )
        ],
    )

    canonical = service._apply_canonicalization(locally_grouped, draft)

    matrix = next(unit for unit in canonical if unit.id == "canonical_matrix")
    worked_example = next(unit for unit in canonical if unit.id == "canonical_example")
    assert matrix.confidence == 0.94
    assert matrix.source_unit_ids == ["raw_001", "raw_002"]
    assert {(ref.material_id, ref.page_no) for ref in matrix.page_refs} == {
        ("mat_001", 1),
        ("mat_002", 3),
    }
    assert worked_example.relations[0].target_unit_id == "canonical_matrix"
    assert worked_example.relations[0].relation_type == "example_of"


def test_knowledge_tree_fallback_keeps_teaching_topics_at_usable_granularity() -> None:
    service = ContentService(Mock(), Mock(), Mock())
    units = []
    for index in range(1, 8):
        source_ref = SourceRef(
            material_id="mat_001",
            page_id=f"page_{index:03d}",
            page_no=index,
        )
        units.append(
            KnowledgeUnit(
                id=f"ku_{index:03d}",
                title=f"Concept {index}",
                summary=f"Summary for concept {index}.",
                keywords=[f"concept-{index}"],
                source_refs=[source_ref],
                page_refs=[PageRef(material_id="mat_001", page_no=index)],
            )
        )

    tree = service._fallback_course_knowledge_tree("tree_001", "Course", units)
    sections = service._sections_from_knowledge_tree(tree, units)

    assert len(tree.root_node_ids) == 1
    assert len(sections) == 3
    assert [
        sum(
            len(next(node for node in tree.nodes if node.id == node_id).knowledge_unit_ids)
            for node_id in section.tree_node_ids
        )
        for section in sections
    ] == [3, 3, 1]

    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        material_ids=["mat_001"],
        title="Course",
        knowledge_units=units,
        knowledge_tree=tree,
        sections=sections,
    )
    quality = service._assess_content_quality(content, expected_material_ids=["mat_001"])

    assert quality["recommended_min_section_count"] == 3
    assert quality["overloaded_section_ids"] == []
    assert "LearningContent may be over-compressed" not in " ".join(quality["warnings"])


def test_incomplete_llm_course_tree_is_completed_with_supplementary_nodes() -> None:
    service = ContentService(Mock(), Mock(), Mock())
    units = [
        KnowledgeUnit(id="ku_001", title="Concept 1", summary="First."),
        KnowledgeUnit(id="ku_002", title="Concept 2", summary="Second."),
    ]
    tree = CourseKnowledgeTree(
        id="tree_001",
        title="Course",
        nodes=[
            CourseKnowledgeTreeNode(
                id="topic_001",
                title="Concept 1",
                knowledge_unit_ids=["ku_001"],
            )
        ],
        root_node_ids=["topic_001"],
    )

    completed = service._complete_course_knowledge_tree(tree, units)

    ContentService._validate_course_knowledge_tree(completed, units)
    assigned = {
        unit_id for node in completed.nodes for unit_id in node.knowledge_unit_ids
    }
    assert assigned == {"ku_001", "ku_002"}
    assert any("supplementary" in node.id for node in completed.nodes)


def test_learning_content_draft_normalizes_common_llm_shape_errors() -> None:
    draft = LearningContentDraft.model_validate(
        {
            "title": "Object Detection",
            "outline": [{"section_title": "YOLO"}],
            "audience": "computer vision students",
            "teaching_intent": "explain detectors",
            "material_overview": "object detection slides",
            "global_concepts": ["YOLO predicts boxes in one pass"],
            "generation_guidance": "focus on misconceptions",
            "quality": "high",
            "sections": [
                {
                    "title": "YOLO",
                    "page_nos": [1],
                    "summary": "YOLO is a one-stage detector.",
                    "teaching_script": "Explain grid prediction.",
                    "visual_opportunities": [
                        {"description": "Show grid layout.", "priority": 1}
                    ],
                    "quiz_items": [
                        {
                            "question": "Which option describes YOLO?",
                            "options": ["Two-stage", "One-stage"],
                            "correct_answer": "B",
                        }
                    ],
                }
            ],
        }
    )

    assert draft.outline == ["YOLO"]
    assert draft.audience == {"description": "computer vision students"}
    assert draft.global_concepts[0].name == "YOLO predicts boxes in one pass"
    assert draft.quality == {"rating": "high"}
    assert draft.sections[0].visual_opportunities[0].priority == "high"
    quiz = draft.sections[0].quiz_items[0]
    assert quiz.correct_index == 1
    assert quiz.explanation == ""
    assert quiz.knowledge_point == "Which option describes YOLO?"


def test_collection_organizer_repairs_empty_sections_with_page_quizzes() -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
    )
    page = PageMetadata(
        id="mat_001_page_001",
        material_id="mat_001",
        page_no=1,
        title="Spatial autocorrelation",
        raw_text="Moran's I measures spatial autocorrelation.",
        image_path="page.png",
        source_refs=[source_ref],
    )
    understanding = PageUnderstanding(
        id="understanding_001",
        material_id="mat_001",
        page_id=page.id,
        page_no=1,
        title=page.title,
        summary="Moran's I measures spatial autocorrelation.",
        knowledge_points=["Moran's I"],
        quiz_items=[
            QuizItemDraft(
                question="What does Moran's I measure?",
                options=["Spatial autocorrelation", "Map scale"],
                correct_index=0,
                explanation="Moran's I is a spatial autocorrelation statistic.",
                knowledge_point="Moran's I",
            )
        ],
        source_refs=[source_ref],
        provider="test",
    )
    unit = KnowledgeUnit(
        id="ku_001",
        title="Moran's I",
        unit_type="method",
        summary="Moran's I measures spatial autocorrelation.",
        keywords=["Moran's I", "spatial autocorrelation"],
        source_refs=[source_ref],
        page_refs=[PageRef(material_id="mat_001", page_no=1)],
    )
    tree = CourseKnowledgeTree(
        id="tree_001",
        title="Spatial Correlation",
        nodes=[
            CourseKnowledgeTreeNode(
                id="node_001",
                title="Moran's I",
                role="method",
                summary="Moran's I measures spatial autocorrelation.",
                knowledge_unit_ids=["ku_001"],
                order=1,
            )
        ],
        root_node_ids=["node_001"],
    )
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.return_value = '{"title":"Broken","sections":[]}'
    provider = LLMLearningProvider(llm)

    draft = provider.organize_collection_learning_content(
        collection_id="single_mat_001",
        material_ids=["mat_001"],
        pages=[page],
        understandings=[understanding],
        knowledge_units=[unit],
        knowledge_tree=tree,
    )

    assert draft.sections
    assert draft.sections[0].tree_node_ids == ["node_001"]
    assert draft.sections[0].quiz_items[0].question == "What does Moran's I measure?"


def test_fallback_tree_sections_reuse_page_understanding_quizzes() -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
    )
    understanding = PageUnderstanding(
        id="understanding_001",
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
        summary="Moran's I measures spatial autocorrelation.",
        knowledge_points=["Moran's I"],
        quiz_items=[
            QuizItemDraft(
                question="Which result indicates positive spatial autocorrelation?",
                options=["Similar values cluster together", "All values are independent"],
                correct_index=0,
                explanation="Positive spatial autocorrelation means nearby values are similar.",
                knowledge_point="spatial autocorrelation",
            )
        ],
        source_refs=[source_ref],
        provider="test",
    )
    unit = KnowledgeUnit(
        id="ku_001",
        title="Moran's I",
        unit_type="method",
        summary="Moran's I measures spatial autocorrelation.",
        keywords=["Moran's I"],
        source_refs=[source_ref],
        page_refs=[PageRef(material_id="mat_001", page_no=1)],
    )
    tree = CourseKnowledgeTree(
        id="tree_001",
        title="Spatial Correlation",
        nodes=[
            CourseKnowledgeTreeNode(
                id="node_001",
                title="Moran's I",
                role="method",
                summary="Moran's I measures spatial autocorrelation.",
                knowledge_unit_ids=["ku_001"],
                order=1,
            )
        ],
        root_node_ids=["node_001"],
    )
    service = ContentService(Mock(), Mock(), FakeLearningProvider())

    sections = service._sections_from_knowledge_tree(
        tree,
        [unit],
        understandings=[understanding],
    )

    assert sections[0].quiz_items[0].question == (
        "Which result indicates positive spatial autocorrelation?"
    )


def test_collection_learning_content_reports_stage_progress() -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
    )
    page = PageMetadata(
        id="mat_001_page_001",
        material_id="mat_001",
        page_no=1,
        title="Matrix multiplication",
        raw_text="Rows are multiplied by columns.",
        image_path="page.png",
        source_refs=[source_ref],
    )
    repository = Mock()
    repository.get.return_value = None
    repository.list_understandings.return_value = []
    materials = Mock()
    materials.get_collection.return_value = SimpleNamespace(
        material_ids=["mat_001"],
        primary_material_id="mat_001",
    )
    materials.get.return_value = SimpleNamespace(status="parsed")
    materials.pages.return_value = [page]
    service = ContentService(repository, materials, FakeLearningProvider())
    updates: list[tuple[int, str]] = []

    content = service.build_collection(
        "col_001",
        progress_callback=lambda progress, step, _message: updates.append((progress, step)),
    )

    assert content.knowledge_units
    assert [progress for progress, _step in updates] == sorted(
        progress for progress, _step in updates
    )
    steps = {step for _progress, step in updates}
    assert {
        "preparing",
        "understanding_pages",
        "extracting_knowledge_units",
        "canonicalizing",
        "organizing",
        "saving",
    } <= steps


def test_llm_learning_provider_parses_knowledge_canonicalization() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.return_value = """
    {
      "groups": [
        {
          "id": "canonical_matrix",
          "title": "Matrix multiplication",
          "unit_ids": ["ku_001", "ku_002"],
          "unit_type": "concept",
          "summary": "Multiply rows by columns.",
          "aliases": ["row-column multiplication"],
          "confidence": 0.95
        }
      ],
      "relations": []
    }
    """
    units = [
        KnowledgeUnit(id="ku_001", title="Matrix multiplication"),
        KnowledgeUnit(id="ku_002", title="Row-column multiplication"),
    ]

    draft = LLMLearningProvider(llm).canonicalize_knowledge_units(units)

    assert draft.groups[0].unit_ids == ["ku_001", "ku_002"]
    assert draft.groups[0].confidence == 0.95


def test_llm_learning_provider_parses_course_knowledge_tree() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.return_value = """
    {
      "id": "tree_001",
      "title": "Matrix Course",
      "nodes": [
        {
          "id": "chapter_001",
          "title": "Matrix Foundations",
          "role": "concept",
          "summary": "Core matrix concepts.",
          "parent_id": null,
          "knowledge_unit_ids": ["ku_001"],
          "order": 1,
          "prerequisite_node_ids": []
        }
      ],
      "root_node_ids": ["chapter_001"],
      "teaching_sequence": ["chapter_001"],
      "orphan_unit_ids": [],
      "warnings": []
    }
    """
    units = [KnowledgeUnit(id="ku_001", title="Matrix multiplication")]

    tree = LLMLearningProvider(llm).build_course_knowledge_tree(
        tree_id="tree_001",
        title="Matrix Course",
        units=units,
    )

    assert isinstance(tree, CourseKnowledgeTree)
    assert tree.root_node_ids == ["chapter_001"]
    assert tree.nodes[0].knowledge_unit_ids == ["ku_001"]


def test_visual_opportunity_hydration_only_allows_embedded_image_paths() -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
        image_path="page-render.png",
    )

    hydrated = ContentService._hydrate_visual_opportunities(
        [
            VisualOpportunity(
                description="Use the real figure.",
                image_path="embedded-figure.png",
                source_refs=[source_ref],
            ),
            VisualOpportunity(
                description="Do not use a full-page render.",
                image_path="page-render.png",
                source_refs=[source_ref],
            ),
        ],
        [source_ref],
        {"embedded-figure.png"},
    )

    assert hydrated[0].image_path == "embedded-figure.png"
    assert hydrated[1].image_path is None


def test_llm_learning_provider_parses_page_understanding() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.return_value = """
    {
      "summary": "Matrix multiplication combines rows and columns.",
      "knowledge_points": ["matrix multiplication", "row by column"],
      "teaching_focus": ["shape compatibility"],
      "possible_questions": ["Why must dimensions match?"],
      "key_excerpts": [
        {
          "text": "Rows are multiplied by columns.",
          "type": "claim",
          "reason": "It states the core operation.",
          "Color": ""
        }
      ],
      "formulas": [
        {
          "latex": "w = u v cos(theta)",
          "meaning": "Example formula",
          "variables": ["u", "v", "θ"]
        }
      ],
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
    assert draft.key_excerpts[0].text == "Rows are multiplied by columns."
    assert draft.formulas[0].variables == [
        {"symbol": "u", "meaning": ""},
        {"symbol": "v", "meaning": ""},
        {"symbol": "θ", "meaning": ""},
    ]
    assert draft.quiz_items[0].question == "What must be true before multiplying two matrices?"
    llm.complete_json.assert_called_once()


def test_llm_learning_provider_describes_visual_and_organizes_content(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    embedded_image = tmp_path / "figure.png"
    image.write_bytes(b"fake image payload")
    embedded_image.write_bytes(b"fake embedded image payload")
    page = PageMetadata(
        id="mat_001_page_001",
        material_id="mat_001",
        page_no=1,
        title="Matrix Multiplication",
        raw_text="Rows by columns.",
        image_path=str(image),
        embedded_images=[
            PageImage(
                id="mat_001_page_001_image_01",
                material_id="mat_001",
                page_id="mat_001_page_001",
                page_no=1,
                image_path=str(embedded_image),
                width=80,
                height=40,
                description="A matrix diagram image.",
            )
        ],
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
    llm.complete_image_json.return_value = """
    ```json
    {"visual_description": "A matrix diagram."}
    ```
    """
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
              "visual_opportunities": [
                {
                  "type": "source_image",
                  "description": "Use the matrix diagram image.",
                  "image_path": "%s",
                  "image_description": "A matrix diagram image.",
                  "usage_hint": "Place beside the row-column explanation.",
                  "priority": "high"
                }
              ],
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
        """
        % str(embedded_image).replace("\\", "\\\\"),
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
    assert draft.sections[0].visual_opportunities[0].image_path == str(embedded_image)
    organizer_payload = json.loads(llm.complete_json.call_args_list[1].args[0][1].content)
    assert organizer_payload["pages"][0]["visual_candidates"][0]["image_path"] == str(
        embedded_image
    )


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
