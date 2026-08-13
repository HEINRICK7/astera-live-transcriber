import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CRITICAL_CATEGORIES = frozenset({"dose", "number", "negation", "medication"})
_SPEAKER_PATTERN = re.compile(r"(?=(?:médica|medica|paciente|médico|medico)\s*:)", re.I)
_NUMBER_WORDS = {
    0: "zero",
    1: "um",
    2: "dois",
    3: "três",
    4: "quatro",
    5: "cinco",
    6: "seis",
    7: "sete",
    8: "oito",
    9: "nove",
    10: "dez",
    50: "cinquenta",
    100: "cem",
}
_UNIT_ALIASES = {
    "mg": ("mg", "miligramas", "miligrama"),
    "dias": ("dia", "dias"),
}


@dataclass(frozen=True, slots=True)
class ClinicalFidelityItem:
    category: str
    expected: str
    speaker: str | None
    checks: dict[str, bool]
    passed: bool
    clinically_relevant: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "expected": self.expected,
            "speaker": self.speaker,
            "checks": dict(self.checks),
            "passed": self.passed,
            "clinically_relevant": self.clinically_relevant,
        }


@dataclass(frozen=True, slots=True)
class ClinicalFidelityReport:
    items: tuple[ClinicalFidelityItem, ...]
    ccer_experimental: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [item.as_dict() for item in self.items],
            "summary": {
                "total": len(self.items),
                "passed": sum(item.passed for item in self.items),
                "failed": sum(not item.passed for item in self.items),
                "accuracy": (
                    sum(item.passed for item in self.items) / len(self.items)
                    if self.items
                    else 1.0
                ),
            },
            "ccer_experimental": self.ccer_experimental,
        }


class ClinicalFidelityEvaluator:
    """Context/value/polarity/speaker checks for annotated lab evidence."""

    def evaluate(
        self,
        hypotheses: Mapping[str, str],
        annotations: Mapping[str, Sequence[Mapping[str, object]]],
    ) -> dict[str, ClinicalFidelityReport]:
        return {
            label: self.evaluate_hypothesis(text, annotations)
            for label, text in hypotheses.items()
        }

    def evaluate_hypothesis(
        self,
        hypothesis: str,
        annotations: Mapping[str, Sequence[Mapping[str, object]]],
    ) -> ClinicalFidelityReport:
        items: list[ClinicalFidelityItem] = []
        for category in annotations:
            if category.startswith("_"):
                continue
            for annotation in annotations[category]:
                items.append(_evaluate_item(category, annotation, hypothesis))
        critical = [item for item in items if item.category in CRITICAL_CATEGORIES]
        failed = sum(not item.passed for item in critical)
        return ClinicalFidelityReport(
            items=tuple(items),
            ccer_experimental={
                "value": failed / len(critical) if critical else 0.0,
                "status": "computed_context_value_polarity_speaker",
                "numerator": failed,
                "denominator": len(critical),
                "critical_categories": sorted(CRITICAL_CATEGORIES),
            },
        )


def _evaluate_item(
    category: str,
    annotation: Mapping[str, object],
    hypothesis: str,
) -> ClinicalFidelityItem:
    expected = str(annotation.get("expected_text") or "")
    speaker = _string(annotation.get("speaker"))
    segment = _find_segment(hypothesis, speaker, expected)
    checks: dict[str, bool] = {
        "context": segment is not None and _contains_phrase(segment, expected),
        "speaker": segment is not None
        and (speaker is None or _starts_with_speaker(segment, speaker)),
    }
    if category in {"dose", "number", "medication"}:
        checks["value"] = (
            _value_present(segment or "", annotation.get("value"))
            if "value" in annotation
            else True
        )
        checks["unit"] = (
            _unit_present(segment or "", annotation.get("unit"))
            if "unit" in annotation
            else True
        )
    if category == "dose":
        checks["medication"] = _term_present(segment or "", annotation.get("medication"))
    if category == "medication":
        checks["medication"] = _term_present(segment or "", annotation.get("name"))
    if category == "negation":
        checks["polarity"] = _polarity_present(segment or "", annotation)
    passed = all(checks.values())
    return ClinicalFidelityItem(
        category=category,
        expected=expected,
        speaker=speaker,
        checks=checks,
        passed=passed,
        clinically_relevant=category in CRITICAL_CATEGORIES,
    )


def _find_segment(text: str, speaker: str | None, expected: str) -> str | None:
    segments = [segment.strip() for segment in _SPEAKER_PATTERN.split(text) if segment.strip()]
    if speaker is None:
        return text if _contains_phrase(text, expected) else None
    for segment in segments:
        if _starts_with_speaker(segment, speaker) and _contains_phrase(segment, expected):
            return segment
    return None


def _starts_with_speaker(segment: str, speaker: str) -> bool:
    return _normalize(segment).startswith(f"{_normalize(speaker)}:")


def _contains_phrase(text: str, phrase: str) -> bool:
    text_tokens = _tokens(text)
    phrase_tokens = _tokens(phrase)
    if not phrase_tokens:
        return False
    for start in range(len(text_tokens) - len(phrase_tokens) + 1):
        if text_tokens[start : start + len(phrase_tokens)] == phrase_tokens:
            return True
    return False


def _value_present(text: str, value: object) -> bool:
    if value is None:
        return True
    value_text = str(value)
    variants = {value_text}
    try:
        variants.add(_NUMBER_WORDS[int(value_text)])
    except (KeyError, TypeError, ValueError):
        pass
    tokens = set(_tokens(text))
    return any(_normalize(variant) in tokens for variant in variants)


def _unit_present(text: str, unit: object) -> bool:
    if unit is None:
        return True
    aliases = _UNIT_ALIASES.get(_normalize(str(unit)), (str(unit),))
    tokens = set(_tokens(text))
    return any(_normalize(alias) in tokens for alias in aliases)


def _term_present(text: str, term: object) -> bool:
    return term is not None and _normalize(str(term)) in set(_tokens(text))


def _polarity_present(text: str, annotation: Mapping[str, object]) -> bool:
    expected = _normalize(str(annotation.get("expected_text") or ""))
    negated = annotation.get("negated")
    if negated is True:
        return (
            "não" in set(_tokens(text))
            or "nao" in set(_tokens(text))
            or expected in _normalize(text)
        )
    return True


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+", _normalize(value))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None
