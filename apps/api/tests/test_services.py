import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from metaclass.infrastructure.providers.fake import FakeLearningProvider
from metaclass.infrastructure.providers.llm import (
    GeminiVisionProvider,
    LLMMessage,
    OpenAICompatibleLLMProvider,
)
from metaclass.infrastructure.providers.learning import LLMLearningProvider
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    KnowledgeCanonicalizationDraft,
    KnowledgeMergeGroup,
    KnowledgeRelation,
    KnowledgeRelationDraft,
    KnowledgeUnit,
    LearningContent,
    LearningContentDraft,
    LearningSection,
    PageUnderstanding,
    PageUnderstandingDraft,
    PageRef,
    QuizItemDraft,
    SourceDeckLearningContentDraft,
    SourceDeckOutlineDraft,
    SourceDeckTeachingStructureDraft,
    TeachingSegment,
    VisualOpportunity,
)
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.schemas import Material, PageImage, PageMetadata, SourceRef
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.video.service import VideoService


def test_page_understanding_is_saved_per_page_and_resumes_after_failure(
    tmp_path: Path,
) -> None:
    material_id = "mat_resume"
    pages = [
        PageMetadata(
            id=f"page_{page_no}",
            material_id=material_id,
            page_no=page_no,
            title=f"Page {page_no}",
            raw_text=f"Content {page_no}",
            image_path=str(tmp_path / f"{page_no}.png"),
            source_refs=[
                SourceRef(
                    material_id=material_id,
                    page_id=f"page_{page_no}",
                    page_no=page_no,
                )
            ],
        )
        for page_no in range(1, 4)
    ]
    material = Material(
        id=material_id,
        filename="resume.pdf",
        file_type="pdf",
        file_hash="resume-hash",
        status="parsed",
        storage_path=str(tmp_path / "resume.pdf"),
        page_count=3,
    )
    materials = SimpleNamespace(
        data_dir=tmp_path,
        get=lambda _: material,
        pages=lambda _: pages,
    )
    stored: dict[str, PageUnderstanding] = {}
    repository = Mock()
    repository.list_understandings.side_effect = lambda _: sorted(
        stored.values(), key=lambda item: item.page_no
    )
    repository.save_understandings.side_effect = lambda items: stored.update(
        {item.page_id: item for item in items}
    )
    calls: list[int] = []

    def understand_page(title: str, raw_text: str, page_no: int) -> PageUnderstandingDraft:
        calls.append(page_no)
        if page_no == 2 and calls.count(2) == 1:
            raise RuntimeError("temporary model failure")
        return PageUnderstandingDraft(
            summary=f"Summary {page_no}", knowledge_points=[f"Point {page_no}"]
        )

    provider = SimpleNamespace(
        name="test",
        model="test-model",
        prompt_version="resume-v1",
        understand_page=understand_page,
    )
    service = ContentService(repository, materials, provider)

    with pytest.raises(RuntimeError, match="temporary model failure"):
        service.understand_pages(material_id)
    assert list(stored) == ["page_1"]

    result = service.understand_pages(material_id)

    assert [item.page_no for item in result] == [1, 2, 3]
    assert calls == [1, 2, 2, 3]
    assert repository.save_understandings.call_count == 3


def test_content_job_state_survives_restart_and_pauses_running_job(
    tmp_path: Path,
) -> None:
    material = Material(
        id="mat_job",
        filename="job.pdf",
        file_type="pdf",
        status="parsed",
        storage_path=str(tmp_path / "job.pdf"),
    )
    materials = SimpleNamespace(data_dir=tmp_path, get=lambda _: material)
    first = ContentService(Mock(), materials, Mock())
    job = first.create_source_deck_generation_job(material.id)
    job.status = "running"
    job.step = "understanding_pages"
    first._save_job(job)

    restored = ContentService(Mock(), materials, Mock()).get_generation_job(job.id)

    assert restored.status == "paused"
    assert restored.step == "paused"
    assert restored.error is None


