from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    ClinicalCheckEvaluator,
)


def test_clinical_checks_report_critical_span_coverage() -> None:
    report = ClinicalCheckEvaluator().evaluate_hypothesis(
        "Paciente não vomitei. Usa losartana de 50 miligramas. Médica:",
        {
            "dose": [{"expected_text": "50 miligramas"}],
            "number": [{"expected_text": "50"}],
            "negation": [{"expected_text": "não vomitei"}],
            "medication": [{"expected_text": "losartana"}],
            "speaker_label": [{"expected_text": "Médica:"}],
        },
    )

    assert {item.category: item.accuracy for item in report.items} == {
        "dose": 1.0,
        "number": 1.0,
        "negation": 1.0,
        "medication": 1.0,
        "speaker_label": 1.0,
    }


def test_clinical_checks_keep_missing_critical_spans_visible() -> None:
    report = ClinicalCheckEvaluator().evaluate_hypothesis(
        "Paciente tomou medicamento.",
        {
            "dose": [{"expected_text": "50 miligramas"}],
            "medication": [{"expected_text": "losartana"}],
        },
    )

    by_category = {item.category: item for item in report.items}
    assert by_category["dose"].missing == ("50 miligramas",)
    assert by_category["dose"].accuracy == 0.0
    assert by_category["medication"].missing == ("losartana",)
