from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METACLASS_", env_file=".env")

    data_dir: Path = Path(__file__).resolve().parents[5] / "data"
    database_url: str | None = None
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'runtime' / 'metaclass.db'}"


settings = Settings()