def test_learning_content_build_reuses_existing_material_version() -> None:
    existing = LearningContent(
        id="content_001",
        material_id="mat_001",
        material_ids=["mat_001"],
        title="Reusable course content",
        sections=[
            LearningSection(
                id="section_001",
                title="Existing section",
                summary="Already generated and saved.",
                source_refs=[
                    SourceRef(
                        material_id="mat_001",
                        page_id="page_001",
                        page_no=1,
                    )
                ],
            )
        ],
    )
    repository = Mock()
    repository.get_for_material_version.return_value = existing
    materials = Mock()
    provider = Mock()
    updates: list[tuple[int, str, str]] = []
    service = ContentService(repository, materials, provider)

    result = service.build(
        "mat_001",
        progress_callback=lambda progress, step, message: updates.append((progress, step, message)),
    )

    assert result is existing
    repository.get_for_material_version.assert_called_once_with("mat_001", 1)
    materials.pages.assert_not_called()
    assert updates == [(100, "completed", "Existing LearningContent reused")]


def test_failed_content_job_can_resume_with_same_identity(tmp_path: Path) -> None:
    material = Material(
        id="mat_failed_job",
        filename="failed.pdf",
        file_type="pdf",
        status="parsed",
        storage_path=str(tmp_path / "failed.pdf"),
    )
    materials = SimpleNamespace(data_dir=tmp_path, get=lambda _: material)
    service = ContentService(Mock(), materials, Mock())
    failed = service.create_source_deck_generation_job(material.id)
    failed.status = "failed"
    failed.step = "failed"
    failed.progress = 100
    failed.error = "temporary validation failure"
    service._save_job(failed)

    resumed = service.resume_generation_job(failed.id)

    assert resumed.id == failed.id
    assert resumed.status == "queued"
    assert resumed.step == "queued"
    assert resumed.error is None
    assert resumed.message == "等待从 checkpoint 继续"


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


def test_knowledge_tree_fallback_groups_units_semantically_without_a_fixed_limit() -> None:
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
    assert len(sections) == 1
    assert [
        sum(
            len(next(node for node in tree.nodes if node.id == node_id).knowledge_unit_ids)
            for node_id in section.tree_node_ids
        )
        for section in sections
    ] == [7]

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

    assert quality["recommended_min_section_count"] == 1
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
    assigned = {unit_id for node in completed.nodes for unit_id in node.knowledge_unit_ids}
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
                    "visual_opportunities": [{"description": "Show grid layout.", "priority": 1}],
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


def test_source_deck_draft_is_lightweight_and_hydrated_from_page_understanding() -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
    )
    draft = SourceDeckLearningContentDraft.model_validate(
        {
            "title": "空间分析",
            "objectives": ["理解空间自相关"],
            "structure_summary": "先介绍概念，再解释指标。",
            "detected_agenda": ["空间自相关"],
            "page_flow": [
                {
                    "page_no": 1,
                    "page_role": "concept",
                    "chapter_title": "空间自相关",
                    "content_summary": "介绍 Moran's I。",
                    "teaching_purpose": "建立核心概念。",
                }
            ],
            "sections": [
                {
                    "title": "空间自相关",
                    "content_goal": "理解 Moran's I 的作用",
                    "page_refs": [{"material_id": "mat_001", "page_no": 1, "reason": "概念页"}],
                    "summary": "介绍空间自相关。",
                    "key_points": ["Moran's I"],
                    "teaching_approach": "先从相邻区域是否相似切入。",
                }
            ],
        }
    )
    understanding = PageUnderstanding(
        id="understanding_001",
        material_id="mat_001",
        page_id="mat_001_page_001",
        page_no=1,
        page_role="concept",
        summary="Moran's I 衡量空间自相关。",
        knowledge_points=["Moran's I"],
        key_excerpts=[{"text": "Moran's I", "type": "definition"}],
        formulas=[{"latex": "I = n/W"}],
        misconceptions=[{"mistake": "只看数值大小", "correction": "结合期望值解释"}],
        source_refs=[source_ref],
        provider="test",
    )

    hydrated = ContentService._hydrate_source_deck_draft(
        draft,
        understandings=[understanding],
        knowledge_units=[],
    )

    assert hydrated.sections[0].teaching_script == ""
    assert hydrated.sections[0].visual_opportunities == []
    assert hydrated.sections[0].tree_node_ids == []
    assert hydrated.sections[0].source_excerpts[0].text == "Moran's I"
    assert hydrated.sections[0].formulas[0].latex == "I = n/W"
    assert hydrated.sections[0].misconceptions[0].mistake == "只看数值大小"
    assert hydrated.material_overview["page_flow"][0]["page_no"] == 1


