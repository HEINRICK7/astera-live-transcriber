"""Build reproducible manual-review samples for long clinical sessions.

This module deliberately does not manufacture a reference transcript.  It only
selects time windows, records provider evidence, and reports which requested
clinical concepts were or were not visible in the captured committed events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_TARGETS: dict[str, tuple[str, ...]] = {
    "anamnese_vomito": ("vômito",),
    "anamnese_diarreia": ("diarreia",),
    "anamnese_doenca_hepatica": ("doença no fígado",),
    "negacao": ("nega", "não", "não tem"),
    "numero_hemacias": ("13 hemácias", "sete"),
    "numero_manifestacoes": ("cinco manifestações",),
    "numero_rni": ("RNI",),
    "exame_fisico": ("exame físico",),
    "exame_maniobra": ("respira", "palpasse o fígado"),
    "medicamento": ("ibuprofeno", "medicação"),
    "cirrose": ("cirrose",),
    "hipertensao_portal": ("hipertensão portal",),
    "varizes_esofagianas": ("varizes esofagianas",),
    "vasoativos": ("terlipressina", "somatostatina", "octreotide"),
    "child_pugh": ("child-pugh", "A, o B e o C"),
}

_ALIAS_TARGETS = {
    "terlipressina": ("terlipressina", "teriperaquina", "terapicina"),
    "child-pugh": ("child-pugh", "gravidade da doença hepática", "A, o B e o C"),
    "RNI": ("rni", "tempo de protrombina"),
    "sete": ("sete", "7"),
}


@dataclass(frozen=True, slots=True)
class ClinicalSample:
    sample_id: str
    reason: str
    categories: tuple[str, ...]
    target_terms: tuple[str, ...]
    matched_terms: tuple[str, ...]
    observed_aliases: dict[str, tuple[str, ...]]
    missing_terms: tuple[str, ...]
    anchor_segment_id: str
    anchor_revision: int | None
    anchor_start_ms: int
    anchor_end_ms: int
    clip_start_ms: int
    clip_end_ms: int
    source_event_index: int
    raw_text: str
    structural_snapshot: str
    adaptive_snapshot: str
    clean_snapshot: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "reason": self.reason,
            "categories": list(self.categories),
            "target_terms": list(self.target_terms),
            "matched_terms": list(self.matched_terms),
            "observed_aliases": {
                term: list(aliases) for term, aliases in self.observed_aliases.items()
            },
            "missing_terms": list(self.missing_terms),
            "anchor": {
                "segment_id": self.anchor_segment_id,
                "revision": self.anchor_revision,
                "start_ms": self.anchor_start_ms,
                "end_ms": self.anchor_end_ms,
                "source_event_index": self.source_event_index,
            },
            "clip": {
                "start_ms": self.clip_start_ms,
                "end_ms": self.clip_end_ms,
                "duration_ms": self.clip_end_ms - self.clip_start_ms,
            },
            "reference_status": "pending_manual_validation",
            "hypothesis_status": {
                "raw_committed": "snapshot_for_review",
                "structural": "cumulative_snapshot_not_segment_isolated",
                "adaptive": "shadow_equals_structural_snapshot",
                "clean": (
                    "not_captured_in_source_run"
                    if self.clean_snapshot is None
                    else "captured"
                ),
            },
        }


def committed_events(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Return committed events with their original array indexes."""
    return [
        (index, event)
        for index, event in enumerate(events)
        if event.get("type") == "transcript.committed" and str(event.get("text") or "").strip()
    ]


