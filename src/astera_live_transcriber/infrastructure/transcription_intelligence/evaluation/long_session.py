import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Any


@dataclass(slots=True)
class _Segment:
    segment_id: str
    first_sequence: int | None = None
    last_sequence: int | None = None
    event_count: int = 0
    partial_count: int = 0
    revised_count: int = 0
    committed_count: int = 0
    max_revision: int = 0
    start_ms: int | None = None
    end_ms: int | None = None
    committed_text: str = ""
    projection_ops: int = 0
    adjacent_dup_removed: int = 0
    temporal_overlap_removed: int = 0
    textual_overlap_removed: int = 0
    adjacent_duplicates: list[dict[str, object]] = field(default_factory=list)
    drift_samples: list[dict[str, float]] = field(default_factory=list)


def analyze_long_session(
    events: list[dict[str, Any]],
    *,
    source_label: str | None = None,
    pause_threshold_ms: int = 60_000,
    timeline_bucket_ms: int = 300_000,
) -> dict[str, object]:
    """Analyze a complete or explicitly partial events.json offline.

    This function only reads event evidence. It never changes transcript text.
    """
    segments: dict[str, _Segment] = {}
    commits: list[dict[str, Any]] = []
    sequence_values: list[int] = []
    timestamps: list[int] = []
    drift_samples: list[dict[str, object]] = []
    speech_starts: list[dict[str, object]] = []
    event_types = Counter(str(event.get("type", "unknown")) for event in events)

    for event in events:
        sequence = _int((event.get("technical") or {}).get("sequence"))
        if sequence is not None:
            sequence_values.append(sequence)
        for key in ("timestamp_ms", "start_ms", "end_ms"):
            value = _int(event.get(key))
            if value is not None:
                timestamps.append(value)
        if event.get("type") == "speech.started":
            speech_starts.append(
                {
                    "timestamp_ms": _int(event.get("timestamp_ms")),
                    "segment_id": event.get("technical", {}).get("astera_turn_id"),
                }
            )
        _collect_drift(event, drift_samples)

        segment_id = event.get("segment_id")
        if not isinstance(segment_id, str):
            continue
        segment = segments.setdefault(segment_id, _Segment(segment_id))
        segment.event_count += 1
        segment.first_sequence = _min_optional(segment.first_sequence, sequence)
        segment.last_sequence = _max_optional(segment.last_sequence, sequence)
        segment.start_ms = _min_optional(segment.start_ms, _int(event.get("start_ms")))
        segment.end_ms = _max_optional(segment.end_ms, _int(event.get("end_ms")))
        revision = _int(event.get("revision")) or 0
        segment.max_revision = max(segment.max_revision, revision)
        event_type = event.get("type")
        if event_type == "transcript.partial":
            segment.partial_count += 1
        elif event_type == "transcript.revised":
            segment.revised_count += 1
        elif event_type == "transcript.committed":
            segment.committed_count += 1
            segment.committed_text = str(event.get("text") or "")
            summary = (event.get("technical") or {}).get("projection_summary") or {}
            segment.projection_ops += _int(summary.get("operation_count")) or _int(
                event.get("projection_ops")
            ) or 0
            segment.adjacent_dup_removed += _int(summary.get("adjacent_dup_removed")) or _int(
                event.get("adjacent_dup")
            ) or 0
            segment.temporal_overlap_removed += _int(summary.get("temporal_overlap_removed")) or 0
            segment.textual_overlap_removed += _int(summary.get("textual_overlap_removed")) or 0
            segment.adjacent_duplicates.extend(_adjacent_duplicate_runs(segment.committed_text))
            commits.append(event)

    duration_ms = _session_duration(events, timestamps)
    ordered_segments = sorted(
        segments.values(),
        key=lambda item: (
            item.start_ms if item.start_ms is not None else 10**18,
            item.segment_id,
        ),
    )
    temporal_overlaps = _temporal_overlaps(ordered_segments)
    text_replays = _text_replays(ordered_segments)
    pauses = _long_pauses(speech_starts, ordered_segments, pause_threshold_ms)
    timeline = _timeline(
        events,
        ordered_segments,
        commits,
        duration_ms,
        timeline_bucket_ms,
    )
    projection_ops_by_minute = _projection_ops_by_minute(commits)
    zero_duration = [
        segment.segment_id
        for segment in ordered_segments
        if segment.start_ms is not None
        and segment.end_ms is not None
        and segment.end_ms <= segment.start_ms
    ]
    residual_adjacent = [
        {"segment_id": segment.segment_id, "runs": segment.adjacent_duplicates}
        for segment in ordered_segments
        if segment.adjacent_duplicates
    ]

    complete = _is_complete_event_stream(events, sequence_values)
    return {
        "analysis": {
            "kind": "long_session_structural_analysis",
            "source_label": source_label,
            "input_event_count": len(events),
            "input_completeness": "complete_events_json" if complete else "partial_or_excerpt",
            "transcript_text_unchanged": True,
        },
        "session": {
            "duration_ms": duration_ms,
            "duration_seconds": duration_ms / 1000 if duration_ms is not None else None,
            "segment_count": len(segments),
            "revision_count": event_types["transcript.revised"],
            "commit_count": event_types["transcript.committed"],
            "partial_count": event_types["transcript.partial"],
            "speech_started_count": event_types["speech.started"],
            "sequence_min": min(sequence_values) if sequence_values else None,
            "sequence_max": max(sequence_values) if sequence_values else None,
            "lifecycle": {
                "audio_completed": event_types["audio.completed"] > 0,
                "session_completed": event_types["session.completed"] > 0,
                "websocket_closed": event_types["websocket.closed"] > 0,
                "websocket_clean": _websocket_clean(events),
            },
        },
        "segments": [_segment_dict(segment) for segment in ordered_segments],
        "revisions_and_commits": {
            "revisions_by_segment": {
                segment.segment_id: segment.revised_count for segment in ordered_segments
            },
            "commits_by_segment": {
                segment.segment_id: segment.committed_count for segment in ordered_segments
            },
            "max_revisions_per_segment": max(
                (segment.max_revision for segment in ordered_segments), default=0
            ),
        },
        "temporal": {
            "zero_duration_segments": zero_duration,
            "zero_duration_count": len(zero_duration),
            "overlaps": temporal_overlaps,
            "overlap_count": len(temporal_overlaps),
            "overlap_total_ms": sum(item["overlap_ms"] for item in temporal_overlaps),
            "max_overlap_ms": max((item["overlap_ms"] for item in temporal_overlaps), default=0),
            "drift": _drift_summary(drift_samples),
        },
        "replay_and_duplicates": {
            "textual_replays": text_replays,
            "replay_count": len(text_replays),
            "residual_adjacent_duplicates": residual_adjacent,
            "residual_adjacent_duplicate_count": sum(
                len(item["runs"]) for item in residual_adjacent
            ),
        },
        "projection": {
            "total_projection_ops": sum(segment.projection_ops for segment in ordered_segments),
            "total_adjacent_dup_removed": sum(
                segment.adjacent_dup_removed for segment in ordered_segments
            ),
            "total_temporal_overlap_removed": sum(
                segment.temporal_overlap_removed for segment in ordered_segments
            ),
            "total_textual_overlap_removed": sum(
                segment.textual_overlap_removed for segment in ordered_segments
            ),
            "ops_by_minute": projection_ops_by_minute,
        },
        "pauses": {
            "threshold_ms": pause_threshold_ms,
            "long_pauses": pauses,
            "long_pause_count": len(pauses),
        },
        "degradation": _projection_degradation(
            events,
            segments=segments,
            temporal_overlaps=temporal_overlaps,
            text_replays=text_replays,
        ),
        "timeline": timeline,
    }


