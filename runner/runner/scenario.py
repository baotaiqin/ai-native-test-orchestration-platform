"""Small, isolated Scenario runtime for the C2.1/C2.2/C2.3 node set.

HTTP is delegated to the existing API executor.  Response snapshots stay in
memory for EXTRACT and ASSERT nodes only; traces and errors never contain
response bodies or credentials.  SQL and Script use local allow-list
interpreters, and Cleanup is deferred to one deterministic LIFO phase.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from threading import Event
from typing import Any

import pymysql

from runner.errors import (
    ExecutionError,
    ProtocolError,
    TargetExecutionError,
    TargetTimeoutError,
)
from runner.executors.api import ApiExecutionResult, ApiExecutor, ApiRequestTemplate
from runner.models import ScenarioExecutionPlanResult, ScenarioNodePlan
from runner.runtime_values import case_insensitive_get as _case_insensitive_get
from runner.runtime_values import jsonpath_get as _jsonpath_get
from runner.safe_script import SafeScriptBudgetExceeded, SafeScriptError, run_safe_script

_TEMPLATE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_CONDITION = re.compile(r"^\s*(.+?)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*$")
_SUPPORTED = {
    "START",
    "END",
    "HTTP",
    "IF",
    "ELSE",
    "LOOP",
    "WAIT",
    "SET_VARIABLE",
    "EXTRACT",
    "ASSERT_STATUS",
    "ASSERT_JSONPATH",
    "AI_ASSERTION",
    "SQL_QUERY",
    "SQL_EXECUTE",
    "PYTHON_SCRIPT",
    "API_CLEANUP",
    "SQL_CLEANUP",
}
_SAFE_ERROR_LIMIT = 500
_MAX_TRACE_DURATION_MS = 86_400_000
_HTTP_CONFIG_FIELDS = {
    "method",
    "url",
    "query_params",
    "headers",
    "body",
    "auth",
    "cookies",
    "timeout_ms",
    "follow_redirects",
    "retry_policy",
}
_SQL_COMMENT_MARKERS = ("--", "#", "/*", "*/")
_SQL_DANGEROUS_PATTERNS = (
    r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b",
    r"\bLOAD_FILE\s*\(",
    r"\bLOAD\s+DATA\b",
    r"\b(?:SLEEP|BENCHMARK)\s*\(",
    r"\b(?:GET_LOCK|RELEASE_LOCK)\s*\(",
    r"\b(?:CALL|GRANT|REVOKE)\b",
    r"\bFOR\s+UPDATE\b",
    r"\bLOCK\b",
    r"\b(?:KILL|SHUTDOWN)\b",
)
_SQL_TRANSACTION_PATTERN = re.compile(
    r"\b(?:BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK|SAVEPOINT|"
    r"RELEASE\s+SAVEPOINT)\b"
)
_SQL_MUTATION_PATTERN = re.compile(r"\b(?:ALTER|CREATE|DROP|TRUNCATE|RENAME|GRANT|REVOKE|USE)\b")
_SQL_QUERY_START = re.compile(r"^(?:SELECT|WITH|SHOW|DESCRIBE|EXPLAIN)\b")
_SQL_CLEANUP_FIELDS = {
    "cleanup_id",
    "cleanup_type",
    "policy",
    "enabled",
    "timeout_ms",
    "method",
    "url",
    "query_params",
    "headers",
    "cookies",
    "body",
    "auth",
    "connection_id",
    "sql",
    "params",
    "resource_id_param",
}
_SQL_NODE_FIELDS = {"connection_id", "sql", "params", "result_variable", "max_rows"}
_CLEANUP_TYPES = {"API_CLEANUP", "SQL_CLEANUP"}
_CLEANUP_POLICIES = {"ALWAYS", "ON_SUCCESS", "ON_FAILURE", "NEVER"}
_CLEANUP_OUTCOMES = {"SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"}
_SQL_TRANSIENT_CODES = {1205, 1213, 2006, 2013, 2055}
_SQL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_SQL_ROWS = 1_000
_MAX_CONTEXT_BYTES = 64_000
_MAX_JSON_VALUE_BYTES = 64_000
_TRACE_SEVERITY = {
    "SUCCESS": 1,
    "SKIPPED": 1,
    "FAILED": 2,
    "TIMEOUT": 3,
    "CANCELLED": 4,
}


@dataclass(frozen=True, repr=False)
class ScenarioExecutionResult:
    outcome: str
    traces: list[dict[str, Any]]
    error_type: str | None = None
    error_message: str | None = None
    pending_ai: tuple[Mapping[str, Any], ...] = ()
    cleanup_context: Mapping[str, Any] | None = None

    def __repr__(self) -> str:
        return (
            "ScenarioExecutionResult("
            f"outcome={self.outcome!r}, traces={len(self.traces)}, "
            f"error_type={self.error_type!r}, "
            f"error_message={'[REDACTED]' if self.error_message else None!r})"
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "traces": [dict(trace) for trace in self.traces],
            "error_type": self.error_type,
            "error_message": self.error_message,
        }

    def to_worker_wire(self) -> dict[str, Any]:
        """Private local IPC payload; response/context never enter Backend completion or logs."""

        return {
            **self.to_wire(),
            "pending_ai": [dict(item) for item in self.pending_ai],
            "cleanup_context": (
                deepcopy(dict(self.cleanup_context)) if self.cleanup_context is not None else None
            ),
        }


@dataclass(frozen=True, repr=False)
class ScenarioCleanupTask:
    plan: ScenarioExecutionPlanResult
    context: Mapping[str, Any]
    primary_outcome: str

    def __repr__(self) -> str:
        return (
            "ScenarioCleanupTask("
            f"run_id={self.plan.run_id!r}, primary_outcome={self.primary_outcome!r}, "
            "context=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class ScenarioCleanupResult:
    outcome: str
    traces: list[dict[str, Any]]
    error_type: str | None = None
    error_message: str | None = None

    def to_worker_wire(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "traces": [dict(item) for item in self.traces],
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


class ScenarioRuntimeError(ExecutionError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool = False,
        timeout: bool = False,
        total_timeout: bool = False,
        response: ApiExecutionResult | None = None,
    ) -> None:
        self.retryable = retryable
        self.timeout = timeout
        self.total_timeout = total_timeout
        self.response = response
        super().__init__(message[:_SAFE_ERROR_LIMIT], error_type=error_type[:100])


class ScenarioExecutor:
    """Execute the reviewed Scenario C2.1/C2.2/C2.3 node set."""

    def __init__(
        self,
        plan: ScenarioExecutionPlanResult,
        *,
        stop_event: Event | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        deadline: float | None = None,
        api_executor: ApiExecutor | None = None,
        sql_connector: Callable[..., Any] | None = None,
        defer_cleanup: bool = False,
    ) -> None:
        self.plan = plan
        self.stop_event = stop_event
        self.clock = clock
        self.sleeper = sleeper
        self.deadline = deadline
        self._api_executor = api_executor
        self._owns_api_executor = api_executor is None
        self._api_executor_closed = False
        self._sql_connector = sql_connector or _default_sql_connector
        self._defer_cleanup = defer_cleanup
        self.context = deepcopy(dict(plan.initial_context))
        self.nodes = tuple(plan.nodes)
        self.nodes_by_id = {node.node_id: node for node in self.nodes}
        self.children: dict[str | None, list[ScenarioNodePlan]] = {}
        for node in self.nodes:
            self.children.setdefault(node.parent_id, []).append(node)
        self.traces: dict[str, dict[str, Any]] = {}
        self._failed = False
        self._cancelled = False
        self._total_timeout = False
        self._timed_out = False
        self._stop_requested = False
        self._last_error: ScenarioRuntimeError | None = None
        self.latest_response: ApiExecutionResult | None = None
        self._cleanup_nodes = tuple(node for node in self.nodes if node.node_type in _CLEANUP_TYPES)
        self._running_cleanup = False
        self._cleanup_finalized = False
        self._cleanup_failed = False
        self._pending_ai: list[dict[str, Any]] = []

    def execute(self) -> ScenarioExecutionResult:
        try:
            self._run_children(None, [])
        except ScenarioRuntimeError as exc:
            self._record_failure(self.nodes[0], exc, retry_count=0)
        finally:
            if not self._pending_ai and not self._defer_cleanup:
                self._run_cleanups()
            self._fill_missing_traces()
            self.close()
        if self._cancelled:
            return ScenarioExecutionResult(
                "CANCELLED",
                self._ordered_traces(),
                "CANCEL_REQUESTED",
                "Runner 检测到取消请求",
                cleanup_context=deepcopy(self.context) if self._defer_cleanup else None,
            )
        if self._total_timeout:
            return ScenarioExecutionResult(
                "TIMEOUT",
                self._ordered_traces(),
                "SCENARIO_TIMEOUT",
                "Scenario 执行超时",
                cleanup_context=deepcopy(self.context) if self._defer_cleanup else None,
            )
        if self._timed_out:
            error = self._last_error
            return ScenarioExecutionResult(
                "TIMEOUT",
                self._ordered_traces(),
                error.error_type if error else "SCENARIO_TIMEOUT",
                "Scenario 节点执行超时",
                cleanup_context=deepcopy(self.context) if self._defer_cleanup else None,
            )
        if self._failed or self._cleanup_failed:
            error = self._last_error
            return ScenarioExecutionResult(
                "FAILED",
                self._ordered_traces(),
                error.error_type if error else "SCENARIO_EXECUTION_FAILED",
                "Scenario 节点执行失败",
                cleanup_context=deepcopy(self.context) if self._defer_cleanup else None,
            )
        return ScenarioExecutionResult(
            "SUCCESS",
            self._ordered_traces(),
            pending_ai=tuple(self._pending_ai),
            cleanup_context=(
                deepcopy(self.context) if self._pending_ai or self._defer_cleanup else None
            ),
        )

    @classmethod
    def execute_cleanup_task(
        cls,
        task: ScenarioCleanupTask,
        *,
        api_executor: ApiExecutor | None = None,
        sql_connector: Callable[..., Any] | None = None,
    ) -> ScenarioCleanupResult:
        if task.primary_outcome not in {"SUCCESS", "FAILURE"}:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup outcome 无效")
        executor = cls(
            task.plan,
            api_executor=api_executor,
            sql_connector=sql_connector,
        )
        executor.context = deepcopy(dict(task.context))
        executor._running_cleanup = True
        try:
            for node in reversed(executor._cleanup_nodes):
                executor._run_cleanup_node(node, task.primary_outcome)
        finally:
            executor._running_cleanup = False
            executor._cleanup_finalized = True
            executor.close()
        error = executor._last_error
        return ScenarioCleanupResult(
            "FAILED" if executor._cleanup_failed else "SUCCESS",
            [
                dict(executor.traces[node.node_id])
                for node in executor._cleanup_nodes
                if node.node_id in executor.traces
            ],
            error.error_type if error else None,
            "Scenario Cleanup 执行失败" if error else None,
        )

    def close(self) -> None:
        if self._api_executor_closed:
            return
        self._api_executor_closed = True
        if self._owns_api_executor and self._api_executor is not None:
            try:
                self._api_executor.close()
            except Exception:  # noqa: BLE001 - cleanup must not leak target details
                pass

    def run_embedded_sql_query(
        self, config: Mapping[str, Any], *, timeout_ms: int
    ) -> dict[str, Any]:
        """Reuse the reviewed Scenario SQL_QUERY semantics in another isolated runtime."""

        node = ScenarioNodePlan(
            node_id="embedded_sql_query",
            node_type="SQL_QUERY",
            name="SQL Query",
            parent_id=None,
            enabled=True,
            timeout_ms=timeout_ms,
            failure_policy=None,
            config=dict(config),
        )
        return self._sql(node, mode="QUERY")

    def run_embedded_cleanup(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """Reuse reviewed API/SQL Cleanup request, credential, and SQL semantics."""

        cleanup_type = config.get("cleanup_type")
        timeout_ms = config.get("timeout_ms", 30_000)
        if cleanup_type not in {"API", "SQL"} or type(timeout_ms) is not int:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup 配置无效")
        node = ScenarioNodePlan(
            node_id="embedded_cleanup",
            node_type="API_CLEANUP" if cleanup_type == "API" else "SQL_CLEANUP",
            name="Cleanup",
            parent_id=None,
            enabled=True,
            timeout_ms=timeout_ms,
            failure_policy=None,
            config=dict(config),
        )
        checked = self._cleanup_config(node)
        return (
            self._api_cleanup(node, checked)
            if cleanup_type == "API"
            else self._sql(node, mode="CLEANUP", cleanup_config=checked)
        )

    def _executor(self) -> ApiExecutor:
        if self._api_executor is None:
            self._api_executor = ApiExecutor()
        return self._api_executor

    def _run_children(self, parent_id: str | None, iteration_path: list[int]) -> None:
        for node in self.children.get(parent_id, ()):
            if self._cancelled or self._total_timeout or self._stop_requested:
                self._record_skipped(node)
                self._skip_descendants(node)
                continue
            try:
                self._check_control()
                self._run_node(node, iteration_path)
            except ScenarioRuntimeError as exc:
                self._record_failure(node, exc, retry_count=0)
                if exc.total_timeout:
                    self._total_timeout = True
                elif exc.error_type == "CANCEL_REQUESTED":
                    self._cancelled = True
                self._failed = self._failed or not exc.total_timeout
                self._last_error = exc
                if self._should_stop(node, exc):
                    self._stop_requested = True
                    self._skip_siblings(parent_id, node.node_id)
                    return

    def _run_node(self, node: ScenarioNodePlan, iteration_path: list[int]) -> None:
        if not node.enabled:
            self._record(node, "SKIPPED")
            self._skip_descendants(node)
            return
        if node.node_type in _CLEANUP_TYPES and not self._running_cleanup:
            # Cleanup nodes are registered by plan order and executed exactly
            # once by _run_cleanups, after the primary Scenario outcome exists.
            return
        if node.node_type not in _SUPPORTED:
            raise ScenarioRuntimeError(
                "UNSUPPORTED_NODE", "Scenario 当前节点类型未开放，已拒绝执行"
            )
        if node.node_type in {"START", "END"}:
            started = self.clock()
            self._record(node, "SUCCESS", duration_ms=self._duration_ms(started))
            return
        if node.node_type == "HTTP":
            self._run_leaf(node, lambda: self._http(node))
            return
        if node.node_type == "WAIT":
            self._run_leaf(node, lambda: self._wait(node))
            return
        if node.node_type == "SET_VARIABLE":
            self._run_leaf(node, lambda: self._set_variable(node))
            return
        if node.node_type == "EXTRACT":
            if node.config.get("enabled", True) is False:
                started = self.clock()
                self._record(node, "SKIPPED", duration_ms=self._duration_ms(started))
                return
            self._run_leaf(node, lambda: self._extract(node))
            return
        if node.node_type in {"ASSERT_STATUS", "ASSERT_JSONPATH"}:
            self._run_leaf(node, lambda: self._assert(node))
            return
        if node.node_type == "AI_ASSERTION":
            self._run_leaf(node, lambda: self._defer_ai_assertion(node))
            return
        if node.node_type == "SQL_QUERY":
            self._run_leaf(node, lambda: self._sql(node, mode="QUERY"))
            return
        if node.node_type == "SQL_EXECUTE":
            self._run_leaf(node, lambda: self._sql(node, mode="EXECUTE"))
            return
        if node.node_type == "PYTHON_SCRIPT":
            self._run_leaf(node, lambda: self._script(node))
            return
        if node.node_type == "IF":
            self._run_if(node, iteration_path)
            return
        if node.node_type == "ELSE":
            started = self.clock()
            self._record(node, "SUCCESS", duration_ms=self._duration_ms(started))
            self._run_children(node.node_id, iteration_path)
            return
        if node.node_type == "LOOP":
            self._run_loop(node, iteration_path)
            return
        raise ScenarioRuntimeError("UNSUPPORTED_NODE", "Scenario 当前节点类型未开放，已拒绝执行")

    def _run_leaf(self, node: ScenarioNodePlan, action: Callable[[], dict[str, Any]]) -> None:
        max_attempts = 2 if self._policy(node) == "RETRY_ONCE" else 1
        last_error: ScenarioRuntimeError | None = None
        total_duration_ms = 0
        for attempt in range(max_attempts):
            started = self.clock()
            context_before = deepcopy(self.context)
            try:
                self._check_control()
                detail = action()
                duration_ms = self._duration_ms(started)
                self._check_control()
                if duration_ms > node.timeout_ms:
                    raise ScenarioRuntimeError(
                        "NODE_TIMEOUT", "Scenario 节点执行超时", timeout=True
                    )
                total_duration_ms = min(_MAX_TRACE_DURATION_MS, total_duration_ms + duration_ms)
                self._record(
                    node,
                    "SUCCESS",
                    retry_count=attempt,
                    detail=detail,
                    duration_ms=total_duration_ms,
                )
                return
            except ScenarioRuntimeError as exc:
                self.context = context_before
                last_error = exc
                duration_ms = self._duration_ms(started)
                total_duration_ms = min(_MAX_TRACE_DURATION_MS, total_duration_ms + duration_ms)
                if exc.total_timeout:
                    self._total_timeout = True
                    self._failed = False
                    self._stop_requested = True
                    self._last_error = exc
                    self._record_failure(
                        node, exc, retry_count=attempt, duration_ms=total_duration_ms
                    )
                    return
                if exc.error_type == "CANCEL_REQUESTED":
                    self._cancelled = True
                    self._stop_requested = True
                    self._record_failure(
                        node, exc, retry_count=attempt, duration_ms=total_duration_ms
                    )
                    return
                if (
                    exc.error_type == "HTTP_5XX"
                    and exc.response is not None
                    and attempt + 1 >= max_attempts
                ):
                    self._record(
                        node,
                        "SUCCESS",
                        retry_count=attempt,
                        duration_ms=total_duration_ms,
                    )
                    return
                if attempt + 1 < max_attempts and exc.retryable:
                    continue
                if exc.timeout:
                    self._timed_out = True
                self._record_failure(node, exc, retry_count=attempt, duration_ms=total_duration_ms)
                self._failed = True
                self._last_error = exc
                if self._should_stop(node, exc):
                    self._stop_requested = True
                    self._skip_siblings(node.parent_id, node.node_id)
                return
        if last_error is not None:
            raise last_error

    def _duration_ms(self, started: float) -> int:
        return min(
            _MAX_TRACE_DURATION_MS,
            max(0, round((self.clock() - started) * 1000)),
        )

    def _run_if(self, node: ScenarioNodePlan, iteration_path: list[int]) -> None:
        started = self.clock()
        matched = self._condition(node.config.get("condition"))
        self._record(
            node,
            "SUCCESS",
            detail={"condition_result": matched},
            duration_ms=self._duration_ms(started),
        )
        children = self.children.get(node.node_id, ())
        if matched:
            for child in children:
                if child.node_type == "ELSE":
                    self._record_skipped(child)
                    self._skip_descendants(child)
                else:
                    self._run_children_one(child, iteration_path)
        else:
            for child in children:
                if child.node_type == "ELSE":
                    self._run_children_one(child, iteration_path)
                else:
                    self._record_skipped(child)
                    self._skip_descendants(child)

    def _run_children_one(self, node: ScenarioNodePlan, iteration_path: list[int]) -> None:
        if self._cancelled or self._total_timeout or self._stop_requested:
            self._record_skipped(node)
            self._skip_descendants(node)
            return
        try:
            self._check_control()
            self._run_node(node, iteration_path)
        except ScenarioRuntimeError as exc:
            self._record_failure(node, exc, retry_count=0)
            if exc.total_timeout:
                self._total_timeout = True
            elif exc.error_type == "CANCEL_REQUESTED":
                self._cancelled = True
            else:
                self._failed = True
            self._last_error = exc
            if self._should_stop(node, exc):
                self._stop_requested = True
                self._skip_siblings(node.parent_id, node.node_id)
                self._skip_descendants(node)

    def _run_loop(self, node: ScenarioNodePlan, iteration_path: list[int]) -> None:
        started = self.clock()
        values = node.config.get("items")
        if values is None:
            iterations = node.config.get("iterations")
            if type(iterations) is not int or iterations < 1:
                raise ScenarioRuntimeError("LOOP_CONFIG_INVALID", "LOOP 配置无效")
            max_iterations = self.plan.settings["max_loop_iterations"]
            if iterations > max_iterations:
                raise ScenarioRuntimeError("LOOP_LIMIT", "LOOP 次数超过安全上限")
            values = list(range(iterations))
        else:
            values = self._resolve(values)
            if not isinstance(values, list):
                raise ScenarioRuntimeError("LOOP_CONFIG_INVALID", "LOOP items 必须是数组")
            if len(values) > self.plan.settings["max_loop_iterations"]:
                raise ScenarioRuntimeError("LOOP_LIMIT", "LOOP items 超过安全上限")
        item_name = node.config.get("item_variable", "loop_item")
        index_name = node.config.get("index_variable", "loop_index")
        if not _variable_name(item_name) or not _variable_name(index_name):
            raise ScenarioRuntimeError("LOOP_CONFIG_INVALID", "LOOP 变量名无效")
        for index, value in enumerate(values):
            if self._cancelled or self._total_timeout or self._stop_requested:
                return
            self._check_control()
            self.context[item_name] = value
            self.context[index_name] = index
            for child in self.children.get(node.node_id, ()):
                self._run_children_one(child, [*iteration_path, index])
                if self._cancelled or self._total_timeout or self._stop_requested:
                    self._record(
                        node,
                        "SUCCESS",
                        detail={"iterations": len(values)},
                        duration_ms=self._duration_ms(started),
                    )
                    return
        self._record(
            node,
            "SUCCESS",
            detail={"iterations": len(values)},
            duration_ms=self._duration_ms(started),
        )

    def _http(self, node: ScenarioNodePlan) -> dict[str, Any]:
        self.latest_response = None
        request = self._http_request(node)
        started = self.clock()
        try:
            result = self._executor().execute(request)
        except TargetTimeoutError as exc:
            raise ScenarioRuntimeError(
                "TARGET_TIMEOUT",
                "目标 API 请求超时",
                retryable=True,
                timeout=True,
            ) from exc
        except TargetExecutionError as exc:
            raise ScenarioRuntimeError(
                "TARGET_NETWORK_ERROR",
                "目标 API 网络请求失败",
                retryable=True,
            ) from exc
        except ExecutionError as exc:
            raise ScenarioRuntimeError(
                exc.error_type or "HTTP_EXECUTION_ERROR",
                "HTTP 节点执行失败",
            ) from exc
        except (ProtocolError, TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("HTTP_CONFIG_INVALID", "HTTP 节点配置无效") from exc
        if not isinstance(result, ApiExecutionResult):
            raise ScenarioRuntimeError("HTTP_RESPONSE_INVALID", "HTTP 节点响应无效")
        self.latest_response = result
        duration_ms = self._duration_ms(started)
        if duration_ms > node.timeout_ms:
            raise ScenarioRuntimeError(
                "NODE_TIMEOUT",
                "HTTP 节点执行超时",
                timeout=True,
                response=result,
            )
        if 500 <= result.status_code <= 599:
            raise ScenarioRuntimeError(
                "HTTP_5XX",
                "目标 API 返回 5xx",
                retryable=True,
                response=result,
            )
        return {"status_code": result.status_code, "elapsed_ms": result.elapsed_ms}

    def _http_request(self, node: ScenarioNodePlan) -> ApiRequestTemplate:
        config = node.config
        if not isinstance(config, Mapping):
            raise ScenarioRuntimeError("HTTP_CONFIG_INVALID", "HTTP 节点配置无效")
        if set(config) - _HTTP_CONFIG_FIELDS:
            raise ScenarioRuntimeError("HTTP_CONFIG_INVALID", "HTTP 节点配置包含未知字段")
        try:
            rendered = self._resolve(dict(config))
            if not isinstance(rendered, Mapping):
                raise TypeError
            headers = self._request_items(rendered.get("headers", []), "headers")
            query_params = self._request_items(rendered.get("query_params", []), "query_params")
            cookies = self._request_items(rendered.get("cookies", []), "cookies")
            auth = rendered.get("auth", {"type": "NONE"})
            if auth is None:
                auth = {"type": "NONE"}
            request_data = {
                "method": rendered.get("method", "GET"),
                "url": rendered.get("url"),
                "query_params": query_params,
                "headers": headers,
                "cookies": cookies,
                "body": rendered.get("body", {"type": "NONE"}),
                "auth": auth,
                "timeout_ms": min(rendered.get("timeout_ms", node.timeout_ms), node.timeout_ms),
                "follow_redirects": rendered.get("follow_redirects", True),
                "retry_policy": rendered.get("retry_policy", {"max_retries": 0}),
            }
            return ApiRequestTemplate.from_mapping(request_data)
        except ScenarioRuntimeError:
            raise
        except (ExecutionError, ProtocolError, TypeError, ValueError, OverflowError) as exc:
            raise ScenarioRuntimeError("HTTP_CONFIG_INVALID", "HTTP 节点配置无效") from exc

    def _request_items(self, value: object, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProtocolError(f"HTTP {label} 必须是数组")
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ProtocolError(f"HTTP {label} 项无效")
            current = dict(item)
            name = current.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ProtocolError(f"HTTP {label} 名称无效")
            if "value" in current and not isinstance(current["value"], str):
                current["value"] = _stringify_request_value(current["value"])
            result.append(current)
        return result

    def _extract(self, node: ScenarioNodePlan) -> dict[str, Any]:
        config = node.config
        name = config.get("name")
        source = config.get("source")
        expression = config.get("expression")
        required = config.get("required", True)
        if (
            not _variable_name(name)
            or source not in {"JSONPATH", "HEADER", "COOKIE"}
            or not isinstance(expression, str)
            or not expression
            or type(required) is not bool
        ):
            raise ScenarioRuntimeError("EXTRACT_CONFIG_INVALID", "EXTRACT 配置无效")
        response = self.latest_response
        if response is None:
            raise ScenarioRuntimeError("RESPONSE_VALUE_MISSING", "Response Snapshot 不存在")
        try:
            if source == "JSONPATH":
                value = _jsonpath_get(response.json_body, expression)
            elif source == "HEADER":
                value = _case_insensitive_get(response.headers, expression)
            else:
                value = _case_insensitive_get(response.cookies, expression)
        except (KeyError, TypeError, ValueError) as exc:
            if required:
                raise ScenarioRuntimeError(
                    "RESPONSE_VALUE_MISSING", "EXTRACT 未匹配响应值"
                ) from exc
            value = deepcopy(config.get("default_value"))
        self.context[name] = deepcopy(value)
        return {"variable": name, "source": source}

    def _assert(self, node: ScenarioNodePlan) -> dict[str, Any]:
        response = self.latest_response
        if response is None:
            raise ScenarioRuntimeError("ASSERTION_FAILED", "断言失败")
        config = node.config
        try:
            if node.node_type == "ASSERT_STATUS":
                expected = self._resolve(config.get("expected"))
                if type(expected) is not int or not 100 <= expected <= 599:
                    raise ValueError
                passed = response.status_code == expected
            else:
                expression = self._resolve(config.get("expression"))
                if not isinstance(expression, str) or not expression.startswith("$"):
                    raise ValueError
                expected = self._resolve(config.get("expected"))
                actual = _jsonpath_get(response.json_body, expression)
                passed = type(actual) is type(expected) and actual == expected
        except (KeyError, TypeError, ValueError, ScenarioRuntimeError) as exc:
            raise ScenarioRuntimeError("ASSERTION_FAILED", "断言失败") from exc
        if not passed:
            raise ScenarioRuntimeError("ASSERTION_FAILED", "断言失败")
        return {"assertion": "passed"}

    def _defer_ai_assertion(self, node: ScenarioNodePlan) -> dict[str, Any]:
        if self.latest_response is None:
            raise ScenarioRuntimeError("RESPONSE_VALUE_MISSING", "AI 断言缺少响应")
        config = node.config
        if (
            not isinstance(config, Mapping)
            or set(config) - {"prompt_id", "criteria", "confidence_threshold"}
            or type(config.get("prompt_id")) is not int
            or config["prompt_id"] <= 0
            or not isinstance(config.get("criteria"), str)
            or not config["criteria"].strip()
            or len(config["criteria"]) > 4000
            or isinstance(config.get("confidence_threshold", 0.8), bool)
            or not isinstance(config.get("confidence_threshold", 0.8), (int, float))
            or not 0 <= config.get("confidence_threshold", 0.8) <= 1
        ):
            raise ScenarioRuntimeError("AI_ASSERTION_CONFIG_INVALID", "AI 断言配置无效")
        self._pending_ai.append(
            {"node_id": node.node_id, "response": self.latest_response.to_wire()}
        )
        return {"assertion": "deferred"}

    def _sql(
        self,
        node: ScenarioNodePlan,
        *,
        mode: str,
        cleanup_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = cleanup_config if cleanup_config is not None else node.config
        allowed = _SQL_CLEANUP_FIELDS if cleanup_config is not None else _SQL_NODE_FIELDS
        if not isinstance(config, Mapping) or set(config) - allowed:
            raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 节点配置无效")
        connection_id = config.get("connection_id")
        if (
            isinstance(connection_id, bool)
            or not isinstance(connection_id, int)
            or connection_id <= 0
        ):
            raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 节点配置无效")
        connection_plan = self._sql_connection(connection_id)
        statement = config.get("sql")
        if not isinstance(statement, str):
            raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 节点配置无效")
        statement = _validate_sql_statement(statement, mode)
        parameters = self._resolve(config.get("params", {}))
        if not isinstance(parameters, (dict, list)):
            raise ScenarioRuntimeError("SQL_PARAMS_INVALID", "SQL 参数格式无效")
        if isinstance(parameters, dict) and any(
            not isinstance(key, str) or _SQL_NAME.fullmatch(key) is None for key in parameters
        ):
            raise ScenarioRuntimeError("SQL_PARAMS_INVALID", "SQL 参数名称无效")
        if cleanup_config is not None:
            resource_id_param = config.get("resource_id_param")
            if resource_id_param is not None:
                if _SQL_NAME.fullmatch(resource_id_param) is None or not isinstance(
                    parameters, dict
                ):
                    raise ScenarioRuntimeError("SQL_PARAMS_INVALID", "SQL Cleanup 参数无效")
                if "resource_id" not in self.context:
                    raise ScenarioRuntimeError("SQL_PARAMS_INVALID", "SQL Cleanup 缺少资源上下文")
                parameters[resource_id_param] = self.context["resource_id"]
        result_variable = config.get(
            "result_variable", "sql_rows" if mode == "QUERY" else "affected_rows"
        )
        if not _variable_name(result_variable):
            raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 结果变量名无效")
        max_rows = config.get("max_rows", 100)
        if mode == "QUERY":
            if (
                isinstance(max_rows, bool)
                or not isinstance(max_rows, int)
                or not 1 <= max_rows <= _MAX_SQL_ROWS
            ):
                raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 返回行数上限无效")
        password = self._secret_value(connection_plan.password_secret_id)
        connection: Any | None = None
        cursor: Any | None = None
        started = self.clock()
        allow_cancel = cleanup_config is None
        try:
            if allow_cancel:
                self._check_control()
            else:
                self._check_deadline_only()
            connection = self._sql_connector(connection_plan, password, node.timeout_ms)
            cursor = connection.cursor()
            affected = cursor.execute(statement, parameters)
            if allow_cancel:
                self._check_control()
            else:
                self._check_deadline_only()
            if self._duration_ms(started) > node.timeout_ms:
                raise ScenarioRuntimeError("SQL_NODE_TIMEOUT", "SQL 节点执行超时", timeout=True)
            if mode == "QUERY":
                rows = cursor.fetchmany(max_rows + 1)
                if not isinstance(rows, (list, tuple)):
                    raise ScenarioRuntimeError("SQL_RESULT_INVALID", "SQL 查询结果无效")
                truncated = len(rows) > max_rows
                safe_rows = [_json_safe_value(row) for row in rows[:max_rows]]
                self.context[result_variable] = safe_rows
                _validate_context_size(self.context)
                _safe_rollback(connection)
                return {
                    "row_count": len(safe_rows),
                    "truncated": truncated,
                    "result_variable": result_variable,
                }
            if isinstance(affected, bool) or not isinstance(affected, int):
                raise ScenarioRuntimeError("SQL_RESULT_INVALID", "SQL 执行结果无效")
            connection.commit()
            self.context[result_variable] = affected
            _validate_context_size(self.context)
            return {"affected_rows": affected, "result_variable": result_variable}
        except ScenarioRuntimeError:
            _safe_rollback(connection)
            raise
        except (TimeoutError, OSError, pymysql.MySQLError) as exc:
            _safe_rollback(connection)
            timeout = _is_sql_timeout(exc)
            retryable = _is_sql_retryable(exc, mode)
            raise ScenarioRuntimeError(
                "SQL_TIMEOUT"
                if timeout
                else ("SQL_TRANSIENT_ERROR" if retryable else "SQL_EXECUTION_ERROR"),
                "SQL 节点执行超时" if timeout else "SQL 节点执行失败",
                retryable=retryable,
                timeout=timeout,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - external driver boundary is sanitized
            _safe_rollback(connection)
            raise ScenarioRuntimeError("SQL_EXECUTION_ERROR", "SQL 节点执行失败") from exc
        finally:
            _safe_close(cursor)
            _safe_close(connection)
            password = ""

    def _script(self, node: ScenarioNodePlan) -> dict[str, Any]:
        config = node.config
        if not isinstance(config, Mapping) or set(config) - {"script"}:
            raise ScenarioRuntimeError("PYTHON_SCRIPT_INVALID", "Python Script 配置无效")
        script = config.get("script")
        max_steps = max(20, min(500, node.timeout_ms // 10))
        try:
            run_safe_script(script, self.context, max_steps=max_steps)
        except SafeScriptBudgetExceeded as exc:
            raise ScenarioRuntimeError(
                "PYTHON_TIMEOUT", "Python Script 超过受限计算预算", timeout=True
            ) from exc
        except SafeScriptError as exc:
            raise ScenarioRuntimeError(
                "PYTHON_SCRIPT_INVALID", "Python Script 不符合受限执行规则"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - evaluator boundary is sanitized
            raise ScenarioRuntimeError("PYTHON_RUNTIME_ERROR", "Python Script 执行失败") from exc
        self._check_control()
        return {"restricted": True}

    def _run_cleanups(self) -> None:
        if not self._cleanup_nodes:
            self._cleanup_finalized = True
            return
        primary_outcome = self._cleanup_outcome()
        self._running_cleanup = True
        try:
            for node in reversed(self._cleanup_nodes):
                try:
                    self._run_cleanup_node(node, primary_outcome)
                except ScenarioRuntimeError as exc:
                    self._record_failure(node, exc, retry_count=0)
                    self._cleanup_failed = True
                    self._last_error = exc
                    if exc.total_timeout:
                        self._total_timeout = True
                        break
                except Exception:  # noqa: BLE001 - one cleanup cannot block later LIFO items
                    error = ScenarioRuntimeError("CLEANUP_EXECUTION_ERROR", "Cleanup 执行失败")
                    self._record_failure(node, error, retry_count=0)
                    self._cleanup_failed = True
                    self._last_error = error
                if self._total_timeout:
                    break
        finally:
            self._running_cleanup = False
            self._cleanup_finalized = True

    def _cleanup_outcome(self) -> str:
        if self._cancelled:
            return "CANCELLED"
        if self._total_timeout or self._timed_out:
            return "TIMEOUT"
        if self._failed:
            return "FAILURE"
        return "SUCCESS"

    def _run_cleanup_node(self, node: ScenarioNodePlan, primary_outcome: str) -> None:
        config = self._cleanup_config(node)
        policy = config.get("policy", self.plan.settings.get("cleanup_policy", "ALWAYS"))
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool) or policy not in _CLEANUP_POLICIES:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup 配置无效")
        if not enabled or not _cleanup_policy_matches(policy, primary_outcome):
            self._record(node, "SKIPPED")
            return
        max_attempts = 2 if node.failure_policy == "RETRY_ONCE" else 1
        total_duration_ms = 0
        for attempt in range(max_attempts):
            started = self.clock()
            try:
                self._check_deadline_only()
                detail = (
                    self._api_cleanup(node, config)
                    if node.node_type == "API_CLEANUP"
                    else self._sql(node, mode="CLEANUP", cleanup_config=config)
                )
                self._check_deadline_only()
                duration_ms = self._duration_ms(started)
                if duration_ms > node.timeout_ms:
                    raise ScenarioRuntimeError(
                        "CLEANUP_NODE_TIMEOUT",
                        "Cleanup 节点执行超时",
                        timeout=True,
                    )
                total_duration_ms = min(_MAX_TRACE_DURATION_MS, total_duration_ms + duration_ms)
                self._record(
                    node,
                    "SUCCESS",
                    retry_count=attempt,
                    detail=detail,
                    duration_ms=total_duration_ms,
                )
                return
            except ScenarioRuntimeError as exc:
                duration_ms = self._duration_ms(started)
                total_duration_ms = min(_MAX_TRACE_DURATION_MS, total_duration_ms + duration_ms)
                if exc.total_timeout:
                    self._total_timeout = True
                if exc.timeout:
                    self._timed_out = True
                if attempt + 1 < max_attempts and exc.retryable and not exc.total_timeout:
                    continue
                self._record_failure(node, exc, retry_count=attempt, duration_ms=total_duration_ms)
                self._cleanup_failed = True
                self._last_error = exc
                return

    def _cleanup_config(self, node: ScenarioNodePlan) -> Mapping[str, Any]:
        config = node.config
        if not isinstance(config, Mapping) or set(config) - _SQL_CLEANUP_FIELDS:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup 配置无效")
        expected = "API" if node.node_type == "API_CLEANUP" else "SQL"
        if config.get("cleanup_type", expected) != expected:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup 类型无效")
        timeout_ms = config.get("timeout_ms", node.timeout_ms)
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 100 <= timeout_ms <= 120_000
            or timeout_ms != node.timeout_ms
        ):
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "Cleanup timeout 无效")
        return config

    def _api_cleanup(self, node: ScenarioNodePlan, config: Mapping[str, Any]) -> dict[str, Any]:
        try:
            request = self._cleanup_request(node, config)
            result = self._executor().execute(request)
        except TargetTimeoutError as exc:
            raise ScenarioRuntimeError(
                "TARGET_TIMEOUT", "Cleanup 目标 API 请求超时", retryable=True, timeout=True
            ) from exc
        except TargetExecutionError as exc:
            raise ScenarioRuntimeError(
                "TARGET_NETWORK_ERROR", "Cleanup 目标 API 网络请求失败", retryable=True
            ) from exc
        except ScenarioRuntimeError:
            raise
        except ExecutionError as exc:
            raise ScenarioRuntimeError(
                exc.error_type or "HTTP_CLEANUP_ERROR", "API Cleanup 执行失败"
            ) from exc
        except (ProtocolError, TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("CLEANUP_CONFIG_INVALID", "API Cleanup 配置无效") from exc
        if not isinstance(result, ApiExecutionResult):
            raise ScenarioRuntimeError("HTTP_CLEANUP_ERROR", "API Cleanup 响应无效")
        if result.status_code >= 400:
            raise ScenarioRuntimeError(
                "HTTP_5XX" if result.status_code >= 500 else "HTTP_CLEANUP_ERROR",
                "API Cleanup 返回错误状态",
                retryable=result.status_code >= 500,
                response=result if result.status_code >= 500 else None,
            )
        return {"status_code": result.status_code, "elapsed_ms": result.elapsed_ms}

    def _cleanup_request(
        self, node: ScenarioNodePlan, config: Mapping[str, Any]
    ) -> ApiRequestTemplate:
        rendered = self._resolve(dict(config))
        if not isinstance(rendered, Mapping):
            raise ProtocolError("API Cleanup 配置无效")
        method = rendered.get("method")
        if method not in {"DELETE", "POST", "PUT", "PATCH"}:
            raise ProtocolError("API Cleanup method 无效")
        headers = self._request_items(rendered.get("headers", []), "cleanup headers")
        query = self._request_items(rendered.get("query_params", []), "cleanup query_params")
        cookies = self._request_items(rendered.get("cookies", []), "cleanup cookies")
        auth = rendered.get("auth", {"type": "NONE"})
        if not isinstance(auth, Mapping):
            raise ProtocolError("API Cleanup auth 无效")
        if set(auth) - {"type", "credential_ref", "secret_id", "key_name", "placement"}:
            raise ProtocolError("API Cleanup auth 包含未知字段")
        auth_type = auth.get("type", "NONE")
        if auth_type not in {"NONE", "BEARER", "BASIC", "API_KEY"}:
            raise ProtocolError("API Cleanup auth 类型无效")
        placement = auth.get("placement", "HEADER")
        key_name = auth.get("key_name")
        if auth_type in {"BEARER", "BASIC"} and (key_name is not None or placement != "HEADER"):
            raise ProtocolError("API Cleanup auth 配置无效")
        if auth_type == "API_KEY" and (not isinstance(key_name, str) or not key_name.strip()):
            raise ProtocolError("API Cleanup API_KEY 配置无效")
        credential = self._cleanup_credential(auth)
        if auth_type == "NONE" and credential is not None:
            raise ProtocolError("NONE API Cleanup 不能配置凭据")
        if auth_type == "BEARER":
            if credential is None:
                raise ProtocolError("API Cleanup 缺少凭据")
            headers.append({"name": "Authorization", "value": f"Bearer {credential}"})
        elif auth_type == "BASIC":
            if credential is None or ":" not in credential:
                raise ProtocolError("API Cleanup BASIC 凭据格式无效")
            encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
            headers.append({"name": "Authorization", "value": f"Basic {encoded}"})
        elif auth_type == "API_KEY":
            if credential is None or not isinstance(key_name, str) or not key_name.strip():
                raise ProtocolError("API Cleanup API_KEY 配置无效")
            if placement == "HEADER":
                headers.append({"name": key_name, "value": credential})
            elif placement == "QUERY":
                query.append({"name": key_name, "value": credential})
            else:
                raise ProtocolError("API Cleanup API_KEY placement 无效")
        elif auth.get("key_name") is not None or auth.get("placement", "HEADER") != "HEADER":
            raise ProtocolError("NONE API Cleanup auth 配置无效")
        body = rendered.get("body")
        if body is None:
            body = {"type": "NONE"}
        return ApiRequestTemplate.from_mapping(
            {
                "method": method,
                "url": rendered.get("url"),
                "query_params": query,
                "headers": headers,
                "cookies": cookies,
                "body": body,
                "auth": {"type": "NONE"},
                "timeout_ms": min(rendered.get("timeout_ms", node.timeout_ms), node.timeout_ms),
                "follow_redirects": False,
            }
        )

    def _cleanup_credential(self, auth: Mapping[str, Any]) -> str | None:
        secret_id = auth.get("secret_id")
        credential_ref = auth.get("credential_ref")
        if secret_id is not None and credential_ref is not None:
            raise ProtocolError("API Cleanup 凭据引用只能二选一")
        if secret_id is not None:
            if isinstance(secret_id, bool) or not isinstance(secret_id, int) or secret_id <= 0:
                raise ProtocolError("API Cleanup secret_id 无效")
            return self._secret_value(secret_id)
        if credential_ref is not None:
            value = self._resolve(credential_ref)
            if not isinstance(value, str) or not value:
                raise ProtocolError("API Cleanup credential_ref 无效")
            return value
        return None

    def _sql_connection(self, connection_id: int) -> Any:
        matches = [
            item for item in self.plan.sql_connections if item.connection_id == connection_id
        ]
        if len(matches) != 1 or matches[0].db_type != "MYSQL":
            raise ScenarioRuntimeError("SQL_CONNECTION_INVALID", "SQL Connection 不可用")
        return matches[0]

    def _secret_value(self, secret_id: int) -> str:
        matches = [item.value for item in self.plan.secrets if item.secret_id == secret_id]
        if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0]:
            raise ScenarioRuntimeError("SECRET_REFERENCE_INVALID", "Scenario Secret 引用无效")
        return matches[0]

    def _set_variable(self, node: ScenarioNodePlan) -> dict[str, Any]:
        name = node.config.get("name")
        if not _variable_name(name):
            raise ScenarioRuntimeError("VARIABLE_NAME_INVALID", "SET_VARIABLE 变量名无效")
        self.context[name] = self._resolve(node.config.get("value"))
        return {"variable": name}

    def _wait(self, node: ScenarioNodePlan) -> dict[str, Any]:
        duration_ms = node.config.get("duration_ms")
        if type(duration_ms) is not int or not 1 <= duration_ms <= 600_000:
            raise ScenarioRuntimeError("WAIT_CONFIG_INVALID", "WAIT duration_ms 无效")
        if duration_ms > node.timeout_ms:
            raise ScenarioRuntimeError(
                "WAIT_TIMEOUT", "WAIT 超过节点 timeout_ms", retryable=True, timeout=True
            )
        remaining = duration_ms / 1000.0
        started = self.clock()
        if self.stop_event is None:
            if self.deadline is not None:
                remaining = min(remaining, max(0.0, self.deadline - started))
            if remaining <= 0:
                raise ScenarioRuntimeError(
                    "TOTAL_TIMEOUT", "Scenario 执行超时", timeout=True, total_timeout=True
                )
            self.sleeper(remaining)
            self._check_control()
            if remaining < duration_ms / 1000.0:
                raise ScenarioRuntimeError(
                    "TOTAL_TIMEOUT", "Scenario 执行超时", timeout=True, total_timeout=True
                )
            return {"duration_ms": duration_ms}
        while remaining > 0:
            self._check_control()
            elapsed = self.clock() - started
            if elapsed >= duration_ms / 1000.0:
                break
            step = min(0.1, remaining, duration_ms / 1000.0 - elapsed)
            if self.stop_event is not None:
                if self.stop_event.wait(step):
                    raise ScenarioRuntimeError("CANCEL_REQUESTED", "Runner 检测到取消请求")
            else:
                self.sleeper(step)
            remaining -= step
        return {"duration_ms": duration_ms}

    def _condition(self, value: object) -> bool:
        resolved = self._resolve(value)
        if isinstance(resolved, bool):
            return resolved
        if isinstance(resolved, Mapping):
            return _compare(
                self._resolve(resolved.get("left")),
                str(resolved.get("operator", "==")),
                self._resolve(resolved.get("right")),
            )
        if not isinstance(resolved, str):
            return bool(resolved)
        match = _CONDITION.fullmatch(resolved)
        if match is None:
            return bool(resolved)
        left, operator, right = match.groups()
        return _compare(self._literal(left), operator, self._literal(right))

    def _literal(self, value: str) -> Any:
        stripped = value.strip()
        resolved = self._resolve(stripped)
        if resolved != stripped or not isinstance(resolved, str):
            return resolved
        try:
            return json.loads(stripped)
        except (TypeError, ValueError):
            return stripped.strip("'\"")

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._resolve(child) for child in value]
        if not isinstance(value, str):
            return value
        match = _TEMPLATE.fullmatch(value)
        if match:
            try:
                return deepcopy(_lookup(self.context, match.group(1)))
            except KeyError as exc:
                raise ScenarioRuntimeError(
                    "MISSING_RUNTIME_CONTEXT", "Runtime Context 缺少变量"
                ) from exc

        def replace(found: re.Match[str]) -> str:
            child = _lookup(self.context, found.group(1))
            if isinstance(child, (dict, list)):
                return json.dumps(child, ensure_ascii=False, separators=(",", ":"))
            if isinstance(child, bool):
                return str(child).lower()
            return str(child)

        try:
            return _TEMPLATE.sub(replace, value)
        except KeyError as exc:
            raise ScenarioRuntimeError(
                "MISSING_RUNTIME_CONTEXT", "Runtime Context 缺少变量"
            ) from exc

    def _check_control(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise ScenarioRuntimeError("CANCEL_REQUESTED", "Runner 检测到取消请求")
        self._check_deadline_only()

    def _check_deadline_only(self) -> None:
        if self.deadline is not None and self.clock() >= self.deadline:
            raise ScenarioRuntimeError(
                "TOTAL_TIMEOUT", "Scenario 执行超时", timeout=True, total_timeout=True
            )

    def _policy(self, node: ScenarioNodePlan) -> str:
        # The scene-wide switch is authoritative for the main flow. A node may
        # still request its one bounded retry, but CONTINUE cannot bypass a
        # scene configured to stop after the final failure. Cleanup is handled
        # by the independent finalization phase below.
        if node.failure_policy == "RETRY_ONCE":
            return "RETRY_ONCE"
        if self.plan.settings["stop_on_failure"]:
            return "STOP"
        return node.failure_policy or "CONTINUE"

    def _should_stop(self, node: ScenarioNodePlan, error: ScenarioRuntimeError) -> bool:
        if error.total_timeout or error.error_type == "CANCEL_REQUESTED":
            return True
        return self._policy(node) != "CONTINUE"

    def _record(
        self,
        node: ScenarioNodePlan,
        status: str,
        *,
        retry_count: int = 0,
        detail: Mapping[str, Any] | None = None,
        duration_ms: int = 0,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        del detail  # Details are intentionally not part of the C1 trace wire model.
        previous = self.traces.get(node.node_id)
        duration_ms = min(
            _MAX_TRACE_DURATION_MS,
            max(0, duration_ms) + (int(previous["duration_ms"]) if previous else 0),
        )
        if previous is not None:
            retry_count = min(3, retry_count + int(previous["retry_count"]))
            previous_status = str(previous["status"])
            if _TRACE_SEVERITY.get(previous_status, 0) > _TRACE_SEVERITY.get(status, 0):
                status = previous_status
                error_type = previous.get("error_type")
                error_message = previous.get("error_message")
            elif _TRACE_SEVERITY.get(previous_status, 0) == _TRACE_SEVERITY.get(status, 0):
                error_type = error_type or previous.get("error_type")
                error_message = error_message or previous.get("error_message")
        self.traces[node.node_id] = {
            "node_id": node.node_id,
            "status": status,
            "duration_ms": duration_ms,
            "retry_count": min(3, retry_count),
            "error_type": error_type,
            "error_message": error_message[:_SAFE_ERROR_LIMIT] if error_message else None,
        }

    def _record_failure(
        self,
        node: ScenarioNodePlan,
        error: ScenarioRuntimeError,
        *,
        retry_count: int,
        duration_ms: int = 0,
    ) -> None:
        self._record(
            node,
            "TIMEOUT" if error.timeout else "FAILED",
            retry_count=retry_count,
            duration_ms=duration_ms,
            error_type=error.error_type,
            error_message=str(error),
        )

    def _record_skipped(self, node: ScenarioNodePlan) -> None:
        if (
            node.node_type in _CLEANUP_TYPES
            and not self._running_cleanup
            and not self._cleanup_finalized
        ):
            return
        if node.node_id not in self.traces:
            self._record(node, "SKIPPED")

    def _skip_descendants(self, node: ScenarioNodePlan) -> None:
        for child in self.children.get(node.node_id, ()):
            self._record_skipped(child)
            self._skip_descendants(child)

    def _skip_siblings(self, parent_id: str | None, after_node_id: str) -> None:
        siblings = self.children.get(parent_id, ())
        seen = False
        for node in siblings:
            if node.node_id == after_node_id:
                seen = True
                continue
            if seen:
                self._record_skipped(node)
                self._skip_descendants(node)

    def _fill_missing_traces(self) -> None:
        for node in self.nodes:
            self._record_skipped(node)

    def _ordered_traces(self) -> list[dict[str, Any]]:
        return [
            dict(self.traces[node.node_id]) for node in self.nodes if node.node_id in self.traces
        ]


def _default_sql_connector(connection_plan: Any, password: str, timeout_ms: int) -> Any:
    timeout_seconds = max(0.1, min(120.0, timeout_ms / 1000.0))
    return pymysql.connect(
        host=connection_plan.host,
        port=connection_plan.port,
        user=connection_plan.username,
        password=password,
        database=connection_plan.database_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=timeout_seconds,
        read_timeout=timeout_seconds,
        write_timeout=timeout_seconds,
        ssl={} if connection_plan.ssl_enabled else None,
        autocommit=False,
    )


def _validate_sql_statement(statement: str, mode: str) -> str:
    if not isinstance(statement, str) or not 1 <= len(statement) <= 10_000:
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 语句无效")
    statement = statement.strip()
    if "{{" in statement or "}}" in statement:
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 不允许模板文本")
    if any(marker in statement for marker in _SQL_COMMENT_MARKERS):
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 不允许注释语法")
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement or ";" in statement:
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 仅允许单条语句")
    normalized = re.sub(r"\s+", " ", statement).upper()
    if _SQL_TRANSACTION_PATTERN.search(normalized):
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 不允许事务控制语句")
    if any(re.search(pattern, normalized) for pattern in _SQL_DANGEROUS_PATTERNS):
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 包含禁止的危险操作")
    if mode == "QUERY":
        if not _SQL_QUERY_START.match(normalized):
            raise ScenarioRuntimeError("SQL_QUERY_UNSAFE", "SQL_QUERY 仅允许只读查询")
        if re.search(
            r"\b(?:INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|TRUNCATE|CREATE|"
            r"RENAME|GRANT|REVOKE|USE|SET|KILL|HANDLER|INTO)\b",
            normalized,
        ):
            raise ScenarioRuntimeError("SQL_QUERY_UNSAFE", "SQL_QUERY 仅允许只读查询")
        if normalized.startswith("WITH") and re.search(
            r"\b(?:INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|TRUNCATE|CREATE)\b", normalized
        ):
            raise ScenarioRuntimeError("SQL_QUERY_UNSAFE", "SQL_QUERY 仅允许只读查询")
    elif mode == "EXECUTE":
        keyword = normalized.split(" ", 1)[0]
        if keyword not in {"INSERT", "UPDATE", "DELETE"} or _SQL_MUTATION_PATTERN.search(
            normalized
        ):
            raise ScenarioRuntimeError("SQL_EXECUTE_UNSAFE", "SQL_EXECUTE 语句不受支持")
    elif mode == "CLEANUP":
        keyword = normalized.split(" ", 1)[0]
        if keyword not in {"UPDATE", "DELETE"} or _SQL_MUTATION_PATTERN.search(normalized):
            raise ScenarioRuntimeError("SQL_CLEANUP_UNSAFE", "SQL_CLEANUP 语句不受支持")
    else:
        raise ScenarioRuntimeError("SQL_CONFIG_INVALID", "SQL 模式无效")
    return statement


def _is_sql_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    code = exc.args[0] if getattr(exc, "args", None) else None
    if isinstance(code, str) and code.isdigit():
        code = int(code)
    text = str(exc).lower()
    return code in {2013, 3024} or any(
        marker in text for marker in ("timed out", "timeout", "read timeout", "write timeout")
    )


def _sql_error_code(exc: BaseException) -> int | None:
    code = exc.args[0] if getattr(exc, "args", None) else None
    if isinstance(code, str) and code.isdigit():
        code = int(code)
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _is_sql_retryable(exc: BaseException, mode: str) -> bool:
    """Classify retries without replaying an uncertain write or cleanup."""
    code = _sql_error_code(exc)
    if mode == "QUERY":
        return isinstance(exc, OSError) or code in _SQL_TRANSIENT_CODES
    if mode in {"EXECUTE", "CLEANUP"}:
        return code in {1205, 1213}
    return False


def _safe_rollback(connection: Any | None) -> None:
    if connection is None:
        return
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 - cleanup boundary must stay silent
            pass


def _safe_close(resource: Any | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - cleanup boundary must stay silent
            pass


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        result = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("非有限浮点值")
        result = value
    elif isinstance(value, (datetime, date)):
        result = value.isoformat()
    elif isinstance(value, Decimal):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("非有限 Decimal")
    elif isinstance(value, bytes):
        result = value.decode("utf-8", errors="replace")
    elif isinstance(value, Mapping):
        if len(value) > 1_000:
            raise ValueError("对象字段过多")
        result = {str(key): _json_safe_value(child) for key, child in value.items()}
    elif isinstance(value, (list, tuple)):
        if len(value) > 1_000:
            raise ValueError("数组元素过多")
        result = [_json_safe_value(child) for child in value]
    else:
        raise ValueError("SQL 结果包含不支持的类型")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_JSON_VALUE_BYTES:
        raise ValueError("SQL 结果字段过大")
    return result


def _validate_context_size(context: Mapping[str, Any]) -> None:
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise ValueError("Runtime Context 超过大小限制")


def _cleanup_policy_matches(policy: str, outcome: str) -> bool:
    if policy == "ALWAYS":
        return True
    if policy == "ON_SUCCESS":
        return outcome == "SUCCESS"
    if policy == "ON_FAILURE":
        return outcome in {"FAILURE", "CANCELLED", "TIMEOUT"}
    return False


def _lookup(context: Mapping[str, Any], name: str) -> Any:
    if name in context:
        return context[name]
    current: Any = context
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(name)
        current = current[part]
    return current


def _stringify_request_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        raise ProtocolError("HTTP 参数值不能为 null")
    return str(value)


def _variable_name(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", value))


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "==":
        return type(left) is type(right) and left == right
    if operator == "!=":
        return not (type(left) is type(right) and left == right)
    if operator == "contains":
        if isinstance(left, (str, list, tuple, dict)):
            return right in left
        raise ScenarioRuntimeError("CONDITION_TYPE_ERROR", "IF 条件类型不支持 contains")
    try:
        if type(left) is not type(right) or isinstance(left, bool):
            raise ScenarioRuntimeError("CONDITION_TYPE_ERROR", "IF 条件两侧类型不一致")
        return {
            ">": left > right,
            ">=": left >= right,
            "<": left < right,
            "<=": left <= right,
        }[operator]
    except KeyError as exc:
        raise ScenarioRuntimeError("CONDITION_CONFIG_INVALID", "IF 运算符无效") from exc
    except TypeError as exc:
        raise ScenarioRuntimeError("CONDITION_TYPE_ERROR", "IF 条件类型不支持比较") from exc
