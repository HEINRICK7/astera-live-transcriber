from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1/engine", tags=["engine"])


@router.get("/status")
async def engine_status(request: Request) -> dict[str, object]:
    return request.app.state.engine_runtime.public_status()
