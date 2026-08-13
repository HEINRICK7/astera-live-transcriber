from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class RetentionPolicy(StrEnum):
    NONE = "none"
    SESSION_ONLY = "session_only"
    DERIVED_PATTERNS_ONLY = "derived_patterns_only"
    EXPLICIT_DATASET = "explicit_dataset"


class RiskClass(StrEnum):
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class TranscriptRepresentation:
    raw_text: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class NormalizationDecision:
    original: str
    replacement: str
    confidence: float
    reason: str
    source: str


@dataclass(slots=True)
class RevisionPattern:
    provider: str
    observed: str
    resolved_as: str
    occurrences: int = 1
    confidence: float = 0.5
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class SessionVocabularyEntry:
    term: str
    count: int = 1
    stability: float = 0.0
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class VocabularyTerm:
    canonical: str
    aliases: tuple[str, ...] = ()
    domain: str | None = None
    source: str = "unknown"
    risk_class: RiskClass = RiskClass.HIGH


@dataclass(frozen=True, slots=True)
class CorrectionCandidate:
    observed: str
    canonical: str
    lexical_score: float
    revision_score: float
    repetition_score: float
    vocabulary_score: float
    provider_score: float | None
    risk_penalty: float
    final_score: float
    risk_class: RiskClass
    evidence_occurrences: int = 0
    source: str = "vocabulary"
