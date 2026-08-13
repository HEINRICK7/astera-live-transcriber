from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from astera_live_transcriber.application.ports.transcription_engine import TranscriptionEnginePort
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.noop.adapter import NoopTranscriptionEngine
from astera_live_transcriber.infrastructure.engines.parakeet.adapter import (
    ParakeetTranscriptionEngine,
)
from astera_live_transcriber.infrastructure.engines.parakeet.config import ParakeetConfig
from astera_live_transcriber.infrastructure.engines.parakeet.loader import ParakeetModelLoader
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.speech.cloud.xai import (
    XaiConfig,
    XaiStreamingSpeechEngine,
)
from astera_live_transcriber.infrastructure.speech.registry import SpeechProviderRegistry


class EngineStatus(StrEnum):
    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


@dataclass
class EngineRuntime:
    settings: Settings
    engine: TranscriptionEnginePort
    loader: ParakeetModelLoader | None = None
    engine_factory: Callable[[PipelineMetrics | None], TranscriptionEnginePort] | None = None
    status: EngineStatus = EngineStatus.NOT_LOADED
    error: str | None = None

    async def start(self) -> None:
        if self.status is EngineStatus.READY:
            return
        self.status = EngineStatus.LOADING
        try:
            if self.loader is not None:
                self.loader.load()
            self.status = EngineStatus.READY
            self.error = None
        except Exception as exc:
            self.status = EngineStatus.FAILED
            self.error = str(exc)
            raise

    async def close(self) -> None:
        close = getattr(self.engine, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    def create_session_engine(
        self, metrics: PipelineMetrics | None = None
    ) -> TranscriptionEnginePort:
        if self.engine_factory is not None:
            return self.engine_factory(metrics)
        return self.engine

    def public_status(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "engine": (
                self.settings.stt_provider
                if self.settings.stt_provider != "local"
                else self.settings.engine
            ),
            "model": self.settings.default_model,
            "status": self.status.value,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def build_engine_runtime(settings: Settings) -> EngineRuntime:
    provider = settings.stt_provider.strip().lower()
    if provider == "xai":
        if not settings.xai_api_key:
            raise ValueError("ASTERA_TRANSCRIBER_XAI_API_KEY is required when STT provider is xai")
        xai_config = XaiConfig(
            api_key=settings.xai_api_key,
            endpoint=settings.xai_endpoint,
            connect_timeout_ms=settings.stt_connect_timeout_ms,
            audio_queue_size=settings.stt_audio_queue_size,
            queue_high_water_mark=settings.stt_queue_high_water_mark,
            provider_stall_timeout_ms=settings.stt_provider_stall_timeout_ms,
            max_reconnect_attempts=settings.stt_max_reconnect_attempts,
            reconnect_buffer_ms=settings.stt_reconnect_buffer_ms,
            endpointing_ms=settings.xai_endpointing_ms,
            filler_words=settings.xai_filler_words,
            vad_threshold=settings.xai_vad_threshold,
            smart_turn=settings.xai_smart_turn,
            smart_turn_timeout_ms=settings.xai_smart_turn_timeout_ms,
            debug_trace=settings.stt_debug_trace,
        )

        def engine_factory(metrics: PipelineMetrics | None = None) -> TranscriptionEnginePort:
            return XaiStreamingSpeechEngine(xai_config, metrics)

        registry = SpeechProviderRegistry()
        registry.register("xai", lambda: engine_factory(None))
        return EngineRuntime(
            settings=settings,
            engine=registry.create("xai"),
            engine_factory=engine_factory,
        )
    engine_name = settings.engine.strip().lower()
    if engine_name == "noop":
        return EngineRuntime(settings=settings, engine=NoopTranscriptionEngine())
    if engine_name != "parakeet":
        raise ValueError(f"unsupported transcription engine: {settings.engine}")
    config = ParakeetConfig(
        model_path=Path(settings.model_path),
        sample_rate=settings.audio_sample_rate,
        num_threads=settings.engine_threads,
        provider=settings.engine_provider,
        debug=settings.engine_debug,
        warmup=settings.engine_warmup,
        max_concurrent_inferences=settings.max_concurrent_inferences,
    )
    loader = ParakeetModelLoader(config)
    return EngineRuntime(
        settings=settings,
        engine=ParakeetTranscriptionEngine(loader, config),
        loader=loader,
    )
