from difflib import SequenceMatcher

from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType
from astera_live_transcriber.domain.transcription_intelligence import RevisionPattern


class RevisionLearner:
    def __init__(self) -> None:
        self._previous: dict[str, str] = {}

    def observe(self, event: TranscriptEvent) -> tuple[RevisionPattern, ...]:
        if event.segment_id is None or event.text is None:
            return ()
        previous = self._previous.get(event.segment_id, "")
        current = event.text.strip()
        self._previous[event.segment_id] = current
        patterns: list[RevisionPattern] = []
        if previous and current and previous != current:
            if "".join(previous.split()) == "".join(current.split()):
                for opcode, start_a, end_a, start_b, end_b in SequenceMatcher(
                    None, previous.split(), current.split(), autojunk=False
                ).get_opcodes():
                    if opcode == "replace":
                        patterns.append(
                            RevisionPattern(
                                provider=event.provider,
                                observed=" ".join(previous.split()[start_a:end_a]),
                                resolved_as=" ".join(current.split()[start_b:end_b]),
                            )
                        )
                if event.type is TranscriptEventType.COMMITTED:
                    self._previous.pop(event.segment_id, None)
                return tuple(patterns)
            for opcode, start_a, end_a, start_b, end_b in SequenceMatcher(
                None, previous, current, autojunk=False
            ).get_opcodes():
                if opcode != "replace":
                    continue
                observed = previous[start_a:end_a].strip()
                resolved = current[start_b:end_b].strip()
                if observed and resolved and observed != resolved:
                    patterns.append(
                        RevisionPattern(
                            provider=event.provider,
                            observed=observed,
                            resolved_as=resolved,
                        )
                    )
        if event.type is TranscriptEventType.COMMITTED:
            self._previous.pop(event.segment_id, None)
        return tuple(patterns)
