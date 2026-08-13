"""Conservative, shadow-only cleanup for deterministic structural artifacts."""

import re
from dataclasses import dataclass

from .committed_diagnostics import adjacent_duplicate_runs

_PROTECTED_REPETITION_TOKENS = {
    "não",
    "nao",
    "sem",
    "nunca",
    "jamais",
    "sim",
    "muito",
}
_CRITICAL_TOKENS = {
    "mg",
    "ml",
    "g",
    "kg",
    "mg/dia",
    "losartana",
}
_SAFE_NEW_SINGLETONS = {
    "a",
    "o",
    "e",
    "ela",
    "ele",
    "como",
    "tenho",
    "perfeito",
    "na",
    "se",
    "pode",
}


@dataclass(frozen=True, slots=True)
class CleanupResult:
    original_text: str
    cleaned_text: str
    changes: tuple[dict[str, object], ...]
    protected_repetitions: tuple[dict[str, object], ...]

    @property
    def changed(self) -> bool:
        return self.original_text != self.cleaned_text

    def as_dict(self, *, include_text: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": "shadow",
            "changed": self.changed,
            "changes": list(self.changes),
            "protected_repetitions": list(self.protected_repetitions),
        }
        if include_text:
            payload["original_text"] = self.original_text
            payload["cleaned_text"] = self.cleaned_text
        return payload


class SafeStructuralCleanup:
    """Remove only exact, low-risk adjacent replay from projected text.

    This service never receives or modifies provider evidence. It intentionally
    leaves protected repetitions (negation, affirmation, intensity and critical
    terms) untouched. The caller can keep the result in shadow until quality
    gates approve promotion.
    """

    def clean(
        self,
        text: str,
        *,
        evidence_text: str = "",
        previous_text: str = "",
        segment_id: str | None = None,
        revision: int | None = None,
    ) -> CleanupResult:
        original = text
        protected: list[dict[str, object]] = []
        changes: list[dict[str, object]] = []
        cleaned = text
        for duplicate in adjacent_duplicate_runs(text):
            block = [str(token) for token in duplicate["block"]]
            normalized = [_normalize(token) for token in block]
            if self._is_protected(normalized):
                protected.append(duplicate)
                continue
            replacement = " ".join(block)
            repeated = str(duplicate["text"])
            evidenced_by_commit = _contains_phrase(evidence_text, repeated)
            newly_introduced = not _contains_phrase(previous_text, repeated)
            if not evidenced_by_commit and not newly_introduced:
                continue
            if (
                not evidenced_by_commit
                and (len(normalized) != 1 or normalized[0] not in _SAFE_NEW_SINGLETONS)
            ):
                continue
            logical_span = _logical_span(evidence_text, normalized)
            cleaned = _replace_token_span(cleaned, repeated, replacement, duplicate)
            changes.append(
                {
                    "cleanup_id": _cleanup_id(segment_id, duplicate, logical_span),
                    "segment_id": segment_id,
                    "revision": revision,
                    "operation": "remove_adjacent_duplicate",
                    "original": repeated,
                    "replacement": replacement,
                    "evidence": "exact_adjacent_replay",
                    "evidenced_by_commit": evidenced_by_commit,
                    "newly_introduced": newly_introduced,
                    "safe": True,
                }
            )
        return CleanupResult(
            original_text=original,
            cleaned_text=cleaned,
            changes=tuple(changes),
            protected_repetitions=tuple(protected),
        )

    @staticmethod
    def _is_protected(tokens: list[str]) -> bool:
        return bool(
            set(tokens) & _PROTECTED_REPETITION_TOKENS
            or set(tokens) & _CRITICAL_TOKENS
            or any(token.isdigit() for token in tokens)
        )


def _replace_token_span(
    text: str,
    repeated: str,
    replacement: str,
    duplicate: dict[str, object],
) -> str:
    del duplicate
    pattern = re.escape(repeated)
    return re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)


def _contains_phrase(text: str, phrase: str) -> bool:
    return _normalize_phrase(phrase) in _normalize_phrase(text)


def _normalize_phrase(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ/]+", text.casefold()))


def _normalize(token: str) -> str:
    return re.sub(r"[^\wÀ-ÿ/]+", "", token.casefold())


def _cleanup_id(
    segment_id: str | None,
    duplicate: dict[str, object],
    logical_span: tuple[int, int] | None,
) -> str:
    segment = segment_id or "unknown_segment"
    normalized_span = "_".join(
        _normalize(str(token)) for token in duplicate["block"]
    )
    start, end = logical_span or ("unknown", "unknown")
    return (
        f"{segment}:adjacent_duplication:"
        f"{normalized_span}:{start}-{end}"
    )


def _logical_span(text: str, normalized_block: list[str]) -> tuple[int, int] | None:
    tokens = [
        _normalize(token)
        for token in text.split()
        if _normalize(token)
    ]
    block_length = len(normalized_block)
    for index in range(len(tokens) - block_length * 2 + 1):
        if (
            tokens[index : index + block_length] == normalized_block
            and tokens[index + block_length : index + block_length * 2]
            == normalized_block
        ):
            return index, index + block_length * 2
    return None
