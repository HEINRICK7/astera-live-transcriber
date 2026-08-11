import base64
import binascii
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from astera_live_transcriber.domain.audio import AudioChunk
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription.events import TranscriptEvent
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.presentation.api.dependencies import create_realtime_pipeline

router = APIRouter()


@router.websocket("/v1/realtime/transcription")
async def realtime_transcription(websocket: WebSocket) -> None:
    await websocket.accept()
    pipeline = None
    settings = Settings()
    sequence = 0
    timestamp_ms = 0

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if pipeline is None:
                    await websocket.send_json(
                        {"type": "error", "message": "session.create required"}
                    )
                    continue
                data = message["bytes"]
                duration_ms = max(1, round(len(data) / (2 * settings.audio_sample_rate) * 1000))
                chunk = AudioChunk(
                    data=data,
                    sequence=sequence,
                    timestamp_ms=timestamp_ms,
                    duration_ms=duration_ms,
                    sample_rate=settings.audio_sample_rate,
                )
                sequence += 1
                timestamp_ms += duration_ms
                await _send_events(websocket, await pipeline.process(chunk))
                continue

            text = message.get("text")
            if text is None:
                continue
            payload = json.loads(text)
            event_type = payload.get("type")
            if event_type == "session.create":
                if pipeline is not None:
                    await websocket.send_json(
                        {"type": "error", "message": "session already created"}
                    )
                    continue
                session_config = payload.get("session", {})
                model = session_config.get("model", settings.default_model)
                language = session_config.get("language", settings.default_language)
                session = TranscriptionSession(
                    id=f"sess_{uuid.uuid4().hex[:12]}",
                    model=model,
                    language=language,
                )
                pipeline = create_realtime_pipeline(session, settings)
                await websocket.send_json({"type": "session.created", "session_id": session.id})
            elif event_type == "audio.append":
                if pipeline is None:
                    await websocket.send_json(
                        {"type": "error", "message": "session.create required"}
                    )
                    continue
                data = _decode_audio(payload.get("audio"))
                sample_rate = int(payload.get("sample_rate", settings.audio_sample_rate))
                duration_ms = int(
                    payload.get(
                        "duration_ms",
                        max(1, round(len(data) / (2 * sample_rate) * 1000)),
                    )
                )
                chunk = AudioChunk(
                    data=data,
                    sequence=int(payload.get("sequence", sequence)),
                    timestamp_ms=int(payload.get("timestamp_ms", timestamp_ms)),
                    duration_ms=duration_ms,
                    sample_rate=sample_rate,
                    channels=int(payload.get("channels", 1)),
                )
                sequence = chunk.sequence + 1
                timestamp_ms = chunk.end_timestamp_ms
                await _send_events(websocket, await pipeline.process(chunk))
            elif event_type == "session.close":
                if pipeline is not None:
                    session_id = pipeline.session.id
                    await pipeline.close()
                    await websocket.send_json({"type": "session.closed", "session_id": session_id})
                await websocket.close()
                return
            else:
                await websocket.send_json({"type": "error", "message": "unsupported event type"})
    except (WebSocketDisconnect, json.JSONDecodeError, ValueError, binascii.Error):
        pass
    finally:
        if pipeline is not None and not pipeline.session.closed:
            await pipeline.close()


async def _send_events(websocket: WebSocket, events: list[TranscriptEvent]) -> None:
    for event in events:
        await websocket.send_json(_event_payload(event))


def _event_payload(event: TranscriptEvent) -> dict[str, object]:
    payload: dict[str, object] = {"type": event.type.value, "session_id": event.session_id}
    for field in (
        "segment_id",
        "revision",
        "text",
        "start_ms",
        "end_ms",
        "timestamp_ms",
        "silence_ms",
        "language",
        "confidence",
    ):
        value = getattr(event, field)
        if value is not None:
            payload[field] = value
    return payload


def _decode_audio(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("audio.append requires base64 audio")
    return base64.b64decode(value, validate=True)
