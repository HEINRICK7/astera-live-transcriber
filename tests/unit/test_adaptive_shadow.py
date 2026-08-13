import json

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
    TranscriptionObserver,
)
from astera_live_transcriber.application.transcription_intelligence.safe_normalizer import (
    SafeNormalizer,
)
from astera_live_transcriber.application.transcription_intelligence.vocabulary_matcher import (
    RapidFuzzVocabularyMatcher,
)
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType


def make_engine() -> AdaptationEngine:
    memory = InMemoryTranscriptionMemory()
    return AdaptationEngine(
        memory=memory,
        matcher=RapidFuzzVocabularyMatcher(),
        normalizer=SafeNormalizer(),
        keyterm_selector=KeytermSelector(memory),
        mode="shadow",
    )


def make_event(kind: TranscriptEventType, text: str, revision: int) -> TranscriptEvent:
    return TranscriptEvent(
        type=kind,
        session_id="shadow",
        segment_id="seg_1",
        revision=revision,
        text=text,
        provider="xai",
    )


@pytest.mark.asyncio
async def test_shadow_keeps_live_representation_unchanged() -> None:
    engine = make_engine()
    observer = TranscriptionObserver("shadow", engine)
    first = engine.observe(
        "shadow", make_event(TranscriptEventType.PARTIAL, "los artana", 1)
    )
    second = engine.observe(
        "shadow", make_event(TranscriptEventType.REVISED, "losartana", 2)
    )
    observer._record_match_telemetry(
        make_event(TranscriptEventType.REVISED, "losartana", 2), second
    )

    assert first.representation.normalized_text == "los artana"
    assert second.representation.raw_text == "losartana"
    assert second.representation.normalized_text == "losartana"
    assert second.mode == "shadow"
    assert second.shadow_representation is not None
    assert second.shadow_representation.normalized_text == "losartana"
    assert all(item.mode == "shadow" for item in observer.telemetry)
    await observer.close()


def test_shadow_telemetry_has_scores_and_would_replace_flag() -> None:
    telemetry = {
        "observed": "los artana",
        "candidate": "losartana",
        "ratio": 95.0,
        "partial_ratio": 100.0,
        "token_sort_ratio": 95.0,
        "token_set_ratio": 95.0,
        "wratio": 95.0,
        "decision": "candidate",
        "mode": "shadow",
        "would_replace": False,
    }
    assert json.loads(json.dumps(telemetry))["would_replace"] is False
