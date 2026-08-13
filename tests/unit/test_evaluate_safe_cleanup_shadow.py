import json

from scripts.evaluate_safe_cleanup_shadow import main


def test_shadow_evaluation_reports_changes_without_mutating_events(tmp_path, monkeypatch) -> None:
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_1",
            "text": "Ela Ela fica",
            "projected_text": "Ela Ela fica",
        }
    ]
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "shadow.json"
    events_path.write_text(json.dumps(events), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_safe_cleanup_shadow.py",
            "--events",
            str(events_path),
            "--output",
            str(output_path),
        ],
    )

    main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["quality_gate"]["cleanup_operations"] == 1
    assert (
        json.loads(events_path.read_text(encoding="utf-8"))[0]["text"]
        == "Ela Ela fica"
    )
