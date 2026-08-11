from collections.abc import Mapping

from astera_live_transcriber.domain.transcription import TranscriptionResult, WordTimestamp


def map_language(language: str | None) -> str:
    if not language:
        return "pt-BR"
    return language.split("-", 1)[0].lower()


def _get(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_confidence(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, result))


def map_result(raw_result: object, language: str | None, duration_ms: int) -> TranscriptionResult:
    text = str(_get(raw_result, "text", "") or "").strip()
    confidence = _as_confidence(_get(raw_result, "confidence"))
    raw_words = _get(raw_result, "words", ()) or ()
    words: list[WordTimestamp] = []
    for raw_word in raw_words:
        word = str(_get(raw_word, "word", _get(raw_word, "text", "")) or "").strip()
        if not word:
            continue
        start_ms = round(float(_get(raw_word, "start", _get(raw_word, "start_ms", 0))) * 1000)
        end_ms = round(
            float(_get(raw_word, "end", _get(raw_word, "end_ms", start_ms / 1000))) * 1000
        )
        if _get(raw_word, "start_ms") is not None:
            start_ms = round(float(_get(raw_word, "start_ms")))
        if _get(raw_word, "end_ms") is not None:
            end_ms = round(float(_get(raw_word, "end_ms")))
        words.append(
            WordTimestamp(
                word=word,
                start_ms=max(0, start_ms),
                end_ms=max(start_ms, end_ms),
                confidence=_as_confidence(_get(raw_word, "confidence")),
            )
        )
    return TranscriptionResult(
        text=text,
        language=language or "pt-BR",
        duration_ms=duration_ms,
        confidence=confidence,
        words=tuple(words),
    )
