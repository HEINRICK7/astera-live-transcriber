import io
import shutil
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.runtime import EngineRuntime
from astera_live_transcriber.presentation.api.app import create_app


class ProgressiveEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, audio: bytes, language: str | None = None, context=None):
        del audio, context
        self.calls += 1
        texts = ["Eu comecei", "Eu comecei a sentir", "Eu comecei a sentir uma dor"]
        return TranscriptionResult(
            text=texts[min(self.calls - 1, len(texts) - 1)],
            language=language or "pt-BR",
            duration_ms=100,
        )


def make_wav(duration_ms: int = 600, amplitude: int = 20_000) -> bytes:
    payload = io.BytesIO()
    with wave.open(payload, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(struct.pack(f"<{duration_ms * 16}h", *([amplitude] * (duration_ms * 16))))
    return payload.getvalue()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_file_stream_publishes_partial_before_audio_completed() -> None:
    settings = Settings(
        audio_chunk_ms=100,
        partial_interval_ms=0,
        min_speech_ms=0,
        engine="noop",
    )
    engine = ProgressiveEngine()
    runtime = EngineRuntime(settings=settings, engine=engine)
    client = TestClient(create_app(settings, runtime))

    with client:
        response = client.post(
            "/v1/realtime/files",
            files={"file": ("consulta.wav", make_wav(), "audio/wav")},
            data={"mode": "accelerated", "language": "pt-BR"},
        )
        assert response.status_code == 202
        session_id = response.json()["session_id"]

        events = []
        with client.websocket_connect(f"/v1/realtime/transcription/{session_id}") as websocket:
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] in {"session.completed", "error"}:
                    break

    event_types = [event["type"] for event in events]
    first_transcript = next(
        index
        for index, event_type in enumerate(event_types)
        if event_type in {"transcript.partial", "transcript.revised"}
    )
    assert first_transcript < event_types.index("audio.completed")
    assert event_types.count("transcript.partial") + event_types.count("transcript.revised") >= 2
    assert "transcript.committed" in event_types
    assert engine.calls >= 3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_invalid_mp3_emits_decode_error_without_crashing_app() -> None:
    client = TestClient(create_app(Settings(engine="noop")))
    with client:
        response = client.post(
            "/v1/realtime/files",
            files={"file": ("broken.mp3", b"not-an-mp3", "audio/mpeg")},
            data={"mode": "accelerated"},
        )
        session_id = response.json()["session_id"]
        with client.websocket_connect(f"/v1/realtime/transcription/{session_id}") as websocket:
            events = [websocket.receive_json(), websocket.receive_json(), websocket.receive_json()]

    assert response.status_code == 202
    assert events[-1] == {
        "type": "error",
        "session_id": session_id,
        "error_code": "audio_decode_error",
    }
