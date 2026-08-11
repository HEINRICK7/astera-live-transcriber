from collections import deque

from astera_live_transcriber.domain.audio import AudioChunk


class RingBuffer:
    """Bounded in-memory audio buffer with an explicit drop policy."""

    def __init__(self, max_duration_ms: int, max_chunks: int = 500) -> None:
        if max_duration_ms <= 0 or max_chunks <= 0:
            raise ValueError("ring buffer limits must be greater than zero")
        self._max_duration_ms = max_duration_ms
        self._max_chunks = max_chunks
        self._chunks: deque[AudioChunk] = deque()
        self._duration_ms = 0
        self.dropped_chunks = 0

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def size(self) -> int:
        return len(self._chunks)

    def append(self, chunk: AudioChunk) -> bool:
        if (
            len(self._chunks) >= self._max_chunks
            or self._duration_ms + chunk.duration_ms > self._max_duration_ms
        ):
            self.dropped_chunks += 1
            return False
        self._chunks.append(chunk)
        self._duration_ms += chunk.duration_ms
        return True

    def read_window(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> tuple[AudioChunk, ...]:
        return tuple(
            chunk
            for chunk in self._chunks
            if (start_ms is None or chunk.end_timestamp_ms > start_ms)
            and (end_ms is None or chunk.timestamp_ms < end_ms)
        )

    def commit(self) -> tuple[AudioChunk, ...]:
        chunks = tuple(self._chunks)
        self.reset()
        return chunks

    def discard(self) -> tuple[AudioChunk, ...]:
        return self.commit()

    def discard_before(self, timestamp_ms: int) -> tuple[AudioChunk, ...]:
        discarded: list[AudioChunk] = []
        while self._chunks and self._chunks[0].end_timestamp_ms <= timestamp_ms:
            chunk = self._chunks.popleft()
            discarded.append(chunk)
            self._duration_ms -= chunk.duration_ms
        return tuple(discarded)

    def reset(self) -> None:
        self._chunks.clear()
        self._duration_ms = 0


AudioBuffer = RingBuffer
