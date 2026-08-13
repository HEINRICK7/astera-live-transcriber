import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType

from .adaptation_engine import AdaptationEngine, AdaptationResult

logger = logging.getLogger(__name__)


class MetricsPort(Protocol):
    def increment(self, name: str, value: int = 1) -> None: ...
    def observe(self, name: str, value: float) -> None: ...
    def set_gauge(self, name: str, value: float) -> None: ...


@dataclass(frozen=True, slots=True)
class _QueuedObservation:
    priority: int
    event: TranscriptEvent


@dataclass(frozen=True, slots=True)
class AdaptiveMatchTelemetry:
    observed: str
    candidate: str | None
    ratio: float | None
    partial_ratio: float | None
    token_sort_ratio: float | None
    token_set_ratio: float | None
    wratio: float | None
    decision: str
    final_score: float | None
    risk_class: str | None
    event_type: str
    segment_id: str | None
    revision: int | None
    mode: str
    would_replace: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "observed": self.observed,
            "candidate": self.candidate,
            "ratio": self.ratio,
            "partial_ratio": self.partial_ratio,
            "token_sort_ratio": self.token_sort_ratio,
            "token_set_ratio": self.token_set_ratio,
            "wratio": self.wratio,
            "decision": self.decision,
            "final_score": self.final_score,
            "risk_class": self.risk_class,
            "event_type": self.event_type,
            "segment_id": self.segment_id,
            "revision": self.revision,
            "mode": self.mode,
            "would_replace": self.would_replace,
        }


class _BoundedObservationQueue:
    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(1, maxsize)
        self._items: deque[_QueuedObservation] = deque()
        self._ready = asyncio.Event()
        self.dropped = 0

    def put(self, item: _QueuedObservation) -> bool:
        if len(self._items) < self._maxsize:
            self._items.append(item)
            self._ready.set()
            return True
        lowest = min(range(len(self._items)), key=lambda index: self._items[index].priority)
        if self._items[lowest].priority >= item.priority:
            self.dropped += 1
            return False
        del self._items[lowest]
        self._items.append(item)
        self.dropped += 1
        self._ready.set()
        return True

    async def get(self) -> _QueuedObservation:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        highest = max(range(len(self._items)), key=lambda index: self._items[index].priority)
        item = self._items[highest]
        del self._items[highest]
        if not self._items:
            self._ready.clear()
        return item

    def __len__(self) -> int:
        return len(self._items)


