import re
from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest


@dataclass(frozen=True, slots=True)
class JiwerScores:
    wer: float
    cer: float
    mer: float
    wil: float


@dataclass(frozen=True, slots=True)
class AlignmentError:
    operation: str
    reference: str
    hypothesis: str
    category: str

    def as_dict(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "reference": self.reference,
            "hypothesis": self.hypothesis,
            "category": self.category,
        }


@dataclass(frozen=True, slots=True)
class JiwerAlignment:
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    errors: tuple[AlignmentError, ...]
    categories: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "hits": self.hits,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "errors": [error.as_dict() for error in self.errors],
            "categories": dict(self.categories),
        }


class JiwerEvaluator:
    """Runs benchmark metrics outside the realtime path."""

    def evaluate(self, reference: str, hypothesis: str) -> JiwerScores:
        if not reference.strip():
            score = 0.0 if not hypothesis.strip() else 1.0
            return JiwerScores(wer=score, cer=score, mer=score, wil=score)
        try:
            from jiwer import cer, mer, wer, wil
        except ImportError as exc:
            raise RuntimeError("jiwer is required for offline evaluation") from exc
        return JiwerScores(
            wer=float(wer(reference, hypothesis)),
            cer=float(cer(reference, hypothesis)),
            mer=float(mer(reference, hypothesis)),
            wil=float(wil(reference, hypothesis)),
        )

    def align(self, reference: str, hypothesis: str) -> JiwerAlignment:
        """Return word-level operations for explaining the remaining WER."""
        if not reference.strip() and not hypothesis.strip():
            return JiwerAlignment(0, 0, 0, 0, (), {})
        try:
            from jiwer import process_words
        except ImportError as exc:
            raise RuntimeError("jiwer is required for offline evaluation") from exc

        output = process_words(reference, hypothesis)
        references = output.references[0] if output.references else []
        hypotheses = output.hypotheses[0] if output.hypotheses else []
        errors: list[AlignmentError] = []
        categories: Counter[str] = Counter()
        for chunk in output.alignments[0] if output.alignments else ():
            if chunk.type == "equal":
                continue
            ref_tokens = references[chunk.ref_start_idx : chunk.ref_end_idx]
            hyp_tokens = hypotheses[chunk.hyp_start_idx : chunk.hyp_end_idx]
            pairs = (
                zip_longest(ref_tokens, hyp_tokens, fillvalue="")
                if chunk.type == "substitute"
                else (("", token) for token in hyp_tokens)
                if chunk.type == "insert"
                else ((token, "") for token in ref_tokens)
            )
            for ref, hyp in pairs:
                operation = (
                    "substitute"
                    if ref and hyp
                    else "insert"
                    if hyp
                    else "delete"
                )
                category = _classify_error(ref, hyp, operation)
                categories[category] += 1
                errors.append(
                    AlignmentError(
                        operation=operation,
                        reference=ref,
                        hypothesis=hyp,
                        category=category,
                    )
                )
        return JiwerAlignment(
            hits=int(output.hits),
            substitutions=int(output.substitutions),
            deletions=int(output.deletions),
            insertions=int(output.insertions),
            errors=tuple(errors),
            categories=dict(categories),
        )


_NUMBER_WORDS = frozenset(
    "zero um uma dois duas três quatro cinco seis sete oito nove dez "
    "primeiro primeira segundo segunda uns umas metade"
    .split()
)
_MEDICAL_WORDS = frozenset(
    {"losartana", "medicamento", "medicamentos", "mg", "miligramas", "hipertensão"}
)


def _classify_error(reference: str, hypothesis: str, operation: str) -> str:
    ref_tokens = re.findall(r"[\wÀ-ÿ]+", reference.lower())
    hyp_tokens = re.findall(r"[\wÀ-ÿ]+", hypothesis.lower())
    tokens = set(ref_tokens + hyp_tokens)
    speaker_tokens = {"médica", "medica", "médico", "medico", "paciente"}
    if tokens & speaker_tokens and max(len(ref_tokens), len(hyp_tokens)) <= 4:
        return "speaker_label"
    if any(token.isdigit() or token in _NUMBER_WORDS for token in tokens):
        return "number"
    if tokens & _MEDICAL_WORDS and max(len(ref_tokens), len(hyp_tokens)) <= 6:
        return "medication_or_clinical_term"
    if operation == "insert" and len(hyp_tokens) >= 5:
        return "replay_or_overlap"
    if operation == "substitute" and _normalize_tokens(reference) == _normalize_tokens(hypothesis):
        return "punctuation_or_tokenization"
    if operation == "delete":
        return "omission"
    if operation == "insert":
        return "insertion"
    return "word_error"


def _normalize_tokens(value: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", value.lower()))


def normalize_layout(value: str) -> str:
    """Normalize presentation whitespace for the supplementary quality view."""
    return re.sub(r"\s+", " ", value).strip()
