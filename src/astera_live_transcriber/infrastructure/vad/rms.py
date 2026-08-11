import math
import struct

from astera_live_transcriber.domain.audio import AudioChunk, VadEvent, VadEventType


class RmsVad:
    """Small deterministic PCM16 VAD used until a dedicated adapter is selected."""

    def __init__(self, threshold: float, silence_candidate_ms: int, min_speech_ms: int = 0) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("VAD threshold must be between zero and one")
        self._threshold = threshold
        self._silence_candidate_ms = silence_candidate_ms
        self._min_speech_ms = min_speech_ms
        self._in_speech = False
        self._speech_started_ms: int | None = None
        self._silence_started_ms: int | None = None
        self._stop_candidate_emitted = False

    async def process(self, audio: AudioChunk) -> VadEvent:
        speech = self._speech_score(audio.data) >= self._threshold
        timestamp_ms = audio.end_timestamp_ms

        if speech:
            self._silence_started_ms = None
            self._stop_candidate_emitted = False
            if not self._in_speech:
                self._in_speech = True
                self._speech_started_ms = audio.timestamp_ms
                return VadEvent(VadEventType.SPEECH_STARTED, timestamp_ms)
            return VadEvent(VadEventType.SPEECH_ACTIVE, timestamp_ms)

        if not self._in_speech:
            return VadEvent(VadEventType.SILENCE, timestamp_ms)

        if self._silence_started_ms is None:
            self._silence_started_ms = audio.timestamp_ms
        silence_ms = max(0, timestamp_ms - self._silence_started_ms)
        speech_duration_ms = max(0, self._silence_started_ms - (self._speech_started_ms or 0))
        if (
            silence_ms >= self._silence_candidate_ms
            and speech_duration_ms >= self._min_speech_ms
            and not self._stop_candidate_emitted
        ):
            self._stop_candidate_emitted = True
            return VadEvent(VadEventType.SPEECH_STOP_CANDIDATE, timestamp_ms, silence_ms)
        return VadEvent(VadEventType.SILENCE, timestamp_ms, silence_ms)

    def reset(self) -> None:
        self._in_speech = False
        self._speech_started_ms = None
        self._silence_started_ms = None
        self._stop_candidate_emitted = False

    @staticmethod
    def _speech_score(data: bytes) -> float:
        if len(data) < 2:
            return 0.0
        sample_count = len(data) // 2
        samples = struct.unpack(f"<{sample_count}h", data[: sample_count * 2])
        mean_square = sum((sample / 32768) ** 2 for sample in samples) / sample_count
        return min(1.0, math.sqrt(mean_square))
