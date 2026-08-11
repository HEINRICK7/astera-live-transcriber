from typing import Protocol


class VoiceActivityDetectorPort(Protocol):
    def is_speech(self, audio: bytes) -> bool:
        """Return whether the audio chunk contains speech."""

