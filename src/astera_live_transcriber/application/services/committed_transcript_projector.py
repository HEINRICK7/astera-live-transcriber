from dataclasses import dataclass
from time import perf_counter
from typing import Final, Protocol

from .committed_diagnostics import (
    _normalize,
    _suffix_prefix_overlap,
    _tokens,
    adjacent_duplicate_runs,
)
from .provider_segment_store import ProviderSegmentState

_MAX_CONSERVATIVE_DUPLICATE_TOKENS: Final = 4
_KNOWN_STRUCTURAL_SINGLETONS: Final = {"a"}


@dataclass(frozen=True, slots=True)
class ProjectedSegment:
    segment_id: str
    source_text: str
    projected_text: str
    start_ms: int
    end_ms: int | None


@dataclass(frozen=True, slots=True)
class ProjectionOperation:
    operation: str
    segment_id: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectedTranscript:
    text: str
    segments: tuple[ProjectedSegment, ...]
    operations: tuple[ProjectionOperation, ...]

    def summary(self) -> dict[str, int]:
        return {
            "operation_count": len(self.operations),
            "adjacent_dup_removed": sum(
                operation.operation == "remove_adjacent_duplicate"
                for operation in self.operations
            ),
            "temporal_overlap_removed": sum(
                "temporal" in operation.details.get("overlap_basis", [])
                for operation in self.operations
            ),
            "textual_overlap_removed": sum(
                "textual" in operation.details.get("overlap_basis", [])
                for operation in self.operations
            ),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "source_text": segment.source_text,
                    "projected_text": segment.projected_text,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                }
                for segment in self.segments
            ],
            "operations": [
                {
                    "operation": operation.operation,
                    "segment_id": operation.segment_id,
                    "details": operation.details,
                }
                for operation in self.operations
            ],
            "summary": self.summary(),
        }


@dataclass(frozen=True, slots=True)
class ProjectionState:
    """Incremental read-model state; provider evidence stays outside it."""

    active_segments: tuple[ProviderSegmentState, ...] = ()
    superseded_segments: tuple[str, ...] = ()
    superseded_sources: tuple[ProviderSegmentState, ...] = ()
    projected_text: str = ""
    local_working_set: tuple[ProviderSegmentState, ...] = ()


class ProjectionWorkingSetPolicy(Protocol):
    def select(
        self,
        state: ProjectionState,
        incoming: ProviderSegmentState,
    ) -> list[ProviderSegmentState]:
        """Select the bounded set that may participate in this projection."""


