import re
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class AuthenticationError(AppError):
    def __init__(self, message: str = "认证失败") -> None:
        super().__init__("AUTH_ERROR", message, status_code=status.HTTP_401_UNAUTHORIZED)


class AuthorizationError(AppError):
    def __init__(self, message: str = "权限不足") -> None:
        super().__init__("PERMISSION_DENIED", message, status_code=status.HTTP_403_FORBIDDEN)


class ResourceNotFoundError(AppError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__("RESOURCE_NOT_FOUND", message, status_code=status.HTTP_404_NOT_FOUND)


class ResourceConflictError(AppError):
    def __init__(self, message: str = "资源冲突") -> None:
        super().__init__("RESOURCE_CONFLICT", message, status_code=status.HTTP_409_CONFLICT)


class InvalidDocumentError(AppError):
    def __init__(self, message: str = "文档格式无效", *, details: Any = None) -> None:
        super().__init__(
            "INVALID_DOCUMENT",
            message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class SecurityConfigurationError(AppError):
    def __init__(self, message: str = "安全组件配置不可用") -> None:
        super().__init__(
            "SECURITY_CONFIGURATION_ERROR",
            message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class RedisUnavailableError(AppError):
    def __init__(self, message: str = "Redis 服务暂不可用，请稍后重试") -> None:
        super().__init__(
            "REDIS_UNAVAILABLE", message, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )


class RunValidationError(AppError):
    def __init__(self, details: Any) -> None:
        super().__init__(
            "RUN_VALIDATION_FAILED",
            "Run 执行前校验失败",
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class RunStateConflictError(AppError):
    def __init__(self, message: str = "Run 状态更新不允许") -> None:
        super().__init__(
            "RUN_STATE_CONFLICT",
            message,
            status_code=status.HTTP_409_CONFLICT,
        )


class RunDispatchPendingError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "RUN_DISPATCH_PENDING",
            "Run 任务发布确认尚未完成，请稍后重试",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class RunExecutionUnsupportedError(AppError):
    def __init__(self, message: str = "当前 API_CASE 执行计划不受支持") -> None:
        super().__init__(
            "RUN_EXECUTION_UNSUPPORTED",
            message,
            status_code=status.HTTP_409_CONFLICT,
        )


class EvidenceStorageError(AppError):
    def __init__(self, message: str = "Evidence 存储暂不可用，请稍后重试") -> None:
        super().__init__(
            "EVIDENCE_STORAGE_UNAVAILABLE",
            message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def error_body(code: str, message: str, request_id: str, details: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "request_id": request_id,
        "details": details,
    }


_SENSITIVE_VALIDATION_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "registration_token",
        "access_token",
        "secret",
        "credential",
        "authorization",
        "cookie",
        "api_key",
        "storage_state",
        "dom_context",
        "locator",
    }
)
_SENSITIVE_VALIDATION_KEY_MARKERS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "authorization",
    "cookie",
    "api_key",
    "storage",
    "dom",
    "locator",
)
_SENSITIVE_VALIDATION_CONTEXT_KEYS = frozenset(
    {"events", "locator_candidates"}
)
_SENSITIVE_VALIDATION_CONTEXT_FIELDS = frozenset({"value", "key"})
_SENSITIVE_VALIDATION_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(?:password|passwd|token|registration_token|access_token|secret|credential|"
        r"authorization|cookie|api_key)\b\s*[:=]\s*[^\s,;]+"
    ),
)
_MAX_VALIDATION_DEPTH = 6
_MAX_VALIDATION_ITEMS = 32
_MAX_VALIDATION_STRING_LENGTH = 256
_MAX_VALIDATION_KEY_LENGTH = 128
_REDACTED_VALIDATION_VALUE = "<redacted>"
_TRUNCATED_VALIDATION_VALUE = "<truncated>"


def _normalized_validation_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _is_sensitive_validation_key(value: Any) -> bool:
    normalized = _normalized_validation_key(value)
    return normalized in _SENSITIVE_VALIDATION_KEYS or any(
        marker in normalized for marker in _SENSITIVE_VALIDATION_KEY_MARKERS
    )


def _redact_validation_text(value: str) -> str:
    redacted = value
    for pattern in _SENSITIVE_VALIDATION_TEXT_PATTERNS:
        redacted = pattern.sub(_REDACTED_VALIDATION_VALUE, redacted)
    if len(redacted) > _MAX_VALIDATION_STRING_LENGTH:
        return redacted[:_MAX_VALIDATION_STRING_LENGTH] + "…"
    return redacted


def _sanitize_validation_value(
    value: Any,
    *,
    sensitive: bool = False,
    sensitive_context: bool = False,
    depth: int = 0,
) -> Any:
    if sensitive:
        return _REDACTED_VALIDATION_VALUE
    if depth >= _MAX_VALIDATION_DEPTH:
        return _TRUNCATED_VALIDATION_VALUE
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_VALIDATION_ITEMS:
                sanitized["<truncated_items>"] = _TRUNCATED_VALIDATION_VALUE
                break
            safe_key = str(key)[:_MAX_VALIDATION_KEY_LENGTH]
            normalized_key = _normalized_validation_key(key)
            sanitized[safe_key] = _sanitize_validation_value(
                item,
                sensitive=(
                    _is_sensitive_validation_key(key)
                    or (
                        sensitive_context
                        and normalized_key in _SENSITIVE_VALIDATION_CONTEXT_FIELDS
                    )
                ),
                sensitive_context=(
                    sensitive_context
                    or normalized_key in _SENSITIVE_VALIDATION_CONTEXT_KEYS
                ),
                depth=depth + 1,
            )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _sanitize_validation_value(
                item, sensitive_context=sensitive_context, depth=depth + 1
            )
            for item in list(value)[:_MAX_VALIDATION_ITEMS]
        ]
    if isinstance(value, str):
        return _redact_validation_text(value)
    if isinstance(value, bytes):
        return "<binary>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_validation_text(str(value))


def _sanitize_validation_loc(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [
        part if isinstance(part, int) else str(part)[:_MAX_VALIDATION_KEY_LENGTH]
        for part in list(value)[:_MAX_VALIDATION_ITEMS]
    ]


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized_errors: list[dict[str, Any]] = []
    for error in errors[:_MAX_VALIDATION_ITEMS]:
        loc = _sanitize_validation_loc(error.get("loc", []))
        sanitized_error: dict[str, Any] = {
            "loc": loc,
            "type": _redact_validation_text(str(error.get("type", ""))),
            "msg": _redact_validation_text(str(error.get("msg", ""))),
        }
        if "input" in error:
            sanitized_error["input"] = _sanitize_validation_value(
                error["input"],
                sensitive=any(_is_sensitive_validation_key(part) for part in loc),
                sensitive_context=any(
                    _normalized_validation_key(part) in _SENSITIVE_VALIDATION_CONTEXT_KEYS
                    for part in loc
                ),
            )
        sanitized_errors.append(sanitized_error)
    if len(errors) > _MAX_VALIDATION_ITEMS:
        sanitized_errors.append(
            {
                "loc": [],
                "type": "validation_errors_truncated",
                "msg": _TRUNCATED_VALIDATION_VALUE,
            }
        )
    return sanitized_errors


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, request_id, exc.details),
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body(
                "VALIDATION_ERROR",
                "请求参数校验失败",
                request_id,
                _sanitize_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unknown_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception(
            "unhandled_exception",
            extra={"event": "UNHANDLED_EXCEPTION", "request_id": request_id},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("INTERNAL_ERROR", "服务内部错误", request_id),
        )