def _segment_dict(segment: _Segment) -> dict[str, object]:
    duration = (
        segment.end_ms - segment.start_ms
        if segment.start_ms is not None and segment.end_ms is not None
        else None
    )
    return {
        "segment_id": segment.segment_id,
        "first_sequence": segment.first_sequence,
        "last_sequence": segment.last_sequence,
        "event_count": segment.event_count,
        "partial_count": segment.partial_count,
        "revised_count": segment.revised_count,
        "committed_count": segment.committed_count,
        "max_revision": segment.max_revision,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "duration_ms": duration,
        "projection_ops": segment.projection_ops,
        "adjacent_dup_removed": segment.adjacent_dup_removed,
        "temporal_overlap_removed": segment.temporal_overlap_removed,
        "textual_overlap_removed": segment.textual_overlap_removed,
        "committed_text_chars": len(segment.committed_text),
    }


def _projection_degradation(
    events: list[dict[str, Any]],
    *,
    segments: dict[str, _Segment],
    temporal_overlaps: list[dict[str, object]],
    text_replays: list[dict[str, object]],
) -> dict[str, object]:
    """Expose per-commit evidence relevant to long-session cost growth.

    The runtime currently does not emit projector latency or an explicit count
    of historical items scanned.  For those fields this report uses the
    serialized projected-transcript segment list as an observable proxy and
    records the availability explicitly; it never presents the proxy as an
    internal timing measurement.
    """
    overlap_counts: Counter[str] = Counter()
    for item in temporal_overlaps:
        for key in ("left_segment_id", "right_segment_id"):
            segment_id = item.get(key)
            if isinstance(segment_id, str):
                overlap_counts[segment_id] += 1
    replay_counts: Counter[str] = Counter()
    for item in text_replays:
        for key in ("left_segment_id", "right_segment_id"):
            segment_id = item.get(key)
            if isinstance(segment_id, str):
                replay_counts[segment_id] += 1

    rows: list[dict[str, object]] = []
    cumulative_ops = 0
    for event in events:
        if event.get("type") != "transcript.committed":
            continue
        technical = event.get("technical") or {}
        summary = technical.get("projection_summary") or {}
        operation_count = _int(summary.get("operation_count"))
        if operation_count is None:
            operation_count = _int(event.get("projection_ops")) or 0
        cumulative_ops += operation_count
        segment_id = event.get("segment_id")
        segment = segments.get(segment_id) if isinstance(segment_id, str) else None
        projection = technical.get("committed_projection") or {}
        projected_transcript = projection.get("projected_transcript") or {}
        historical_segments = (
            projected_transcript.get("segments")
            or projection.get("ordered_segments")
            or projection.get("segments")
        )
        historical_available = isinstance(historical_segments, list)
        historical_segments = historical_segments if historical_available else []
        input_texts = [
            str(item.get("source_text") or item.get("committed_text") or "")
            for item in historical_segments
            if isinstance(item, dict)
        ]
        input_chars = sum(len(text) for text in input_texts)
        input_tokens = sum(len(_tokens(text)) for text in input_texts)
        output_text = str(
            technical.get("projected_text")
            or event.get("projected_text")
            or projected_transcript.get("text")
            or ""
        )
        start_ms = _int(event.get("start_ms"))
        end_ms = _int(event.get("end_ms"))
        adjacent_dup_count = len(_adjacent_duplicate_runs(str(event.get("text") or "")))
        row = {
            "segment_id": segment_id,
            "revision": _int(event.get("revision")),
            "sequence": technical.get("sequence"),
            "minute_bucket": start_ms // 60_000 if start_ms is not None else None,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": (
                end_ms - start_ms
                if start_ms is not None and end_ms is not None
                else None
            ),
            "zero_duration": bool(
                start_ms is not None and end_ms is not None and end_ms <= start_ms
            ),
            "revision_count": segment.revised_count if segment else None,
            "segment_count_seen": len(historical_segments) if historical_available else None,
            "historical_segments_scanned": (
                len(historical_segments) if historical_available else None
            ),
            "historical_tokens_scanned": input_tokens if historical_available else None,
            "projector_input_chars": input_chars if historical_available else None,
            "projector_input_tokens": input_tokens if historical_available else None,
            "projector_output_chars": len(output_text),
            "projector_output_tokens": len(_tokens(output_text)),
            "projection_ops_added": operation_count,
            "projection_ops_total": cumulative_ops,
            "projection_latency_ms": _projection_latency_ms(technical, projection),
            "overlap_count": overlap_counts.get(segment_id, 0),
            "replay_count": replay_counts.get(segment_id, 0),
            "adjacent_dup_count": adjacent_dup_count,
            "measurement_notes": {
                "historical_scan": (
                    "proxy_from_projected_transcript_segments"
                    if historical_available
                    else "unavailable"
                ),
                "projection_latency": (
                    "runtime_metric"
                    if _projection_latency_ms(technical, projection) is not None
                    else "not_emitted"
                ),
            },
        }
        rows.append(row)

    numeric_pairs = {
        "segment_count_seen_vs_projection_ops_added": (
            "segment_count_seen",
            "projection_ops_added",
        ),
        "historical_tokens_scanned_vs_projection_ops_added": (
            "historical_tokens_scanned",
            "projection_ops_added",
        ),
        "zero_duration_vs_projection_ops_added": ("zero_duration", "projection_ops_added"),
        "replay_count_vs_projection_ops_added": ("replay_count", "projection_ops_added"),
        "overlap_count_vs_projection_ops_added": ("overlap_count", "projection_ops_added"),
    }
    correlations = {
        name: _correlation(
            [row[left] for row in rows if isinstance(row.get(left), int | float)],
            [row[right] for row in rows if isinstance(row.get(left), int | float)],
        )
        for name, (left, right) in numeric_pairs.items()
    }
    available_latency = [
        row["projection_latency_ms"]
        for row in rows
        if isinstance(row.get("projection_latency_ms"), int | float)
    ]
    return {
        "availability": {
            "projection_latency_ms": bool(available_latency),
            "historical_scan_proxy": any(
                row["historical_segments_scanned"] is not None for row in rows
            ),
            "active_vs_superseded_segments": False,
        },
        "missing_runtime_metrics": [
            "projection_latency_ms",
            "active_vs_superseded_segments",
        ],
        "commit_count": len(rows),
        "commits": rows,
        "correlations": correlations,
    }


