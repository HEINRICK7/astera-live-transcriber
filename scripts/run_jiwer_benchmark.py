#!/usr/bin/env python3
"""Compare named transcription variants against one manual reference."""

import argparse
import json
from pathlib import Path

from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    ClinicalCheckEvaluator,
    ClinicalFidelityEvaluator,
    JiwerBenchmark,
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run WER/CER/MER/WIL against a manually reviewed reference."
    )
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--structural", required=True, type=Path)
    parser.add_argument("--adaptive", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--clinical-annotations",
        type=Path,
        help="JSON with manually reviewed dose/number/negation/medication/speaker spans",
    )
    args = parser.parse_args()

    reference = read_text(args.reference)
    report = JiwerBenchmark().evaluate(
        reference,
        {
            "xai_raw_committed": read_text(args.raw),
            "astera_structural_projection": read_text(args.structural),
            "astera_structural_adaptive": read_text(args.adaptive),
        },
    )
    hypotheses = {
        "xai_raw_committed": read_text(args.raw),
        "astera_structural_projection": read_text(args.structural),
        "astera_structural_adaptive": read_text(args.adaptive),
    }
    payload = report.as_dict()
    payload["raw_jiwer"] = {
        item["label"]: {
            metric: item[metric] for metric in ("wer", "cer", "mer", "wil")
        }
        for item in payload["items"]
    }
    payload["normalized_jiwer"] = {
        item["label"]: {
            metric: item["normalized_jiwer"][metric]
            for metric in ("wer", "cer", "mer", "wil")
        }
        for item in payload["items"]
    }
    if args.clinical_annotations:
        annotations = json.loads(args.clinical_annotations.read_text(encoding="utf-8"))
        payload["clinical_checks"] = {
            label: item.as_dict()
            for label, item in ClinicalCheckEvaluator().evaluate(hypotheses, annotations).items()
        }
        payload["clinical_fidelity"] = {
            label: item.as_dict()
            for label, item in ClinicalFidelityEvaluator().evaluate(
                hypotheses, annotations
            ).items()
        }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(_markdown(payload))


def _markdown(payload: dict[str, object]) -> str:
    lines = [
        "ASTERA STT BENCHMARK",
        "",
        "| Variante | WER | CER | MER | WIL |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in payload["items"]:
        lines.append(
            f"| {item['label']} | {_percent(item['wer'])} | {_percent(item['cer'])} | "
            f"{_percent(item['mer'])} | {_percent(item['wil'])} |"
        )
    lines.extend(
        [
            "",
            "NORMALIZED JIWER (line breaks collapsed)",
            "",
            "| Variante | WER | CER | MER | WIL |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in payload["items"]:
        scores = item["normalized_jiwer"]
        lines.append(
            f"| {item['label']} | {_percent(scores['wer'])} | {_percent(scores['cer'])} | "
            f"{_percent(scores['mer'])} | {_percent(scores['wil'])} |"
        )
    lines.extend(
        [
            "",
            "ALIGNMENT",
            "",
            "| Variante | Hits | Substitutions | Deletions | Insertions | Categories |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload["items"]:
        alignment = item["alignment"]
        categories = ", ".join(
            f"{key}={value}" for key, value in alignment["categories"].items()
        ) or "none"
        lines.append(
            f"| {item['label']} | {alignment['hits']} | {alignment['substitutions']} | "
            f"{alignment['deletions']} | {alignment['insertions']} | {categories} |"
        )
    return "\n".join(lines)


def _percent(value: object) -> str:
    return f"{float(value) * 100:.2f}%"


if __name__ == "__main__":
    main()
