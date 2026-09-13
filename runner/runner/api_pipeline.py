"""Bounded API_CASE pre-action runtime executed inside the API child process."""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from runner.errors import (
    ExecutionError,
    ProtocolError,
    TargetExecutionError,
    TargetTimeoutError,
)
from runner.executors.api import ApiExecutionResult, ApiExecutor, ApiRequestTemplate
from runner.models import ScenarioExecutionPlanResult
from runner.runtime_values import case_insensitive_get, jsonpath_get
from runner.safe_script import SafeScriptBudgetExceeded, SafeScriptError, run_safe_script
from runner.scenario import ScenarioExecutor

_TEMPLATE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,254}$")
_SENSITIVE_NAME = re.compile(
    r"(?i)(authorization|cookie|password|passwd|token|secret|credential|api[_-]?key)"
)
_MAX_CONTEXT_BYTES = 64_000


@dataclass(frozen=True, repr=False)
class ApiPipelineExecutionResult:
    response: ApiExecutionResult
    resources: tuple[Mapping[str, Any], ...]
    action_traces: tuple[Mapping[str, Any], ...] = ()
    retry_count: int = 0
    cleanup_pending: bool = False
    cleanup_context: Mapping[str, Any] | None = None

    @property
    def status_code(self) -> int:
        return self.response.status_code

    def to_wire(self) -> dict[str, Any]:
        return {
            "response": self.response.to_wire(),
            "resources": [dict(item) for item in self.resources],
            "action_traces": [dict(item) for item in self.action_traces],
            "retry_count": self.retry_count,
        }

    def to_worker_wire(self) -> dict[str, Any]:
        value = self.to_wire()
        value["cleanup_pending"] = self.cleanup_pending
        if self.cleanup_pending:
            value["cleanup_context"] = deepcopy(dict(self.cleanup_context or {}))
        return value


@dataclass(frozen=True, repr=False)
class ApiPipelineCleanupTask:
    plan: object
    context: Mapping[str, Any]
    resources: tuple[Mapping[str, Any], ...]
    outcome: str

    def __repr__(self) -> str:
        return (
            "ApiPipelineCleanupTask("
            f"resources={len(self.resources)}, outcome={self.outcome!r}, "
            "context=<redacted>)"
        )


@dataclass(frozen=True)
class ApiPipelineCleanupResult:
    resources: tuple[Mapping[str, Any], ...]

    def to_wire(self) -> dict[str, Any]:
        return {"resources": [dict(item) for item in self.resources]}


