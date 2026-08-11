import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from astera_live_transcriber.application.services.audio_pipeline import AudioPipeline
from astera_live_transcriber.domain.audio import (
    AudioSourceMetadata,
    AudioSourceType,
    AudioStreamMode,
)
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription.events import (
    TranscriptEvent,
    TranscriptEventType,
)
from astera_live_transcriber.infrastructure.audio.file_source import (
    FileAudioSource,
    FileAudioSourceError,
)
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.runtime import EngineRuntime
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics

SUPPORTED_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
}
SUPPORTED_SUFFIXES = {".mp3", ".wav"}


class FileSessionError(ValueError):
    pass


@dataclass(slots=True)
class FileSession:
    session: TranscriptionSession
    path: Path
    source: FileAudioSource
    pipeline: AudioPipeline
    queue: asyncio.Queue[TranscriptEvent | None]
    task: asyncio.Task[None] | None = None
    expiry_task: asyncio.Task[None] | None = None
    attached: bool = False
    completed: bool = False


class FileSessionManager:
    """Owns file-source sessions and starts them only after WebSocket attach."""

    def __init__(
        self,
        settings: Settings,
        runtime: EngineRuntime,
        pipeline_factory: Callable[..., AudioPipeline],
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._pipeline_factory = pipeline_factory
        self._sessions: dict[str, FileSession] = {}

    async def create(
        self,
        path: Path,
        filename: str,
        mime_type: str | None,
        size_bytes: int,
        model: str,
        language: str,
        mode: AudioStreamMode,
    ) -> FileSession:
        validate_file_metadata(filename, mime_type)
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        metrics = PipelineMetrics()
        metadata = AudioSourceMetadata(
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            source_type=AudioSourceType.FILE,
            stream_mode=mode,
        )
        session = TranscriptionSession(
            id=session_id,
            model=model,
            language=language,
            source_metadata=metadata,
        )
        source = FileAudioSource(
            path=path,
            sample_rate=self._settings.audio_sample_rate,
            chunk_ms=self._settings.audio_chunk_ms,
            mode=mode,
            metrics=metrics,
        )
        pipeline = self._pipeline_factory(session, self._settings, self._runtime, metrics)
        file_session = FileSession(
            session=session,
            path=path,
            source=source,
            pipeline=pipeline,
            queue=asyncio.Queue(maxsize=self._settings.file_event_queue_size),
        )
        self._sessions[session_id] = file_session
        file_session.expiry_task = asyncio.create_task(self._expire(session_id))
        return file_session

    async def attach(self, session_id: str) -> FileSession:
        file_session = self._sessions.get(session_id)
        if file_session is None:
            raise FileSessionError("file session not found")
        if file_session.attached:
            raise FileSessionError("file session already attached")
        file_session.attached = True
        await self._cancel_expiry(file_session)
        file_session.task = asyncio.create_task(self._run(file_session))
        return file_session

    async def cancel(self, session_id: str) -> None:
        file_session = self._sessions.get(session_id)
        if file_session is None:
            return
        if file_session.task is not None and not file_session.task.done():
            file_session.task.cancel()
            await asyncio.gather(file_session.task, return_exceptions=True)
        else:
            await self._cancel_expiry(file_session)
            await self._dispose(file_session)
            self._sessions.pop(session_id, None)

    async def close(self) -> None:
        for session_id in tuple(self._sessions):
            await self.cancel(session_id)

    async def _run(self, file_session: FileSession) -> None:
        started = time.perf_counter()
        first_partial_recorded = False
        cancelled = False
        try:
            await self._put(
                file_session,
                TranscriptEvent(
                    type=TranscriptEventType.AUDIO_STARTED,
                    session_id=file_session.session.id,
                    timestamp_ms=0,
                ),
            )
            async for chunk in file_session.source.stream():
                events = await file_session.pipeline.process(chunk)
                for event in events:
                    if not first_partial_recorded and event.type in (
                        TranscriptEventType.PARTIAL,
                        TranscriptEventType.REVISED,
                    ):
                        first_partial_ms = (time.perf_counter() - started) * 1000
                        file_session.pipeline.metrics.observe(
                            "time_to_first_partial_ms", first_partial_ms
                        )
                        file_session.pipeline.metrics.observe(
                            "audio_to_first_partial_ms", first_partial_ms
                        )
                        first_partial_recorded = True
                    await self._put(file_session, event)
            for event in await file_session.pipeline.flush():
                await self._put(file_session, event)
            await self._put(
                file_session,
                TranscriptEvent(
                    type=TranscriptEventType.AUDIO_COMPLETED,
                    session_id=file_session.session.id,
                    timestamp_ms=file_session.session.last_audio_timestamp_ms,
                ),
            )
            await self._put(
                file_session,
                TranscriptEvent(
                    type=TranscriptEventType.SESSION_COMPLETED,
                    session_id=file_session.session.id,
                    timestamp_ms=file_session.session.last_audio_timestamp_ms,
                ),
            )
            file_session.completed = True
        except asyncio.CancelledError:
            cancelled = True
            raise
        except FileAudioSourceError:
            await self._put(
                file_session,
                TranscriptEvent(
                    type=TranscriptEventType.ERROR,
                    session_id=file_session.session.id,
                    error_code="audio_decode_error",
                ),
            )
        except Exception:
            await self._put(
                file_session,
                TranscriptEvent(
                    type=TranscriptEventType.ERROR,
                    session_id=file_session.session.id,
                    error_code="audio_stream_error",
                ),
            )
        finally:
            await file_session.pipeline.close()
            await file_session.source.close()
            await self._dispose(file_session)
            self._sessions.pop(file_session.session.id, None)
            if not cancelled:
                await self._put(file_session, None)

    async def _expire(self, session_id: str) -> None:
        await asyncio.sleep(self._settings.file_attach_timeout_seconds)
        file_session = self._sessions.get(session_id)
        if file_session is not None and not file_session.attached:
            await self._dispose(file_session)
            self._sessions.pop(session_id, None)

    @staticmethod
    async def _cancel_expiry(file_session: FileSession) -> None:
        if file_session.expiry_task is not None and not file_session.expiry_task.done():
            file_session.expiry_task.cancel()
            await asyncio.gather(file_session.expiry_task, return_exceptions=True)
        file_session.expiry_task = None

    async def _put(self, file_session: FileSession, event: TranscriptEvent | None) -> None:
        started = time.perf_counter()
        await file_session.queue.put(event)
        waited = time.perf_counter() - started
        if waited > 0.001:
            file_session.pipeline.metrics.observe("audio_source_backpressure_seconds", waited)

    @staticmethod
    async def _dispose(file_session: FileSession) -> None:
        file_session.path.unlink(missing_ok=True)


def validate_file_metadata(filename: str, mime_type: str | None) -> None:
    suffix = Path(filename).suffix.lower()
    normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if (
        normalized_mime
        and normalized_mime not in SUPPORTED_MIME_TYPES
        and normalized_mime != "application/octet-stream"
    ):
        raise FileSessionError("unsupported audio MIME type")
    if suffix not in SUPPORTED_SUFFIXES and normalized_mime not in SUPPORTED_MIME_TYPES:
        raise FileSessionError("only MP3 and WAV files are supported")
