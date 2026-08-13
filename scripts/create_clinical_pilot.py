#!/usr/bin/env python3
"""Create a fixed, auditable three-sample annotation pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_SAMPLE_IDS = ("sample_002", "sample_015", "sample_019")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    args = parser.parse_args()

    requested = tuple(args.sample_ids or DEFAULT_SAMPLE_IDS)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    by_id = {item["sample_id"]: item for item in manifest["samples"]}
    missing = sorted(set(requested) - set(by_id))
    if missing:
        raise SystemExit(f"sample ids not found in manifest: {', '.join(missing)}")

    args.output.mkdir(parents=True, exist_ok=True)
    pilot = {
        "kind": "clinical_long_session_quality_pilot",
        "status": "reference_pending_manual_validation",
        "selection_policy": {
            "sample_count": len(requested),
            "stop_after_pilot": True,
            "source_of_truth": "audio.wav only",
        },
        "samples": [
            {
                "sample_id": sample_id,
                "role": _role(by_id[sample_id]),
                "source_dir": f"../{sample_id}",
                "reference": "reference.txt (not created)",
                "annotations": "clinical_annotations.json",
                "expected_manual_action": (
                    "Listen to audio.wav, write literal reference.txt, fill "
                    "clinical_annotations.json, set _status=validated."
                ),
            }
            for sample_id in requested
        ],
        "benchmark_command_after_validation": (
            "Run the sample benchmark only after the pilot validator reports "
            "references_ready=3 and benchmark_ready=true."
        ),
    }
    (args.output / "pilot-manifest.json").write_text(
        json.dumps(pilot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "README.md").write_text(_readme(requested), encoding="utf-8")
    print(json.dumps({"sample_count": len(requested), "benchmark_ready": False}, indent=2))


def _role(sample: dict[str, object]) -> str:
    categories = set(sample.get("categories") or [])
    if "vasoativos" in categories:
        return "difficult_medical_terminology"
    if "medicamento" in categories:
        return "medication_number_negation_candidate"
    return "ordinary_clinical_conversation"


def _readme(sample_ids: tuple[str, ...]) -> str:
    lines = [
        "# Clinical quality pilot",
        "",
        "This pilot intentionally stops after three samples:",
        "",
    ]
    lines.extend(f"- `{sample_id}`" for sample_id in sample_ids)
    lines.extend(
        [
            "",
            "For each referenced sample directory, listen to `audio.wav` and",
            "create `reference.txt` literally from the audio. Fill",
            "`clinical_annotations.json` from the audio only and change `_status`",
            "to `validated` after review.",
            "",
            "Do not use provider or projected text as ground truth. Do not annotate",
            "the remaining 17 samples until this pilot benchmark is reviewed.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
