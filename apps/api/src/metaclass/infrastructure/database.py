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
            "materials": {"updated_at": "DATETIME"},
            "learning_contents": {
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
            "classroom_sessions": {
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
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
                for name in columns:
                    connection.execute(
                        text(f"UPDATE {table} SET {name} = CURRENT_TIMESTAMP WHERE {name} IS NULL")
                    )
            for table, (name, columns) in unique_indexes.items():
                if table in inspector.get_table_names():
                    connection.execute(
                        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
                    )