def test_source_deck_structure_uses_outline_then_batched_page_flow() -> None:
    pages = []
    understandings = []
    for page_no in range(1, 14):
        source_ref = SourceRef(
            material_id="mat_001",
            page_id=f"mat_001_page_{page_no:03d}",
            page_no=page_no,
        )
        pages.append(
            PageMetadata(
                id=source_ref.page_id,
                material_id="mat_001",
                page_no=page_no,
                title=f"第 {page_no} 页",
                raw_text=f"第 {page_no} 页正文",
                image_path=f"page-{page_no}.png",
                source_refs=[source_ref],
            )
        )
        understandings.append(
            PageUnderstanding(
                id=f"understanding_{page_no:03d}",
                material_id="mat_001",
                page_id=source_ref.page_id,
                page_no=page_no,
                page_role="concept",
                title=f"第 {page_no} 页",
                summary=f"第 {page_no} 页摘要",
                knowledge_points=[f"知识点 {page_no}"],
                source_refs=[source_ref],
                provider="test",
            )
        )

    outline = {
        "title": "长课件",
        "structure_summary": "一个连续章节",
        "detected_agenda": ["第一章"],
        "sections": [
            {
                "title": "第一章",
                "page_refs": [
                    {"material_id": "mat_001", "page_no": page_no} for page_no in range(1, 14)
                ],
            }
        ],
    }

    def flow_payload(start: int, end: int) -> str:
        return json.dumps(
            {
                "page_flow": [
                    {
                        "page_no": page_no,
                        "page_role": "concept",
                        "chapter_title": "第一章",
                        "content_summary": f"第 {page_no} 页摘要",
                        "teaching_purpose": "解释当前知识点",
                        "logic_from_previous": "承接前页" if page_no > 1 else "",
                        "leads_to_next": "引出后页" if page_no < 13 else "",
                    }
                    for page_no in range(start, end + 1)
                ]
            },
            ensure_ascii=False,
        )

    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.side_effect = [
        json.dumps(outline, ensure_ascii=False),
        flow_payload(1, 12),
        flow_payload(13, 13),
    ]

    draft = LLMLearningProvider(llm).organize_source_deck_learning_content(
        material_id="mat_001",
        pages=pages,
        understandings=understandings,
        knowledge_units=[],
    )

    assert llm.complete_json.call_count == 3
    assert [item.page_no for item in draft.page_flow] == list(range(1, 14))
    assert [ref.page_no for ref in draft.sections[0].page_refs] == list(range(1, 14))


def test_source_deck_outline_retries_once_when_page_coverage_is_invalid() -> None:
    pages = [
        PageMetadata(
            id=f"page_{page_no:03d}",
            material_id="mat_001",
            page_no=page_no,
            title=f"页面 {page_no}",
            raw_text=f"内容 {page_no}",
            image_path=f"page-{page_no}.png",
            source_refs=[
                SourceRef(
                    material_id="mat_001",
                    page_id=f"page_{page_no:03d}",
                    page_no=page_no,
                )
            ],
        )
        for page_no in (1, 2)
    ]
    invalid_outline = json.dumps(
        {
            "title": "课程",
            "sections": [
                {
                    "title": "第一章",
                    "page_refs": [{"material_id": "mat_001", "page_no": 1}],
                }
            ],
        }
    )
    valid_outline = json.dumps(
        {
            "title": "课程",
            "sections": [
                {
                    "title": "第一章",
                    "page_refs": [
                        {"material_id": "mat_001", "page_no": page_no} for page_no in (1, 2)
                    ],
                }
            ],
        }
    )
    flow = json.dumps(
        {
            "page_flow": [
                {
                    "page_no": page_no,
                    "page_role": "concept",
                    "chapter_title": "第一章",
                }
                for page_no in (1, 2)
            ]
        }
    )
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.side_effect = [invalid_outline, valid_outline, flow]

    draft = LLMLearningProvider(llm).organize_source_deck_learning_content(
        material_id="mat_001",
        pages=pages,
        understandings=[],
        knowledge_units=[],
    )

    assert llm.complete_json.call_count == 3
    repair_message = llm.complete_json.call_args_list[1].args[0][-1].content
    assert "Expected flattened page_nos exactly [1, 2]" in repair_message
    assert "Missing pages: [2]" in repair_message
    assert [ref.page_no for ref in draft.sections[0].page_refs] == [1, 2]


