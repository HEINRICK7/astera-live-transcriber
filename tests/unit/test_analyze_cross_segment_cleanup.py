from scripts.analyze_cross_segment_cleanup import analyze


def test_cross_segment_investigation_requires_exact_boundary_overlap() -> None:
    unresolved = {
        "unresolved_spans": [
            {
                "segment_id": "seg_2",
                "reason": "cross_segment_duplicate",
                "evidence": {
                    "duplicate": {
                        "block": ["Pode"],
                        "repeated_block": ["Pode"],
                        "text": "Pode Pode",
                    }
                },
            }
        ]
    }
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_1",
            "text": "Pode",
            "start_ms": 0,
            "end_ms": 1000,
        },
        {
            "type": "transcript.committed",
            "segment_id": "seg_2",
            "text": "Pode deitar",
            "start_ms": 1100,
            "end_ms": 2000,
        },
    ]

    report = analyze(unresolved, events)

    assert report["summary"]["cross_segment_candidates"] == 1
    assert report["summary"]["cross_segment_eligible"] == 1
    assert report["summary"]["cross_segment_removed"] == 0
    assert report["candidates"][0]["boundary"]["exact_boundary_overlap_tokens"] == 1
