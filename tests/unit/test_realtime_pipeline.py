import struct

import pytest

from astera_live_transcriber.application.ports.transcription_engine import TranscriptionContext
from astera_live_transcriber.application.services.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
)
from astera_live_transcriber.application.services.segment_service import SegmentLifecycleService
from astera_live_transcriber.domain.audio import AudioChunk
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEventType
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.turn_detection.time_based import TimeBasedTurnDetector
from astera_live_transcriber.infrastructure.vad.rms import RmsVad


class ScriptedEngine:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self._index = 0

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        context: TranscriptionContext | None = None,
    ) -> TranscriptionResult:
        del audio, context
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        return TranscriptionResult(text=text, language=language or "pt-BR", duration_ms=0)


def make_chunk(
    sequence: int,
    timestamp_ms: int,
    speech: bool,
    duration_ms: int = 100,
) -> AudioChunk:
    amplitude = 20_000 if speech else 0
    samples = max(1, duration_ms * 16)
    data = struct.pack(f"<{samples}h", *([amplitude] * samples))
    return AudioChunk(
        data=data,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        duration_ms=duration_ms,
    )


def build_pipeline(
    texts: list[str],
    max_segment_duration_ms: int = 30_000,
) -> AudioPipeline:
    session = TranscriptionSession(id="sess_test", model="astera-stt-realtime-1", language="pt-BR")
    return AudioPipeline(
        session=session,
        normalizer=AudioNormalizer(),
        buffer=RingBuffer(max_duration_ms=10_000),
        vad=RmsVad(threshold=0.2, silence_candidate_ms=500, min_speech_ms=0),
        turn_detector=TimeBasedTurnDetector(commit_silence_ms=1500, max_silence_ms=3000),
        engine=ScriptedEngine(texts),
        lifecycle=SegmentLifecycleService(),
        config=AudioPipelineConfig(
            prefix_padding_ms=300,
            max_segment_duration_ms=max_segment_duration_ms,
            partial_interval_ms=0,
        ),
        metrics=PipelineMetrics(),
    )


async def feed(
    pipeline: AudioPipeline,
    sequence: int,
    timestamp_ms: int,
    speech: bool,
    count: int = 1,
) -> list:
    events = []
    for offset in range(count):
        events.extend(
            await pipeline.process(
                make_chunk(sequence + offset, timestamp_ms + offset * 100, speech)
            )
        )
    return events


@pytest.mark.asyncio
async def test_short_and_intermediate_pauses_keep_the_same_segment_open() -> None:
    pipeline = build_pipeline(["Eu comecei", "Eu comecei a sentir"])

    await feed(pipeline, 0, 0, True)
    await feed(pipeline, 1, 100, False, count=3)
    await feed(pipeline, 4, 400, True)
    assert pipeline.session.active_segment_id == "seg_0001"

    await feed(pipeline, 5, 500, False, count=4)
    await feed(pipeline, 9, 900, True)
    assert pipeline.session.active_segment_id == "seg_0001"


@pytest.mark.asyncio
async def test_long_pause_commits_one_segment() -> None:
    pipeline = build_pipeline(["Tenho uma dor"])

    await feed(pipeline, 0, 0, True, count=3)
    events = await feed(pipeline, 3, 300, False, count=20)

    committed = [event for event in events if event.type is TranscriptEventType.COMMITTED]
    assert len(committed) == 1
    assert committed[0].segment_id == "seg_0001"
    assert pipeline.session.active_segment_id is None


@pytest.mark.asyncio
async def test_silence_only_never_publishes_text() -> None:
    pipeline = build_pipeline(["this must never be emitted"])

    events = await feed(pipeline, 0, 0, False, count=20)

    assert not [event for event in events if event.type.value.startswith("transcript.")]


@pytest.mark.asyncio
async def test_revisions_keep_segment_identity_and_commit_final_revision() -> None:
    pipeline = build_pipeline(["Tenho uma", "Tenho uma dor", "Tenho uma dor no peito"])

    events = []
    events.extend(await feed(pipeline, 0, 0, True, count=3))
    events.extend(await feed(pipeline, 3, 300, False, count=15))
    transcript_events = [event for event in events if event.segment_id is not None]

    assert [event.type for event in transcript_events] == [
        TranscriptEventType.PARTIAL,
        TranscriptEventType.REVISED,
        TranscriptEventType.REVISED,
        TranscriptEventType.COMMITTED,
    ]
    assert {event.segment_id for event in transcript_events} == {"seg_0001"}
    assert [event.revision for event in transcript_events] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_max_segment_duration_forces_commit() -> None:
    pipeline = build_pipeline(["fala contínua"], max_segment_duration_ms=250)

    events = await feed(pipeline, 0, 0, True, count=3)

    assert any(event.type is TranscriptEventType.COMMITTED for event in events)
    assert pipeline.metrics.counters["turn_force_commit"] == 1


@pytest.mark.asyncio
async def test_sessions_do_not_share_buffers_or_segment_state() -> None:
    first = build_pipeline(["sessão A"])
    second = build_pipeline(["sessão B"])
    second.session.id = "sess_02"

    await feed(first, 0, 0, True)
    await feed(second, 0, 0, False)

    assert first.session.active_segment_id == "seg_0001"
    assert second.session.active_segment_id is None
    await first.close()
    assert first.session.closed is True
    assert second.session.closed is False


@pytest.mark.asyncio
async def test_partial_inference_respects_configured_audio_cadence() -> None:
    engine = ScriptedEngine(["primeira", "segunda", "final"])
    pipeline = build_pipeline(["unused"])
    pipeline._engine = engine
    pipeline._config = AudioPipelineConfig(
        prefix_padding_ms=300,
        max_segment_duration_ms=30_000,
        partial_interval_ms=200,
    )

    events = await feed(pipeline, 0, 0, True, count=5)

    revisions = [
        event
        for event in events
        if event.type in (TranscriptEventType.PARTIAL, TranscriptEventType.REVISED)
    ]
    assert len(revisions) == 3
    assert engine._index == 3
