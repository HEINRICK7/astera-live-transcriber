from typing import Protocol

from astera_live_transcriber.domain.audio import AudioChunk, VadEvent


class VadPort(Protocol):
    async def process(self, audio: AudioChunk) -> VadEvent:
        """Classify one canonical audio chunk."""


VoiceActivityDetectorPort = VadPort