def _projection_latency_ms(
    technical: dict[str, Any], projection: dict[str, Any]
) -> int | float | None:
    for container in (technical, projection):
        for key in ("projection_latency_ms", "latency_ms"):
            value = container.get(key)
            if isinstance(value, int | float):
                return value
    return None


def _correlation(left: list[object], right: list[object]) -> float | None:
    pairs = [
        (float(left_value), float(right_value))
        for left_value, right_value in zip(left, right)
        if isinstance(left_value, int | float) and isinstance(right_value, int | float)
    ]
    if len(pairs) < 2:
        return None
    left_values = [pair[0] for pair in pairs]
    right_values = [pair[1] for pair in pairs]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in pairs
    )
    left_variance = sum((value - left_mean) ** 2 for value in left_values)
    right_variance = sum((value - right_mean) ** 2 for value in right_values)
    denominator = (left_variance * right_variance) ** 0.5
    return numerator / denominator if denominator else None


def _collect_drift(event: dict[str, Any], output: list[dict[str, object]]) -> None:
    diagnostics = (event.get("technical") or {}).get("span_diagnostics") or {}
    event_span = diagnostics.get("provider_event_span") or {}
    word_span = diagnostics.get("provider_word_span") or {}
    astera_span = diagnostics.get("astera_segment_span") or {}
    pairs = (
        ("provider_event_to_words", event_span, word_span),
        ("provider_words_to_astera", word_span, astera_span),
    )
    for kind, left, right in pairs:
        left_start, left_end = _int(left.get("start_ms")), _int(left.get("end_ms"))
        right_start, right_end = _int(right.get("start_ms")), _int(right.get("end_ms"))
        if left_start is None or left_end is None or right_start is None or right_end is None:
            continue
        output.append(
            {
                "kind": kind,
                "start_drift_ms": right_start - left_start,
                "end_drift_ms": right_end - left_end,
                "absolute_max_drift_ms": max(
                    abs(right_start - left_start), abs(right_end - left_end)
                ),
                "segment_id": event.get("segment_id"),
                "sequence": (event.get("technical") or {}).get("sequence"),
            }
        )


