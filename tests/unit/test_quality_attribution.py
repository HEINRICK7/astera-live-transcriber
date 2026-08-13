from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    analyze_quality_attribution,
)


def test_quality_attribution_marks_provider_duplicate_and_structural_candidate() -> None:
    report = analyze_quality_attribution(
        [
            {
                "type": "transcript.committed",
                "segment_id": "seg_1",
                "text": "Como Como está",
                "projected_text": "Como está",
            }
        ]
    )

    issue = report["issues"][0]
    assert issue["origin"] == "provider"
    assert issue["provider_committed_contains_issue"] is True
    assert issue["projected_text_contains_issue"] is False
    assert issue["projector_introduced_issue"] is False
    assert issue["safe_structural_candidate"] is True


def test_quality_attribution_detects_replay_and_language_drift() -> None:
    report = analyze_quality_attribution(
        [
            {
                "type": "transcript.committed",
                "segment_id": "seg_1",
                "text": "Paciente com dor intensa",
                "projected_text": "Paciente com dor intensa",
            },
            {
                "type": "transcript.committed",
                "segment_id": "seg_2",
                "text": "Paciente com dor intensa You can use",
                "projected_text": "Paciente com dor intensa You can use",
            },
        ]
    )

    assert report["summary"]["issue_counts"]["segment_replay"] == 1
    assert report["summary"]["issue_counts"]["language_drift"] == 1
    assert report["summary"]["origin_counts"]["provider"] == 2
