from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from astera_live_transcriber.domain.transcription import TranscriptionResult, TranscriptSegment


@dataclass(frozen=True, slots=True)
class StreamingConfig:
    session_id: str
    language: str
    model: str


class TranscriptionEnginePort(Protocol):
    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe a complete audio payload."""

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[TranscriptSegment]:
        """Yield structured segments from an audio stream."""
