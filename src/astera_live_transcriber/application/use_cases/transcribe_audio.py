from dataclasses import dataclass

from astera_live_transcriber.application.services.transcription_service import TranscriptionService
from astera_live_transcriber.domain.transcription import TranscriptionResult


@dataclass(frozen=True, slots=True)
class TranscribeAudioRequest:
    audio: bytes
    language: str | None = None


class TranscribeAudio:
    def __init__(self, service: TranscriptionService) -> None:
        self._service = service

    async def execute(self, request: TranscribeAudioRequest) -> TranscriptionResult:
        return await self._service.transcribe(request.audio, request.language)

