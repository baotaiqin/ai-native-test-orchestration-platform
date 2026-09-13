from fastapi import APIRouter, Response, status
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.core.config import get_settings
from app.modules.system.schemas import SystemHealth, SystemReadiness
from app.modules.system.service import get_database_health

router = APIRouter()


@router.get("/health", response_model=SystemHealth, summary="API 健康检查")
async def health() -> SystemHealth:
    settings = get_settings()
    return SystemHealth(
        status="ok",
        service="backend",
        version=__version__,
        environment=settings.env,
    )


@router.get("/readiness", response_model=SystemReadiness, summary="应用与数据库就绪检查")
async def readiness(response: Response) -> SystemReadiness:
    database = await run_in_threadpool(get_database_health)
    ready = database.status == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return SystemReadiness(status="ready" if ready else "not_ready", database=database)
