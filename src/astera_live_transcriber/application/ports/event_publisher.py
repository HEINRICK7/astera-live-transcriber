from typing import Protocol

from astera_live_transcriber.domain.transcription.events import TranscriptEvent


class EventPublisherPort(Protocol):
    async def publish(self, event: TranscriptEvent) -> None:
        """Publish a realtime transcript event."""

