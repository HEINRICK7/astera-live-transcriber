from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Canonical audio chunk crossing the pipeline boundary."""

    data: bytes
    sequence: int
    timestamp_ms: int
    duration_ms: int
    sample_rate: int = 16_000
    channels: int = 1

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("audio chunk cannot be empty")
        if self.sequence < 0:
            raise ValueError("audio sequence cannot be negative")
        if self.timestamp_ms < 0:
            raise ValueError("audio timestamp cannot be negative")
        if self.duration_ms <= 0:
            raise ValueError("audio duration must be greater than zero")
        if self.sample_rate <= 0:
            raise ValueError("sample rate must be greater than zero")
        if self.channels <= 0:
            raise ValueError("channels must be greater than zero")

    @property
    def end_timestamp_ms(self) -> int:
        return self.timestamp_ms + self.duration_ms

