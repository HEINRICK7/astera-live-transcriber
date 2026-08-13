import json
from pathlib import Path

from astera_live_transcriber.application.services.committed_transcript_projector import (
    CommittedTranscriptProjector,
    IncrementalCommittedTranscriptProjector,
    IndexedOverlapWorkingSetPolicy,
    LastActiveSegmentsPolicy,
    ProjectionState,
)
from astera_live_transcriber.application.services.provider_segment_store import (
    ProviderSegmentState,
)


def segment(segment_id: str, text: str, start: int, end: int) -> ProviderSegmentState:
    return ProviderSegmentState(
        segment_id=segment_id,
        revision=1,
        committed_text=text,
        start_ms=start,
        end_ms=end,
        status="committed",
    )


def test_projector_removes_internal_phrase_replay_without_mutating_source() -> None:
    original = segment("seg_0010", "Perfeito. Perfeito.", 100, 200)

    result = CommittedTranscriptProjector().project([original])

    assert result.text == "Perfeito."
    assert result.segments[0].source_text == original.committed_text


def test_projector_removes_adjacent_replay_when_segment_overlaps_previous() -> None:
    previous = segment("seg_0009", "Como está seu sono.", 100, 200)
    current = segment("seg_0010", "Como Como está seu sono. Perfeito. Perfeito.", 199, 300)

    result = CommittedTranscriptProjector().project([previous, current])

    assert result.text == "Como está seu sono. Perfeito."
    assert "remove_revision_duplicate" in [item.operation for item in result.operations]


def test_projector_replaces_previous_when_current_is_authoritative_snapshot() -> None:
    previous = segment("seg_0009", "Como está seu sono.", 100, 200)
    current = segment("seg_0010", "Como está seu sono. Você fuma?", 199, 300)

    result = CommittedTranscriptProjector().project([previous, current])

    assert result.text == "Como está seu sono. Você fuma?"
    assert any(item.operation == "remove_boundary_replay" for item in result.operations)


def test_projector_preserves_legitimate_repeated_words_without_overlap() -> None:
    segments = [segment("seg_1", "não, não tive sim, sim muito, muito forte", 0, 100)]

    result = CommittedTranscriptProjector().project(segments)

    assert result.text == segments[0].committed_text
    assert result.operations == ()


def test_real_fixture_artifacts_are_regression_cases() -> None:
    path = Path("benchmarks/pt_br/clinical/consultation_001/events.json")
    if not path.exists():
        return
    events = json.loads(path.read_text(encoding="utf-8"))
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    states = [
        segment(
            event["segment_id"],
            event["text"],
            event.get("start_ms") or 0,
            event.get("end_ms") or event.get("start_ms") or 0,
        )
        for event in committed
        if event.get("text")
    ]

    result = CommittedTranscriptProjector().project(states)

    assert "No começo achei No começo achei" not in result.text
    assert "Médica: Médica:" not in result.text
    assert "Paciente: Paciente:" not in result.text
    assert "Ela Ela" not in result.text
    assert "Como Como" not in result.text
    assert "Tenho Tenho" not in result.text
    assert "A A visão" not in result.text
    assert "Perfeito. Perfeito." not in result.text


def test_incremental_projector_matches_full_projector_on_two_minute_fixture() -> None:
    path = Path("benchmarks/pt_br/clinical/consultation_001/events.json")
    if not path.exists():
        return
    events = json.loads(path.read_text(encoding="utf-8"))
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    full_states: list[ProviderSegmentState] = []
    full_projector = CommittedTranscriptProjector()
    incremental = IncrementalCommittedTranscriptProjector(working_set_size=4)

    for event in committed:
        state = segment(
            event["segment_id"],
            event["text"],
            event.get("start_ms") or 0,
            event.get("end_ms") or event.get("start_ms") or 0,
        )
        full_states = [
            item for item in full_states if item.segment_id != state.segment_id
        ] + [state]
        expected = full_projector.project(
            sorted(full_states, key=lambda item: (item.start_ms, item.segment_id))
        )
        actual = incremental.project(state)

        assert actual.text == expected.text
        metrics = incremental.projection_metrics()
        assert metrics["historical_segments_scanned"] <= metrics["working_set_bound"]


def test_working_set_policy_is_replaceable_and_bounded() -> None:
    policy = LastActiveSegmentsPolicy(size=2)
    state = ProjectionState(
        active_segments=(
            segment("seg_1", "A", 0, 100),
            segment("seg_2", "B", 100, 200),
            segment("seg_3", "C", 200, 300),
        )
    )

    selected = policy.select(state, segment("seg_4", "D", 300, 400))

    assert [item.segment_id for item in selected] == ["seg_3", "seg_4"]


def test_incremental_projection_preserves_suffix_on_out_of_order_commit() -> None:
    projector = IncrementalCommittedTranscriptProjector(working_set_size=2)

    projector.project(segment("seg_1", "A", 0, 100))
    projector.project(segment("seg_2", "B", 200, 300))
    projector.project(segment("seg_3", "C", 400, 500))

    result = projector.project(segment("seg_late", "X", 100, 200))

    assert [item.segment_id for item in result.segments] == [
        "seg_1",
        "seg_late",
        "seg_2",
        "seg_3",
    ]


def test_indexed_policy_reactivates_only_temporally_eligible_superseded() -> None:
    policy = IndexedOverlapWorkingSetPolicy(size=2, max_reactivated_segments=1)
    state = ProjectionState(
        active_segments=(
            segment("active_1", "A", 0, 100),
            segment("active_2", "B", 500, 600),
        ),
        superseded_segments=("old_overlap", "old_far"),
        superseded_sources=(
            segment("old_overlap", "old", 90, 160),
            segment("old_far", "old", 700, 800),
        ),
    )

    selected = policy.select(state, segment("incoming", "new", 100, 200))

    assert "old_overlap" in [item.segment_id for item in selected]
    assert "old_far" not in [item.segment_id for item in selected]
    assert len(selected) <= 3
