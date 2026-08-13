
from .memory import TranscriptionMemoryPort


class KeytermSelector:
    def __init__(self, memory: TranscriptionMemoryPort, max_terms: int = 20) -> None:
        self._memory = memory
        self._max_terms = max(1, min(100, max_terms))

    def select(self, session_id: str, provider: str) -> tuple[str, ...]:
        scores: dict[str, float] = {
            entry.term: entry.count * (0.5 + entry.stability)
            for entry in self._memory.session_vocabulary(session_id)
        }
        for pattern in self._memory.revision_patterns(session_id, provider):
            scores[pattern.resolved_as] = scores.get(pattern.resolved_as, 0) + pattern.occurrences
        return tuple(
            term
            for term, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if 1 <= len(term) <= 50
        )[: self._max_terms]