def _drift_summary(samples: list[dict[str, object]]) -> dict[str, object]:
    by_kind: dict[str, list[dict[str, object]]] = defaultdict(list)
    for sample in samples:
        by_kind[str(sample["kind"])].append(sample)
    result: dict[str, object] = {"sample_count": len(samples), "samples": samples}
    for kind, items in by_kind.items():
        values = [float(item["absolute_max_drift_ms"]) for item in items]
        result[kind] = {
            "sample_count": len(values),
            "mean_absolute_max_drift_ms": mean(values),
            "max_absolute_max_drift_ms": max(values),
            "over_1000ms_count": sum(value > 1000 for value in values),
        }
    return result


def _temporal_overlaps(segments: list[_Segment]) -> list[dict[str, object]]:
    overlaps: list[dict[str, object]] = []
    for left, right in zip(segments, segments[1:]):
        if left.end_ms is None or right.start_ms is None:
            continue
        overlap = min(left.end_ms, right.end_ms or left.end_ms) - max(
            left.start_ms or 0, right.start_ms
        )
        if overlap > 0:
            overlaps.append(
                {
                    "left_segment_id": left.segment_id,
                    "right_segment_id": right.segment_id,
                    "overlap_ms": overlap,
                }
            )
    return overlaps


def _text_replays(segments: list[_Segment]) -> list[dict[str, object]]:
    replays: list[dict[str, object]] = []
    for left, right in zip(segments, segments[1:]):
        left_tokens, right_tokens = _tokens(left.committed_text), _tokens(right.committed_text)
        suffix_prefix = _suffix_prefix(left_tokens, right_tokens)
        contains = _contains(right_tokens, left_tokens)
        if suffix_prefix or contains:
            replays.append(
                {
                    "left_segment_id": left.segment_id,
                    "right_segment_id": right.segment_id,
                    "suffix_prefix_overlap_tokens": suffix_prefix,
                    "right_contains_left": contains,
                }
            )
    return replays


