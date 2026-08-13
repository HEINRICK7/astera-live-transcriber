import io
import uuid
import wave

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from astera_live_transcriber.application.ports.speech_errors import SpeechEngineError
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.engines.parakeet.exceptions import ParakeetEngineError

router = APIRouter(prefix="/v1/audio", tags=["transcriptions"])


@router.post("/transcriptions")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    model: str | None = Form(None),
    language: str | None = Form(None),
) -> dict[str, object]:
    settings = request.app.state.settings
    requested_model = model or settings.default_model
    if requested_model not in {settings.default_model, "astera-stt-realtime-1"}:
        raise HTTPException(status_code=400, detail=f"unsupported model: {requested_model}")
    payload = await file.read()
    try:
        chunk = _wav_to_chunk(payload, settings.audio_sample_rate)
        normalized = AudioNormalizer().normalize(chunk)
        result = await request.app.state.engine_runtime.engine.transcribe(
            normalized.data,
            language=language or settings.default_language,
        )
    except (ValueError, wave.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ParakeetEngineError, SpeechEngineError) as exc:
        raise HTTPException(status_code=503, detail="transcription engine failed") from exc

    duration_ms = result.duration_ms or normalized.duration_ms
    segments = [
        {
            "id": f"seg_{uuid.uuid4().hex[:12]}",
            "text": result.text,
            "start_ms": 0,
            "end_ms": duration_ms,
            "confidence": result.confidence,
        }
    ] if result.text else []
    return {
        "id": f"tr_{uuid.uuid4().hex[:12]}",
        "object": "transcription",
        "model": requested_model,
        "language": language or settings.default_language,
        "text": result.text,
        "duration_ms": duration_ms,
        "segments": segments,
        "words": [
            {
                "word": word.word,
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "confidence": word.confidence,
            }
            for word in result.words
        ],
    }


def _wav_to_chunk(payload: bytes, sample_rate: int):
    from astera_live_transcriber.domain.audio import AudioChunk

    with wave.open(io.BytesIO(payload), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        source_rate = wav.getframerate()
        data = wav.readframes(wav.getnframes())
    if channels != 1 or width != 2 or source_rate != sample_rate:
        raise ValueError(f"WAV must be PCM16 mono at {sample_rate}Hz")
    duration_ms = round(len(data) / 2 / sample_rate * 1000)
    return AudioChunk(
        data=data,
        sequence=0,
        timestamp_ms=0,
        duration_ms=duration_ms,
        sample_rate=source_rate,
        channels=channels,
    )
