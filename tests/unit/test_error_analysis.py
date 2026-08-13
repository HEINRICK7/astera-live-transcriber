from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    analyze_error_attribution,
)


def test_error_analysis_separates_line_breaks_from_content_errors() -> None:
    report = analyze_error_attribution(
        "Médica: Bom dia.\nPaciente: Tudo bem.",
        "Médica: Bom dia. Paciente: Tudo bem.",
        critical_annotation_count=2,
    )

    assert report.raw_alignment.insertions == 1
    assert report.raw_alignment.substitutions == 1
    assert report.normalized_alignment.hits == 6
    assert len(report.errors) == 2
    assert all(error.category == "formatting_or_tokenization" for error in report.errors)
    assert len(report.normalized_errors) == 0
    assert report.ccer_experimental["value"] is None
