from fastapi import FastAPI

from astera_live_transcriber import __version__
from astera_live_transcriber.presentation.api.routes import health, models, transcriptions


def create_app() -> FastAPI:
    app = FastAPI(
        title="Astera Live Transcriber",
        version=__version__,
        description="Standalone structured transcription service for Astera.",
    )
    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(transcriptions.router)
    return app

