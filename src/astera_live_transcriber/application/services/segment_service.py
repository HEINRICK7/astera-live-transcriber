from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription import (
    SegmentStatus,
    TranscriptionResult,
    TranscriptSegment,
    is_publishable_text,
)
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType


class SegmentLifecycleService:
    """Owns identity, revisions and the immutable committed boundary."""

    def __init__(self) -> None:
        self._current: dict[str, TranscriptSegment] = {}
        self._next_id = 1

    def start(self, session: TranscriptionSession, start_ms: int) -> str:
        segment_id = f"seg_{self._next_id:04d}"
        self._next_id += 1
        session.begin_segment(segment_id, start_ms)
        return segment_id

    def publish(
        self,
        session: TranscriptionSession,
        result: TranscriptionResult,
        end_ms: int,
        committed: bool = False,
    ) -> TranscriptEvent | None:
        current = self._current.get(session.id)
        text = result.text.strip()
        if not is_publishable_text(text) and not (committed and current):
            return None
        if current and current.status is SegmentStatus.COMMITTED:
            raise RuntimeError("committed segments cannot be silently revised")
        if not text and current:
            text = current.text
        if current and current.text == text and not committed:
            return None
        if session.active_segment_id is None:
            raise RuntimeError("cannot publish without an active segment")

        status = SegmentStatus.COMMITTED if committed else (
            SegmentStatus.PARTIAL if current is None else SegmentStatus.REVISED
        )
        revision = session.next_revision()
        confidence = result.segments[-1].confidence if result.segments else None
        start_ms = session.segment_started_at_ms or 0
        segment = TranscriptSegment(
            id=session.active_segment_id,
            session_id=session.id,
            text=text,
            revision=revision,
            status=status,
            start_ms=start_ms,
            end_ms=max(start_ms, end_ms),
            language=result.language,
            confidence=confidence,
        )
        self._current[session.id] = segment
        event_type = {
            SegmentStatus.PARTIAL: TranscriptEventType.PARTIAL,
            SegmentStatus.REVISED: TranscriptEventType.REVISED,
            SegmentStatus.COMMITTED: TranscriptEventType.COMMITTED,
        }[status]
        return TranscriptEvent(
            type=event_type,
            session_id=segment.session_id,
            segment_id=segment.id,
            revision=segment.revision,
            text=segment.text,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms if committed else None,
            language=segment.language if committed else None,
            confidence=segment.confidence if committed else None,
        )

    def current(self, session_id: str) -> TranscriptSegment | None:
        return self._current.get(session_id)

    def clear(self, session: TranscriptionSession) -> None:
        self._current.pop(session.id, None)
