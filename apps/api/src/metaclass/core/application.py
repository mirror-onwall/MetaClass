from dataclasses import dataclass
from pathlib import Path

from metaclass.core.config import settings
from metaclass.infrastructure.database import Database
from metaclass.infrastructure.providers import build_llm_provider
from metaclass.infrastructure.providers.fake import FakeLearningProvider, FakeTTSProvider
from metaclass.modules.classroom.agents import EvaluatorAgent, StudentRosterAgent, TeacherAgent
from metaclass.modules.classroom.controller import ClassroomController
from metaclass.modules.classroom.repository import SqlAlchemyClassroomRepository
from metaclass.modules.classroom.service import ClassroomService
from metaclass.modules.content.repository import SqlAlchemyContentRepository
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.repository import SqlAlchemyMaterialRepository
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.video.repository import SqlAlchemyVideoRepository
from metaclass.modules.video.service import VideoService


@dataclass(frozen=True)
class ApplicationServices:
    database: Database
    materials: MaterialService
    contents: ContentService
    classrooms: ClassroomService
    videos: VideoService


def build_services(
    data_dir: Path,
    database_url: str | None = None,
    *,
    create_schema: bool = True,
) -> ApplicationServices:
    """Compose the modular monolith in one explicit place."""
    database = (
        Database(database_url)
        if database_url
        else Database.from_sqlite_path(data_dir / "runtime" / "metaclass.db")
    )

    if create_schema:
        database.create_schema()

    material_repository = SqlAlchemyMaterialRepository(database)
    content_repository = SqlAlchemyContentRepository(database)
    classroom_repository = SqlAlchemyClassroomRepository(database)
    video_repository = SqlAlchemyVideoRepository(database)
    llm = build_llm_provider(
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    materials = MaterialService(
        data_dir,
        material_repository,
        parser_backend=settings.material_parser,
        mineru_command=settings.mineru_command,
        mineru_timeout_seconds=settings.mineru_timeout_seconds,
    )
    contents = ContentService(content_repository, materials, FakeLearningProvider())
    classrooms = ClassroomService(
        classroom_repository,
        contents,
        teacher=TeacherAgent(llm),
        evaluator=EvaluatorAgent(),
        student_roster=StudentRosterAgent(llm),
        controller=ClassroomController(llm),
    )
    videos = VideoService(data_dir, video_repository, contents, FakeTTSProvider())
    return ApplicationServices(database, materials, contents, classrooms, videos)
