from dataclasses import dataclass
from urllib.parse import urlencode

from astera_live_transcriber.application.ports.speech_errors import SpeechConfigurationError


@dataclass(frozen=True, slots=True)
class XaiConfig:
    api_key: str
    endpoint: str = "wss://api.x.ai/v1/stt"
    connect_timeout_ms: int = 10_000
    audio_queue_size: int = 32
    queue_high_water_mark: float = 0.8
    provider_stall_timeout_ms: int = 5_000
    max_reconnect_attempts: int = 4
    reconnect_buffer_ms: int = 3_000
    endpointing_ms: int = 500
    filler_words: bool = False
    vad_threshold: float = 0.08
    smart_turn: float | None = None
    smart_turn_timeout_ms: int | None = None
    debug_trace: bool = False

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("xAI API key cannot be empty")
        if self.connect_timeout_ms <= 0:
            raise ValueError("connect_timeout_ms must be positive")
        if self.audio_queue_size <= 0:
            raise ValueError("audio_queue_size must be positive")
        if not 0 < self.queue_high_water_mark <= 1:
            raise ValueError("queue_high_water_mark must be between 0 and 1")
        if self.provider_stall_timeout_ms <= 0:
            raise ValueError("provider_stall_timeout_ms must be positive")
        if self.max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts cannot be negative")
        if not 0 <= self.endpointing_ms <= 5_000:
            raise ValueError("endpointing_ms must be between 0 and 5000")
        if not 0 <= self.vad_threshold <= 1:
            raise ValueError("vad_threshold must be between 0 and 1")
        if self.smart_turn is not None and not 0 <= self.smart_turn <= 1:
            raise ValueError("smart_turn must be between 0 and 1")

    def url(
        self,
        *,
        sample_rate: int,
        channels: int,
        interim_results: bool,
        language: str,
        diarization: bool,
        keyterms: tuple[str, ...],
    ) -> str:
        if len(keyterms) > 100:
            raise SpeechConfigurationError("xAI accepts at most 100 keyterms")
        if any(len(term) > 50 for term in keyterms):
            raise SpeechConfigurationError("xAI keyterms cannot exceed 50 characters")
        params: list[tuple[str, str]] = [
            ("sample_rate", str(sample_rate)),
            ("encoding", "pcm"),
            ("interim_results", str(interim_results).lower()),
            ("endpointing", str(self.endpointing_ms)),
            ("language", language.split("-", 1)[0].lower()),
            ("diarize", str(diarization).lower()),
            ("channels", str(channels)),
            ("filler_words", str(self.filler_words).lower()),
            ("vad_threshold", str(self.vad_threshold)),
        ]
        if channels > 1:
            params.append(("multichannel", "true"))
        if self.smart_turn is not None:
            params.append(("smart_turn", str(self.smart_turn)))
        if self.smart_turn_timeout_ms is not None:
            params.append(("smart_turn_timeout", str(self.smart_turn_timeout_ms)))
        params.extend(("keyterm", term) for term in keyterms)
        return f"{self.endpoint}?{urlencode(params)}"
