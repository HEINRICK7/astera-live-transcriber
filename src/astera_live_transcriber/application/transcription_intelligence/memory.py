from collections.abc import Iterable
from datetime import UTC, datetime
from math import exp, log
from typing import Protocol

from astera_live_transcriber.domain.transcription_intelligence import (
    RetentionPolicy,
    RevisionPattern,
    SessionVocabularyEntry,
    VocabularyTerm,
)


class TranscriptionMemoryPort(Protocol):
    def record_revision(self, session_id: str, pattern: RevisionPattern) -> RevisionPattern:
        ...

    def record_term(self, session_id: str, term: str) -> SessionVocabularyEntry:
        ...

    def revision_patterns(self, session_id: str, provider: str) -> tuple[RevisionPattern, ...]:
        ...

    def session_vocabulary(self, session_id: str) -> tuple[SessionVocabularyEntry, ...]:
        ...

    def vocabulary_terms(self) -> tuple[VocabularyTerm, ...]:
        ...

    def clear_session(self, session_id: str) -> None:
        ...


class InMemoryTranscriptionMemory:
    """Ephemeral memory with optional derived-pattern promotion."""

    def __init__(
        self,
        policy: RetentionPolicy = RetentionPolicy.SESSION_ONLY,
        decay_half_life_days: float = 30.0,
        vocabulary: Iterable[VocabularyTerm] = (),
    ) -> None:
        self.policy = policy
        self.decay_half_life_days = max(1.0, decay_half_life_days)
        self._session_patterns: dict[str, dict[tuple[str, str, str], RevisionPattern]] = {}
        self._long_term_patterns: dict[tuple[str, str, str], RevisionPattern] = {}
        self._session_terms: dict[str, dict[str, SessionVocabularyEntry]] = {}
        self._vocabulary = {term.canonical.lower(): term for term in vocabulary}

    def record_revision(self, session_id: str, pattern: RevisionPattern) -> RevisionPattern:
        if self.policy is RetentionPolicy.NONE:
            return pattern
        key = (pattern.provider, pattern.observed, pattern.resolved_as)
        session_patterns = self._session_patterns.setdefault(session_id, {})
        current = session_patterns.get(key)
        if current is None:
            current = RevisionPattern(
                provider=pattern.provider,
                observed=pattern.observed,
                resolved_as=pattern.resolved_as,
            )
            session_patterns[key] = current
        else:
            current.occurrences += 1
            current.last_seen_at = datetime.now(UTC)
        current.confidence = min(0.99, 0.45 + current.occurrences * 0.12)
        if (
            self.policy is RetentionPolicy.DERIVED_PATTERNS_ONLY
            and current.occurrences >= 3
        ):
            long_key = (current.provider, current.observed, current.resolved_as)
            promoted = self._long_term_patterns.get(long_key)
            if promoted is None:
                self._long_term_patterns[long_key] = RevisionPattern(
                    provider=current.provider,
                    observed=current.observed,
                    resolved_as=current.resolved_as,
                    occurrences=current.occurrences,
                    confidence=current.confidence,
                    last_seen_at=current.last_seen_at,
                )
            else:
                promoted.occurrences = max(promoted.occurrences, current.occurrences)
                promoted.confidence = max(promoted.confidence, current.confidence)
                promoted.last_seen_at = current.last_seen_at
        return current

    def record_term(self, session_id: str, term: str) -> SessionVocabularyEntry:
        normalized = term.strip().lower()
        if self.policy is RetentionPolicy.NONE:
            return SessionVocabularyEntry(term=normalized)
        terms = self._session_terms.setdefault(session_id, {})
        entry = terms.get(normalized)
        if entry is None:
            entry = SessionVocabularyEntry(term=normalized)
            terms[normalized] = entry
        else:
            entry.count += 1
        entry.stability = min(1.0, entry.count / 3)
        entry.confidence = min(0.99, 0.4 + entry.count * 0.1)
        return entry

    def revision_patterns(self, session_id: str, provider: str) -> tuple[RevisionPattern, ...]:
        patterns = [
            pattern
            for pattern in self._session_patterns.get(session_id, {}).values()
            if pattern.provider == provider
        ]
        if self.policy is RetentionPolicy.DERIVED_PATTERNS_ONLY:
            patterns.extend(
                pattern
                for pattern in self._long_term_patterns.values()
                if pattern.provider == provider
            )
        return tuple(patterns)

    def session_vocabulary(self, session_id: str) -> tuple[SessionVocabularyEntry, ...]:
        return tuple(self._session_terms.get(session_id, {}).values())

    def vocabulary_terms(self) -> tuple[VocabularyTerm, ...]:
        return tuple(self._vocabulary.values())

    def clear_session(self, session_id: str) -> None:
        self._session_patterns.pop(session_id, None)
        self._session_terms.pop(session_id, None)

    def effective_confidence(self, pattern: RevisionPattern, now: datetime | None = None) -> float:
        current = now or datetime.now(UTC)
        age_days = max(0.0, (current - pattern.last_seen_at).total_seconds() / 86_400)
        decay = exp(-log(2) * age_days / self.decay_half_life_days)
        return pattern.confidence * decay
