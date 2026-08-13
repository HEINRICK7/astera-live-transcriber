#!/usr/bin/env python3
"""Attribute structural transcript artifacts from a captured events.json."""

import argparse
import json
from pathlib import Path

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    analyze_quality_attribution,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise SystemExit("events.json must contain a JSON array")
    report = analyze_quality_attribution(events)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
