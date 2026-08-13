from dataclasses import dataclass

from astera_live_transcriber.domain.transcription.events import TranscriptEvent
from astera_live_transcriber.domain.transcription_intelligence import (
    CorrectionCandidate,
    NormalizationDecision,
    RiskClass,
    TranscriptRepresentation,
    VocabularyTerm,
)

from .candidate_ranker import CorrectionCandidateRanker
from .keyterm_selector import KeytermSelector
from .memory import TranscriptionMemoryPort
from .repetition_learner import RepetitionLearner
from .revision_learner import RevisionLearner
from .safe_normalizer import SafeNormalizer
from .vocabulary_matcher import VocabularyMatcher


@dataclass(frozen=True, slots=True)
class AdaptationResult:
    representation: TranscriptRepresentation
    candidates: tuple[CorrectionCandidate, ...] = ()
    decisions: tuple[NormalizationDecision, ...] = ()
    shadow_representation: TranscriptRepresentation | None = None
    mode: str = "active"


class AdaptationEngine:
    def __init__(
        self,
        memory: TranscriptionMemoryPort,
        matcher: VocabularyMatcher,
        normalizer: SafeNormalizer,
        keyterm_selector: KeytermSelector,
        mode: str = "active",
    ) -> None:
        self._memory = memory
        self._matcher = matcher
        self._normalizer = normalizer
        self._keyterm_selector = keyterm_selector
        if mode not in {"active", "shadow"}:
            raise ValueError(f"unsupported adaptive mode: {mode}")
        self._mode = mode
        self._revision_learner = RevisionLearner()
        self._repetition_learner = RepetitionLearner(memory)
        self._ranker = CorrectionCandidateRanker(memory)

    def observe(self, session_id: str, event: TranscriptEvent) -> AdaptationResult:
        patterns = self._revision_learner.observe(event)
        for pattern in patterns:
            self._memory.record_revision(session_id, pattern)
            self._memory.record_term(session_id, pattern.resolved_as)
        self._repetition_learner.observe(session_id, event)
        if not event.text:
            return AdaptationResult(TranscriptRepresentation("", ""), mode=self._mode)
        all_patterns = self._memory.revision_patterns(session_id, event.provider)
        learned_terms = tuple(
            VocabularyTerm(
                canonical=pattern.resolved_as,
                source="revision_pattern",
                # Revision-derived terms are untrusted until a domain vocabulary
                # explicitly marks them as LOW risk.
                risk_class=RiskClass.HIGH,
            )
            for pattern in all_patterns
        )
        all_terms = self._memory.vocabulary_terms() + learned_terms
        candidates: list[CorrectionCandidate] = []
        for pattern in patterns:
            matches = self._matcher.match(pattern.observed, all_terms)
            candidates.extend(
                self._ranker.rank(
                    pattern.observed,
                    matches,
                    all_patterns,
                    self._memory.session_vocabulary(session_id),
                    provider_confidence=event.confidence,
                )
            )
        ranked = tuple(
            sorted(candidates, key=lambda candidate: candidate.final_score, reverse=True)
        )
        shadow = self._normalizer.preview(event.text, ranked) if self._mode == "shadow" else None
        normalized = self._normalizer.normalize(event.text, ranked)
        representation = (
            TranscriptRepresentation(event.text, event.text)
            if self._mode == "shadow"
            else normalized.representation
        )
        return AdaptationResult(
            representation=representation,
            candidates=ranked,
            decisions=(shadow.decisions if shadow is not None else normalized.decisions),
            shadow_representation=(shadow.representation if shadow is not None else None),
            mode=self._mode,
        )

    def keyterms_for_session(self, session_id: str, provider: str) -> tuple[str, ...]:
        return self._keyterm_selector.select(session_id, provider)

    def close_session(self, session_id: str) -> None:
        if hasattr(self._memory, "clear_session"):
            self._memory.clear_session(session_id)
