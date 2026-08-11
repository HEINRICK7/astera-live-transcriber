from astera_live_transcriber.domain.session import TurnDecision, TurnState


class TimeBasedTurnDetector:
    def __init__(
        self,
        commit_silence_ms: int,
        max_silence_ms: int,
    ) -> None:
        if commit_silence_ms <= 0 or max_silence_ms < commit_silence_ms:
            raise ValueError("turn silence thresholds are invalid")
        self._commit_silence_ms = commit_silence_ms
        self._max_silence_ms = max_silence_ms

    async def evaluate(self, state: TurnState) -> TurnDecision:
        if not state.active_segment:
            return TurnDecision.WAIT
        if state.segment_duration_ms >= state.max_segment_duration_ms:
            return TurnDecision.FORCE_COMMIT
        if state.silence_duration_ms >= self._max_silence_ms:
            return TurnDecision.FORCE_COMMIT
        if state.silence_duration_ms >= self._commit_silence_ms:
            return TurnDecision.COMMIT
        return TurnDecision.WAIT
