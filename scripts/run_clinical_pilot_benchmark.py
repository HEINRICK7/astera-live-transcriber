#!/usr/bin/env python3
"""Run the three-sample clinical pilot against local, segment-local files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    ClinicalFidelityEvaluator,
    JiwerBenchmark,
)

DEFAULT_SAMPLE_IDS = ("sample_002", "sample_015", "sample_019")
VARIANTS = {
    "xai_committed": "provider_committed.txt",
    "projected": "projected_text.txt",
    "projected_clean": "projected_text_clean.txt",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    args = parser.parse_args()

    sample_ids = tuple(args.sample_ids or DEFAULT_SAMPLE_IDS)
    benchmark = JiwerBenchmark()
    fidelity = ClinicalFidelityEvaluator()
    per_sample: list[dict[str, Any]] = []
    aggregate_inputs: dict[str, list[str]] = {variant: [] for variant in VARIANTS}
    aggregate_references: list[str] = []

    for sample_id in sample_ids:
        sample_dir = args.samples / sample_id
        reference = _read_required(sample_dir / "reference.txt")
        hypotheses = {
            variant: _read_required(sample_dir / filename)
            for variant, filename in VARIANTS.items()
        }
        annotations = json.loads(
            (sample_dir / "clinical_annotations.json").read_text(encoding="utf-8")
        )
        report = benchmark.evaluate(reference, hypotheses)
        clinical = fidelity.evaluate(hypotheses, annotations)
        items = []
        for item in report.items:
            clinical_report = clinical[item.label].as_dict()
            items.append(
                {
                    "variant": item.label,
                    "wer": item.scores.wer,
                    "cer": item.scores.cer,
                    "mer": item.scores.mer,
                    "wil": item.scores.wil,
                    "normalized_jiwer": {
                        "wer": item.normalized_scores.wer,
                        "cer": item.normalized_scores.cer,
                        "mer": item.normalized_scores.mer,
                        "wil": item.normalized_scores.wil,
                    },
                    "alignment": item.alignment.as_dict(),
                    "clinical_fidelity": clinical_report,
                    "ccer_experimental": clinical_report["ccer_experimental"],
                }
            )
            aggregate_inputs[item.label].append(hypotheses[item.label])
        aggregate_references.append(reference)
        per_sample.append({"sample_id": sample_id, "items": items})

    aggregate_hypotheses = {
        variant: "\n".join(aggregate_inputs[variant]) for variant in VARIANTS
    }
    aggregate_reference = "\n".join(aggregate_references)
    aggregate_report = benchmark.evaluate(aggregate_reference, aggregate_hypotheses)
    aggregate_clinical = fidelity.evaluate(
        aggregate_hypotheses,
        _merge_annotations(args.samples, sample_ids),
    )
    aggregate_items = []
    for item in aggregate_report.items:
        clinical_report = aggregate_clinical[item.label].as_dict()
        aggregate_items.append(
            {
                "variant": item.label,
                "wer": item.scores.wer,
                "cer": item.scores.cer,
                "mer": item.scores.mer,
                "wil": item.scores.wil,
                "normalized_jiwer": {
                    "wer": item.normalized_scores.wer,
                    "cer": item.normalized_scores.cer,
                    "mer": item.normalized_scores.mer,
                    "wil": item.normalized_scores.wil,
                },
                "alignment": item.alignment.as_dict(),
                "clinical_fidelity": clinical_report,
                "ccer_experimental": clinical_report["ccer_experimental"],
            }
        )

    sample_019 = next(item for item in per_sample if item["sample_id"] == "sample_019")
    payload = {
        "kind": "clinical_long_session_quality_pilot",
        "samples": per_sample,
        "aggregate": {"items": aggregate_items},
        "sample_019_term_detail": _term_detail(sample_019),
        "stage_comparison": _stage_comparison(per_sample),
        "ground_truth_policy": "human-provided reference.txt; no provider output used as truth",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(_markdown(payload))


def _read_required(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"missing benchmark input: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"empty benchmark input: {path}")
    return text


def _merge_annotations(
    samples: Path,
    sample_ids: tuple[str, ...],
) -> dict[str, list[dict[str, object]]]:
    merged: dict[str, list[dict[str, object]]] = {}
    for sample_id in sample_ids:
        annotations = json.loads(
            (samples / sample_id / "clinical_annotations.json").read_text(encoding="utf-8")
        )
        for category, values in annotations.items():
            if category.startswith("_") or not isinstance(values, list):
                continue
            merged.setdefault(category, []).extend(values)
    return merged


def _term_detail(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": sample["sample_id"],
        "terms": [
            {
                "term": clinical_item["expected"],
                "variant": variant_item["variant"],
                "passed": clinical_item["passed"],
                "checks": clinical_item["checks"],
            }
            for variant_item in sample["items"]
            for clinical_item in variant_item["clinical_fidelity"]["items"]
            if clinical_item["expected"]
            in {"terlipressina", "somatostatina", "octreotide"}
        ],
    }


def _stage_comparison(per_sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = {item["sample_id"]: item["items"] for item in per_sample}
    rows = []
    for sample_id, items in baseline.items():
        raw = next(item for item in items if item["variant"] == "xai_committed")
        projected = next(item for item in items if item["variant"] == "projected")
        clean = next(item for item in items if item["variant"] == "projected_clean")
        rows.append(
            {
                "sample_id": sample_id,
                "projected_minus_xai": _delta(projected, raw),
                "clean_minus_projected": _delta(clean, projected),
            }
        )
    return rows


def _delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    return {
        metric: left[metric] - right[metric]
        for metric in ("wer", "cer", "mer", "wil")
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "CLINICAL PILOT BENCHMARK",
        "",
        "| Sample | Variant | WER | CER | MER | WIL |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for sample in payload["samples"]:
        for item in sample["items"]:
            lines.append(
                f"| {sample['sample_id']} | {item['variant']} | {_pct(item['wer'])} | "
                f"{_pct(item['cer'])} | {_pct(item['mer'])} | {_pct(item['wil'])} |"
            )
    lines.extend(
        [
            "",
            "AGGREGATE",
            "",
            "| Variant | WER | CER | MER | WIL |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in payload["aggregate"]["items"]:
        lines.append(
            f"| {item['variant']} | {_pct(item['wer'])} | {_pct(item['cer'])} | "
            f"{_pct(item['mer'])} | {_pct(item['wil'])} |"
        )
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


if __name__ == "__main__":
    main()