class TranscriptionObserver:
    """Non-blocking bridge from canonical events to the intelligence worker."""

    def __init__(
        self,
        session_id: str,
        engine: AdaptationEngine,
        queue_size: int = 256,
        metrics: MetricsPort | None = None,
    ) -> None:
        self.session_id = session_id
        self._engine = engine
        self._queue = _BoundedObservationQueue(queue_size)
        self._metrics = metrics
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self.results: list[AdaptationResult] = []
        self.telemetry: list[AdaptiveMatchTelemetry] = []

    def observe(self, event: TranscriptEvent) -> None:
        if self._closed:
            return
        accepted = self._queue.put(_QueuedObservation(self._priority(event), event))
        self._metric_gauge("learning_queue_depth", len(self._queue))
        if not accepted:
            self._metric_increment("learning_events_dropped")
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        if self._worker is not None:
            await self._worker
            self._worker = None
        self._engine.close_session(self.session_id)

    async def _run(self) -> None:
        while self._queue:
            item = await self._queue.get()
            started = asyncio.get_running_loop().time()
            try:
                result = self._engine.observe(self.session_id, item.event)
                self.results.append(result)
                self._record_match_telemetry(item.event, result)
                self._metric_increment("learning_events_processed")
                self._metric_increment("fuzzy_candidates_generated", len(result.candidates))
                self._metric_increment(
                    "high_risk_candidates_blocked",
                    sum(candidate.risk_class.value == "high" for candidate in result.candidates),
                )
                metric = (
                    "normalizations_shadowed"
                    if result.mode == "shadow"
                    else "normalizations_applied"
                )
                self._metric_increment(metric, len(result.decisions))
            except Exception:
                self._metric_increment("learning_worker_errors")
            finally:
                self._metric_gauge("learning_queue_depth", len(self._queue))
                self._metric_observe(
                    "learning_worker_latency_ms",
                    (asyncio.get_running_loop().time() - started) * 1000,
                )

    def _record_match_telemetry(
        self,
        event: TranscriptEvent,
        result: AdaptationResult,
    ) -> None:
        decisions = {
            (decision.original.casefold(), decision.replacement.casefold())
            for decision in result.decisions
        }
        if not result.candidates:
            self._emit_match_telemetry(
                AdaptiveMatchTelemetry(
                    observed=event.text or "",
                    candidate=None,
                    ratio=None,
                    partial_ratio=None,
                    token_sort_ratio=None,
                    token_set_ratio=None,
                    wratio=None,
                    decision="ignored",
                    final_score=None,
                    risk_class=None,
                    event_type=event.type.value,
                    segment_id=event.segment_id,
                    revision=event.revision,
                    mode=result.mode,
                    would_replace=False,
                )
            )
            return
        for candidate in result.candidates:
            scores = _rapidfuzz_scores(candidate.observed, candidate.canonical)
            decision = (
                "promoted"
                if (candidate.observed.casefold(), candidate.canonical.casefold()) in decisions
                else "candidate"
            )
            self._emit_match_telemetry(
                AdaptiveMatchTelemetry(
                    observed=candidate.observed,
                    candidate=candidate.canonical,
                    ratio=scores["ratio"],
                    partial_ratio=scores["partial_ratio"],
                    token_sort_ratio=scores["token_sort_ratio"],
                    token_set_ratio=scores["token_set_ratio"],
                    wratio=scores["wratio"],
                    decision=decision,
                    final_score=candidate.final_score,
                    risk_class=candidate.risk_class.value,
                    event_type=event.type.value,
                    segment_id=event.segment_id,
                    revision=event.revision,
                    mode=result.mode,
                    would_replace=(
                        (candidate.observed.casefold(), candidate.canonical.casefold()) in decisions
                    ),
                )
            )

    def _emit_match_telemetry(self, telemetry: AdaptiveMatchTelemetry) -> None:
        self.telemetry.append(telemetry)
        logger.info("[adaptive.match] %s", json.dumps(telemetry.as_dict(), ensure_ascii=False))

    def _metric_increment(self, name: str, value: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.increment(name, value)

    def _metric_observe(self, name: str, value: float) -> None:
        if self._metrics is not None:
            self._metrics.observe(name, value)

    def _metric_gauge(self, name: str, value: float) -> None:
        if self._metrics is not None:
            self._metrics.set_gauge(name, value)

    @staticmethod
    def _priority(event: TranscriptEvent) -> int:
        if event.type is TranscriptEventType.COMMITTED:
            return 3
        if event.type is TranscriptEventType.REVISED:
            return 2
        if event.type is TranscriptEventType.PARTIAL:
            return 1 if event.text and len(event.text.strip()) > 2 else 0
        return 0


def _rapidfuzz_scores(observed: str, candidate: str) -> dict[str, float]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return {
            "ratio": 0.0,
            "partial_ratio": 0.0,
            "token_sort_ratio": 0.0,
            "token_set_ratio": 0.0,
            "wratio": 0.0,
        }
    return {
        "ratio": round(float(fuzz.ratio(observed, candidate)), 3),
        "partial_ratio": round(float(fuzz.partial_ratio(observed, candidate)), 3),
        "token_sort_ratio": round(float(fuzz.token_sort_ratio(observed, candidate)), 3),
        "token_set_ratio": round(float(fuzz.token_set_ratio(observed, candidate)), 3),
        "wratio": round(float(fuzz.WRatio(observed, candidate)), 3),
    }
