import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CLINICAL_CATEGORIES = (
    "dose",
    "number",
    "negation",
    "medication",
    "speaker_label",
)


@dataclass(frozen=True, slots=True)
class ClinicalCheckItem:
    category: str
    expected: tuple[str, ...]
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    accuracy: float

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "expected": list(self.expected),
            "matched": list(self.matched),
            "missing": list(self.missing),
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True, slots=True)
class ClinicalCheckReport:
    items: tuple[ClinicalCheckItem, ...]

    def as_dict(self) -> dict[str, object]:
        return {"items": [item.as_dict() for item in self.items]}


class ClinicalCheckEvaluator:
    """Offline, exact-span checks for manually annotated critical content."""

    def evaluate(
        self,
        hypotheses: Mapping[str, str],
        annotations: Mapping[str, Sequence[str | Mapping[str, object]]],
    ) -> dict[str, ClinicalCheckReport]:
        return {
            label: self.evaluate_hypothesis(text, annotations)
            for label, text in hypotheses.items()
        }

    def evaluate_hypothesis(
        self,
        hypothesis: str,
        annotations: Mapping[str, Sequence[str | Mapping[str, object]]],
    ) -> ClinicalCheckReport:
        normalized_hypothesis = _normalize(hypothesis)
        items: list[ClinicalCheckItem] = []
        for category in CLINICAL_CATEGORIES:
            expected = tuple(
                _annotation_text(value) for value in annotations.get(category, ())
            )
            matched = tuple(
                value for value in expected if _normalize(value) in normalized_hypothesis
            )
            missing = tuple(value for value in expected if value not in matched)
            accuracy = len(matched) / len(expected) if expected else 1.0
            items.append(
                ClinicalCheckItem(
                    category=category,
                    expected=expected,
                    matched=matched,
                    missing=missing,
                    accuracy=accuracy,
                )
            )
        return ClinicalCheckReport(tuple(items))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _annotation_text(value: str | Mapping[str, object]) -> str:
    if isinstance(value, Mapping):
        expected_text = value.get("expected_text")
        if isinstance(expected_text, str):
            return expected_text
    return str(value)
