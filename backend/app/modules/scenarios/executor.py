import json
import re
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pymysql
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.projects.service import get_project
from app.modules.resource_registry.schemas import (
    CleanupConfig,
    CleanupType,
    cleanup_policy_matches,
    is_sensitive_name,
    validate_secret_free_payload,
)
from app.modules.resource_registry.security import (
    validate_sql_cleanup,
    validate_sql_execute,
    validate_sql_query,
)
from app.modules.resource_registry.service import validate_cleanup_scope
from app.modules.scenarios.safe_script import (
    SafeScriptBudgetExceeded,
    run_safe_script,
    validate_safe_script,
)
from app.modules.scenarios.schemas import (
    FailurePolicy,
    ScenarioExecutionPreviewRequest,
    ScenarioExecutionPreviewResponse,
    ScenarioNode,
    ScenarioNodeTrace,
    ScenarioNodeType,
    ScenarioPreviewMode,
)
from app.modules.scenarios.validator import validate_scenario_dsl
from app.modules.secrets.service import resolve_secret
from app.modules.test_cases.assertions import run_assertion, summarize_final_status
from app.modules.test_cases.runtime import extract_response_value, resolve_runtime_value
from app.modules.test_cases.schemas import (
    Assertion,
    AssertionSource,
    ExtractorSource,
    ResponseExtractor,
)

_CONDITION = re.compile(r"^\s*(.+?)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*$")
_TEMPLATE_VARIABLE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_MAX_ERROR_MESSAGE = 500
_DEBUG_CONTROL_TYPES = {
    ScenarioNodeType.START,
    ScenarioNodeType.END,
    ScenarioNodeType.ELSE,
    ScenarioNodeType.IF,
    ScenarioNodeType.LOOP,
}
_CLEANUP_TYPES = {ScenarioNodeType.API_CLEANUP, ScenarioNodeType.SQL_CLEANUP}


class _PreviewRuntimeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        timeout: bool = False,
        review: bool = False,
        detail: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.code = code
        self.message = message[:_MAX_ERROR_MESSAGE]
        self.retryable = retryable
        self.timeout = timeout
        self.review = review
        self.detail = detail or {}
        self.duration_ms = duration_ms
        super().__init__(self.message)