def test_source_deck_outline_prompt_and_payload_require_exact_page_enumeration() -> None:
    prompt_path = (
        Path(__file__).parents[1]
        / "src"
        / "metaclass"
        / "modules"
        / "content"
        / "source_deck_learning_content_prompt.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "逐页显式输出" in prompt
    assert "flattened_page_nos" in prompt
    assert "逐项完全相等" in prompt
    assert "不得因为页面是封面" in prompt

    page = PageMetadata(
        id="page_001",
        material_id="mat_001",
        page_no=1,
        title="封面",
        raw_text="课程封面",
        image_path="page-1.png",
        source_refs=[SourceRef(material_id="mat_001", page_id="page_001", page_no=1)],
    )
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.side_effect = [
        json.dumps(
            {
                "title": "课程",
                "sections": [
                    {
                        "title": "导入",
                        "page_refs": [{"material_id": "mat_001", "page_no": 1}],
                    }
                ],
            }
        ),
        json.dumps(
            {
                "page_flow": [
                    {
                        "page_no": 1,
                        "page_role": "cover",
                        "chapter_title": "导入",
                    }
                ]
            }
        ),
    ]

    LLMLearningProvider(llm).organize_source_deck_learning_content(
        material_id="mat_001",
        pages=[page],
        understandings=[],
        knowledge_units=[],
    )

    payload = json.loads(llm.complete_json.call_args_list[0].args[0][1].content)
    assert payload["page_count"] == 1
    assert payload["first_page_no"] == 1
    assert payload["last_page_no"] == 1
    assert payload["expected_page_nos"] == [1]


def test_source_deck_outline_repair_preserves_titles_and_fills_contiguous_ranges() -> None:
    outline = SourceDeckOutlineDraft.model_validate(
        {
            "title": "时间序列",
            "sections": [
                {
                    "title": "基础",
                    "page_refs": [
                        {"material_id": "mat_001", "page_no": 1},
                        {"material_id": "mat_001", "page_no": 3},
                    ],
                },
                {
                    "title": "方法",
                    "page_refs": [
                        {"material_id": "mat_001", "page_no": 5},
                        {"material_id": "mat_001", "page_no": 7},
                    ],
                },
            ],
        }
    )

    repaired = LLMLearningProvider._repair_source_deck_outline(
        outline,
        material_id="mat_001",
        expected_pages=list(range(1, 9)),
    )

    assert [section.title for section in repaired.sections] == ["基础", "方法"]
    assert [[ref.page_no for ref in section.page_refs] for section in repaired.sections] == [
        list(range(1, 5)),
        list(range(5, 9)),
    ]


def test_source_deck_drafts_normalize_nullable_optional_text() -> None:
    outline = SourceDeckOutlineDraft.model_validate(
        {
            "title": "时间序列",
            "subtitle": None,
            "structure_summary": None,
            "sections": [
                {
                    "title": "预测方法",
                    "content_goal": None,
                    "summary": None,
                    "teaching_approach": None,
                    "transition_to_next": None,
                    "page_refs": [{"material_id": "mat_001", "page_no": 1}],
                }
            ],
        }
    )

    assert outline.subtitle == ""
    assert outline.structure_summary == ""
    assert outline.sections[0].content_goal == ""
    assert outline.sections[0].summary == ""
    assert outline.sections[0].teaching_approach == ""
    assert outline.sections[0].transition_to_next == ""


def test_source_deck_teaching_segments_retry_then_store_valid_result() -> None:
    source = SourceDeckLearningContentDraft.model_validate(
        {
            "title": "时间序列",
            "page_flow": [
                {
                    "page_no": page_no,
                    "page_role": "concept",
                    "chapter_title": "趋势分析",
                    "content_summary": f"第 {page_no} 页",
                }
                for page_no in range(1, 7)
            ],
            "sections": [
                {
                    "title": "趋势分析",
                    "page_refs": [
                        {"material_id": "mat_001", "page_no": page_no} for page_no in range(1, 7)
                    ],
                }
            ],
        }
    )
    invalid = SourceDeckTeachingStructureDraft.model_validate(
        {
            "section_title": "趋势分析",
            "segments": [
                {
                    "title": "趋势定义",
                    "teaching_goal": "理解趋势",
                    "start_page": 1,
                    "end_page": 2,
                }
            ],
        }
    )
    valid = SourceDeckTeachingStructureDraft.model_validate(
        {
            "section_title": "趋势分析",
            "segments": [
                {
                    "title": "趋势的直观含义",
                    "teaching_goal": "建立直觉",
                    "start_page": 1,
                    "end_page": 3,
                    "knowledge_unit_ids": ["ku_trend"],
                },
                {
                    "title": "趋势的估计方法",
                    "role": "method",
                    "teaching_goal": "掌握估计方法",
                    "start_page": 4,
                    "end_page": 6,
                    "prerequisite_segment_titles": ["趋势的直观含义"],
                },
            ],
        }
    )
    provider = Mock()
    provider.plan_source_deck_teaching_segments.side_effect = [invalid, valid]
    service = ContentService(Mock(), Mock(), provider)
    unit = KnowledgeUnit(
        id="ku_trend",
        title="趋势定义",
        page_refs=[PageRef(material_id="mat_001", page_no=2)],
    )

    segments_by_section, warnings = service._build_source_deck_teaching_segments(
        source, knowledge_units=[unit]
    )
    understandings = [
        PageUnderstanding(
            id=f"understanding_{page_no:03d}",
            material_id="mat_001",
            page_id=f"page_{page_no:03d}",
            page_no=page_no,
            summary=f"第 {page_no} 页",
            source_refs=[
                SourceRef(
                    material_id="mat_001",
                    page_id=f"page_{page_no:03d}",
                    page_no=page_no,
                )
            ],
            provider="test",
        )
        for page_no in range(1, 7)
    ]
    hydrated = service._hydrate_source_deck_draft(
        source,
        understandings=understandings,
        knowledge_units=[unit],
        segments_by_section=segments_by_section,
    )

    assert provider.plan_source_deck_teaching_segments.call_count == 2
    assert warnings == []
    assert [segment.title for segment in hydrated.sections[0].segments] == [
        "趋势的直观含义",
        "趋势的估计方法",
    ]
    assert hydrated.sections[0].segments[1].prerequisite_segment_ids == ["segment_001_01"]


def test_source_deck_teaching_segments_reject_unit_from_another_section() -> None:
    source = SourceDeckLearningContentDraft.model_validate(
        {
            "title": "时间序列",
            "page_flow": [
                {
                    "page_no": page_no,
                    "page_role": "concept",
                    "chapter_title": "第一节" if page_no == 1 else "第二节",
                }
                for page_no in (1, 2)
            ],
            "sections": [
                {
                    "title": "第一节",
                    "page_refs": [{"material_id": "mat_001", "page_no": 1}],
                },
                {
                    "title": "第二节",
                    "page_refs": [{"material_id": "mat_001", "page_no": 2}],
                },
            ],
        }
    )
    foreign_unit = KnowledgeUnit(
        id="ku_second",
        title="第二节知识",
        page_refs=[PageRef(material_id="mat_001", page_no=2)],
    )
    provider = Mock()
    provider.plan_source_deck_teaching_segments.return_value = (
        SourceDeckTeachingStructureDraft.model_validate(
            {
                "section_title": "第一节",
                "segments": [
                    {
                        "title": "第一节",
                        "teaching_goal": "讲解第一节",
                        "start_page": 1,
                        "end_page": 1,
                        "knowledge_unit_ids": ["ku_second"],
                    }
                ],
            }
        )
    )
    service = ContentService(Mock(), Mock(), provider)

    segments_by_section, warnings = service._build_source_deck_teaching_segments(
        source, knowledge_units=[foreign_unit]
    )

    assert provider.plan_source_deck_teaching_segments.call_count == 4
    assert len(warnings) == 2
    assert segments_by_section[1][0].knowledge_unit_ids == []
    assert segments_by_section[2][0].knowledge_unit_ids == ["ku_second"]


def test_source_deck_tree_is_deterministic_view_of_sections_segments_and_units() -> None:
    def pages(start: int, end: int) -> list[PageRef]:
        return [
            PageRef(material_id="mat_001", page_no=page_no) for page_no in range(start, end + 1)
        ]

    first_unit = KnowledgeUnit(
        id="ku_definition",
        title="趋势成分的定义",
        summary="区分长期变化与短期波动。",
        page_refs=pages(1, 2),
    )
    second_unit = KnowledgeUnit(
        id="ku_estimation",
        title="长期趋势的形式与估计",
        unit_type="method",
        summary="使用回归形式估计长期趋势。",
        page_refs=pages(3, 4),
        relations=[
            KnowledgeRelation(
                target_unit_id="ku_definition",
                relation_type="builds_on",
            )
        ],
    )
    third_unit = KnowledgeUnit(
        id="ku_seasonal",
        title="季节变动的识别",
        summary="识别固定周期内的重复模式。",
        page_refs=pages(5, 6),
    )
    draft = LearningContentDraft.model_validate(
        {
            "title": "时间序列分析",
            "sections": [
                {
                    "title": "趋势分析",
                    "summary": "先定义趋势，再介绍估计。",
                    "page_refs": [ref.model_dump() for ref in pages(1, 4)],
                    "segments": [
                        TeachingSegment(
                            id="segment_001_01",
                            title="趋势成分的定义",
                            summary="区分长期变化与短期波动。",
                            page_refs=pages(1, 2),
                            knowledge_unit_ids=["ku_definition"],
                            order=1,
                        ),
                        TeachingSegment(
                            id="segment_001_02",
                            title="长期趋势的形式与估计",
                            summary="使用回归形式估计长期趋势。",
                            page_refs=pages(3, 4),
                            knowledge_unit_ids=["ku_estimation"],
                            prerequisite_segment_ids=["segment_001_01"],
                            order=2,
                        ),
                    ],
                },
                {
                    "title": "季节变动",
                    "summary": "认识周期重复模式。",
                    "page_refs": [ref.model_dump() for ref in pages(5, 6)],
                    "segments": [
                        TeachingSegment(
                            id="segment_002_01",
                            title="季节变动的识别",
                            summary="识别固定周期内的重复模式。",
                            page_refs=pages(5, 6),
                            knowledge_unit_ids=["ku_seasonal"],
                            order=1,
                        )
                    ],
                },
            ],
        }
    )

    tree, updated = ContentService._build_source_deck_tree(
        tree_id="tree_source_time_series",
        draft=draft,
        units=[first_unit, second_unit, third_unit],
    )

    section_nodes = [node for node in tree.nodes if node.node_type == "section"]
    segment_nodes = [node for node in tree.nodes if node.node_type == "segment"]
    unit_nodes = [node for node in tree.nodes if node.node_type == "knowledge_unit"]
    assert [node.title for node in section_nodes] == ["趋势分析", "季节变动"]
    assert [node.title for node in segment_nodes] == [
        "趋势成分的定义",
        "长期趋势的形式与估计",
        "季节变动的识别",
    ]
    assert [node.title for node in unit_nodes] == [
        "趋势成分的定义",
        "长期趋势的形式与估计",
        "季节变动的识别",
    ]
    assert {node.ref_id for node in unit_nodes} == {
        "ku_definition",
        "ku_estimation",
        "ku_seasonal",
    }
    assert tree.root_node_ids == [node.id for node in section_nodes]
    assert updated.sections[0].tree_node_ids == [section_nodes[0].id]
    segment_by_ref = {node.ref_id: node for node in segment_nodes}
    assert segment_by_ref["segment_001_02"].prerequisite_node_ids == [
        segment_by_ref["segment_001_01"].id
    ]
    unit_by_ref = {node.ref_id: node for node in unit_nodes}
    assert unit_by_ref["ku_estimation"].prerequisite_node_ids == [unit_by_ref["ku_definition"].id]


def test_source_deck_tree_assigns_unlisted_unit_by_page_containment() -> None:
    segment = TeachingSegment(
        id="segment_001_01",
        title="趋势定义",
        page_refs=[PageRef(material_id="mat_001", page_no=1)],
    )
    draft = LearningContentDraft.model_validate(
        {
            "title": "时间序列",
            "sections": [
                {
                    "title": "趋势分析",
                    "summary": "定义趋势成分。",
                    "page_refs": [{"material_id": "mat_001", "page_no": 1}],
                    "segments": [segment.model_dump()],
                }
            ],
        }
    )
    unit = KnowledgeUnit(
        id="ku_trend",
        title="趋势定义",
        page_refs=[PageRef(material_id="mat_001", page_no=1)],
    )

    tree, _ = ContentService._build_source_deck_tree(
        tree_id="tree_source_time_series", draft=draft, units=[unit]
    )

    unit_node = next(node for node in tree.nodes if node.node_type == "knowledge_unit")
    segment_node = next(node for node in tree.nodes if node.node_type == "segment")
    assert unit_node.parent_id == segment_node.id
    assert unit_node.title == unit.title
    assert unit_node.page_refs == unit.page_refs


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


def test_source_deck_units_exclude_navigation_and_keep_reference_and_practice() -> None:
    service = ContentService(Mock(), Mock(), Mock())

    def understanding(page_no: int, role: str, title: str) -> PageUnderstanding:
        ref = SourceRef(
            material_id="mat_001",
            page_id=f"page_{page_no:03d}",
            page_no=page_no,
        )
        return PageUnderstanding(
            id=f"understanding_{page_no:03d}",
            material_id="mat_001",
            page_id=ref.page_id,
            page_no=page_no,
            page_role=role,
            title=title,
            summary=title,
            knowledge_points=[title],
            source_refs=[ref],
            provider="test",
        )

    units = service._source_deck_knowledge_units_from_understandings(
        [
            understanding(1, "agenda", "课程目录"),
            understanding(2, "concept", "趋势定义"),
            understanding(3, "reference", "参考文献"),
            understanding(4, "exercise", "预测练习"),
        ]
    )

    assert [unit.title for unit in units] == ["趋势定义", "参考文献", "预测练习"]
    assert [unit.unit_type for unit in units] == ["concept", "reference", "practice"]


def test_source_deck_local_merge_requires_contiguous_pages() -> None:
    service = ContentService(Mock(), Mock(), Mock())

    def unit(unit_id: str, page_no: int, title: str) -> KnowledgeUnit:
        return KnowledgeUnit(
            id=unit_id,
            title=title,
            unit_type="concept",
            summary=title,
            keywords=["趋势"],
            page_refs=[PageRef(material_id="mat_001", page_no=page_no)],
        )

    merged = service._merge_source_deck_knowledge_units(
        [
            unit("page_017", 17, "趋势成分"),
            unit("page_018", 18, "趋势成分"),
            unit("page_044", 44, "趋势成分"),
        ]
    )

    assert len(merged) == 2
    assert [ref.page_no for ref in merged[0].page_refs] == [17, 18]
    assert [ref.page_no for ref in merged[1].page_refs] == [44]


def test_source_deck_canonicalization_rejects_non_contiguous_group() -> None:
    service = ContentService(Mock(), Mock(), Mock())
    units = [
        KnowledgeUnit(
            id="trend_definition",
            title="趋势成分的定义",
            page_refs=[PageRef(material_id="mat_001", page_no=17)],
        ),
        KnowledgeUnit(
            id="trend_estimation",
            title="长期趋势的形式与回归估计",
            page_refs=[PageRef(material_id="mat_001", page_no=44)],
        ),
    ]
    invalid = KnowledgeCanonicalizationDraft(
        groups=[
            KnowledgeMergeGroup(
                id="trend_all",
                title="趋势分析",
                unit_ids=["trend_definition", "trend_estimation"],
            )
        ]
    )

    with pytest.raises(ValueError, match="contiguous pages"):
        service._validate_source_deck_canonical_groups(units, invalid)


def test_source_deck_canonicalization_keeps_builds_on_relation() -> None:
    service = ContentService(Mock(), Mock(), Mock())
    units = [
        KnowledgeUnit(
            id="trend_definition",
            title="趋势成分的定义",
            page_refs=[PageRef(material_id="mat_001", page_no=17)],
        ),
        KnowledgeUnit(
            id="trend_estimation",
            title="长期趋势的形式与回归估计",
            unit_type="method",
            page_refs=[PageRef(material_id="mat_001", page_no=44)],
        ),
    ]
    draft = KnowledgeCanonicalizationDraft(
        groups=[
            KnowledgeMergeGroup(
                id="trend_definition_group",
                title="趋势成分的定义",
                unit_ids=["trend_definition"],
            ),
            KnowledgeMergeGroup(
                id="trend_estimation_group",
                title="长期趋势的形式与回归估计",
                unit_ids=["trend_estimation"],
                unit_type="method",
            ),
        ],
        relations=[
            KnowledgeRelationDraft(
                source_group_id="trend_estimation_group",
                target_group_id="trend_definition_group",
                relation_type="builds_on",
                reason="长期趋势估计建立在趋势概念之上。",
            )
        ],
    )

    service._validate_source_deck_canonical_groups(units, draft)
    result = service._apply_canonicalization(units, draft)
    advanced = next(unit for unit in result if unit.id == "trend_estimation_group")

    assert advanced.relations[0].relation_type == "builds_on"
    assert advanced.relations[0].target_unit_id == "trend_definition_group"


def test_source_deck_knowledge_unit_prompt_requires_contiguous_merges() -> None:
    prompt_path = (
        Path(__file__).parents[1]
        / "src"
        / "metaclass"
        / "modules"
        / "content"
        / "source_deck_knowledge_unit_prompt.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "第 17 页" in prompt
    assert "第 44 页" in prompt
    assert "builds_on" in prompt
    assert "不得仅因关键词" in prompt


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


def test_page_understanding_retries_missing_summary_and_normalizes_field_names() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.side_effect = [
        '{"page-role":"x"}',
        '{"page-role":"concept","summary":"趋势是序列的长期变化。"}',
    ]

    draft = LLMLearningProvider(llm).understand_page("趋势分析", "趋势反映时间序列的长期变化。", 1)

    assert llm.complete_json.call_count == 2
    assert draft.page_role == "concept"
    assert draft.summary == "趋势是序列的长期变化。"
    repair_message = llm.complete_json.call_args_list[1].args[0][-1].content
    assert "summary` is required" in repair_message


def test_page_understanding_falls_back_after_two_invalid_responses() -> None:
    llm = Mock()
    llm.model = "test-model"
    llm.complete_json.side_effect = ['{"page-role":"x"}', '{"page_role":"concept"}']

    draft = LLMLearningProvider(llm).understand_page("趋势分析", "趋势反映时间序列的长期变化。", 1)

    assert llm.complete_json.call_count == 2
    assert draft.summary == "趋势反映时间序列的长期变化。"
    assert draft.knowledge_points == ["趋势分析"]


def test_openai_compatible_provider_uses_temperature_one_for_restricted_models() -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="test-key-12345",
        model="gpt-5.5",
    )
    captured = []

    def read(req, label):
        captured.append(json.loads(req.data.decode("utf-8")))
        return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    provider._read_json_with_retry = read

    result = provider.complete_json([LLMMessage(role="user", content="test")], temperature=0.15)

    assert result == '{"ok":true}'
    assert captured[0]["temperature"] == 1.0


@pytest.mark.parametrize(
    "model",
    ["kimi-k3", "moonshotai/kimi-k3-preview", "kimi-k2.5"],
)
def test_openai_compatible_provider_uses_temperature_one_for_kimi_models(model) -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="test-key-12345",
        model=model,
    )
    captured = []

    def read(req, label):
        captured.append(json.loads(req.data.decode("utf-8")))
        return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    provider._read_json_with_retry = read

    provider.complete_json([LLMMessage(role="user", content="test")], temperature=0.2)

    assert captured[0]["temperature"] == 1.0


