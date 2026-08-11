from collections.abc import AsyncIterator
from typing import Protocol

from astera_live_transcriber.domain.audio import AudioChunk


class AudioSourcePort(Protocol):
    async def stream(self) -> AsyncIterator[AudioChunk]:
        """Yield canonical audio chunks incrementally."""
