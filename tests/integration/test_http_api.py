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


def test_models_endpoint() -> None:
    response = TestClient(create_app()).get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [{"id": "astera-stt-1"}, {"id": "astera-stt-realtime-1"}],
    }

