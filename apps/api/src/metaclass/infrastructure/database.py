from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event, inspect, text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    """SQLAlchemy runtime shared by module repositories."""

    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            url,
            connect_args=connect_args,
            future=True,
        )
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @classmethod
    def from_sqlite_path(cls, path: Path) -> "Database":
        resolved = path.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{resolved}")

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        self._upgrade_mvp_sqlite_schema()

    def dispose(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def _upgrade_mvp_sqlite_schema(self) -> None:
        """Keep pre-Alembic local databases usable after additive MVP changes."""
        if self.engine.dialect.name != "sqlite":
            return
        additions = {
            "materials": {"file_hash": "VARCHAR(64)", "updated_at": "DATETIME"},
            "page_metadata": {"embedded_images": "JSON"},
            "learning_contents": {
                "material_ids": "JSON",
                "collection_id": "VARCHAR(64)",
                "subtitle": "VARCHAR(500)",
                "audience": "JSON",
                "teaching_intent": "JSON",
                "material_overview": "JSON",
                "global_concepts": "JSON",
                "knowledge_units": "JSON",
                "knowledge_tree": "JSON",
                "generation_guidance": "JSON",
                "quality": "JSON",
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
            "page_understandings": {
                "page_role": "VARCHAR(50)",
                "title": "VARCHAR(500)",
                "teachable_points": "JSON",
                "key_excerpts": "JSON",
                "concepts": "JSON",
                "formulas": "JSON",
                "visual_analysis": "JSON",
                "misconceptions": "JSON",
                "relations": "JSON",
                "quiz_items": "JSON",
            },
            "classroom_sessions": {
                "mode": "VARCHAR(20)",
                "student_states": "JSON",
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
            "classroom_plan_jobs": {
                "presentation_plan_id": "VARCHAR(64)",
            },
            "ppt_artifacts": {
                "slide_images": "JSON",
            },
            "classroom_qa_items": {"embedding": "JSON"},
        }
        unique_indexes = {
            "page_metadata": (
                "uq_page_material_page_no",
                "material_id, page_no",
            ),
            "learning_contents": (
                "uq_learning_content_material_version",
                "material_id, version",
            ),
        }
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            for table, columns in additions.items():
                if table not in inspector.get_table_names():
                    continue
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, sql_type in columns.items():
                    if name not in existing:
                        connection.execute(
                            text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                        )
                if table == "classroom_sessions":
                    connection.execute(
                        text(
                            "UPDATE classroom_sessions "
                            "SET student_states = '[]' "
                            "WHERE student_states IS NULL"
                        )
                    )
                    connection.execute(
                        text("UPDATE classroom_sessions SET mode = 'lecture' WHERE mode IS NULL")
                    )
                if table == "ppt_artifacts":
                    connection.execute(
                        text(
                            "UPDATE ppt_artifacts "
                            "SET slide_images = '[]' "
                            "WHERE slide_images IS NULL"
                        )
                    )
                if table == "page_metadata":
                    connection.execute(
                        text(
                            "UPDATE page_metadata "
                            "SET embedded_images = '[]' "
                            "WHERE embedded_images IS NULL"
                        )
                    )
                if table == "learning_contents":
                    connection.execute(
                        text(
                            "UPDATE learning_contents "
                            "SET material_ids = '[\"' || material_id || '\"]' "
                            "WHERE material_ids IS NULL"
                        )
                    )
                    for json_column in (
                        "audience",
                        "teaching_intent",
                        "material_overview",
                        "generation_guidance",
                        "quality",
                    ):
                        connection.execute(
                            text(
                                f"UPDATE learning_contents SET {json_column} = '{{}}' "
                                f"WHERE {json_column} IS NULL"
                            )
                        )
                    connection.execute(
                        text(
                            "UPDATE learning_contents SET global_concepts = '[]' "
                            "WHERE global_concepts IS NULL"
                        )
                    )
                    connection.execute(
                        text(
                            "UPDATE learning_contents SET knowledge_units = '[]' "
                            "WHERE knowledge_units IS NULL"
                        )
                    )
                    connection.execute(
                        text(
                            "UPDATE learning_contents SET knowledge_tree = NULL "
                            "WHERE knowledge_tree IS NOT NULL "
                            "AND substr(trim(knowledge_tree), 1, 1) <> '{'"
                        )
                    )
                    connection.execute(
                        text("UPDATE learning_contents SET subtitle = '' WHERE subtitle IS NULL")
                    )
                if table == "page_understandings":
                    connection.execute(
                        text(
                            "UPDATE page_understandings "
                            "SET page_role = 'concept' "
                            "WHERE page_role IS NULL"
                        )
                    )
                    connection.execute(
                        text("UPDATE page_understandings SET title = '' WHERE title IS NULL")
                    )
                    for json_column in (
                        "teachable_points",
                        "key_excerpts",
                        "concepts",
                        "formulas",
                        "misconceptions",
                    ):
                        connection.execute(
                            text(
                                f"UPDATE page_understandings SET {json_column} = '[]' "
                                f"WHERE {json_column} IS NULL"
                            )
                        )
                    for json_column in ("visual_analysis", "relations"):
                        connection.execute(
                            text(
                                f"UPDATE page_understandings SET {json_column} = '{{}}' "
                                f"WHERE {json_column} IS NULL"
                            )
                        )
                    connection.execute(
                        text(
                            "UPDATE page_understandings "
                            "SET quiz_items = '[]' "
                            "WHERE quiz_items IS NULL"
                        )
                    )
                for name in columns:
                    if name in {
                        "file_hash",
                        "material_ids",
                        "collection_id",
                        "subtitle",
                        "audience",
                        "teaching_intent",
                        "material_overview",
                        "global_concepts",
                        "knowledge_units",
                        "knowledge_tree",
                        "generation_guidance",
                        "quality",
                        "page_role",
                        "title",
                        "teachable_points",
                        "key_excerpts",
                        "concepts",
                        "formulas",
                        "visual_analysis",
                        "misconceptions",
                        "relations",
                        "student_states",
                        "mode",
                        "slide_images",
                        "quiz_items",
                        "embedded_images",
                    }:
                        continue
                    connection.execute(
                        text(f"UPDATE {table} SET {name} = CURRENT_TIMESTAMP WHERE {name} IS NULL")
                    )
            for table, (name, columns) in unique_indexes.items():
                if table in inspector.get_table_names():
                    connection.execute(
                        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
                    )
