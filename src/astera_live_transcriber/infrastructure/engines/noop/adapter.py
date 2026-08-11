from collections.abc import AsyncIterator

from astera_live_transcriber.application.ports.transcription_engine import StreamingConfig
from astera_live_transcriber.domain.transcription import TranscriptionResult


class NoopTranscriptionEngine:
    """Safe bootstrap adapter that does not run an STT model."""

    async def transcribe(self, audio: bytes, language: str | None = None) -> TranscriptionResult:
        del audio
        return TranscriptionResult(text="", language=language or "pt-BR", duration_ms=0)

    async def stream(self, audio: AsyncIterator[bytes], config: StreamingConfig) -> AsyncIterator:
        del config
        async for _chunk in audio:
            continue
        if False:
            yield None

