#!/usr/bin/env python3
"""Capture one file-stream run for offline structural/JiWER inspection."""

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets


async def capture(audio: Path, server: str, output_dir: Path) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "events.jsonl"
    if jsonl_path.exists() and jsonl_path.stat().st_size:
        raise SystemExit(
            f"{jsonl_path} já contém dados; use um novo diretório versionado "
            "para não sobrescrever o run anterior"
        )
    writer = _EventLogWriter(jsonl_path)
    async with httpx.AsyncClient(base_url=server, timeout=60) as client:
        with audio.open("rb") as source:
            response = await client.post(
                "/v1/realtime/files",
                files={"file": (audio.name, source, "audio/mpeg")},
                data={"language": "pt-BR", "mode": "realtime"},
            )
        response.raise_for_status()
        session = response.json()

    websocket_url = server.replace("http://", "ws://").replace("https://", "wss://")
    websocket_url += session["websocket"]
    events: list[dict[str, object]] = []
    session_completed_seen = False
    failure: dict[str, object] | None = None
    try:
        async with websockets.connect(websocket_url, max_size=None) as socket:
            try:
                async for message in socket:
                    event = json.loads(message)
                    captured = writer.append(event)
                    events.append(captured)
                    if event.get("type") == "session.completed":
                        session_completed_seen = True
                        break
            except websockets.ConnectionClosedOK:
                pass
    except Exception as exc:
        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        if session_completed_seen and not any(
            event.get("type") == "websocket.closed" for event in events
        ):
            events.append(
                writer.append(
                    {
                        "type": "websocket.closed",
                        "code": 1000,
                        "reason": "session completed",
                        "clean": True,
                        "technical": {"source": "capture_client_close_observation"},
                    }
                )
            )
        writer.close()
        _finalize_capture(events, audio, output_dir, failure=failure)
    return events


