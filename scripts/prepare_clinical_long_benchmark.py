#!/usr/bin/env python3
"""Prepare auditable 30--60 second samples for clinical quality review.

The command never creates a ground-truth transcript.  Human reviewers must
listen to each generated clip and create its reference.txt independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    build_sample_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-duration-ms", type=int, default=45_000)
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and any(args.output.iterdir()) and not args.force:
        raise SystemExit(f"output is not empty; use --force to replace: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise SystemExit("events must be a JSON array")
    duration_ms = args.duration_ms or _duration_from_events(events)
    samples = build_sample_plan(
        events,
        duration_ms=duration_ms,
        sample_duration_ms=args.sample_duration_ms,
    )
    manifest: dict[str, Any] = {
        "kind": "clinical_long_session_quality_sampling",
        "status": "reference_pending_manual_validation",
        "source_events": str(args.events),
        "source_audio": str(args.audio),
        "source_audio_sha256": _sha256(args.audio),
        "duration_ms": duration_ms,
        "sample_duration_ms": args.sample_duration_ms,
        "reference_policy": (
            "Listen to every clip and write reference.txt literally from audio. "
            "Do not copy provider or projected output as ground truth."
        ),
        "hypothesis_warning": (
            "The source run stores projected_text as a cumulative transcript snapshot. "
            "It is preserved for audit, but must not be scored as a segment-local "
            "hypothesis until a local extraction is manually validated."
        ),
        "samples": [],
    }
    for sample in samples:
        sample_dir = args.output / sample.sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        if sample.anchor_segment_id:
            _extract_clip(
                args.audio,
                sample_dir / "audio.wav",
                sample.clip_start_ms,
                sample.clip_end_ms,
            )
        (sample_dir / "reference.pending.txt").write_text(
            "REFERENCE PENDING MANUAL VALIDATION\n"
            "Listen to audio.wav and transcribe literally.\n",
            encoding="utf-8",
        )
        (sample_dir / "clinical_annotations.json").write_text(
            json.dumps(
                {
                    "_status": "pending_manual_validation",
                    "_instruction": (
                        "Fill only from audio.wav. Keep spoken wording, values, "
                        "units, polarity and speaker context literal."
                    ),
                    "medications": [],
                    "numbers": [],
                    "doses": [],
                    "negations": [],
                    "clinical_terms": [],
                    "speaker_labels": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (sample_dir / "xai_raw_committed_snapshot.txt").write_text(
            sample.raw_text + "\n", encoding="utf-8"
        )
        (sample_dir / "provider_committed.txt").write_text(
            sample.raw_text + "\n", encoding="utf-8"
        )
        (sample_dir / "astera_structural_projection_snapshot.txt").write_text(
            sample.structural_snapshot + "\n", encoding="utf-8"
        )
        (sample_dir / "projected_text.txt").write_text(
            sample.structural_snapshot + "\n", encoding="utf-8"
        )
        (sample_dir / "astera_adaptive_shadow_snapshot.txt").write_text(
            sample.adaptive_snapshot + "\n", encoding="utf-8"
        )
        if sample.clean_snapshot is not None:
            (sample_dir / "astera_structural_clean_snapshot.txt").write_text(
                sample.clean_snapshot + "\n", encoding="utf-8"
            )
            (sample_dir / "projected_text_clean.txt").write_text(
                sample.clean_snapshot + "\n", encoding="utf-8"
            )
        else:
            (sample_dir / "projected_text_clean.pending.txt").write_text(
                "NOT CAPTURED IN SOURCE RUN\n"
                "Do not use this file as a benchmark hypothesis.\n",
                encoding="utf-8",
            )
        (sample_dir / "metadata.json").write_text(
            json.dumps(sample_payload_for_metadata(sample), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        sample_payload = sample.as_dict()
        (sample_dir / "sample.json").write_text(
            json.dumps(sample_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["samples"].append(sample_payload)
    manifest["summary"] = _summary(samples)
    (args.output / "sampling-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))


def _duration_from_events(events: list[dict[str, Any]]) -> int:
    values = [
        int(event[key])
        for event in events
        for key in ("end_ms", "timestamp_ms")
        if isinstance(event.get(key), (int, float))
    ]
    if not values:
        raise SystemExit("could not infer duration; pass --duration-ms")
    return max(values)


def _extract_clip(audio: Path, output: Path, start_ms: int, end_ms: int) -> None:
    duration = max(1, end_ms - start_ms) / 1000
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-i",
        str(audio),
        "-t",
        f"{duration:.3f}",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output),
    ]
    subprocess.run(command, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(samples: list[Any]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "time_bucket_samples": sum(sample.reason == "time_bucket" for sample in samples),
        "clinical_target_samples": sum(sample.reason == "clinical_target" for sample in samples),
        "target_not_found_samples": sum(
            sample.reason == "clinical_target_not_found_in_captured_commits" for sample in samples
        ),
        "matched_target_terms": sorted(
            {term for sample in samples for term in sample.matched_terms}
        ),
        "missing_target_terms": sorted(
            {term for sample in samples for term in sample.missing_terms}
        ),
        "references_ready": 0,
        "benchmark_ready": False,
    }


def sample_payload_for_metadata(sample: Any) -> dict[str, Any]:
    """Keep per-sample metadata explicit about what is and is not scoreable."""
    payload = sample.as_dict()
    payload["reference_file"] = "reference.txt (not created yet)"
    payload["annotation_file"] = "clinical_annotations.json"
    payload["provider_file"] = "provider_committed.txt"
    payload["structural_file"] = "projected_text.txt"
    payload["clean_file"] = (
        "projected_text_clean.txt"
        if sample.clean_snapshot is not None
        else "projected_text_clean.pending.txt (not scoreable)"
    )
    return payload


if __name__ == "__main__":
    main()
