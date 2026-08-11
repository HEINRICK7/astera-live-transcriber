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
    audio_sample_rate: int = 16_000
    vad_enabled: bool = True
    vad_threshold: float = 0.5
    prefix_padding_ms: int = 300
    min_speech_ms: int = 200
    silence_candidate_ms: int = 500
    commit_silence_ms: int = 1_500
    max_silence_ms: int = 3_000
    max_segment_duration_ms: int = 30_000
    ring_buffer_max_duration_ms: int = 60_000
    ring_buffer_max_chunks: int = 500

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
