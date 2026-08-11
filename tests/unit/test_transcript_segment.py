import pytest

from astera_live_transcriber.domain.transcription import SegmentStatus, TranscriptSegment


def test_transcript_segment_keeps_structured_metadata() -> None:
    segment = TranscriptSegment(
        id="seg_0042",
        session_id="sess_01",
        text="Comecei a sentir dor no peito ontem à noite.",
        revision=3,
        status=SegmentStatus.COMMITTED,
        start_ms=12420,
        end_ms=16880,
        language="pt-BR",
        confidence=0.94,
    )

    assert segment.status is SegmentStatus.COMMITTED
    assert segment.confidence == 0.94


@pytest.mark.parametrize(
    ("field", "value"),
    [("revision", 0), ("start_ms", -1), ("end_ms", -1), ("confidence", 1.1)],
)
def test_transcript_segment_rejects_invalid_metadata(field: str, value: object) -> None:
    values: dict[str, object] = {
        "id": "seg_1",
        "session_id": "sess_1",
        "text": "text",
        "revision": 1,
        "status": SegmentStatus.PARTIAL,
        "start_ms": 0,
        "end_ms": 10,
        "language": "pt-BR",
        "confidence": 0.5,
    }
    values[field] = value

    with pytest.raises(ValueError):
        TranscriptSegment(**values)

