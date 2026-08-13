from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    analyze_long_session,
)


def event(kind: str, timestamp: int, **extra: object) -> dict[str, object]:
    return {"type": kind, "timestamp_ms": timestamp, **extra}


def test_long_session_report_detects_zero_duration_overlap_replay_and_pause() -> None:
    events = [
        event("audio.started", 0),
        event("speech.started", 0, technical={"astera_turn_id": "seg_1"}),
        {
            "type": "transcript.partial",
            "segment_id": "seg_1",
            "start_ms": 0,
            "technical": {"sequence": 1},
            "text": "A",
        },
        {
            "type": "transcript.committed",
            "segment_id": "seg_1",
            "start_ms": 0,
            "end_ms": 1000,
            "technical": {
                "sequence": 2,
                "projection_summary": {"operation_count": 2, "adjacent_dup_removed": 1},
                "span_diagnostics": {
                    "provider_event_span": {"start_ms": 0, "end_ms": 1000},
                    "provider_word_span": {"start_ms": 10, "end_ms": 900},
                    "astera_segment_span": {"start_ms": 0, "end_ms": 1000},
                },
            },
            "text": "A A",
        },
        event("speech.started", 70_000, technical={"astera_turn_id": "seg_2"}),
        {
            "type": "transcript.committed",
            "segment_id": "seg_2",
            "start_ms": 900,
            "end_ms": 2000,
            "technical": {"sequence": 3},
            "text": "A B",
        },
        event("audio.completed", 2000),
        event("session.completed", 2000),
        {"type": "websocket.closed", "code": 1000, "clean": True},
    ]

    report = analyze_long_session(events, pause_threshold_ms=60_000)

    assert report["analysis"]["transcript_text_unchanged"] is True
    assert report["temporal"]["zero_duration_count"] == 0
    assert report["temporal"]["overlap_count"] == 1
    assert report["replay_and_duplicates"]["replay_count"] == 1
    assert report["replay_and_duplicates"]["residual_adjacent_duplicate_count"] == 1
    assert report["pauses"]["long_pause_count"] == 1
    assert report["projection"]["total_projection_ops"] == 2


def test_long_session_marks_partial_sequence_and_zero_duration() -> None:
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_2",
            "start_ms": 100,
            "end_ms": 100,
            "technical": {
                "sequence": 42,
                "projection_summary": {"operation_count": 1},
                "span_diagnostics": {
                    "provider_event_span": {"start_ms": 0, "end_ms": 200},
                    "provider_word_span": {"start_ms": 0, "end_ms": 200},
                    "astera_segment_span": {"start_ms": 100, "end_ms": 100},
                },
            },
            "text": "texto",
        }
    ]

    report = analyze_long_session(events)

    assert report["analysis"]["input_completeness"] == "partial_or_excerpt"
    assert report["temporal"]["zero_duration_segments"] == ["seg_2"]
    assert report["temporal"]["drift"]["sample_count"] == 2


def test_long_session_reports_projection_degradation_proxies() -> None:
    projection = {
        "projected_transcript": {
            "text": "A B",
            "segments": [
                {"segment_id": "seg_1", "committed_text": "A"},
                {"segment_id": "seg_2", "committed_text": "B"},
            ],
        }
    }
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_2",
            "revision": 3,
            "start_ms": 60_000,
            "end_ms": 60_000,
            "capture_seq": 1,
            "technical": {
                "sequence": 1,
                "committed_projection": projection,
                "projected_text": "A B",
                "projection_summary": {"operation_count": 4},
            },
            "text": "B B",
        },
        {"type": "audio.completed", "timestamp_ms": 60_000},
        {"type": "session.completed", "timestamp_ms": 60_000},
    ]

    report = analyze_long_session(events)
    degradation = report["degradation"]
    row = degradation["commits"][0]

    assert degradation["availability"]["historical_scan_proxy"] is True
    assert row["segment_count_seen"] == 2
    assert row["historical_tokens_scanned"] == 2
    assert row["projection_ops_added"] == 4
    assert row["zero_duration"] is True
    assert row["adjacent_dup_count"] == 1
    assert row["projection_latency_ms"] is None
