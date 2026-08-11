from dataclasses import dataclass, field
from datetime import UTC, datetime

from astera_live_transcriber.domain.audio import (
    AudioSourceMetadata,
    AudioSourceType,
    AudioStreamMode,
)

from .value_objects import SessionState


@dataclass(frozen=True, slots=True)
class TurnState:
    active_segment: bool
    segment_started_at_ms: int | None
    speech_started_at_ms: int | None
    silence_started_at_ms: int | None
    current_timestamp_ms: int
    buffer_duration_ms: int
    max_segment_duration_ms: int

    @property
    def silence_duration_ms(self) -> int:
        if self.silence_started_at_ms is None:
            return 0
        return max(0, self.current_timestamp_ms - self.silence_started_at_ms)

    @property
    def segment_duration_ms(self) -> int:
        if self.segment_started_at_ms is None:
            return 0
        return max(0, self.current_timestamp_ms - self.segment_started_at_ms)


@dataclass(slots=True)
class TranscriptionSession:
    id: str
    model: str
    language: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    active_segment_id: str | None = None
    segment_revision: int = 0
    speech_started_at_ms: int | None = None
    silence_started_at_ms: int | None = None
    segment_started_at_ms: int | None = None
    last_audio_timestamp_ms: int = 0
    dropped_chunks: int = 0
    state: SessionState = SessionState.ACTIVE
    source_metadata: AudioSourceMetadata = field(default_factory=AudioSourceMetadata)

    @property
    def closed(self) -> bool:
        return self.state is not SessionState.ACTIVE

    @property
    def source_type(self) -> AudioSourceType:
        return self.source_metadata.source_type

    @property
    def stream_mode(self) -> AudioStreamMode | None:
        return self.source_metadata.stream_mode

    @property
    def source_format(self) -> str | None:
        filename = self.source_metadata.filename or ""
        if "." not in filename:
            return None
        return filename.rsplit(".", 1)[-1].lower()

    def begin_segment(self, segment_id: str, start_ms: int) -> None:
        if self.state is not SessionState.ACTIVE:
            raise RuntimeError("cannot start a segment on a closed session")
        self.active_segment_id = segment_id
        self.segment_revision = 0
        self.segment_started_at_ms = start_ms
        self.speech_started_at_ms = start_ms
        self.silence_started_at_ms = None

    def mark_speech_started(self, timestamp_ms: int) -> None:
        if self.speech_started_at_ms is None:
            self.speech_started_at_ms = timestamp_ms
        self.silence_started_at_ms = None

    def mark_silence_started(self, timestamp_ms: int) -> None:
        if self.silence_started_at_ms is None:
            self.silence_started_at_ms = timestamp_ms

    def mark_audio(self, timestamp_ms: int) -> None:
        self.last_audio_timestamp_ms = max(self.last_audio_timestamp_ms, timestamp_ms)

    def next_revision(self) -> int:
        self.segment_revision += 1
        return self.segment_revision

    def clear_active_segment(self) -> None:
        self.active_segment_id = None
        self.segment_revision = 0
        self.speech_started_at_ms = None
        self.silence_started_at_ms = None
        self.segment_started_at_ms = None

    def turn_state(self, buffer_duration_ms: int, max_segment_duration_ms: int) -> TurnState:
        return TurnState(
            active_segment=self.active_segment_id is not None,
            segment_started_at_ms=self.segment_started_at_ms,
            speech_started_at_ms=self.speech_started_at_ms,
            silence_started_at_ms=self.silence_started_at_ms,
            current_timestamp_ms=self.last_audio_timestamp_ms,
            buffer_duration_ms=buffer_duration_ms,
            max_segment_duration_ms=max_segment_duration_ms,
        )

    def begin_cancelling(self) -> None:
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.CANCELLING

    def mark_completed(self) -> None:
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.COMPLETED

    def mark_failed(self) -> None:
        if self.state is not SessionState.CLOSED:
            self.state = SessionState.FAILED

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.clear_active_segment()
