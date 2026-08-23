from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, text

from metaclass.infrastructure.database import Database
from metaclass.modules.content.repository import SqlAlchemyContentRepository
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    CourseKnowledgeTreeNode,
    LearningContent,
    LearningSection,
    PageRef,
    TeachingSegment,
)
from metaclass.modules.materials.models import MaterialRecord
from metaclass.modules.materials.repository import SqlAlchemyMaterialRepository
from metaclass.modules.materials.schemas import (
    Material,
    MaterialCollection,
    PageMetadata,
    SourceRef,
)
from metaclass.modules.presentation.schemas import (
    PPTArtifact,
    PPTGenerationJob,
    PresentationPlan,
    PresentationResource,
    PresentationSlideResource,
    SlidePlan,
)


def test_sqlalchemy_creates_domain_tables(tmp_path) -> None:
    from metaclass.core.application import build_services

    services = build_services(tmp_path)
    tables = set(inspect(services.database.engine).get_table_names())
    assert tables == {
        "materials",
        "material_collections",
        "page_metadata",
        "page_understandings",
        "learning_contents",
        "presentation_plans",
        "presentation_resources",
        "ppt_generation_jobs",
        "ppt_artifacts",
        "classroom_plans",
        "classroom_plan_generation_meta",
        "classroom_plan_jobs",
        "classroom_sessions",
        "classroom_requests",
            "classroom_qa_items",
        "video_jobs",
        "video_artifacts",
        "tts_artifacts",
    }
    assert "records" not in tables
    assert {
        item["name"]
        for item in inspect(services.database.engine).get_unique_constraints("page_metadata")
    } == {"uq_page_material_page_no"}
    assert {
        item["name"]
        for item in inspect(services.database.engine).get_unique_constraints("learning_contents")
    } == {"uq_learning_content_material_version"}
    assert {
        item["name"]
        for item in inspect(services.database.engine).get_check_constraints("video_jobs")
    } == {"ck_video_progress_range"}
    services.database.dispose()


def test_build_services_can_skip_schema_creation(tmp_path) -> None:
    from metaclass.core.application import build_services

    services = build_services(tmp_path, create_schema=False)
    assert inspect(services.database.engine).get_table_names() == []
    services.database.dispose()


def test_sqlite_path_is_resolved_to_absolute_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    database = Database.from_sqlite_path(Path("relative/repository.db"))

    assert Path(database.engine.url.database or "").is_absolute()
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    database.dispose()


def test_database_session_rolls_back_on_error(tmp_path) -> None:
    database = Database.from_sqlite_path(tmp_path / "rollback.db")
    database.create_schema()
    record = MaterialRecord(
        id="mat_rollback",
        filename="rollback.pdf",
        file_type="pdf",
        file_hash=None,
        status="uploaded",
        storage_path="data/raw/rollback.pdf",
        page_count=0,
        error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    with pytest.raises(RuntimeError, match="force rollback"):
        with database.session() as session:
            session.add(record)
            session.flush()
            raise RuntimeError("force rollback")

    with database.session() as session:
        assert session.get(MaterialRecord, record.id) is None
    database.dispose()


def test_schema_upgrade_clears_invalid_legacy_knowledge_tree(tmp_path) -> None:
    database = Database.from_sqlite_path(tmp_path / "legacy-tree.db")
    from metaclass.modules.content import models as _content_models  # noqa: F401

    database.create_schema()
    material = Material(
        id="mat_tree",
        filename="tree.pdf",
        file_type="pdf",
        status="parsed",
        storage_path="data/raw/tree.pdf",
        page_count=1,
        created_at=datetime.now(timezone.utc),
    )
    source_ref = SourceRef(
        material_id=material.id,
        page_id="page_tree",
        page_no=1,
    )
    SqlAlchemyMaterialRepository(database).save_material(material)
    repository = SqlAlchemyContentRepository(database)
    repository.save(
        LearningContent(
            id="content_tree",
            material_id=material.id,
            title="Tree content",
            sections=[
                LearningSection(
                    id="section_tree",
                    title="Tree section",
                    summary="Summary",
                    source_refs=[source_ref],
                )
            ],
        )
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE learning_contents SET knowledge_tree = "
                "'2026-07-14 13:53:36' WHERE id = 'content_tree'"
            )
        )

    database.create_schema()

    assert repository.get("content_tree").knowledge_tree is None
    database.dispose()


