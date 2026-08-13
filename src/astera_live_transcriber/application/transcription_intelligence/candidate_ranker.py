from collections.abc import Sequence

from astera_live_transcriber.domain.transcription_intelligence import (
    CorrectionCandidate,
    RevisionPattern,
    RiskClass,
    SessionVocabularyEntry,
    VocabularyTerm,
)

from .memory import TranscriptionMemoryPort


class CorrectionCandidateRanker:
    def __init__(self, memory: TranscriptionMemoryPort) -> None:
        self._memory = memory

    def rank(
        self,
        observed: str,
        matches: Sequence[tuple[VocabularyTerm, float]],
        patterns: Sequence[RevisionPattern],
        session_terms: Sequence[SessionVocabularyEntry],
        provider_confidence: float | None = None,
    ) -> tuple[CorrectionCandidate, ...]:
        result: list[CorrectionCandidate] = []
        for term, lexical_score in matches:
            related = [
                pattern
                for pattern in patterns
                if pattern.observed.casefold() == observed.casefold()
                and pattern.resolved_as.casefold() == term.canonical.casefold()
            ]
            occurrences = sum(pattern.occurrences for pattern in related)
            confidence = max(
                (self._memory.effective_confidence(pattern) for pattern in related),
                default=0.0,
            )
            revision_score = min(1.0, occurrences / 3) * confidence
            repetition_score = min(
                1.0,
                max(
                    (
                        entry.count
                        for entry in session_terms
                        if entry.term == term.canonical.casefold()
                    ),
                    default=0,
                )
                / 3,
            )
            vocabulary_score = 1.0 if term.source != "unknown" else 0.0
            provider_score = provider_confidence
            risk_class = term.risk_class
            risk_penalty = 1.0 if risk_class is RiskClass.HIGH else 0.0
            final_score = (
                lexical_score / 100 * 0.35
                + revision_score * 0.3
                + repetition_score * 0.15
                + vocabulary_score * 0.2
                + (provider_score or 0.0) * 0.1
                - risk_penalty * 0.4
            )
            result.append(
                CorrectionCandidate(
                    observed=observed,
                    canonical=term.canonical,
                    lexical_score=lexical_score,
                    revision_score=revision_score,
                    repetition_score=repetition_score,
                    vocabulary_score=vocabulary_score,
                    provider_score=provider_score,
                    risk_penalty=risk_penalty,
                    final_score=max(0.0, min(1.0, final_score)),
                    risk_class=risk_class,
                    evidence_occurrences=occurrences,
                    source=term.source,
                )
            )
        return tuple(sorted(result, key=lambda candidate: candidate.final_score, reverse=True))
