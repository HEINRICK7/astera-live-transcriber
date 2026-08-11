import asyncio
import struct
import time
from collections.abc import AsyncIterator

from astera_live_transcriber.application.ports.transcription_engine import (
    StreamingConfig,
    TranscriptionContext,
)
from astera_live_transcriber.domain.transcription import TranscriptionResult
from astera_live_transcriber.domain.transcription.events import TranscriptEvent, TranscriptEventType
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics

from .config import ParakeetConfig
from .exceptions import ParakeetEngineError
from .loader import ParakeetModelLoader
from .mapper import map_result


class ParakeetTranscriptionEngine:
    """Async application port backed by a resident sherpa-onnx recognizer."""

    def __init__(
        self,
        loader: ParakeetModelLoader,
        config: ParakeetConfig,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self._loader = loader
        self._config = config
        self._metrics = metrics or PipelineMetrics()
        self._inference_slots = asyncio.Semaphore(config.max_concurrent_inferences)

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        context: TranscriptionContext | None = None,
    ) -> TranscriptionResult:
        del context
        if not audio or len(audio) % 2:
            raise ParakeetEngineError("audio must be non-empty PCM16 little-endian")
        duration_ms = round(len(audio) / 2 / self._config.sample_rate * 1000)
        started = time.perf_counter()
        await self._inference_slots.acquire()
        self._metrics.set_gauge("stt_active_inferences", 1)
        try:
            raw_result = await asyncio.to_thread(self._decode, audio)
        except Exception as exc:
            self._metrics.increment("stt_failures_total")
            raise ParakeetEngineError(f"Parakeet inference failed: {exc}") from exc
        finally:
            self._inference_slots.release()
            self._metrics.set_gauge("stt_active_inferences", 0)
        elapsed = time.perf_counter() - started
        audio_seconds = duration_ms / 1000
        self._metrics.increment("stt_inference_total")
        self._metrics.observe("stt_inference_seconds", elapsed)
        self._metrics.observe("stt_audio_seconds", audio_seconds)
        if audio_seconds > 0:
            self._metrics.observe("stt_realtime_factor", elapsed / audio_seconds)
        result = map_result(raw_result, language, duration_ms)
        if not result.text:
            self._metrics.increment("stt_empty_results_total")
        return result

    def _decode(self, audio: bytes) -> object:
        import numpy as np

        recognizer = self._loader.recognizer
        samples = struct.unpack(f"<{len(audio) // 2}h", audio)
        waveform = np.asarray(samples, dtype=np.float32) / 32768.0
        stream = recognizer.create_stream()
        stream.accept_waveform(self._config.sample_rate, waveform)
        recognizer.decode_stream(stream)
        return stream.result

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        config: StreamingConfig,
    ) -> AsyncIterator[TranscriptEvent]:
        del config
        async for chunk in audio:
            result = await self.transcribe(chunk)
            if result.text:
                yield TranscriptEvent(
                    type=TranscriptEventType.PARTIAL,
                    session_id="stream",
                    text=result.text,
                    language=result.language,
                    confidence=result.confidence,
                )
