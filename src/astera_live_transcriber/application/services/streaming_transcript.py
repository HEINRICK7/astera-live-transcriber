import re
from dataclasses import dataclass

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingEngineEvent,
    StreamingEngineEventType,
)
from astera_live_transcriber.domain.transcription import WordTimestamp

from .unstable_hypothesis import UnstableHypothesisReconciler


@dataclass(frozen=True, slots=True)
class StreamingTranscriptUpdate:
    text: str
    is_final: bool
    speech_final: bool
    start_ms: int | None
    end_ms: int | None
    language: str | None
    confidence: float | None
    words: tuple[WordTimestamp, ...]


class StreamingTranscriptState:
    """Projects provider text into Astera events, with a temporal path upstream."""

    _MAX_COMPLETED_SEGMENTS = 100
    _MIN_REPLAY_TOKENS = 2

    def __init__(self) -> None:
        self._unstable_text = ""
        self._interim_text = ""
        self._last_completed_text = ""
        self._completed_segments: list[str] = []
        self._unstable_reconciler = UnstableHypothesisReconciler()

    def apply(self, event: StreamingEngineEvent) -> StreamingTranscriptUpdate | None:
        raw_text = event.text.strip()
        if not raw_text:
            return None

        incoming = self._unstable_reconciler.collapse_repeated_blocks(raw_text)
        incoming = self._remove_committed_replay(incoming)
        if not incoming:
            if not self._unstable_text or not self._is_terminal(event):
                return None
            text = self._unstable_text
        else:
            text = self._unstable_reconciler.reconcile(self._unstable_text, incoming)

        if not event.is_final:
            self._interim_text = incoming
        else:
            self._interim_text = ""

        speech_final = self._is_terminal(event)
        if speech_final:
            self.remember_completed(text)
            self._unstable_text = ""
        else:
            self._unstable_text = text

        return StreamingTranscriptUpdate(
            text=text,
            is_final=event.is_final,
            speech_final=speech_final,
            start_ms=event.start_ms,
            end_ms=event.end_ms,
            language=event.language,
            confidence=event.confidence,
            words=tuple(
                WordTimestamp(
                    word=word.text,
                    start_ms=word.start_ms or 0,
                    end_ms=max(word.start_ms or 0, word.end_ms or word.start_ms or 0),
                    confidence=word.confidence,
                )
                for word in event.words
                if word.text
            ),
        )

    def apply_authoritative(self, event: StreamingEngineEvent) -> StreamingTranscriptUpdate | None:
        """Accept a provider commit as a complete snapshot for this segment."""
        text = event.text.strip()
        if not text:
            return None
        self._unstable_text = ""
        self._interim_text = ""
        self.remember_completed(text)
        return self._build_update(text, event)

    def apply_provider_projection(
        self,
        event: StreamingEngineEvent,
        text: str,
    ) -> StreamingTranscriptUpdate | None:
        """Publish a projection built from provider word timestamps."""
        if not text.strip():
            return None
        speech_final = self._is_terminal(event)
        if speech_final:
            self.remember_completed(text)
            self._unstable_text = ""
        else:
            self._unstable_text = text
        return self._build_update(text, event)

    @staticmethod
    def _build_update(text: str, event: StreamingEngineEvent) -> StreamingTranscriptUpdate:
        return StreamingTranscriptUpdate(
            text=text,
            is_final=event.is_final,
            speech_final=event.speech_final,
            start_ms=event.start_ms,
            end_ms=event.end_ms,
            language=event.language,
            confidence=event.confidence,
            words=tuple(
                WordTimestamp(
                    word=word.text,
                    start_ms=word.start_ms or 0,
                    end_ms=max(word.start_ms or 0, word.end_ms or word.start_ms or 0),
                    confidence=word.confidence,
                )
                for word in event.words
                if word.text
            ),
        )

    @property
    def unstable_text(self) -> str:
        return self._unstable_text

    def remember_completed(self, text: str) -> None:
        completed = text.strip()
        if not completed:
            return
        self._last_completed_text = completed
        if not self._completed_segments or self._completed_segments[-1] != completed:
            self._completed_segments.append(completed)
            self._completed_segments = self._completed_segments[-self._MAX_COMPLETED_SEGMENTS :]

    def reset(self, preserve_completed: bool = False) -> None:
        self._unstable_text = ""
        self._interim_text = ""
        if not preserve_completed:
            self._last_completed_text = ""
            self._completed_segments = []

    def _remove_committed_replay(self, text: str) -> str:
        remaining = text
        while remaining:
            best_overlap = 0
            for completed in self._completed_segments:
                best_overlap = max(
                    best_overlap,
                    self._best_history_overlap(completed, remaining),
                )
            if best_overlap == 0:
                break
            remaining = " ".join(remaining.split()[best_overlap:]).lstrip(
                " \t.,;:!?-"
            )
        return remaining

    def _best_history_overlap(self, completed: str, hypothesis: str) -> int:
        completed_tokens = self._normalized_tokens(completed)
        hypothesis_tokens = self._normalized_tokens(hypothesis)
        if not completed_tokens or not hypothesis_tokens:
            return 0

        maximum = min(len(completed_tokens), len(hypothesis_tokens))
        best = 0
        for start in range(len(completed_tokens)):
            maximum_from_start = min(maximum, len(completed_tokens) - start)
            count = 0
            while (
                count < maximum_from_start
                and completed_tokens[start + count] == hypothesis_tokens[count]
            ):
                count += 1
            if count >= self._MIN_REPLAY_TOKENS:
                best = max(best, count)
        return best

    @staticmethod
    def _normalized_tokens(text: str) -> list[str]:
        return [
            re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())
            for token in text.split()
            if re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())
        ]

    @staticmethod
    def _is_terminal(event: StreamingEngineEvent) -> bool:
        return event.speech_final or event.type is StreamingEngineEventType.TRANSCRIPT_DONE