class LastActiveSegmentsPolicy:
    """Deterministic bounded neighborhood policy for the first implementation."""

    def __init__(self, size: int = 4) -> None:
        if size < 1:
            raise ValueError("working set size must be at least 1")
        self.size = size

    def select(
        self,
        state: ProjectionState,
        incoming: ProviderSegmentState,
    ) -> list[ProviderSegmentState]:
        active = [
            segment
            for segment in state.active_segments
            if segment.segment_id != incoming.segment_id
        ]
        combined = sorted(
            [*active, incoming], key=lambda item: (item.start_ms, item.segment_id)
        )
        if len(combined) <= self.size:
            return combined
        insertion = next(
            index
            for index, segment in enumerate(combined)
            if segment.segment_id == incoming.segment_id
        )
        left = max(0, insertion - self.size // 2)
        right = min(len(combined), left + self.size)
        if right - left < self.size:
            left = max(0, right - self.size)
        return combined[left:right]


class IndexedOverlapWorkingSetPolicy:
    """Add only time-compatible superseded snapshots to a bounded working set."""

    def __init__(self, size: int = 4, max_reactivated_segments: int = 4) -> None:
        if size < 1:
            raise ValueError("working set size must be at least 1")
        if max_reactivated_segments < 0:
            raise ValueError("max reactivated segments cannot be negative")
        self.size = size
        self.max_reactivated_segments = max_reactivated_segments
        self._recent_policy = LastActiveSegmentsPolicy(size)

    def select(
        self,
        state: ProjectionState,
        incoming: ProviderSegmentState,
    ) -> list[ProviderSegmentState]:
        selected = self._recent_policy.select(state, incoming)
        selected_ids = {segment.segment_id for segment in selected}
        anchors = [incoming, *selected]
        eligible = sorted(
            (
                segment
                for segment in state.superseded_sources
                if segment.segment_id not in selected_ids
                and max(
                    _segment_time_overlap(segment, anchor) for anchor in anchors
                )
                > 0
            ),
            key=lambda segment: (
                -max(
                    _segment_time_overlap(segment, anchor) for anchor in anchors
                ),
                segment.start_ms,
                segment.segment_id,
            ),
        )
        selected.extend(eligible[: self.max_reactivated_segments])
        return sorted(selected, key=lambda segment: (segment.start_ms, segment.segment_id))

class CommittedTranscriptProjector:
    """Builds a clean read model without mutating provider segment evidence.

    This is intentionally structural. It only removes exact token replay that
    is supported by temporal/textual segment overlap or by an adjacent replay
    inside an overlapping committed snapshot. It never applies semantic or
    fuzzy corrections.
    """

    def project(
        self,
        segments: tuple[ProviderSegmentState, ...] | list[ProviderSegmentState],
        *,
        replay_evidence: set[tuple[str, tuple[str, ...]]] | None = None,
        replay_evidence_by_segment: dict[str, set[tuple[str, tuple[str, ...]]]]
        | None = None,
        preprojected_ids: set[str] | None = None,
    ) -> ProjectedTranscript:
        projected: list[ProjectedSegment] = []
        operations: list[ProjectionOperation] = []
        for source in segments:
            if not source.committed_text:
                continue
            source_text = source.committed_text
            if preprojected_ids and source.segment_id in preprojected_ids:
                current_text, duplicate_details = source_text, None
            else:
                current_text, duplicate_details = _clean_internal_replay(
                    source_text,
                    [
                        item.committed_text or ""
                        for item in segments
                        if item.segment_id != source.segment_id
                    ],
                    (
                        replay_evidence_by_segment.get(source.segment_id)
                        if replay_evidence_by_segment is not None
                        else replay_evidence
                    ),
                )
            if duplicate_details:
                operations.append(
                    ProjectionOperation(
                        "remove_revision_duplicate",
                        source.segment_id,
                        duplicate_details,
                    )
                )
            previous = projected[-1] if projected else None
            if previous is not None:
                temporal_overlap = self._temporal_overlap(previous, source)
                overlap_tokens = _suffix_prefix_overlap(
                    _tokens(previous.projected_text), _tokens(current_text)
                )
                contains_previous = self._contains(current_text, previous.projected_text)
                common = _longest_common_block(previous.projected_text, current_text)
                structural_overlap = (
                    temporal_overlap > 0
                    or overlap_tokens > 0
                    or contains_previous
                    or common[2] >= 5
                )
                if structural_overlap and (contains_previous or common[2] >= 5):
                    projected.pop()
                    operations.append(
                        ProjectionOperation(
                            "remove_boundary_replay",
                            source.segment_id,
                            {
                                "target_segment_id": previous.segment_id,
                                "removed_token_count": (
                                    len(_tokens(previous.projected_text))
                                    if contains_previous
                                    else common[2]
                                ),
                                "common_block": common[3],
                                "overlap_basis": _overlap_basis(
                                    temporal_overlap,
                                    max(overlap_tokens, common[2]),
                                    contains_previous,
                                ),
                                "reason": "authoritative_snapshot_replaces_overlapping_segment",
                            },
                        )
                    )
                elif structural_overlap and overlap_tokens:
                    current_text, replay_details = self._remove_segment_replay(
                        previous,
                        current_text,
                        overlap_tokens,
                        contains_previous,
                    )
                    if replay_details is not None:
                        replay_details["overlap_basis"] = _overlap_basis(
                            temporal_overlap,
                            overlap_tokens,
                            contains_previous,
                        )
                        operations.append(
                            ProjectionOperation(
                                "remove_boundary_replay",
                                source.segment_id,
                                replay_details,
                            )
                        )
            projected.append(
                ProjectedSegment(
                    segment_id=source.segment_id,
                    source_text=source_text,
                    projected_text=current_text,
                    start_ms=source.start_ms,
                    end_ms=source.end_ms,
                )
            )
        return ProjectedTranscript(
            text="\n".join(
                segment.projected_text for segment in projected if segment.projected_text
            ),
            segments=tuple(projected),
            operations=tuple(operations),
        )

    @staticmethod
    def _remove_segment_replay(
        previous: ProjectedSegment,
        current_text: str,
        overlap_tokens: int,
        contains_previous: bool,
    ) -> tuple[str, dict[str, object] | None]:
        if contains_previous and previous.projected_text.strip():
            return (
                current_text,
                {
                    "mode": "replace_previous",
                    "previous_segment_id": previous.segment_id,
                    "removed_text": previous.projected_text,
                    "overlap_tokens": len(_tokens(previous.projected_text)),
                },
            )
        if overlap_tokens:
            current_tokens = current_text.split()
            # The overlap is a prefix of the current snapshot; retain its
            # latest, potentially expanded version rather than appending it.
            return (
                " ".join(current_tokens[overlap_tokens:]),
                {
                    "mode": "remove_segment_replay",
                    "previous_segment_id": previous.segment_id,
                    "removed_token_count": overlap_tokens,
                    "removed_prefix": current_tokens[:overlap_tokens],
                },
            )
        return current_text, None

    @staticmethod
    def _temporal_overlap(previous: ProjectedSegment, current: ProviderSegmentState) -> int:
        if previous.end_ms is None or current.end_ms is None:
            return 0
        return max(
            0,
            min(previous.end_ms, current.end_ms) - max(previous.start_ms, current.start_ms),
        )

    @staticmethod
    def _contains(haystack: str, needle: str) -> bool:
        normalized_haystack = [_normalize(token) for token in haystack.split()]
        normalized_needle = [_normalize(token) for token in needle.split()]
        if not normalized_needle or len(normalized_needle) > len(normalized_haystack):
            return False
        return any(
            normalized_haystack[index : index + len(normalized_needle)] == normalized_needle
            for index in range(len(normalized_haystack) - len(normalized_needle) + 1)
        )


class IncrementalCommittedTranscriptProjector:
    """Incrementally project committed segments using a bounded working set.

    The stateless :class:`CommittedTranscriptProjector` remains the structural
    oracle used by regression tests. This adapter keeps the provider evidence
    outside its working set and only reprojects a local neighborhood around
    the newly committed segment. Removed segments are retained as IDs in the
    superseded view; the provider store remains the immutable evidence history.
    """

    def __init__(
        self,
        working_set_size: int = 4,
        working_set_policy: ProjectionWorkingSetPolicy | None = None,
    ) -> None:
        self._policy = working_set_policy or IndexedOverlapWorkingSetPolicy(
            working_set_size
        )
        self.working_set_size = getattr(self._policy, "size", working_set_size)
        self._sources: dict[str, ProviderSegmentState] = {}
        self._active: list[ProjectedSegment] = []
        self._superseded: set[str] = set()
        self._replay_evidence_by_segment: dict[
            str, set[tuple[str, tuple[str, ...]]]
        ] = {}
        self._last_reactivated_segments: tuple[str, ...] = ()
        self._state = ProjectionState()
        self._projection_ops_total = 0
        self._last_metrics: dict[str, object] = {
            "working_set_size": self.working_set_size,
            "working_set_segment_count": 0,
            "working_set_token_count": 0,
            "historical_segments_scanned": 0,
            "historical_tokens_scanned": 0,
            "projector_input_chars": 0,
            "projector_input_tokens": 0,
            "projector_output_chars": 0,
            "projector_output_tokens": 0,
            "projection_ops_added": 0,
            "projection_ops_total": 0,
            "active_segment_count": 0,
            "superseded_segment_count": 0,
            "projection_latency_ms": None,
        }

    def project(self, source: ProviderSegmentState) -> ProjectedTranscript:
        """Project one authoritative commit and return the full read model."""
        started_at = perf_counter()
        self._sources[source.segment_id] = source
        active_before = list(self._state.active_segments)
        source_position = next(
            (
                index
                for index, segment in enumerate(self._active)
                if segment.segment_id == source.segment_id
            ),
            None,
        )
        active_projected = [
            segment for segment in self._active if segment.segment_id != source.segment_id
        ]
        self._active = active_projected
        state_before = ProjectionState(
            active_segments=tuple(active_before),
            superseded_segments=tuple(sorted(self._superseded)),
            superseded_sources=tuple(
                self._sources[segment_id]
                for segment_id in sorted(self._superseded)
                if segment_id in self._sources
            ),
            projected_text=self._state.projected_text,
        )
        local_sources = self._policy.select(state_before, source)
        active_ids = {segment.segment_id for segment in active_before}
        self._last_reactivated_segments = tuple(
            segment.segment_id
            for segment in local_sources
            if segment.segment_id in self._superseded and segment.segment_id not in active_ids
        )
        local_ids = {segment.segment_id for segment in local_sources}
        local_positions = [
            index
            for index, segment in enumerate(active_projected)
            if segment.segment_id in local_ids
        ]
        if source_position is not None:
            local_positions.append(source_position)
        if local_positions:
            local_start = min(local_positions)
            local_end = max(local_positions) + 1
        else:
            local_start = sum(
                (item.start_ms, item.segment_id)
                < (source.start_ms, source.segment_id)
                for item in active_projected
            )
            local_end = local_start
        prefix = active_projected[:local_start]
        suffix = active_projected[local_end:]
        if suffix:
            trailing_context = suffix[0]
            if trailing_context.segment_id not in local_ids:
                local_sources.append(self._sources[trailing_context.segment_id])
                suffix = suffix[1:]
        local_sources = sorted(
            local_sources, key=lambda segment: (segment.start_ms, segment.segment_id)
        )
        boundary = prefix[-1] if prefix else None
        if boundary is not None and any(
            (segment.start_ms, segment.segment_id)
            < (boundary.start_ms, boundary.segment_id)
            for segment in local_sources
        ):
            local_sources.append(self._sources[boundary.segment_id])
            local_sources = sorted(
                local_sources,
                key=lambda segment: (segment.start_ms, segment.segment_id),
            )
            prefix = prefix[:-1]
            boundary = None
        working_sources: list[ProviderSegmentState] = []
        if boundary is not None:
            working_sources.append(
                ProviderSegmentState(
                    segment_id=boundary.segment_id,
                    revision=0,
                    committed_text=boundary.projected_text,
                    start_ms=boundary.start_ms,
                    end_ms=boundary.end_ms,
                    status="committed",
                )
            )
        working_sources.extend(local_sources)
        local_projection = CommittedTranscriptProjector().project(
            working_sources,
            replay_evidence_by_segment={
                item.segment_id: {
                    evidence
                    for segment_id, segment_evidence in self._replay_evidence_by_segment.items()
                    if segment_id != item.segment_id
                    for evidence in segment_evidence
                }
                for item in working_sources
            },
            preprojected_ids={boundary.segment_id} if boundary is not None else None,
        )
        projected = self._merge(prefix, boundary, local_projection, suffix)
        projected_ids = {segment.segment_id for segment in projected}
        self._superseded.update(set(self._sources) - projected_ids)
        self._superseded.difference_update(projected_ids)
        self._active = projected
        self._replay_evidence_by_segment.setdefault(source.segment_id, set()).update(
            _replay_evidence(source.committed_text or "")
        )
        projected_text = "\n".join(
            segment.projected_text for segment in projected if segment.projected_text
        )
        self._state = ProjectionState(
            active_segments=tuple(
                self._sources[segment.segment_id]
                for segment in projected
                if segment.segment_id in self._sources
            ),
            superseded_segments=tuple(sorted(self._superseded)),
            superseded_sources=tuple(
                self._sources[segment_id]
                for segment_id in sorted(self._superseded)
                if segment_id in self._sources
            ),
            projected_text=projected_text,
            local_working_set=tuple(local_sources),
        )
        self._projection_ops_total += len(local_projection.operations)
        self._last_metrics = self._metrics(
            local_sources,
            projected,
            local_projection,
            self._projection_ops_total,
            len(self._state.active_segments),
            len(self._state.superseded_segments),
            (perf_counter() - started_at) * 1000,
        )
        self._last_metrics["reactivated_segments"] = list(
            self._last_reactivated_segments
        )
        self._last_metrics["working_set_bound"] = (
            self.working_set_size
            + getattr(self._policy, "max_reactivated_segments", 0)
            + 1
        )
        return ProjectedTranscript(
            text=projected_text,
            segments=tuple(projected),
            operations=local_projection.operations,
        )

    def active_sources(self) -> tuple[ProviderSegmentState, ...]:
        return self._state.active_segments

    def projection_state(self) -> dict[str, object]:
        return {
            "active_segments": [segment.segment_id for segment in self._state.active_segments],
            "superseded_segments": list(self._state.superseded_segments),
            "projected_text": self._state.projected_text,
            "local_working_set": [
                segment.segment_id for segment in self._state.local_working_set
            ],
            "active_segment_count": len(self._state.active_segments),
            "superseded_segment_count": len(self._state.superseded_segments),
            "superseded_index": {
                "by_time_range": [
                    segment.segment_id for segment in self._state.superseded_sources
                ]
            },
            "reactivated_segments": list(self._last_reactivated_segments),
            "working_set_size": self.working_set_size,
        }

    def projection_metrics(self) -> dict[str, object]:
        return dict(self._last_metrics)

    def projection_debug_context(self, incoming: ProviderSegmentState) -> dict[str, object]:
        """Return pre-projection evidence for offline divergence analysis."""
        state_before = ProjectionState(
            active_segments=tuple(self._state.active_segments),
            superseded_segments=tuple(sorted(self._superseded)),
            superseded_sources=tuple(
                self._sources[segment_id]
                for segment_id in sorted(self._superseded)
                if segment_id in self._sources
            ),
            projected_text=self._state.projected_text,
        )
        selected = self._policy.select(state_before, incoming)
        active = list(state_before.active_segments)
        ordered = sorted(
            [*active, incoming], key=lambda segment: (segment.start_ms, segment.segment_id)
        )
        incoming_index = next(
            index
            for index, segment in enumerate(ordered)
            if segment.segment_id == incoming.segment_id
        )
        return {
            "previous_state": {
                "projected_text_tail": state_before.projected_text[-500:],
                "active_segments": [segment.segment_id for segment in active],
                "superseded_segments": list(state_before.superseded_segments),
            },
            "incoming_segment": _provider_segment_as_dict(incoming),
            "working_set_candidates": [segment.segment_id for segment in selected],
            "superseded_eligible": [
                segment.segment_id
                for segment in selected
                if segment.segment_id in state_before.superseded_segments
            ],
            "boundary_context": {
                "previous_active": ordered[incoming_index - 1].segment_id
                if incoming_index > 0
                else None,
                "next_active": ordered[incoming_index + 1].segment_id
                if incoming_index + 1 < len(ordered)
                else None,
            },
            "decision": {
                "reactivate": [
                    segment.segment_id
                    for segment in selected
                    if segment.segment_id in state_before.superseded_segments
                ],
                "working_set_bounded": len(selected)
                <= self.working_set_size
                + getattr(self._policy, "max_reactivated_segments", 0)
                + 1,
            },
        }

    def reset(self) -> None:
        self._sources.clear()
        self._active.clear()
        self._superseded.clear()
        self._replay_evidence_by_segment.clear()
        self._last_reactivated_segments = ()
        self._state = ProjectionState()
        self._projection_ops_total = 0

    @staticmethod
    def _merge(
        prefix: list[ProjectedSegment],
        boundary: ProjectedSegment | None,
        local: ProjectedTranscript,
        suffix: list[ProjectedSegment],
    ) -> list[ProjectedSegment]:
        if boundary is None:
            return [*prefix, *local.segments, *suffix]
        prefix_without_boundary = prefix[:-1]
        return [*prefix_without_boundary, *local.segments, *suffix]

    @staticmethod
    def _metrics(
        local_sources: list[ProviderSegmentState],
        projected: list[ProjectedSegment],
        local_projection: ProjectedTranscript,
        projection_ops_total: int,
        active_segment_count: int,
        superseded_segment_count: int,
        projection_latency_ms: float,
    ) -> dict[str, object]:
        input_text = [source.committed_text or "" for source in local_sources]
        output_text = "\n".join(segment.projected_text for segment in projected)
        return {
            "working_set_size": len(local_sources),
            "working_set_segment_count": len(local_sources),
            "working_set_token_count": sum(len(_tokens(text)) for text in input_text),
            "historical_segments_scanned": len(local_sources),
            "historical_tokens_scanned": sum(len(_tokens(text)) for text in input_text),
            "projector_input_chars": sum(len(text) for text in input_text),
            "projector_input_tokens": sum(len(_tokens(text)) for text in input_text),
            "projector_output_chars": len(output_text),
            "projector_output_tokens": len(_tokens(output_text)),
            "projection_ops_added": len(local_projection.operations),
            "projection_ops_total": projection_ops_total,
            "active_segment_count": active_segment_count,
            "superseded_segment_count": superseded_segment_count,
            "projection_latency_ms": round(projection_latency_ms, 3),
        }


def _segment_time_overlap(
    left: ProviderSegmentState, right: ProviderSegmentState
) -> int:
    if left.end_ms is None or right.end_ms is None:
        return 0
    return max(
        0,
        min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms),
    )


