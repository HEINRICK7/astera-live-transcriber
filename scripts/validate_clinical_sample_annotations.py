#!/usr/bin/env python3
"""Validate the manual annotation gate for the sampled clinical benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    args = parser.parse_args()

    samples = sorted(args.samples.glob("sample_*"))
    if args.sample_ids:
        selected = set(args.sample_ids)
        samples = [sample for sample in samples if sample.name in selected]
    rows = [_inspect_sample(sample) for sample in samples if sample.is_dir()]
    references_ready = sum(row["reference_ready"] for row in rows)
    annotations_validated = sum(row["annotations_validated"] for row in rows)
    projected_clean_ready = sum(row["projected_clean_ready"] for row in rows)
    ready = sum(row["ready"] for row in rows)
    report: dict[str, Any] = {
        "kind": "clinical_sample_annotation_gate",
        "sample_root": str(args.samples),
        "sample_count": len(rows),
        "references_ready": references_ready,
        "annotations_validated": annotations_validated,
        "projected_clean_ready": projected_clean_ready,
        "benchmark_ready": bool(rows) and ready == len(rows),
        "samples": rows,
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    summary = {
        key: report[key]
        for key in ("sample_count", "references_ready", "benchmark_ready")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _inspect_sample(sample: Path) -> dict[str, Any]:
    reference = sample / "reference.txt"
    annotations = sample / "clinical_annotations.json"
    reference_ready = reference.is_file() and bool(reference.read_text(encoding="utf-8").strip())
    annotations_file = annotations.is_file()
    annotations_validated = False
    annotation_status = None
    if annotations_file:
        payload = json.loads(annotations.read_text(encoding="utf-8"))
        annotation_status = payload.get("_status")
        annotations_validated = annotation_status == "validated"
    required = {
        "reference": reference_ready,
        "annotations": annotations_validated,
        "provider": (sample / "provider_committed.txt").is_file(),
        "structural": (sample / "projected_text.txt").is_file(),
        "clean": (sample / "projected_text_clean.txt").is_file(),
    }
    reasons = [name for name, present in required.items() if not present]
    return {
        "sample_id": sample.name,
        "reference_ready": reference_ready,
        "annotations_validated": annotations_validated,
        "projected_clean_ready": required["clean"],
        "ready": not reasons,
        "blocking_reasons": reasons,
        "annotation_status": annotation_status,
    }


if __name__ == "__main__":
    main()
