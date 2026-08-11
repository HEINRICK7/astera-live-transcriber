from astera_live_transcriber.application.ports.transcription_engine import TranscriptionEnginePort
from astera_live_transcriber.domain.transcription import TranscriptionResult


class TranscriptionService:
    """Coordinates complete-audio transcription without knowing the engine detail."""

    def __init__(self, engine: TranscriptionEnginePort) -> None:
        self._engine = engine

    async def transcribe(self, audio: bytes, language: str | None = None) -> TranscriptionResult:
        return await self._engine.transcribe(audio, language)

