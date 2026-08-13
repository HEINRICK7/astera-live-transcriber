from scripts.analyze_unresolved_cleanup import analyze


def test_unresolved_analysis_separates_covered_and_protected_spans() -> None:
    quality = {
        "issues": [
            {
                "segment_id": "seg_1",
                "issue": "adjacent_duplication",
                "safe_structural_candidate": True,
                "evidence": {
                    "duplicate": {
                        "block": ["Ela"],
                        "repeated_block": ["Ela"],
                        "text": "Ela Ela",
                        "token_start": 1,
                        "token_end": 3,
                    }
                },
            },
            {
                "segment_id": "seg_2",
                "issue": "adjacent_duplication",
                "safe_structural_candidate": True,
                "evidence": {
                    "duplicate": {
                        "block": ["não"],
                        "repeated_block": ["não"],
                        "text": "não não",
                        "token_start": 1,
                        "token_end": 3,
                    }
                },
            },
        ]
    }
    cleanup = {
        "unique_spans": [
            {
                "segment_id": "seg_1",
                "original": "Ela Ela",
            }
        ]
    }

    report = analyze(quality, cleanup)

    assert report["sets"] == {
        "quality_safe_adjacent_candidates": 2,
        "covered_by_cleanup": 1,
        "safe_but_unresolved": 1,
        "cleanup_only": 0,
    }
    assert report["summary"]["category_counts"] == {"protected_context": 1}
