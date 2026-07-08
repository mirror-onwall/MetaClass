from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METACLASS_", env_file=".env")

    data_dir: Path = Path(__file__).resolve().parents[5] / "data"
    database_url: str | None = None

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'runtime' / 'metaclass.db'}"


settings = Settings()
