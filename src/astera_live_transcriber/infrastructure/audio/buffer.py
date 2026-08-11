class AudioBuffer:
    """Small in-memory buffer boundary for the first streaming implementation."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def append(self, chunk: bytes) -> None:
        self._chunks.append(chunk)

    def drain(self) -> bytes:
        audio = b"".join(self._chunks)
        self._chunks.clear()
        return audio