class ApiCasePipelineExecutor:
    """Apply reviewed Pre Actions, render one request, then use the existing HTTP executor."""

    def __init__(
        self,
        plan: object,
        api_executor: ApiExecutor | None = None,
        sql_connector: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.plan: Any = plan
        self._api_executor = api_executor or ApiExecutor()
        self._owns_executor = api_executor is None
        self._sql_connector = sql_connector
        self._sleeper = sleeper
        self.context = deepcopy(dict(getattr(plan, "initial_context", None) or {}))
        self.resources: list[dict[str, Any]] = []
        self.action_traces: list[dict[str, Any]] = []
        self.retry_count = 0

    def execute(self) -> ApiPipelineExecutionResult:
        primary_error: Exception | None = None
        response: ApiExecutionResult | None = None
        try:
            try:
                for sequence, action in enumerate(getattr(self.plan, "pre_actions", ()), start=1):
                    self._execute_traced_action("PRE", sequence, action)
                response = self._execute_target_with_retries()
                for sequence, extractor in enumerate(getattr(self.plan, "extractors", ()), start=1):
                    self._execute_traced_action("EXTRACTOR", sequence, extractor, response=response)
                for sequence, action in enumerate(getattr(self.plan, "post_actions", ()), start=1):
                    self._execute_traced_action("POST", sequence, action, response=response)
            except Exception as exc:  # noqa: BLE001 - cleanup must follow every primary outcome
                primary_error = exc
                primary_error.retry_count = self.retry_count  # type: ignore[attr-defined]
            cleanup_outcome = (
                "TIMEOUT"
                if getattr(primary_error, "error_type", None)
                in {
                    "TARGET_TIMEOUT",
                    "TOTAL_TIMEOUT",
                }
                else "FAILURE"
                if primary_error is not None
                else "SUCCESS"
            )
            if primary_error is None and getattr(
                self.plan, "defer_cleanup_until_assertions", False
            ):
                if response is None:
                    raise ExecutionError("API 未产生响应", error_type="EXECUTOR_ERROR")
                return ApiPipelineExecutionResult(
                    response,
                    tuple(self.resources),
                    tuple(self.action_traces),
                    self.retry_count,
                    cleanup_pending=True,
                    cleanup_context=deepcopy(self.context),
                )
            cleanup_error = self._run_cleanups(cleanup_outcome)
            if primary_error is not None:
                primary_error.action_traces = tuple(self.action_traces)  # type: ignore[attr-defined]
                raise primary_error
            if cleanup_error is not None:
                raise ExecutionError(
                    "一个或多个 Cleanup 执行失败",
                    error_type=getattr(cleanup_error, "error_type", None)
                    or "CLEANUP_EXECUTION_ERROR",
                ) from cleanup_error
            if response is None:
                raise ExecutionError("API 未产生响应", error_type="EXECUTOR_ERROR")
            return ApiPipelineExecutionResult(
                response,
                tuple(self.resources),
                tuple(self.action_traces),
                self.retry_count,
            )
        finally:
            if self._owns_executor:
                self._api_executor.close()

    @classmethod
    def execute_cleanup_task(
        cls,
        task: ApiPipelineCleanupTask,
        *,
        api_executor: ApiExecutor | None = None,
        sql_connector: Callable[..., Any] | None = None,
    ) -> ApiPipelineCleanupResult:
        if task.outcome not in {"SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"}:
            raise ProtocolError("API Cleanup outcome 无效")
        runtime = cls(
            task.plan,
            api_executor=api_executor,
            sql_connector=sql_connector,
        )
        runtime.context = deepcopy(dict(task.context))
        runtime.resources = [deepcopy(dict(item)) for item in task.resources]
        try:
            cleanup_error = runtime._run_cleanups(task.outcome)
            if cleanup_error is not None:
                raise ExecutionError(
                    "一个或多个 Cleanup 执行失败",
                    error_type=getattr(cleanup_error, "error_type", None)
                    or "CLEANUP_EXECUTION_ERROR",
                ) from cleanup_error
            return ApiPipelineCleanupResult(tuple(runtime.resources))
        finally:
            if runtime._owns_executor:
                runtime._api_executor.close()

    def _execute_target_with_retries(self) -> ApiExecutionResult:
        """Retry only the rendered target request; Pre/Post/Cleanup stay single-run."""

        policy = self.plan.request.retry_policy
        token_actions = [
            action
            for action in getattr(self.plan, "pre_actions", ())
            if action.get("type") == "GET_TOKEN" and action.get("enabled", True)
        ]
        refresh_used = False
        while True:
            request = self._render_request(self.plan.request)
            failure: TargetExecutionError | TargetTimeoutError | None = None
            try:
                response = self._api_executor.execute(request)
            except TargetTimeoutError as exc:
                failure = exc
                retry_reason = "TARGET_TIMEOUT"
            except TargetExecutionError as exc:
                failure = exc
                retry_reason = exc.error_type or "TARGET_NETWORK_ERROR"
            else:
                if response.status_code == 401 and token_actions and not refresh_used:
                    refresh_used = True
                    for sequence, action in enumerate(token_actions, start=1):
                        self._execute_traced_action("AUTH_REFRESH", sequence, action)
                    request = self._render_request(self.plan.request)
                    try:
                        response = self._api_executor.execute(request)
                    except TargetTimeoutError as exc:
                        failure = exc
                        retry_reason = "TARGET_TIMEOUT"
                    except TargetExecutionError as exc:
                        failure = exc
                        retry_reason = exc.error_type or "TARGET_NETWORK_ERROR"
                    else:
                        retry_reason = "HTTP_5XX" if 500 <= response.status_code <= 599 else None
                else:
                    retry_reason = "HTTP_5XX" if 500 <= response.status_code <= 599 else None

            if (
                self.retry_count >= policy.max_retries
                or retry_reason is None
                or retry_reason not in policy.retry_on
            ):
                if failure is not None:
                    raise failure
                return response

            if policy.backoff_ms:
                self._sleeper(policy.backoff_ms / 1000)
            self.retry_count += 1

    def _run_cleanups(self, outcome: str) -> Exception | None:
        first_error: Exception | None = None
        bound_cleanup_ids: set[str] = set()
        for resource in reversed(self.resources):
            cleanup = resource["cleanup"]
            cleanup_id = cleanup.get("cleanup_id")
            if isinstance(cleanup_id, str):
                bound_cleanup_ids.add(cleanup_id)
            previous_resource_id = self.context.get("resource_id")
            had_resource_id = "resource_id" in self.context
            self.context["resource_id"] = resource["resource_id"]
            try:
                error, status = self._run_one_cleanup(cleanup, outcome)
                resource["status"] = status
                if status == "CLEANED":
                    resource["attempt_count"] = 1
                if error is not None:
                    resource["attempt_count"] = 1
                    resource["error_code"] = getattr(error, "error_type", None) or (
                        "CLEANUP_EXECUTION_ERROR"
                    )
                    resource["error_message"] = "Cleanup 执行失败"
                    first_error = first_error or error
            finally:
                if had_resource_id:
                    self.context["resource_id"] = previous_resource_id
                else:
                    self.context.pop("resource_id", None)
        for cleanup in reversed(tuple(getattr(self.plan, "cleanups", ()))):
            if cleanup.get("cleanup_id") in bound_cleanup_ids:
                continue
            error, _status = self._run_one_cleanup(cleanup, outcome)
            first_error = first_error or error
        return first_error

    def _run_one_cleanup(self, cleanup: object, outcome: str) -> tuple[Exception | None, str]:
        first_error: Exception | None = None
        status = "SKIPPED"
        if not isinstance(cleanup, Mapping):
            return ProtocolError("API Cleanup 必须是对象"), "FAILED"
        policy = cleanup.get("policy", "ALWAYS")
        enabled = cleanup.get("enabled", True)
        if type(enabled) is not bool or policy not in {
            "ALWAYS",
            "ON_SUCCESS",
            "ON_FAILURE",
            "NEVER",
        }:
            return ProtocolError("API Cleanup 配置无效"), "FAILED"
        if not enabled or policy == "NEVER":
            return None, status
        if policy == "ON_SUCCESS" and outcome != "SUCCESS":
            return None, status
        if policy == "ON_FAILURE" and outcome == "SUCCESS":
            return None, status
        runtime = self._scenario_runtime()
        try:
            runtime.run_embedded_cleanup(cleanup)
            status = "CLEANED"
        except Exception as exc:  # noqa: BLE001 - remaining LIFO cleanup must continue
            first_error = exc
            status = "FAILED"
        finally:
            runtime.close()
        return first_error, status

    def _apply_pre_action(self, action: Mapping[str, Any]) -> None:
        if not isinstance(action, Mapping):
            raise ProtocolError("API Pre Action 必须是对象")
        action_type = action.get("type", "SET_VARIABLE")
        enabled = action.get("enabled", True)
        if type(enabled) is not bool:
            raise ProtocolError("API Pre Action enabled 无效")
        if not enabled:
            return
        name = action.get("name")
        if action_type not in {"PYTHON_SCRIPT", "SQL_QUERY"} and (
            not isinstance(name, str) or _VARIABLE.fullmatch(name) is None
        ):
            raise ProtocolError("API Pre Action 变量名无效")
        if action_type == "SET_VARIABLE":
            allowed = {"type", "name", "value", "enabled"}
            if set(action) - allowed or "value" not in action:
                raise ProtocolError("SET_VARIABLE Pre Action 字段无效")
            self.context[name] = self._resolve(action["value"])
        elif action_type == "FAKER":
            allowed = {"type", "name", "generator", "seed", "enabled"}
            if set(action) - allowed:
                raise ProtocolError("FAKER Pre Action 字段无效")
            self.context[name] = _faker_value(action.get("generator"), action.get("seed"))
        elif action_type == "PYTHON_SCRIPT":
            self._apply_script(action)
        elif action_type == "SQL_QUERY":
            self._apply_sql_query(action)
        elif action_type in {"GET_TOKEN", "API_SETUP"}:
            self._apply_get_token(action)
        else:
            raise ProtocolError("API Pre Action 类型尚未开放")
        _validate_context(self.context)

    def _execute_traced_action(
        self,
        phase: str,
        sequence: int,
        action: Mapping[str, Any],
        *,
        response: ApiExecutionResult | None = None,
    ) -> None:
        started = time.monotonic()
        action_type = (
            f"EXTRACT_{action.get('source', 'UNKNOWN')}"
            if phase == "EXTRACTOR"
            else str(action.get("type", "SET_VARIABLE"))
        )
        name = action.get("name")
        enabled = action.get("enabled", True)
        status = "SKIPPED" if enabled is False else "SUCCESS"
        error_type: str | None = None
        try:
            if phase in {"PRE", "AUTH_REFRESH"}:
                if phase == "AUTH_REFRESH":
                    self._apply_get_token(action)
                else:
                    self._apply_pre_action(action)
            elif phase == "EXTRACTOR":
                if response is None:
                    raise ProtocolError("API Extractor 缺少响应")
                self._apply_extractor(action, response)
            elif phase == "POST":
                if response is None:
                    raise ProtocolError("API Post Action 缺少响应")
                self._apply_post_action(action, response)
            else:
                raise ProtocolError("API Action phase 无效")
        except Exception as exc:
            status = "FAILED"
            error_type = getattr(exc, "error_type", None) or type(exc).__name__.upper()
            raise
        finally:
            self.action_traces.append(
                {
                    "sequence": sequence,
                    "phase": phase,
                    "type": action_type,
                    "name": name if isinstance(name, str) else None,
                    "status": status,
                    "duration_ms": min(
                        86_400_000,
                        max(0, round((time.monotonic() - started) * 1000)),
                    ),
                    "error_type": error_type[:100] if error_type else None,
                }
            )

    def _apply_get_token(self, action: Mapping[str, Any]) -> None:
        allowed = {
            "type",
            "name",
            "source",
            "expression",
            "required",
            "default_value",
            "request",
            "enabled",
        }
        if set(action) != allowed or not isinstance(action.get("request"), Mapping):
            raise ProtocolError("API request Pre Action 字段无效")
        request = self._render_request(ApiRequestTemplate.from_mapping(action["request"]))
        response = self._api_executor.execute(request)
        if not 200 <= response.status_code <= 299:
            error_type = (
                "AUTH_REQUEST_REJECTED"
                if action.get("type") == "GET_TOKEN"
                else "SETUP_REQUEST_REJECTED"
            )
            raise ExecutionError(
                f"API 前置请求返回 HTTP {response.status_code}",
                error_type=error_type,
            )
        name = action.get("name")
        if not isinstance(name, str) or _VARIABLE.fullmatch(name) is None:
            raise ProtocolError("API request Pre Action 变量名无效")
        try:
            value = _response_value(action, response)
        except (KeyError, TypeError, ValueError) as exc:
            if action.get("required", True):
                raise ExecutionError(
                    "API 前置请求未匹配响应值",
                    error_type="TOKEN_VALUE_MISSING",
                ) from exc
            value = deepcopy(action.get("default_value"))
        self.context[name] = deepcopy(value)
        _validate_context(self.context)

    def _apply_sql_query(self, action: Mapping[str, Any]) -> None:
        runtime = self._scenario_runtime()
        try:
            runtime.run_embedded_sql_query(
                {key: value for key, value in action.items() if key not in {"type", "enabled"}},
                timeout_ms=min(getattr(self.plan.request, "timeout_ms", 30_000), 120_000),
            )
        finally:
            runtime.close()
        _validate_context(self.context)

    def _scenario_runtime(self) -> ScenarioExecutor:
        runtime_plan = ScenarioExecutionPlanResult(
            schema_version=1,
            run_id=str(getattr(self.plan, "run_id", "api-case")),
            message_id=str(getattr(self.plan, "message_id", "api-case")),
            runner_id=str(getattr(self.plan, "runner_id", "api-case")),
            case_run_id=int(getattr(self.plan, "case_run_id", 1)),
            scenario_id=1,
            scenario_version_id=1,
            total_timeout_ms=int(getattr(self.plan, "total_timeout_ms", 900_000)),
            initial_context=self.context,
            settings={
                "stop_on_failure": True,
                "cleanup_policy": "ALWAYS",
                "max_loop_iterations": 1,
                "initial_variables": [],
            },
            nodes=(),
            sql_connections=tuple(getattr(self.plan, "sql_connections", ())),
            secrets=tuple(getattr(self.plan, "secrets", ())),
        )
        runtime = ScenarioExecutor(
            runtime_plan,
            api_executor=self._api_executor,
            sql_connector=self._sql_connector,
        )
        runtime.context = self.context
        return runtime

    def _apply_extractor(self, extractor: Mapping[str, Any], response: ApiExecutionResult) -> None:
        if not isinstance(extractor, Mapping):
            raise ProtocolError("API Response Extractor 必须是对象")
        enabled = extractor.get("enabled", True)
        if type(enabled) is not bool:
            raise ProtocolError("API Response Extractor enabled 无效")
        if not enabled:
            return
        name = extractor.get("name")
        if not isinstance(name, str) or _VARIABLE.fullmatch(name) is None:
            raise ProtocolError("API Response Extractor 变量名无效")
        try:
            value = _response_value(extractor, response)
        except (KeyError, TypeError, ValueError) as exc:
            if extractor.get("required", True):
                raise ExecutionError(
                    "Response Extractor 未匹配响应值",
                    error_type="RESPONSE_VALUE_MISSING",
                ) from exc
            value = deepcopy(extractor.get("default_value"))
        self.context[name] = deepcopy(value)
        _validate_context(self.context)

    def _apply_post_action(self, action: Mapping[str, Any], response: ApiExecutionResult) -> None:
        if not isinstance(action, Mapping):
            raise ProtocolError("API Post Action 必须是对象")
        action_type = action.get("type", "SET_VARIABLE")
        if action_type == "EXTRACT_RESPONSE":
            self._apply_extractor(action, response)
            return
        if action_type == "PYTHON_SCRIPT":
            self._apply_script(action)
            return
        if action_type == "REGISTER_RESOURCE":
            self._register_resource(action)
            return
        if action_type != "SET_VARIABLE":
            raise ProtocolError("API Post Action 类型尚未开放")
        enabled = action.get("enabled", True)
        if type(enabled) is not bool:
            raise ProtocolError("API Post Action enabled 无效")
        if not enabled:
            return
        name = action.get("name")
        if (
            set(action) - {"type", "name", "value", "enabled"}
            or "value" not in action
            or not isinstance(name, str)
            or _VARIABLE.fullmatch(name) is None
        ):
            raise ProtocolError("SET_VARIABLE Post Action 字段无效")
        self.context[name] = self._resolve(action["value"])
        _validate_context(self.context)

    def _register_resource(self, action: Mapping[str, Any]) -> None:
        enabled = action.get("enabled", True)
        if type(enabled) is not bool:
            raise ProtocolError("REGISTER_RESOURCE enabled 无效")
        if not enabled:
            return
        allowed = {
            "type",
            "name",
            "resource_type",
            "value",
            "metadata",
            "cleanup",
            "cleanup_ref",
            "enabled",
        }
        name = action.get("name")
        resource_type = action.get("resource_type")
        if (
            set(action) - allowed
            or not isinstance(name, str)
            or _VARIABLE.fullmatch(name) is None
            or not isinstance(resource_type, str)
            or _VARIABLE.fullmatch(resource_type) is None
        ):
            raise ProtocolError("REGISTER_RESOURCE 配置无效")
        inline_cleanup = action.get("cleanup")
        cleanup_ref = action.get("cleanup_ref")
        if inline_cleanup is not None and cleanup_ref is not None:
            raise ProtocolError("REGISTER_RESOURCE Cleanup 只能二选一")
        cleanup = inline_cleanup
        if cleanup_ref is not None:
            matches = [
                item
                for item in getattr(self.plan, "cleanups", ())
                if item.get("cleanup_id") == cleanup_ref
            ]
            if len(matches) != 1:
                raise ProtocolError("REGISTER_RESOURCE Cleanup 引用无效")
            cleanup = matches[0]
        if not isinstance(cleanup, Mapping):
            raise ProtocolError("REGISTER_RESOURCE 必须绑定 Cleanup")
        resource_id = _stringify(self._resolve(action.get("value")))
        if not 1 <= len(resource_id) <= 512:
            raise ProtocolError("REGISTER_RESOURCE resource_id 无效")
        metadata = self._resolve(action.get("metadata", {}))
        if not isinstance(metadata, Mapping):
            raise ProtocolError("REGISTER_RESOURCE metadata 无效")
        resource = {
            "registration_sequence": len(self.resources) + 1,
            "name": name,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "cleanup": dict(cleanup),
            "status": "PENDING",
            "attempt_count": 0,
            "error_code": None,
            "error_message": None,
        }
        self.resources.append(resource)
        runtime_resources = self.context.setdefault("__resources", {})
        if not isinstance(runtime_resources, dict):
            raise ExecutionError(
                "Runtime Context 的 __resources 必须是对象",
                error_type="RUNTIME_CONTEXT_INVALID",
            )
        runtime_resources[name] = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "metadata": dict(metadata),
        }
        _validate_context(self.context)

    def _apply_script(self, action: Mapping[str, Any]) -> None:
        if set(action) != {"type", "script", "enabled"}:
            raise ProtocolError("PYTHON_SCRIPT Action 字段无效")
        enabled = action.get("enabled")
        script = action.get("script")
        if type(enabled) is not bool or not isinstance(script, str):
            raise ProtocolError("PYTHON_SCRIPT Action 配置无效")
        if not enabled:
            return
        try:
            run_safe_script(script, self.context, max_steps=500)
        except SafeScriptBudgetExceeded as exc:
            raise ExecutionError(
                "Python Script 超过受限计算预算", error_type="PYTHON_TIMEOUT"
            ) from exc
        except SafeScriptError as exc:
            raise ExecutionError(
                "Python Script 不符合受限执行规则", error_type="PYTHON_SCRIPT_INVALID"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - evaluator boundary is sanitized
            raise ExecutionError(
                "Python Script 执行失败", error_type="PYTHON_RUNTIME_ERROR"
            ) from exc
        _validate_context(self.context)

    def _render_request(self, request: ApiRequestTemplate) -> ApiRequestTemplate:
        if not isinstance(request, ApiRequestTemplate):
            raise ProtocolError("API pipeline request 无效")
        payload = {
            "method": request.method,
            "url": self._resolve(request.url),
            "query_params": self._render_items(request.query_params),
            "headers": self._render_items(request.headers),
            "cookies": [
                {
                    "name": item.name,
                    "value": _stringify(self._resolve(item.value)),
                    "enabled": item.enabled,
                }
                for item in request.cookies
            ],
            "body": {
                "type": request.body.type,
                "content": self._resolve(request.body.content),
                "content_type": request.body.content_type,
            },
            "auth": {
                "type": request.auth.type,
                "token": self._resolve(request.auth.token),
                "username": request.auth.username,
                "password": self._resolve(request.auth.password),
                "key_name": request.auth.key_name,
                "key_value": self._resolve(request.auth.key_value),
                "placement": request.auth.placement,
            },
            "timeout_ms": request.timeout_ms,
            "follow_redirects": request.follow_redirects,
            "retry_policy": {
                "max_retries": request.retry_policy.max_retries,
                "backoff_ms": request.retry_policy.backoff_ms,
                "retry_on": list(request.retry_policy.retry_on),
            },
        }
        return ApiRequestTemplate.from_mapping(payload)

    def _render_items(self, items: object) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for item in items:  # type: ignore[union-attr]
            value = _stringify(self._resolve(item.value))
            rendered.append({"name": item.name, "value": value, "enabled": item.enabled})
        return rendered

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._resolve(child) for child in value]
        if not isinstance(value, str):
            return value
        full = _TEMPLATE.fullmatch(value)
        if full:
            return deepcopy(_context_value(self.context, full.group(1)))

        def replace(match: re.Match[str]) -> str:
            return _stringify(_context_value(self.context, match.group(1)))

        rendered = _TEMPLATE.sub(replace, value)
        if "{{" in rendered or "}}" in rendered:
            raise ExecutionError("Runtime Context 模板无法解析", error_type="RUNTIME_TEMPLATE")
        return rendered