def build_sample_plan(
    events: list[dict[str, Any]],
    *,
    duration_ms: int,
    sample_duration_ms: int = 45_000,
    bucket_ms: int = 300_000,
    targets: dict[str, tuple[str, ...]] | None = None,
) -> list[ClinicalSample]:
    """Select five time-distributed samples plus targeted clinical samples."""
    commits = committed_events(events)
    if not commits:
        raise ValueError("events contain no non-empty transcript.committed entries")
    target_map = targets or DEFAULT_TARGETS
    samples: list[ClinicalSample] = []
    used_anchors: set[str] = set()

    max_bucket = max(1, (duration_ms + bucket_ms - 1) // bucket_ms)
    for bucket in range(min(5, max_bucket)):
        bucket_start = bucket * bucket_ms
        bucket_end = min(duration_ms, (bucket + 1) * bucket_ms)
        midpoint = (bucket_start + bucket_end) // 2
        index, event = min(
            commits,
            key=lambda item: abs(_anchor_ms(item[1]) - midpoint),
        )
        sample = _make_sample(
            len(samples) + 1,
            "time_bucket",
            (f"{bucket_start // 60_000}-{bucket_end // 60_000}min",),
            (),
            index,
            event,
            duration_ms,
            sample_duration_ms,
        )
        samples.append(sample)
        used_anchors.add(sample.anchor_segment_id)

    for category, requested in target_map.items():
        candidates = [
            (index, event)
            for index, event in commits
            if _matches_any(str(event.get("text") or ""), requested)
        ]
        if not candidates:
            samples.append(
                _make_missing_sample(
                    len(samples) + 1,
                    category,
                    requested,
                    duration_ms,
                    sample_duration_ms,
                )
            )
            continue
        index, event = max(
            candidates,
            key=lambda item: (
                sum(_matches(term, str(item[1].get("text") or "")) for term in requested),
                -len(str(item[1].get("text") or "")),
            ),
        )
        sample = _make_sample(
            len(samples) + 1,
            "clinical_target",
            (category,),
            requested,
            index,
            event,
            duration_ms,
            sample_duration_ms,
        )
        samples.append(sample)
        used_anchors.add(sample.anchor_segment_id)

    return samples


def _make_sample(
    number: int,
    reason: str,
    categories: tuple[str, ...],
    requested: tuple[str, ...],
    index: int,
    event: dict[str, Any],
    duration_ms: int,
    sample_duration_ms: int,
) -> ClinicalSample:
    start_ms = _int(event.get("start_ms"))
    end_ms = _int(event.get("end_ms"))
    anchor_end = max(start_ms, end_ms)
    anchor = (start_ms + anchor_end) // 2
    half = sample_duration_ms // 2
    clip_start = max(0, min(anchor - half, max(0, duration_ms - sample_duration_ms)))
    clip_end = min(duration_ms, clip_start + sample_duration_ms)
    text = str(event.get("text") or "").strip()
    matched = tuple(term for term in requested if _matches(term, text))
    observed_aliases = {
        term: _observed_aliases(term, text) for term in matched
    }
    missing = tuple(term for term in requested if term not in matched)
    return ClinicalSample(
        sample_id=f"sample_{number:03d}",
        reason=reason,
        categories=categories,
        target_terms=requested,
        matched_terms=matched,
        observed_aliases=observed_aliases,
        missing_terms=missing,
        anchor_segment_id=str(event.get("segment_id") or f"event_{index}"),
        anchor_revision=_optional_int(event.get("revision")),
        anchor_start_ms=start_ms,
        anchor_end_ms=anchor_end,
        clip_start_ms=clip_start,
        clip_end_ms=clip_end,
        source_event_index=index,
        raw_text=text,
        structural_snapshot=str(event.get("projected_text") or "").strip(),
        adaptive_snapshot=str(event.get("projected_text") or "").strip(),
        clean_snapshot=(
            str(event.get("projected_text_clean") or "").strip()
            if event.get("projected_text_clean")
            else None
        ),
    )


def _make_missing_sample(
    number: int,
    category: str,
    requested: tuple[str, ...],
    duration_ms: int,
    sample_duration_ms: int,
) -> ClinicalSample:
    return ClinicalSample(
        sample_id=f"sample_{number:03d}",
        reason="clinical_target_not_found_in_captured_commits",
        categories=(category,),
        target_terms=requested,
        matched_terms=(),
        observed_aliases={},
        missing_terms=requested,
        anchor_segment_id="",
        anchor_revision=None,
        anchor_start_ms=0,
        anchor_end_ms=0,
        clip_start_ms=0,
        clip_end_ms=min(duration_ms, sample_duration_ms),
        source_event_index=-1,
        raw_text="",
        structural_snapshot="",
        adaptive_snapshot="",
        clean_snapshot=None,
    )


def _anchor_ms(event: dict[str, Any]) -> int:
    start = _int(event.get("start_ms"))
    end = max(start, _int(event.get("end_ms")))
    return (start + end) // 2


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _matches_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_matches(term, text) for term in terms)


def _matches(term: str, text: str) -> bool:
    return bool(_observed_aliases(term, text))


def _observed_aliases(term: str, text: str) -> tuple[str, ...]:
    haystack = _normalize(text)
    aliases = _ALIAS_TARGETS.get(term, (term,))
    return tuple(alias for alias in aliases if _normalize(alias) in haystack)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
