#!/usr/bin/env python3
"""Investigate cross-segment cleanup candidates without applying cleanup."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

_PROTECTED = {"não", "nao", "sem", "nunca", "jamais", "sim", "muito"}
_UNITS = {"mg", "ml", "g", "kg", "mcg", "miligrama", "miligramas"}


def analyze(
    unresolved: dict[str, object],
    events: list[dict[str, object]],
    *,
    proximity_ms: int = 5_000,
    boundary_token_limit: int = 8,
) -> dict[str, object]:
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    positions = {str(event.get("segment_id")): index for index, event in enumerate(committed)}
    candidates = unresolved.get("unresolved_spans", [])
    cross_candidates = [
        item for item in candidates if item.get("reason") == "cross_segment_duplicate"
    ]
    punctuation_candidates = [
        item for item in candidates if item.get("reason") == "punctuation_variant"
    ]
    evidence: list[dict[str, object]] = []
    for item in [*cross_candidates, *punctuation_candidates]:
        segment_id = str(item.get("segment_id"))
        index = positions.get(segment_id)
        previous = committed[index - 1] if index is not None and index > 0 else None
        current = committed[index] if index is not None else None
        boundary = _boundary_evidence(
            previous,
            current,
            proximity_ms=proximity_ms,
            boundary_token_limit=boundary_token_limit,
        )
        duplicate = ((item.get("evidence") or {}).get("duplicate") or {})
        block = [str(token) for token in duplicate.get("block", [])]
        protected = _protected(block)
        eligible = (
            item.get("reason") == "cross_segment_duplicate"
            and boundary["boundary_adjacent"]
            and boundary["temporally_close"]
            and boundary["exact_boundary_overlap_tokens"] > 0
            and not protected
        )
        evidence.append(
            {
                **item,
                "candidate_class": item.get("reason"),
                "protected": protected,
                "eligible_for_cross_segment_cleanup": eligible,
                "boundary": boundary,
            }
        )

    counts = Counter(str(item["candidate_class"]) for item in evidence)
    eligible = [
        item for item in evidence if item["eligible_for_cross_segment_cleanup"] is True
    ]
    cross_evidence = [
        item for item in evidence if item["candidate_class"] == "cross_segment_duplicate"
    ]
    return {
        "analysis": {
            "kind": "cross_segment_safe_cleanup_investigation",
            "rules_added": 0,
            "cleanup_applied": False,
            "proximity_threshold_ms": proximity_ms,
            "boundary_token_limit": boundary_token_limit,
        },
        "summary": {
            "cross_segment_candidates": len(cross_candidates),
            "cross_segment_eligible": len(eligible),
            "cross_segment_exact_boundary_overlap": sum(
                item["boundary"]["exact_boundary_overlap_tokens"] > 0
                for item in cross_evidence
            ),
            "cross_segment_temporally_close": sum(
                item["boundary"]["temporally_close"] for item in cross_evidence
            ),
            "cross_segment_rejected_no_exact_boundary": sum(
                item["boundary"]["exact_boundary_overlap_tokens"] == 0
                for item in cross_evidence
            ),
            "cross_segment_rejected_protected": sum(
                item["protected"] for item in cross_evidence
            ),
            "cross_segment_removed": 0,
            "punctuation_candidates": len(punctuation_candidates),
            "punctuation_removed": 0,
            "ambiguous_untouched": sum(
                item.get("reason") == "ambiguous_repetition" for item in candidates
            ),
            "protected_untouched": sum(
                item.get("reason") == "protected_context" for item in candidates
            ),
            "clinical_changes": 0,
            "candidate_class_counts": dict(counts),
        },
        "candidates": evidence,
        "boundary_index": {
            "source": "previous_tail_tokens_to_incoming_head_tokens",
            "segments_indexed": len(committed),
        },
        "limitations": [
            "Eligibility is evidence only; no projected_text_clean mutation is applied.",
            "Speaker-boundary semantics require provider item metadata or human review.",
        ],
    }


def _boundary_evidence(
    previous: dict[str, object] | None,
    current: dict[str, object] | None,
    *,
    proximity_ms: int,
    boundary_token_limit: int,
) -> dict[str, object]:
    if previous is None or current is None:
        return {
            "boundary_adjacent": False,
            "temporally_close": False,
            "temporal_gap_ms": None,
            "previous_tail_tokens": [],
            "incoming_head_tokens": [],
            "exact_boundary_overlap_tokens": 0,
        }
    previous_tokens = _tokens(str(previous.get("text") or ""))
    current_tokens = _tokens(str(current.get("text") or ""))
    tail = previous_tokens[-boundary_token_limit:]
    head = current_tokens[:boundary_token_limit]
    start = current.get("start_ms")
    end = previous.get("end_ms")
    gap = start - end if isinstance(start, int) and isinstance(end, int) else None
    return {
        "boundary_adjacent": True,
        "temporally_close": gap is not None and abs(gap) <= proximity_ms,
        "temporal_gap_ms": gap,
        "previous_tail_tokens": tail,
        "incoming_head_tokens": head,
        "exact_boundary_overlap_tokens": _suffix_prefix_overlap(tail, head),
        "previous_segment_id": previous.get("segment_id"),
        "current_segment_id": current.get("segment_id"),
    }


def _protected(tokens: list[str]) -> bool:
    normalized = [_normalize(token) for token in tokens]
    return bool(
        set(normalized) & (_PROTECTED | _UNITS)
        or any(token.isdigit() for token in normalized)
    )


def _tokens(text: str) -> list[str]:
    return [_normalize(token) for token in text.split() if _normalize(token)]


def _normalize(text: str) -> str:
    return re.sub(r"[^\wÀ-ÿ/]+", "", text.casefold())


def _suffix_prefix_overlap(left: list[str], right: list[str]) -> int:
    for count in range(min(len(left), len(right)), 0, -1):
        if left[-count:] == right[:count]:
            return count
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unresolved", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--proximity-ms", type=int, default=5_000)
    args = parser.parse_args()
    unresolved = json.loads(args.unresolved.read_text(encoding="utf-8"))
    events = json.loads(args.events.read_text(encoding="utf-8"))
    report = analyze(unresolved, events, proximity_ms=args.proximity_ms)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
