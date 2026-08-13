import asyncio
import json
import logging

import pytest

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    StreamingEngineEvent,
    StreamingEngineEventType,
)
from astera_live_transcriber.application.services.streaming_transcript import (
    StreamingTranscriptState,
)
from astera_live_transcriber.application.services.unstable_hypothesis import (
    UnstableHypothesisReconciler,
)
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.runtime import build_engine_runtime
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.speech.cloud.xai.adapter import (
    XaiStreamingSpeechEngine,
)
from astera_live_transcriber.infrastructure.speech.cloud.xai.config import XaiConfig
from astera_live_transcriber.infrastructure.speech.cloud.xai.mapper import map_xai_event


class FakeWebSocket:
    def __init__(self, send_delay: float = 0.0) -> None:
        self.sent: list[object] = []
        self.messages: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False
        self.send_delay = send_delay

    async def send(self, value: object) -> None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(value)
        if value == json.dumps({"type": "audio.done"}):
            await self.messages.put(
                json.dumps(
                    {
                        "type": "transcript.done",
                        "text": "olá mundo",
                        "duration": 0.8,
                    }
                )
            )

    async def recv(self) -> str:
        return await self.messages.get()

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        await self.websocket.messages.put(json.dumps({"type": "transcript.created"}))
        return self.websocket

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_xai_adapter_builds_provider_url_and_normalizes_events() -> None:
    config = XaiConfig(api_key="secret", endpointing_ms=450)
    url = config.url(
        sample_rate=16_000,
        channels=1,
        interim_results=True,
        language="pt-BR",
        diarization=False,
        keyterms=("losartana", "metformina"),
    )

    assert "sample_rate=16000" in url
    assert "language=pt" in url
    assert url.count("keyterm=") == 2

    event = map_xai_event(
        {
            "type": "transcript.partial",
            "text": "eu comecei",
            "id": "evt_1",
            "response_id": "resp_1",
            "item_id": "item_1",
            "previous_item_id": "item_0",
            "turn_id": "turn_1",
            "api_key": "should-not-escape",
            "is_final": False,
            "speech_final": False,
            "start": 0.1,
            "duration": 0.8,
            "words": [{"text": "eu", "start": 0.1, "end": 0.3, "speaker": 1}],
        }
    )

    assert event is not None
    assert event.provider == "xai"
    assert event.start_ms == 100
    assert event.end_ms == 900
    assert event.words[0].speaker == 1
    assert event.provider_event_id == "evt_1"
    assert event.provider_response_id == "resp_1"
    assert event.provider_item_id == "item_1"
    assert event.provider_previous_item_id == "item_0"
    assert event.provider_turn_id == "turn_1"
    assert event.provider_raw_payload is not None
    assert event.provider_raw_payload["api_key"] == "[redacted]"


def test_streaming_transcript_state_does_not_duplicate_revisions() -> None:
    state = StreamingTranscriptState()
    first = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="eu",
            provider="xai",
        )
    )
    second = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="comecei",
            provider="xai",
            is_final=True,
        )
    )
    final = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="eu comecei a sentir",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert first is not None and first.text == "eu"
    assert second is not None and second.text == "eu comecei"
    assert final is not None and final.text == "eu comecei a sentir"


def test_streaming_transcript_state_drops_provider_replay_after_segment_boundary() -> None:
    state = StreamingTranscriptState()
    completed = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="bloco anterior",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )
    state.reset(preserve_completed=True)

    next_segment = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="bloco anterior bloco novo",
            provider="xai",
        )
    )

    assert completed is not None and completed.text == "bloco anterior"
    assert next_segment is not None and next_segment.text == "bloco novo"


def test_streaming_transcript_state_reconciles_append_only_commit() -> None:
    state = StreamingTranscriptState()
    first = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="Paciente tem hipertensão",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )
    state.reset(preserve_completed=True)
    second = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="Paciente tem hipertensão e toma losartana",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    committed = [update.text for update in (first, second) if update is not None]
    assert committed == ["Paciente tem hipertensão", "e toma losartana"]
    assert "Paciente tem hipertensão Paciente tem hipertensão" not in " ".join(committed)


def test_streaming_transcript_state_ignores_exact_committed_replay() -> None:
    state = StreamingTranscriptState()
    state.remember_completed("Paciente tem hipertensão")
    state.reset(preserve_completed=True)

    replay = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="Paciente tem hipertensão",
            provider="xai",
        )
    )

    assert replay is None


