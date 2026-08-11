import base64
import struct

from fastapi.testclient import TestClient

from astera_live_transcriber.presentation.api.app import create_app


def test_websocket_session_lifecycle_and_speech_event() -> None:
    data = struct.pack("<1600h", *([20_000] * 1600))
    client = TestClient(create_app())

    with client.websocket_connect("/v1/realtime/transcription") as websocket:
        websocket.send_json(
            {
                "type": "session.create",
                "session": {
                    "model": "astera-stt-realtime-1",
                    "language": "pt-BR",
                    "audio_format": "pcm16",
                    "sample_rate": 16_000,
                    "vad": True,
                },
            }
        )
        created = websocket.receive_json()
        assert created["type"] == "session.created"

        websocket.send_json(
            {
                "type": "audio.append",
                "audio": base64.b64encode(data).decode("ascii"),
                "sequence": 0,
                "timestamp_ms": 0,
                "duration_ms": 100,
                "sample_rate": 16_000,
                "channels": 1,
            }
        )
        started = websocket.receive_json()
        assert started["type"] == "speech.started"
        assert started["session_id"] == created["session_id"]

        websocket.send_json({"type": "session.close"})
        closed = websocket.receive_json()
        assert closed == {"type": "session.closed", "session_id": created["session_id"]}
