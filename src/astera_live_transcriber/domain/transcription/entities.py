from dataclasses import dataclass

from .value_objects import SegmentStatus


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    id: str
    session_id: str
    text: str
    revision: int
    status: SegmentStatus
    start_ms: int
    end_ms: int
    language: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("segment id cannot be empty")
        if not self.session_id:
            raise ValueError("session id cannot be empty")
        if self.revision < 1:
            raise ValueError("revision must be greater than zero")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("segment timestamps are invalid")
        if not self.language:
            raise ValueError("language cannot be empty")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str
    duration_ms: int
    segments: tuple[TranscriptSegment, ...] = ()

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration cannot be negative")

