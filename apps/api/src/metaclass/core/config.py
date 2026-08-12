from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="METACLASS_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str | None = None
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 180.0
    material_parser: str = "auto"
    mineru_command: str = "mineru"
    mineru_timeout_seconds: float = 180.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    qa_student_concurrency: int = 3
    qa_candidates_per_slide: int = 4
    ppt_provider: str = "codex"
    codex_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CODEX_API_KEY",
            "METACLASS_CODEX_API_KEY",
        ),
    )
    codex_model: str | None = None
    codex_timeout_seconds: float = 900.0
    codex_repair_attempts: int = Field(default=1, ge=0, le=2)
    libreoffice_bin: str | None = None
    presenton_base_url: str = Field(
        default="https://api.presenton.ai",
        validation_alias=AliasChoices(
            "PRESENTON_BASE_URL",
            "METACLASS_PRESENTON_BASE_URL",
        ),
    )
    presenton_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PRESENTON_API_KEY",
            "METACLASS_PRESENTON_API_KEY",
        ),
    )
    presenton_timeout_seconds: float = 300.0
    presenton_template: str = "general"
    tts_provider: str = "fake"
    tts_base_url: str | None = None
    tts_api_key: str | None = None
    tts_model: str = "tts-1"
    tts_teacher_voice: str = "alloy"
    tts_student_voices: str = "ash,ballad,coral,echo,fable,nova,onyx,shimmer"
    tts_timeout_seconds: float = 180.0
    vision_enabled: bool = False
    vision_provider: str | None = None
    vision_base_url: str | None = None
    vision_api_key: str | None = None
    vision_model: str | None = None
    vision_timeout_seconds: float | None = None
    embedding_provider: str = "local"
    embedding_model_path: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1024

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'runtime' / 'metaclass.db'}"


settings = Settings()
