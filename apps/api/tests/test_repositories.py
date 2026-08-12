from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from metaclass.infrastructure.database import Database
from metaclass.modules.content.repository import SqlAlchemyContentRepository
from metaclass.modules.content.schemas import LearningContent, LearningSection
from metaclass.modules.materials.models import MaterialRecord
from metaclass.modules.materials.repository import SqlAlchemyMaterialRepository
from metaclass.modules.materials.schemas import (
    Material,
    MaterialCollection,
    PageMetadata,
    SourceRef,
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
