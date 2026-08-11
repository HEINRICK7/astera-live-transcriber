from collections.abc import AsyncIterator
from dataclasses import dataclass

from astera_live_transcriber.application.ports.transcription_engine import StreamingConfig
from astera_live_transcriber.application.services.streaming_service import StreamingService
from astera_live_transcriber.domain.transcription import TranscriptSegment


@dataclass(frozen=True, slots=True)
class StreamTranscriptionRequest:
    audio: AsyncIterator[bytes]
    config: StreamingConfig


class StreamTranscription:
    def __init__(self, service: StreamingService) -> None:
        self._service = service

    def execute(self, request: StreamTranscriptionRequest) -> AsyncIterator[TranscriptSegment]:
        return self._service.stream(request.audio, request.config)

