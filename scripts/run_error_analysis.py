#!/usr/bin/env python3
"""Attribute structural benchmark errors without changing any transcript."""

import argparse
import json
from pathlib import Path

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    ClinicalFidelityEvaluator,
    analyze_error_attribution,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--hypothesis", required=True, type=Path)
    parser.add_argument("--clinical-annotations", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    annotation_count = 0
    annotations = None
    if args.clinical_annotations:
        annotations = json.loads(args.clinical_annotations.read_text(encoding="utf-8"))
        annotation_count = sum(
            len(value) for key, value in annotations.items() if not key.startswith("_")
        )
    report = analyze_error_attribution(
        args.reference.read_text(encoding="utf-8"),
        args.hypothesis.read_text(encoding="utf-8"),
        critical_annotation_count=annotation_count,
    )
    payload = report.as_dict()
    if annotations is not None:
        fidelity = ClinicalFidelityEvaluator().evaluate_hypothesis(
            args.hypothesis.read_text(encoding="utf-8"), annotations
        )
        payload["ccer_experimental"] = fidelity.ccer_experimental
        payload["clinical_fidelity"] = fidelity.as_dict()
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
