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
    vad_threshold: float = 0.03
    prefix_padding_ms: int = 300
    min_speech_ms: int = 200
    silence_candidate_ms: int = 500
    commit_silence_ms: int = 1_500
    max_silence_ms: int = 3_000
    max_segment_duration_ms: int = 30_000
    ring_buffer_max_duration_ms: int = 60_000
    ring_buffer_max_chunks: int = 500
    engine: str = "noop"
    stt_provider: str = "local"
    xai_api_key: str | None = None
    xai_endpoint: str = "wss://api.x.ai/v1/stt"
    xai_endpointing_ms: int = 500
    xai_filler_words: bool = False
    xai_vad_threshold: float = 0.08
    xai_smart_turn: float | None = None
    xai_smart_turn_timeout_ms: int | None = None
    stt_audio_queue_size: int = 32
    stt_queue_high_water_mark: float = 0.8
    stt_provider_stall_timeout_ms: int = 5_000
    stt_max_reconnect_attempts: int = 4
    stt_connect_timeout_ms: int = 10_000
    stt_finalize_timeout_ms: int = 15_000
    stt_reconnect_buffer_ms: int = 3_000
    stt_keyterms: tuple[str, ...] = ()
    stt_diarization: bool = False
    stt_debug_trace: bool = False
    engine_provider: str = "cpu"
    model_path: str = "/models/parakeet-tdt-0.6b-v3-int8"
    engine_threads: int = 4
    engine_debug: bool = False
    engine_warmup: bool = True
    partial_interval_ms: int = 2_000
    partial_window_ms: int = 4_000
    partial_overlap_ms: int = 1_000
    inference_cancel_grace_ms: int = 1_500
    emit_vad_debug_events: bool = False
    max_concurrent_inferences: int = 1
    audio_chunk_ms: int = 100
    file_event_queue_size: int = 100
    file_temp_dir: str = "/tmp/astera-live-transcriber"
    file_cancel_on_disconnect: bool = True
    file_attach_timeout_seconds: int = 300
    intelligence_enabled: bool = True
    intelligence_mode: str = "shadow"
    intelligence_normalization_enabled: bool = False
    intelligence_memory_policy: str = "session_only"
    intelligence_observer_queue_size: int = 256
    intelligence_max_keyterms: int = 20
    intelligence_decay_half_life_days: float = 30.0
    incremental_projection_enabled: bool = False
    incremental_projection_working_set_size: int = 4

    model_config = SettingsConfigDict(
        env_prefix="ASTERA_TRANSCRIBER_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @property
    def environment(self) -> str:
        return self.env


@lru_cache
def get_settings() -> Settings:
    return Settings()
