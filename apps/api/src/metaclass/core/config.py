from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METACLASS_", env_file=".env", extra="ignore")

    data_dir: Path = Path(__file__).resolve().parents[5] / "data"
    database_url: str | None = None
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    material_parser: str = "auto"
    mineru_command: str = "mineru"
    mineru_timeout_seconds: float = 180.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    embedding_provider: str = "local"
    embedding_model_path: str = ""
    embedding_base_url: str =  "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1024

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'runtime' / 'metaclass.db'}"


settings = Settings()