def test_material_repository_round_trip(tmp_path) -> None:
    database = Database.from_sqlite_path(tmp_path / "repository.db")
    from metaclass.modules.materials import models as _models  # noqa: F401

    database.create_schema()
    repository = SqlAlchemyMaterialRepository(database)
    material = Material(
        id="mat_001",
        filename="demo.pdf",
        file_type="pdf",
        file_hash="a" * 64,
        status="parsed",
        storage_path="data/raw/mat_001/source.pdf",
        page_count=1,
        created_at=datetime.now(timezone.utc),
    )
    source = SourceRef(
        material_id=material.id,
        page_id="page_001",
        page_no=1,
        text_span="测试内容",
        image_path="data/processed/mat_001/page_001.png",
    )
    page = PageMetadata(
        id="page_001",
        material_id=material.id,
        page_no=1,
        title="测试",
        raw_text="测试内容",
        image_path=source.image_path or "",
        source_refs=[source],
    )

    repository.save_material(material)
    repository.replace_pages(material.id, [page])

    assert repository.get_material(material.id) == material
    assert repository.list_materials() == [material]
    assert repository.list_pages(material.id) == [page]

    collection = MaterialCollection(
        id="col_001",
        title="课程资料集",
        material_ids=[material.id],
        primary_material_id=material.id,
        created_at=datetime.now(timezone.utc),
    )
    repository.save_collection(collection)
    assert repository.get_collection(collection.id) == collection
    assert repository.list_collections() == [collection]

    replacement = page.model_copy(
        update={
            "id": "page_reparsed",
            "title": "重新解析",
            "source_refs": [source.model_copy(update={"page_id": "page_reparsed"})],
        }
    )
    repository.replace_pages(material.id, [replacement])
    assert repository.list_pages(material.id) == [replacement]
    database.dispose()


def test_learning_content_repository_round_trips_segments_and_legacy_content(tmp_path) -> None:
    database = Database.from_sqlite_path(tmp_path / "content-roundtrip.db")
    from metaclass.modules.content import models as _content_models  # noqa: F401

    database.create_schema()
    repository = SqlAlchemyContentRepository(database)
    material_repository = SqlAlchemyMaterialRepository(database)
    for material_id in ("mat_source", "mat_legacy"):
        material_repository.save_material(
            Material(
                id=material_id,
                filename=f"{material_id}.pdf",
                file_type="pdf",
                status="parsed",
                storage_path=f"data/raw/{material_id}.pdf",
                page_count=1,
            )
        )
    ref = PageRef(material_id="mat_source", page_no=1)
    source = SourceRef(material_id="mat_source", page_id="page_001", page_no=1)
    segment = TeachingSegment(
        id="segment_001_01",
        title="趋势定义",
        teaching_goal="能够解释趋势。",
        page_refs=[ref],
        order=1,
    )
    modern = LearningContent(
        id="content_modern",
        material_id="mat_source",
        material_ids=["mat_source"],
        organization_mode="source_deck",
        title="现代原稿内容",
        sections=[
            LearningSection(
                id="section_001",
                title="趋势分析",
                summary="趋势分析。",
                source_refs=[source],
                page_refs=[ref],
                segments=[segment],
            )
        ],
        knowledge_tree=CourseKnowledgeTree(
            id="tree_modern",
            title="现代原稿内容",
            nodes=[
                CourseKnowledgeTreeNode(
                    id="tree_section_001",
                    title="趋势分析",
                    node_type="section",
                    ref_id="section_001",
                    page_refs=[ref],
                )
            ],
            root_node_ids=["tree_section_001"],
        ),
    )
    legacy = LearningContent.model_validate(
        {
            "id": "content_legacy",
            "material_id": "mat_legacy",
            "organization_mode": "knowledge",
            "title": "旧内容",
            "sections": [
                {
                    "id": "section_old",
                    "title": "旧章节",
                    "summary": "兼容",
                    "source_refs": [source.model_dump(mode="json")],
                }
            ],
        }
    )

    repository.save(modern)
    repository.save(legacy)

    restored_modern = repository.get(modern.id)
    assert restored_modern is not None
    assert restored_modern.model_dump(mode="json", exclude={"created_at", "updated_at"}) == (
        modern.model_dump(mode="json", exclude={"created_at", "updated_at"})
    )
    restored_legacy = repository.get(legacy.id)
    assert restored_legacy is not None
    assert restored_legacy.organization_mode == "knowledge"
    assert restored_legacy.sections[0].segments == []
    database.dispose()