def test_openai_compatible_provider_retries_temperature_specific_400_once() -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="test-key-12345",
        model="custom-reasoning-model",
    )
    captured = []

    def read(req, label):
        payload = json.loads(req.data.decode("utf-8"))
        captured.append(payload)
        if len(captured) == 1:
            raise RuntimeError(
                'LLM request failed: HTTP 400 {"error":{"message":'
                '"invalid temperature: only 1 is allowed for this model"}}'
            )
        return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    provider._read_json_with_retry = read

    result = provider.complete_json([LLMMessage(role="user", content="test")], temperature=0.2)

    assert result == '{"ok":true}'
    assert [payload["temperature"] for payload in captured] == [0.2, 1.0]

    provider.complete_json([LLMMessage(role="user", content="again")], temperature=0.3)

    assert captured[-1]["temperature"] == 1.0


def test_openai_compatible_provider_retries_remote_disconnect(monkeypatch) -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="test-key-12345",
        model="custom-model",
    )
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def open_request(*_, **__):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError("remote closed connection")
        return Response()

    monkeypatch.setattr("metaclass.infrastructure.providers.llm.request.urlopen", open_request)

    result = provider.complete_json([LLMMessage(role="user", content="test")])

    assert result == '{"ok":true}'
    assert calls == 2


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
