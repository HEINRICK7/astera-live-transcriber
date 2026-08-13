from collections.abc import Mapping

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingEngineEvent,
    StreamingEngineEventType,
    StreamingWord,
)


def map_xai_event(payload: Mapping[str, object]) -> StreamingEngineEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type == "transcript.created":
        return None
    if event_type == "error":
        return StreamingEngineEvent(
            type=StreamingEngineEventType.ERROR,
            provider="xai",
            error_code=_error_code(payload),
        )
    if event_type == "transcript.done":
        return _transcript_event(payload, StreamingEngineEventType.TRANSCRIPT_DONE)
    if event_type == "transcript.partial":
        return _transcript_event(payload, StreamingEngineEventType.PARTIAL)
    return None


def _transcript_event(
    payload: Mapping[str, object], event_type: StreamingEngineEventType
) -> StreamingEngineEvent:
    start_ms = _milliseconds(payload.get("start"))
    duration_ms = _milliseconds(payload.get("duration"))
    end_ms = _milliseconds(payload.get("end"))
    if end_ms is None and start_ms is not None and duration_ms is not None:
        end_ms = start_ms + duration_ms
    return StreamingEngineEvent(
        type=event_type,
        text=str(payload.get("text", "") or "").strip(),
        provider="xai",
        provider_event_id=_string(payload.get("id") or payload.get("event_id")),
        provider_response_id=_string(payload.get("response_id")),
        provider_item_id=_string(payload.get("item_id") or payload.get("transcript_id")),
        provider_previous_item_id=_string(payload.get("previous_item_id")),
        provider_turn_id=_string(payload.get("turn_id") or payload.get("segment_id")),
        provider_raw_payload=_safe_payload(payload),
        is_final=bool(
            payload.get("is_final", event_type is StreamingEngineEventType.TRANSCRIPT_DONE)
        ),
        speech_final=bool(
            payload.get("speech_final", event_type is StreamingEngineEventType.TRANSCRIPT_DONE)
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=_confidence(payload.get("confidence")),
        words=_words(payload.get("words")),
    )


def _words(value: object) -> tuple[StreamingWord, ...]:
    if not isinstance(value, list):
        return ()
    words: list[StreamingWord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        words.append(
            StreamingWord(
                text=text,
                start_ms=_milliseconds(item.get("start")),
                end_ms=_milliseconds(item.get("end")),
                speaker=_integer(item.get("speaker")),
                confidence=_confidence(item.get("confidence")),
            )
        )
    return tuple(words)


def _milliseconds(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, round(float(value) * 1000))
    except (TypeError, ValueError):
        return None


def _confidence(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _safe_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Keep provider diagnostics useful without allowing secrets to escape."""
    blocked = {"authorization", "api_key", "apikey", "token", "access_token", "secret"}

    def clean(value: object, key: str = "") -> object:
        if key.casefold() in blocked or any(
            part in key.casefold() for part in ("authorization", "api_key", "token")
        ):
            return "[redacted]"
        if isinstance(value, Mapping):
            return {
                str(child_key): clean(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return {str(key): clean(value, str(key)) for key, value in payload.items()}


def _error_code(payload: Mapping[str, object]) -> str:
    message = str(payload.get("message", "provider_error") or "provider_error").lower()
    if "auth" in message or "401" in message or "403" in message:
        return "speech_authentication_error"
    if "rate" in message or "429" in message:
        return "speech_rate_limit_error"
    return "speech_provider_error"
