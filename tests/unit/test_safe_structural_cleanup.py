from astera_live_transcriber.application.services.safe_structural_cleanup import (
    SafeStructuralCleanup,
)


def test_cleanup_removes_the_two_observed_structural_candidates() -> None:
    cleanup = SafeStructuralCleanup()

    first = cleanup.clean(
        "Paciente: Ela Ela fica o dia todo", segment_id="seg_0042", revision=8
    )
    second = cleanup.clean(
        "Na na verdade eu tive diarreia", segment_id="seg_0091", revision=3
    )

    assert first.cleaned_text == "Paciente: Ela fica o dia todo"
    assert second.cleaned_text == "Na verdade eu tive diarreia"
    assert first.changed is True
    assert second.changed is True
    assert first.changes[0]["cleanup_id"].startswith(
        "seg_0042:adjacent_duplication:ela:"
    )
    assert first.changes[0]["revision"] == 8


def test_cleanup_preserves_protected_repetitions_and_original_evidence() -> None:
    original = "não não tive febre e muito muito forte, losartana losartana"

    result = SafeStructuralCleanup().clean(original)

    assert result.cleaned_text == original
    assert len(result.protected_repetitions) == 3
    assert result.original_text == original
