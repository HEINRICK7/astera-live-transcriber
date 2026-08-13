from collections.abc import Sequence
from difflib import SequenceMatcher

from astera_live_transcriber.domain.transcription_intelligence import VocabularyTerm


class VocabularyMatcher:
    def match(
        self,
        observed: str,
        vocabulary: Sequence[VocabularyTerm],
    ) -> list[tuple[VocabularyTerm, float]]:
        raise NotImplementedError


class RapidFuzzVocabularyMatcher(VocabularyMatcher):
    """Candidate generation only; similarity never authorizes a correction."""

    def match(
        self,
        observed: str,
        vocabulary: Sequence[VocabularyTerm],
    ) -> list[tuple[VocabularyTerm, float]]:
        if not vocabulary:
            return []
        choices = [term.canonical for term in vocabulary]
        try:
            from rapidfuzz import fuzz, process

            matches = process.extract(observed, choices, scorer=fuzz.WRatio, limit=5)
            return [(vocabulary[index], float(score)) for _, score, index in matches]
        except ImportError:
            scored = [
                (
                    term,
                    SequenceMatcher(None, observed.lower(), term.canonical.lower()).ratio() * 100,
                )
                for term in vocabulary
            ]
            return sorted(scored, key=lambda item: item[1], reverse=True)[:5]
