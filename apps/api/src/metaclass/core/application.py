from dataclasses import dataclass
from pathlib import Path

from metaclass.core.config import settings
from metaclass.core.llm_config import get_llm_runtime_config
from metaclass.infrastructure.database import Database
from metaclass.infrastructure.providers import (
    FakeLLMProvider,
    LLMLearningProvider,
    build_embedding_provider,
    build_llm_provider,
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
from metaclass.modules.paper_workflow.orchestrator import PaperWorkflowOrchestrator
from metaclass.modules.paper_workflow.providers.composed_skills import ComposedSkillsProvider
from metaclass.modules.paper_workflow.repository import SqlAlchemyPaperWorkflowRepository
from metaclass.modules.paper_workflow.service import PaperWorkflowService
from metaclass.modules.presentation.codex_provider import (
    CodexGenerationError,
    CodexPPTProvider,
)
from metaclass.modules.presentation.planner import PresentationPlanGenerator
from metaclass.modules.presentation.providers import (
    FallbackPPTProvider,
    PresentonPPTProvider,
    UnavailablePPTProvider,
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
    paper_workflows: PaperWorkflowService


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
    paper_workflow_repository = SqlAlchemyPaperWorkflowRepository(database)
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
    local_ppt_provider = PPTSkillAdapter(
        libreoffice_bin=settings.libreoffice_bin,
    )
    ppt_provider = local_ppt_provider
    configured_ppt_provider = settings.ppt_provider.strip().lower()
    presenton_provider = None
    if settings.presenton_api_key:
        presenton_provider = PresentonPPTProvider(
            base_url=settings.presenton_base_url,
            api_key=settings.presenton_api_key,
            adapter=local_ppt_provider,
            timeout_seconds=settings.presenton_timeout_seconds,
            template=settings.presenton_template,
        )
    if not force_fake_llm and configured_ppt_provider == "presenton":
        if presenton_provider is None:
            raise ValueError("PRESENTON_API_KEY is required when METACLASS_PPT_PROVIDER=presenton")
        ppt_provider = presenton_provider
    elif not force_fake_llm and configured_ppt_provider == "codex":
        codex_provider = CodexPPTProvider(
            adapter=local_ppt_provider,
            api_key=settings.codex_api_key,
            model=settings.codex_model,
            timeout_seconds=settings.codex_timeout_seconds,
            repair_attempts=settings.codex_repair_attempts,
            paper_craft_enabled=settings.codex_paper_craft_enabled,
            paper_craft_max_images=settings.codex_paper_craft_max_images,
            paper_craft_concurrency=settings.codex_paper_craft_concurrency,
            paper_craft_skills_dir=settings.codex_paper_craft_skills_dir,
        )
        fallback_provider = presenton_provider or UnavailablePPTProvider(
            "Presenton fallback is unavailable because PRESENTON_API_KEY is not configured"
        )
        ppt_provider = FallbackPPTProvider(
            primary=codex_provider,
            fallback=fallback_provider,
            primary_name="codex",
            fallback_name="presenton",
            fallback_exceptions=(CodexGenerationError,),
        )
    elif configured_ppt_provider != "local" and not force_fake_llm:
        raise ValueError(f"Unsupported PPT provider: {settings.ppt_provider}")
    presentations = PresentationService(
        data_dir,
        presentation_repository,
        contents,
        materials,
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
    paper_workflows = PaperWorkflowService(
        data_dir,
        paper_workflow_repository,
        materials,
        PaperWorkflowOrchestrator(
            data_dir,
            [
                ComposedSkillsProvider(
                    register_presentation=lambda path, filename, derivation_key: (
                        materials.register_generated_pptx(
                            path,
                            filename=filename,
                            derivation_key=derivation_key,
                        ).id
                    )
                )
            ],
        ),
        contents=contents,
        presentations=presentations,
        narration_provider=llm,
    )
    return ApplicationServices(
        database,
        materials,
        contents,
        presentations,
        question_banks,
        classrooms,
        videos,
        paper_workflows,
    )
