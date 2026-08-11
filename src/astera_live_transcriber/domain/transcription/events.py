from dataclasses import dataclass
from enum import StrEnum


class TranscriptEventType(StrEnum):
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    SPEECH_STARTED = "speech.started"
    SPEECH_STOP_CANDIDATE = "speech.stop_candidate"
    PARTIAL = "transcript.partial"
    REVISED = "transcript.revised"
    COMMITTED = "transcript.committed"


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    type: TranscriptEventType
    session_id: str
    segment_id: str | None = None
    revision: int | None = None
    text: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    timestamp_ms: int | None = None
    silence_ms: int | None = None
    language: str | None = None
    confidence: float | None = None
