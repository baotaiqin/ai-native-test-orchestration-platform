from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.secret_cipher import validate_secret_provider_configuration
from app.modules.ci_cd.coordinator import build_webhook_coordinator
from app.modules.runs.disconnect_coordinator import build_disconnect_coordinator
from app.modules.schedules.coordinator import build_schedule_coordinator

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    validate_secret_provider_configuration(settings)
    coordinator_factory = application.dependency_overrides.get(
        build_disconnect_coordinator, build_disconnect_coordinator
    )
    coordinator = coordinator_factory()
    schedule_coordinator_factory = application.dependency_overrides.get(
        build_schedule_coordinator, build_schedule_coordinator
    )
    schedule_coordinator = schedule_coordinator_factory()
    webhook_coordinator_factory = application.dependency_overrides.get(
        build_webhook_coordinator, build_webhook_coordinator
    )
    webhook_coordinator = webhook_coordinator_factory()
    coordinator.start()
    schedule_coordinator.start()
    webhook_coordinator.start()
    logger.info("application_started", extra={"event": "APPLICATION_STARTED"})
    try:
        yield
    finally:
        webhook_coordinator.stop()
        schedule_coordinator.stop()
        coordinator.stop()
        logger.info("application_stopped", extra={"event": "APPLICATION_STOPPED"})


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")

    if settings.metrics_enabled:

        @application.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            return Response(
                content=generate_latest(),
                headers={"Content-Type": CONTENT_TYPE_LATEST},
            )

    @application.get("/health", tags=["system"], summary="进程健康检查")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "backend", "version": __version__}

    return application


app = create_app()
