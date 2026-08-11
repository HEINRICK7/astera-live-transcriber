from collections.abc import AsyncIterator

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    TranscriptionContext,
)
from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType


class NoopTranscriptionEngine:
    """Safe bootstrap adapter that does not run an STT model."""

    def __init__(self, placeholder_text: str = "") -> None:
        self._placeholder_text = placeholder_text

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        context: TranscriptionContext | None = None,
    ) -> TranscriptionResult:
        del audio
        del context
        return TranscriptionResult(
            text=self._placeholder_text,
            language=language or "pt-BR",
            duration_ms=0,
        )

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[TranscriptEvent]:
        del config
        async for _chunk in audio:
            continue
        if False:
            yield TranscriptEvent(type=TranscriptEventType.SESSION_CREATED, session_id="noop")
