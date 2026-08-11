from fastapi import APIRouter, Depends

from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.presentation.api.dependencies import get_app_settings

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
async def models(settings: Settings = Depends(get_app_settings)) -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {"id": settings.default_model},
            {"id": "astera-stt-realtime-1"},
        ],
    }

