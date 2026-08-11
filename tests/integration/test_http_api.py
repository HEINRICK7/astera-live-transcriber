import io
import struct
import wave

from fastapi.testclient import TestClient

from astera_live_transcriber.presentation.api.app import create_app


def test_health_endpoint() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "astera-live-transcriber",
        "version": "0.1.0",
    }


def test_frontend_is_served_at_root() -> None:
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "ASTERA Live Transcriber" in response.text


def test_models_endpoint() -> None:
    response = TestClient(create_app()).get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [{"id": "astera-stt-1"}, {"id": "astera-stt-realtime-1"}],
    }


def test_engine_status_is_ready_for_safe_noop_default() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/v1/engine/status")

    assert response.status_code == 200
    assert response.json()["engine"] == "noop"
    assert response.json()["status"] == "ready"


def test_http_transcription_accepts_canonical_wav() -> None:
    payload = io.BytesIO()
    with wave.open(payload, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(struct.pack("<1600h", *([0] * 1600)))

    response = TestClient(create_app()).post(
        "/v1/audio/transcriptions",
        files={"file": ("sample.wav", payload.getvalue(), "audio/wav")},
        data={"language": "pt-BR"},
    )

    assert response.status_code == 200
    assert response.json()["object"] == "transcription"
    assert response.json()["text"] == ""
