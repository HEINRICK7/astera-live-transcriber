from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    ClinicalFidelityEvaluator,
)


def test_clinical_fidelity_requires_context_value_unit_and_speaker() -> None:
    report = ClinicalFidelityEvaluator().evaluate_hypothesis(
        "Paciente: Tenho hipertensão e tomo losartana de 50 miligramas todos os dias.",
        {
            "dose": [
                {
                    "medication": "losartana",
                    "value": 50,
                    "unit": "mg",
                    "speaker": "Paciente",
                    "expected_text": "tomo losartana de 50 miligramas",
                }
            ]
        },
    )

    item = report.items[0]
    assert item.passed is True
    assert item.checks == {
        "context": True,
        "speaker": True,
        "value": True,
        "unit": True,
        "medication": True,
    }
    assert report.ccer_experimental["value"] == 0.0


def test_clinical_fidelity_does_not_accept_scattered_evidence() -> None:
    report = ClinicalFidelityEvaluator().evaluate_hypothesis(
        "Médica: usa losartana. Paciente: toma 100 miligramas.",
        {
            "dose": [
                {
                    "medication": "losartana",
                    "value": 50,
                    "unit": "mg",
                    "speaker": "Paciente",
                    "expected_text": "toma losartana de 50 miligramas",
                }
            ]
        },
    )

    assert report.items[0].passed is False
    assert report.ccer_experimental["numerator"] == 1
