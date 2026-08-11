import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from astera_live_transcriber.domain.audio import AudioChunk, AudioStreamMode
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics


class FileAudioSourceError(RuntimeError):
    """A file could not be decoded into canonical audio."""


ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


class FileAudioSource:
    """Decode a compressed or WAV file through FFmpeg without buffering PCM."""

    def __init__(
        self,
        path: Path,
        sample_rate: int = 16_000,
        chunk_ms: int = 100,
        mode: AudioStreamMode = AudioStreamMode.REALTIME,
        metrics: PipelineMetrics | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        if sample_rate <= 0 or chunk_ms <= 0:
            raise ValueError("sample_rate and chunk_ms must be positive")
        self.path = path
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.mode = AudioStreamMode(mode)
        self.metrics = metrics or PipelineMetrics()
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._process: asyncio.subprocess.Process | None = None

    async def stream(self) -> AsyncIterator[AudioChunk]:
        started = time.perf_counter()
        audio_ms = 0
        first_chunk = True
        process: asyncio.subprocess.Process | None = None
        self.metrics.increment("audio_source_started_total")
        try:
            process = await self._start_process()
            self._process = process
            bytes_per_chunk = self.sample_rate * 2 * self.chunk_ms // 1000
            sequence = 0
            while True:
                if self.mode is AudioStreamMode.REALTIME and not first_chunk:
                    await self._pace(started, audio_ms)
                block = await _read_exact_or_eof(process.stdout, bytes_per_chunk)
                if not block:
                    break
                if len(block) % 2:
                    raise FileAudioSourceError("FFmpeg returned an incomplete PCM16 sample")
                duration_ms = max(1, round(len(block) / 2 / self.sample_rate * 1000))
                chunk = AudioChunk(
                    data=block,
                    sequence=sequence,
                    timestamp_ms=audio_ms,
                    duration_ms=duration_ms,
                    sample_rate=self.sample_rate,
                    channels=1,
                )
                sequence += 1
                audio_ms += duration_ms
                first_chunk = False
                self.metrics.increment("audio_chunks_emitted_total")
                yield chunk
            return_code = await process.wait()
            stderr = await process.stderr.read() if process.stderr is not None else b""
            if return_code != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise FileAudioSourceError(detail or "FFmpeg could not decode the audio file")
            self.metrics.increment("audio_source_completed_total")
        except asyncio.CancelledError:
            raise
        except FileAudioSourceError:
            self.metrics.increment("audio_source_failed_total")
            raise
        except Exception as exc:
            self.metrics.increment("audio_source_failed_total")
            raise FileAudioSourceError(str(exc)) from exc
        finally:
            if process is not None and process.returncode is None:
                await self._terminate(process)
            self._process = None
            self.metrics.observe("file_decode_seconds", time.perf_counter() - started)
            self.metrics.observe("stream_audio_time_seconds", audio_ms / 1000)
            self.metrics.observe("stream_wall_time_seconds", time.perf_counter() - started)

    async def close(self) -> None:
        if self._process is not None:
            await self._terminate(self._process)

    async def _start_process(self) -> asyncio.subprocess.Process:
        try:
            return await self._process_factory(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(self.path),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(self.sample_rate),
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise FileAudioSourceError("ffmpeg is not installed") from exc

    async def _pace(self, started: float, audio_ms: int) -> None:
        target = started + audio_ms / 1000
        delay = target - time.perf_counter()
        if delay <= 0:
            return
        wait_started = time.perf_counter()
        await asyncio.sleep(delay)
        self.metrics.observe(
            "stream_pacing_sleep_seconds",
            time.perf_counter() - wait_started,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()


async def _read_exact_or_eof(reader: asyncio.StreamReader | None, size: int) -> bytes:
    if reader is None:
        raise FileAudioSourceError("FFmpeg stdout is unavailable")
    blocks: list[bytes] = []
    remaining = size
    while remaining:
        block = await reader.read(remaining)
        if not block:
            break
        blocks.append(block)
        remaining -= len(block)
    return b"".join(blocks)
