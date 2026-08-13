#!/usr/bin/env python3
"""Explain the first divergence between legacy and incremental projection."""

import argparse
import json
from pathlib import Path
from typing import Any

from astera_live_transcriber.application.services.committed_transcript_projector import (
    CommittedTranscriptProjector,
    IncrementalCommittedTranscriptProjector,
    ProjectedTranscript,
)
from astera_live_transcriber.application.services.provider_segment_store import (
    ProviderSegmentState,
)


def _state_from_event(event: dict[str, object]) -> ProviderSegmentState:
    return ProviderSegmentState(
        segment_id=str(event.get("segment_id") or "unknown"),
        revision=int(event.get("revision") or 0),
        committed_text=str(event.get("text") or ""),
        start_ms=int(event.get("start_ms") or 0),
        end_ms=(
            int(event["end_ms"])
            if isinstance(event.get("end_ms"), int)
            else None
        ),
        status="committed",
    )


def _operation_as_dict(operation: Any) -> dict[str, object]:
    return {
        "operation": operation.operation,
        "segment_id": operation.segment_id,
        "details": operation.details,
    }


def _first_difference(left: str, right: str, context: int = 120) -> dict[str, object] | None:
    limit = min(len(left), len(right))
    offset = next(
        (index for index in range(limit) if left[index] != right[index]),
        limit if len(left) != len(right) else None,
    )
    if offset is None:
        return None
    return {
        "offset": offset,
        "legacy_context": left[max(0, offset - context) : offset + context],
        "incremental_context": right[max(0, offset - context) : offset + context],
    }


def _cause_candidate(
    legacy: ProjectedTranscript,
    incremental: ProjectedTranscript,
    working_set: set[str],
    active_segments: set[str],
    superseded_segments: set[str],
    legacy_current_segments: set[str],
) -> str:
    legacy_projected_segments = {segment.segment_id for segment in legacy.segments}
    incremental_projected_segments = {
        segment.segment_id for segment in incremental.segments
    }
    prematurely_hidden = (legacy_current_segments - active_segments) & legacy_projected_segments
    if prematurely_hidden and prematurely_hidden & incremental_projected_segments == set():
        return "premature_supersede"
    legacy_targets = {
        str(operation.details["target_segment_id"])
        for operation in legacy.operations
        if operation.details.get("target_segment_id")
    }
    missing_targets = legacy_targets - working_set
    if missing_targets:
        return "missing_historical_overlap"
    if legacy_targets and legacy_targets <= active_segments and not legacy.operations:
        return "working_set_too_small"
    if legacy.operations and not incremental.operations:
        if superseded_segments & legacy_targets:
            return "premature_supersede"
        return "working_set_too_small"
    if any(
        operation.details.get("reason") == "authoritative_snapshot_replaces_overlapping_segment"
        for operation in legacy.operations
    ):
        return "snapshot_replacement"
    if [segment.segment_id for segment in legacy.segments] != [
        segment.segment_id for segment in incremental.segments
    ]:
        return "ordering_difference"
    return "other"


def analyze(events: list[dict[str, object]], working_set_size: int) -> dict[str, object]:
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    legacy_states: list[ProviderSegmentState] = []
    incremental = IncrementalCommittedTranscriptProjector(working_set_size)
    rows: list[dict[str, object]] = []
    first_divergence: dict[str, object] | None = None

    for sequence, event in enumerate(committed, start=1):
        incoming = _state_from_event(event)
        debug_before = incremental.projection_debug_context(incoming)
        legacy_states = [
            state for state in legacy_states if state.segment_id != incoming.segment_id
        ] + [incoming]
        legacy = CommittedTranscriptProjector().project(
            sorted(legacy_states, key=lambda state: (state.start_ms, state.segment_id))
        )
        actual = incremental.project(incoming)
        projection_state = incremental.projection_state()
        mismatch = legacy.text != actual.text
        difference = _first_difference(legacy.text, actual.text) if mismatch else None
        row = {
            "commit_index": sequence,
            "segment_id": incoming.segment_id,
            "revision": incoming.revision,
            "start_ms": incoming.start_ms,
            "byte_equivalent": not mismatch,
            "legacy_projected_text": legacy.text,
            "incremental_projected_text": actual.text,
            "first_difference": difference,
            "working_set_segments": projection_state["local_working_set"],
            "active_segments": projection_state["active_segments"],
            "superseded_segments": projection_state["superseded_segments"],
            "legacy_current_segments": [state.segment_id for state in legacy_states],
            "legacy_projected_segments": [
                segment.segment_id for segment in legacy.segments
            ],
            "incremental_projected_segments": [
                segment.segment_id for segment in actual.segments
            ],
            "legacy_operations": [_operation_as_dict(operation) for operation in legacy.operations],
            "incremental_operations": [
                _operation_as_dict(operation) for operation in actual.operations
            ],
            "projection_metrics": incremental.projection_metrics(),
            "state_before": debug_before["previous_state"],
            "incoming_segment": debug_before["incoming_segment"],
            "superseded_eligible": debug_before["superseded_eligible"],
            "boundary_context": debug_before["boundary_context"],
            "reactivation_decision": debug_before["decision"],
        }
        row["cause_candidate"] = (
            _cause_candidate(
                legacy,
                actual,
                set(str(value) for value in projection_state["local_working_set"]),
                set(str(value) for value in projection_state["active_segments"]),
                set(str(value) for value in projection_state["superseded_segments"]),
                {state.segment_id for state in legacy_states},
            )
            if mismatch
            else None
        )
        rows.append(row)
        if mismatch and first_divergence is None:
            first_divergence = {
                "commit_index": sequence,
                "segment_id": incoming.segment_id,
                "revision": incoming.revision,
                "cause_candidate": row["cause_candidate"],
            }

    cause_counts: dict[str, int] = {}
    for row in rows:
        cause = row.get("cause_candidate")
        if isinstance(cause, str):
            cause_counts[cause] = cause_counts.get(cause, 0) + 1

    return {
        "working_set_size": working_set_size,
        "commit_count": len(rows),
        "equivalent_commit_count": sum(bool(row["byte_equivalent"]) for row in rows),
        "mismatch_count": sum(not bool(row["byte_equivalent"]) for row in rows),
        "first_divergence": first_divergence,
        "mismatch_commits": [
            {
                "commit_index": row["commit_index"],
                "segment_id": row["segment_id"],
                "revision": row["revision"],
                "cause_candidate": row["cause_candidate"],
            }
            for row in rows
            if not row["byte_equivalent"]
        ],
        "cause_counts": cause_counts,
        "commits": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--working-set-size", type=int, default=4)
    args = parser.parse_args()
    report = analyze(json.loads(args.events.read_text(encoding="utf-8")), args.working_set_size)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {key: value for key, value in report.items() if key != "commits"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
