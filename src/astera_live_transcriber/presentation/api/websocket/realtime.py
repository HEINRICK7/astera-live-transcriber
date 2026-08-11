import base64
import binascii
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from astera_live_transcriber.domain.audio import AudioChunk
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription.events import (
    TranscriptEvent,
    TranscriptEventType,
)
from astera_live_transcriber.infrastructure.audio.file_session_manager import FileSessionError
from astera_live_transcriber.infrastructure.engines.parakeet.exceptions import ParakeetEngineError
from astera_live_transcriber.presentation.api.dependencies import create_realtime_pipeline

router = APIRouter()


@router.websocket("/v1/realtime/transcription/{session_id}")
async def file_realtime_transcription(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    manager = websocket.app.state.file_sessions
    cancel_on_disconnect = websocket.app.state.settings.file_cancel_on_disconnect
    file_session = None
    try:
        file_session = await manager.attach(session_id)
        await websocket.send_json(
            {
                "type": "session.created",
                "session_id": session_id,
                "source_type": "file",
                "stream_mode": file_session.session.stream_mode,
                "source_format": file_session.session.source_format,
            }
        )
        while True:
            event = await file_session.queue.get()
            if event is None:
                break
            await websocket.send_json(_event_payload(event))
            if event.type is TranscriptEventType.ERROR:
                break
            if event.type is TranscriptEventType.SESSION_COMPLETED:
                break
    except (FileSessionError, WebSocketDisconnect):
        if cancel_on_disconnect and file_session is not None and not file_session.completed:
            await manager.cancel(session_id)
        if file_session is None:
            try:
                await websocket.send_json({"type": "error", "code": "session_not_found"})
            except WebSocketDisconnect:
                pass
    finally:
        if cancel_on_disconnect and file_session is not None and not file_session.completed:
            await manager.cancel(session_id)


@router.websocket("/v1/realtime/transcription")
async def realtime_transcription(websocket: WebSocket) -> None:
    await websocket.accept()
    pipeline = None
    settings = websocket.app.state.settings
    runtime = websocket.app.state.engine_runtime
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
                pipeline = create_realtime_pipeline(session, settings, runtime)
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
                    await _send_events(websocket, await pipeline.flush())
                    await pipeline.close()
                    await websocket.send_json({"type": "session.closed", "session_id": session_id})
                await websocket.close()
                return
            else:
                await websocket.send_json({"type": "error", "message": "unsupported event type"})
    except ParakeetEngineError:
        if pipeline is not None:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "transcription_engine_error",
                    "session_id": pipeline.session.id,
                }
            )
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
        "error_code",
    ):
        value = getattr(event, field)
        if value is not None:
            payload[field] = value
    return payload


def _decode_audio(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("audio.append requires base64 audio")
    return base64.b64decode(value, validate=True)
