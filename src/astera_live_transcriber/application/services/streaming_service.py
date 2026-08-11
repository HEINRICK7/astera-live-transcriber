from collections.abc import AsyncIterator

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    TranscriptionEnginePort,
)
from astera_live_transcriber.domain.transcription import TranscriptSegment


class StreamingService:
    """Coordinates realtime transcription without knowing the engine detail."""

    def __init__(self, engine: TranscriptionEnginePort) -> None:
        self._engine = engine

    def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[TranscriptSegment]:
        return self._engine.stream(audio, config)

