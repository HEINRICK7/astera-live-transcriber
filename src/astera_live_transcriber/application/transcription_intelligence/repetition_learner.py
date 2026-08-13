import re

from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType

from .memory import TranscriptionMemoryPort


class RepetitionLearner:
    def __init__(self, memory: TranscriptionMemoryPort) -> None:
        self._memory = memory

    def observe(self, session_id: str, event: TranscriptEvent) -> int:
        if event.type is not TranscriptEventType.COMMITTED or not event.text:
            return 0
        observed = 0
        for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", event.text.lower()):
            self._memory.record_term(session_id, token)
            observed += 1
        return observed