def test_streaming_transcript_state_replaces_current_turn_revision() -> None:
    state = StreamingTranscriptState()
    first = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="Paciente toma losartana de 100",
            provider="xai",
            is_final=True,
        )
    )
    revised = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="Paciente toma losartana de 50 miligramas",
            provider="xai",
            is_final=True,
        )
    )
    completed = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="Paciente toma losartana de 50 miligramas todos os dias",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert first is not None and first.text == "Paciente toma losartana de 100"
    assert revised is not None and revised.text == "Paciente toma losartana de 50 miligramas"
    assert completed is not None
    assert "losartana de 100" not in completed.text
    assert completed.text == "Paciente toma losartana de 50 miligramas todos os dias"


def test_streaming_transcript_state_accepts_provider_commit_as_authoritative_snapshot() -> None:
    state = StreamingTranscriptState()
    state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="fuma ou cons fuma ou consome bebidas alcoólicas?",
            provider="xai",
        )
    )

    committed = state.apply_authoritative(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="Você fuma ou consome bebidas alcoólicas?",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert committed is not None
    assert committed.text == "Você fuma ou consome bebidas alcoólicas?"


def test_streaming_transcript_state_replaces_one_token_revision_with_punctuation() -> None:
    state = StreamingTranscriptState()
    state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="Médica.",
            provider="xai",
            is_final=True,
        )
    )

    update = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text="Médica, você teve febre?",
            provider="xai",
            is_final=True,
        )
    )

    assert update is not None and update.text == "Médica, você teve febre?"


def test_streaming_transcript_state_reconciles_suffix_overlap() -> None:
    state = StreamingTranscriptState()
    state.remember_completed("A B C D")
    state.reset(preserve_completed=True)

    update = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="C D E F",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert update is not None and update.text == "E F"


def test_streaming_transcript_state_collapses_repeated_provider_block_before_commit() -> None:
    state = StreamingTranscriptState()
    update = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="A B C A B C D",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert update is not None and update.text == "A B C D"


def test_streaming_transcript_state_collapses_repeated_block_inside_hypothesis() -> None:
    state = StreamingTranscriptState()
    update = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text="A B C D C D E",
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )

    assert update is not None and update.text == "A B C D E"


def test_unstable_hypothesis_reconciler_removes_replayed_prefix() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert reconciler.reconcile("teve", "teve febre") == "teve febre"
    assert reconciler.reconcile("teve febre", "teve teve febre") == "teve febre"


def test_unstable_hypothesis_reconciler_replaces_partial_word_replay() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert (
        reconciler.reconcile(
            "Médica: fuma ou cons",
            "Médica: fuma ou cons fuma ou consome bebidas alcoólicas?",
        )
        == "Médica: fuma ou consome bebidas alcoólicas?"
    )


def test_unstable_hypothesis_reconciler_replaces_internal_span() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert (
        reconciler.reconcile(
            "Paciente toma losartana de 100",
            "Paciente toma losartana de 50 miligramas",
        )
        == "Paciente toma losartana de 50 miligramas"
    )
    assert (
        reconciler.reconcile(
            "A B C D E F",
            "A B X Y E F",
        )
        == "A B X Y E F"
    )


def test_unstable_hypothesis_reconciler_removes_early_repeated_token() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert (
        reconciler.reconcile("Médica. teve", "Médica. teve teve febre")
        == "Médica. teve febre"
    )


def test_unstable_hypothesis_reconciler_keeps_prefix_expansion() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert reconciler.reconcile("A B", "A B C D") == "A B C D"


def test_unstable_hypothesis_reconciler_does_not_merge_dose_revisions() -> None:
    reconciler = UnstableHypothesisReconciler()

    assert (
        reconciler.reconcile(
            "Paciente toma losartana de 100",
            "Paciente toma losartana de 50 miligramas",
        )
        == "Paciente toma losartana de 50 miligramas"
    )


def test_streaming_transcript_state_drops_rolling_window_replay() -> None:
    state = StreamingTranscriptState()
    previous = (
        "Médica: você teve febre, náusea, vômito ou alteração da visão? "
        "Paciente: Náusea. Eu tive ontem, mas não vomitei. A visão ficou pouco "
        "embaçada em alguns momentos. Médica: Você possui alguma doença ou faz "
        "uso de medicamentos? Paciente: Tenho hipertensão."
    )
    state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.TRANSCRIPT_DONE,
            text=previous,
            provider="xai",
            is_final=True,
            speech_final=True,
        )
    )
    state.reset(preserve_completed=True)

    next_segment = state.apply(
        StreamingEngineEvent(
            type=StreamingEngineEventType.PARTIAL,
            text=(
                "Paciente: Náusea. Eu tive ontem, mas não vomitei. A visão ficou pouco "
                "embaçada em alguns momentos. Médica: Você possui alguma doença ou faz "
                "uso de medicamentos? Paciente: Tenho hipertensão controlada e tomo "
                "losartana de 50 miligramas."
            ),
            provider="xai",
        )
    )

    assert next_segment is not None
    assert next_segment.text == "controlada e tomo losartana de 50 miligramas."


