from contextlib import asynccontextmanager

from fastapi import FastAPI

from astera_live_transcriber import __version__
from astera_live_transcriber.infrastructure.config.settings import Settings, get_settings
from astera_live_transcriber.infrastructure.engines.runtime import (
    EngineRuntime,
    build_engine_runtime,
)
from astera_live_transcriber.presentation.api.routes import engine, health, models, transcriptions
from astera_live_transcriber.presentation.api.websocket import realtime


def create_app(settings: Settings | None = None, runtime: EngineRuntime | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine_runtime = runtime or build_engine_runtime(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.engine_runtime = engine_runtime
        await engine_runtime.start()
        yield
        await engine_runtime.close()

    app = FastAPI(
        title="Astera Live Transcriber",
        version=__version__,
        description="Standalone structured transcription service for Astera.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.engine_runtime = engine_runtime
    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(transcriptions.router)
    app.include_router(engine.router)
    app.include_router(realtime.router)
    return app