def _bounded_message(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()[:_MAX_ERROR_MESSAGE]


def _contains_sensitive_template(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_name(str(key)) or _contains_sensitive_template(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_template(child) for child in value)
    return isinstance(value, str) and any(
        is_sensitive_name(match) for match in _TEMPLATE_VARIABLE.findall(value)
    )


def _safe_cleanup_preview(
    value: Any, name: str | None = None, template_source: Any = None
) -> Any:
    if name and is_sensitive_name(name):
        return "<redacted>"
    if template_source is not None and _contains_sensitive_template(template_source):
        return "<redacted>"
    if isinstance(value, dict):
        source = template_source if isinstance(template_source, dict) else {}
        return {
            str(key): _safe_cleanup_preview(
                item, str(key), source.get(key) if isinstance(source, dict) else None
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        source_items = template_source if isinstance(template_source, list) else []
        return [
            _safe_cleanup_preview(
                item, template_source=source_items[index] if index < len(source_items) else None
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        if any(is_sensitive_name(match) for match in _TEMPLATE_VARIABLE.findall(value)):
            return "<redacted>"
        try:
            validate_secret_free_payload(value)
        except ValueError:
            return "<redacted>"
        return value[:500]
    return value


def _parse_literal(value: str, context: dict[str, Any]) -> Any:
    resolved = resolve_runtime_value(value.strip(), context)
    if not isinstance(resolved, str) or resolved != value.strip():
        return resolved
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip().strip("'\"")


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == "contains":
        try:
            return right in left
        except TypeError as exc:
            raise ResourceConflictError("条件两侧类型不支持 contains") from exc
    try:
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
        if operator == "<":
            return left < right
        return left <= right
    except TypeError as exc:
        raise ResourceConflictError("条件两侧类型不支持比较") from exc


def evaluate_condition(condition: Any, context: dict[str, Any]) -> bool:
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, dict):
        left = resolve_runtime_value(condition.get("left"), context)
        right = resolve_runtime_value(condition.get("right"), context)
        return _compare(left, str(condition.get("operator", "==")), right)
    if not isinstance(condition, str):
        raise ResourceConflictError("IF 条件格式无效")
    match = _CONDITION.fullmatch(condition)
    if match is None:
        return bool(resolve_runtime_value(condition, context))
    left, operator, right = match.groups()
    return _compare(_parse_literal(left, context), operator, _parse_literal(right, context))


def _unresolved_templates(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.extend(_TEMPLATE_VARIABLE.findall(value))
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(_unresolved_templates(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_unresolved_templates(child))
    return sorted(set(found))


def _is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    text = str(exc).lower()
    code = exc.args[0] if getattr(exc, "args", None) else None
    return code in {2013, 3024} or any(
        token in text for token in ("timed out", "timeout", "read timeout", "write timeout")
    )


class _PreviewExecutor:
    def __init__(
        self,
        payload: ScenarioExecutionPreviewRequest,
        session: Session,
        user: CurrentUser,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.payload = payload
        self.session = session
        self.user = user
        self.dsl = payload.dsl
        self.context = deepcopy(payload.context)
        self.current_response = payload.response
        self.traces: list[ScenarioNodeTrace] = []
        self.assertion_results = []
        self.connections: dict[tuple[int, int], pymysql.Connection] = {}
        self.children: dict[str | None, list[ScenarioNode]] = {}
        self.nodes_by_id = {node.id: node for node in self.dsl.nodes}
        self.node_indexes = {node.id: index for index, node in enumerate(self.dsl.nodes)}
        self.clock = clock or time.monotonic
        self.stop_requested = False
        self.stop_reason: str | None = None
        self.stop_message: str | None = None
        self.target_reached = False
        self.review_detected = False
        self.deterministic_failure = False
        for node in self.dsl.nodes:
            self.children.setdefault(node.parent_id, []).append(node)

    def effective_failure_policy(self, node: ScenarioNode) -> FailurePolicy:
        # A scene-wide stop is a hard main-flow guard, not merely a legacy
        # fallback. RETRY_ONCE may still perform its one bounded retry, but an
        # exhausted retry stops afterwards. Cleanup is finalized separately.
        if node.failure_policy == FailurePolicy.RETRY_ONCE:
            return FailurePolicy.RETRY_ONCE
        if self.dsl.settings.stop_on_failure:
            return FailurePolicy.STOP
        return node.failure_policy or FailurePolicy.CONTINUE

    def _timeout_seconds(self, timeout_ms: int) -> float:
        # Preview never creates a worker thread to fake cancellation.  The
        # database driver's bounded timeout is capped and the monotonic check
        # below maps a returned-over-budget operation to TIMEOUT.
        return max(0.1, min(5.0, timeout_ms / 1000))

    def database_connection(self, node: ScenarioNode, connection_id: int) -> pymysql.Connection:
        key = (connection_id, node.timeout_ms)
        existing = self.connections.get(key)
        if existing is not None:
            return existing
        config = self.session.get(DatabaseConnection, connection_id)
        if config is None or not config.enabled:
            raise ResourceConflictError("SQL Node 引用的数据库连接不存在或已停用")
        if self.payload.project_id is None or config.project_id != self.payload.project_id:
            raise ResourceConflictError("SQL Node 数据库连接与执行项目不匹配")
        get_project(self.session, self.user, config.project_id)
        password = resolve_secret(self.session, config.password_secret_id, config.project_id)
        timeout = self._timeout_seconds(node.timeout_ms)
        connection = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=password,
            database=config.database_name,
            charset="utf8mb4",
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
            ssl={} if config.ssl_enabled else None,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.connections[key] = connection
        return connection

    def _resolved(self, value: Any, *, label: str = "Runtime Context") -> Any:
        try:
            resolved = resolve_runtime_value(value, self.context)
        except ResourceConflictError as exc:
            match = re.search(r"变量未定义：([A-Za-z_][A-Za-z0-9_.-]*)", str(exc))
            if match:
                raise _PreviewRuntimeError(
                    "MISSING_RUNTIME_CONTEXT",
                    f"{label} 缺少变量：{match.group(1)}",
                ) from exc
            raise
        missing = _unresolved_templates(resolved)
        if missing:
            names = ", ".join(missing[:5])
            raise _PreviewRuntimeError(
                "MISSING_RUNTIME_CONTEXT", f"{label} 缺少变量：{names}", retryable=False
            )
        return resolved

    def _discard_connection(self, node: ScenarioNode, connection_id: int) -> None:
        connection = self.connections.pop((connection_id, node.timeout_ms), None)
        if connection is None:
            return
        try:
            connection.rollback()
        finally:
            connection.close()

    def run_sql(self, node: ScenarioNode, cleanup: CleanupConfig | None = None) -> dict[str, Any]:
        config = cleanup.model_dump(mode="python") if cleanup is not None else node.config
        try:
            connection_id = int(config["connection_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceConflictError("SQL Node 缺少有效 connection_id") from exc
        raw_statement = config.get("sql")
        if not isinstance(raw_statement, str):
            raise ResourceConflictError("SQL Node 语句必须是字符串")
        if node.type == ScenarioNodeType.SQL_QUERY:
            statement = validate_sql_query(raw_statement)
        elif node.type == ScenarioNodeType.SQL_CLEANUP:
            statement = validate_sql_cleanup(raw_statement)
        else:
            statement = validate_sql_execute(raw_statement)
        parameters = self._resolved(config.get("params", {}), label="SQL params")
        if cleanup is not None and cleanup.resource_id_param:
            if not isinstance(parameters, dict):
                raise ResourceConflictError("SQL Cleanup resource_id_param 需要对象参数")
            parameters[cleanup.resource_id_param] = self.context.get("resource_id")
        connection = self.database_connection(node, connection_id)
        started = self.clock()
        try:
            with connection.cursor() as cursor:
                affected = cursor.execute(statement, parameters)
                if node.type == ScenarioNodeType.SQL_QUERY:
                    max_rows = min(max(int(config.get("max_rows", 100)), 1), 1000)
                    rows = list(cursor.fetchmany(max_rows + 1))
                    truncated = len(rows) > max_rows
                    rows = rows[:max_rows]
                else:
                    rows = []
            duration_ms = max(0, round((self.clock() - started) * 1000))
            if duration_ms > node.timeout_ms:
                raise _PreviewRuntimeError(
                    "SQL_TIMEOUT",
                    "SQL Node 超过节点 timeout_ms",
                    retryable=True,
                    timeout=True,
                    duration_ms=duration_ms,
                )
            if node.type == ScenarioNodeType.SQL_QUERY:
                result_name = str(config.get("result_variable", "sql_rows"))
                self.context[result_name] = rows
                return {
                    "row_count": len(rows),
                    "truncated": truncated,
                    "result_variable": result_name,
                    "preview_rolled_back": True,
                }
            result_name = str(config.get("result_variable", "affected_rows"))
            self.context[result_name] = affected
            return {
                "affected_rows": affected,
                "result_variable": result_name,
                "preview_rolled_back": True,
            }
        except _PreviewRuntimeError as exc:
            if exc.timeout:
                self._discard_connection(node, connection_id)
            raise
        except pymysql.MySQLError as exc:
            timed_out = _is_timeout_exception(exc)
            if timed_out:
                self._discard_connection(node, connection_id)
            raise _PreviewRuntimeError(
                "SQL_TIMEOUT" if timed_out else "SQL_EXECUTION_ERROR",
                "SQL Node 执行超时" if timed_out else "SQL Node 执行失败，请检查语句和参数",
                retryable=timed_out,
                timeout=timed_out,
            ) from exc

    def trace(
        self,
        node: ScenarioNode,
        status: str,
        iteration_path: list[int],
        detail: dict[str, Any] | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
        duration_ms: int = 0,
        error_code: str | None = None,
        message: str | None = None,
        terminal: bool = True,
        retryable: bool = False,
    ) -> None:
        self.traces.append(
            ScenarioNodeTrace(
                sequence=len(self.traces) + 1,
                node_id=node.id,
                node_type=node.type,
                status=status,
                iteration_path=list(iteration_path),
                detail=detail or {},
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=max(0, duration_ms),
                timeout_ms=node.timeout_ms,
                failure_policy=self.effective_failure_policy(node),
                error_code=error_code,
                message=_bounded_message(message, "") if message else None,
                terminal=terminal,
                retryable=retryable,
            )
        )

    def _safe_node_error(self, node: ScenarioNode, exc: BaseException) -> _PreviewRuntimeError:
        if isinstance(exc, _PreviewRuntimeError):
            return exc
        if isinstance(exc, SafeScriptBudgetExceeded):
            return _PreviewRuntimeError(
                "PYTHON_TIMEOUT",
                "Python Script 超过受限计算预算",
                timeout=True,
                retryable=False,
                detail={"max_steps": exc.max_steps, "steps": exc.steps},
            )
        if isinstance(exc, pymysql.MySQLError):
            timed_out = _is_timeout_exception(exc)
            return _PreviewRuntimeError(
                "SQL_TIMEOUT" if timed_out else "SQL_EXECUTION_ERROR",
                "SQL Node 执行超时" if timed_out else "SQL Node 执行失败，请检查语句和参数",
                timeout=timed_out,
                retryable=timed_out,
            )
        if isinstance(exc, ResourceConflictError) and node.type == ScenarioNodeType.EXTRACT:
            return _PreviewRuntimeError(
                "RESPONSE_VALUE_MISSING",
                _bounded_message(str(exc), "模拟响应未包含提取节点需要的值"),
            )
        if node.type == ScenarioNodeType.PYTHON_SCRIPT:
            return _PreviewRuntimeError(
                "PYTHON_RUNTIME_ERROR", "Python Script 执行失败，请检查受限脚本"
            )
        if node.type in {
            ScenarioNodeType.SQL_QUERY,
            ScenarioNodeType.SQL_EXECUTE,
            ScenarioNodeType.SQL_CLEANUP,
        }:
            return _PreviewRuntimeError("SQL_RUNTIME_ERROR", "SQL Node 执行失败")
        if node.type in {
            ScenarioNodeType.ASSERT_STATUS,
            ScenarioNodeType.ASSERT_JSONPATH,
            ScenarioNodeType.AI_ASSERTION,
        }:
            return _PreviewRuntimeError("ASSERTION_RUNTIME_ERROR", "断言节点执行失败")
        return _PreviewRuntimeError("NODE_RUNTIME_ERROR", "节点执行失败")

    def _mark_target(self, node: ScenarioNode, *, failed: bool = False) -> None:
        if self.payload.target_node_id != node.id:
            return
        self.target_reached = True
        if self.payload.mode == ScenarioPreviewMode.RUN_TO_HERE:
            self.stop_requested = True
            self.stop_reason = "DEBUG_TARGET_FAILED" if failed else "DEBUG_TARGET_REACHED"
            self.stop_message = (
                "目标节点已执行但失败，运行到此处预览已停止"
                if failed
                else "目标节点已执行，运行到此处预览已停止"
            )

    def _terminal_failure(
        self,
        node: ScenarioNode,
        iteration_path: list[int],
        error: _PreviewRuntimeError,
        *,
        attempt: int,
        max_attempts: int,
        duration_ms: int,
    ) -> None:
        self.trace(
            node,
            "TIMEOUT" if error.timeout else "FAILED",
            iteration_path,
            error.detail,
            attempt=attempt,
            max_attempts=max_attempts,
            duration_ms=error.duration_ms if error.duration_ms is not None else duration_ms,
            error_code=error.code,
            message=error.message,
            terminal=True,
            retryable=error.retryable,
        )
        if error.review:
            self.review_detected = True
        else:
            self.deterministic_failure = True
        self._mark_target(node, failed=True)
        if node.type in _CLEANUP_TYPES or self.payload.mode == ScenarioPreviewMode.RUN_TO_HERE:
            return
        policy = self.effective_failure_policy(node)
        if policy == FailurePolicy.CONTINUE:
            return
        self.stop_requested = True
        self.stop_reason = (
            "RETRY_EXHAUSTED"
            if policy == FailurePolicy.RETRY_ONCE
            else "FAILURE_POLICY_STOP"
        )
        self.stop_message = (
            "节点重试一次后仍失败，按 RETRY_ONCE 的终止规则停止后续节点"
            if policy == FailurePolicy.RETRY_ONCE
            else "节点失败，按失败策略停止后续可执行节点"
        )

    def _run_leaf(self, node: ScenarioNode, iteration_path: list[int]) -> None:
        policy = self.effective_failure_policy(node)
        max_attempts = 2 if policy == FailurePolicy.RETRY_ONCE else 1
        for attempt in range(1, max_attempts + 1):
            context_before = deepcopy(self.context)
            started = self.clock()
            try:
                detail = self._execute_node_once(node)
                duration_ms = max(0, round((self.clock() - started) * 1000))
            except Exception as exc:  # Convert every runtime fault into a safe trace.
                self.context = context_before
                duration_ms = max(0, round((self.clock() - started) * 1000))
                error = self._safe_node_error(node, exc)
                can_retry = (
                    policy == FailurePolicy.RETRY_ONCE
                    and attempt == 1
                    and error.retryable
                )
                if can_retry:
                    self.trace(
                        node,
                        "TIMEOUT" if error.timeout else "FAILED",
                        iteration_path,
                        error.detail,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        duration_ms=(
                            error.duration_ms
                            if error.duration_ms is not None
                            else duration_ms
                        ),
                        error_code=error.code,
                        message=error.message,
                        terminal=False,
                        retryable=error.retryable,
                    )
                    continue
                self._terminal_failure(
                    node,
                    iteration_path,
                    error,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    duration_ms=duration_ms,
                )
                return
            self.trace(
                node,
                "PASSED",
                iteration_path,
                detail,
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=(
                    int(detail.pop("_duration_ms"))
                    if "_duration_ms" in detail
                    else duration_ms
                ),
            )
            self._mark_target(node)
            return

    def _run_control_failure(
        self, node: ScenarioNode, iteration_path: list[int], exc: BaseException
    ) -> None:
        error = self._safe_node_error(node, exc)
        self._terminal_failure(
            node, iteration_path, error, attempt=1, max_attempts=1, duration_ms=0
        )

    def _skip_subtree(
        self, node: ScenarioNode, iteration_path: list[int], reason: str
    ) -> None:
        self.trace(node, "SKIPPED", iteration_path, {"reason": reason})
        for child in self.children.get(node.id, []):
            self._skip_subtree(child, iteration_path, reason)

    def run_children(
        self,
        parent_id: str | None,
        iteration_path: list[int],
        exclude_else: bool = False,
    ) -> None:
        for child in self.children.get(parent_id, []):
            if exclude_else and child.type == ScenarioNodeType.ELSE:
                self._skip_subtree(child, iteration_path, "else_branch_not_selected")
                continue
            if self.stop_requested:
                if (
                    child.type in _CLEANUP_TYPES
                    and self.payload.mode != ScenarioPreviewMode.RUN_TO_HERE
                ):
                    self.run_node(child, iteration_path)
                else:
                    self._skip_subtree(child, iteration_path, self.stop_reason or "stopped")
                continue
            self.run_node(child, iteration_path)

    def _run_if(self, node: ScenarioNode, iteration_path: list[int]) -> None:
        try:
            condition = self._resolved(node.config.get("condition"), label="IF 条件")
            matched = evaluate_condition(condition, self.context)
        except Exception as exc:
            self._run_control_failure(node, iteration_path, exc)
            return
        self.trace(node, "PASSED", iteration_path, {"condition_result": matched})
        if matched:
            self.run_children(node.id, iteration_path, exclude_else=True)
            return
        else_nodes = [
            item for item in self.children.get(node.id, []) if item.type == ScenarioNodeType.ELSE
        ]
        for child in self.children.get(node.id, []):
            if child.type != ScenarioNodeType.ELSE:
                self._skip_subtree(child, iteration_path, "if_branch_not_selected")
        for else_node in else_nodes:
            if self.stop_requested:
                self._skip_subtree(else_node, iteration_path, self.stop_reason or "stopped")
                continue
            self.run_node(else_node, iteration_path)

    def _run_loop(self, node: ScenarioNode, iteration_path: list[int]) -> None:
        try:
            items = node.config.get("items")
            if items is not None:
                values = self._resolved(items, label="LOOP items")
                if not isinstance(values, list):
                    raise ResourceConflictError("LOOP items 必须解析为数组")
            else:
                values = list(range(int(node.config["iterations"])))
        except Exception as exc:
            self._run_control_failure(node, iteration_path, exc)
            return
        self.trace(node, "PASSED", iteration_path, {"iterations": len(values)})
        item_name = str(node.config.get("item_variable", "loop_item"))
        index_name = str(node.config.get("index_variable", "loop_index"))
        for index, value in enumerate(values):
            if self.stop_requested:
                for child in self.children.get(node.id, []):
                    self._skip_subtree(
                        child,
                        [*iteration_path, index],
                        self.stop_reason or "stopped",
                    )
                continue
            self.context[item_name] = value
            self.context[index_name] = index
            self.run_children(node.id, [*iteration_path, index])

    def _render_cleanup_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.get("name"),
                "enabled": item.get("enabled", True),
                "value": _safe_cleanup_preview(
                    self._resolved(item.get("value", ""), label="Cleanup Runtime Context"),
                    str(item.get("name", "")),
                    item.get("value", ""),
                ),
            }
            for item in items
        ]

    def _cleanup_skip_detail(self, cleanup: CleanupConfig) -> dict[str, Any]:
        reason = "disabled"
        if cleanup.enabled and not cleanup_policy_matches(
            cleanup.policy, self.payload.cleanup_outcome
        ):
            reason = (
                f"policy {cleanup.policy.value} does not match "
                f"{self.payload.cleanup_outcome.value}"
            )
        return {
            "cleanup_type": cleanup.cleanup_type.value,
            "cleanup_action": "SKIP",
            "reason": reason,
            "cleanup_outcome": self.payload.cleanup_outcome.value,
            "simulated": True,
            "preview_rolled_back": cleanup.cleanup_type == CleanupType.SQL,
            "persisted": False,
        }

    def _execute_node_once(self, node: ScenarioNode) -> dict[str, Any]:
        if node.type == ScenarioNodeType.HTTP:
            if node.config.get("url") is not None:
                self._resolved(node.config.get("url"), label="HTTP URL")
            self.current_response = self.payload.responses_by_node.get(
                node.id, self.payload.response
            )
            return {
                "simulated": True,
                "response_fixture_node_id": node.id,
                "status_code": self.current_response.status_code,
                "execution_boundary": "no_network_preview",
            }
        if node.type == ScenarioNodeType.SET_VARIABLE:
            name = str(node.config["name"])
            self.context[name] = self._resolved(node.config.get("value"))
            return {"variable": name}
        if node.type == ScenarioNodeType.EXTRACT:
            if node.config.get("enabled", True) is False:
                raise _PreviewRuntimeError("EXTRACT_DISABLED", "提取器已停用")
            try:
                extractor = ResponseExtractor(
                    name=str(node.config["name"]),
                    source=ExtractorSource(str(node.config["source"])),
                    expression=str(node.config["expression"]),
                    required=bool(node.config.get("required", True)),
                    default_value=node.config.get("default_value"),
                    enabled=True,
                )
                value = extract_response_value(
                    extractor,
                    self.current_response.json_body,
                    self.current_response.headers,
                    self.current_response.cookies,
                )
            except KeyError as exc:
                raise _PreviewRuntimeError(
                    "RESPONSE_VALUE_MISSING", "Response Snapshot 缺少提取目标"
                ) from exc
            except (TypeError, ValueError) as exc:
                raise _PreviewRuntimeError(
                    "EXTRACT_CONFIG_ERROR", "EXTRACT 配置或 Response Snapshot 无效"
                ) from exc
            self.context[extractor.name] = value
            return {"variable": extractor.name, "source": extractor.source.value}
        if node.type == ScenarioNodeType.WAIT:
            duration_ms = int(node.config["duration_ms"])
            if duration_ms > node.timeout_ms:
                raise _PreviewRuntimeError(
                    "WAIT_TIMEOUT",
                    "WAIT duration_ms 超过节点 timeout_ms",
                    retryable=True,
                    timeout=True,
                    duration_ms=node.timeout_ms,
                    detail={"duration_ms": duration_ms, "simulated": True},
                )
            return {"duration_ms": duration_ms, "simulated": True, "_duration_ms": duration_ms}
        if node.type in {
            ScenarioNodeType.ASSERT_STATUS,
            ScenarioNodeType.ASSERT_JSONPATH,
            ScenarioNodeType.AI_ASSERTION,
        }:
            if node.type == ScenarioNodeType.ASSERT_STATUS:
                assertion = Assertion(
                    kind="DETERMINISTIC",
                    type="STATUS_CODE",
                    name=node.name,
                    enabled=True,
                    source=AssertionSource.STATUS_CODE,
                    operator="EQ",
                    expected=node.config.get("expected"),
                )
            elif node.type == ScenarioNodeType.ASSERT_JSONPATH:
                assertion = Assertion(
                    kind="DETERMINISTIC",
                    type="JSONPATH_EQUAL",
                    name=node.name,
                    enabled=True,
                    source=AssertionSource.JSON_BODY,
                    expression=str(node.config.get("expression", "")),
                    operator="EQ",
                    expected=node.config.get("expected"),
                )
            else:
                assertion = Assertion(
                    kind="AI_SEMANTIC",
                    type="AI_SEMANTIC",
                    name=node.name,
                    enabled=True,
                    prompt_id=node.config.get("prompt_id"),
                    criteria=node.config.get("criteria"),
                    confidence_threshold=node.config.get("confidence_threshold", 0.8),
                )
            result = run_assertion(
                assertion,
                len(self.assertion_results) + 1,
                self.current_response,
                project_id=self.payload.project_id,
                session=self.session,
                user=self.user,
            )
            self.assertion_results.append(result)
            detail = {"assertion_result": result.model_dump(mode="json")}
            if result.status.value == "FAIL":
                raise _PreviewRuntimeError(
                    "ASSERTION_FAILED",
                    _bounded_message(result.message, "断言失败"),
                    detail=detail,
                )
            if result.status.value == "REVIEW":
                raise _PreviewRuntimeError(
                    "ASSERTION_REVIEW",
                    _bounded_message(result.message, "断言需要人工复核"),
                    review=True,
                    detail=detail,
                )
            return detail
        if node.type in {ScenarioNodeType.SQL_QUERY, ScenarioNodeType.SQL_EXECUTE}:
            return self.run_sql(node)
        if node.type == ScenarioNodeType.SQL_CLEANUP:
            cleanup = node.cleanup_config(self.dsl.settings.cleanup_policy)
            detail = self.run_sql(node, cleanup)
            detail.update(
                {
                    "cleanup_type": CleanupType.SQL.value,
                    "cleanup_action": "CLEAN",
                    "policy": cleanup.policy.value,
                    "cleanup_outcome": self.payload.cleanup_outcome.value,
                    "simulated": True,
                    "persisted": False,
                }
            )
            return detail
        if node.type == ScenarioNodeType.PYTHON_SCRIPT:
            script = node.config.get("script")
            if not isinstance(script, str):
                raise ResourceConflictError("PYTHON_SCRIPT 缺少 script")
            max_steps = max(20, min(500, node.timeout_ms // 10))
            run_safe_script(script, self.context, max_steps=max_steps)
            return {"restricted": True, "max_steps": max_steps}
        if node.type == ScenarioNodeType.API_CLEANUP:
            cleanup = node.cleanup_config(self.dsl.settings.cleanup_policy)
            rendered = {
                "method": cleanup.method.value if cleanup.method else None,
                "url": _safe_cleanup_preview(
                    self._resolved(cleanup.url, label="Cleanup Runtime Context"),
                    "url",
                    cleanup.url,
                ),
                "query_params": self._render_cleanup_items(cleanup.query_params),
                "headers": self._render_cleanup_items(cleanup.headers),
                "cookies": self._render_cleanup_items(cleanup.cookies),
                "body": _safe_cleanup_preview(
                    self._resolved(
                        cleanup.body or {"type": "NONE"}, label="Cleanup Runtime Context"
                    ),
                    template_source=cleanup.body or {"type": "NONE"},
                ),
                "auth": {
                    "type": cleanup.auth.type,
                    "placement": cleanup.auth.placement,
                    "key_name": cleanup.auth.key_name,
                    "has_credential_reference": bool(
                        cleanup.auth.secret_id or cleanup.auth.credential_ref
                    ),
                },
            }
            return {
                "cleanup_type": CleanupType.API.value,
                "cleanup_action": "CLEAN",
                "policy": cleanup.policy.value,
                "cleanup_outcome": self.payload.cleanup_outcome.value,
                "execution_boundary": "render_validate_plan_only",
                "simulated": True,
                "persisted": False,
                "request": rendered,
            }
        return {}

    def run_node(self, node: ScenarioNode, iteration_path: list[int]) -> None:
        if not node.enabled:
            self.trace(node, "SKIPPED", iteration_path, {"reason": "disabled"})
            if node.type in {ScenarioNodeType.IF, ScenarioNodeType.LOOP}:
                for child in self.children.get(node.id, []):
                    self._skip_subtree(child, iteration_path, "parent_disabled")
            return
        if node.type == ScenarioNodeType.IF:
            self._run_if(node, iteration_path)
            return
        if node.type == ScenarioNodeType.LOOP:
            self._run_loop(node, iteration_path)
            return
        if node.type == ScenarioNodeType.ELSE:
            self.trace(node, "PASSED", iteration_path, {"branch": "else"})
            self.run_children(node.id, iteration_path)
            return
        if node.type in _CLEANUP_TYPES:
            cleanup = node.cleanup_config(self.dsl.settings.cleanup_policy)
            if not cleanup.enabled or not cleanup_policy_matches(
                cleanup.policy, self.payload.cleanup_outcome
            ):
                self.trace(node, "SKIPPED", iteration_path, self._cleanup_skip_detail(cleanup))
                return
        self._run_leaf(node, iteration_path)

    def _run_siblings_after(self, parent_id: str | None, index: int) -> None:
        parent = self.nodes_by_id.get(parent_id) if parent_id else None
        for child in self.children.get(parent_id, []):
            if self.node_indexes[child.id] <= index:
                continue
            if (
                parent is not None
                and parent.type == ScenarioNodeType.IF
                and child.type == ScenarioNodeType.ELSE
            ):
                self._skip_subtree(child, [], "else_branch_not_selected")
                continue
            if self.stop_requested:
                self._skip_subtree(child, [], self.stop_reason or "stopped")
                continue
            self.run_node(child, [])

    def _run_from_continuation(self, node: ScenarioNode) -> None:
        self._run_siblings_after(node.parent_id, self.node_indexes[node.id])
        if self.stop_requested:
            return
        parent = self.nodes_by_id.get(node.parent_id) if node.parent_id else None
        if parent is not None and parent.type in {ScenarioNodeType.IF, ScenarioNodeType.ELSE}:
            self._run_from_continuation(parent)

    def run(self) -> ScenarioExecutionPreviewResponse:
        try:
            return self._run()
        finally:
            for connection in self.connections.values():
                try:
                    connection.rollback()
                finally:
                    connection.close()

    def _run(self) -> ScenarioExecutionPreviewResponse:
        target = (
            self.nodes_by_id.get(self.payload.target_node_id)
            if self.payload.target_node_id
            else None
        )
        if target is not None and self.payload.mode in {
            ScenarioPreviewMode.NODE,
            ScenarioPreviewMode.RUN_FROM_HERE,
        }:
            target_index = self.node_indexes[target.id]
            prior_http = next(
                (
                    node
                    for node in reversed(self.dsl.nodes[:target_index])
                    if node.enabled and node.type == ScenarioNodeType.HTTP
                ),
                None,
            )
            if prior_http is not None:
                self.current_response = self.payload.responses_by_node.get(
                    prior_http.id, self.payload.response
                )
        if self.payload.mode in {ScenarioPreviewMode.FULL, ScenarioPreviewMode.RUN_TO_HERE}:
            self.run_children(None, [])
        elif self.payload.mode == ScenarioPreviewMode.NODE:
            assert target is not None
            self.run_node(target, [])
        else:
            assert target is not None
            self.run_node(target, [])
            if not self.stop_requested:
                self._run_from_continuation(target)

        if (
            self.payload.mode == ScenarioPreviewMode.RUN_TO_HERE
            and not self.target_reached
            and self.stop_reason is None
        ):
            self.stop_reason = "TARGET_UNREACHABLE"
            self.stop_message = "目标节点未在实际执行分支中命中"
            self.review_detected = True

        assertion_status = summarize_final_status(self.assertion_results)
        if self.deterministic_failure or assertion_status == "FAIL":
            final_status = "FAIL"
        elif self.review_detected or assertion_status == "REVIEW":
            final_status = "REVIEW"
        else:
            final_status = "PASS"
        return ScenarioExecutionPreviewResponse(
            status=final_status,
            traces=self.traces,
            context=self.context,
            assertion_results=self.assertion_results,
            final_status=final_status,
            mode=self.payload.mode,
            target_node_id=self.payload.target_node_id,
            target_reached=(self.target_reached if target is not None else None),
            stop_reason=self.stop_reason,
            stop_message=self.stop_message,
        )


def _validate_debug_target(payload: ScenarioExecutionPreviewRequest) -> None:
    if payload.mode == ScenarioPreviewMode.FULL:
        return
    target = next(
        (node for node in payload.dsl.nodes if node.id == payload.target_node_id), None
    )
    if target is None:
        raise ResourceConflictError("调试目标节点不存在")
    if not target.enabled:
        raise ResourceConflictError("调试目标节点已停用")
    if target.type in _DEBUG_CONTROL_TYPES:
        raise ResourceConflictError("START/END/ELSE/IF/LOOP 不能作为单节点调试目标")
    if payload.mode == ScenarioPreviewMode.RUN_FROM_HERE:
        cursor = target.parent_id
        while cursor is not None:
            parent = next((node for node in payload.dsl.nodes if node.id == cursor), None)
            if parent is None:
                break
            if parent.type == ScenarioNodeType.LOOP:
                raise ResourceConflictError(
                    "RUN_FROM_HERE 目标位于 LOOP 内，需要真实 Runner 的迭代状态；"
                    "Preview 不伪造前置循环上下文"
                )
            cursor = parent.parent_id


def _validate_preview_assets(
    payload: ScenarioExecutionPreviewRequest, session: Session, user: CurrentUser
) -> None:
    if payload.project_id is not None:
        get_project(session, user, payload.project_id)
    for node in payload.dsl.nodes:
        if node.type in {
            ScenarioNodeType.SQL_QUERY,
            ScenarioNodeType.SQL_EXECUTE,
            ScenarioNodeType.SQL_CLEANUP,
        }:
            if payload.project_id is None:
                raise ResourceConflictError("SQL Node 预览必须提供 project_id")
            if node.type == ScenarioNodeType.SQL_CLEANUP:
                cleanup = node.cleanup_config(payload.dsl.settings.cleanup_policy)
                validate_cleanup_scope(session, payload.project_id, cleanup)
            raw_statement = node.config.get("sql", "")
            if node.type == ScenarioNodeType.SQL_QUERY:
                validate_sql_query(raw_statement)
            elif node.type == ScenarioNodeType.SQL_CLEANUP:
                validate_sql_cleanup(raw_statement)
            else:
                validate_sql_execute(raw_statement)
            connection_id = node.config.get("connection_id")
            config = session.get(DatabaseConnection, connection_id)
            if config is None or not config.enabled:
                raise ResourceConflictError("SQL Node 引用的数据库连接不存在或已停用")
            if config.project_id != payload.project_id:
                raise ResourceConflictError("SQL Node 数据库连接与执行项目不匹配")
            resolve_secret(session, config.password_secret_id, config.project_id)
        elif node.type in _CLEANUP_TYPES and payload.project_id is not None:
            validate_cleanup_scope(
                session,
                payload.project_id,
                node.cleanup_config(payload.dsl.settings.cleanup_policy),
            )
        elif (
            node.type == ScenarioNodeType.API_CLEANUP
            and node.config.get("auth", {}).get("secret_id") is not None
            and payload.project_id is None
        ):
            raise ResourceConflictError("引用 Secret 的 API Cleanup 预览必须提供 project_id")
        elif node.type == ScenarioNodeType.PYTHON_SCRIPT:
            script = node.config.get("script")
            if not isinstance(script, str):
                raise ResourceConflictError("PYTHON_SCRIPT 缺少 script")
            validate_safe_script(script)


def execute_preview(
    payload: ScenarioExecutionPreviewRequest,
    session: Session,
    user: CurrentUser,
    *,
    clock: Callable[[], float] | None = None,
) -> ScenarioExecutionPreviewResponse:
    validation = validate_scenario_dsl(payload.dsl, context_keys=set(payload.context))
    if not validation.valid:
        raise ResourceConflictError(f"Scenario DSL 校验失败：{validation.issues[0].message}")
    _validate_debug_target(payload)
    _validate_preview_assets(payload, session, user)
    return _PreviewExecutor(payload, session, user, clock=clock).run()