def _adjacent_duplicate_runs(text: str) -> list[dict[str, object]]:
    tokens = text.split()
    normalized = [_normalize(token) for token in tokens]
    result: list[dict[str, object]] = []
    for index in range(len(tokens) - 1):
        if normalized[index] and normalized[index] == normalized[index + 1]:
            result.append({"text": " ".join(tokens[index : index + 2]), "token_start": index})
    return result


def _long_pauses(
    speech_starts: list[dict[str, object]],
    segments: list[_Segment],
    threshold_ms: int,
) -> list[dict[str, object]]:
    points = sorted(
        [item for item in speech_starts if item.get("timestamp_ms") is not None],
        key=lambda item: int(item["timestamp_ms"]),
    )
    pauses: list[dict[str, object]] = []
    for previous, current in zip(points, points[1:]):
        gap = int(current["timestamp_ms"]) - int(previous["timestamp_ms"])
        if gap < threshold_ms:
            continue
        next_segment = next(
            (segment for segment in segments if segment.segment_id == current.get("segment_id")),
            None,
        )
        pauses.append(
            {
                "from_timestamp_ms": previous["timestamp_ms"],
                "to_timestamp_ms": current["timestamp_ms"],
                "gap_ms": gap,
                "before_segment_id": previous.get("segment_id"),
                "after_segment_id": current.get("segment_id"),
                "after_revision_count": next_segment.revised_count if next_segment else None,
                "after_commit_count": next_segment.committed_count if next_segment else None,
            }
        )
    return pauses


