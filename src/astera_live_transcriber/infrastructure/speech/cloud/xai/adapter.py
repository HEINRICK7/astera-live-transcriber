import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from typing import Any

from astera_live_transcriber.application.ports.speech_capabilities import (
    SpeechProviderCapabilities,
)
from astera_live_transcriber.application.ports.speech_errors import (
    SpeechAuthenticationError,
    SpeechConnectionError,
    SpeechEngineError,
    SpeechProviderUnavailableError,
    SpeechRateLimitError,
)
from astera_live_transcriber.application.ports.transcription_engine import (
    RealtimeTranscriptionEnginePort,
    StreamingConfig,
    StreamingEngineEvent,
    StreamingEngineEventType,
    TranscriptionContext,
    TranscriptionEnginePort,
)
from astera_live_transcriber.domain.transcription import TranscriptionResult, WordTimestamp
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics

from .config import XaiConfig
from .mapper import map_xai_event

logger = logging.getLogger(__name__)


def _trace_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class XaiStreamingSpeechEngine(TranscriptionEnginePort, RealtimeTranscriptionEnginePort):
    """xAI STT WebSocket adapter; no xAI concepts leave this module."""

    def __init__(
        self,
        config: XaiConfig,
        metrics: PipelineMetrics | None = None,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._metrics = metrics or PipelineMetrics()
        self._connect_factory = connect_factory
        self._connect_context: Any | None = None
        self._websocket: Any | None = None
        self._audio_queue: asyncio.Queue[bytes] | None = None
        self._event_queue: asyncio.Queue[StreamingEngineEvent] = asyncio.Queue(
            maxsize=config.audio_queue_size
        )
        self._sender_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._session_config: StreamingConfig | None = None
        self._started = False
        self._stopping = False
        self._finalizing = False
        self._remote_done = asyncio.Event()
        self._connection_ready = asyncio.Event()
        self._reconnect_lock = asyncio.Lock()
        self._queued_audio_ms = 0
        self._sample_rate = 16_000
        self._channels = 1
        self._trace_sequence = 0
        self._stall_started_at: float | None = None
        self._inflight_audio_ms = 0

    @property
    def capabilities(self) -> SpeechProviderCapabilities:
        return SpeechProviderCapabilities(
            streaming=True,
            partial_results=True,
            timestamps=True,
            word_timestamps=True,
            diarization=True,
            keyterms=True,
            multichannel=True,
            manual_finalize=True,
        )

    async def start(self, config: StreamingConfig) -> None:
        if self._started:
            return
        self._validate_config(config)
        self._session_config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._queued_audio_ms = 0
        self._trace_sequence = 0
        self._stall_started_at = None
        self._inflight_audio_ms = 0
        self._audio_queue = asyncio.Queue(maxsize=self._config.audio_queue_size)
        self._stopping = False
        self._finalizing = False
        self._remote_done.clear()
        await self._connect_with_retry()
        self._started = True
        self._sender_task = asyncio.create_task(self._sender_loop())
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._metrics.increment("speech_connection_total")

    async def push_audio(self, chunk: bytes) -> None:
        if not self._started or self._audio_queue is None:
            raise SpeechConnectionError("xAI streaming session is not started")
        if not chunk:
            return
        chunk_duration_ms = round(
            len(chunk) / (2 * self._sample_rate * self._channels) * 1000
        )
        queue = self._audio_queue
        stall_started = self._stall_started_at
        wait_started = time.monotonic()
        while True:
            self._observe_queue(queue)
            try:
                await asyncio.wait_for(
                    queue.put(chunk),
                    timeout=self._config.provider_stall_timeout_ms / 1000,
                )
                break
            except TimeoutError:
                if stall_started is None:
                    stall_started = time.monotonic()
                    self._stall_started_at = stall_started
                self._metrics.increment("queue_full_events")
                self._metrics.increment("speech_provider_stalled_total")
                self._metrics.set_gauge("provider_stalled", 1)
                await self._reconnect_after_stall()
        if stall_started is not None:
            stall_duration_ms = (time.monotonic() - stall_started) * 1000
            self._metrics.observe("stall_duration_ms", stall_duration_ms)
            self._record_metric_extrema("stall_duration_ms", stall_duration_ms)
            self._metrics.set_gauge("provider_stalled", 0)
            self._stall_started_at = None
        self._queued_audio_ms += chunk_duration_ms
        producer_wait_ms = (time.monotonic() - wait_started) * 1000
        self._metrics.observe("producer_wait_ms", producer_wait_ms)
        self._record_metric_extrema("producer_wait_ms", producer_wait_ms)
        self._observe_queue(queue)
        self._observe_audio_backlog()

    def _observe_queue(self, queue: asyncio.Queue[bytes]) -> None:
        depth = queue.qsize()
        capacity = queue.maxsize
        utilization = depth / capacity if capacity else 0.0
        self._metrics.set_gauge("audio_queue_size", depth)
        self._metrics.set_gauge("audio_queue_capacity", capacity)
        self._metrics.set_gauge("queue_utilization_pct", utilization * 100)
        self._set_peak_gauge("queue_utilization_peak_pct", utilization * 100)
        self._metrics.set_gauge("speech_audio_queue_depth", depth)
        self._set_peak_gauge("audio_queue_peak_size", depth)
        self._metrics.set_gauge("dropped_audio_ms", 0)
        if utilization >= self._config.queue_high_water_mark:
            self._metrics.increment("queue_high_water_events")
            self._metrics.set_gauge("queue_high_water_mark", depth)

    def _observe_audio_backlog(self) -> None:
        backlog = max(0, self._queued_audio_ms - self._inflight_audio_ms)
        self._metrics.set_gauge("backlog_audio_ms", backlog)
        self._set_peak_gauge("backlog_audio_ms_max", backlog)
        self._metrics.set_gauge("inflight_audio_ms", self._inflight_audio_ms)
        self._set_peak_gauge("inflight_audio_ms_max", self._inflight_audio_ms)

    def _set_peak_gauge(self, name: str, value: float) -> None:
        self._metrics.set_gauge(name, max(value, self._metrics.gauges.get(name, 0)))

    def _record_metric_extrema(self, name: str, value: float) -> None:
        values = self._metrics.observations.get(name, [])
        self._metrics.set_gauge(f"{name}_total", sum(values))
        self._metrics.set_gauge(f"{name}_max", max(values, default=value))

    async def events(self) -> AsyncIterator[StreamingEngineEvent]:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._metrics.set_gauge("speech_event_queue_depth", self._event_queue.qsize())
            yield event

    async def finalize(self) -> None:
        if not self._started or self._websocket is None:
            return
        if self._audio_queue is not None:
            deadline = time.monotonic() + 5
            while self._audio_queue.qsize() and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
        self._finalizing = True
        await self._websocket.send(json.dumps({"type": "audio.done"}))

    async def stop(self) -> None:
        if not self._started and self._websocket is None:
            return
        self._stopping = True
        self._started = False
        tasks = [task for task in (self._sender_task, self._reader_task) if task is not None]
        self._sender_task = None
        self._reader_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        websocket = self._websocket
        context = self._connect_context
        self._websocket = None
        self._connect_context = None
        self._connection_ready.clear()
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                logger.debug("xai_websocket_close_failed", exc_info=True)
        if context is not None:
            try:
                await context.__aexit__(None, None, None)
            except Exception:
                logger.debug("xai_websocket_context_close_failed", exc_info=True)
        self._audio_queue = None
        self._session_config = None

    async def close(self) -> None:
        await self.stop()

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        context: TranscriptionContext | None = None,
    ) -> TranscriptionResult:
        del context
        config = StreamingConfig(
            session_id="batch",
            language=language or "pt-BR",
            model="astera-stt-1",
            sample_rate=16_000,
        )
        await self.start(config)
        try:
            await self.push_audio(audio)
            await self.finalize()
            result_event = await self._wait_for_done()
            duration_ms = round(len(audio) / 2 / config.sample_rate * 1000)
            return TranscriptionResult(
                text=result_event.text,
                language=language or "pt-BR",
                duration_ms=duration_ms,
                words=tuple(
                    WordTimestamp(
                        word=word.text,
                        start_ms=word.start_ms or 0,
                        end_ms=max(word.start_ms or 0, word.end_ms or word.start_ms or 0),
                        confidence=word.confidence,
                    )
                    for word in result_event.words
                    if word.text
                ),
            )
        finally:
            await self.stop()

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[Any]:
        await self.start(config)
        try:
            async for chunk in audio:
                await self.push_audio(chunk)
                async for event in self.events():
                    yield event
            await self.finalize()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                emitted = False
                async for event in self.events():
                    emitted = True
                    yield event
                if self._remote_done.is_set() and not emitted:
                    break
                await asyncio.sleep(0.01)
        finally:
            await self.stop()

    async def _wait_for_done(self) -> StreamingEngineEvent:
        deadline = time.monotonic() + 15
        last_text = ""
        while time.monotonic() < deadline:
            async for event in self.events():
                if event.type is StreamingEngineEventType.ERROR:
                    raise self._error_from_code(event.error_code)
                if event.text:
                    last_text = event.text
                if event.type is StreamingEngineEventType.TRANSCRIPT_DONE:
                    return event
            if self._remote_done.is_set() and last_text:
                return StreamingEngineEvent(
                    type=StreamingEngineEventType.TRANSCRIPT_DONE,
                    text=last_text,
                    is_final=True,
                    speech_final=True,
                    provider="xai",
                )
            await asyncio.sleep(0.01)
        raise SpeechConnectionError("timed out waiting for xAI transcript.done")

    async def _connect_with_retry(self) -> None:
        for attempt in range(self._config.max_reconnect_attempts + 1):
            try:
                await self._connect_once()
                return
            except SpeechAuthenticationError:
                raise
            except SpeechRateLimitError:
                raise
            except SpeechEngineError:
                if attempt >= self._config.max_reconnect_attempts:
                    raise
                await asyncio.sleep(min(4, 0.25 * (2**attempt)))
        raise SpeechConnectionError("unable to connect to xAI")

    async def _connect_once(self) -> None:
        if self._session_config is None:
            raise SpeechConnectionError("xAI session configuration is missing")
        factory = self._connect_factory or self._default_connect_factory()
        url = self._config.url(
            sample_rate=self._session_config.sample_rate,
            channels=self._session_config.channels,
            interim_results=self._session_config.interim_results,
            language=self._session_config.language,
            diarization=self._session_config.diarization,
            keyterms=self._session_config.keyterms,
        )
        context = factory(
            url,
            additional_headers={"Authorization": f"Bearer {self._config.api_key}"},
            open_timeout=self._config.connect_timeout_ms / 1000,
        )
        self._connect_context = context
        try:
            websocket = await context.__aenter__()
            ready = json.loads(await websocket.recv())
        except Exception as exc:
            try:
                await context.__aexit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            self._connect_context = None
            raise self._classify_connection_error(exc) from exc
        if ready.get("type") == "error":
            await websocket.close()
            self._connect_context = None
            raise self._classify_message_error(ready)
        if ready.get("type") != "transcript.created":
            await websocket.close()
            self._connect_context = None
            raise SpeechConnectionError("xAI did not send transcript.created")
        self._websocket = websocket
        self._connection_ready.set()

    async def _sender_loop(self) -> None:
        assert self._audio_queue is not None
        while not self._stopping:
            chunk = await self._audio_queue.get()
            chunk_duration_ms = round(
                len(chunk) / (2 * self._sample_rate * self._channels) * 1000
            )
            self._inflight_audio_ms += chunk_duration_ms
            self._observe_audio_backlog()
            try:
                while self._websocket is None and not self._stopping:
                    await self._connection_ready.wait()
                if self._websocket is None:
                    continue
                send_started = time.monotonic()
                await asyncio.wait_for(
                    self._websocket.send(chunk),
                    timeout=self._config.provider_stall_timeout_ms / 1000,
                )
                provider_send_latency_ms = (time.monotonic() - send_started) * 1000
                self._metrics.observe(
                    "provider_send_latency_ms", provider_send_latency_ms
                )
                self._record_metric_extrema(
                    "provider_send_latency_ms", provider_send_latency_ms
                )
                self._metrics.increment("provider_drain_rate")
                self._metrics.increment("speech_audio_chunks_sent")
            except Exception as exc:
                self._metrics.increment("speech_send_errors_total")
                if not self._stopping and not self._finalizing:
                    self._connection_ready.clear()
                    self._websocket = None
                    try:
                        await self._reconnect()
                    except SpeechEngineError as reconnect_error:
                        await self._emit_error(reconnect_error)
                else:
                    await self._emit_error(self._classify_connection_error(exc))
            finally:
                self._inflight_audio_ms = max(
                    0, self._inflight_audio_ms - chunk_duration_ms
                )
                self._queued_audio_ms = max(0, self._queued_audio_ms - chunk_duration_ms)
                self._observe_queue(self._audio_queue)
                self._observe_audio_backlog()

    async def _reader_loop(self) -> None:
        while not self._stopping and self._websocket is not None:
            websocket = self._websocket
            try:
                raw = await websocket.recv()
                payload = json.loads(raw) if isinstance(raw, str | bytes) else raw
                if isinstance(payload, Mapping):
                    self._trace_sequence += 1
                    sequence = self._trace_sequence
                    self._trace_raw_payload(payload, sequence)
                    event = map_xai_event(payload)
                    if event is not None:
                        event = replace(event, provider_sequence=sequence)
                    self._trace_mapped_event(event, sequence)
                    if event is not None:
                        if event.type is StreamingEngineEventType.ERROR:
                            await self._emit(event)
                        else:
                            await self._emit(event)
                        if event.type is StreamingEngineEventType.TRANSCRIPT_DONE:
                            self._remote_done.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stopping or self._finalizing:
                    self._remote_done.set()
                    return
                self._connection_ready.clear()
                self._websocket = None
                try:
                    await self._reconnect()
                except SpeechEngineError as reconnect_error:
                    await self._emit_error(reconnect_error)
                    return

    def _trace_raw_payload(self, payload: Mapping[str, object], sequence: int) -> None:
        if not self._config.debug_trace:
            return
        event_type = str(payload.get("type", ""))
        if not event_type.startswith("transcript.") and event_type != "error":
            return
        logger.debug(
            "xai_trace seq=%s stage=RAW_XAI event_type=%s payload=%s",
            sequence,
            event_type,
            _trace_json(payload),
        )

    def _trace_mapped_event(
        self, event: StreamingEngineEvent | None, sequence: int
    ) -> None:
        if not self._config.debug_trace:
            return
        if event is None:
            logger.debug(
                "xai_trace seq=%s stage=MAPPED result=dropped",
                sequence,
            )
            return
        logger.debug(
            "xai_trace seq=%s stage=MAPPED provider=%s provider_event_id=%s "
            "response_id=%s "
            "item_id=%s previous_item_id=%s turn_id=%s type=%s text=%r "
            "is_final=%s speech_final=%s speakers=%s",
            sequence,
            event.provider,
            event.provider_event_id,
            event.provider_response_id,
            event.provider_item_id,
            event.provider_previous_item_id,
            event.provider_turn_id,
            event.type,
            event.text,
            event.is_final,
            event.speech_final,
            tuple(word.speaker for word in event.words if word.speaker is not None),
        )

    async def _reconnect(self) -> None:
        async with self._reconnect_lock:
            if self._stopping or self._finalizing:
                return
            old_context = self._connect_context
            old_websocket = self._websocket
            self._connect_context = None
            self._websocket = None
            self._connection_ready.clear()
            if old_websocket is not None:
                try:
                    await old_websocket.close()
                except Exception:
                    pass
            if old_context is not None:
                try:
                    await old_context.__aexit__(None, None, None)
                except Exception:
                    pass
            self._metrics.increment("speech_reconnect_total")
            self._metrics.increment("reconnect_count")
            await self._connect_with_retry()

    async def _reconnect_after_stall(self) -> None:
        if self._stopping or self._finalizing:
            return
        await self._reconnect()

    async def _emit(self, event: StreamingEngineEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self._metrics.increment("speech_event_queue_dropped_total")

    async def _emit_error(self, error: SpeechEngineError) -> None:
        await self._emit(
            StreamingEngineEvent(
                type=StreamingEngineEventType.ERROR,
                provider="xai",
                error_code=self._code_for_error(error),
            )
        )

    @staticmethod
    def _validate_config(config: StreamingConfig) -> None:
        if config.sample_rate not in {8_000, 16_000, 22_050, 24_000, 44_100, 48_000}:
            raise SpeechConnectionError("xAI does not support this sample rate")
        if not 1 <= config.channels <= 8:
            raise SpeechConnectionError("xAI channels must be between 1 and 8")

    @staticmethod
    def _default_connect_factory() -> Callable[..., Any]:
        from websockets.asyncio.client import connect

        return connect

    @staticmethod
    def _classify_connection_error(exc: Exception) -> SpeechEngineError:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            return SpeechAuthenticationError("xAI authentication failed")
        if status == 429:
            return SpeechRateLimitError("xAI rate limit exceeded")
        if status is not None and status >= 500:
            return SpeechProviderUnavailableError("xAI is unavailable")
        return SpeechConnectionError("xAI WebSocket connection failed")

    @staticmethod
    def _classify_message_error(payload: Mapping[str, object]) -> SpeechEngineError:
        message = str(payload.get("message", "xAI rejected the session"))
        lowered = message.lower()
        if "auth" in lowered or "401" in lowered or "403" in lowered:
            return SpeechAuthenticationError(message)
        if "429" in lowered or "rate" in lowered:
            return SpeechRateLimitError(message)
        return SpeechProviderUnavailableError(message)

    @staticmethod
    def _error_from_code(code: str | None) -> SpeechEngineError:
        if code == "speech_authentication_error":
            return SpeechAuthenticationError("xAI authentication failed")
        if code == "speech_rate_limit_error":
            return SpeechRateLimitError("xAI rate limit exceeded")
        return SpeechEngineError("xAI provider error")

    @staticmethod
    def _code_for_error(error: SpeechEngineError) -> str:
        if isinstance(error, SpeechAuthenticationError):
            return "speech_authentication_error"
        if isinstance(error, SpeechRateLimitError):
            return "speech_rate_limit_error"
        if isinstance(error, SpeechConnectionError):
            return "speech_connection_error"
        return "speech_provider_error"
