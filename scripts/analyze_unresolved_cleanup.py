#!/usr/bin/env python3
"""Classify unresolved Safe Cleanup spans without adding cleanup rules."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

_PROTECTED = {"não", "nao", "sem", "nunca", "jamais", "sim", "muito"}
_UNITS = {"mg", "ml", "g", "kg", "mcg", "miligrama", "miligramas"}


def analyze(
    quality_attribution: dict[str, object],
    cleanup_shadow: dict[str, object],
    events: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    issues = [
        item
        for item in quality_attribution.get("issues", [])
        if isinstance(item, dict)
        and item.get("safe_structural_candidate") is True
        and item.get("issue") == "adjacent_duplication"
    ]
    unique_spans = cleanup_shadow.get("unique_spans", [])
    cleanup_keys = {
        _span_key(item)
        for item in unique_spans
        if isinstance(item, dict)
    }
    candidate_keys = {_candidate_key(item) for item in issues}
    covered = [item for item in issues if _candidate_key(item) in cleanup_keys]
    unresolved = [item for item in issues if _candidate_key(item) not in cleanup_keys]
    cleanup_only = [
        item
        for item in unique_spans
        if isinstance(item, dict) and _span_key(item) not in candidate_keys
    ]
    segment_revisions = _segment_revisions(events or [])
    repeated_keys = Counter(_candidate_key(item)[1] for item in issues)

    classified: list[dict[str, object]] = []
    for item in unresolved:
        evidence = item.get("evidence") or {}
        duplicate = evidence.get("duplicate") if isinstance(evidence, dict) else {}
        duplicate = duplicate if isinstance(duplicate, dict) else {}
        block = [str(token) for token in duplicate.get("block", [])]
        repeated = [str(token) for token in duplicate.get("repeated_block", [])]
        segment_id = str(item.get("segment_id") or "unknown")
        reason = _classify_reason(block, repeated, repeated_keys[_candidate_key(item)[1]] > 1)
        classified.append(
            {
                "span_id": _span_id(segment_id, block, duplicate),
                "segment_id": segment_id,
                "rule": "exact_adjacent_duplication",
                "original": duplicate.get("text", ""),
                "status": "unresolved",
                "reason": reason,
                "first_seen_revision": segment_revisions.get(segment_id, {}).get("first"),
                "last_seen_revision": segment_revisions.get(segment_id, {}).get("last"),
                "snapshot_occurrences": 1,
                "protected": reason == "protected_context",
                "provider_origin": True,
                "evidence": evidence,
            }
        )

    category_counts = Counter(str(item["reason"]) for item in classified)
    return {
        "analysis": {
            "kind": "safe_cleanup_unresolved_span_analysis",
            "rules_added": 0,
            "cleanup_promoted": False,
        },
        "sets": {
            "quality_safe_adjacent_candidates": len(issues),
            "covered_by_cleanup": len(covered),
            "safe_but_unresolved": len(unresolved),
            "cleanup_only": len(cleanup_only),
        },
        "summary": {
            "unresolved_count": len(classified),
            "category_counts": dict(category_counts),
            "protected_unresolved": sum(item["protected"] for item in classified),
            "ambiguous_unresolved": category_counts.get("ambiguous_repetition", 0),
        },
        "unresolved_spans": classified,
        "covered_candidates": covered,
        "cleanup_only_spans": cleanup_only,
        "limitations": [
            "No new cleanup rule is inferred from this report.",
            "Speaker/boundary semantics require provider item metadata or human review.",
        ],
    }


def _classify_reason(
    block: list[str], repeated: list[str], repeated_across_segments: bool
) -> str:
    if repeated_across_segments:
        return "cross_segment_duplicate"
    normalized = [_normalize(token) for token in block + repeated]
    if set(normalized) & (_PROTECTED | _UNITS) or any(token.isdigit() for token in normalized):
        return "protected_context"
    left = " ".join(block)
    right = " ".join(repeated)
    if left == right:
        return "ambiguous_repetition"
    left_plain = _plain(left)
    right_plain = _plain(right)
    if left_plain == right_plain and left.casefold() != right.casefold():
        return "punctuation_variant"
    if _normalize(left) == _normalize(right) and left != right:
        return "case_variant"
    if len(block) >= 3:
        return "boundary_crossing"
    return "tokenization_variant"


def _candidate_key(item: dict[str, object]) -> tuple[str, str]:
    evidence = item.get("evidence") or {}
    duplicate = evidence.get("duplicate") if isinstance(evidence, dict) else {}
    duplicate_text = duplicate.get("text", "") if isinstance(duplicate, dict) else ""
    return str(item.get("segment_id")), _normalize(str(duplicate_text))


def _span_key(item: dict[str, object]) -> tuple[str, str]:
    return str(item.get("segment_id")), _normalize(str(item.get("original", "")))


def _span_id(segment_id: str, block: list[str], duplicate: dict[str, object]) -> str:
    normalized = "_".join(_normalize(token) for token in block)
    return (
        f"{segment_id}:adjacent_duplication:{normalized}:"
        f"{duplicate.get('token_start', 'unknown')}-{duplicate.get('token_end', 'unknown')}"
    )


def _segment_revisions(events: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for event in events:
        segment_id = event.get("segment_id")
        revision = event.get("revision")
        if not isinstance(segment_id, str) or not isinstance(revision, int):
            continue
        current = result.setdefault(segment_id, {"first": revision, "last": revision})
        current["first"] = min(current["first"], revision)
        current["last"] = max(current["last"], revision)
    return result


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ/]+", text.casefold()))


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ/]+", text.casefold()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-attribution", required=True, type=Path)
    parser.add_argument("--cleanup-shadow", required=True, type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    quality = json.loads(args.quality_attribution.read_text(encoding="utf-8"))
    cleanup = json.loads(args.cleanup_shadow.read_text(encoding="utf-8"))
    events = json.loads(args.events.read_text(encoding="utf-8")) if args.events else []
    report = analyze(quality, cleanup, events)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