@pytest.mark.asyncio
async def test_xai_adapter_streams_binary_audio_and_flushes() -> None:
    websocket = FakeWebSocket()
    connection: dict[str, object] = {}

    def connect_factory(*_args: object, **_kwargs: object) -> FakeContext:
        connection["url"] = _args[0]
        connection.update(_kwargs)
        return FakeContext(websocket)

    engine = XaiStreamingSpeechEngine(
        XaiConfig(api_key="secret", audio_queue_size=2),
        connect_factory=connect_factory,
    )
    await engine.start(
        StreamingConfig(
            session_id="sess_xai",
            language="pt-BR",
            model="astera-stt-realtime-1",
        )
    )
    assert "secret" not in str(connection["url"])
    assert connection["additional_headers"] == {"Authorization": "Bearer secret"}
    await engine.push_audio(b"\x00\x00" * 1600)
    await asyncio.sleep(0.01)
    assert b"\x00\x00" * 1600 in websocket.sent

    await engine.finalize()
    await asyncio.sleep(0.01)
    events = [event async for event in engine.events()]
    assert any(event.type is StreamingEngineEventType.TRANSCRIPT_DONE for event in events)
    assert any(
        json.loads(value) == {"type": "audio.done"}
        for value in websocket.sent
        if isinstance(value, str)
    )
    await engine.stop()
    assert websocket.closed is True


@pytest.mark.asyncio
async def test_xai_adapter_waits_for_slow_provider_without_dropping_audio() -> None:
    websocket = FakeWebSocket(send_delay=0.03)

    def connect_factory(*_args: object, **_kwargs: object) -> FakeContext:
        return FakeContext(websocket)

    metrics = PipelineMetrics()
    engine = XaiStreamingSpeechEngine(
        XaiConfig(
            api_key="secret",
            audio_queue_size=1,
            provider_stall_timeout_ms=100,
        ),
        metrics=metrics,
        connect_factory=connect_factory,
    )
    await engine.start(
        StreamingConfig(
            session_id="sess_slow_provider",
            language="pt-BR",
            model="astera-stt-realtime-1",
        )
    )

    chunk = b"\x00\x00" * 1600
    await engine.push_audio(chunk)
    await engine.push_audio(chunk)
    await engine.push_audio(chunk)
    await asyncio.sleep(0.12)

    snapshot = metrics.snapshot()
    assert snapshot["counters"].get("speech_audio_queue_dropped_chunks", 0) == 0
    assert snapshot["gauges"]["dropped_audio_ms"] == 0
    assert snapshot["counters"].get("provider_drain_rate", 0) >= 1
    await engine.stop()


@pytest.mark.asyncio
async def test_xai_debug_trace_separates_raw_and_mapped_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    websocket = FakeWebSocket()

    def connect_factory(*_args: object, **_kwargs: object) -> FakeContext:
        return FakeContext(websocket)

    engine = XaiStreamingSpeechEngine(
        XaiConfig(api_key="secret", debug_trace=True),
        connect_factory=connect_factory,
    )
    caplog.set_level(
        logging.DEBUG,
        logger="astera_live_transcriber.infrastructure.speech.cloud.xai.adapter",
    )
    await engine.start(
        StreamingConfig(
            session_id="sess_trace",
            language="pt-BR",
            model="astera-stt-realtime-1",
        )
    )
    await websocket.messages.put(
        json.dumps(
            {
                "type": "transcript.partial",
                "id": "evt_1",
                "text": "Paciente: teve febre",
                "is_final": False,
                "speech_final": False,
                "words": [{"text": "febre", "speaker": 1}],
            }
        )
    )
    await asyncio.sleep(0.02)
    await engine.stop()

    messages = [record.getMessage() for record in caplog.records]
    assert any("stage=RAW_XAI" in message and "teve febre" in message for message in messages)
    assert any("stage=MAPPED" in message and "speakers=(1,)" in message for message in messages)


def test_xai_runtime_creates_isolated_engine_per_session() -> None:
    runtime = build_engine_runtime(
        Settings(stt_provider="xai", xai_api_key="secret")
    )

    first = runtime.create_session_engine()
    second = runtime.create_session_engine()
    assert first is not second
