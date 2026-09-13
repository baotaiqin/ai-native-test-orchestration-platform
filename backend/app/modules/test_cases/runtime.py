import random
import uuid
from copy import deepcopy
from typing import Any

import pymysql
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.environments.resolver import resolve_template
from app.modules.projects.service import get_project
from app.modules.resource_registry.security import validate_sql_query
from app.modules.scenarios.safe_script import run_safe_script
from app.modules.secrets.service import resolve_secret
from app.modules.test_cases.assertions import (
    _case_insensitive_get,
    _jsonpath_get,
    run_assertion,
    summarize_final_status,
)
from app.modules.test_cases.schemas import (
    Action,
    ApiSetupAction,
    ExtractorSource,
    ExtractResponseAction,
    FakerAction,
    FakerGenerator,
    GetTokenAction,
    PythonScriptAction,
    RegisterResourceAction,
    ResponseExtractor,
    RuntimeActionTrace,
    RuntimePreviewRequest,
    RuntimePreviewResponse,
    SetVariableAction,
    SqlQueryAction,
    VariableAction,
    action_type,
)

_SQL_BANNED_PATTERNS = (
    r"\bINTO\s+OUTFILE\b",
    r"\bINTO\s+DUMPFILE\b",
    r"\bLOAD_FILE\b",
    r"\bSLEEP\b",
    r"\bBENCHMARK\b",
    r"\bGET_LOCK\b",
    r"\bRELEASE_LOCK\b",
)
_SQL_COMMENT_MARKERS = ("--", "#", "/*", "*/")
_FAKER_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
_FAKER_FIRST_NAMES = ("Alice", "Bob", "Carol", "David", "Eve", "Frank")
_FAKER_LAST_NAMES = ("Smith", "Jones", "Taylor", "Brown", "Miller", "Wilson")


def resolve_runtime_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_runtime_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_runtime_value(item, context) for item in value]
    return resolve_template(value, context)


