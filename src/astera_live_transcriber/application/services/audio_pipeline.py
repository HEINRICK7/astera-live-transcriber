import asyncio
import logging
import time
from dataclasses import dataclass, replace

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    StreamingEngineEvent,
    StreamingEngineEventType,
    TranscriptionEnginePort,
)
from astera_live_transcriber.application.ports.turn_detection import TurnDetectionPort
from astera_live_transcriber.application.ports.vad import VadPort
from astera_live_transcriber.domain.audio import AudioChunk, VadEventType
from astera_live_transcriber.domain.session import TranscriptionSession, TurnDecision
from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics

from ..transcription_intelligence.observer import TranscriptionObserver
from .committed_diagnostics import committed_projection_diagnostics
from .committed_transcript_projector import (
    CommittedTranscriptProjector,
    IncrementalCommittedTranscriptProjector,
)
from .provider_segment_store import ProviderSegmentStore
from .safe_structural_cleanup import SafeStructuralCleanup
from .segment_service import SegmentLifecycleService
from .streaming_transcript import StreamingTranscriptState
from .text_diagnostics import text_comparison_diagnostics
from .word_timeline import WordTimelineStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioPipelineConfig:
    prefix_padding_ms: int
    max_segment_duration_ms: int
    partial_interval_ms: int = 2_000
    partial_window_ms: int = 4_000
    partial_overlap_ms: int = 1_000
    inference_cancel_grace_ms: int = 1_500
    streaming_finalize_timeout_ms: int = 15_000
    streaming_keyterms: tuple[str, ...] = ()
    streaming_diarization: bool = False
    streaming_debug_trace: bool = False
    incremental_projection_enabled: bool = False
    incremental_projection_working_set_size: int = 4


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
        observer: TranscriptionObserver | None = None,
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
        self._observer = observer
        self._last_partial_inference_at_ms: int | None = None
        self._partial_task: asyncio.Task[tuple[_PartialSnapshot, TranscriptionResult]] | None = None
        self._pending_partial: _PartialSnapshot | None = None
        self._last_transcribed_audio_end_ms = 0
        self._inference_count = 0
        self._started_at = time.perf_counter()
        self._first_partial_recorded = False
        self._stream_state = StreamingTranscriptState()
        self._provider_segments = ProviderSegmentStore()
        self._committed_projector = (
            IncrementalCommittedTranscriptProjector(
                working_set_size=config.incremental_projection_working_set_size
            )
            if config.incremental_projection_enabled
            else CommittedTranscriptProjector()
        )
        self._word_timeline = WordTimelineStore()
        self._safe_structural_cleanup = SafeStructuralCleanup()
        self._last_projected_text = ""
        self._stream_started = False
        self._stream_provider = "local"
        self.metrics.set_gauge("partial_window_ms", config.partial_window_ms)
        self.metrics.set_gauge("partial_overlap_ms", config.partial_overlap_ms)
        self.metrics.set_gauge("active_sessions", 1)

    async def process(self, chunk: AudioChunk) -> list[TranscriptEvent]:
        if self.session.closed:
            return []
        if self._is_streaming_engine:
            return await self._process_streaming(chunk)
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
        self._observe_events(events)
        return events

    async def close(self) -> None:
        self.session.begin_cancelling()
        if self._is_streaming_engine:
            await self._stop_streaming_engine()
        await self._cancel_partial_scheduler()
        if self._observer is not None:
            await self._observer.close()
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
        if self._is_streaming_engine:
            return await self._flush_streaming()
        if self.session.closed or self.session.active_segment_id is None:
            return []
        self.metrics.increment("turn_force_commit")
        committed = await self._commit()
        events = [committed] if committed is not None else []
        self._observe_events(events)
        return events

    @property
    def _is_streaming_engine(self) -> bool:
        return all(
            hasattr(self._engine, name)
            for name in ("start", "push_audio", "events", "finalize", "stop")
        )

    async def _process_streaming(self, chunk: AudioChunk) -> list[TranscriptEvent]:
        normalized = self._normalizer.normalize(chunk)
        self.metrics.increment("audio_received_ms", normalized.duration_ms)
        if not self._buffer.append(normalized):
            self.session.dropped_chunks += 1
            self.metrics.increment("dropped_chunks")
            return []
        self.metrics.set_gauge("buffer_size", self._buffer.size)
        self.session.mark_audio(normalized.end_timestamp_ms)
        await self._ensure_streaming_started(normalized)
        await self._engine.push_audio(normalized.data)
        self.metrics.increment("speech_audio_received_chunks")

        events: list[TranscriptEvent] = []
        vad_event = await self._vad.process(normalized)
        self._record_vad_metric(vad_event.type, normalized.duration_ms)
        if vad_event.type is VadEventType.SPEECH_STARTED:
            unstable_before = self._stream_state.unstable_text
            current_before = self._lifecycle.current(self.session.id)
            start_ms = max(0, normalized.timestamp_ms - self._config.prefix_padding_ms)
            self._ensure_segment(start_ms)
            self.session.mark_speech_started(start_ms)
            events.append(
                TranscriptEvent(
                    type=TranscriptEventType.SPEECH_STARTED,
                    session_id=self.session.id,
                    timestamp_ms=start_ms,
                    technical=(
                        {
                            "stage": "CANONICAL",
                            "event": "speech.started",
                            "astera_turn_id": self.session.active_segment_id,
                            "unstable_before": unstable_before,
                            "unstable_after": self._stream_state.unstable_text,
                            "committed_before": current_before.text if current_before else None,
                            "committed_after": None,
                            "timeline_word_count": len(self._word_timeline.words()),
                            "timeline_text": self._word_timeline.text(),
                            "provider_segment_before": self._provider_segments.snapshot(
                                self._provider_segments.get(self.session.active_segment_id or "")
                            ),
                        }
                        if self._config.streaming_debug_trace
                        else None
                    ),
                )
            )
            if self._config.streaming_debug_trace:
                logger.debug(
                    "stream_trace stage=CANONICAL lifecycle=speech.started "
                    "astera_turn_id=%s unstable_before=%r unstable_after=%r "
                    "committed_before=%r committed_after=%r",
                    self.session.active_segment_id,
                    unstable_before,
                    self._stream_state.unstable_text,
                    current_before.text if current_before else None,
                    None,
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

        events.extend(await self._drain_streaming_events())
        decision = await self._turn_detector.evaluate(
            self.session.turn_state(
                buffer_duration_ms=self._buffer.duration_ms,
                max_segment_duration_ms=self._config.max_segment_duration_ms,
            )
        )
        self.metrics.increment(f"turn_{decision.value}")
        if decision in (TurnDecision.COMMIT, TurnDecision.FORCE_COMMIT):
            committed = self._commit_streaming_current()
            if committed is not None:
                events.append(committed)
        self._observe_events(events)
        return events

    async def _ensure_streaming_started(self, chunk: AudioChunk) -> None:
        if self._stream_started:
            return
        await self._engine.start(
            StreamingConfig(
                session_id=self.session.id,
                language=self.session.language,
                model=self.session.model,
                sample_rate=chunk.sample_rate,
                channels=chunk.channels,
                interim_results=True,
                diarization=self._config.streaming_diarization,
                keyterms=self._config.streaming_keyterms,
            )
        )
        self._stream_started = True
        self.metrics.observe("speech_connection_time_ms", 0)

    async def _drain_streaming_events(self) -> list[TranscriptEvent]:
        events: list[TranscriptEvent] = []
        async for provider_event in self._engine.events():
            mapped = self._map_streaming_event(provider_event)
            if mapped is not None:
                events.append(mapped)
        return events

    def _map_streaming_event(self, provider_event: StreamingEngineEvent) -> TranscriptEvent | None:
        if provider_event.type is StreamingEngineEventType.ERROR:
            self.metrics.increment("speech_provider_errors")
            return TranscriptEvent(
                type=TranscriptEventType.ERROR,
                session_id=self.session.id,
                error_code=provider_event.error_code or "speech_provider_error",
            )
        unstable_before = self._stream_state.unstable_text
        current_before = self._lifecycle.current(self.session.id)
        timeline_text_before = self._word_timeline.text()
        timeline_words_before = self._word_timeline.words()
        provider_segment_id = (
            provider_event.provider_turn_id
            or provider_event.provider_item_id
            or "unknown"
        )
        provider_segment_before = self._provider_segments.snapshot(
            self._provider_segments.get(self.session.active_segment_id or "")
        )
        timeline_event = provider_event
        has_timed_words = self._word_timeline.upsert(provider_event.words)
        timeline_change = self._word_timeline.last_change
        source = "word_timeline" if has_timed_words else "string_reconciler"
        if has_timed_words:
            timeline_event = replace(
                provider_event,
                text=self._word_timeline.text(),
                start_ms=self._word_timeline.start_ms(),
                end_ms=self._word_timeline.end_ms(),
                words=self._word_timeline.words(),
            )
            update = self._stream_state.apply_provider_projection(
                timeline_event,
                timeline_event.text,
            )
        else:
            update = (
                self._stream_state.apply_authoritative(provider_event)
                if provider_event.speech_final
                or provider_event.type is StreamingEngineEventType.TRANSCRIPT_DONE
                else self._stream_state.apply(provider_event)
            )
        if update is None:
            if self._config.streaming_debug_trace:
                logger.debug(
                    "stream_trace seq=%s stage=CANONICAL result=dropped "
                    "provider=%s provider_event_id=%s item_id=%s previous_item_id=%s "
                    "turn_id=%s provider_type=%s astera_turn_id=%s "
                    "unstable_before=%r unstable_after=%r committed_before=%r "
                    "committed_after=%r raw_text=%r",
                    provider_event.provider_sequence,
                    provider_event.provider,
                    provider_event.provider_event_id,
                    provider_event.provider_item_id,
                    provider_event.provider_previous_item_id,
                    provider_event.provider_turn_id,
                    provider_event.type,
                    self.session.active_segment_id,
                    unstable_before,
                    self._stream_state.unstable_text,
                    current_before.text if current_before else None,
                    None,
                    provider_event.text,
                )
            return None
        if self.session.active_segment_id is None:
            self._ensure_segment(update.start_ms or self.session.last_audio_timestamp_ms)
        end_ms = update.end_ms or self.session.last_audio_timestamp_ms
        result = TranscriptionResult(
            text=update.text,
            language=update.language or self.session.language,
            duration_ms=max(0, end_ms - (self.session.segment_started_at_ms or 0)),
            confidence=update.confidence,
            words=update.words,
        )
        event = self._lifecycle.publish(
            self.session,
            result,
            end_ms=end_ms,
            committed=update.speech_final,
            provider=provider_event.provider,
        )
        self._stream_provider = provider_event.provider
        self._last_transcribed_audio_end_ms = max(self._last_transcribed_audio_end_ms, end_ms)
        self._record_audio_lag()
        if event is not None:
            projected_transcript = None
            if event.type is TranscriptEventType.COMMITTED:
                self._provider_segments.commit(
                    event.segment_id or provider_segment_id,
                    event.revision or 0,
                    event.text or "",
                    event.start_ms or 0,
                    event.end_ms,
                )
                committed_state = self._provider_segments.get(
                    event.segment_id or provider_segment_id
                )
                assert committed_state is not None
                projected_transcript = self._project_committed(committed_state)
                cleanup = self._safe_structural_cleanup.clean(
                    projected_transcript.text,
                    evidence_text=event.text or "",
                    previous_text=self._last_projected_text,
                    segment_id=event.segment_id,
                    revision=event.revision,
                )
                self._last_projected_text = projected_transcript.text
                event = replace(
                    event,
                    projected_text=projected_transcript.text,
                    projected_text_clean=cleanup.cleaned_text,
                    technical={"safe_cleanup": cleanup.as_dict()},
                )
            else:
                self._provider_segments.upsert_provisional(
                    event.segment_id or provider_segment_id,
                    event.revision or 0,
                    event.text or "",
                    event.start_ms or 0,
                    event.end_ms,
                )
            if self._config.streaming_debug_trace:
                technical = self._technical_trace(
                    provider_event,
                    update.text,
                    event,
                    unstable_before,
                    current_before.text if current_before else None,
                )
                segment_state = self._provider_segments.get(event.segment_id or "")
                if segment_state is not None:
                    technical["provider_segment_before"] = provider_segment_before
                    technical["provider_segment_after"] = self._provider_segments.snapshot(
                        segment_state
                    )
                technical["projection"] = {
                    "source": source,
                    "text_before": timeline_text_before,
                    "text_after": self._word_timeline.text(),
                    "word_count_before": len(timeline_words_before),
                    "word_count_after": len(self._word_timeline.words()),
                    "timeline_change": self._timeline_change_payload(timeline_change),
                }
                technical["provider_identity"] = {
                    "provider_segment_id": provider_segment_id,
                    "provider_event_id": provider_event.provider_event_id,
                    "provider_item_id": provider_event.provider_item_id,
                    "provider_turn_id": provider_event.provider_turn_id,
                }
                technical["span_diagnostics"] = self._span_diagnostics(
                    provider_event,
                    event,
                )
                technical["text_metrics"] = text_comparison_diagnostics(
                    self._text_metric_pairs(
                        provider_event,
                        update.text,
                        unstable_before,
                        current_before.text if current_before else None,
                    )
                )
                if event.type is TranscriptEventType.COMMITTED:
                    committed_projection = committed_projection_diagnostics(
                        self._projection_sources(),
                        event.segment_id or "unknown",
                    )
                    assert projected_transcript is not None
                    committed_projection["projected_transcript"] = projected_transcript.as_dict()
                    technical["committed_text"] = event.text or ""
                    technical["projected_text"] = projected_transcript.text
                    technical["projected_text_clean"] = cleanup.cleaned_text
                    technical["safe_cleanup"] = cleanup.as_dict()
                    technical["projection_summary"] = projected_transcript.summary()
                    technical["committed_projection"] = committed_projection
                    self._append_incremental_projection_diagnostics(technical)
                    previous = technical["committed_projection"].get("previous")
                    if isinstance(previous, dict):
                        technical["text_metrics"] = text_comparison_diagnostics(
                            [
                                *self._text_metric_pairs(
                                    provider_event,
                                    update.text,
                                    unstable_before,
                                    current_before.text if current_before else None,
                                ),
                                (
                                    "previous_committed_segment_to_current",
                                    str(previous.get("committed_text") or ""),
                                    event.text or "",
                                    "previous_committed_segment",
                                ),
                            ]
                        )
                event = replace(
                    event,
                    technical=technical,
                )
            if self._config.streaming_debug_trace:
                logger.debug(
                    "stream_trace seq=%s stage=CANONICAL provider=%s "
                    "provider_event_id=%s item_id=%s previous_item_id=%s turn_id=%s "
                    "provider_type=%s event_type=%s astera_turn_id=%s "
                    "unstable_before=%r unstable_after=%r committed_before=%r "
                    "committed_after=%r text=%r speakers=%s",
                    provider_event.provider_sequence,
                    provider_event.provider,
                    provider_event.provider_event_id,
                    provider_event.provider_item_id,
                    provider_event.provider_previous_item_id,
                    provider_event.provider_turn_id,
                    provider_event.type,
                    event.type,
                    event.segment_id,
                    unstable_before,
                    self._stream_state.unstable_text,
                    current_before.text if current_before else None,
                    event.text if event.type is TranscriptEventType.COMMITTED else None,
                    event.text,
                    tuple(
                        word.speaker
                        for word in provider_event.words
                        if word.speaker is not None
                    ),
                )
                logger.debug(
                    "stream_trace seq=%s stage=PROJECTION source=%s operation=%s "
                    "timeline_before=%r timeline_after=%r removed_words=%s "
                    "provider_segment_before=%s provider_segment_after=%s",
                    provider_event.provider_sequence,
                    source,
                    timeline_change.operation if timeline_change else "none",
                    timeline_text_before,
                    self._word_timeline.text(),
                    len(timeline_change.removed) if timeline_change else 0,
                    provider_segment_before,
                    self._provider_segments.snapshot(
                        self._provider_segments.get(event.segment_id or "")
                    ),
                )
            self.metrics.increment("revisions_per_segment")
            if not self._first_partial_recorded:
                self.metrics.observe(
                    "time_to_first_partial_ms",
                    (time.perf_counter() - self._started_at) * 1000,
                )
                self._first_partial_recorded = True
        if update.speech_final:
            self._clear_streaming_segment()
        return event

    @staticmethod
    def _timeline_change_payload(change: object) -> dict[str, object] | None:
        if change is None:
            return None

        def word_payload(word: object) -> dict[str, object]:
            return {
                "text": word.text,
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "speaker": word.speaker,
                "confidence": word.confidence,
            }

        return {
            "operation": change.operation,
            "incoming": [word_payload(word) for word in change.incoming],
            "removed": [word_payload(word) for word in change.removed],
            "before": [word_payload(word) for word in change.before],
            "after": [word_payload(word) for word in change.after],
        }

    @staticmethod
    def _technical_trace(
        provider_event: StreamingEngineEvent,
        canonical_text: str,
        event: TranscriptEvent,
        unstable_before: str,
        committed_before: str | None,
    ) -> dict[str, object]:
        return {
            "sequence": provider_event.provider_sequence,
            "provider": {
                "event_id": provider_event.provider_event_id,
                "response_id": provider_event.provider_response_id,
                "item_id": provider_event.provider_item_id,
                "previous_item_id": provider_event.provider_previous_item_id,
                "turn_id": provider_event.provider_turn_id,
                "type": provider_event.type.value,
                "start_ms": provider_event.start_ms,
                "end_ms": provider_event.end_ms,
                "is_final": provider_event.is_final,
                "speech_final": provider_event.speech_final,
            },
            "raw_xai": provider_event.provider_raw_payload,
            "mapped": {
                "text": provider_event.text,
                "type": provider_event.type.value,
                "is_final": provider_event.is_final,
                "speech_final": provider_event.speech_final,
                "word_count": len(provider_event.words),
                "speakers": sorted(
                    {word.speaker for word in provider_event.words if word.speaker is not None}
                ),
            },
            "canonical": {
                "event_type": event.type.value,
                "segment_id": event.segment_id,
                "text": canonical_text,
                "source": "word_timeline" if provider_event.words else "string_reconciler",
                "text_before": unstable_before,
            },
            "state": {
                "unstable_before": unstable_before,
                "unstable_after": canonical_text,
                "committed_before": committed_before,
                "committed_after": canonical_text
                if event.type is TranscriptEventType.COMMITTED
                else None,
            },
        }

    @staticmethod
    def _text_metric_pairs(
        provider_event: StreamingEngineEvent,
        canonical_text: str,
        unstable_before: str,
        committed_before: str | None,
    ) -> list[tuple[str, str, str | None, str | None]]:
        raw_text = None
        if provider_event.provider_raw_payload is not None:
            for key in ("text", "transcript", "utterance"):
                value = provider_event.provider_raw_payload.get(key)
                if isinstance(value, str):
                    raw_text = value
                    break
        pairs = [
            ("raw_xai_to_mapped", raw_text or "", provider_event.text, "raw_xai"),
            ("mapped_to_canonical", provider_event.text, canonical_text, "mapped"),
            ("unstable_before_to_canonical", unstable_before, canonical_text, "unstable_before"),
        ]
        if committed_before is not None:
            pairs.append(
                (
                    "committed_before_to_canonical",
                    committed_before,
                    canonical_text,
                    "committed_before",
                )
            )
        return pairs

    @staticmethod
    def _span_diagnostics(
        provider_event: StreamingEngineEvent,
        event: TranscriptEvent,
    ) -> dict[str, object]:
        timed_words = [
            word for word in provider_event.words
            if word.start_ms is not None and word.end_ms is not None
        ]
        word_start = min((word.start_ms for word in timed_words), default=None)
        word_end = max((word.end_ms for word in timed_words), default=None)
        provider_duration = (
            provider_event.end_ms - provider_event.start_ms
            if provider_event.start_ms is not None and provider_event.end_ms is not None
            else None
        )
        astera_duration = (
            event.end_ms - event.start_ms
            if event.start_ms is not None and event.end_ms is not None
            else None
        )
        word_duration = (
            word_end - word_start
            if word_start is not None and word_end is not None
            else None
        )
        return {
            "provider_event_span": {
                "start_ms": provider_event.start_ms,
                "end_ms": provider_event.end_ms,
                "duration_ms": provider_duration,
            },
            "provider_word_span": {
                "start_ms": word_start,
                "end_ms": word_end,
                "duration_ms": word_duration,
                "timed_word_count": len(timed_words),
            },
            "astera_segment_span": {
                "segment_id": event.segment_id,
                "start_ms": event.start_ms,
                "end_ms": event.end_ms,
                "duration_ms": astera_duration,
            },
            "anomalies": {
                "astera_zero_duration": astera_duration == 0,
                "provider_zero_duration": provider_duration == 0,
                "provider_words_outside_event_span": bool(
                    timed_words
                    and provider_event.start_ms is not None
                    and provider_event.end_ms is not None
                    and (
                        word_start < provider_event.start_ms
                        or word_end > provider_event.end_ms
                    )
                ),
                "astera_span_much_larger_than_provider_words": bool(
                    astera_duration is not None
                    and word_duration is not None
                    and astera_duration > word_duration * 3
                    and word_duration > 0
                ),
            },
        }

    def _ensure_segment(self, start_ms: int) -> None:
        if self.session.active_segment_id is None:
            self._lifecycle.start(self.session, max(0, start_ms))
            self.metrics.increment("segments_created")

    def _commit_streaming_current(self) -> TranscriptEvent | None:
        current = self._lifecycle.current(self.session.id)
        if current is None or self.session.active_segment_id is None:
            self._clear_streaming_segment()
            return None
        result = TranscriptionResult(
            text=current.text,
            language=current.language,
            duration_ms=max(0, self.session.last_audio_timestamp_ms - current.start_ms),
            confidence=current.confidence,
        )
        self._stream_state.remember_completed(current.text)
        started_at = time.perf_counter()
        event = self._lifecycle.publish(
            self.session,
            result,
            end_ms=self.session.last_audio_timestamp_ms,
            committed=True,
            provider=self._stream_provider,
        )
        if event is not None:
            self._provider_segments.commit(
                event.segment_id or "unknown",
                event.revision or 0,
                event.text or "",
                event.start_ms or 0,
                event.end_ms,
            )
            committed_state = self._provider_segments.get(event.segment_id or "unknown")
            assert committed_state is not None
            projected_transcript = self._project_committed(committed_state)
            cleanup = self._safe_structural_cleanup.clean(
                projected_transcript.text,
                evidence_text=event.text or "",
                previous_text=self._last_projected_text,
                segment_id=event.segment_id,
                revision=event.revision,
            )
            self._last_projected_text = projected_transcript.text
            event = replace(
                event,
                projected_text=projected_transcript.text,
                projected_text_clean=cleanup.cleaned_text,
                technical={"safe_cleanup": cleanup.as_dict()},
            )
            if self._config.streaming_debug_trace:
                event = replace(
                    event,
                    technical={
                        "stage": "CANONICAL",
                        "decision": "force_commit",
                        "astera_turn_id": event.segment_id,
                        "canonical": {"event_type": event.type.value, "text": event.text},
                        "committed_text": event.text or "",
                        "projected_text": projected_transcript.text,
                        "projected_text_clean": cleanup.cleaned_text,
                        "safe_cleanup": cleanup.as_dict(),
                        "projection_summary": projected_transcript.summary(),
                        "committed_projection": projected_transcript.as_dict(),
                        "state": {
                            "committed_before": current.text,
                            "committed_after": event.text,
                        },
                    },
                )
                self._append_incremental_projection_diagnostics(event.technical)
            self.metrics.increment("segments_committed")
            self.metrics.observe(
                "segment_commit_latency_ms",
                (time.perf_counter() - started_at) * 1000,
            )
        self._clear_streaming_segment()
        return event

    def _projection_sources(self):
        if isinstance(self._committed_projector, IncrementalCommittedTranscriptProjector):
            return self._committed_projector.active_sources()
        return self._provider_segments.ordered_committed()

    def _project_committed(self, state):
        if isinstance(self._committed_projector, IncrementalCommittedTranscriptProjector):
            return self._committed_projector.project(state)
        return self._committed_projector.project(self._provider_segments.ordered_committed())

    def _append_incremental_projection_diagnostics(self, technical: dict[str, object]) -> None:
        if not isinstance(self._committed_projector, IncrementalCommittedTranscriptProjector):
            return
        technical["projection_state"] = self._committed_projector.projection_state()
        technical["projection_metrics"] = self._committed_projector.projection_metrics()

    def technical_metrics_snapshot(self) -> dict[str, object]:
        snapshot = self.metrics.snapshot()
        if isinstance(self._committed_projector, IncrementalCommittedTranscriptProjector):
            snapshot["projection_metrics"] = self._committed_projector.projection_metrics()
            snapshot["projection_state"] = self._committed_projector.projection_state()
        return snapshot

    async def _flush_streaming(self) -> list[TranscriptEvent]:
        if self.session.closed:
            return []
        self.metrics.increment("turn_force_commit")
        await self._engine.finalize()
        events: list[TranscriptEvent] = []
        deadline = time.monotonic() + self._config.streaming_finalize_timeout_ms / 1000
        while time.monotonic() < deadline:
            drained = await self._drain_streaming_events()
            events.extend(drained)
            if self.session.active_segment_id is None:
                break
            await asyncio.sleep(0.01)
        if self.session.active_segment_id is not None:
            committed = self._commit_streaming_current()
            if committed is not None:
                events.append(committed)
        self._observe_events(events)
        return events

    async def _stop_streaming_engine(self) -> None:
        if self._stream_started:
            await self._engine.stop()
            self._stream_started = False

    def _clear_streaming_segment(self) -> None:
        self._lifecycle.clear(self.session)
        self.session.clear_active_segment()
        self._stream_state.reset(preserve_completed=True)
        self._word_timeline.reset()
        self._stream_provider = "local"
        self._last_partial_inference_at_ms = None
        self._last_transcribed_audio_end_ms = self.session.last_audio_timestamp_ms
        self._record_audio_lag()
        self._buffer.commit()
        reset = getattr(self._vad, "reset", None)
        if reset is not None:
            reset()

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

    def _observe_events(self, events: list[TranscriptEvent]) -> None:
        if self._observer is None:
            return
        for event in events:
            try:
                self._observer.observe(event)
            except Exception:
                self.metrics.increment("learning_observer_errors")
