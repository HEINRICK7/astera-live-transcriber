from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEvent


@dataclass(frozen=True, slots=True)
class TranscriptionContext:
    previous_text: str | None = None
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class StreamingConfig:
    session_id: str
    language: str
    model: str
    context: TranscriptionContext | None = None


class TranscriptionEnginePort(Protocol):
    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        context: TranscriptionContext | None = None,
    ) -> TranscriptionResult:
        """Transcribe a complete audio payload."""

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[TranscriptEvent]:
        """Yield protocol events from an audio stream."""