def _provider_segment_as_dict(segment: ProviderSegmentState) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "revision": segment.revision,
        "committed_text": segment.committed_text,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "status": segment.status,
    }


def _collapse_duplicate_runs(text: str, runs: list[dict[str, object]]) -> str:
    tokens = text.split()
    remove: set[int] = set()
    for run in runs:
        start = int(run["token_start"])
        block = run["block"]
        if not isinstance(block, list) or len(block) > _MAX_CONSERVATIVE_DUPLICATE_TOKENS:
            continue
        remove.update(range(start + len(block), int(run["token_end"])))
    return " ".join(token for index, token in enumerate(tokens) if index not in remove)


def _clean_internal_replay(
    text: str,
    evidence_texts: list[str],
    replay_evidence: set[tuple[str, tuple[str, ...]]] | None = None,
) -> tuple[str, dict[str, object] | None]:
    tokens = text.split()
    normalized = [_normalize(token) for token in tokens]
    remove: set[int] = set()
    runs = adjacent_duplicate_runs(text)
    removed_runs: list[dict[str, object]] = []
    for run in runs:
        start = int(run["token_start"])
        end = int(run["token_end"])
        block = run["block"]
        if not isinstance(block, list):
            continue
        block_len = len(block)
        if block_len >= 2:
            remove.update(range(start + block_len, end))
            removed_runs.append(run)
            continue
        left = tokens[start]
        right = tokens[start + 1]
        if left != right and not left.endswith(":"):
            continue
        if (
            left.endswith(":")
            or left == right and left.endswith((".", "!", "?"))
            or left == right and left.casefold() in _KNOWN_STRUCTURAL_SINGLETONS
            or _single_token_replay_has_evidence(
                normalized, start, evidence_texts, replay_evidence
            )
        ):
            remove.add(start + 1)
            removed_runs.append(run)
    return (
        " ".join(token for index, token in enumerate(tokens) if index not in remove),
        {
            "runs": removed_runs,
            "basis": ["internal_exact_replay", "committed_snapshot_evidence"],
        }
        if removed_runs
        else None,
    )


