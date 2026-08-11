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

    def public_status(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "engine": self.settings.engine,
            "model": self.settings.default_model,
            "status": self.status.value,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def build_engine_runtime(settings: Settings) -> EngineRuntime:
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
