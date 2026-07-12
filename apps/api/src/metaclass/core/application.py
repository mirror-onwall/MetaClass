from dataclasses import dataclass
from pathlib import Path

from metaclass.core.config import settings
from metaclass.core.llm_config import get_llm_runtime_config
from metaclass.infrastructure.database import Database
from metaclass.infrastructure.providers import (
    FakeLLMProvider,
    LLMLearningProvider,
    build_llm_provider,
)
from metaclass.infrastructure.providers.fake import FakeLearningProvider, FakeTTSProvider
from metaclass.modules.classroom.agents import EvaluatorAgent, StudentRosterAgent, TeacherAgent
from metaclass.modules.classroom.controller import ClassroomController
from metaclass.modules.classroom.planner import ClassroomPlanGenerator
from metaclass.modules.classroom.repository import SqlAlchemyClassroomRepository
from metaclass.modules.classroom.service import ClassroomService
from metaclass.modules.content.repository import SqlAlchemyContentRepository
from metaclass.modules.content.service import ContentService
from metaclass.modules.materials.repository import SqlAlchemyMaterialRepository
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.repository import SqlAlchemyPresentationRepository
from metaclass.modules.presentation.service import PresentationService
from metaclass.modules.video.repository import SqlAlchemyVideoRepository
from metaclass.modules.video.service import VideoService


@dataclass(frozen=True)
class ApplicationServices:
    database: Database
    materials: MaterialService
    contents: ContentService
    presentations: PresentationService
    classrooms: ClassroomService
    videos: VideoService


def build_services(
    data_dir: Path,
    database_url: str | None = None,
    *,
    create_schema: bool = True,
    force_fake_llm: bool = False,
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
    presentation_repository = SqlAlchemyPresentationRepository(database)
    classroom_repository = SqlAlchemyClassroomRepository(database)
    video_repository = SqlAlchemyVideoRepository(database)
    llm_config = get_llm_runtime_config()
    llm = build_llm_provider(
        provider="fake" if force_fake_llm else llm_config.provider,
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        model=llm_config.model,
        timeout_seconds=llm_config.timeout_seconds,
        temperature=llm_config.temperature,
        max_tokens=llm_config.max_tokens,
    )
    materials = MaterialService(
        data_dir,
        material_repository,
        parser_backend=settings.material_parser,
        mineru_command=settings.mineru_command,
        mineru_timeout_seconds=settings.mineru_timeout_seconds,
    )
    learning_provider = (
        FakeLearningProvider() if isinstance(llm, FakeLLMProvider) else LLMLearningProvider(llm)
    )
    contents = ContentService(content_repository, materials, learning_provider)
    presentations = PresentationService(
        data_dir,
        presentation_repository,
        contents,
        planner=PresentationPlanGenerator(llm),
    )
    classrooms = ClassroomService(
        classroom_repository,
        contents,
        teacher=TeacherAgent(llm),
        evaluator=EvaluatorAgent(),
        student_roster=StudentRosterAgent(llm),
        controller=ClassroomController(llm),
        planner=ClassroomPlanGenerator(llm, fallback_teacher=TeacherAgent()),
    )
    videos = VideoService(data_dir, video_repository, contents, FakeTTSProvider())
    return ApplicationServices(database, materials, contents, presentations, classrooms, videos)
