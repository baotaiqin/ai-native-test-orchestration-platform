import time
from functools import lru_cache
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

from app.core.logging import get_logger
from app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS, HTTP_REQUESTS_IN_PROGRESS

logger = get_logger(__name__)


@lru_cache(maxsize=8)
def _metric_routes(application: Any) -> tuple[Any, ...]:
    candidates = []
    for route in application.routes:
        effective_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_contexts):
            candidates.extend(effective_contexts())
        else:
            candidates.append(route)
    return tuple(candidates)


def _route_template(request: Request) -> str:
    partial: str | None = None
    for route in _metric_routes(request.app):
        match, _ = route.matches(request.scope)
        route_path = getattr(route, "path", None)
        if match is Match.FULL and route_path:
            return str(route_path)
        if match is Match.PARTIAL and route_path:
            partial = str(route_path)
    return partial or "unmatched"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        request.state.request_id = request_id
        started_at = time.perf_counter()
        method = request.method.upper()
        status_code = 500
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_seconds = time.perf_counter() - started_at
            route_path = _route_template(request)
            HTTP_REQUESTS.labels(
                method=method,
                route=route_path,
                status_code=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION.labels(method=method, route=route_path).observe(duration_seconds)
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
            logger.info(
                "http_request_completed",
                extra={
                    "event": "HTTP_REQUEST_COMPLETED",
                    "request_id": request_id,
                    "method": method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
