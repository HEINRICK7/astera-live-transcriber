#!/usr/bin/env python3
"""Evaluate Adaptive candidates without changing the structural transcript."""

import argparse
import asyncio
import json
from pathlib import Path

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
from astera_live_transcriber.domain.transcription.events import (
    TranscriptEvent,
    TranscriptEventType,
)
from astera_live_transcriber.domain.transcription_intelligence import RetentionPolicy


async def run(events_path: Path, structural_path: Path, output_path: Path) -> None:
    payload = json.loads(events_path.read_text(encoding="utf-8"))
    memory = InMemoryTranscriptionMemory(policy=RetentionPolicy.SESSION_ONLY)
    engine = AdaptationEngine(
        memory=memory,
        matcher=RapidFuzzVocabularyMatcher(),
        normalizer=SafeNormalizer(enabled=False),
        keyterm_selector=KeytermSelector(memory),
        mode="shadow",
    )
    observer = TranscriptionObserver("shadow-session", engine, queue_size=1)
    processed = 0
    for item in payload:
        event_type = item.get("type")
        if event_type not in {
            TranscriptEventType.PARTIAL.value,
            TranscriptEventType.REVISED.value,
            TranscriptEventType.COMMITTED.value,
        }:
            continue
        event = TranscriptEvent(
            type=TranscriptEventType(event_type),
            session_id=str(item.get("session_id") or "shadow-session"),
            segment_id=item.get("segment_id"),
            revision=item.get("revision"),
            text=item.get("text"),
            confidence=item.get("confidence"),
            provider=str(item.get("provider") or "xai"),
        )
        result = engine.observe(event.session_id, event)
        observer._record_match_telemetry(event, result)  # offline diagnostic boundary
        processed += 1

    structural = structural_path.read_text(encoding="utf-8").strip()
    telemetry = [item.as_dict() for item in observer.telemetry]
    output = {
        "mode": "shadow",
        "projected_text_changed": False,
        "structural_text": structural,
        "shadow_text": structural,
        "events_processed": processed,
        "telemetry_count": len(telemetry),
        "would_replace_count": sum(item["would_replace"] is True for item in telemetry),
        "candidate_count": sum(item["decision"] == "candidate" for item in telemetry),
        "ignored_count": sum(item["decision"] == "ignored" for item in telemetry),
        "high_risk_candidate_count": sum(item["risk_class"] == "high" for item in telemetry),
        "telemetry": telemetry,
    }
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    await observer.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--structural", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.events, args.structural, args.output))


if __name__ == "__main__":
    main()
