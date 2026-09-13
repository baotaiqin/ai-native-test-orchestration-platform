import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pymysql
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.core.time import utc_now_naive
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.resource_registry.models import ResourceRegistryEntry
from app.modules.resource_registry.schemas import (
    CleanupConfig,
    CleanupExecuteResponse,
    CleanupOutcome,
    CleanupPlanItem,
    CleanupPlanResponse,
    CleanupPolicy,
    CleanupResultItem,
    CleanupStatus,
    CleanupType,
    ResourceListResponse,
    ResourceRegisterRequest,
    ResourceResponse,
    cleanup_policy_matches,
)
from app.modules.secrets.models import Secret
from app.modules.secrets.service import resolve_secret


class CleanupExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CleanupExecutionResult:
    status: CleanupStatus
    message: str | None = None
    error_code: str | None = None


CleanupHandler = Callable[[ResourceRegistryEntry], CleanupExecutionResult]


def _safe_error(message: str, *, maximum: int = 500) -> str:
    sanitized = re.sub(
        r"(?i)(authorization|cookie|password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        str(message),
    )
    sanitized = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer <redacted>", sanitized)
    return sanitized[:maximum]


def _config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return an intentionally small, secret-free view for API responses."""

    summary: dict[str, Any] = {
        "cleanup_id": config.get("cleanup_id"),
        "cleanup_type": config.get("cleanup_type"),
        "policy": config.get("policy", CleanupPolicy.ALWAYS.value),
        "enabled": config.get("enabled", True),
        "timeout_ms": config.get("timeout_ms", 30000),
    }
    if config.get("cleanup_type") == CleanupType.API.value:
        summary.update(
            {
                "method": config.get("method"),
                "url": _safe_error(str(config.get("url", "")), maximum=400),
                "query_names": [item.get("name") for item in config.get("query_params", [])],
                "header_names": [item.get("name") for item in config.get("headers", [])],
                "cookie_names": [item.get("name") for item in config.get("cookies", [])],
                "body_type": (config.get("body") or {}).get("type", "NONE"),
                "auth_type": (config.get("auth") or {}).get("type", "NONE"),
                "has_credential_reference": bool(
                    (config.get("auth") or {}).get("credential_ref")
                    or (config.get("auth") or {}).get("secret_id")
                ),
            }
        )
    else:
        statement = str(config.get("sql", ""))
        statement_preview = re.sub(
            r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "<redacted>", statement
        )
        summary.update(
            {
                "connection_id": config.get("connection_id"),
                "sql_preview": _safe_error(statement_preview, maximum=300),
                "parameter_names": list(config.get("params", {}).keys())
                if isinstance(config.get("params"), dict)
                else [],
                "resource_id_param": config.get("resource_id_param"),
            }
        )
    return summary


def _resource_response(entry: ResourceRegistryEntry) -> ResourceResponse:
    return ResourceResponse(
        id=entry.id,
        project_id=entry.project_id,
        run_id=entry.run_id,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        cleanup_type=CleanupType(entry.cleanup_type),
        cleanup_config=_config_summary(entry.cleanup_config),
        status=CleanupStatus(entry.status),
        registration_sequence=entry.registration_sequence,
        attempt_count=entry.attempt_count,
        last_error=entry.last_error,
        error_code=entry.error_code,
        source=entry.source,
        created_at=entry.created_at,
        cleaned_at=entry.cleaned_at,
        updated_at=entry.updated_at,
    )


def validate_cleanup_scope(
    session: Session, project_id: int, cleanup: CleanupConfig
) -> None:
    if cleanup.cleanup_type == CleanupType.SQL:
        connection = session.get(DatabaseConnection, cleanup.connection_id)
        if connection is None or connection.project_id != project_id or not connection.enabled:
            raise ResourceConflictError("SQL Cleanup 数据库连接不存在或不属于当前项目")
        password_secret = session.get(Secret, connection.password_secret_id)
        if (
            password_secret is None
            or password_secret.project_id != project_id
            or not password_secret.enabled
        ):
                raise ResourceConflictError(
                    "SQL Cleanup 数据库连接的 Secret 不存在、停用或不属于当前项目"
                )
    else:
        auth_secret_id = cleanup.auth.secret_id
        if auth_secret_id is not None:
            secret = session.get(Secret, auth_secret_id)
            if secret is None or secret.project_id != project_id or not secret.enabled:
                raise ResourceConflictError(
                    "API Cleanup 引用的 Secret 不存在、停用或不属于当前项目"
                )


def _next_sequence(session: Session, project_id: int, run_id: str) -> int:
    value = session.scalar(
        select(func.max(ResourceRegistryEntry.registration_sequence))
        .where(
            ResourceRegistryEntry.project_id == project_id,
            ResourceRegistryEntry.run_id == run_id,
        )
        .with_for_update()
    )
    return int(value or 0) + 1


def register_resource(
    session: Session, user: CurrentUser, payload: ResourceRegisterRequest
) -> ResourceResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能登记资源")
    validate_cleanup_scope(session, payload.project_id, payload.cleanup)
    snapshot = payload.cleanup.model_dump(mode="json")
    # The indexed MAX(...).FOR UPDATE serializes existing run rows on MySQL;
    # the unique key is the race-safe fallback when two transactions enter an
    # empty run at once.  Deadlock/duplicate retries make this safe without a
    # premature runs table or an application-only counter.
    for _ in range(5):
        try:
            entry = ResourceRegistryEntry(
                project_id=payload.project_id,
                run_id=payload.run_id,
                resource_type=payload.resource_type,
                resource_id=payload.resource_id,
                cleanup_type=payload.cleanup.cleanup_type.value,
                cleanup_config=snapshot,
                status=CleanupStatus.PENDING.value,
                registration_sequence=_next_sequence(
                    session, payload.project_id, payload.run_id
                ),
                source=payload.source,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return _resource_response(entry)
        except IntegrityError as exc:
            session.rollback()
            if _is_sequence_conflict(exc):
                continue
            raise ResourceConflictError("资源登记失败") from exc
        except OperationalError as exc:
            session.rollback()
            if _is_retryable_lock_error(exc):
                continue
            raise ResourceConflictError("资源登记失败") from exc
    raise ResourceConflictError("资源登记顺序冲突，请重试")


def _is_sequence_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "registration_sequence" in message or "project_run_sequence" in message


def _is_retryable_lock_error(exc: OperationalError) -> bool:
    message = str(exc.orig).lower()
    return "deadlock" in message or "lock wait timeout" in message


def _get_entry(session: Session, resource_id: int) -> ResourceRegistryEntry:
    entry = session.get(ResourceRegistryEntry, resource_id)
    if entry is None:
        raise ResourceNotFoundError("Resource Registry 记录不存在")
    return entry


def _ensure_entry_access(
    session: Session, user: CurrentUser, entry: ResourceRegistryEntry, *, writable: bool = False
) -> None:
    project = get_project(session, user, entry.project_id)
    if writable:
        ensure_project_writable(session, project, user)


def list_resources(
    session: Session,
    user: CurrentUser,
    project_id: int,
    run_id: str,
    status: CleanupStatus | None = None,
) -> ResourceListResponse:
    get_project(session, user, project_id)
    statement = select(ResourceRegistryEntry).where(
        ResourceRegistryEntry.project_id == project_id,
        ResourceRegistryEntry.run_id == run_id,
    )
    if status is not None:
        statement = statement.where(ResourceRegistryEntry.status == status.value)
    entries = list(
        session.scalars(
            statement.order_by(ResourceRegistryEntry.registration_sequence.desc())
        ).all()
    )
    return ResourceListResponse(
        items=[_resource_response(item) for item in entries], total=len(entries)
    )


def get_resource(
    session: Session, user: CurrentUser, project_id: int, resource_id: int
) -> ResourceResponse:
    entry = _get_entry(session, resource_id)
    if entry.project_id != project_id:
        raise ResourceNotFoundError("Resource Registry 记录不存在")
    _ensure_entry_access(session, user, entry)
    return _resource_response(entry)


def _policy_matches(policy: CleanupPolicy, outcome: CleanupOutcome) -> bool:
    return cleanup_policy_matches(policy, outcome)


def plan_cleanup(
    session: Session,
    user: CurrentUser,
    project_id: int,
    run_id: str,
    outcome: CleanupOutcome,
    *,
    retry_failed: bool = False,
) -> CleanupPlanResponse:
    get_project(session, user, project_id)
    entries = list(
        session.scalars(
            select(ResourceRegistryEntry)
            .where(
                ResourceRegistryEntry.project_id == project_id,
                ResourceRegistryEntry.run_id == run_id,
            )
            .order_by(ResourceRegistryEntry.registration_sequence.desc())
        ).all()
    )
    items: list[CleanupPlanItem] = []
    for entry in entries:
        config = CleanupConfig.model_validate(entry.cleanup_config)
        action = "CLEAN"
        reason = "LIFO eligible"
        if entry.status == CleanupStatus.CLEANED.value:
            action, reason = "SKIP", "already cleaned"
        elif entry.status == CleanupStatus.CLEANING.value:
            action, reason = "SKIP", "cleanup already in progress"
        elif entry.status == CleanupStatus.SKIPPED.value:
            action, reason = "SKIP", "already skipped"
        elif not config.enabled:
            action, reason = "SKIP", "cleanup disabled"
        elif config.policy == CleanupPolicy.NEVER:
            action, reason = "SKIP", "policy NEVER"
        elif not _policy_matches(config.policy, outcome):
            action, reason = "SKIP", f"policy {config.policy.value} does not match {outcome.value}"
        elif entry.status == CleanupStatus.FAILED.value and not retry_failed:
            action, reason = "SKIP", "FAILED requires explicit retry_failed"
        elif entry.status not in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}:
            action, reason = "SKIP", f"status {entry.status} is not eligible"
        items.append(
            CleanupPlanItem(resource=_resource_response(entry), action=action, reason=reason)
        )
    return CleanupPlanResponse(
        project_id=project_id, run_id=run_id, outcome=outcome, items=items
    )


def _mark_skipped(
    session: Session, entry_id: int, reason: str, *, error_code: str = "CLEANUP_SKIPPED"
) -> ResourceRegistryEntry:
    entry = session.scalar(
        select(ResourceRegistryEntry)
        .where(ResourceRegistryEntry.id == entry_id)
        .with_for_update()
    )
    if entry is None:
        raise ResourceNotFoundError("Resource Registry 记录不存在")
    if entry.status in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}:
        entry.status = CleanupStatus.SKIPPED.value
        entry.error_code = error_code
        entry.last_error = _safe_error(reason)
        entry.updated_at = utc_now_naive()
        session.commit()
        session.refresh(entry)
    return entry


def _claim_cleanup(session: Session, entry_id: int) -> ResourceRegistryEntry | None:
    entry = session.scalar(
        select(ResourceRegistryEntry)
        .where(ResourceRegistryEntry.id == entry_id)
        .with_for_update()
    )
    if entry is None or entry.status not in {
        CleanupStatus.PENDING.value,
        CleanupStatus.FAILED.value,
    }:
        return None
    entry.status = CleanupStatus.CLEANING.value
    entry.attempt_count += 1
    entry.last_error = None
    entry.error_code = None
    entry.updated_at = utc_now_naive()
    session.commit()
    session.refresh(entry)
    return entry


def mark_cleanup_result(
    session: Session,
    user: CurrentUser,
    resource_id: int,
    result: CleanupExecutionResult,
) -> ResourceResponse:
    entry = _get_entry(session, resource_id)
    _ensure_entry_access(session, user, entry, writable=True)
    if entry.status == CleanupStatus.CLEANED.value:
        return _resource_response(entry)
    if entry.status != CleanupStatus.CLEANING.value:
        raise ResourceConflictError("只有 CLEANING 状态的资源可以记录 Cleanup 结果")
    if result.status not in {CleanupStatus.CLEANED, CleanupStatus.FAILED, CleanupStatus.SKIPPED}:
        raise ResourceConflictError("Cleanup 结果状态非法")
    entry.status = result.status.value
    entry.error_code = result.error_code
    entry.last_error = (
        _safe_error(result.message) if result.status != CleanupStatus.CLEANED else None
    )
    entry.cleaned_at = utc_now_naive() if result.status == CleanupStatus.CLEANED else None
    entry.updated_at = utc_now_naive()
    session.commit()
    session.refresh(entry)
    return _resource_response(entry)


def _default_api_handler(_: ResourceRegistryEntry) -> CleanupExecutionResult:
    return CleanupExecutionResult(
        status=CleanupStatus.SKIPPED,
        error_code="API_HANDLER_NOT_CONFIGURED",
        message="API Cleanup 已完成配置校验与执行计划，真实网络 handler 待 Phase 4 Runner 接入",
    )


def _call_handler(handler: CleanupHandler, entry: ResourceRegistryEntry) -> CleanupExecutionResult:
    try:
        result = handler(entry)
    except CleanupExecutionError:
        raise
    except Exception as exc:  # Handlers are an external execution boundary.
        raise CleanupExecutionError("CLEANUP_HANDLER_ERROR", "Cleanup handler 执行失败") from exc
    if not isinstance(result, CleanupExecutionResult):
        raise CleanupExecutionError("CLEANUP_HANDLER_ERROR", "Cleanup handler 返回结果无效")
    return result


def _execute_sql_cleanup(
    session: Session, entry: ResourceRegistryEntry
) -> CleanupExecutionResult:
    config = CleanupConfig.model_validate(entry.cleanup_config)
    from app.modules.resource_registry.security import validate_sql_cleanup
    from app.modules.test_cases.runtime import resolve_runtime_value

    statement = validate_sql_cleanup(config.sql or "")
    context = {"resource_id": entry.resource_id}
    parameters = resolve_runtime_value(config.params, context)
    if config.resource_id_param:
        if not isinstance(parameters, dict):
            raise CleanupExecutionError(
                "SQL_PARAMS_INVALID", "SQL Cleanup resource_id_param 需要对象参数"
            )
        parameters[config.resource_id_param] = entry.resource_id
    connection_config = session.get(DatabaseConnection, config.connection_id)
    if (
        connection_config is None
        or connection_config.project_id != entry.project_id
        or not connection_config.enabled
    ):
        raise CleanupExecutionError(
            "CONNECTION_SCOPE_ERROR", "SQL Cleanup 数据库连接不存在、停用或项目不匹配"
        )
    password = resolve_secret(session, connection_config.password_secret_id, entry.project_id)
    connection = None
    try:
        connection = pymysql.connect(
            host=connection_config.host,
            port=connection_config.port,
            user=connection_config.username,
            password=password,
            database=connection_config.database_name,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            ssl={} if connection_config.ssl_enabled else None,
            autocommit=False,
        )
        with connection.cursor() as cursor:
            affected = cursor.execute(statement, parameters)
        connection.commit()
        return CleanupExecutionResult(
            status=CleanupStatus.CLEANED,
            message=f"SQL Cleanup 已执行，影响 {int(affected)} 行",
        )
    except pymysql.MySQLError as exc:
        if connection is not None:
            connection.rollback()
        raise CleanupExecutionError(
            "SQL_CLEANUP_ERROR", "SQL Cleanup 执行失败，请检查参数和连接权限"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def execute_cleanup(
    session: Session,
    user: CurrentUser,
    project_id: int,
    run_id: str,
    outcome: CleanupOutcome,
    *,
    retry_failed: bool = False,
    handler: CleanupHandler | None = None,
) -> CleanupExecuteResponse:
    ensure_project_writable(session, get_project(session, user, project_id), user)
    plan = plan_cleanup(
        session,
        user,
        project_id,
        run_id,
        outcome,
        retry_failed=retry_failed,
    )
    results: list[CleanupResultItem] = []
    for item in plan.items:
        entry_id = item.resource.id
        if item.action == "SKIP":
            if item.reason in {
                "already cleaned",
                "cleanup already in progress",
                "already skipped",
            } or item.reason.startswith("FAILED requires explicit retry_failed"):
                entry = _get_entry(session, entry_id)
            else:
                entry = _mark_skipped(session, entry_id, item.reason)
            results.append(
                CleanupResultItem(
                    resource=_resource_response(entry),
                    status=CleanupStatus(entry.status),
                    error_code=entry.error_code,
                    message=entry.last_error,
                )
            )
            continue
        entry = _claim_cleanup(session, entry_id)
        if entry is None:
            current = _get_entry(session, entry_id)
            results.append(
                CleanupResultItem(
                    resource=_resource_response(current),
                    status=CleanupStatus(current.status),
                    error_code=current.error_code,
                    message=current.last_error,
                )
            )
            continue
        try:
            if handler is not None:
                result = _call_handler(handler, entry)
            elif entry.cleanup_type == CleanupType.SQL.value:
                result = _execute_sql_cleanup(session, entry)
            else:
                result = _default_api_handler(entry)
        except CleanupExecutionError as exc:
            result = CleanupExecutionResult(
                status=CleanupStatus.FAILED,
                error_code=exc.code,
                message=exc.message,
            )
        except Exception as exc:  # Keep one resource failure from stopping LIFO.
            result = CleanupExecutionResult(
                status=CleanupStatus.FAILED,
                error_code="CLEANUP_ERROR",
                message=f"Cleanup 失败：{type(exc).__name__}",
            )
        updated = mark_cleanup_result(session, user, entry.id, result)
        results.append(
            CleanupResultItem(
                resource=updated,
                status=CleanupStatus(updated.status),
                error_code=updated.error_code,
                message=updated.last_error,
            )
        )
    return CleanupExecuteResponse(
        project_id=project_id,
        run_id=run_id,
        outcome=outcome,
        items=results,
    )
