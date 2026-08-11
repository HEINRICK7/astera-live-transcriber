import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from astera_live_transcriber.domain.audio import AudioStreamMode
from astera_live_transcriber.infrastructure.audio.file_session_manager import (
    FileSessionError,
    validate_file_metadata,
)

router = APIRouter(prefix="/v1/realtime", tags=["realtime-files"])


@router.post("/files", status_code=202)
async def create_file_stream(
    request: Request,
    file: UploadFile = File(...),
    model: str | None = Form(None),
    language: str | None = Form(None),
    mode: str = Form("realtime"),
) -> dict[str, object]:
    settings = request.app.state.settings
    requested_model = model or settings.default_model
    requested_language = language or settings.default_language
    if requested_model not in {settings.default_model, "astera-stt-realtime-1"}:
        raise HTTPException(status_code=400, detail=f"unsupported model: {requested_model}")
    try:
        stream_mode = AudioStreamMode(mode)
        validate_file_metadata(file.filename or "audio.wav", file.content_type)
    except (ValueError, FileSessionError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    temp_dir = Path(settings.file_temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "audio.wav").suffix.lower() or ".audio"
    fd, temp_name = tempfile.mkstemp(prefix="source-", suffix=suffix, dir=temp_dir)
    os.close(fd)
    path = Path(temp_name)
    size_bytes = 0
    try:
        with path.open("wb") as destination:
            while block := await file.read(1024 * 1024):
                destination.write(block)
                size_bytes += len(block)
        session = await request.app.state.file_sessions.create(
            path=path,
            filename=file.filename or path.name,
            mime_type=file.content_type,
            size_bytes=size_bytes,
            model=requested_model,
            language=requested_language,
            mode=stream_mode,
        )
    except FileSessionError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {
        "session_id": session.session.id,
        "status": "streaming",
        "source_type": "file",
        "stream_mode": stream_mode.value,
        "websocket": f"/v1/realtime/transcription/{session.session.id}",
    }
