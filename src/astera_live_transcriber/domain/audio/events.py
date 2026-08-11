from dataclasses import dataclass
from enum import StrEnum


class VadEventType(StrEnum):
    SPEECH_STARTED = "speech.started"
    SPEECH_ACTIVE = "speech.active"
    SPEECH_STOP_CANDIDATE = "speech.stop_candidate"
    SILENCE = "silence"


@dataclass(frozen=True, slots=True)
class VadEvent:
    type: VadEventType
    timestamp_ms: int
    silence_ms: int = 0

