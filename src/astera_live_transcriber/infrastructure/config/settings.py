from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    api_key: str = "change-me"
    default_model: str = "astera-stt-1"
    default_language: str = "pt-BR"
    audio_max_seconds: int = 3600
    audio_retention: bool = False

    model_config = SettingsConfigDict(
        env_prefix="ASTERA_TRANSCRIBER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def environment(self) -> str:
        return self.env


@lru_cache
def get_settings() -> Settings:
    return Settings()

