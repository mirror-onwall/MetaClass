from dataclasses import dataclass
from pathlib import Path

from metaclass.core.config import settings
from metaclass.core.llm_config import get_llm_runtime_config
from metaclass.infrastructure.database import Database
from metaclass.infrastructure.providers import (
    FakeLLMProvider,
    LLMLearningProvider,
    build_llm_provider,
    build_embedding_provider,
    build_tts_provider,
)
from metaclass.infrastructure.providers.fake import FakeLearningProvider
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
from metaclass.modules.presentation.providers import (
    PresentonPPTProvider,
)
from metaclass.modules.presentation.repository import SqlAlchemyPresentationRepository
from metaclass.modules.presentation.service import PresentationService
from metaclass.modules.presentation.skill_adapter import PPTSkillAdapter
from metaclass.modules.question_bank.generator import QuestionBankGenerator
from metaclass.modules.question_bank.repository import SqlAlchemyQuestionBankRepository
from metaclass.modules.question_bank.service import QuestionBankService
from metaclass.modules.video.repository import SqlAlchemyVideoRepository
from metaclass.modules.video.service import VideoService


@dataclass(frozen=True)
class ApplicationServices:
    database: Database
    materials: MaterialService
    contents: ContentService
    presentations: PresentationService
    question_banks: QuestionBankService
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
    question_bank_repository = SqlAlchemyQuestionBankRepository(database)
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
    vision_llm = None
    if settings.vision_enabled and not isinstance(llm, FakeLLMProvider):
        vision_llm = build_llm_provider(
            provider=settings.vision_provider or llm_config.provider,
            base_url=settings.vision_base_url or llm_config.base_url,
            api_key=settings.vision_api_key or llm_config.api_key,
            model=settings.vision_model or llm_config.model,
            timeout_seconds=settings.vision_timeout_seconds or llm_config.timeout_seconds,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
        )
    learning_provider = (
        FakeLearningProvider()
        if isinstance(llm, FakeLLMProvider)
        else LLMLearningProvider(
            llm,
            vision_llm=vision_llm,
            vision_enabled=settings.vision_enabled,
        )
    )
    contents = ContentService(content_repository, materials, learning_provider)
    question_bank_generator = QuestionBankGenerator(
        llm,
        student_concurrency=settings.qa_student_concurrency,
        candidates_per_slide=settings.qa_candidates_per_slide,
    )
    local_ppt_provider = PPTSkillAdapter()
    ppt_provider = local_ppt_provider
    configured_ppt_provider = settings.ppt_provider.strip().lower()
    if not force_fake_llm and configured_ppt_provider == "presenton":
        if not settings.presenton_api_key:
            raise ValueError("PRESENTON_API_KEY is required when METACLASS_PPT_PROVIDER=presenton")
        ppt_provider = PresentonPPTProvider(
            base_url=settings.presenton_base_url,
            api_key=settings.presenton_api_key,
            adapter=local_ppt_provider,
            timeout_seconds=settings.presenton_timeout_seconds,
            template=settings.presenton_template,
        )
    elif configured_ppt_provider != "local" and not force_fake_llm:
        raise ValueError(f"Unsupported PPT provider: {settings.ppt_provider}")
    presentations = PresentationService(
        data_dir,
        presentation_repository,
        contents,
        planner=PresentationPlanGenerator(llm),
        ppt_adapter=ppt_provider,
        question_bank_generator=question_bank_generator,
        question_bank_repository=question_bank_repository,
    )
    question_banks = QuestionBankService(
        question_bank_repository,
        contents,
        presentations,
        question_bank_generator,
        build_embedding_provider(
            provider=settings.embedding_provider,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        ),
    )
    classrooms = ClassroomService(
        classroom_repository,
        contents,
        teacher=TeacherAgent(llm),
        evaluator=EvaluatorAgent(),
        student_roster=StudentRosterAgent(llm),
        controller=ClassroomController(llm),
        planner=ClassroomPlanGenerator(llm, fallback_teacher=TeacherAgent()),
        presentations=presentations,
        question_banks=question_banks,
    )
    tts = build_tts_provider(
        provider="fake" if force_fake_llm else settings.tts_provider,
        base_url=settings.tts_base_url or llm_config.base_url,
        api_key=settings.tts_api_key or llm_config.api_key,
        model=settings.tts_model,
        teacher_voice=settings.tts_teacher_voice,
        student_voices=[
            voice.strip() for voice in settings.tts_student_voices.split(",") if voice.strip()
        ],
        timeout_seconds=settings.tts_timeout_seconds,
    )
    videos = VideoService(
        data_dir,
        video_repository,
        contents,
        tts,
        presentations=presentations,
    )
    return ApplicationServices(
        database,
        materials,
        contents,
        presentations,
        question_banks,
        classrooms,
        videos,
    )
