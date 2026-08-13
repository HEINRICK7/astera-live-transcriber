from astera_live_transcriber.application.services.text_diagnostics import (
    text_comparison_diagnostics,
)


def test_text_comparison_diagnostics_exposes_rapidfuzz_and_jiwer_pairs() -> None:
    result = text_comparison_diagnostics(
        [("mapped_to_canonical", "Como Como está", "Como está", "mapped")]
    )

    assert result["purpose"] == "debug_diagnostics_only"
    assert result["ground_truth_available"] is False
    rapidfuzz = result["rapidfuzz"]
    jiwer = result["jiwer"]
    assert rapidfuzz["pairs"][0]["name"] == "mapped_to_canonical"
    assert rapidfuzz["pairs"][0]["ratio"] < 100
    assert jiwer["pairs"][0]["wer"] > 0


def test_text_comparison_diagnostics_reports_exact_replay() -> None:
    result = text_comparison_diagnostics([("replay", "A B", "A B", "previous")])

    assert result["rapidfuzz"]["pairs"][0]["exact_match"] is True
    assert result["rapidfuzz"]["pairs"][0]["ratio"] == 100
    assert result["jiwer"]["pairs"][0]["wer"] == 0