def _timeline(
    events: list[dict[str, Any]],
    segments: list[_Segment],
    commits: list[dict[str, Any]],
    duration_ms: int | None,
    bucket_ms: int,
) -> list[dict[str, object]]:
    maximum = duration_ms or 0
    buckets: dict[int, dict[str, object]] = {}
    for event in events:
        timestamp = _event_timestamp(event)
        if timestamp is None:
            continue
        bucket = (timestamp // bucket_ms) * bucket_ms
        item = buckets.setdefault(
            bucket,
            {
                "start_ms": bucket,
                "end_ms": bucket + bucket_ms,
                "event_count": 0,
                "revisions": 0,
                "commits": 0,
                "projection_ops": 0,
                "adjacent_dup_removed": 0,
            },
        )
        item["event_count"] += 1
        if event.get("type") == "transcript.revised":
            item["revisions"] += 1
        if event.get("type") == "transcript.committed":
            item["commits"] += 1
            summary = (event.get("technical") or {}).get("projection_summary") or {}
            item["projection_ops"] += _int(summary.get("operation_count")) or 0
            item["adjacent_dup_removed"] += _int(summary.get("adjacent_dup_removed")) or 0
        maximum = max(maximum, timestamp)
    if maximum and not buckets:
        buckets[0] = {
            "start_ms": 0,
            "end_ms": bucket_ms,
            "event_count": 0,
            "revisions": 0,
            "commits": 0,
            "projection_ops": 0,
            "adjacent_dup_removed": 0,
        }
    return [buckets[key] for key in sorted(buckets)]


def _projection_ops_by_minute(commits: list[dict[str, Any]]) -> dict[str, int]:
    result: Counter[str] = Counter()
    for event in commits:
        timestamp = _int(event.get("start_ms"))
        if timestamp is None:
            continue
        summary = (event.get("technical") or {}).get("projection_summary") or {}
        result[str(timestamp // 60_000)] += _int(summary.get("operation_count")) or 0
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


def _session_duration(events: list[dict[str, Any]], timestamps: list[int]) -> int | None:
    completed = [
        _int(event.get("timestamp_ms"))
        for event in events
        if event.get("type") in {"audio.completed", "session.completed"}
    ]
    completed = [value for value in completed if value is not None]
    return max(completed or timestamps or [0])


def _event_timestamp(event: dict[str, Any]) -> int | None:
    for key in ("timestamp_ms", "start_ms", "end_ms"):
        value = _int(event.get(key))
        if value is not None:
            return value
    return None


def _websocket_clean(events: list[dict[str, Any]]) -> bool | None:
    closed = [event for event in events if event.get("type") == "websocket.closed"]
    if not closed:
        return None
    return all(
        event.get("clean") is not False and event.get("code") in {None, 1000}
        for event in closed
    )


def _is_complete_event_stream(events: list[dict[str, Any]], sequences: list[int]) -> bool:
    types = {event.get("type") for event in events}
    lifecycle_complete = {"audio.completed", "session.completed"}.issubset(types)
    if not lifecycle_complete:
        return False
    capture_sequences = sorted(
        _int(event.get("capture_seq"))
        for event in events
        if _int(event.get("capture_seq")) is not None
    )
    if capture_sequences:
        return capture_sequences == list(range(1, len(capture_sequences) + 1))
    return bool(sequences and min(sequences) == 1)


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _min_optional(left: int | None, right: int | None) -> int | None:
    if right is None:
        return left
    return right if left is None else min(left, right)


def _max_optional(left: int | None, right: int | None) -> int | None:
    if right is None:
        return left
    return right if left is None else max(left, right)


def _tokens(text: str) -> list[str]:
    return [_normalize(token) for token in text.split() if _normalize(token)]


def _normalize(token: str) -> str:
    return re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())


def _suffix_prefix(left: list[str], right: list[str]) -> int:
    for count in range(min(len(left), len(right)), 0, -1):
        if left[-count:] == right[:count]:
            return count
    return 0


def _contains(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )
