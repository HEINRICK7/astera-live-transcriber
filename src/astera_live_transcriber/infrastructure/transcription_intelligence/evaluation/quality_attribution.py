"""Offline attribution of transcript artifacts across the realtime stages."""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from astera_live_transcriber.application.services.committed_diagnostics import (
    adjacent_duplicate_runs,
)

_ENGLISH_MARKERS = ("you can use", "it's", "and the", "so", "see?")


def analyze_quality_attribution(events: list[dict[str, object]]) -> dict[str, object]:
    """Report observed artifacts without changing committed or projected text.

    The analyzer deliberately attributes only structural evidence available in
    the event stream. Semantic/medical errors require a reference transcript and
    are reported as review scope rather than guessed from string similarity.
    """
    committed = [event for event in events if event.get("type") == "transcript.committed"]
    revisions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for event in events:
        if event.get("type") in {"transcript.partial", "transcript.revised"}:
            segment_id = str(event.get("segment_id") or "")
            if segment_id:
                revisions[segment_id].append(event)

    issues: list[dict[str, object]] = []
    previous_projected = ""
    for index, event in enumerate(committed, start=1):
        segment_id = str(event.get("segment_id") or f"commit_{index:04d}")
        text = str(event.get("text") or "")
        projected = str(event.get("projected_text") or "")
        for duplicate in adjacent_duplicate_runs(text):
            phrase = duplicate["text"]
            cleaned = not _contains_phrase(projected, phrase)
            issues.append(
                _issue(
                    segment_id=segment_id,
                    issue="adjacent_duplication",
                    origin="provider",
                    provider_contains=True,
                    projected_contains=not cleaned,
                    projector_introduced=False,
                    clinical_risk="low",
                    safe_structural_candidate=True,
                    evidence={"duplicate": duplicate, "stage": "committed"},
                )
            )
        for duplicate in adjacent_duplicate_runs(projected):
            if _contains_duplicate(text, duplicate):
                continue
            if _contains_phrase(previous_projected, str(duplicate["text"])):
                continue
            issues.append(
                _issue(
                    segment_id=segment_id,
                    issue="adjacent_duplication",
                    origin="projector",
                    provider_contains=False,
                    projected_contains=True,
                    projector_introduced=True,
                    clinical_risk="low",
                    safe_structural_candidate=False,
                    evidence={"duplicate": duplicate, "stage": "projected"},
                )
            )
        if _contains_language_drift(text):
            issues.append(
                _issue(
                    segment_id=segment_id,
                    issue="language_drift",
                    origin="provider",
                    provider_contains=True,
                    projected_contains=_contains_language_drift(projected),
                    projector_introduced=False,
                    clinical_risk="low",
                    safe_structural_candidate=False,
                    evidence={"markers": _language_markers(text)},
                )
            )
        previous_projected = projected

    for previous, current in zip(committed, committed[1:]):
        replay = _replay_evidence(previous, current)
        if replay is None:
            continue
        projected = str(current.get("projected_text") or "")
        replay_text = str(replay["text"])
        issues.append(
            _issue(
                segment_id=str(current.get("segment_id") or "unknown"),
                issue="segment_replay",
                origin="provider",
                provider_contains=True,
                projected_contains=_contains_phrase(projected, replay_text),
                projector_introduced=False,
                clinical_risk="low",
                safe_structural_candidate=True,
                evidence=replay,
            )
        )

    issue_counts = Counter(str(item["issue"]) for item in issues)
    origin_counts = Counter(str(item["origin"]) for item in issues)
    return {
        "analysis": {
            "kind": "transcript_quality_attribution",
            "input_event_count": len(events),
            "committed_count": len(committed),
            "revision_count": sum(len(value) for value in revisions.values()),
            "text_mutated": False,
            "semantic_attribution": "not_inferred_without_reference",
        },
        "summary": {
            "issue_count": len(issues),
            "issue_counts": dict(issue_counts),
            "origin_counts": dict(origin_counts),
            "provider_artifacts": sum(item["origin"] == "provider" for item in issues),
            "structural_artifacts": sum(
                item["origin"] == "projector" for item in issues
            ),
            "safe_structural_candidates": sum(
                item["safe_structural_candidate"] for item in issues
            ),
            "projection_regressions": sum(
                item["projector_introduced_issue"] for item in issues
            ),
        },
        "issues": issues,
        "limitations": [
            "Medical-term, number, dose and negation errors require reference alignment.",
            "Provider/projector attribution is structural evidence, not a quality judgment.",
        ],
    }


