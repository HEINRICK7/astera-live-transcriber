#!/usr/bin/env python3
"""Convert the visible technical trace excerpt into an auditable events.json.

The source trace is intentionally allowed to be incomplete.  This parser never
invents missing timestamps, sequences, word spans, or events; the resulting
events.json is therefore suitable for partial/offline analysis only.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TRANSCRIPT_TYPES = {
    "transcript.partial",
    "transcript.revised",
    "transcript.committed",
}


def parse_trace(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("["):
            continue
        event_type, _, payload = line.partition("]")
        event_type = event_type.removeprefix("[")
        payload = payload.strip()
        if event_type in TRANSCRIPT_TYPES:
            event = _parse_transcript(event_type, payload)
        elif event_type == "websocket.closed":
            event = _parse_websocket(payload)
        elif event_type in {"speech.started", "audio.completed", "session.completed"}:
            event = {"type": event_type}
        else:
            continue
        event["technical"] = {
            **(event.get("technical") or {}),
            "source_trace_line": line_number,
            "source_trace_kind": "excerpt",
        }
        events.append(event)
    return events


def _parse_transcript(event_type: str, payload: str) -> dict[str, Any]:
    event: dict[str, Any] = {"type": event_type}
    for name, pattern in (
        ("provider", r"provider=(\S+)"),
        ("segment_id", r"segment=(\S+)"),
        ("revision", r"revision=(\d+)"),
        ("start_ms", r"start=(\d+)ms"),
        ("end_ms", r"end=(\d+)ms"),
        ("sequence", r"seq=(\d+)"),
        ("source", r"source=(\S+)"),
        ("operation", r"op=(\S+)"),
        ("segment_overlap", r"segment_overlap=(\S+)"),
        ("adjacent_dup", r"adjacent_dup=(\d+)"),
        ("projection_ops", r"projection_ops=(\d+)"),
    ):
        match = re.search(pattern, payload)
        if not match:
            continue
        value: Any = match.group(1)
        if name in {"revision", "start_ms", "end_ms", "sequence", "adjacent_dup", "projection_ops"}:
            value = int(value)
        event[name] = value

    text, committed_text, projected_text = _extract_text(
        payload, event_type == "transcript.committed"
    )
    event["text"] = text
    if committed_text is not None:
        event["committed_text"] = committed_text
    if projected_text is not None:
        event["projected_text"] = projected_text
    technical: dict[str, Any] = {
        "provider": event.get("provider"),
        "provider_segment_id": event.get("segment_id"),
        "revision": event.get("revision"),
        "sequence": event.get("sequence"),
        "source": event.get("source"),
        "operation": event.get("operation"),
    }
    if event.get("segment_overlap"):
        technical["segment_overlap"] = event["segment_overlap"]
    if event_type == "transcript.committed":
        technical["projection_summary"] = {
            "operation_count": event.get("projection_ops", 0),
            "adjacent_dup_removed": event.get("adjacent_dup", 0),
        }
        # The trace excerpt exposes event/segment bounds but does not expose
        # provider word spans.  Keep the available evidence without inventing
        # a drift measurement.
        technical["span_diagnostics"] = {
            "provider_event_span": _span(event),
            "astera_segment_span": _span(event),
            "word_span_available": False,
        }
    event["technical"] = technical
    return event


def _extract_text(payload: str, committed: bool) -> tuple[str, str | None, str | None]:
    if committed:
        match = re.search(r"committed_text=(.*?) \| projected_text=(.*)$", payload)
        if match:
            committed_text, projected_text = match.groups()
            return committed_text.strip(), committed_text.strip(), projected_text.strip()
        match = re.search(r"committed_text=(.*)$", payload)
        committed_text = match.group(1).strip() if match else ""
        return committed_text, committed_text, None
    marker = re.search(r"\bop=\S+\s+(.*)$", payload)
    return (marker.group(1).strip() if marker else ""), None, None


def _span(event: dict[str, Any]) -> dict[str, int] | None:
    start_ms, end_ms = event.get("start_ms"), event.get("end_ms")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        return None
    return {"start_ms": start_ms, "end_ms": end_ms}


def _parse_websocket(payload: str) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "websocket.closed"}
    code = re.search(r"\bcode=(\d+)", payload)
    clean = re.search(r"\bclean=(true|false)", payload)
    reason = re.search(r"\breason=(.*?)\s+clean=", payload)
    if code:
        event["code"] = int(code.group(1))
    if clean:
        event["clean"] = clean.group(1) == "true"
    if reason:
        event["reason"] = reason.group(1).strip()
    return event


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    events = parse_trace(args.input.read_text(encoding="utf-8"))
    args.output.write_text(
        json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"event_count": len(events), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
