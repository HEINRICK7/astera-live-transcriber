from collections.abc import Iterable


def text_comparison_diagnostics(
    pairs: Iterable[tuple[str, str, str | None, str | None]],
) -> dict[str, object]:
    """Return explainable text metrics for debug traces only.

    Each pair is ``(name, reference, hypothesis, reference_label)``.  The
    metrics never participate in reconciliation or text correction.  Labels
    make it explicit that these are structural comparisons, not ground-truth
    transcription scores.
    """
    rapidfuzz_pairs: list[dict[str, object]] = []
    jiwer_pairs: list[dict[str, object]] = []
    for name, reference, hypothesis, reference_label in pairs:
        if hypothesis is None:
            continue
        reference_label = reference_label or "reference"
        base = {
            "name": name,
            "reference_label": reference_label,
            "hypothesis_label": "hypothesis",
            "reference_chars": len(reference),
            "hypothesis_chars": len(hypothesis),
            "reference_tokens": len(reference.split()),
            "hypothesis_tokens": len(hypothesis.split()),
            "exact_match": reference == hypothesis,
        }
        rapidfuzz_pairs.append({**base, **_rapidfuzz_scores(reference, hypothesis)})
        jiwer_pairs.append({**base, **_jiwer_scores(reference, hypothesis)})
    return {
        "purpose": "debug_diagnostics_only",
        "ground_truth_available": False,
        "rapidfuzz": {
            "available": all("error" not in item for item in rapidfuzz_pairs),
            "score_scale": "0_to_100",
            "pairs": rapidfuzz_pairs,
        },
        "jiwer": {
            "available": all("error" not in item for item in jiwer_pairs),
            "score_scale": "0_to_1_lower_is_better",
            "pairs": jiwer_pairs,
        },
    }


def _rapidfuzz_scores(reference: str, hypothesis: str) -> dict[str, object]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return {"error": "rapidfuzz_not_installed"}
    return {
        "ratio": round(float(fuzz.ratio(reference, hypothesis)), 3),
        "partial_ratio": round(float(fuzz.partial_ratio(reference, hypothesis)), 3),
        "token_sort_ratio": round(float(fuzz.token_sort_ratio(reference, hypothesis)), 3),
        "token_set_ratio": round(float(fuzz.token_set_ratio(reference, hypothesis)), 3),
        "WRatio": round(float(fuzz.WRatio(reference, hypothesis)), 3),
    }


def _jiwer_scores(reference: str, hypothesis: str) -> dict[str, object]:
    if not reference.strip():
        score = 0.0 if not hypothesis.strip() else 1.0
        return {"wer": score, "cer": score, "mer": score, "wil": score}
    try:
        from jiwer import cer, mer, wer, wil
    except ImportError:
        return {"error": "jiwer_not_installed"}
    try:
        return {
            "wer": round(float(wer(reference, hypothesis)), 6),
            "cer": round(float(cer(reference, hypothesis)), 6),
            "mer": round(float(mer(reference, hypothesis)), 6),
            "wil": round(float(wil(reference, hypothesis)), 6),
        }
    except Exception as exc:  # pragma: no cover - defensive debug instrumentation
        return {"error": f"jiwer_error:{type(exc).__name__}"}
