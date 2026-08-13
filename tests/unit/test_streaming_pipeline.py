import struct

import pytest

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    StreamingEngineEvent,
    StreamingEngineEventType,
    StreamingWord,
)
from astera_live_transcriber.application.services.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
)
from astera_live_transcriber.application.services.segment_service import SegmentLifecycleService
from astera_live_transcriber.domain.audio import AudioChunk
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription.events import TranscriptEventType
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.turn_detection.time_based import TimeBasedTurnDetector
from astera_live_transcriber.infrastructure.vad.rms import RmsVad


class FakeStreamingEngine:
    def __init__(self) -> None:
        self.started: StreamingConfig | None = None
        self.pending: list[StreamingEngineEvent] = []
        self.stopped = False
        self._index = 0

    async def start(self, config: StreamingConfig) -> None:
        self.started = config

    async def push_audio(self, _chunk: bytes) -> None:
        texts = ["Tenho uma", "Tenho uma dor", "Tenho uma dor"]
        text = texts[min(self._index, len(texts) - 1)]
        self._index += 1
        self.pending.append(
            StreamingEngineEvent(
                type=StreamingEngineEventType.PARTIAL,
                text=text,
                provider="fake",
                provider_event_id=f"evt_{self._index}",
                provider_sequence=self._index,
                provider_raw_payload={"type": "transcript.partial", "text": text},
                is_final=self._index == 3,
                speech_final=self._index == 3,
            )
        )

    async def events(self):
        while self.pending:
            yield self.pending.pop(0)

    async def finalize(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


class TimedStreamingEngine(FakeStreamingEngine):
    async def push_audio(self, _chunk: bytes) -> None:
        self._index += 1
        if self._index == 1:
            words = (StreamingEngineEventType.PARTIAL,)
            text = "tomo losartana de 100"
            timed = (
                StreamingWord(text="tomo", start_ms=1_000, end_ms=1_100),
                StreamingWord(text="losartana", start_ms=1_100, end_ms=1_500),
                StreamingWord(text="de", start_ms=1_500, end_ms=1_600),
                StreamingWord(text="100", start_ms=1_600, end_ms=1_800),
            )
        else:
            words = (StreamingEngineEventType.PARTIAL,)
            text = "tomo losartana de 50 miligramas"
            timed = (
                StreamingWord(text="tomo", start_ms=1_000, end_ms=1_100),
                StreamingWord(text="losartana", start_ms=1_100, end_ms=1_500),
                StreamingWord(text="de", start_ms=1_500, end_ms=1_600),
                StreamingWord(text="50", start_ms=1_600, end_ms=1_800),
                StreamingWord(text="miligramas", start_ms=1_800, end_ms=2_100),
            )
        self.pending.append(
            StreamingEngineEvent(
                type=words[0],
                text=text,
                provider="fake",
                words=timed,
                is_final=self._index == 2,
                speech_final=self._index == 2,
            )
        )


def make_chunk(sequence: int, timestamp_ms: int) -> AudioChunk:
    return AudioChunk(
        data=struct.pack("<1600h", *([20_000] * 1600)),
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        duration_ms=100,
    )


@pytest.mark.asyncio
async def test_streaming_engine_events_use_existing_segment_lifecycle() -> None:
    engine = FakeStreamingEngine()
    session = TranscriptionSession(
        id="sess_stream",
        model="astera-stt-realtime-1",
        language="pt-BR",
    )
    pipeline = AudioPipeline(
        session=session,
        normalizer=AudioNormalizer(),
        buffer=RingBuffer(max_duration_ms=10_000),
        vad=RmsVad(threshold=0.2, silence_candidate_ms=500, min_speech_ms=0),
        turn_detector=TimeBasedTurnDetector(commit_silence_ms=1500, max_silence_ms=3000),
        engine=engine,
        lifecycle=SegmentLifecycleService(),
        config=AudioPipelineConfig(
            prefix_padding_ms=0,
            max_segment_duration_ms=30_000,
            streaming_debug_trace=True,
        ),
        metrics=PipelineMetrics(),
    )

    events = []
    for sequence in range(3):
        events.extend(await pipeline.process(make_chunk(sequence, sequence * 100)))

    transcript = [event for event in events if event.type.value.startswith("transcript.")]
    assert [event.type for event in transcript] == [
        TranscriptEventType.PARTIAL,
        TranscriptEventType.REVISED,
        TranscriptEventType.COMMITTED,
    ]
    assert {event.segment_id for event in transcript} == {"seg_0001"}
    assert transcript[-1].text == "Tenho uma dor"
    assert transcript[0].technical is not None
    assert transcript[0].technical["sequence"] == 1
    assert transcript[0].technical["raw_xai"]["text"] == "Tenho uma"
    assert transcript[0].technical["mapped"]["text"] == "Tenho uma"
    assert transcript[0].technical["canonical"]["text"] == "Tenho uma"
    text_metrics = transcript[0].technical["text_metrics"]
    assert text_metrics["rapidfuzz"]["pairs"]
    assert text_metrics["jiwer"]["pairs"]
    assert transcript[-1].technical["canonical"]["event_type"] == "transcript.committed"
    assert transcript[-1].projected_text == "Tenho uma dor"
    assert transcript[-1].technical["committed_text"] == "Tenho uma dor"
    assert transcript[-1].technical["projected_text"] == "Tenho uma dor"
    assert transcript[-1].technical["projection_summary"]["operation_count"] == 0
    assert engine.started is not None
    await pipeline.close()
    assert engine.stopped is True


@pytest.mark.asyncio
async def test_streaming_pipeline_uses_word_timeline_for_revisions() -> None:
    engine = TimedStreamingEngine()
    session = TranscriptionSession(
        id="sess_timed",
        model="astera-stt-realtime-1",
        language="pt-BR",
    )
    pipeline = AudioPipeline(
        session=session,
        normalizer=AudioNormalizer(),
        buffer=RingBuffer(max_duration_ms=10_000),
        vad=RmsVad(threshold=0.2, silence_candidate_ms=500, min_speech_ms=0),
        turn_detector=TimeBasedTurnDetector(commit_silence_ms=1500, max_silence_ms=3000),
        engine=engine,
        lifecycle=SegmentLifecycleService(),
        config=AudioPipelineConfig(prefix_padding_ms=0, max_segment_duration_ms=30_000),
        metrics=PipelineMetrics(),
    )

    events = []
    events.extend(await pipeline.process(make_chunk(0, 0)))
    events.extend(await pipeline.process(make_chunk(1, 100)))

    transcript = [event for event in events if event.type.value.startswith("transcript.")]
    assert transcript[0].text == "tomo losartana de 100"
    assert transcript[-1].type is TranscriptEventType.COMMITTED
    assert transcript[-1].text == "tomo losartana de 50 miligramas"
    await pipeline.close()
