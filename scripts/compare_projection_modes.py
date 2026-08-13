#!/usr/bin/env python3
"""Compare full-history and bounded incremental committed projection offline."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from astera_live_transcriber.application.services.committed_transcript_projector import (
    CommittedTranscriptProjector,
    IncrementalCommittedTranscriptProjector,
)
from astera_live_transcriber.application.services.provider_segment_store import (
    ProviderSegmentState,
)


def compare(events: list[dict[str, object]], working_set_size: int) -> dict[str, object]:
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    full_states: list[ProviderSegmentState] = []
    incremental = IncrementalCommittedTranscriptProjector(working_set_size)
    rows: list[dict[str, object]] = []
    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: {"commits": 0, "mismatches": 0, "full_ops": 0, "incremental_ops": 0}
    )

    for event in committed:
        state = ProviderSegmentState(
            segment_id=str(event.get("segment_id")),
            revision=int(event.get("revision") or 0),
            committed_text=str(event.get("text") or ""),
            start_ms=int(event.get("start_ms") or 0),
            end_ms=int(event["end_ms"]) if isinstance(event.get("end_ms"), int) else None,
            status="committed",
        )
        full_states = [
            item for item in full_states if item.segment_id != state.segment_id
        ] + [state]
        ordered = sorted(full_states, key=lambda item: (item.start_ms, item.segment_id))
        full = CommittedTranscriptProjector().project(ordered)
        actual = incremental.project(state)
        metrics = incremental.projection_metrics()
        mismatch = actual.text != full.text
        bucket = state.start_ms // 300_000
        buckets[bucket]["commits"] += 1
        buckets[bucket]["mismatches"] += int(mismatch)
        buckets[bucket]["full_ops"] += len(full.operations)
        buckets[bucket]["incremental_ops"] += int(metrics["projection_ops_added"])
        rows.append(
            {
                "segment_id": state.segment_id,
                "revision": state.revision,
                "start_ms": state.start_ms,
                "byte_equivalent": not mismatch,
                "full_output_chars": len(full.text),
                "incremental_output_chars": len(actual.text),
                "full_historical_segments": len(ordered),
                "full_historical_tokens": sum(
                    len((item.committed_text or "").split()) for item in ordered
                ),
                "full_projection_ops": len(full.operations),
                "incremental_projection_ops": metrics["projection_ops_added"],
                "incremental_historical_segments_scanned": metrics[
                    "historical_segments_scanned"
                ],
                "incremental_historical_tokens_scanned": metrics[
                    "historical_tokens_scanned"
                ],
                "incremental_projector_input_chars": metrics["projector_input_chars"],
                "incremental_projector_output_chars": metrics["projector_output_chars"],
                "active_segment_count": incremental.projection_state()["active_segment_count"],
                "superseded_segment_count": incremental.projection_state()[
                    "superseded_segment_count"
                ],
            }
        )

    return {
        "working_set_size": working_set_size,
        "commit_count": len(rows),
        "byte_equivalent_commit_count": sum(row["byte_equivalent"] for row in rows),
        "mismatch_count": sum(not row["byte_equivalent"] for row in rows),
        "full_projection_ops": sum(int(row["full_projection_ops"]) for row in rows),
        "incremental_projection_ops": sum(
            int(row["incremental_projection_ops"]) for row in rows
        ),
        "max_full_historical_segments": max(
            (int(row["full_historical_segments"]) for row in rows), default=0
        ),
        "max_incremental_segments_scanned": max(
            (int(row["incremental_historical_segments_scanned"]) for row in rows),
            default=0,
        ),
        "timeline": [
            {"minute_bucket_5m": bucket, **values}
            for bucket, values in sorted(buckets.items())
        ],
        "commits": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--working-set-size", type=int, default=4)
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    report = compare(events, args.working_set_size)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {key: value for key, value in report.items() if key != "commits"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
