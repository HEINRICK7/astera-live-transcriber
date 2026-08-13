import re
from collections import Counter
from dataclasses import dataclass

from .jiwer_evaluator import AlignmentError, JiwerAlignment, JiwerEvaluator, JiwerScores


@dataclass(frozen=True, slots=True)
class AttributedError:
    error_id: str
    operation: str
    reference: str
    hypothesis: str
    category: str
    clinically_relevant: bool
    adaptive_correctable: bool
    structurally_correctable: bool
    correctable_by: str
    source: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.error_id,
            "type": self.operation,
            "reference": self.reference,
            "hypothesis": self.hypothesis,
            "category": self.category,
            "clinically_relevant": self.clinically_relevant,
            "adaptive_correctable": self.adaptive_correctable,
            "structurally_correctable": self.structurally_correctable,
            "correctable_by": self.correctable_by,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ErrorAnalysisReport:
    raw_scores: JiwerScores
    raw_alignment: JiwerAlignment
    normalized_scores: JiwerScores
    normalized_alignment: JiwerAlignment
    errors: tuple[AttributedError, ...]
    normalized_errors: tuple[AttributedError, ...]
    ccer_experimental: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "variant": "astera_structural_projection",
            "evaluation_normalization": {
                "raw": "preserve_reference_and_hypothesis_line_breaks",
                "supplementary": "replace_line_breaks_with_spaces",
            },
            "raw_metrics": _scores(self.raw_scores),
            "raw_alignment": self.raw_alignment.as_dict(),
            "normalized_metrics": _scores(self.normalized_scores),
            "normalized_alignment": self.normalized_alignment.as_dict(),
            "errors": [error.as_dict() for error in self.errors],
            "summary": _summary(self.errors),
            "normalized_errors": [error.as_dict() for error in self.normalized_errors],
            "normalized_summary": _summary(self.normalized_errors),
            "ccer_experimental": self.ccer_experimental,
        }


def analyze_error_attribution(
    reference: str,
    hypothesis: str,
    *,
    critical_annotation_count: int = 0,
) -> ErrorAnalysisReport:
    evaluator = JiwerEvaluator()
    raw_scores = evaluator.evaluate(reference, hypothesis)
    raw_alignment = evaluator.align(reference, hypothesis)
    normalized_reference = _normalize_line_breaks(reference)
    normalized_hypothesis = _normalize_line_breaks(hypothesis)
    normalized_scores = evaluator.evaluate(normalized_reference, normalized_hypothesis)
    normalized_alignment = evaluator.align(normalized_reference, normalized_hypothesis)

    errors = tuple(
        _attribute(index, error, raw_alignment.errors)
        for index, error in enumerate(raw_alignment.errors, start=1)
    )
    normalized_errors = tuple(
        _attribute(index, error, normalized_alignment.errors)
        for index, error in enumerate(normalized_alignment.errors, start=1)
    )
    return ErrorAnalysisReport(
        raw_scores=raw_scores,
        raw_alignment=raw_alignment,
        normalized_scores=normalized_scores,
        normalized_alignment=normalized_alignment,
        errors=errors,
        normalized_errors=normalized_errors,
        ccer_experimental={
            "value": None,
            "status": "not_computable_presence_only",
            "numerator": None,
            "denominator": critical_annotation_count,
            "reason": (
                "Current clinical annotations are presence checks; they do not yet "
                "compare value, context, polarity, and speaker."
            ),
        },
    )


