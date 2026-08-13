import re
from collections.abc import Sequence

from .provider_segment_store import ProviderSegmentState, ProviderSegmentStore


def committed_projection_diagnostics(
    segments: Sequence[ProviderSegmentState],
    current_segment_id: str,
) -> dict[str, object]:
    ordered = list(segments)
    current_index = next(
        (
            index
            for index, segment in enumerate(ordered)
            if segment.segment_id == current_segment_id
        ),
        None,
    )
    current = ordered[current_index] if current_index is not None else None
    previous = (
        ordered[current_index - 1]
        if current_index is not None and current_index > 0
        else None
    )
    following = (
        ordered[current_index + 1]
        if current_index is not None and current_index + 1 < len(ordered)
        else None
    )

    return {
        "current_segment_id": current_segment_id,
        "committed_count": len(ordered),
        "ordered_segments": [ProviderSegmentStore.snapshot(segment) for segment in ordered],
        "current": ProviderSegmentStore.snapshot(current),
        "previous": ProviderSegmentStore.snapshot(previous),
        "next": ProviderSegmentStore.snapshot(following),
        "current_adjacent_duplicates": adjacent_duplicate_runs(
            current.committed_text if current else ""
        ),
        "overlap_with_previous": segment_overlap(previous, current),
        "overlap_with_next": segment_overlap(current, following),
    }


def adjacent_duplicate_runs(text: str) -> list[dict[str, object]]:
    tokens = text.split()
    normalized = [_normalize(token) for token in tokens]
    duplicates: list[dict[str, object]] = []
    index = 0
    while index < len(normalized) - 1:
        match_length = 0
        for block_length in range(min(4, (len(normalized) - index) // 2), 0, -1):
            left = normalized[index : index + block_length]
            right = normalized[index + block_length : index + block_length * 2]
            if left and left == right and all(left):
                match_length = block_length
                break
        if match_length:
            duplicates.append(
                {
                    "token_start": index,
                    "token_end": index + match_length * 2,
                    "block": tokens[index : index + match_length],
                    "repeated_block": tokens[index + match_length : index + match_length * 2],
                    "text": " ".join(tokens[index : index + match_length * 2]),
                }
            )
            index += match_length * 2
        else:
            index += 1
    return duplicates


def segment_overlap(
    left: ProviderSegmentState | None,
    right: ProviderSegmentState | None,
) -> dict[str, object] | None:
    if left is None or right is None:
        return None
    left_text = left.committed_text or ""
    right_text = right.committed_text or ""
    left_tokens = _tokens(left_text)
    right_tokens = _tokens(right_text)
    temporal_overlap = None
    if left.end_ms is not None and right.end_ms is not None:
        temporal_overlap = max(
            0,
            min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms),
        )
    suffix_prefix = _suffix_prefix_overlap(left_tokens, right_tokens)
    contains = _contains_tokens(right_tokens, left_tokens)
    return {
        "left_segment_id": left.segment_id,
        "right_segment_id": right.segment_id,
        "temporal_overlap_ms": temporal_overlap,
        "left_start_ms": left.start_ms,
        "left_end_ms": left.end_ms,
        "right_start_ms": right.start_ms,
        "right_end_ms": right.end_ms,
        "textual_suffix_prefix_overlap_tokens": suffix_prefix,
        "right_contains_left": contains,
        "left_text_suffix": left_tokens[-max(suffix_prefix, 8) :] if left_tokens else [],
        "right_text_prefix": right_tokens[:max(suffix_prefix, 8)],
        "classification": (
            "temporal_and_textual_overlap"
            if temporal_overlap and (suffix_prefix or contains)
            else "textual_overlap"
            if suffix_prefix or contains
            else "temporal_overlap"
            if temporal_overlap
            else "none"
        ),
    }


def _suffix_prefix_overlap(left: list[str], right: list[str]) -> int:
    maximum = min(len(left), len(right))
    for count in range(maximum, 0, -1):
        if left[-count:] == right[:count]:
            return count
    return 0


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )


def _tokens(text: str) -> list[str]:
    return [_normalize(token) for token in text.split() if _normalize(token)]


def _normalize(token: str) -> str:
    return re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())
