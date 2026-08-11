from fastapi import APIRouter

from astera_live_transcriber import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "astera-live-transcriber",
        "version": __version__,
    }

