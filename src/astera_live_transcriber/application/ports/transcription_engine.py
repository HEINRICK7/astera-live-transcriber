from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
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
    sample_rate: int = 16_000
    channels: int = 1
    interim_results: bool = True
    diarization: bool = False
    keyterms: tuple[str, ...] = ()


class StreamingEngineEventType(StrEnum):
    PARTIAL = "partial"
    TRANSCRIPT_DONE = "transcript_done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StreamingWord:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: int | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class StreamingEngineEvent:
    """Provider-neutral event emitted by a stateful streaming engine."""

    type: StreamingEngineEventType
    text: str = ""
    provider: str = "local"
    provider_event_id: str | None = None
    provider_response_id: str | None = None
    provider_item_id: str | None = None
    provider_previous_item_id: str | None = None
    provider_turn_id: str | None = None
    provider_sequence: int | None = None
    provider_raw_payload: dict[str, object] | None = None
    is_final: bool = False
    speech_final: bool = False
    start_ms: int | None = None
    end_ms: int | None = None
    language: str | None = None
    confidence: float | None = None
    error_code: str | None = None
    words: tuple[StreamingWord, ...] = ()


class RealtimeTranscriptionEnginePort(Protocol):
    async def start(self, config: StreamingConfig) -> None:
        """Open one provider session for one Astera connection."""

    async def push_audio(self, chunk: bytes) -> None:
        """Push one canonical PCM chunk into the bounded provider queue."""

    async def events(self) -> AsyncIterator[StreamingEngineEvent]:
        """Yield currently available provider-neutral events."""

    async def finalize(self) -> None:
        """Ask the provider to flush the current utterance and finish audio."""

    async def stop(self) -> None:
        """Close the provider session idempotently."""


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
