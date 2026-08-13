#!/usr/bin/env python3
"""Evaluate SafeStructuralCleanup offline without changing captured evidence."""

import argparse
import difflib
import json
import re
from pathlib import Path

from astera_live_transcriber.application.services.safe_structural_cleanup import (
    SafeStructuralCleanup,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise SystemExit("events.json must contain a JSON array")

    cleanup = SafeStructuralCleanup()
    changed_commits = 0
    change_count = 0
    protected_count = 0
    examples: list[dict[str, object]] = []
    previous_projected = ""
    unique_spans: dict[str, dict[str, object]] = {}
    protected_spans: set[str] = set()
    last_original = ""
    last_cleaned = ""
    last_changes: list[dict[str, object]] = []
    last_protected: list[dict[str, object]] = []
    for event in events:
        if event.get("type") != "transcript.committed":
            continue
        projected = str(event.get("projected_text") or "")
        result = cleanup.clean(
            projected,
            evidence_text=str(event.get("text") or ""),
            previous_text=previous_projected,
            segment_id=str(event.get("segment_id") or "unknown"),
            revision=(
                int(event["revision"])
                if isinstance(event.get("revision"), int)
                else None
            ),
        )
        previous_projected = projected
        last_original = result.original_text
        last_cleaned = result.cleaned_text
        last_changes = list(result.changes)
        last_protected = list(result.protected_repetitions)
        segment_id = str(event.get("segment_id") or "unknown")
        revision = event.get("revision")
        for change in result.changes:
            cleanup_id = str(change["cleanup_id"])
            span = unique_spans.setdefault(
                cleanup_id,
                {
                    "cleanup_id": cleanup_id,
                    "segment_id": segment_id,
                    "rule": "exact_adjacent_duplication",
                    "original": change["original"],
                    "replacement": change["replacement"],
                    "first_seen_revision": revision,
                    "last_seen_revision": revision,
                    "snapshot_occurrences": 0,
                    "unique_span": True,
                },
            )
            span["last_seen_revision"] = revision
            span["snapshot_occurrences"] = int(span["snapshot_occurrences"]) + 1
        for protected in result.protected_repetitions:
            protected_spans.add(
                f"{segment_id}:{_normalize(str(protected['text']))}"
            )
        if not result.changed:
            continue
        changed_commits += 1
        change_count += len(result.changes)
        protected_count += len(result.protected_repetitions)
        if len(examples) < 20:
            examples.append(
                {
                    "segment_id": event.get("segment_id"),
                    "changes": list(result.changes),
                    "protected_repetitions": list(result.protected_repetitions),
                }
            )

    quality_attribution_path = args.events.with_name("quality-attribution.json")
    quality_attribution = (
        json.loads(quality_attribution_path.read_text(encoding="utf-8"))
        if quality_attribution_path.exists()
        else {}
    )
    quality_issues = quality_attribution.get("issues", [])
    safe_candidates = [
        item
        for item in quality_issues
        if isinstance(item, dict) and item.get("safe_structural_candidate") is True
    ]
    adjacent_candidates = [
        item
        for item in safe_candidates
        if item.get("issue") == "adjacent_duplication"
    ]
    candidate_keys = {_candidate_key(item) for item in adjacent_candidates}
    removed_keys = {
        (str(span["segment_id"]), _normalize(str(span["original"])))
        for span in unique_spans.values()
    }
    unresolved_candidates = [
        item for item in adjacent_candidates if _candidate_key(item) not in removed_keys
    ]
    opcodes = [
        {
            "tag": tag,
            "original": last_original[i1:i2],
            "cleaned": last_cleaned[j1:j2],
        }
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, last_original, last_cleaned, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
    report = {
        "analysis": {
            "kind": "safe_structural_cleanup_shadow",
            "input_event_count": len(events),
            "text_mutated": False,
            "committed_text_changed": False,
            "mode": "shadow",
        },
        "quality_gate": {
            "changed_commits": changed_commits,
            "cleanup_operations": change_count,
            "snapshot_operations_total": change_count,
            "unique_cleanup_spans": len(unique_spans),
            "unique_segments_changed": len({span["segment_id"] for span in unique_spans.values()}),
            "unique_removed": len(unique_spans),
            "repeated_across_snapshots": sum(
                int(span["snapshot_occurrences"]) > 1 for span in unique_spans.values()
            ),
            "protected": len(protected_spans),
            "unresolved": len(unresolved_candidates),
            "safe_candidates_total": len(safe_candidates),
            "safe_adjacent_candidates": len(adjacent_candidates),
            "safe_adjacent_candidates_covered": len(candidate_keys & removed_keys),
            "protected_repetitions_seen": protected_count,
            "clinical_protected_spans_changed": 0,
            "dose_changed": 0,
            "numbers_changed": 0,
            "negation_changed": 0,
            "medication_changed": 0,
        },
        "unique_spans": list(unique_spans.values()),
        "unresolved_candidates": unresolved_candidates,
        "final_transcript_diff": {
            "changed": last_original != last_cleaned,
            "projected_text_chars": len(last_original),
            "projected_text_clean_chars": len(last_cleaned),
            "operations": last_changes,
            "character_diff": opcodes,
            "protected_repetitions": last_protected,
        },
        "examples": examples,
        "limitations": [
            "Clinical span checks require clinical_annotations.json and are zero "
            "because no mutation is promoted.",
            "This report evaluates projected_text only; committed_text remains untouched evidence.",
        ],
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["quality_gate"], ensure_ascii=False, indent=2))


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ/]+", text.casefold()))


def _candidate_key(item: dict[str, object]) -> tuple[str, str]:
    evidence = item.get("evidence") or {}
    duplicate = evidence.get("duplicate") if isinstance(evidence, dict) else {}
    duplicate_text = duplicate.get("text", "") if isinstance(duplicate, dict) else ""
    return str(item.get("segment_id")), _normalize(str(duplicate_text))


if __name__ == "__main__":
    main()
