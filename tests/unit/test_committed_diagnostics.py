from astera_live_transcriber.application.services.committed_diagnostics import (
    adjacent_duplicate_runs,
    committed_projection_diagnostics,
    segment_overlap,
)
from astera_live_transcriber.application.services.provider_segment_store import (
    ProviderSegmentState,
)


def test_adjacent_duplicate_diagnostics_reports_single_and_phrase_replays() -> None:
    duplicates = adjacent_duplicate_runs(
        "Como Como está. Perfeito. Perfeito. A A visão."
    )

    assert [item["block"] for item in duplicates] == [
        ["Como"],
        ["Perfeito."],
        ["A"],
    ]


def test_segment_overlap_diagnostics_reports_temporal_and_textual_overlap() -> None:
    left = ProviderSegmentState(
        segment_id="seg_0009",
        revision=1,
        committed_text="Como está seu sono e sua alimentação?",
        start_ms=81_600,
        end_ms=98_600,
        status="committed",
    )
    right = ProviderSegmentState(
        segment_id="seg_0010",
        revision=1,
        committed_text="Como está seu sono e sua alimentação? Você fuma?",
        start_ms=98_500,
        end_ms=116_620,
        status="committed",
    )

    result = segment_overlap(left, right)

    assert result is not None
    assert result["temporal_overlap_ms"] == 100
    assert result["textual_suffix_prefix_overlap_tokens"] >= 7
    assert result["classification"] == "temporal_and_textual_overlap"


def test_committed_projection_diagnostics_exposes_current_neighbors() -> None:
    segments = (
        ProviderSegmentState(
            "seg_0009",
            1,
            committed_text="A B C",
            start_ms=100,
            end_ms=200,
            status="committed",
        ),
        ProviderSegmentState(
            "seg_0010",
            1,
            committed_text="A B C D",
            start_ms=199,
            end_ms=300,
            status="committed",
        ),
    )

    result = committed_projection_diagnostics(segments, "seg_0010")

    assert result["committed_count"] == 2
    assert result["current"]["segment_id"] == "seg_0010"
    assert result["previous"]["segment_id"] == "seg_0009"
    assert result["overlap_with_previous"]["right_contains_left"] is True