def test_delete_material_project_removes_related_content_plan_pages_and_files(tmp_path) -> None:
    from metaclass.core.application import build_services
    from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan

    services = build_services(tmp_path)
    material_id = "mat_delete"
    material_dir = tmp_path / "raw" / material_id
    processed_dir = tmp_path / "processed" / material_id
    material_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)
    source_path = material_dir / "source.pdf"
    page_path = processed_dir / "page.png"
    source_path.write_bytes(b"source")
    page_path.write_bytes(b"page")
    material = Material(
        id=material_id,
        filename="delete.pdf",
        file_type="pdf",
        status="parsed",
        storage_path=str(source_path),
        page_count=1,
    )
    source_ref = SourceRef(
        material_id=material_id,
        page_id="page_delete",
        page_no=1,
        image_path=str(page_path),
    )
    services.materials.repository.save_material(material)
    services.materials.repository.replace_pages(
        material_id,
        [
            PageMetadata(
                id="page_delete",
                material_id=material_id,
                page_no=1,
                title="Delete",
                raw_text="Delete",
                image_path=str(page_path),
                source_refs=[source_ref],
            )
        ],
    )
    content = LearningContent(
        id="content_delete",
        material_id=material_id,
        material_ids=[material_id],
        title="Delete",
        sections=[
            LearningSection(
                id="section_delete",
                title="Delete",
                summary="Delete",
                source_refs=[source_ref],
            )
        ],
    )
    services.contents.repository.save(content)
    plan = PresentationPlan(
        id="plan_delete",
        content_id=content.id,
        title="Delete",
        source_material_id=material_id,
        slides=[
            SlidePlan(
                id="slide_delete",
                order=1,
                source_section_ids=["section_delete"],
                title="Delete",
                speaker_script="Delete",
                suggested_visual="Delete",
            )
        ],
    )
    services.presentations.repository.save_plan(plan)

    services.materials.delete_project(material_id)

    assert services.materials.repository.get_material(material_id) is None
    assert services.contents.repository.get(content.id) is None
    assert services.presentations.repository.get_plan(plan.id) is None
    assert not material_dir.exists()
    assert not processed_dir.exists()
    services.database.dispose()


def test_delete_secondary_material_rejects_dangling_learning_content(tmp_path) -> None:
    from metaclass.core.application import build_services

    services = build_services(tmp_path)
    primary = Material(
        id="mat_primary",
        filename="primary.pdf",
        file_type="pdf",
        storage_path=str(tmp_path / "raw" / "mat_primary" / "source.pdf"),
    )
    secondary = Material(
        id="mat_secondary",
        filename="secondary.pdf",
        file_type="pdf",
        storage_path=str(tmp_path / "raw" / "mat_secondary" / "source.pdf"),
    )
    services.materials.repository.save_material(primary)
    services.materials.repository.save_material(secondary)
    services.contents.repository.save(
        LearningContent(
            id="content_shared",
            material_id=primary.id,
            material_ids=[primary.id, secondary.id],
            title="Shared content",
            sections=[
                LearningSection(
                    id="section_shared",
                    title="Shared",
                    summary="Shared",
                    source_refs=[
                        SourceRef(
                            material_id=secondary.id,
                            page_id="page_secondary",
                            page_no=1,
                        )
                    ],
                )
            ],
        )
    )

    with pytest.raises(HTTPException, match="多资料 LearningContent") as exc_info:
        services.materials.delete_project(secondary.id)

    assert exc_info.value.status_code == 409
    assert services.materials.repository.get_material(secondary.id) is not None
    assert services.contents.repository.get("content_shared") is not None
    services.database.dispose()


