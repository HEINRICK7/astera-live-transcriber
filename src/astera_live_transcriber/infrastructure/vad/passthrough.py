from astera_live_transcriber.domain.audio import AudioChunk, VadEvent, VadEventType


class PassthroughVad:
    """Treat every received chunk as speech when acoustic VAD is disabled."""

    def __init__(self) -> None:
        self._started = False

    async def process(self, audio: AudioChunk) -> VadEvent:
        if not self._started:
            self._started = True
            return VadEvent(VadEventType.SPEECH_STARTED, audio.end_timestamp_ms)
        return VadEvent(VadEventType.SPEECH_ACTIVE, audio.end_timestamp_ms)

    def reset(self) -> None:
        self._started = False