def _context_value(context: Mapping[str, Any], name: str) -> Any:
    if name not in context:
        raise ExecutionError("Runtime Context 变量未定义", error_type="RUNTIME_TEMPLATE")
    return context[name]


def _response_value(extractor: Mapping[str, Any], response: ApiExecutionResult) -> Any:
    source = extractor.get("source")
    expression = extractor.get("expression")
    if not isinstance(expression, str) or not expression:
        raise ProtocolError("API Response Extractor expression 无效")
    if source == "JSONPATH":
        return jsonpath_get(response.json_body, expression)
    if source == "HEADER":
        return case_insensitive_get(response.headers, expression)
    if source == "COOKIE":
        return case_insensitive_get(response.cookies, expression)
    raise ProtocolError("API Response Extractor source 无效")


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _faker_value(generator: object, seed: object) -> Any:
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ProtocolError("FAKER seed 无效")
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    if generator == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if generator == "email":
        return f"test-{rng.getrandbits(40):010x}@example.test"
    if generator == "username":
        return f"user_{rng.randint(100000, 999999)}"
    if generator == "first_name":
        return rng.choice(("Alice", "Bob", "Carol", "David"))
    if generator == "last_name":
        return rng.choice(("Smith", "Jones", "Taylor", "Brown"))
    if generator == "integer":
        return rng.randint(0, 1_000_000)
    if generator == "word":
        return rng.choice(("alpha", "bravo", "charlie", "delta"))
    if generator == "boolean":
        return bool(rng.getrandbits(1))
    raise ProtocolError("FAKER generator 无效")


def _validate_context(context: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        raise ExecutionError(
            "API Runtime Context 无法序列化", error_type="RUNTIME_CONTEXT_INVALID"
        ) from exc
    if len(encoded.encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise ExecutionError("API Runtime Context 超过 64KB", error_type="RUNTIME_CONTEXT_LIMIT")
