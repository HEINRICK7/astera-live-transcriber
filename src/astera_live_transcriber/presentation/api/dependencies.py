from functools import lru_cache

from astera_live_transcriber.application.services.transcription_service import TranscriptionService
from astera_live_transcriber.infrastructure.config.settings import Settings, get_settings
from astera_live_transcriber.infrastructure.engines.noop.adapter import NoopTranscriptionEngine


@lru_cache
def get_engine() -> NoopTranscriptionEngine:
    return NoopTranscriptionEngine()


def get_transcription_service() -> TranscriptionService:
    return TranscriptionService(get_engine())


def get_app_settings() -> Settings:
    return get_settings()