def _issue(
    *,
    segment_id: str,
    issue: str,
    origin: str,
    provider_contains: bool,
    projected_contains: bool,
    projector_introduced: bool,
    clinical_risk: str,
    safe_structural_candidate: bool,
    evidence: dict[str, object],
) -> dict[str, object]:
    return {
        "segment_id": segment_id,
        "issue": issue,
        "classification": _classification(origin, issue),
        "origin": origin,
        "provider_committed_contains_issue": provider_contains,
        "projected_text_contains_issue": projected_contains,
        "projector_introduced_issue": projector_introduced,
        "clinical_risk": clinical_risk,
        "safe_structural_candidate": safe_structural_candidate,
        "correctable_by": "structural" if safe_structural_candidate else "none",
        "evidence": evidence,
    }


def _classification(origin: str, issue: str) -> str:
    if issue == "segment_replay":
        return f"{origin}_replay"
    return f"{origin}_{issue}"


def _contains_phrase(text: str, phrase: str) -> bool:
    return _normalize(phrase) in _normalize(text)


def _contains_duplicate(text: str, duplicate: dict[str, object]) -> bool:
    return _contains_phrase(text, str(duplicate["text"]))


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", text.casefold()))


def _contains_language_drift(text: str) -> bool:
    tokens = _normalize(text).split()
    return any(_contains_tokens(tokens, marker.split()) for marker in _ENGLISH_MARKERS)


def _language_markers(text: str) -> list[str]:
    tokens = _normalize(text).split()
    return [
        marker
        for marker in _ENGLISH_MARKERS
        if _contains_tokens(tokens, marker.split())
    ]


def _replay_evidence(
    previous: dict[str, object], current: dict[str, object]
) -> dict[str, object] | None:
    previous_tokens = _tokens(str(previous.get("text") or ""))
    current_tokens = _tokens(str(current.get("text") or ""))
    if not previous_tokens or not current_tokens:
        return None
    if previous_tokens == current_tokens:
        return {
            "mode": "exact_replay",
            "text": str(current.get("text") or ""),
            "previous_segment_id": previous.get("segment_id"),
        }
    if len(previous_tokens) >= 4 and _contains_tokens(current_tokens, previous_tokens):
        return {
            "mode": "contained_replay",
            "text": " ".join(previous_tokens),
            "previous_segment_id": previous.get("segment_id"),
        }
    overlap = _suffix_prefix_overlap(previous_tokens, current_tokens)
    if overlap >= 4:
        return {
            "mode": "boundary_overlap",
            "text": " ".join(current_tokens[:overlap]),
            "overlap_tokens": overlap,
            "previous_segment_id": previous.get("segment_id"),
        }
    return None


def _tokens(text: str) -> list[str]:
    return _normalize(text).split()


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )


def _suffix_prefix_overlap(left: list[str], right: list[str]) -> int:
    for count in range(min(len(left), len(right)), 0, -1):
        if left[-count:] == right[:count]:
            return count
    return 0


def iter_quality_issues(report: dict[str, object]) -> Iterable[dict[str, object]]:
    """Small typed-by-convention helper for downstream report consumers."""
    issues = report.get("issues", [])
    if isinstance(issues, list):
        yield from (item for item in issues if isinstance(item, dict))
