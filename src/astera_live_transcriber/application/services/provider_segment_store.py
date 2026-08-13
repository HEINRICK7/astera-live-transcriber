from dataclasses import dataclass


@dataclass(slots=True)
class ProviderSegmentState:
    """State of one provider-backed segment.

    Provisional hypotheses are replaceable. A committed snapshot is
    authoritative for this segment and replaces the provisional text in full.
    """

    segment_id: str
    revision: int
    provisional_text: str = ""
    committed_text: str | None = None
    start_ms: int = 0
    end_ms: int | None = None
    status: str = "streaming"


@dataclass(frozen=True, slots=True)
class ProviderSegmentEvidence:
    """Immutable committed snapshot retained for audit and regression analysis."""

    segment_id: str
    revision: int
    committed_text: str
    start_ms: int
    end_ms: int | None


class ProviderSegmentStore:
    def __init__(self) -> None:
        self._segments: dict[str, ProviderSegmentState] = {}
        self._evidence_history: list[ProviderSegmentEvidence] = []

    def upsert_provisional(
        self,
        segment_id: str,
        revision: int,
        text: str,
        start_ms: int,
        end_ms: int | None = None,
    ) -> ProviderSegmentState:
        state = self._segments.get(segment_id)
        if state is None:
            state = ProviderSegmentState(segment_id=segment_id, revision=revision)
            self._segments[segment_id] = state
        elif state.status == "committed":
            return state
        state.revision = max(state.revision, revision)
        state.provisional_text = text
        state.start_ms = start_ms
        state.end_ms = end_ms
        state.status = "streaming"
        return state

    def commit(
        self,
        segment_id: str,
        revision: int,
        text: str,
        start_ms: int,
        end_ms: int | None = None,
    ) -> ProviderSegmentState:
        state = self._segments.get(segment_id)
        if state is None:
            state = ProviderSegmentState(segment_id=segment_id, revision=revision)
            self._segments[segment_id] = state
        self._evidence_history.append(
            ProviderSegmentEvidence(
                segment_id=segment_id,
                revision=revision,
                committed_text=text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
        state.revision = max(state.revision, revision)
        state.provisional_text = ""
        state.committed_text = text
        state.start_ms = start_ms
        state.end_ms = end_ms
        state.status = "committed"
        return state

    def get(self, segment_id: str) -> ProviderSegmentState | None:
        return self._segments.get(segment_id)

    @staticmethod
    def snapshot(state: ProviderSegmentState | None) -> dict[str, object] | None:
        if state is None:
            return None
        return {
            "segment_id": state.segment_id,
            "revision": state.revision,
            "status": state.status,
            "provisional_text": state.provisional_text,
            "committed_text": state.committed_text,
            "start_ms": state.start_ms,
            "end_ms": state.end_ms,
        }

    def ordered_committed(self) -> tuple[ProviderSegmentState, ...]:
        return tuple(
            sorted(
                (segment for segment in self._segments.values() if segment.committed_text),
                key=lambda segment: (segment.start_ms, segment.segment_id),
            )
        )

    def evidence_history(self) -> tuple[ProviderSegmentEvidence, ...]:
        return tuple(self._evidence_history)

    def reset(self) -> None:
        self._segments.clear()
        self._evidence_history.clear()