def test_delete_ppt_job_repoints_resource_to_previous_artifact(tmp_path) -> None:
    from metaclass.core.application import build_services

    services = build_services(tmp_path)
    material = Material(
        id="mat_ppt_cleanup",
        filename="deck.pdf",
        file_type="pdf",
        storage_path=str(tmp_path / "raw" / "mat_ppt_cleanup" / "source.pdf"),
    )
    services.materials.repository.save_material(material)
    services.contents.repository.save(
        LearningContent(
            id="content_ppt_cleanup",
            material_id=material.id,
            material_ids=[material.id],
            title="PPT cleanup",
            sections=[
                LearningSection(
                    id="section_ppt_cleanup",
                    title="Cleanup",
                    summary="Cleanup",
                    source_refs=[
                        SourceRef(
                            material_id=material.id,
                            page_id="page_ppt_cleanup",
                            page_no=1,
                        )
                    ],
                )
            ],
        )
    )
    plan = PresentationPlan(
        id="plan_ppt_cleanup",
        content_id="content_ppt_cleanup",
        title="PPT cleanup",
        presentation_resource_id="resource_ppt_cleanup",
        slides=[
            SlidePlan(
                id="slide_ppt_cleanup",
                order=1,
                source_section_ids=["section_ppt_cleanup"],
                title="Cleanup",
                speaker_script="Cleanup",
                suggested_visual="Cleanup",
            )
        ],
    )
    services.presentations.repository.save_plan(plan)
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_job = PPTGenerationJob(
        id="ppt_job_old",
        presentation_plan_id=plan.id,
        status="finished",
        progress=1,
        artifact_id="artifact_old",
        created_at=created_at,
        updated_at=created_at,
    )
    new_job = PPTGenerationJob(
        id="ppt_job_new",
        presentation_plan_id=plan.id,
        status="finished",
        progress=1,
        artifact_id="artifact_new",
        created_at=created_at + timedelta(minutes=1),
        updated_at=created_at + timedelta(minutes=1),
    )
    services.presentations.repository.save_job(old_job)
    services.presentations.repository.save_job(new_job)
    for job, artifact_id in ((old_job, "artifact_old"), (new_job, "artifact_new")):
        services.presentations.repository.save_artifact(
            PPTArtifact(
                id=artifact_id,
                job_id=job.id,
                presentation_plan_id=plan.id,
                skill_request_path=str(tmp_path / f"{artifact_id}.json"),
                created_at=job.created_at,
            )
        )
    services.presentations.repository.save_resource(
        PresentationResource(
            id="resource_ppt_cleanup",
            presentation_plan_id=plan.id,
            kind="generated_artifact",
            artifact_id="artifact_new",
            slides=[
                PresentationSlideResource(
                    slide_id="slide_ppt_cleanup",
                    order=1,
                    kind="generated",
                    artifact_slide_no=1,
                )
            ],
        )
    )

    services.presentations.repository.delete_job(new_job.id)

    resource = services.presentations.repository.get_resource_for_plan(plan.id)
    assert resource is not None
    assert resource.artifact_id == "artifact_old"
    assert services.presentations.repository.get_artifact("artifact_new") is None

    services.presentations.repository.delete_job(old_job.id)
    resource = services.presentations.repository.get_resource_for_plan(plan.id)
    assert resource is not None
    assert resource.artifact_id is None
    services.database.dispose()


def test_sqlite_upgrade_cleans_invalid_presentation_source_material_id(tmp_path) -> None:
    from metaclass.core.application import build_services
    from metaclass.modules.presentation.schemas import PresentationPlan, SlidePlan

    services = build_services(tmp_path)
    material = Material(
        id="mat_cleanup",
        filename="cleanup.pdf",
        file_type="pdf",
        status="parsed",
        storage_path="data/raw/mat_cleanup/source.pdf",
        page_count=1,
    )
    services.materials.repository.save_material(material)
    content = LearningContent(
        id="content_cleanup",
        material_id=material.id,
        title="Cleanup",
        sections=[
            LearningSection(
                id="section_cleanup",
                title="Cleanup",
                summary="Cleanup",
                source_refs=[
                    SourceRef(
                        material_id=material.id,
                        page_id="page_cleanup",
                        page_no=1,
                    )
                ],
            )
        ],
    )
    services.contents.repository.save(content)
    services.presentations.repository.save_plan(
        PresentationPlan(
            id="presentation_cleanup",
            content_id=content.id,
            title="Cleanup",
            slides=[
                SlidePlan(
                    id="slide_cleanup",
                    order=1,
                    source_section_ids=["section_cleanup"],
                    title="Cleanup",
                    speaker_script="Cleanup",
                    suggested_visual="Cleanup",
                )
            ],
        )
    )
    raw_connection = services.database.engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            "UPDATE presentation_plans "
            "SET source_material_id = '2026-08-03 02:53:57' "
            "WHERE id = 'presentation_cleanup'"
        )
        raw_connection.commit()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    finally:
        raw_connection.close()

    services.database.create_schema()

    with services.database.engine.connect() as connection:
        source_material_id = connection.scalar(
            text(
                "SELECT source_material_id FROM presentation_plans "
                "WHERE id = 'presentation_cleanup'"
            )
        )
    assert source_material_id is None
    services.database.dispose()