def _attribute(
    index: int,
    error: AlignmentError,
    all_errors: tuple[AlignmentError, ...],
) -> AttributedError:
    boundary_related = _is_boundary_related(error, all_errors)
    tokens = set(re.findall(r"[\wÀ-ÿ]+", f"{error.reference} {error.hypothesis}".lower()))
    if boundary_related:
        return _make(
            index,
            error,
            category="formatting_or_tokenization",
            clinically_relevant=False,
            adaptive_correctable=False,
            structurally_correctable=False,
            correctable_by="normalization",
            source="line_boundary",
            reason="JiWER operation is caused by dialogue line-break alignment.",
        )
    if error.operation == "substitute" and _normalize_tokens(
        error.reference
    ) == _normalize_tokens(error.hypothesis):
        return _make(
            index,
            error,
            category="formatting_or_tokenization",
            clinically_relevant=False,
            adaptive_correctable=False,
            structurally_correctable=False,
            correctable_by="normalization",
            source="text_representation",
            reason="Words match after case and punctuation normalization.",
        )
    if tokens & {"não", "nao", "sem", "nunca"}:
        return _make(
            index,
            error,
            "negation",
            True,
            False,
            False,
            "provider",
            "provider_output",
            "Negation must not be inferred by fuzzy matching.",
        )
    if any(token.isdigit() for token in tokens) or tokens & _NUMBER_WORDS:
        return _make(
            index,
            error,
            "number_or_dose",
            True,
            False,
            False,
            "provider",
            "provider_output",
            "Numeric clinical content is blocked from adaptive normalization.",
        )
    if tokens & _MEDICATION_WORDS:
        return _make(
            index,
            error,
            "medication_or_clinical_term",
            True,
            False,
            False,
            "provider",
            "provider_output",
            "Medication terms require authoritative provider or human review.",
        )
    if tokens & _SPEAKER_WORDS:
        return _make(
            index,
            error,
            "speaker_or_label",
            False,
            False,
            False,
            "provider",
            "provider_diarization",
            "Speaker labels are not safe fuzzy-normalization targets.",
        )
    if error.operation == "insert" and len(error.hypothesis.split()) >= 5:
        return _make(
            index,
            error,
            "stream_artifact",
            False,
            False,
            True,
            "structural",
            "segment_boundary",
            "Large inserted span is structurally attributable to a replay or boundary.",
        )
    if error.operation == "insert":
        return _make(
            index,
            error,
            "insertion",
            False,
            False,
            False,
            "provider",
            "provider_output",
            "No structural evidence was available for this isolated insertion.",
        )
    if error.operation == "delete":
        return _make(
            index,
            error,
            "omission",
            False,
            False,
            False,
            "provider",
            "provider_output",
            "The reference token is absent from the hypothesis.",
        )
    return _make(
        index,
        error,
        "genuine_content_error",
        False,
        False,
        False,
        "provider",
        "provider_output",
        "Content mismatch is not safe to infer from similarity alone.",
    )


def _make(
    index: int,
    error: AlignmentError,
    category: str,
    clinically_relevant: bool,
    adaptive_correctable: bool,
    structurally_correctable: bool,
    correctable_by: str,
    source: str,
    reason: str,
) -> AttributedError:
    return AttributedError(
        error_id=f"err_{index:03d}",
        operation=error.operation,
        reference=error.reference,
        hypothesis=error.hypothesis,
        category=category,
        clinically_relevant=clinically_relevant,
        adaptive_correctable=adaptive_correctable,
        structurally_correctable=structurally_correctable,
        correctable_by=correctable_by,
        source=source,
        reason=reason,
    )


def _is_boundary_related(error: AlignmentError, all_errors: tuple[AlignmentError, ...]) -> bool:
    if "\n" in error.reference or "\n" in error.hypothesis:
        return True
    index = all_errors.index(error)
    neighbors = all_errors[max(0, index - 1) : index + 2]
    return any("\n" in item.reference or "\n" in item.hypothesis for item in neighbors)


def _normalize_line_breaks(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_tokens(value: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", value.lower()))


def _scores(scores: JiwerScores) -> dict[str, float]:
    return {key: getattr(scores, key) for key in ("wer", "cer", "mer", "wil")}


def _summary(errors: tuple[AttributedError, ...]) -> dict[str, object]:
    return {
        "total_operations": len(errors),
        "by_type": dict(Counter(error.operation for error in errors)),
        "by_category": dict(Counter(error.category for error in errors)),
        "by_correctable_by": dict(Counter(error.correctable_by for error in errors)),
        "clinically_relevant_count": sum(error.clinically_relevant for error in errors),
        "adaptive_correctable_count": sum(error.adaptive_correctable for error in errors),
        "structurally_correctable_count": sum(error.structurally_correctable for error in errors),
    }


_NUMBER_WORDS = frozenset("zero um uma dois duas três quatro cinco seis sete oito nove dez".split())
_MEDICATION_WORDS = frozenset({"losartana", "medicamento", "medicamentos", "miligramas", "mg"})
_SPEAKER_WORDS = frozenset({"médica", "medica", "médico", "medico", "paciente"})
