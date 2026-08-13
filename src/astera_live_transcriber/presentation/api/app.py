from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from astera_live_transcriber import __version__
from astera_live_transcriber.application.transcription_intelligence.memory import (
    InMemoryTranscriptionMemory,
)
from astera_live_transcriber.domain.transcription_intelligence import RetentionPolicy
from astera_live_transcriber.infrastructure.audio.file_session_manager import FileSessionManager
from astera_live_transcriber.infrastructure.config.settings import Settings, get_settings
from astera_live_transcriber.infrastructure.engines.runtime import (
    EngineRuntime,
    build_engine_runtime,
)
from astera_live_transcriber.presentation.api.dependencies import create_realtime_pipeline
from astera_live_transcriber.presentation.api.routes import (
    engine,
    files,
    health,
    models,
    transcriptions,
)
from astera_live_transcriber.presentation.api.websocket import realtime

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None, runtime: EngineRuntime | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine_runtime = runtime or build_engine_runtime(app_settings)
    intelligence_memory = (
        InMemoryTranscriptionMemory(
            policy=RetentionPolicy(app_settings.intelligence_memory_policy),
            decay_half_life_days=app_settings.intelligence_decay_half_life_days,
        )
        if app_settings.intelligence_enabled
        else None
    )
    file_sessions = FileSessionManager(
        app_settings,
        engine_runtime,
        create_realtime_pipeline,
        intelligence_memory=intelligence_memory,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.engine_runtime = engine_runtime
        app.state.file_sessions = file_sessions
        app.state.transcription_memory = intelligence_memory
        await engine_runtime.start()
        yield
        await file_sessions.close()
        await engine_runtime.close()

    app = FastAPI(
        title="Astera Live Transcriber",
        version=__version__,
        description="Standalone structured transcription service for Astera.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.engine_runtime = engine_runtime
    app.state.file_sessions = file_sessions
    app.state.transcription_memory = intelligence_memory
    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(transcriptions.router)
    app.include_router(files.router)
    app.include_router(engine.router)
    app.include_router(realtime.router)

    @app.get("/", include_in_schema=False)
    async def frontend() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
