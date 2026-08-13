import json

from scripts.capture_realtime_benchmark import _finalize_capture, _write_hypotheses


def test_capture_writes_raw_and_latest_projected_hypotheses(tmp_path) -> None:
    events = [
        {
            "type": "transcript.committed",
            "text": "Como Como está",
            "projected_text": "Como está",
            "technical": {"raw_xai": {"text": "diagnostic partial"}},
        },
        {
            "type": "transcript.committed",
            "text": "Perfeito. Perfeito.",
            "projected_text": "Como está\nPerfeito.",
            "technical": {"raw_xai": {"text": "diagnostic partial"}},
        },
    ]

    _write_hypotheses(events, tmp_path)

    assert (tmp_path / "xai_raw_committed.txt").read_text().splitlines() == [
        "Como Como está",
        "Perfeito. Perfeito.",
    ]
    assert (tmp_path / "astera_structural_projection.txt").read_text().strip() == (
        "Como está\nPerfeito."
    )
    assert json.loads(json.dumps(events))[1]["projected_text"] == "Como está\nPerfeito."


def test_capture_finalization_persists_failed_run_metadata(tmp_path) -> None:
    _finalize_capture(
        [{"type": "session.created", "session_id": "sess_failed"}],
        tmp_path / "audio.mp3",
        tmp_path,
        failure={"type": "SpeechBackpressureError", "message": "queue is full"},
    )

    failure = json.loads((tmp_path / "capture-failure.json").read_text())
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert failure["status"] == "not_a_benchmark_run"
    assert failure["events_persisted"] == 1
    assert metadata["status"] == "failed"
