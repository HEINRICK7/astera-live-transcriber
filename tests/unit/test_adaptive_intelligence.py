from datetime import UTC, datetime, timedelta

import pytest

from astera_live_transcriber.application.transcription_intelligence.adaptation_engine import (
    AdaptationEngine,
)
from astera_live_transcriber.application.transcription_intelligence.keyterm_selector import (
    KeytermSelector,
)
from astera_live_transcriber.application.transcription_intelligence.memory import (
    InMemoryTranscriptionMemory,
)
from astera_live_transcriber.application.transcription_intelligence.observer import (
    AdaptiveMatchTelemetry,
    TranscriptionObserver,
    _BoundedObservationQueue,
    _QueuedObservation,
)
from astera_live_transcriber.application.transcription_intelligence.revision_learner import (
    RevisionLearner,
)
from astera_live_transcriber.application.transcription_intelligence.safe_normalizer import (
    SafeNormalizer,
)
from astera_live_transcriber.application.transcription_intelligence.vocabulary_matcher import (
    RapidFuzzVocabularyMatcher,
)
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType
from astera_live_transcriber.domain.transcription_intelligence import (
    CorrectionCandidate,
    RetentionPolicy,
    RevisionPattern,
    RiskClass,
    VocabularyTerm,
)
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    JiwerEvaluator,
)


def event(
    event_type: TranscriptEventType,
    text: str,
    revision: int,
    provider: str = "test",
) -> TranscriptEvent:
    return TranscriptEvent(
        type=event_type,
        session_id="sess_1",
        segment_id="seg_1",
        revision=revision,
        text=text,
        provider=provider,
    )


def candidate(
    observed: str,
    canonical: str,
    score: float = 0.98,
    risk: RiskClass = RiskClass.LOW,
    occurrences: int = 5,
) -> CorrectionCandidate:
    return CorrectionCandidate(
        observed=observed,
        canonical=canonical,
        lexical_score=99,
        revision_score=1,
        repetition_score=1,
        vocabulary_score=1,
        provider_score=None,
        risk_penalty=0,
        final_score=score,
        risk_class=risk,
        evidence_occurrences=occurrences,
        source="test",
    )


def test_revision_learner_preserves_provider_and_span_alignment() -> None:
    learner = RevisionLearner()
    learner.observe(event(TranscriptEventType.PARTIAL, "tomar los artana", 1))

    patterns = learner.observe(event(TranscriptEventType.REVISED, "tomar losartana", 2))

    assert [(pattern.observed, pattern.resolved_as, pattern.provider) for pattern in patterns] == [
        ("los artana", "losartana", "test")
    ]


def test_memory_decay_and_provider_isolation() -> None:
    memory = InMemoryTranscriptionMemory(decay_half_life_days=10)
    pattern = RevisionPattern(
        provider="xai",
        observed="los artana",
        resolved_as="losartana",
        confidence=0.9,
        last_seen_at=datetime.now(UTC) - timedelta(days=10),
    )
    memory.record_revision("sess_1", pattern)
    memory.record_revision(
        "sess_1",
        RevisionPattern(provider="local", observed="los artana", resolved_as="losartana"),
    )

    assert len(memory.revision_patterns("sess_1", "xai")) == 1
    assert len(memory.revision_patterns("sess_1", "other")) == 0
    assert memory.effective_confidence(pattern) == pytest.approx(0.45, abs=0.05)


def test_safe_normalizer_preserves_raw_and_blocks_high_risk_and_ambiguity() -> None:
    normalizer = SafeNormalizer(enabled=True)
    result = normalizer.normalize(
        "dor em los artana, dose 15 mg",
        (
            candidate("los artana", "losartana"),
            candidate("los artana", "losartan"),
            candidate("15 mg", "50 mg", risk=RiskClass.HIGH),
        ),
    )

    assert result.representation.raw_text == "dor em los artana, dose 15 mg"
    assert result.representation.normalized_text == result.representation.raw_text
    assert result.decisions == ()


def test_safe_normalizer_allows_only_stable_low_risk_decision() -> None:
    result = SafeNormalizer(enabled=True).normalize(
        "consulta de los artana",
        (candidate("los artana", "losartana"),),
    )

    assert result.representation.normalized_text == "consulta de losartana"
    assert result.decisions[0].source == "adaptive_transcription"


def test_memory_retention_none_does_not_store_observations() -> None:
    memory = InMemoryTranscriptionMemory(policy=RetentionPolicy.NONE)
    memory.record_revision(
        "sess_1",
        RevisionPattern(provider="test", observed="foo", resolved_as="bar"),
    )
    memory.record_term("sess_1", "bar")

    assert memory.revision_patterns("sess_1", "test") == ()
    assert memory.session_vocabulary("sess_1") == ()


def test_derived_patterns_promote_after_three_observations() -> None:
    memory = InMemoryTranscriptionMemory(policy=RetentionPolicy.DERIVED_PATTERNS_ONLY)
    for _ in range(3):
        memory.record_revision(
            "sess_1",
            RevisionPattern(provider="test", observed="foo", resolved_as="bar"),
        )

    assert memory.revision_patterns("new_session", "test")[0].resolved_as == "bar"


def test_risk_policy_marks_semantic_medical_changes_high_risk() -> None:
    from astera_live_transcriber.domain.transcription_intelligence.policies import classify_risk

    assert classify_risk("positivo", "negativo") is RiskClass.HIGH
    assert classify_risk("usa", "não usa") is RiskClass.HIGH
    assert classify_risk("hipertensao", "hipotensao") is RiskClass.HIGH


def test_matcher_generates_candidates_without_deciding_correction() -> None:
    terms = (
        VocabularyTerm(
            canonical="eletrocardiograma",
            source="medical_dictionary",
            risk_class=RiskClass.LOW,
        ),
    )

    matches = RapidFuzzVocabularyMatcher().match("eletrocardiograma", terms)

    assert matches[0][0].canonical == "eletrocardiograma"
    assert matches[0][1] >= 99


def test_jiwer_evaluator_supports_silence_reference() -> None:
    scores = JiwerEvaluator().evaluate("", "")

    assert scores.wer == 0
    assert scores.cer == 0


@pytest.mark.asyncio
async def test_observer_is_bounded_and_drains_on_close() -> None:
    queue = _BoundedObservationQueue(1)
    partial = event(TranscriptEventType.PARTIAL, "parcial", 1)
    committed = event(TranscriptEventType.COMMITTED, "final", 2)
    queue.put(_QueuedObservation(1, partial))
    assert queue.put(_QueuedObservation(3, committed)) is True
    assert (await queue.get()).event is committed
    assert queue.dropped == 1

    memory = InMemoryTranscriptionMemory()
    engine = AdaptationEngine(
        memory,
        RapidFuzzVocabularyMatcher(),
        SafeNormalizer(),
        KeytermSelector(memory),
    )
    metrics = PipelineMetrics()
    observer = TranscriptionObserver("sess_1", engine, queue_size=4, metrics=metrics)
    observer.observe(committed)
    await observer.close()

    assert metrics.counters["learning_events_processed"] == 1
    assert observer.telemetry
    assert isinstance(observer.telemetry[0], AdaptiveMatchTelemetry)
    assert observer.telemetry[0].decision == "ignored"
