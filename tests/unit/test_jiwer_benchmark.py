from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    JiwerBenchmark,
)


def test_jiwer_benchmark_compares_named_pipeline_variants() -> None:
    report = JiwerBenchmark().evaluate(
        "Como está seu sono",
        {
            "xai_raw_committed": "Como Como está seu sono",
            "astera_structural_projection": "Como está seu sono",
            "astera_structural_adaptive": "Como está seu sono",
        },
    )

    assert [item.label for item in report.items] == [
        "xai_raw_committed",
        "astera_structural_projection",
        "astera_structural_adaptive",
    ]
    assert report.items[1].scores.wer < report.items[0].scores.wer
    assert report.items[0].alignment.insertions == 1
    assert report.items[1].alignment.hits == 4
    assert report.items[1].normalized_scores.wer == 0
