#!/usr/bin/env python3
"""Generate an offline structural report from a realtime events.json."""

import argparse
import json
from pathlib import Path

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    analyze_long_session,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-label")
    parser.add_argument("--pause-threshold-ms", type=int, default=60_000)
    parser.add_argument("--timeline-bucket-ms", type=int, default=300_000)
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise SystemExit("events.json must contain a JSON array")
    report = analyze_long_session(
        events,
        source_label=args.source_label,
        pause_threshold_ms=args.pause_threshold_ms,
        timeline_bucket_ms=args.timeline_bucket_ms,
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["session"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