def _single_token_replay_has_evidence(
    normalized: list[str],
    index: int,
    evidence_texts: list[str],
    replay_evidence: set[tuple[str, tuple[str, ...]]] | None = None,
) -> bool:
    token = normalized[index]
    following = normalized[index + 2 : index + 4]
    if not following:
        return False
    if replay_evidence is not None and (token, tuple(following)) in replay_evidence:
        return True
    for evidence in evidence_texts:
        evidence_tokens = [_normalize(item) for item in evidence.split()]
        for start, value in enumerate(evidence_tokens):
            if value == token and evidence_tokens[
                start + 1 : start + 1 + len(following)
            ] == following:
                return True
    return False


def _add_replay_evidence(
    index: set[tuple[str, tuple[str, ...]]], text: str
) -> None:
    index.update(_replay_evidence(text))


def _replay_evidence(text: str) -> set[tuple[str, tuple[str, ...]]]:
    index: set[tuple[str, tuple[str, ...]]] = set()
    normalized = [_normalize(token) for token in text.split()]
    for position, token in enumerate(normalized):
        following = tuple(item for item in normalized[position + 1 : position + 3] if item)
        if token and following:
            index.add((token, following))
    return index


def _longest_common_block(left: str, right: str) -> tuple[int, int, int, list[str]]:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    best = (0, 0, 0, [])
    previous: list[int] = [0] * (len(right_tokens) + 1)
    for left_index, left_token in enumerate(left_tokens, start=1):
        current = [0] * (len(right_tokens) + 1)
        for right_index, right_token in enumerate(right_tokens, start=1):
            if left_token != right_token:
                continue
            current[right_index] = previous[right_index - 1] + 1
            if current[right_index] > best[2]:
                count = current[right_index]
                best = (
                    left_index - count,
                    right_index - count,
                    count,
                    right_tokens[right_index - count : right_index],
                )
        previous = current
    return best


def _overlap_basis(
    temporal_overlap_ms: int,
    textual_overlap_tokens: int,
    contains_previous: bool,
) -> list[str]:
    basis: list[str] = []
    if temporal_overlap_ms > 0:
        basis.append("temporal")
    if textual_overlap_tokens > 0 or contains_previous:
        basis.append("textual")
    return basis
