from typing import Protocol

from astera_live_transcriber.domain.session import TurnDecision, TurnState


class TurnDetectionPort(Protocol):
    async def evaluate(self, state: TurnState) -> TurnDecision:
        """Decide whether the current turn should remain open or commit."""