def extract_response_value(
    extractor: ResponseExtractor,
    json_body: Any,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> Any:
    try:
        if extractor.source == ExtractorSource.JSONPATH:
            return _jsonpath_get(json_body, extractor.expression)
        if extractor.source == ExtractorSource.HEADER:
            return _case_insensitive_get(headers, extractor.expression)
        return _case_insensitive_get(cookies, extractor.expression)
    except KeyError as exc:
        if extractor.required:
            raise ResourceConflictError(
                f"必填提取器 {extractor.name} 未匹配：{extractor.expression}"
            ) from exc
        return deepcopy(extractor.default_value)


def render_api_request(request: Any, context: dict[str, Any]) -> dict[str, Any]:
    request_data = request.model_dump(mode="json")
    for collection in ("query_params", "headers", "cookies"):
        request_data[collection] = [
            item for item in request_data[collection] if item.get("enabled", True)
        ]
    return resolve_runtime_value(request_data, context)


def _faker_value(action: FakerAction) -> Any:
    generator = random.Random(action.seed) if action.seed is not None else random.SystemRandom()
    if action.generator == FakerGenerator.UUID:
        return str(uuid.UUID(int=generator.getrandbits(128), version=4))
    if action.generator == FakerGenerator.EMAIL:
        return f"{generator.choice(_FAKER_WORDS)}{generator.randint(100, 999)}@example.test"
    if action.generator == FakerGenerator.USERNAME:
        return f"{generator.choice(_FAKER_WORDS)}_{generator.randint(100, 999)}"
    if action.generator == FakerGenerator.FIRST_NAME:
        return generator.choice(_FAKER_FIRST_NAMES)
    if action.generator == FakerGenerator.LAST_NAME:
        return generator.choice(_FAKER_LAST_NAMES)
    if action.generator == FakerGenerator.INTEGER:
        return generator.randint(100000, 999999)
    if action.generator == FakerGenerator.WORD:
        return generator.choice(_FAKER_WORDS)
    return bool(generator.randint(0, 1))


def _validated_sql(statement: str) -> str:
    try:
        return validate_sql_query(statement)
    except ResourceConflictError as exc:
        # Keep the established public error wording for existing SQL Query UI.
        if "危险函数" not in exc.message and "文件、系统" in exc.message:
            raise ResourceConflictError("SQL_QUERY 包含禁止的危险函数") from exc
        raise


def _run_sql_query(
    action: SqlQueryAction,
    context: dict[str, Any],
    session: Session | None,
    user: CurrentUser | None,
    project_id: int | None,
) -> dict[str, Any]:
    if session is None or user is None or project_id is None:
        raise ResourceConflictError("SQL_QUERY 预览缺少项目访问上下文")
    get_project(session, user, project_id)
    config = session.get(DatabaseConnection, action.connection_id)
    if config is None or not config.enabled:
        raise ResourceConflictError("SQL_QUERY 引用的数据库连接不存在或已停用")
    if config.project_id != project_id:
        raise ResourceConflictError("SQL_QUERY 数据库连接与执行项目不匹配")
    statement = _validated_sql(action.sql)
    parameters = resolve_runtime_value(action.params, context)
    password = resolve_secret(session, config.password_secret_id, config.project_id)
    connection = None
    try:
        connection = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=password,
            database=config.database_name,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            ssl={} if config.ssl_enabled else None,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            rows = list(cursor.fetchmany(action.max_rows + 1))
        truncated = len(rows) > action.max_rows
        rows = rows[: action.max_rows]
        context[action.result_variable] = rows
        return {
            "result_variable": action.result_variable,
            "row_count": len(rows),
            "truncated": truncated,
            "preview_rolled_back": True,
        }
    except pymysql.MySQLError as exc:
        raise ResourceConflictError("SQL_QUERY 执行失败，请检查语句和参数") from exc
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def _as_extractor(
    action: GetTokenAction | ApiSetupAction | ExtractResponseAction,
) -> ResponseExtractor:
    return ResponseExtractor(
        name=action.name,
        source=action.source,
        expression=action.expression,
        required=action.required,
        default_value=action.default_value,
        enabled=action.enabled,
    )


def _apply_action(
    action: Action,
    context: dict[str, Any],
    payload: RuntimePreviewRequest,
    session: Session | None,
    user: CurrentUser | None,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(action, VariableAction | SetVariableAction):
        context[action.name] = resolve_runtime_value(action.value, context)
        return {"variable": action.name}
    if isinstance(action, FakerAction):
        context[action.name] = _faker_value(action)
        return {"variable": action.name, "generator": action.generator.value, "seed": action.seed}
    if isinstance(action, SqlQueryAction):
        return _run_sql_query(action, context, session, user, payload.project_id)
    if isinstance(action, PythonScriptAction):
        run_safe_script(action.script, context)
        return {"restricted": True, "max_steps": 500}
    if isinstance(action, GetTokenAction | ApiSetupAction | ExtractResponseAction):
        snapshot = (
            action.response
            if isinstance(action, GetTokenAction | ApiSetupAction)
            else payload.response
        )
        value = extract_response_value(
            _as_extractor(action), snapshot.json_body, snapshot.headers, snapshot.cookies
        )
        context[action.name] = value
        extracted[action.name] = value
        detail = {"variable": action.name, "source": action.source.value}
        if isinstance(action, GetTokenAction | ApiSetupAction):
            detail["simulated"] = True
        return detail
    if isinstance(action, RegisterResourceAction):
        resources = context.setdefault("__resources", {})
        if not isinstance(resources, dict):
            raise ResourceConflictError("Runtime Context 的 __resources 必须是对象")
        resource_snapshot: dict[str, Any] = {
            "resource_type": action.resource_type,
            "value": resolve_runtime_value(action.value, context),
            "metadata": resolve_runtime_value(action.metadata, context),
            "preview_only": True,
        }
        if action.cleanup is not None:
            resource_snapshot["cleanup"] = action.cleanup.model_dump(mode="json")
        if action.cleanup_ref is not None:
            resource_snapshot["cleanup_ref"] = action.cleanup_ref
        resources[action.name] = resource_snapshot
        return {
            "resource": action.name,
            "resource_type": action.resource_type,
            "cleanup_type": action.cleanup.cleanup_type.value if action.cleanup else None,
            "cleanup_policy": action.cleanup.policy.value if action.cleanup else None,
            "cleanup_ref": action.cleanup_ref,
            "preview_only": True,
            "persisted": False,
        }
    raise ResourceConflictError("不支持的 Runtime Action")


def preview_runtime(
    payload: RuntimePreviewRequest,
    session: Session | None = None,
    user: CurrentUser | None = None,
) -> RuntimePreviewResponse:
    context = deepcopy(payload.context)
    extracted: dict[str, Any] = {}
    traces: list[RuntimeActionTrace] = []

    def run_action(action: Action, phase: str) -> None:
        sequence = len(traces) + 1
        current_type = action_type(action).value
        if not action.enabled:
            traces.append(
                RuntimeActionTrace(
                    sequence=sequence,
                    phase=phase,
                    action_type=current_type,
                    status="SKIPPED",
                    detail={"reason": "disabled"},
                )
            )
            return
        try:
            detail = _apply_action(action, context, payload, session, user, extracted)
        except ResourceConflictError:
            traces.append(
                RuntimeActionTrace(
                    sequence=sequence,
                    phase=phase,
                    action_type=current_type,
                    status="FAILED",
                    detail={"error": "action execution failed"},
                )
            )
            raise
        traces.append(
            RuntimeActionTrace(
                sequence=sequence,
                phase=phase,
                action_type=current_type,
                status="PASSED",
                detail=detail,
            )
        )

    for action in payload.pre_actions:
        run_action(action, "PRE")
    rendered_request = render_api_request(payload.request, context)
    traces.append(
        RuntimeActionTrace(
            sequence=len(traces) + 1,
            phase="REQUEST",
            action_type="RENDERED_REQUEST",
            status="PASSED",
            detail={
                "method": rendered_request.get("method"),
                "url": rendered_request.get("url"),
            },
        )
    )
    context["response.status_code"] = payload.response.status_code
    if payload.response.response_time_ms is not None:
        context["response.response_time_ms"] = payload.response.response_time_ms
        context["response.elapsed_ms"] = payload.response.elapsed_ms
    for extractor in payload.extractors:
        run_action(
            ExtractResponseAction(
                name=extractor.name,
                source=extractor.source,
                expression=extractor.expression,
                required=extractor.required,
                default_value=extractor.default_value,
                enabled=extractor.enabled,
            ),
            "POST",
        )
    for action in payload.post_actions:
        run_action(action, "POST")
    assertion_results = [
        run_assertion(
            assertion,
            sequence,
            payload.response,
            project_id=payload.project_id,
            session=session,
            user=user,
        )
        for sequence, assertion in enumerate(payload.assertions, start=1)
    ]
    return RuntimePreviewResponse(
        rendered_request=rendered_request,
        context=context,
        extracted=extracted,
        traces=traces,
        assertion_results=assertion_results,
        final_status=summarize_final_status(assertion_results),
    )
