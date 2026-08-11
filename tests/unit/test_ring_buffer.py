from astera_live_transcriber.domain.audio import AudioChunk
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer


def chunk(sequence: int, timestamp_ms: int, duration_ms: int = 100) -> AudioChunk:
    return AudioChunk(
        data=b"\x00\x00",
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        duration_ms=duration_ms,
    )


def test_ring_buffer_is_bounded_and_commits_atomically() -> None:
    buffer = RingBuffer(max_duration_ms=200, max_chunks=10)

    assert buffer.append(chunk(0, 0))
    assert buffer.append(chunk(1, 100))
    assert not buffer.append(chunk(2, 200))
    assert buffer.dropped_chunks == 1

    committed = buffer.commit()

    assert len(committed) == 2
    assert buffer.duration_ms == 0
    assert buffer.size == 0


def test_ring_buffer_can_discard_only_the_old_window() -> None:
    buffer = RingBuffer(max_duration_ms=500)
    buffer.append(chunk(0, 0))
    buffer.append(chunk(1, 100))
    buffer.append(chunk(2, 200))

    discarded = buffer.discard_before(200)

    assert [item.sequence for item in discarded] == [0, 1]
    assert [item.sequence for item in buffer.read_window()] == [2]

