from collections.abc import Mapping
from dataclasses import dataclass

from .jiwer_evaluator import (
    JiwerAlignment,
    JiwerEvaluator,
    JiwerScores,
    normalize_layout,
)


@dataclass(frozen=True, slots=True)
class BenchmarkItem:
    label: str
    scores: JiwerScores
    alignment: JiwerAlignment
    normalized_scores: JiwerScores
    normalized_alignment: JiwerAlignment

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "wer": self.scores.wer,
            "cer": self.scores.cer,
            "mer": self.scores.mer,
            "wil": self.scores.wil,
            "alignment": self.alignment.as_dict(),
            "normalized_jiwer": {
                "wer": self.normalized_scores.wer,
                "cer": self.normalized_scores.cer,
                "mer": self.normalized_scores.mer,
                "wil": self.normalized_scores.wil,
                "alignment": self.normalized_alignment.as_dict(),
            },
        }


@dataclass(frozen=True, slots=True)
class JiwerBenchmarkReport:
    reference_chars: int
    reference_tokens: int
    items: tuple[BenchmarkItem, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "reference_chars": self.reference_chars,
            "reference_tokens": self.reference_tokens,
            "items": [item.as_dict() for item in self.items],
        }


class JiwerBenchmark:
    """Offline comparison of the raw, structural, and adaptive variants."""

    def __init__(self, evaluator: JiwerEvaluator | None = None) -> None:
        self._evaluator = evaluator or JiwerEvaluator()

    def evaluate(
        self,
        reference: str,
        hypotheses: Mapping[str, str],
    ) -> JiwerBenchmarkReport:
        return JiwerBenchmarkReport(
            reference_chars=len(reference),
            reference_tokens=len(reference.split()),
            items=tuple(
                _benchmark_item(self._evaluator, reference, label, text)
                for label, text in hypotheses.items()
            ),
        )


def _benchmark_item(
    evaluator: JiwerEvaluator,
    reference: str,
    label: str,
    hypothesis: str,
) -> BenchmarkItem:
    normalized_reference = normalize_layout(reference)
    normalized_hypothesis = normalize_layout(hypothesis)
    return BenchmarkItem(
        label=label,
        scores=evaluator.evaluate(reference, hypothesis),
        alignment=evaluator.align(reference, hypothesis),
        normalized_scores=evaluator.evaluate(normalized_reference, normalized_hypothesis),
        normalized_alignment=evaluator.align(normalized_reference, normalized_hypothesis),
    )
