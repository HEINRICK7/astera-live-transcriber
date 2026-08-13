from dataclasses import dataclass

from astera_live_transcriber.application.ports.transcription_engine import StreamingWord


@dataclass(frozen=True, slots=True)
class TimelineWord:
    text: str
    start_ms: int
    end_ms: int
    speaker: int | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class WordTimelineChange:
    operation: str
    incoming: tuple[TimelineWord, ...]
    removed: tuple[TimelineWord, ...]
    before: tuple[TimelineWord, ...]
    after: tuple[TimelineWord, ...]


class WordTimelineStore:
    """Temporal source of truth for provider words with timestamps."""

    def __init__(self) -> None:
        self._words: list[TimelineWord] = []
        self._last_change: WordTimelineChange | None = None

    def upsert(self, words: tuple[StreamingWord, ...]) -> bool:
        before = tuple(self._words)
        incoming = [
            TimelineWord(
                text=word.text,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                speaker=word.speaker,
                confidence=word.confidence,
            )
            for word in words
            if word.text and word.start_ms is not None and word.end_ms is not None
            and word.end_ms >= word.start_ms
        ]
        if not incoming:
            self._last_change = None
            return False
        removed: list[TimelineWord] = []
        for word in incoming:
            removed.extend(
                existing for existing in self._words if self._overlaps(existing, word)
            )
            self._words = [
                existing
                for existing in self._words
                if not self._overlaps(existing, word)
            ]
            self._words.append(word)
        self._words.sort(key=lambda word: (word.start_ms, word.end_ms, word.text))
        self._last_change = WordTimelineChange(
            operation="replace_overlap" if removed else "append_new_span",
            incoming=tuple(incoming),
            removed=tuple(dict.fromkeys(removed)),
            before=before,
            after=tuple(self._words),
        )
        return True

    @property
    def last_change(self) -> WordTimelineChange | None:
        return self._last_change

    def words(self) -> tuple[StreamingWord, ...]:
        return tuple(
            StreamingWord(
                text=word.text,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                speaker=word.speaker,
                confidence=word.confidence,
            )
            for word in self._words
        )

    def text(self) -> str:
        output = ""
        for word in self._words:
            if not output:
                output = word.text
            elif word.text in {".", ",", "!", "?", ";", ":", "%", ")"}:
                output += word.text
            elif output.endswith(("(", "[")):
                output += word.text
            else:
                output += f" {word.text}"
        return output

    def start_ms(self) -> int | None:
        return self._words[0].start_ms if self._words else None

    def end_ms(self) -> int | None:
        return self._words[-1].end_ms if self._words else None

    def reset(self) -> None:
        self._words.clear()

    @staticmethod
    def _overlaps(left: TimelineWord, right: TimelineWord) -> bool:
        return left.start_ms < right.end_ms and right.start_ms < left.end_ms
