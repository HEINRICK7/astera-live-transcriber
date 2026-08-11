import asyncio
import logging
import time
from dataclasses import dataclass

from astera_live_transcriber.application.ports.transcription_engine import TranscriptionEnginePort
from astera_live_transcriber.application.ports.turn_detection import TurnDetectionPort
from astera_live_transcriber.application.ports.vad import VadPort
from astera_live_transcriber.domain.audio import AudioChunk, VadEventType
from astera_live_transcriber.domain.session import TranscriptionSession, TurnDecision
from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics

from .segment_service import SegmentLifecycleService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioPipelineConfig:
    prefix_padding_ms: int
    max_segment_duration_ms: int
    partial_interval_ms: int = 2_000
    partial_window_ms: int = 4_000
    partial_overlap_ms: int = 1_000
    inference_cancel_grace_ms: int = 1_500


@dataclass(frozen=True, slots=True)
class _PartialSnapshot:
    audio: bytes
    end_ms: int


class AudioPipeline:
    """Transforms canonical audio chunks into lifecycle events for one session."""

    def __init__(
        self,
        session: TranscriptionSession,
        normalizer: AudioNormalizer,
        buffer: RingBuffer,
        vad: VadPort,
        turn_detector: TurnDetectionPort,
        engine: TranscriptionEnginePort,
        lifecycle: SegmentLifecycleService,
        config: AudioPipelineConfig,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self.session = session
        self._normalizer = normalizer
        self._buffer = buffer
        self._vad = vad
        self._turn_detector = turn_detector
        self._engine = engine
        self._lifecycle = lifecycle
        self._config = config
        self.metrics = metrics or PipelineMetrics()
        self._last_partial_inference_at_ms: int | None = None
        self._partial_task: asyncio.Task[tuple[_PartialSnapshot, TranscriptionResult]] | None = None
        self._pending_partial: _PartialSnapshot | None = None
        self._last_transcribed_audio_end_ms = 0
        self._inference_count = 0
        self._started_at = time.perf_counter()
        self._first_partial_recorded = False
        self.metrics.set_gauge("partial_window_ms", config.partial_window_ms)
        self.metrics.set_gauge("partial_overlap_ms", config.partial_overlap_ms)
        self.metrics.set_gauge("active_sessions", 1)

    async def process(self, chunk: AudioChunk) -> list[TranscriptEvent]:
        if self.session.closed:
            return []
        events = await self._drain_partial()
        normalized = self._normalizer.normalize(chunk)
        self.metrics.increment("audio_received_ms", normalized.duration_ms)
        if not self._buffer.append(normalized):
            self.session.dropped_chunks += 1
            self.metrics.increment("dropped_chunks")
            logger.warning("buffer_overflow", extra={"session_id": self.session.id})
            return []
        self.metrics.set_gauge("buffer_size", self._buffer.size)

        self.session.mark_audio(normalized.end_timestamp_ms)
        self._record_audio_lag()
        vad_event = await self._vad.process(normalized)
        self._record_vad_metric(vad_event.type, normalized.duration_ms)

        if vad_event.type is VadEventType.SPEECH_STARTED:
            start_ms = max(0, normalized.timestamp_ms - self._config.prefix_padding_ms)
            if self.session.active_segment_id is None:
                self._lifecycle.start(self.session, start_ms)
                self.metrics.increment("segments_created")
            self.session.mark_speech_started(start_ms)
            events.append(
                TranscriptEvent(
                    type=TranscriptEventType.SPEECH_STARTED,
                    session_id=self.session.id,
                    timestamp_ms=start_ms,
                )
            )
        elif vad_event.type is VadEventType.SPEECH_ACTIVE:
            self.session.mark_speech_started(normalized.timestamp_ms)
        elif self.session.active_segment_id is not None:
            silence_start = vad_event.timestamp_ms - vad_event.silence_ms
            self.session.mark_silence_started(max(0, silence_start))
            if vad_event.type is VadEventType.SPEECH_STOP_CANDIDATE:
                events.append(
                    TranscriptEvent(
                        type=TranscriptEventType.SPEECH_STOP_CANDIDATE,
                        session_id=self.session.id,
                        timestamp_ms=vad_event.timestamp_ms,
                        silence_ms=vad_event.silence_ms,
                    )
                )

        if self.session.active_segment_id is not None:
            segment_start_ms = self.session.segment_started_at_ms
            if segment_start_ms is None:
                segment_start_ms = self.session.last_audio_timestamp_ms
            elapsed_ms = self.session.last_audio_timestamp_ms - segment_start_ms
            should_infer = (
                self._last_partial_inference_at_ms is None
                or self._config.partial_interval_ms <= 0
                or elapsed_ms - self._last_partial_inference_at_ms
                >= self._config.partial_interval_ms
            )
            if should_infer:
                self._request_partial()
                self._last_partial_inference_at_ms = elapsed_ms

        decision = await self._turn_detector.evaluate(
            self.session.turn_state(
                buffer_duration_ms=self._buffer.duration_ms,
                max_segment_duration_ms=self._config.max_segment_duration_ms,
            )
        )
        self.metrics.increment(f"turn_{decision.value}")
        if decision in (TurnDecision.COMMIT, TurnDecision.FORCE_COMMIT):
            committed_event = await self._commit()
            if committed_event is not None:
                events.append(committed_event)
        if self._partial_task is not None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            events.extend(await self._drain_partial())
        return events

    async def close(self) -> None:
        self.session.begin_cancelling()
        await self._cancel_partial_scheduler()
        self._buffer.reset()
        self._lifecycle.clear(self.session)
        reset = getattr(self._vad, "reset", None)
        if reset is not None:
            reset()
        self.session.close()
        self.metrics.increment("session_closed")
        self.metrics.set_gauge("active_sessions", 0)
        self.metrics.set_gauge("buffer_size", 0)
        logger.info("session_closed", extra={"session_id": self.session.id})

    async def flush(self) -> list[TranscriptEvent]:
        """Force the current segment at a finite source boundary such as EOF."""
        if self.session.closed or self.session.active_segment_id is None:
            return []
        self.metrics.increment("turn_force_commit")
        committed = await self._commit()
        return [committed] if committed is not None else []

    def _request_partial(self) -> None:
        snapshot = self._snapshot_partial()
        if self._partial_task is None:
            self._partial_task = asyncio.create_task(self._run_partial(snapshot))
        else:
            self._pending_partial = snapshot

    async def _run_partial(
        self, snapshot: _PartialSnapshot
    ) -> tuple[_PartialSnapshot, TranscriptionResult]:
        return snapshot, await self._transcribe(snapshot.audio)

    async def _drain_partial(self) -> list[TranscriptEvent]:
        if self._partial_task is None or not self._partial_task.done():
            return []
        task = self._partial_task
        self._partial_task = None
        snapshot, result = task.result()
        events: list[TranscriptEvent] = []
        event = self._publish_partial_result(snapshot, result)
        if event is not None:
            events.append(event)
        if self._pending_partial is not None and not self.session.closed:
            pending = self._pending_partial
            self._pending_partial = None
            self._partial_task = asyncio.create_task(self._run_partial(pending))
        return events

    def _publish_partial_result(
        self, snapshot: _PartialSnapshot, result: TranscriptionResult
    ) -> TranscriptEvent | None:
        if self.session.closed:
            self.metrics.increment("late_inference_result_total")
            return None
        event = self._lifecycle.publish(
            self.session,
            result,
            end_ms=snapshot.end_ms,
        )
        self._last_transcribed_audio_end_ms = max(
            self._last_transcribed_audio_end_ms, snapshot.end_ms
        )
        self._record_audio_lag()
        if event is not None:
            self.metrics.increment("revisions_per_segment")
            if not self._first_partial_recorded:
                self.metrics.observe(
                    "time_to_first_partial_ms",
                    (time.perf_counter() - self._started_at) * 1000,
                )
                self._first_partial_recorded = True
        return event

    def _snapshot_partial(self) -> _PartialSnapshot:
        segment_start_ms = self.session.segment_started_at_ms or 0
        window_start_ms = max(
            segment_start_ms,
            self.session.last_audio_timestamp_ms - self._config.partial_window_ms,
        )
        window = self._buffer.read_window(start_ms=window_start_ms)
        return _PartialSnapshot(
            audio=b"".join(chunk.data for chunk in window),
            end_ms=self.session.last_audio_timestamp_ms,
        )

    async def _commit(self) -> TranscriptEvent | None:
        started_at = time.perf_counter()
        await self._finish_partial_scheduler()
        chunks = self._buffer.commit()
        audio = b"".join(chunk.data for chunk in chunks)
        result = await self._transcribe(audio)
        event = self._lifecycle.publish(
            self.session,
            result,
            end_ms=self.session.last_audio_timestamp_ms,
            committed=True,
        )
        if event is not None:
            self.metrics.increment("segments_committed")
            self.metrics.observe(
                "segment_commit_latency_ms",
                (time.perf_counter() - started_at) * 1000,
            )
            self.metrics.observe(
                "segment_duration_ms",
                float(
                    self.session.last_audio_timestamp_ms
                    - (self.session.segment_started_at_ms or 0)
                ),
            )
        self._lifecycle.clear(self.session)
        self.session.clear_active_segment()
        self._last_partial_inference_at_ms = None
        self._last_transcribed_audio_end_ms = self.session.last_audio_timestamp_ms
        self._record_audio_lag()
        reset = getattr(self._vad, "reset", None)
        if reset is not None:
            reset()
        return event

    async def _transcribe(self, audio: bytes):
        if self.session.closed:
            self.metrics.increment("discarded_inference_request_total")
            return TranscriptionResult(text="", language=self.session.language, duration_ms=0)
        started_at = time.perf_counter()
        logger.info("engine_started", extra={"session_id": self.session.id})
        inference_task = asyncio.create_task(
            self._engine.transcribe(audio, language=self.session.language)
        )
        self.metrics.set_gauge("inference_in_flight", 1)
        try:
            result = await asyncio.shield(inference_task)
        except asyncio.CancelledError:
            self.metrics.increment("cancelled_inference_total")
            await self._wait_for_cancelled_inference(inference_task)
            raise
        except Exception:
            self.metrics.increment("engine_failed")
            logger.exception("engine_failed", extra={"session_id": self.session.id})
            raise
        finally:
            if inference_task.done():
                self.metrics.set_gauge("inference_in_flight", 0)
        self.metrics.increment("engine_completed")
        self._inference_count += 1
        audio_minutes = self.session.last_audio_timestamp_ms / 60_000
        if audio_minutes > 0:
            self.metrics.set_gauge(
                "inferences_per_audio_minute", self._inference_count / audio_minutes
            )
        self.metrics.observe("transcription_latency_ms", (time.perf_counter() - started_at) * 1000)
        logger.info("engine_completed", extra={"session_id": self.session.id})
        return result

    async def _wait_for_cancelled_inference(
        self, inference_task: asyncio.Task[TranscriptionResult]
    ) -> None:
        grace_seconds = self._config.inference_cancel_grace_ms / 1000
        try:
            await asyncio.wait_for(asyncio.shield(inference_task), grace_seconds)
        except TimeoutError:
            inference_task.add_done_callback(self._record_late_inference)
        except asyncio.CancelledError:
            inference_task.add_done_callback(self._record_late_inference)
        except Exception:
            pass
        else:
            self.metrics.set_gauge("inference_in_flight", 0)
            self._record_late_inference(inference_task)

    def _record_late_inference(self, task: asyncio.Task[TranscriptionResult]) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            return
        self.metrics.increment("late_inference_result_total")
        self.metrics.set_gauge("inference_in_flight", 0)

    async def _finish_partial_scheduler(self) -> None:
        if self._partial_task is None:
            self._pending_partial = None
            return
        task = self._partial_task
        self._partial_task = None
        self._pending_partial = None
        snapshot, result = await task
        self._publish_partial_result(snapshot, result)

    async def _cancel_partial_scheduler(self) -> None:
        task = self._partial_task
        self._partial_task = None
        self._pending_partial = None
        if task is not None:
            self.metrics.increment("cancelled_inference_total")
            task.add_done_callback(self._record_late_partial)
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    self._config.inference_cancel_grace_ms / 1000,
                )
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass

    def _record_late_partial(
        self, task: asyncio.Task[tuple[_PartialSnapshot, TranscriptionResult]]
    ) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            return
        self.metrics.increment("late_inference_result_total")

    def _record_audio_lag(self) -> None:
        lag_ms = max(
            0, self.session.last_audio_timestamp_ms - self._last_transcribed_audio_end_ms
        )
        max_lag_ms = max(lag_ms, self.metrics.gauges.get("audio_lag_max_ms", 0))
        self.metrics.set_gauge(
            "audio_lag_ms",
            lag_ms,
        )
        self.metrics.observe("audio_lag_ms", float(lag_ms))
        self.metrics.set_gauge("audio_lag_max_ms", max_lag_ms)

    def _record_vad_metric(self, event_type: VadEventType, duration_ms: int) -> None:
        metric = {
            VadEventType.SPEECH_STARTED: "speech_detected_ms",
            VadEventType.SPEECH_ACTIVE: "speech_detected_ms",
            VadEventType.SPEECH_STOP_CANDIDATE: "silence_detected_ms",
            VadEventType.SILENCE: "silence_detected_ms",
        }[event_type]
        self.metrics.increment(metric, duration_ms)