class _EventLogWriter:
    """Append each received event durably before allowing the stream to continue."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")
        self._capture_seq = 0

    def append(self, event: dict[str, object]) -> dict[str, object]:
        self._capture_seq += 1
        captured = {
            **event,
            "capture_seq": self._capture_seq,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        self._file.write(json.dumps(captured, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        return captured

    def close(self) -> None:
        self._file.close()


def _finalize_capture(
    events: list[dict[str, object]],
    audio: Path,
    output_dir: Path,
    failure: dict[str, object] | None = None,
) -> None:
    (output_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_hypotheses(events, output_dir)
    structural = (output_dir / "astera_structural_projection.txt").read_text(encoding="utf-8")
    (output_dir / "projected-transcript.txt").write_text(structural, encoding="utf-8")
    (output_dir / "technical-summary.json").write_text(
        json.dumps(_technical_summary(events), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "kind": "realtime_capture",
                "source_audio": str(audio),
                "output_dir": str(output_dir),
                "events_jsonl": "events.jsonl",
                "events_json": "events.json",
                "persistence": "append_flush_fsync_per_event",
                "summary": "technical-summary.json",
                "status": "failed" if failure else "completed",
                "failure": failure,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if failure is not None:
        (output_dir / "capture-failure.json").write_text(
            json.dumps(
                {
                    "status": "not_a_benchmark_run",
                    "reason": "capture_failed_after_persistent_event_logging",
                    "failure": failure,
                    "events_persisted": len(events),
                    "events_jsonl": "events.jsonl",
                    "events_json": "events.json",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _technical_summary(events: list[dict[str, object]]) -> dict[str, object]:
    types = Counter(str(event.get("type", "unknown")) for event in events)
    transcript_events = [
        event
        for event in events
        if str(event.get("type", "")).startswith("transcript.")
    ]
    segment_ids = [
        str(event.get("segment_id"))
        for event in transcript_events
        if event.get("segment_id")
    ]
    technical_sequences = sorted(
        int((event.get("technical") or {}).get("sequence"))
        for event in events
        if isinstance((event.get("technical") or {}).get("sequence"), int)
    )
    capture_sequences = [
        int(event["capture_seq"])
        for event in events
        if isinstance(event.get("capture_seq"), int)
    ]
    close = next(
        (event for event in reversed(events) if event.get("type") == "websocket.closed"),
        None,
    )
    completed = next(
        (event for event in reversed(events) if event.get("type") == "session.completed"),
        None,
    )
    completed_technical = completed.get("technical") if completed else None
    return {
        "session_id": next(
            (event.get("session_id") for event in events if event.get("session_id")),
            None,
        ),
        "provider": next(
            (event.get("provider") for event in events if event.get("provider")),
            None,
        ),
        "started_at": events[0].get("captured_at") if events else None,
        "completed_at": events[-1].get("captured_at") if events else None,
        "event_count": len(events),
        "event_counts": dict(types),
        "commit_count": types["transcript.committed"],
        "revision_count": types["transcript.revised"],
        "partial_count": types["transcript.partial"],
        "last_segment_id": segment_ids[-1] if segment_ids else None,
        "last_sequence": max(technical_sequences) if technical_sequences else None,
        "technical_sequence": {
            "first": min(technical_sequences) if technical_sequences else None,
            "last": max(technical_sequences) if technical_sequences else None,
            "missing": _missing(technical_sequences),
        },
        "capture_sequence": {
            "first": min(capture_sequences) if capture_sequences else None,
            "last": max(capture_sequences) if capture_sequences else None,
            "missing": _missing(capture_sequences),
        },
        "websocket_close_code": close.get("code") if close else None,
        "websocket_clean": close.get("clean") if close else None,
        "runtime_metrics": (
            completed_technical.get("runtime_metrics")
            if isinstance(completed_technical, dict)
            else None
        ),
        "audio_duration_ms": _max_time(events),
    }


def _missing(values: list[int]) -> list[int]:
    if not values:
        return []
    present = set(values)
    return [value for value in range(values[0], values[-1] + 1) if value not in present]


def _max_time(events: list[dict[str, object]]) -> int | None:
    values = [
        int(event[key])
        for event in events
        for key in ("timestamp_ms", "end_ms", "start_ms")
        if isinstance(event.get(key), int)
    ]
    return max(values) if values else None


def _write_hypotheses(events: list[dict[str, object]], output_dir: Path) -> None:
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    raw_chunks = []
    structural = ""
    clean = ""
    for event in committed:
        # Compare the provider's authoritative committed snapshot at the same
        # granularity as the projected output. A diagnostic raw_xai payload can
        # be a partial/revision trace and is therefore not a fair raw baseline.
        raw_committed_text = event.get("text")
        raw_chunks.append(str(raw_committed_text or "").strip())
        structural = str(event.get("projected_text") or structural).strip()
        clean = str(event.get("projected_text_clean") or clean).strip()
    raw_text = "\n".join(chunk for chunk in raw_chunks if chunk) + "\n"
    (output_dir / "xai_raw_committed.txt").write_text(
        raw_text,
        encoding="utf-8",
    )
    (output_dir / "provider_committed.txt").write_text(raw_text, encoding="utf-8")
    (output_dir / "astera_structural_projection.txt").write_text(
        structural + "\n",
        encoding="utf-8",
    )
    (output_dir / "projected_text.txt").write_text(structural + "\n", encoding="utf-8")
    if clean:
        (output_dir / "astera_structural_clean.txt").write_text(
            clean + "\n", encoding="utf-8"
        )
        (output_dir / "projected_text_clean.txt").write_text(
            clean + "\n", encoding="utf-8"
        )
    # Normalization is disabled by default. Keep this explicit baseline until
    # an adaptive representation is exported by an approved experiment.
    (output_dir / "astera_structural_adaptive.txt").write_text(
        structural + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8001")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(capture(args.audio, args.server, args.output_dir))


if __name__ == "__main__":
    main()
