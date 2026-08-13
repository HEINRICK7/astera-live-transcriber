from dataclasses import dataclass

from astera_live_transcriber.domain.transcription_intelligence import (
    CorrectionCandidate,
    NormalizationDecision,
    RiskClass,
    TranscriptRepresentation,
)


@dataclass(frozen=True, slots=True)
class SafeNormalizationResult:
    representation: TranscriptRepresentation
    decisions: tuple[NormalizationDecision, ...]


class SafeNormalizer:
    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    def normalize(
        self,
        raw_text: str,
        candidates: tuple[CorrectionCandidate, ...],
    ) -> SafeNormalizationResult:
        return self._evaluate(raw_text, candidates, enabled=self._enabled)

    def preview(
        self,
        raw_text: str,
        candidates: tuple[CorrectionCandidate, ...],
    ) -> SafeNormalizationResult:
        """Evaluate active-policy decisions without changing the live output."""
        return self._evaluate(raw_text, candidates, enabled=True)

    def _evaluate(
        self,
        raw_text: str,
        candidates: tuple[CorrectionCandidate, ...],
        *,
        enabled: bool,
    ) -> SafeNormalizationResult:
        normalized = raw_text
        decisions: list[NormalizationDecision] = []
        if enabled:
            grouped: dict[str, dict[str, CorrectionCandidate]] = {}
            for candidate in candidates:
                options = grouped.setdefault(candidate.observed.casefold(), {})
                existing = options.get(candidate.canonical.casefold())
                if existing is None or candidate.final_score > existing.final_score:
                    options[candidate.canonical.casefold()] = candidate
            for options in grouped.values():
                group = sorted(options.values(), key=lambda item: item.final_score, reverse=True)
                candidate = group[0]
                second_score = group[1].final_score if len(group) > 1 else 0.0
                if (
                    candidate.final_score <= 0.96
                    or candidate.final_score - second_score < 0.05
                    or candidate.evidence_occurrences < 3
                    or candidate.risk_class is not RiskClass.LOW
                    or candidate.observed not in normalized
                ):
                    continue
                normalized = normalized.replace(candidate.observed, candidate.canonical)
                decisions.append(
                    NormalizationDecision(
                        original=candidate.observed,
                        replacement=candidate.canonical,
                        confidence=candidate.final_score,
                        reason="stable_revision+known_low_risk_vocabulary",
                        source="adaptive_transcription",
                    )
                )
        return SafeNormalizationResult(
            representation=TranscriptRepresentation(raw_text=raw_text, normalized_text=normalized),
            decisions=tuple(decisions),
        )
