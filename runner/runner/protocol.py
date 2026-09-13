"""Runner 注册/心跳协议客户端。"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

from runner.config import normalize_backend_url
from runner.errors import (
    AuthenticationError,
    ConfigurationError,
    ExecutionError,
    ProtocolError,
    RegistrationOutcomeUnknownError,
    TransportError,
)
from runner.evidence import (
    ARTIFACT_EXTENSIONS,
    ARTIFACT_NAME_PATTERN,
    ValidatedEvidenceArtifact,
    canonical_json,
)
from runner.executors.api import ApiRequestTemplate
from runner.models import (
    ClaimResult,
    ExecutionCancellationResult,
    ExecutionCompleteResult,
    ExecutionEvaluationResult,
    ExecutionPlanResult,
    ExecutionStartResult,
    HeartbeatResult,
    HttpResponse,
    PerformanceCompleteResult,
    PerformanceExecutionPlanResult,
    RegistrationResult,
    RunnerIdentity,
    ScenarioExecutionCompleteResult,
    ScenarioExecutionEvaluationResult,
    ScenarioExecutionPlanResult,
    ScenarioExecutionSecret,
    ScenarioExecutionStartResult,
    ScenarioNodePlan,
    ScenarioSqlConnectionPlan,
    WebEvidenceUploadResult,
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionBrowserConfig,
    WebExecutionCompleteResult,
    WebExecutionEvaluationResult,
    WebExecutionPlanResult,
    WebExecutionProxyConfig,
    WebExecutionSecret,
    WebExecutionStartResult,
    WebExplorationClaimResult,
    WebExplorationCompleteResult,
    WebExplorationControlResult,
    WebExplorationDecisionResult,
    WebExplorationEvidenceUploadResult,
    WebExplorationExecutionPlanResult,
    WebExplorationSession,
    WebExplorationStartResult,
    WebLocatorCandidate,
    WebLocatorPlan,
    WebRecordingClaimResult,
    WebRecordingCompleteResult,
    WebRecordingControlResult,
    WebRecordingExecutionPlanResult,
    WebRecordingExecutionStartResult,
    WebRecordingSession,
    WebSessionConditionPlan,
    WebSessionPlan,
    WebSessionRecoveryPlan,
)
from runner.redaction import redact_text
from runner.snapshot import Snapshot
from runner.transport import TimeoutSettings, Transport

MIN_HEARTBEAT_INTERVAL_SECONDS = 5
MAX_HEARTBEAT_INTERVAL_SECONDS = 3600
_WEB_EXPLORATION_DECISION_READ_TIMEOUT_SECONDS = 630.0
_WEB_TAB_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SCENARIO_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|cookie|token|password|passwd|secret|credential|api[_-]?key)"
)
_SCENARIO_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:authorization|cookie|password|passwd|token|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*\S+)"
)


def clamp_heartbeat_interval(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("服务端 heartbeat_interval_seconds 不是有效整数")
    if value <= 0:
        raise ProtocolError("服务端 heartbeat_interval_seconds 必须大于 0")
    return max(MIN_HEARTBEAT_INTERVAL_SECONDS, min(value, MAX_HEARTBEAT_INTERVAL_SECONDS))


def _parse_datetime(value: object, *, field_name: str = "server_time") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"服务端 {field_name} 缺失或格式无效")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError(f"服务端 {field_name} 格式无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolError(f"服务端 {field_name} 必须包含时区")
    return parsed


def _response_object(response: HttpResponse, *, secrets: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(response.body, Mapping):
        detail = redact_text(response.body, secrets)
        raise ProtocolError(f"服务端响应不是 JSON 对象：{detail}")
    return response.body


def _safe_backend_error_detail(body: object, secrets: tuple[str, ...]) -> str:
    """Keep actionable API errors without persisting request IDs or nested payloads."""

    parts: list[str] = []
    if isinstance(body, Mapping):
        message = body.get("message")
        detail = body.get("detail")
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        elif isinstance(detail, str) and detail.strip():
            parts.append(detail.strip())
        details = body.get("details")
        validation_errors = (
            details.get("validation_errors") if isinstance(details, Mapping) else None
        )
        if isinstance(validation_errors, list):
            parts.extend(
                item.strip()
                for item in validation_errors[:3]
                if isinstance(item, str) and item.strip()
            )
    source = "；".join(parts) if parts else str(body)
    return redact_text(source, secrets, limit=900)


def _required_text(
    payload: Mapping[str, Any], key: str, *, min_length: int = 1, max_length: int = 512
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not min_length <= len(value) <= max_length:
        raise ProtocolError(f"服务端响应缺少有效字段：{key}")
    return value


class RunnerClient:
    """访问 Runner 控制面以及 API_CASE 的最小执行协议。"""

    def __init__(
        self,
        backend_url: str,
        transport: Transport,
        *,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 15.0,
        max_attempts: int = 4,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 8.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend_url = normalize_backend_url(backend_url)
        if not 1 <= max_attempts <= 5:
            raise ConfigurationError("最大请求次数必须在 1 到 5 次之间")
        self._transport = transport
        self._timeout = TimeoutSettings(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
        )
        self._max_attempts = max_attempts
        self._backoff_base = max(0.0, backoff_base_seconds)
        self._backoff_max = max(0.0, backoff_max_seconds)
        self._sleeper = sleeper

    def register(self, snapshot: Snapshot, registration_token: str) -> RegistrationResult:
        if not isinstance(registration_token, str) or not 16 <= len(registration_token) <= 512:
            raise AuthenticationError("Runner Registration Token 无效")
        payload = dict(snapshot)
        payload["registration_token"] = registration_token
        response = self._post(
            f"{self.backend_url}/api/v1/runners/register",
            payload,
            operation="注册",
            secrets=(registration_token,),
            retryable=False,
        )
        body = _response_object(response, secrets=(registration_token,))
        runner_id = _required_text(body, "runner_id", max_length=64)
        credential = _required_text(body, "credential", min_length=16)
        interval = clamp_heartbeat_interval(body.get("heartbeat_interval_seconds"))
        return RegistrationResult(runner_id, credential, interval)

    def heartbeat(
        self,
        identity: RunnerIdentity,
        snapshot: Snapshot,
    ) -> HeartbeatResult:
        if (
            not identity.runner_id
            or len(identity.runner_id) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for character in identity.runner_id
            )
        ):
            raise ConfigurationError("本地 runner_id 无效")
        if not isinstance(identity.credential, str) or not 16 <= len(identity.credential) <= 512:
            raise AuthenticationError("本地 Runner credential 无效")
        endpoint = (
            f"{self.backend_url}/api/v1/runners/{quote(identity.runner_id, safe='')}/heartbeat"
        )
        response = self._post(
            endpoint,
            dict(snapshot),
            operation="心跳",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        runner_id = _required_text(body, "runner_id", max_length=64)
        if runner_id != identity.runner_id:
            raise ProtocolError("服务端返回的 runner_id 与本地身份不一致")
        status = _required_text(body, "status", max_length=16)
        if status != "ACTIVE":
            raise ProtocolError("服务端返回了不可继续心跳的 Runner 状态")
        interval = clamp_heartbeat_interval(body.get("heartbeat_interval_seconds"))
        server_time = _parse_datetime(body.get("server_time"))
        return HeartbeatResult(runner_id, status, interval, server_time)

    def claim(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
    ) -> ClaimResult:
        """向后端声明任务归属；合法 ASSIGNED/CANCELLED 结果可被消费者确认。"""

        self._validate_identity(identity)
        if not _valid_identifier(run_id, max_length=128):
            raise ConfigurationError("本地 run_id 无效")
        if not _valid_identifier(message_id, max_length=64):
            raise ConfigurationError("任务 message_id 无效")
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/claim"
        response = self._post(
            endpoint,
            {"message_id": message_id},
            operation="claim",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        status = _required_text(body, "status", max_length=16)
        claimed_at_value = body.get("claimed_at")
        claimed_at = (
            _parse_datetime(claimed_at_value, field_name="claimed_at")
            if status == "ASSIGNED"
            else None
        )
        idempotent = body.get("idempotent")
        if type(idempotent) is not bool:
            raise ProtocolError("服务端 claim 响应 idempotent 必须是 bool")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
        ):
            raise ProtocolError("服务端 claim 响应与任务身份不一致")
        if status == "ASSIGNED" and claimed_at is None:
            raise ProtocolError("ASSIGNED claim 响应必须包含 claimed_at")
        if status == "CANCELLED" and (claimed_at_value is not None or not idempotent):
            raise ProtocolError("CANCELLED claim 响应必须是 claimed_at=null 且幂等")
        if status not in {"ASSIGNED", "CANCELLED"}:
            raise ProtocolError("服务端 claim 响应状态无效")
        if set(body) != {
            "run_id",
            "message_id",
            "runner_id",
            "status",
            "claimed_at",
            "idempotent",
        }:
            raise ProtocolError("服务端 claim 响应字段不符合协议")
        return ClaimResult(
            response_run_id,
            response_message_id,
            response_runner_id,
            status,
            claimed_at,
            idempotent,
        )

    def get_performance_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
    ) -> PerformanceExecutionPlanResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        endpoint = (
            f"{self.backend_url}/api/v1/performance/runs/{quote(run_id, safe='')}/"
            f"execution-plan?{urlencode({'message_id': message_id})}"
        )
        response = self._request(
            "GET",
            endpoint,
            operation="获取性能执行计划",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        schema_version = body.get("schema_version")
        base_keys = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "request",
            "concurrency",
            "iterations",
            "load_mode",
            "target_rps",
            "duration_seconds",
            "warmup_iterations",
            "request_timeout_ms",
            "total_timeout_ms",
        }
        extended_keys = {
            "target_type",
            "engine",
            "step_stages",
            "cleanup_mode",
            "stream_config",
        }
        if schema_version == 2:
            _require_exact_keys(body, base_keys, "performance execution plan")
        elif schema_version == 3:
            _require_exact_keys(body, base_keys | extended_keys, "performance execution plan")
        else:
            raise ProtocolError("performance execution plan schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        concurrency = _required_positive_int(body.get("concurrency"), "concurrency")
        iterations = _required_positive_int(body.get("iterations"), "iterations")
        load_mode = body.get("load_mode")
        target_rps = body.get("target_rps")
        duration_seconds = body.get("duration_seconds")
        warmup_iterations = body.get("warmup_iterations")
        request_timeout_ms = _required_positive_int(
            body.get("request_timeout_ms"), "request_timeout_ms"
        )
        total_timeout_ms = _required_positive_int(body.get("total_timeout_ms"), "total_timeout_ms")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or concurrency > 100
            or iterations > 10_000
            or type(warmup_iterations) is not int
            or not 0 <= warmup_iterations <= 1_000
            or not 100 <= request_timeout_ms <= 120_000
            or not 1_000 <= total_timeout_ms <= 86_400_000
        ):
            raise ProtocolError("performance execution plan 参数或任务身份无效")
        step_stages: tuple[Mapping[str, int], ...] = ()
        if load_mode == "FIXED_ITERATIONS":
            if iterations < concurrency or target_rps is not None or duration_seconds is not None:
                raise ProtocolError("performance execution plan 固定迭代参数无效")
        elif load_mode == "FIXED_RPS":
            if (
                not isinstance(target_rps, int | float)
                or isinstance(target_rps, bool)
                or not 0 < float(target_rps) <= 10_000
                or type(duration_seconds) is not int
                or not 1 <= duration_seconds <= 3_600
            ):
                raise ProtocolError("performance execution plan 固定 RPS 参数无效")
            target_rps = float(target_rps)
        elif load_mode == "FIXED_CONCURRENCY":
            if (
                target_rps is not None
                or type(duration_seconds) is not int
                or not 1 <= duration_seconds <= 3_600
            ):
                raise ProtocolError("performance execution plan 固定并发参数无效")
        elif load_mode == "STEP_LOAD":
            raw_stages = body.get("step_stages")
            if not isinstance(raw_stages, list) or not 1 <= len(raw_stages) <= 20:
                raise ProtocolError("performance execution plan 阶梯参数无效")
            parsed_stages: list[Mapping[str, int]] = []
            total_stage_seconds = 0
            for stage in raw_stages:
                if not isinstance(stage, Mapping) or set(stage) != {
                    "concurrency",
                    "duration_seconds",
                }:
                    raise ProtocolError("performance execution plan 阶梯参数无效")
                stage_concurrency = stage.get("concurrency")
                stage_duration = stage.get("duration_seconds")
                if (
                    type(stage_concurrency) is not int
                    or not 1 <= stage_concurrency <= 100
                    or type(stage_duration) is not int
                    or not 1 <= stage_duration <= 3_600
                ):
                    raise ProtocolError("performance execution plan 阶梯参数无效")
                total_stage_seconds += stage_duration
                parsed_stages.append(
                    {"concurrency": stage_concurrency, "duration_seconds": stage_duration}
                )
            if (
                total_stage_seconds > 3_600
                or target_rps is not None
                or duration_seconds is not None
            ):
                raise ProtocolError("performance execution plan 阶梯参数无效")
            step_stages = tuple(parsed_stages)
        else:
            raise ProtocolError("performance execution plan load_mode 无效")
        target_type = body.get("target_type", "API_CASE")
        engine = body.get("engine", "PYTHON_HTTP")
        cleanup_mode = body.get("cleanup_mode", "NONE")
        stream_config = body.get("stream_config", {})
        if target_type not in {"API_CASE", "SCENARIO"}:
            raise ProtocolError("performance execution plan target_type 无效")
        if engine not in {"PYTHON_HTTP", "PYTHON_STREAM", "JMETER"}:
            raise ProtocolError("performance execution plan engine 无效")
        if cleanup_mode not in {"NONE", "RESOURCE_CLEANUP", "BATCH_CLEANUP"}:
            raise ProtocolError("performance execution plan cleanup_mode 无效")
        if not isinstance(stream_config, Mapping):
            raise ProtocolError("performance execution plan stream_config 无效")
        request = (
            ApiRequestTemplate.from_mapping(body.get("request"))
            if target_type == "API_CASE"
            else None
        )
        if target_type == "SCENARIO" and body.get("request") is not None:
            raise ProtocolError("Scenario 性能计划不能包含单请求模板")
        return PerformanceExecutionPlanResult(
            schema_version,
            response_run_id,
            response_message_id,
            response_runner_id,
            case_run_id,
            request,
            concurrency,
            iterations,
            load_mode,
            target_rps,
            duration_seconds,
            warmup_iterations,
            request_timeout_ms,
            total_timeout_ms,
            target_type,
            engine,
            step_stages,
            cleanup_mode,
            dict(stream_config),
        )

    def performance_complete(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        wall_duration_ms: int,
        samples: list[Mapping[str, object]],
    ) -> PerformanceCompleteResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if not 1 <= wall_duration_ms <= 86_400_000 or not 1 <= len(samples) <= 10_000:
            raise ConfigurationError("性能执行结果范围无效")
        endpoint = f"{self.backend_url}/api/v1/performance/runs/{quote(run_id, safe='')}/complete"
        response = self._post(
            endpoint,
            {
                "message_id": message_id,
                "case_run_id": case_run_id,
                "wall_duration_ms": wall_duration_ms,
                "samples": [dict(item) for item in samples],
            },
            operation="完成性能执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {"run_id", "status", "metrics", "sla_results", "error_distribution", "idempotent"},
            "performance complete",
        )
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_status = _required_text(body, "status", max_length=16)
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if response_run_id != run_id or response_status not in {"SUCCESS", "FAILED"}:
            raise ProtocolError("performance complete 响应与任务不一致")
        if not isinstance(body.get("metrics"), Mapping):
            raise ProtocolError("performance complete metrics 无效")
        if not isinstance(body.get("sla_results"), list) or not isinstance(
            body.get("error_distribution"), Mapping
        ):
            raise ProtocolError("performance complete SLA 或错误分布无效")
        return PerformanceCompleteResult(response_run_id, response_status, idempotent)

    def get_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> ExecutionPlanResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        if case_run_id is not None:
            _validate_case_run_id(case_run_id)
        requested_case_run_id = case_run_id
        query = {"message_id": message_id}
        if case_run_id is not None:
            query["case_run_id"] = str(case_run_id)
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/execution-plan?"
            f"{urlencode(query)}"
        )
        response = self._request(
            "GET",
            endpoint,
            operation="获取执行计划",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        required_plan_keys = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "case_version_id",
            "request",
            "total_timeout_ms",
        }
        optional_plan_keys = {
            "initial_context",
            "pre_actions",
            "extractors",
            "post_actions",
            "sql_connections",
            "secrets",
            "cleanups",
            "defer_cleanup_until_assertions",
        }
        if (
            not required_plan_keys.issubset(body)
            or set(body) - required_plan_keys - optional_plan_keys
        ):
            raise ProtocolError("execution plan 响应字段不符合协议")
        schema_version = body.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise ProtocolError("execution plan schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        case_version_id = _required_positive_int(body.get("case_version_id"), "case_version_id")
        total_timeout_ms = body.get("total_timeout_ms")
        if (
            isinstance(total_timeout_ms, bool)
            or not isinstance(total_timeout_ms, int)
            or not 1000 <= total_timeout_ms <= 86_400_000
        ):
            raise ProtocolError("execution plan total_timeout_ms 无效")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or (requested_case_run_id is not None and requested_case_run_id != response_case_run_id)
        ):
            raise ProtocolError("execution plan 与任务身份不一致")
        try:
            request = ApiRequestTemplate.from_mapping(body.get("request"))
        except ExecutionError as exc:
            raise ProtocolError("execution plan request 包含当前阶段不支持的认证") from exc
        initial_context = _scenario_context(body.get("initial_context", {}))
        pre_actions = _api_pre_actions(body.get("pre_actions", []))
        extractors = _api_extractors(body.get("extractors", []))
        post_actions = _api_post_actions(body.get("post_actions", []))
        connections = _scenario_sql_connections(body.get("sql_connections", []))
        secrets = _scenario_secrets(body.get("secrets", []))
        cleanups = _api_cleanups(body.get("cleanups", []))
        defer_cleanup = body.get("defer_cleanup_until_assertions", False)
        if type(defer_cleanup) is not bool:
            raise ProtocolError("execution plan defer_cleanup_until_assertions 无效")
        if defer_cleanup and not cleanups:
            raise ProtocolError("execution plan 延迟 Cleanup 标记与计划不一致")
        if initial_context and not (pre_actions or extractors or post_actions):
            raise ProtocolError("execution plan Runtime pipeline 与 initial_context 不匹配")
        return ExecutionPlanResult(
            schema_version,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            case_version_id,
            request,
            total_timeout_ms,
            initial_context,
            tuple(pre_actions),
            tuple(extractors),
            tuple(post_actions),
            tuple(connections),
            tuple(secrets),
            tuple(cleanups),
            defer_cleanup,
        )

    def get_scenario_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> ScenarioExecutionPlanResult:
        """Fetch the fixed, secret-bound Scenario plan using its protected route."""

        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        if case_run_id is not None:
            _validate_case_run_id(case_run_id)
        query_values: dict[str, object] = {"message_id": message_id}
        if case_run_id is not None:
            query_values["case_run_id"] = case_run_id
        query = urlencode(query_values)
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/"
            f"scenario-execution-plan?{query}"
        )
        response = self._request(
            "GET",
            endpoint,
            operation="获取 Scenario 执行计划",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "scenario_id",
            "scenario_version_id",
            "total_timeout_ms",
            "initial_context",
            "settings",
            "nodes",
            "sql_connections",
            "secrets",
        }
        _require_exact_keys(body, expected, "Scenario execution plan")
        if body.get("schema_version") != 1 or type(body.get("schema_version")) is not int:
            raise ProtocolError("Scenario execution plan schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        scenario_id = _required_positive_int(body.get("scenario_id"), "scenario_id")
        scenario_version_id = _required_positive_int(
            body.get("scenario_version_id"), "scenario_version_id"
        )
        total_timeout_ms = body.get("total_timeout_ms")
        if type(total_timeout_ms) is not int or not 1000 <= total_timeout_ms <= 86_400_000:
            raise ProtocolError("Scenario execution plan total_timeout_ms 无效")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or (case_run_id is not None and response_case_run_id != case_run_id)
        ):
            raise ProtocolError("Scenario execution plan 与任务身份不一致")
        initial_context = _scenario_context(body.get("initial_context"))
        settings = _scenario_settings(body.get("settings"))
        nodes = _scenario_nodes(body.get("nodes"))
        connections = _scenario_sql_connections(body.get("sql_connections"))
        secrets = _scenario_secrets(body.get("secrets"))
        return ScenarioExecutionPlanResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            scenario_id,
            scenario_version_id,
            total_timeout_ms,
            initial_context,
            settings,
            tuple(nodes),
            tuple(connections),
            tuple(secrets),
        )

    def get_web_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> WebExecutionPlanResult:
        """Fetch the fixed Web plan.  The optional case_run_id is a compatibility hint."""

        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        if case_run_id is not None:
            _validate_case_run_id(case_run_id)
        query: dict[str, object] = {"message_id": message_id}
        if case_run_id is not None:
            query["case_run_id"] = case_run_id
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/"
            f"web-execution-plan?{urlencode(query)}"
        )
        response = self._request(
            "GET",
            endpoint,
            operation="获取 Web 执行计划",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "web_case_id",
            "web_case_version_id",
            "start_url",
            "browser",
            "headless",
            "total_timeout_ms",
            "initial_context",
            "actions",
            "assertions",
            "session",
        }
        if frozenset(body) not in {
            frozenset(expected),
            frozenset(expected | {"secrets"}),
            frozenset(expected | {"browser_config"}),
            frozenset(expected | {"secrets", "browser_config"}),
        }:
            raise ProtocolError("Web execution plan 字段不符合协议")
        if type(body.get("schema_version")) is not int or body.get("schema_version") != 1:
            raise ProtocolError("Web execution plan schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        web_case_id = _required_positive_int(body.get("web_case_id"), "web_case_id")
        web_case_version_id = _required_positive_int(
            body.get("web_case_version_id"), "web_case_version_id"
        )
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or (case_run_id is not None and response_case_run_id != case_run_id)
        ):
            raise ProtocolError("Web execution plan 与任务身份不一致")
        start_url = _required_text(body, "start_url", max_length=2048)
        browser = _required_text(body, "browser", max_length=16)
        if browser != "CHROME":
            raise ProtocolError("Web execution plan browser 不受支持")
        headless = _required_bool(body.get("headless"), "headless")
        total_timeout_ms = body.get("total_timeout_ms")
        if type(total_timeout_ms) is not int or not 1_000 <= total_timeout_ms <= 86_400_000:
            raise ProtocolError("Web execution plan total_timeout_ms 无效")
        initial_context = _web_context(body.get("initial_context"))
        actions = _web_actions(body.get("actions"))
        assertions = _web_assertions(body.get("assertions"))
        session = _web_session(body.get("session"))
        secrets = _web_secrets(body.get("secrets", []))
        browser_config = _web_browser_config(body.get("browser_config"))
        return WebExecutionPlanResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            web_case_id,
            web_case_version_id,
            start_url,
            browser,
            headless,
            total_timeout_ms,
            initial_context,
            tuple(actions),
            tuple(assertions),
            session,
            tuple(secrets),
            browser_config,
        )

    def web_execution_start(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> WebExecutionStartResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/web-execution-start"
        response = self._post(
            endpoint,
            {"message_id": message_id, "case_run_id": case_run_id},
            operation="开始 Web 执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "status",
            "started_at",
            "idempotent",
        }
        _require_exact_keys(body, expected, "Web execution start")
        if type(body.get("schema_version")) is not int or body.get("schema_version") != 1:
            raise ProtocolError("Web execution start schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        status = _required_text(body, "status", max_length=32)
        if status not in {"RUNNING", "CANCELLING"}:
            raise ProtocolError("Web execution start 响应状态无效")
        started_at_value = body.get("started_at")
        started_at = (
            None
            if started_at_value is None
            else _parse_datetime(started_at_value, field_name="started_at")
        )
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or (status == "RUNNING" and started_at is None)
        ):
            raise ProtocolError("Web execution start 响应与任务身份或状态不一致")
        return WebExecutionStartResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            status,
            started_at,
            idempotent,
        )

    def web_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> WebExecutionPlanResult:
        """Compatibility alias matching the Scenario/API plan methods."""

        return self.get_web_execution_plan(identity, run_id, message_id, case_run_id)

    def web_execution_complete(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        outcome: str,
        traces: list[Mapping[str, Any]],
        error_type: str | None = None,
        error_message: str | None = None,
        refreshed_session: Mapping[str, Any] | None = None,
        session_recovery: Mapping[str, Any] | None = None,
        evaluation_token: str | None = None,
    ) -> WebExecutionCompleteResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if outcome not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ConfigurationError("Web complete outcome 无效")
        wire_traces = _web_trace_payload(traces)
        if error_type is not None and (
            not isinstance(error_type, str) or not 1 <= len(error_type) <= 100
        ):
            raise ConfigurationError("Web complete error_type 无效")
        if error_message is not None and (
            not isinstance(error_message, str) or not 1 <= len(error_message) <= 500
        ):
            raise ConfigurationError("Web complete error_message 无效")
        if outcome == "SUCCESS" and (error_type is not None or error_message is not None):
            raise ConfigurationError("Web SUCCESS 不能携带错误字段")
        if outcome == "CANCELLED" and (error_type != "CANCEL_REQUESTED" or not error_message):
            raise ConfigurationError("Web CANCELLED 必须携带安全取消信息")
        if outcome in {"FAILED", "TIMEOUT"} and (not error_type or not error_message):
            raise ConfigurationError("Web 失败类 outcome 必须携带安全错误信息")
        refreshed_payload = _web_refreshed_session_payload(refreshed_session)
        recovery_payload = _web_session_recovery_audit_payload(session_recovery, response=False)
        if refreshed_payload is not None and (
            recovery_payload is None or recovery_payload["status"] != "SUCCESS"
        ):
            raise ConfigurationError("Web Session 刷新缺少成功恢复审计")
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/web-execution-complete"
        payload = {
            "message_id": message_id,
            "case_run_id": case_run_id,
            "outcome": outcome,
            "traces": wire_traces,
            "error_type": error_type,
            "error_message": error_message,
        }
        if refreshed_payload is not None:
            payload["refreshed_session"] = refreshed_payload
        if recovery_payload is not None:
            payload["session_recovery"] = recovery_payload
        if evaluation_token is not None:
            if not isinstance(evaluation_token, str) or not 1 <= len(evaluation_token) <= 64_000:
                raise ConfigurationError("Web evaluation_token 无效")
            payload["evaluation_token"] = evaluation_token
        sensitive_values = tuple(_nested_string_values(refreshed_payload))
        response = self._post(
            endpoint,
            payload,
            operation="完成 Web 执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential, *sensitive_values),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "outcome",
            "run_status",
            "case_run_status",
            "traces",
            "error_type",
            "error_message",
            "completed_at",
            "idempotent",
            "assertion_results",
        }
        if frozenset(body) not in {
            frozenset(expected),
            frozenset(expected | {"session_recovery"}),
            frozenset(expected - {"assertion_results"}),
            frozenset((expected - {"assertion_results"}) | {"session_recovery"}),
        }:
            raise ProtocolError("Web execution complete 字段不符合协议")
        if type(body.get("schema_version")) is not int or body.get("schema_version") != 1:
            raise ProtocolError("Web execution complete schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        response_outcome = _required_text(body, "outcome", max_length=32)
        run_status = _required_text(body, "run_status", max_length=32)
        case_run_status = _required_text(body, "case_run_status", max_length=32)
        response_traces = _web_trace_payload(body.get("traces"))
        response_error_type = _optional_text(body.get("error_type"), "error_type", max_length=100)
        response_error_message = _optional_text(
            body.get("error_message"), "error_message", max_length=500
        )
        completed_at = _parse_datetime(body.get("completed_at"), field_name="completed_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        response_recovery = _web_session_recovery_audit_payload(
            body.get("session_recovery"), response=True
        )
        assertion_results = tuple(_parse_assertion_results(body.get("assertion_results", [])))
        statuses = {
            "SUCCESS": {"SUCCESS"},
            "FAILED": {"FAILED"},
            "TIMEOUT": {"TIMEOUT"},
            "CANCELLED": {"CANCELLED"},
        }
        cancelled_override = response_outcome == "CANCELLED" and outcome != "CANCELLED"
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or response_outcome not in statuses
            or (response_outcome != outcome and not cancelled_override)
            or run_status not in statuses.get(response_outcome, set())
            or case_run_status
            not in (
                {"FAILED", "REVIEW"}
                if response_outcome == "FAILED"
                else statuses.get(response_outcome, set())
            )
            or (response_outcome != "SUCCESS" and not response_error_type)
            or (response_outcome != "SUCCESS" and not response_error_message)
        ):
            raise ProtocolError("Web execution complete 未返回合法终态")
        if response_outcome == "SUCCESS" and (
            response_error_type is not None or response_error_message is not None
        ):
            raise ProtocolError("Web SUCCESS 响应不能携带错误字段")
        if response_outcome == "CANCELLED" and response_error_type not in {
            "CANCEL_REQUESTED",
            "FORCE_STOP_REQUESTED",
        }:
            raise ProtocolError("Web CANCELLED 响应错误类型无效")
        return WebExecutionCompleteResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            response_outcome,
            run_status,
            case_run_status,
            response_traces,
            response_error_type,
            response_error_message,
            completed_at,
            idempotent,
            response_recovery,
            assertion_results,
        )

    def web_execution_evaluate(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        evaluations: list[Mapping[str, Any]],
    ) -> WebExecutionEvaluationResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if not isinstance(evaluations, list) or not 1 <= len(evaluations) <= 100:
            raise ConfigurationError("Web AI evaluations 必须是有限数组")
        wire: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in evaluations:
            if not isinstance(item, Mapping) or set(item) != {"node_id", "response"}:
                raise ConfigurationError("Web AI evaluation 字段无效")
            node_id = item.get("node_id")
            response = item.get("response")
            if (
                not isinstance(node_id, str)
                or re.fullmatch(r"assertion_[1-9][0-9]{0,2}", node_id) is None
                or node_id in seen
                or not isinstance(response, Mapping)
            ):
                raise ConfigurationError("Web AI evaluation 无效或重复")
            seen.add(node_id)
            wire.append({"node_id": node_id, "response": _execution_response_snapshot(response)})
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/web-execution-evaluate"
        response = self._post(
            endpoint,
            {"message_id": message_id, "case_run_id": case_run_id, "evaluations": wire},
            operation="评估 Web AI 断言",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "assertion_status",
                "assertion_results",
                "node_results",
                "evaluation_token",
            },
            "Web execution evaluate",
        )
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        assertion_status = _required_text(body, "assertion_status", max_length=16)
        raw_nodes = body.get("node_results")
        if not isinstance(raw_nodes, list) or len(raw_nodes) != len(wire):
            raise ProtocolError("Web execution evaluate node_results 无效")
        nodes: list[dict[str, str]] = []
        for expected_item, item in zip(wire, raw_nodes, strict=True):
            if (
                not isinstance(item, Mapping)
                or set(item) != {"node_id", "status"}
                or item.get("node_id") != expected_item["node_id"]
                or item.get("status") not in {"PASS", "FAIL", "REVIEW"}
            ):
                raise ProtocolError("Web execution evaluate node_result 无效")
            nodes.append({"node_id": str(item["node_id"]), "status": str(item["status"])})
        if (
            body.get("schema_version") != 1
            or response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or assertion_status not in {"PASS", "FAIL", "REVIEW"}
        ):
            raise ProtocolError("Web execution evaluate 响应与请求不一致")
        return WebExecutionEvaluationResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            assertion_status,
            tuple(_parse_assertion_results(body.get("assertion_results"))),
            tuple(nodes),
            _required_text(body, "evaluation_token", max_length=64_000),
        )

    def web_recording_claim(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingClaimResult:
        self._validate_recording_identity(identity, recording_id, message_id)
        response = self._recording_request(
            "POST",
            f"{self.backend_url}/api/v1/web-recordings/{quote(recording_id, safe='')}/claim",
            payload={"message_id": message_id},
            operation="认领 Web 录制",
            identity=identity,
        )
        body = self._recording_response_object(response)
        expected = {
            "schema_version",
            "recording_id",
            "message_id",
            "runner_id",
            "status",
            "claimed_at",
            "idempotent",
        }
        _require_exact_keys(body, expected, "Web 录制 claim")
        schema_version = _recording_schema(body)
        response_recording_id = _recording_text(body, "recording_id", 128)
        response_message_id = _recording_message(body, "message_id")
        response_runner_id = _recording_text(body, "runner_id", 64)
        status = _recording_status(body.get("status"))
        claimed_at = _recording_optional_datetime(body.get("claimed_at"), "claimed_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_recording_id != recording_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or (status == "CANCELLED" and claimed_at is not None)
            or (status != "CANCELLED" and claimed_at is None)
        ):
            raise ProtocolError("Web 录制 claim 响应与请求状态不一致")
        return WebRecordingClaimResult(
            schema_version,
            response_recording_id,
            response_message_id,
            response_runner_id,
            status,
            claimed_at,
            idempotent,
        )

    def get_web_recording_execution_plan(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionPlanResult:
        self._validate_recording_identity(identity, recording_id, message_id)
        endpoint = (
            f"{self.backend_url}/api/v1/web-recordings/{quote(recording_id, safe='')}"
            f"/execution-plan?{urlencode({'message_id': message_id})}"
        )
        response = self._recording_request(
            "GET", endpoint, operation="获取 Web 录制计划", identity=identity
        )
        body = self._recording_response_object(response)
        expected = {
            "schema_version",
            "task_type",
            "recording_id",
            "message_id",
            "runner_id",
            "project_id",
            "environment_id",
            "start_url",
            "browser",
            "headless",
            "session",
            "save_session",
            "save_session_name",
            "save_session_expires_at",
        }
        _require_exact_keys(body, expected, "Web 录制 execution plan")
        schema_version = _recording_schema(body)
        response_recording_id = _recording_text(body, "recording_id", 128)
        response_message_id = _recording_message(body, "message_id")
        response_runner_id = _recording_text(body, "runner_id", 64)
        project_id = _required_positive_int(body.get("project_id"), "project_id")
        environment_id = _recording_optional_positive_int(
            body.get("environment_id"), "environment_id"
        )
        task_type = body.get("task_type")
        browser = body.get("browser")
        headless = _required_bool(body.get("headless"), "headless")
        if task_type != "WEB_RECORDING" or browser != "CHROME" or headless:
            raise ProtocolError("Web 录制 execution plan 类型或浏览器不受支持")
        start_url = _recording_plan_url(body.get("start_url"))
        session = _recording_session(body.get("session"))
        save_session = _required_bool(body.get("save_session"), "save_session")
        save_session_name = _recording_optional_text(
            body.get("save_session_name"), "save_session_name", 128
        )
        expires = _recording_optional_datetime(
            body.get("save_session_expires_at"), "save_session_expires_at"
        )
        if save_session and not save_session_name:
            raise ProtocolError("Web 录制保存 Session 配置无效")
        if not save_session and (save_session_name is not None or expires is not None):
            raise ProtocolError("未启用保存 Session 时不能携带保存配置")
        if (
            response_recording_id != recording_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
        ):
            raise ProtocolError("Web 录制 execution plan 与本地身份不一致")
        return WebRecordingExecutionPlanResult(
            schema_version,
            "WEB_RECORDING",
            response_recording_id,
            response_message_id,
            response_runner_id,
            project_id,
            environment_id,
            start_url,
            "CHROME",
            False,
            session,
            save_session,
            save_session_name,
            expires,
        )

    def web_recording_plan(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionPlanResult:
        return self.get_web_recording_execution_plan(identity, recording_id, message_id)

    def get_web_recording_plan(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionPlanResult:
        return self.get_web_recording_execution_plan(identity, recording_id, message_id)

    def web_recording_execution_plan(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionPlanResult:
        return self.get_web_recording_execution_plan(identity, recording_id, message_id)

    def web_recording_execution_start(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionStartResult:
        self._validate_recording_identity(identity, recording_id, message_id)
        endpoint = (
            f"{self.backend_url}/api/v1/web-recordings/{quote(recording_id, safe='')}"
            "/execution-start"
        )
        response = self._recording_request(
            "POST",
            endpoint,
            payload={"message_id": message_id},
            operation="开始 Web 录制",
            identity=identity,
        )
        body = self._recording_response_object(response)
        expected = {
            "schema_version",
            "recording_id",
            "message_id",
            "runner_id",
            "status",
            "started_at",
            "idempotent",
        }
        _require_exact_keys(body, expected, "Web 录制 execution start")
        schema_version = _recording_schema(body)
        response_recording_id = _recording_text(body, "recording_id", 128)
        response_message_id = _recording_message(body, "message_id")
        response_runner_id = _recording_text(body, "runner_id", 64)
        status = _recording_status(body.get("status"))
        started_at = _recording_optional_datetime(body.get("started_at"), "started_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if status == "RUNNING" and started_at is None:
            raise ProtocolError("Web 录制 RUNNING 必须携带 started_at")
        if (
            response_recording_id != recording_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or status not in {"RUNNING", "STOP_REQUESTED"}
        ):
            raise ProtocolError("Web 录制 execution start 响应无效")
        return WebRecordingExecutionStartResult(
            schema_version,
            response_recording_id,
            response_message_id,
            response_runner_id,
            status,
            started_at,
            idempotent,
        )

    def web_recording_start(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingExecutionStartResult:
        return self.web_recording_execution_start(identity, recording_id, message_id)

    def get_web_recording_control(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingControlResult:
        self._validate_recording_identity(identity, recording_id, message_id)
        endpoint = (
            f"{self.backend_url}/api/v1/web-recordings/{quote(recording_id, safe='')}"
            f"/control?{urlencode({'message_id': message_id})}"
        )
        response = self._recording_request(
            "GET", endpoint, operation="查询 Web 录制控制状态", identity=identity
        )
        body = self._recording_response_object(response)
        expected = {
            "schema_version",
            "recording_id",
            "message_id",
            "runner_id",
            "status",
            "stop_requested",
            "cancellation_requested",
        }
        _require_exact_keys(body, expected, "Web 录制 control")
        result = WebRecordingControlResult(
            _recording_schema(body),
            _recording_text(body, "recording_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_status(body.get("status")),
            _required_bool(body.get("stop_requested"), "stop_requested"),
            _required_bool(body.get("cancellation_requested"), "cancellation_requested"),
        )
        if (
            result.recording_id != recording_id
            or result.message_id != message_id
            or result.runner_id != identity.runner_id
        ):
            raise ProtocolError("Web 录制 control 与本地身份不一致")
        if result.cancellation_requested and result.status not in {"STOP_REQUESTED", "CANCELLED"}:
            raise ProtocolError("Web 录制取消状态组合无效")
        return result

    def web_recording_control(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingControlResult:
        return self.get_web_recording_control(identity, recording_id, message_id)

    def claim_web_recording(
        self, identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> WebRecordingClaimResult:
        return self.web_recording_claim(identity, recording_id, message_id)

    def web_recording_execution_complete(
        self,
        identity: RunnerIdentity,
        recording_id: str,
        message_id: str,
        *,
        outcome: str,
        events: list[Mapping[str, Any]],
        error_type: str | None = None,
        error_message: str | None = None,
        storage_state: Mapping[str, Any] | None = None,
    ) -> WebRecordingCompleteResult:
        self._validate_recording_identity(identity, recording_id, message_id)
        if outcome not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ConfigurationError("Web 录制 complete outcome 无效")
        wire_events = _recording_events_payload(events)
        error_type = _safe_recording_error(error_type, identity.credential, max_length=100)
        error_message = _safe_recording_error(error_message, identity.credential, max_length=1000)
        if outcome == "COMPLETED" and (error_type is not None or error_message is not None):
            raise ConfigurationError("Web 录制 COMPLETED 不能携带错误字段")
        if outcome == "FAILED" and (
            not isinstance(error_type, str)
            or not 1 <= len(error_type) <= 100
            or not isinstance(error_message, str)
            or not 1 <= len(error_message) <= 1000
        ):
            raise ConfigurationError("Web 录制 FAILED 必须携带安全错误字段")
        if outcome == "CANCELLED":
            if (
                error_type != "CANCEL_REQUESTED"
                or not isinstance(error_message, str)
                or not error_message
            ):
                raise ConfigurationError("Web 录制 CANCELLED 必须携带安全取消信息")
            if wire_events or storage_state is not None:
                raise ConfigurationError("Web 录制 CANCELLED 不能携带事件或 Session")
        if storage_state is not None:
            if outcome != "COMPLETED" or not isinstance(storage_state, Mapping):
                raise ConfigurationError("Web 录制仅允许在 COMPLETED 保存 Session")
            _safe_json_mapping(storage_state, "storage_state", max_items=100, max_bytes=1_000_000)
        payload: dict[str, Any] = {
            "message_id": message_id,
            "outcome": outcome,
            "events": wire_events,
        }
        if error_type is not None:
            payload["error_type"] = error_type
        if error_message is not None:
            payload["error_message"] = error_message
        if storage_state is not None:
            payload["storage_state"] = dict(storage_state)
        endpoint = (
            f"{self.backend_url}/api/v1/web-recordings/{quote(recording_id, safe='')}"
            "/execution-complete"
        )
        response = self._recording_request(
            "POST",
            endpoint,
            payload=payload,
            operation="完成 Web 录制",
            identity=identity,
        )
        body = self._recording_response_object(response)
        expected = {
            "schema_version",
            "recording_id",
            "message_id",
            "runner_id",
            "outcome",
            "status",
            "event_count",
            "saved_session_profile_id",
            "completed_at",
            "idempotent",
        }
        _require_exact_keys(body, expected, "Web 录制 execution complete")
        schema_version = _recording_schema(body)
        response_recording_id = _recording_text(body, "recording_id", 128)
        response_message_id = _recording_message(body, "message_id")
        response_runner_id = _recording_text(body, "runner_id", 64)
        response_outcome = body.get("outcome")
        response_status = _recording_status(body.get("status"))
        if response_outcome not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ProtocolError("Web 录制 complete outcome 响应无效")
        event_count = body.get("event_count")
        if (
            isinstance(event_count, bool)
            or not isinstance(event_count, int)
            or not 0 <= event_count <= 1000
        ):
            raise ProtocolError("Web 录制 complete event_count 无效")
        saved_profile = _recording_optional_positive_int(
            body.get("saved_session_profile_id"), "saved_session_profile_id"
        )
        completed_at = _recording_optional_datetime(body.get("completed_at"), "completed_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_recording_id != recording_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_outcome != outcome
            or response_status != response_outcome
            or (outcome == "COMPLETED" and saved_profile is None and storage_state is not None)
        ):
            raise ProtocolError("Web 录制 complete 响应与请求不一致")
        return WebRecordingCompleteResult(
            schema_version,
            response_recording_id,
            response_message_id,
            response_runner_id,
            response_outcome,
            response_status,
            event_count,
            saved_profile,
            completed_at,
            idempotent,
        )

    def web_recording_complete(
        self, identity: RunnerIdentity, recording_id: str, message_id: str, **kwargs: Any
    ) -> WebRecordingCompleteResult:
        return self.web_recording_execution_complete(identity, recording_id, message_id, **kwargs)

    def web_exploration_claim(
        self, identity: RunnerIdentity, exploration_id: str, message_id: str
    ) -> WebExplorationClaimResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        response = self._exploration_request(
            "POST",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/claim",
            operation="认领 Web 探索",
            identity=identity,
            payload={"message_id": message_id},
        )
        body = self._exploration_response_object(response)
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "runner_id",
                "status",
                "idempotent",
            },
            "Web 探索 claim",
        )
        result = WebExplorationClaimResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_status(body.get("status")),
            _required_bool(body.get("idempotent"), "idempotent"),
        )
        self._validate_exploration_response_identity(identity, exploration_id, message_id, result)
        return result

    def get_web_exploration_execution_plan(
        self, identity: RunnerIdentity, exploration_id: str, message_id: str
    ) -> WebExplorationExecutionPlanResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        response = self._exploration_request(
            "GET",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/execution-plan?"
            f"{urlencode({'message_id': message_id})}",
            operation="读取 Web 探索计划",
            identity=identity,
        )
        body = self._exploration_response_object(response)
        expected = {
            "schema_version",
            "task_type",
            "exploration_id",
            "message_id",
            "runner_id",
            "project_id",
            "start_url",
            "allowed_origins",
            "permissions",
            "max_steps",
            "headless",
            "objective",
            "planned_steps",
            "expected_outcomes",
            "session",
        }
        required = expected - {"headless", "permissions"}
        optional = {"headless", "permissions", "secrets"}
        if not required.issubset(body) or not set(body).issubset(required | optional):
            raise ProtocolError("Web 探索 execution plan 字段集合不符合协议")
        if body.get("task_type") != "WEB_EXPLORATION":
            raise ProtocolError("Web 探索 task_type 无效")
        allowed_origins = _exploration_text_list(
            body.get("allowed_origins"), "allowed_origins", maximum=20, item_max=2048
        )
        normalized_origins = tuple(_exploration_origin(value) for value in allowed_origins)
        permissions = _exploration_text_list(
            body.get("permissions", ["PAGE_READ"]), "permissions", maximum=6, item_max=64
        )
        allowed_permissions = {
            "PAGE_READ", "MANAGED_LOGIN", "FORM_SUBMIT", "TEST_DATA_CREATE",
            "DIRECT_API_NAVIGATION",
            "SCREENSHOT_CAPTURE",
        }
        if "PAGE_READ" not in permissions or not set(permissions).issubset(allowed_permissions):
            raise ProtocolError("Web 探索 permissions 无效")
        max_steps = body.get("max_steps")
        if type(max_steps) is not int or not 1 <= max_steps <= 50:
            raise ProtocolError("Web 探索 max_steps 无效")
        headless = _required_bool(body.get("headless", True), "headless")
        session = _exploration_session(body.get("session"))
        secrets = _web_secrets(body.get("secrets", []))
        result = WebExplorationExecutionPlanResult(
            _recording_schema(body),
            "WEB_EXPLORATION",
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _required_positive_int(body.get("project_id"), "project_id"),
            _recording_plan_url(body.get("start_url")),
            normalized_origins,
            max_steps,
            _recording_text(body, "objective", 2000),
            _exploration_text_list(
                body.get("planned_steps"), "planned_steps", maximum=50, item_max=2000
            ),
            _exploration_text_list(
                body.get("expected_outcomes"),
                "expected_outcomes",
                maximum=30,
                item_max=2000,
            ),
            session,
            permissions=permissions,
            headless=headless,
            secrets=tuple(secrets),
        )
        self._validate_exploration_response_identity(identity, exploration_id, message_id, result)
        if _web_url_origin(result.start_url) not in result.allowed_origins:
            raise ProtocolError("Web 探索 start_url 不在 allowed_origins 中")
        return result

    def web_exploration_execution_start(
        self, identity: RunnerIdentity, exploration_id: str, message_id: str
    ) -> WebExplorationStartResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        response = self._exploration_request(
            "POST",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/execution-start",
            operation="开始 Web 探索",
            identity=identity,
            payload={"message_id": message_id},
        )
        body = self._exploration_response_object(response)
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "runner_id",
                "status",
                "idempotent",
            },
            "Web 探索 start",
        )
        result = WebExplorationStartResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_status(body.get("status")),
            _required_bool(body.get("idempotent"), "idempotent"),
        )
        self._validate_exploration_response_identity(identity, exploration_id, message_id, result)
        return result

    def get_web_exploration_control(
        self, identity: RunnerIdentity, exploration_id: str, message_id: str
    ) -> WebExplorationControlResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        response = self._exploration_request(
            "GET",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/control?"
            f"{urlencode({'message_id': message_id})}",
            operation="读取 Web 探索控制状态",
            identity=identity,
        )
        body = self._exploration_response_object(response)
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "runner_id",
                "status",
                "stop_requested",
                "cancellation_requested",
            },
            "Web 探索 control",
        )
        result = WebExplorationControlResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_status(body.get("status")),
            _required_bool(body.get("stop_requested"), "stop_requested"),
            _required_bool(body.get("cancellation_requested"), "cancellation_requested"),
        )
        self._validate_exploration_response_identity(identity, exploration_id, message_id, result)
        return result

    def request_web_exploration_decision(
        self,
        identity: RunnerIdentity,
        exploration_id: str,
        message_id: str,
        observation: Mapping[str, Any],
    ) -> WebExplorationDecisionResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        _validate_exploration_observation(observation)
        response = self._exploration_request(
            "POST",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/decision",
            operation="请求 Web 探索决策",
            identity=identity,
            payload={"message_id": message_id, "observation": dict(observation)},
            timeout=TimeoutSettings(
                connect=self._timeout.connect,
                read=max(
                    self._timeout.read,
                    _WEB_EXPLORATION_DECISION_READ_TIMEOUT_SECONDS,
                ),
            ),
        )
        body = self._exploration_response_object(response)
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "sequence",
                "ai_call_id",
                "decision",
            },
            "Web 探索 decision",
        )
        sequence = body.get("sequence")
        ai_call_id = body.get("ai_call_id")
        if type(sequence) is not int or not 1 <= sequence <= 50:
            raise ProtocolError("Web 探索 decision sequence 无效")
        if type(ai_call_id) is not int or ai_call_id < 0:
            raise ProtocolError("Web 探索 decision ai_call_id 无效")
        decision = _validate_exploration_decision(body.get("decision"))
        result = WebExplorationDecisionResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            sequence,
            ai_call_id,
            decision,
        )
        if (
            result.exploration_id != exploration_id
            or result.message_id != message_id
            or result.sequence != observation.get("sequence")
        ):
            raise ProtocolError("Web 探索 decision 与请求不一致")
        return result

    def upload_web_exploration_evidence(
        self,
        identity: RunnerIdentity,
        exploration_id: str,
        message_id: str,
        evidence: ValidatedEvidenceArtifact,
        *,
        sequence: int,
        kind: str,
        label: str,
    ) -> WebExplorationEvidenceUploadResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        if evidence.artifact_type != "SCREENSHOT" or evidence.mime != "image/png":
            raise ConfigurationError("Web 探索只接受已校验的 PNG 截图")
        if type(sequence) is not int or not 1 <= sequence <= 50:
            raise ConfigurationError("Web 探索截图 sequence 无效")
        if kind not in {"CHECKPOINT", "FINAL", "FAILURE"}:
            raise ConfigurationError("Web 探索截图 kind 无效")
        if not isinstance(label, str) or not 1 <= len(label) <= 255:
            raise ConfigurationError("Web 探索截图 label 无效")
        endpoint = (
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/evidence"
        )
        response = self._post_multipart(
            endpoint,
            fields={
                "message_id": message_id,
                "sequence": str(sequence),
                "kind": kind,
                "label": label,
                "artifact_name": evidence.artifact_name,
                "sha256": evidence.sha256,
            },
            file_path=str(evidence.path),
            file_name=evidence.artifact_name + ".png",
            mime="image/png",
            operation="上传 Web 探索截图",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "runner_id",
                "id",
                "sequence",
                "kind",
                "label",
                "file_name",
                "mime",
                "size",
                "sha256",
                "created_at",
                "download_path",
                "idempotent",
            },
            "Web 探索截图上传",
        )
        result = WebExplorationEvidenceUploadResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_text(body, "id", 128),
            _required_positive_int(body.get("sequence"), "sequence"),
            _recording_text(body, "kind", 16),
            _recording_text(body, "label", 255),
            _recording_text(body, "file_name", 255),
            _recording_text(body, "mime", 32),
            _required_positive_int(body.get("size"), "size"),
            _recording_text(body, "sha256", 64),
            _parse_datetime(body.get("created_at"), field_name="created_at"),
            _recording_text(body, "download_path", 512),
            _required_bool(body.get("idempotent"), "idempotent"),
        )
        self._validate_exploration_response_identity(
            identity, exploration_id, message_id, result
        )
        if (
            result.sequence != sequence
            or result.kind != kind
            or result.label != label
            or result.file_name != evidence.artifact_name + ".png"
            or result.mime != evidence.mime
            or result.size != evidence.size
            or result.sha256 != evidence.sha256
        ):
            raise ProtocolError("Web 探索截图响应与上传文件不一致")
        return result

    def web_exploration_execution_complete(
        self,
        identity: RunnerIdentity,
        exploration_id: str,
        message_id: str,
        *,
        outcome: str,
        observations: list[Mapping[str, Any]],
        action_trace: list[Mapping[str, Any]],
        result_summary: Mapping[str, Any] | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> WebExplorationCompleteResult:
        self._validate_exploration_identity(identity, exploration_id, message_id)
        if outcome not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ConfigurationError("Web 探索 outcome 无效")
        if len(observations) > 50 or len(action_trace) > 50:
            raise ConfigurationError("Web 探索结果数量超过上限")
        for observation in observations:
            _validate_exploration_observation(observation)
        safe_error_type = _safe_recording_error(error_type, identity.credential, max_length=100)
        safe_error_message = _safe_recording_error(
            error_message, identity.credential, max_length=1000
        )
        if outcome == "COMPLETED" and (safe_error_type or safe_error_message):
            raise ConfigurationError("Web 探索 COMPLETED 不能携带错误")
        if outcome == "FAILED" and (not safe_error_type or not safe_error_message):
            raise ConfigurationError("Web 探索 FAILED 必须携带安全错误")
        payload: dict[str, Any] = {
            "message_id": message_id,
            "outcome": outcome,
            "observations": [dict(item) for item in observations],
            "action_trace": [dict(item) for item in action_trace],
            "result_summary": dict(result_summary) if result_summary else None,
            "error_type": safe_error_type,
            "error_message": safe_error_message,
        }
        response = self._exploration_request(
            "POST",
            f"{self.backend_url}/api/v1/web-design/explorations/"
            f"{quote(exploration_id, safe='')}/execution-complete",
            operation="完成 Web 探索",
            identity=identity,
            payload=payload,
        )
        body = self._exploration_response_object(response)
        _require_exact_keys(
            body,
            {
                "schema_version",
                "exploration_id",
                "message_id",
                "runner_id",
                "status",
                "idempotent",
            },
            "Web 探索 complete",
        )
        result = WebExplorationCompleteResult(
            _recording_schema(body),
            _recording_text(body, "exploration_id", 128),
            _recording_message(body, "message_id"),
            _recording_text(body, "runner_id", 64),
            _recording_status(body.get("status")),
            _required_bool(body.get("idempotent"), "idempotent"),
        )
        self._validate_exploration_response_identity(identity, exploration_id, message_id, result)
        if result.status != outcome:
            raise ProtocolError("Web 探索 complete 状态与请求不一致")
        return result

    def _exploration_request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        identity: RunnerIdentity,
        payload: Mapping[str, Any] | None = None,
        timeout: TimeoutSettings | None = None,
    ) -> HttpResponse:
        try:
            return self._request(
                method,
                url,
                payload=payload,
                operation=operation,
                headers={"Authorization": f"Bearer {identity.credential}"},
                secrets=(identity.credential,),
                timeout=timeout,
            )
        except ProtocolError as exc:
            detail = redact_text(exc, (identity.credential,), limit=800)
            raise ProtocolError(f"Web 探索 {operation} 请求被后端拒绝：{detail}") from exc

    @staticmethod
    def _exploration_response_object(response: HttpResponse) -> Mapping[str, Any]:
        if not isinstance(response.body, Mapping):
            raise ProtocolError("Web 探索服务端响应不是 JSON 对象")
        return response.body

    @staticmethod
    def _validate_exploration_identity(
        identity: RunnerIdentity, exploration_id: str, message_id: str
    ) -> None:
        RunnerClient._validate_identity(identity)
        if not isinstance(exploration_id, str) or not 1 <= len(exploration_id) <= 128:
            raise ConfigurationError("Web 探索 exploration_id 无效")
        if not _valid_identifier(message_id, max_length=64):
            raise ConfigurationError("Web 探索 message_id 无效")

    @staticmethod
    def _validate_exploration_response_identity(
        identity: RunnerIdentity,
        exploration_id: str,
        message_id: str,
        result: object,
    ) -> None:
        if (
            getattr(result, "exploration_id", None) != exploration_id
            or getattr(result, "message_id", None) != message_id
            or getattr(result, "runner_id", None) != identity.runner_id
        ):
            raise ProtocolError("Web 探索响应与本机身份不一致")

    def _recording_request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        identity: RunnerIdentity,
        payload: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        try:
            return self._request(
                method,
                url,
                payload=payload,
                operation=operation,
                headers={"Authorization": f"Bearer {identity.credential}"},
                secrets=(identity.credential,),
            )
        except ProtocolError as exc:
            raise ProtocolError(f"Web 录制 {operation} 请求被后端拒绝") from exc

    @staticmethod
    def _recording_response_object(response: HttpResponse) -> Mapping[str, Any]:
        if not isinstance(response.body, Mapping):
            raise ProtocolError("Web 录制服务端响应不是 JSON 对象")
        return response.body

    @staticmethod
    def _validate_recording_identity(
        identity: RunnerIdentity, recording_id: str, message_id: str
    ) -> None:
        RunnerClient._validate_identity(identity)
        if not isinstance(recording_id, str) or not 1 <= len(recording_id) <= 128:
            raise ConfigurationError("Web 录制 recording_id 无效")
        if not _valid_identifier(message_id, max_length=64):
            raise ConfigurationError("Web 录制 message_id 无效")

    def upload_web_evidence(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        evidence: ValidatedEvidenceArtifact,
    ) -> WebEvidenceUploadResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if not isinstance(evidence, ValidatedEvidenceArtifact):
            raise ConfigurationError("Web Evidence 文件未通过本地校验")
        try:
            metadata_json = canonical_json(evidence.metadata).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationError("Web Evidence metadata 编码无效") from exc
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/web-evidence"
        fields = {
            "message_id": message_id,
            "case_run_id": str(case_run_id),
            "artifact_type": evidence.artifact_type,
            "artifact_name": evidence.artifact_name,
            "sha256": evidence.sha256,
            "metadata_json": metadata_json,
        }
        response = self._post_multipart(
            endpoint,
            fields=fields,
            file_path=str(evidence.path),
            file_name=evidence.artifact_name + ARTIFACT_EXTENSIONS[evidence.artifact_type],
            mime=evidence.mime,
            operation="上传 Web Evidence",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "id",
            "artifact_type",
            "artifact_name",
            "file_name",
            "mime",
            "size",
            "sha256",
            "metadata",
            "created_at",
            "idempotent",
        }
        _require_exact_keys(body, expected, "Web Evidence 上传")
        if type(body.get("schema_version")) is not int or body.get("schema_version") != 1:
            raise ProtocolError("Web Evidence schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        evidence_id = _required_text(body, "id", max_length=128)
        artifact_type = _required_text(body, "artifact_type", max_length=32)
        artifact_name = _required_text(body, "artifact_name", max_length=128)
        file_name = _required_text(body, "file_name", max_length=255)
        mime = _required_text(body, "mime", max_length=128)
        size = _required_positive_int(body.get("size"), "size")
        sha256 = _required_text(body, "sha256", max_length=64)
        metadata = body.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ProtocolError("Web Evidence 响应 metadata 无效")
        created_at = _parse_datetime(body.get("created_at"), field_name="created_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or artifact_type != evidence.artifact_type
            or artifact_name != evidence.artifact_name
            or mime != evidence.mime
            or size != evidence.size
            or sha256 != evidence.sha256
            or dict(metadata) != dict(evidence.metadata)
        ):
            raise ProtocolError("Web Evidence 响应与上传文件不一致")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ProtocolError("Web Evidence sha256 无效")
        if not ARTIFACT_NAME_PATTERN.fullmatch(artifact_name):
            raise ProtocolError("Web Evidence artifact_name 无效")
        expected_file_name = artifact_name + ARTIFACT_EXTENSIONS.get(artifact_type, "")
        if file_name != expected_file_name:
            raise ProtocolError("Web Evidence file_name 与 artifact_name 不一致")
        return WebEvidenceUploadResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            evidence_id,
            artifact_type,
            artifact_name,
            file_name,
            mime,
            size,
            sha256,
            dict(metadata),
            created_at,
            idempotent,
        )

    def scenario_execution_start(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ScenarioExecutionStartResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/scenario-execution-start"
        )
        response = self._post(
            endpoint,
            {"message_id": message_id, "case_run_id": case_run_id},
            operation="开始 Scenario 执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "status",
                "started_at",
                "idempotent",
            },
            "Scenario execution start",
        )
        if body.get("schema_version") != 1 or type(body.get("schema_version")) is not int:
            raise ProtocolError("Scenario execution start schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        status = _required_text(body, "status", max_length=32)
        started_at_value = body.get("started_at")
        if status == "RUNNING":
            started_at = _parse_datetime(started_at_value, field_name="started_at")
        elif status == "CANCELLING":
            if started_at_value is not None:
                raise ProtocolError("CANCELLING Scenario start 的 started_at 必须为 null")
            started_at = None
        else:
            raise ProtocolError("Scenario execution start 响应状态无效")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
        ):
            raise ProtocolError("Scenario execution start 响应与任务身份不一致")
        return ScenarioExecutionStartResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            status,
            started_at,
            idempotent,
        )

    def scenario_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> ScenarioExecutionPlanResult:
        """Short compatibility name matching the API_CASE client methods."""

        return self.get_scenario_execution_plan(identity, run_id, message_id, case_run_id)

    def scenario_execution_complete(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        outcome: str,
        traces: list[Mapping[str, Any]],
        error_type: str | None = None,
        error_message: str | None = None,
        evaluation_token: str | None = None,
    ) -> ScenarioExecutionCompleteResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if outcome not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ConfigurationError("Scenario complete outcome 无效")
        wire_traces = _scenario_trace_payload(traces)
        if not isinstance(error_type, str) and error_type is not None:
            raise ConfigurationError("Scenario complete error_type 无效")
        if error_type is not None and not 1 <= len(error_type) <= 100:
            raise ConfigurationError("Scenario complete error_type 长度无效")
        if not isinstance(error_message, str) and error_message is not None:
            raise ConfigurationError("Scenario complete error_message 无效")
        if error_message is not None and not 1 <= len(error_message) <= 500:
            raise ConfigurationError("Scenario complete error_message 长度无效")
        if outcome in {"TIMEOUT", "CANCELLED"} and (not error_type or not error_message):
            raise ConfigurationError("Scenario TIMEOUT/CANCELLED 必须提供安全错误信息")
        payload: dict[str, Any] = {
            "message_id": message_id,
            "case_run_id": case_run_id,
            "outcome": outcome,
            "traces": wire_traces,
            "error_type": error_type,
            "error_message": error_message,
        }
        if evaluation_token is not None:
            if not isinstance(evaluation_token, str) or not 1 <= len(evaluation_token) <= 64_000:
                raise ConfigurationError("Scenario evaluation_token 无效")
            payload["evaluation_token"] = evaluation_token
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/scenario-execution-complete"
        )
        response = self._post(
            endpoint,
            payload,
            operation="完成 Scenario 执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        expected_response_keys = {
            "schema_version",
            "run_id",
            "message_id",
            "runner_id",
            "case_run_id",
            "outcome",
            "run_status",
            "case_run_status",
            "traces",
            "assertion_results",
            "error_type",
            "error_message",
            "completed_at",
            "idempotent",
        }
        if frozenset(body) not in {
            frozenset(expected_response_keys),
            frozenset(expected_response_keys - {"assertion_results"}),
        }:
            raise ProtocolError("Scenario execution complete 响应字段不符合协议")
        if body.get("schema_version") != 1 or type(body.get("schema_version")) is not int:
            raise ProtocolError("Scenario execution complete schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        response_outcome = _required_text(body, "outcome", max_length=32)
        run_status = _required_text(body, "run_status", max_length=32)
        case_run_status = _required_text(body, "case_run_status", max_length=32)
        response_traces = _scenario_trace_payload(body.get("traces"))
        assertion_results = _parse_assertion_results(body.get("assertion_results", []))
        response_error_type = _optional_text(body.get("error_type"), "error_type", max_length=100)
        response_error_message = _optional_text(
            body.get("error_message"), "error_message", max_length=500
        )
        completed_at = _parse_datetime(body.get("completed_at"), field_name="completed_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        status_by_outcome = {
            "SUCCESS": {"SUCCESS"},
            "FAILED": {"FAILED"},
            "TIMEOUT": {"TIMEOUT"},
            "CANCELLED": {"CANCELLED"},
        }
        cancelled_override = response_outcome == "CANCELLED" and outcome != "CANCELLED"
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or response_outcome not in status_by_outcome
            or (response_outcome != outcome and not cancelled_override)
            or run_status not in status_by_outcome.get(response_outcome, set())
            or case_run_status
            not in (
                {"FAILED", "REVIEW"}
                if response_outcome == "FAILED"
                else status_by_outcome.get(response_outcome, set())
            )
            or (
                response_outcome in {"TIMEOUT", "CANCELLED"}
                and (not response_error_type or not response_error_message)
            )
        ):
            raise ProtocolError("Scenario execution complete 未返回合法终态")
        return ScenarioExecutionCompleteResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            response_outcome,
            run_status,
            case_run_status,
            response_traces,
            response_error_type,
            response_error_message,
            completed_at,
            idempotent,
            assertion_results,
        )

    def scenario_execution_evaluate(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        evaluations: list[Mapping[str, Any]],
    ) -> ScenarioExecutionEvaluationResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if not isinstance(evaluations, list) or not 1 <= len(evaluations) <= 20:
            raise ConfigurationError("Scenario AI evaluations 无效")
        wire_evaluations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in evaluations:
            if not isinstance(item, Mapping) or set(item) != {"node_id", "response"}:
                raise ConfigurationError("Scenario AI evaluation 项无效")
            node_id = item.get("node_id")
            response = item.get("response")
            if (
                not isinstance(node_id, str)
                or not 1 <= len(node_id) <= 64
                or node_id in seen
                or not isinstance(response, Mapping)
            ):
                raise ConfigurationError("Scenario AI evaluation node/response 无效")
            seen.add(node_id)
            wire_evaluations.append(
                {"node_id": node_id, "response": _execution_response_snapshot(response)}
            )
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/scenario-execution-evaluate"
        )
        response = self._post(
            endpoint,
            {
                "message_id": message_id,
                "case_run_id": case_run_id,
                "evaluations": wire_evaluations,
            },
            operation="评估 Scenario AI 断言",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "assertion_status",
                "cleanup_outcome",
                "assertion_results",
                "node_results",
                "evaluation_token",
            },
            "Scenario execution evaluate",
        )
        assertion_status = _required_text(body, "assertion_status", max_length=16)
        cleanup_outcome = _required_text(body, "cleanup_outcome", max_length=16)
        node_results = body.get("node_results")
        if not isinstance(node_results, list) or len(node_results) != len(wire_evaluations):
            raise ProtocolError("Scenario execution evaluate node_results 无效")
        parsed_nodes: list[dict[str, str]] = []
        for expected, item in zip(wire_evaluations, node_results, strict=True):
            if (
                not isinstance(item, Mapping)
                or set(item) != {"node_id", "status"}
                or item.get("node_id") != expected["node_id"]
                or item.get("status") not in {"PASS", "FAIL", "REVIEW"}
            ):
                raise ProtocolError("Scenario execution evaluate node result 无效")
            parsed_nodes.append(dict(item))
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        if (
            body.get("schema_version") != 1
            or response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or assertion_status not in {"PASS", "FAIL", "REVIEW"}
            or cleanup_outcome not in {"SUCCESS", "FAILURE"}
            or (assertion_status == "PASS") != (cleanup_outcome == "SUCCESS")
        ):
            raise ProtocolError("Scenario execution evaluate 响应与请求不一致")
        return ScenarioExecutionEvaluationResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            assertion_status,
            cleanup_outcome,
            _parse_assertion_results(body.get("assertion_results")),
            parsed_nodes,
            _required_text(body, "evaluation_token", max_length=64_000),
        )

    def execution_start(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ExecutionStartResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/execution-start"
        response = self._post(
            endpoint,
            {"message_id": message_id, "case_run_id": case_run_id},
            operation="开始执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "status",
                "started_at",
                "idempotent",
            },
            "execution start",
        )
        schema_version = body.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise ProtocolError("execution start schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        status = _required_text(body, "status", max_length=32)
        started_at_value = body.get("started_at")
        if status == "RUNNING":
            started_at = _parse_datetime(started_at_value, field_name="started_at")
        elif status == "CANCELLING":
            if started_at_value is not None:
                raise ProtocolError("CANCELLING execution start 的 started_at 必须为 null")
            started_at = None
        else:
            raise ProtocolError("execution start 响应状态无效")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or status not in {"RUNNING", "CANCELLING"}
        ):
            raise ProtocolError("execution start 响应与请求状态不一致")
        return ExecutionStartResult(
            schema_version,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            status,
            started_at,
            idempotent,
        )

    def get_execution_cancellation(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ExecutionCancellationResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        endpoint = (
            f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/execution-cancellation?"
            f"{urlencode({'message_id': message_id, 'case_run_id': case_run_id})}"
        )
        response = self._request(
            "GET",
            endpoint,
            operation="查询执行取消状态",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(response, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "case_run_id",
                "status",
                "cancellation_requested",
                "force_stop_requested",
            },
            "execution cancellation",
        )
        schema_version = body.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise ProtocolError("execution cancellation schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        status = _required_text(body, "status", max_length=32)
        cancellation_requested = _required_bool(
            body.get("cancellation_requested"), "cancellation_requested"
        )
        force_stop_requested = _required_bool(
            body.get("force_stop_requested"), "force_stop_requested"
        )
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_case_run_id != case_run_id
        ):
            raise ProtocolError("execution cancellation 响应与请求身份不一致")
        if status == "RUNNING" and cancellation_requested:
            raise ProtocolError("RUNNING execution cancellation 不能请求取消")
        if status == "RUNNING" and force_stop_requested:
            raise ProtocolError("RUNNING execution cancellation 不能请求强制停止")
        if status in {"CANCELLING", "CANCELLED"} and not cancellation_requested:
            raise ProtocolError("取消中或已取消状态必须标记 cancellation_requested")
        if status not in {"RUNNING", "CANCELLING", "CANCELLED"}:
            raise ProtocolError("execution cancellation 响应状态无效")
        return ExecutionCancellationResult(
            schema_version,
            response_run_id,
            response_message_id,
            response_case_run_id,
            status,
            cancellation_requested,
            force_stop_requested,
        )

    def execution_cancellation(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ExecutionCancellationResult:
        """兼容调用方对取消查询的简短命名。"""

        return self.get_execution_cancellation(identity, run_id, message_id, case_run_id)

    def execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
    ) -> ExecutionPlanResult:
        """兼容调用方对 GET execution-plan 的简短命名。"""

        return self.get_execution_plan(identity, run_id, message_id)

    def execution_evaluate(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        response: Mapping[str, Any],
    ) -> ExecutionEvaluationResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if not isinstance(response, Mapping):
            raise ConfigurationError("execution evaluate 必须包含 response")
        payload = {
            "message_id": message_id,
            "case_run_id": case_run_id,
            "response": _execution_response_snapshot(response),
        }
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/execution-evaluate"
        result = self._post(
            endpoint,
            payload,
            operation="评估 API 断言",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(result, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "assertion_status",
                "cleanup_outcome",
                "assertion_results",
                "evaluation_token",
            },
            "execution evaluate",
        )
        if body.get("schema_version") != 1:
            raise ProtocolError("execution evaluate schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        assertion_status = _required_text(body, "assertion_status", max_length=16)
        cleanup_outcome = _required_text(body, "cleanup_outcome", max_length=16)
        assertion_results = _parse_assertion_results(body.get("assertion_results"))
        evaluation_token = _required_text(body, "evaluation_token", max_length=64_000)
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or assertion_status not in {"PASS", "FAIL", "REVIEW"}
            or cleanup_outcome not in {"SUCCESS", "FAILURE"}
            or (assertion_status == "PASS") != (cleanup_outcome == "SUCCESS")
        ):
            raise ProtocolError("execution evaluate 响应与请求不一致")
        return ExecutionEvaluationResult(
            1,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            assertion_status,
            cleanup_outcome,
            assertion_results,
            evaluation_token,
        )

    def execution_complete(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        outcome: str,
        response: Mapping[str, Any] | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        retry_count: int = 0,
        resources: list[Mapping[str, Any]] | None = None,
        action_traces: list[Mapping[str, Any]] | None = None,
        evaluation_token: str | None = None,
    ) -> ExecutionCompleteResult:
        self._validate_identity(identity)
        _validate_execution_identifiers(run_id, message_id)
        _validate_case_run_id(case_run_id)
        if type(retry_count) is not int or not 0 <= retry_count <= 3:
            raise ConfigurationError("retry_count 必须是 0 到 3 的整数")
        payload: dict[str, Any] = {
            "message_id": message_id,
            "case_run_id": case_run_id,
            "outcome": outcome,
            "retry_count": retry_count,
        }
        if resources:
            if len(resources) > 100 or any(not isinstance(item, Mapping) for item in resources):
                raise ConfigurationError("resources 必须是有限对象数组")
            _validate_api_pipeline_json(resources, "completion resources")
            payload["resources"] = [dict(item) for item in resources]
        if action_traces:
            payload["action_traces"] = _api_action_trace_payload(action_traces)
        if evaluation_token is not None:
            if outcome != "HTTP_RESPONSE" or not 1 <= len(evaluation_token) <= 64_000:
                raise ConfigurationError("evaluation_token 无效")
            payload["evaluation_token"] = evaluation_token
        if outcome == "HTTP_RESPONSE":
            if response is None or not isinstance(response, Mapping):
                raise ConfigurationError("HTTP_RESPONSE 必须包含安全 response 摘要")
            if error_type is not None or error_message is not None:
                raise ConfigurationError("HTTP_RESPONSE 不能包含错误字段")
            payload["response"] = _execution_response_snapshot(response)
        elif outcome == "EXECUTION_ERROR":
            if response is not None:
                raise ConfigurationError("EXECUTION_ERROR 不能包含 response")
            if not isinstance(error_type, str) or not 1 <= len(error_type) <= 100:
                raise ConfigurationError("EXECUTION_ERROR 缺少有效 error_type")
            if not isinstance(error_message, str) or not 1 <= len(error_message) <= 2000:
                raise ConfigurationError("EXECUTION_ERROR 缺少有效 error_message")
            payload["error_type"] = error_type
            payload["error_message"] = error_message
        elif outcome == "TIMEOUT":
            if response is not None:
                raise ConfigurationError("TIMEOUT 不能包含 response")
            if not isinstance(error_type, str) or not 1 <= len(error_type) <= 100:
                raise ConfigurationError("TIMEOUT 缺少有效 error_type")
            if not isinstance(error_message, str) or not 1 <= len(error_message) <= 2000:
                raise ConfigurationError("TIMEOUT 缺少有效 error_message")
            payload["error_type"] = error_type
            payload["error_message"] = error_message
        elif outcome == "CANCELLED":
            if response is not None:
                raise ConfigurationError("CANCELLED 不能包含 response")
            if error_type not in {"CANCEL_REQUESTED", "FORCE_STOP_REQUESTED"}:
                raise ConfigurationError(
                    "CANCELLED 的 error_type 必须为 CANCEL_REQUESTED 或 FORCE_STOP_REQUESTED"
                )
            if not isinstance(error_message, str) or not 1 <= len(error_message) <= 2000:
                raise ConfigurationError("CANCELLED 缺少有效 error_message")
            payload["error_type"] = error_type
            payload["error_message"] = error_message
        else:
            raise ConfigurationError("execution complete outcome 无效")
        endpoint = f"{self.backend_url}/api/v1/runs/{quote(run_id, safe='')}/execution-complete"
        result = self._post(
            endpoint,
            payload,
            operation="完成执行",
            headers={"Authorization": f"Bearer {identity.credential}"},
            secrets=(identity.credential,),
        )
        body = _response_object(result, secrets=(identity.credential,))
        _require_exact_keys(
            body,
            {
                "schema_version",
                "run_id",
                "message_id",
                "runner_id",
                "case_run_id",
                "outcome",
                "run_status",
                "case_run_status",
                "assertion_results",
                "error_type",
                "error_message",
                "completed_at",
                "idempotent",
                "retry_count",
            },
            "execution complete",
        )
        schema_version = body.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise ProtocolError("execution complete schema_version 无效")
        response_run_id = _required_text(body, "run_id", max_length=128)
        response_message_id = _required_text(body, "message_id", max_length=64)
        response_runner_id = _required_text(body, "runner_id", max_length=64)
        response_case_run_id = _required_positive_int(body.get("case_run_id"), "case_run_id")
        response_outcome = _required_text(body, "outcome", max_length=32)
        run_status = _required_text(body, "run_status", max_length=32)
        case_run_status = _required_text(body, "case_run_status", max_length=32)
        assertion_results = _parse_assertion_results(body.get("assertion_results"))
        error_type = _optional_text(body.get("error_type"), "error_type", max_length=100)
        error_message = _optional_text(body.get("error_message"), "error_message", max_length=2000)
        if response_outcome in {"TIMEOUT", "CANCELLED"} and (not error_type or not error_message):
            raise ProtocolError("TIMEOUT/CANCELLED 响应必须包含非空错误信息")
        if response_outcome == "CANCELLED" and (
            error_type not in {"CANCEL_REQUESTED", "FORCE_STOP_REQUESTED"} or assertion_results
        ):
            raise ProtocolError("服务端取消覆盖响应必须使用通用错误且不含断言结果")
        completed_at = _parse_datetime(body.get("completed_at"), field_name="completed_at")
        idempotent = _required_bool(body.get("idempotent"), "idempotent")
        response_retry_count = _required_retry_count(body.get("retry_count"))
        cancelled_override = response_outcome == "CANCELLED" and outcome != "CANCELLED"
        if (
            response_run_id != run_id
            or response_message_id != message_id
            or response_runner_id != identity.runner_id
            or response_case_run_id != case_run_id
            or (response_outcome != outcome and not cancelled_override)
            or response_retry_count != retry_count
            or (
                (
                    response_outcome == "TIMEOUT"
                    and (run_status not in {"RUNNING", "TIMEOUT"} or case_run_status != "TIMEOUT")
                )
                or (
                    response_outcome == "CANCELLED"
                    and (run_status != "CANCELLED" or case_run_status != "CANCELLED")
                )
                or (
                    response_outcome not in {"TIMEOUT", "CANCELLED"}
                    and (
                        run_status not in {"RUNNING", "SUCCESS", "FAILED", "TIMEOUT"}
                        or case_run_status not in {"SUCCESS", "FAILED", "REVIEW"}
                    )
                )
            )
        ):
            raise ProtocolError("execution complete 未返回合法终态")
        return ExecutionCompleteResult(
            schema_version,
            response_run_id,
            response_message_id,
            response_runner_id,
            response_case_run_id,
            response_outcome,
            run_status,
            case_run_status,
            assertion_results,
            error_type,
            error_message,
            completed_at,
            idempotent,
            response_retry_count,
        )

    @staticmethod
    def _validate_identity(identity: RunnerIdentity) -> None:
        if not _valid_identifier(identity.runner_id, max_length=64):
            raise ConfigurationError("本地 runner_id 无效")
        if not isinstance(identity.credential, str) or not 16 <= len(identity.credential) <= 512:
            raise AuthenticationError("本地 Runner credential 无效")

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        operation: str,
        headers: Mapping[str, str] | None = None,
        secrets: tuple[str, ...] = (),
        retryable: bool = True,
    ) -> HttpResponse:
        return self._request(
            "POST",
            url,
            payload=payload,
            operation=operation,
            headers=headers,
            secrets=secrets,
            retryable=retryable,
        )

    def _post_multipart(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        file_path: str,
        file_name: str,
        mime: str,
        operation: str,
        headers: Mapping[str, str] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> HttpResponse:
        for attempt in range(self._max_attempts):
            try:
                post_multipart = getattr(self._transport, "post_multipart", None)
                if not callable(post_multipart):
                    raise TransportError("Runner HTTP 客户端不支持 multipart", transient=False)
                response = post_multipart(
                    url,
                    fields=fields,
                    file_path=file_path,
                    file_name=file_name,
                    mime=mime,
                    headers=headers or {},
                    timeout=self._timeout,
                )
            except TransportError as exc:
                if not exc.transient or attempt == self._max_attempts - 1:
                    raise TransportError(redact_text(exc, secrets)) from exc
                self._sleep_before_retry(attempt)
                continue
            except (ConnectionError, OSError, TimeoutError) as exc:
                if attempt == self._max_attempts - 1:
                    raise TransportError("Runner Evidence 网络请求重试失败") from exc
                self._sleep_before_retry(attempt)
                continue

            if 200 <= response.status_code < 300:
                return response
            if response.status_code in {401, 403}:
                raise AuthenticationError("Runner credential 无效或已撤销，已停止任务执行")
            if 500 <= response.status_code <= 599:
                if attempt < self._max_attempts - 1:
                    self._sleep_before_retry(attempt)
                    continue
                raise TransportError(
                    f"Runner 后端暂时不可用，{operation}已达到重试上限",
                    transient=False,
                )
            detail = _safe_backend_error_detail(response.body, secrets)
            raise ProtocolError(
                f"Runner 后端拒绝{operation}请求（HTTP {response.status_code}）：{detail}"
            )
        raise TransportError("Runner Evidence 请求失败", transient=False)

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        operation: str,
        headers: Mapping[str, str] | None = None,
        secrets: tuple[str, ...] = (),
        retryable: bool = True,
        timeout: TimeoutSettings | None = None,
    ) -> HttpResponse:
        effective_timeout = timeout or self._timeout
        for attempt in range(self._max_attempts):
            try:
                if method == "GET":
                    get = getattr(self._transport, "get", None)
                    if not callable(get):
                        raise TransportError("Runner HTTP 客户端不支持 GET", transient=False)
                    response = get(url, headers=headers or {}, timeout=effective_timeout)
                else:
                    response = self._transport.post(
                        url,
                        json_body=payload or {},
                        headers=headers or {},
                        timeout=effective_timeout,
                    )
            except TransportError as exc:
                if not retryable:
                    raise RegistrationOutcomeUnknownError(
                        "注册结果未知，勿重试同一 Token；请管理员检查或撤销孤儿 Runner，"
                        "然后重新生成 Registration Token"
                    ) from exc
                if not exc.transient or attempt == self._max_attempts - 1:
                    raise TransportError(redact_text(exc, secrets)) from exc
                self._sleep_before_retry(attempt)
                continue
            except (ConnectionError, OSError, TimeoutError) as exc:
                if not retryable:
                    raise RegistrationOutcomeUnknownError(
                        "注册结果未知，勿重试同一 Token；请管理员检查或撤销孤儿 Runner，"
                        "然后重新生成 Registration Token"
                    ) from exc
                if attempt == self._max_attempts - 1:
                    raise TransportError("Runner HTTP 网络请求重试失败", transient=False) from exc
                self._sleep_before_retry(attempt)
                continue

            if 200 <= response.status_code < 300:
                return response
            if response.status_code in {401, 403}:
                if operation == "注册":
                    raise AuthenticationError("Runner Registration Token 无效、已消费或已撤销")
                if operation == "心跳":
                    raise AuthenticationError("Runner credential 无效或已撤销，已停止心跳")
                raise AuthenticationError("Runner credential 无效或已撤销，已停止任务执行")
            if 500 <= response.status_code <= 599:
                if not retryable:
                    raise RegistrationOutcomeUnknownError(
                        "注册结果未知，勿重试同一 Token；请管理员检查或撤销孤儿 Runner，"
                        "然后重新生成 Registration Token"
                    )
                if attempt < self._max_attempts - 1:
                    self._sleep_before_retry(attempt)
                    continue
                raise TransportError(
                    f"Runner 后端暂时不可用，{operation}已达到重试上限",
                    transient=False,
                )
            detail = _safe_backend_error_detail(response.body, secrets)
            raise ProtocolError(
                f"Runner 后端拒绝{operation}请求（HTTP {response.status_code}）：{detail}"
            )
        raise TransportError("Runner HTTP 请求失败", transient=False)

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = min(self._backoff_max, self._backoff_base * (2**attempt))
        if delay > 0:
            self._sleeper(delay)


def _api_pre_actions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("execution plan pre_actions 必须是有限数组")
    result: list[dict[str, Any]] = []
    generators = {
        "uuid",
        "email",
        "username",
        "first_name",
        "last_name",
        "integer",
        "word",
        "boolean",
    }
    for action in value:
        if not isinstance(action, Mapping):
            raise ProtocolError("execution plan pre_actions 项必须是对象")
        current = dict(action)
        action_type = current.get("type", "SET_VARIABLE")
        allowed = (
            {"type", "name", "value", "enabled"}
            if action_type == "SET_VARIABLE"
            else {"type", "name", "generator", "seed", "enabled"}
            if action_type == "FAKER"
            else {"type", "script", "enabled"}
            if action_type == "PYTHON_SCRIPT"
            else {
                "type",
                "name",
                "source",
                "expression",
                "required",
                "default_value",
                "request",
                "enabled",
            }
            if action_type in {"GET_TOKEN", "API_SETUP"}
            else {
                "type",
                "connection_id",
                "sql",
                "params",
                "result_variable",
                "max_rows",
                "enabled",
            }
            if action_type == "SQL_QUERY"
            else set()
        )
        if not allowed or set(current) - allowed:
            raise ProtocolError("execution plan pre_actions 类型或字段无效")
        name = current.get("name")
        if action_type not in {"PYTHON_SCRIPT", "SQL_QUERY"} and (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", name) is None
        ):
            raise ProtocolError("execution plan pre_actions 变量名无效")
        if type(current.get("enabled", True)) is not bool:
            raise ProtocolError("execution plan pre_actions enabled 无效")
        if action_type == "SET_VARIABLE" and "value" not in current:
            raise ProtocolError("SET_VARIABLE pre_action 缺少 value")
        if action_type == "FAKER":
            if current.get("generator") not in generators:
                raise ProtocolError("FAKER pre_action generator 无效")
            seed = current.get("seed")
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
                raise ProtocolError("FAKER pre_action seed 无效")
        if action_type == "PYTHON_SCRIPT" and (
            not isinstance(current.get("script"), str) or not 1 <= len(current["script"]) <= 5000
        ):
            raise ProtocolError("PYTHON_SCRIPT pre_action script 无效")
        if action_type == "SQL_QUERY" and (
            set(current) != allowed
            or isinstance(current.get("connection_id"), bool)
            or not isinstance(current.get("connection_id"), int)
            or current["connection_id"] <= 0
            or not isinstance(current.get("sql"), str)
            or not 1 <= len(current["sql"]) <= 10_000
            or not isinstance(current.get("params"), (dict, list))
            or not isinstance(current.get("result_variable"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", current["result_variable"]) is None
            or isinstance(current.get("max_rows"), bool)
            or not isinstance(current.get("max_rows"), int)
            or not 1 <= current["max_rows"] <= 1000
        ):
            raise ProtocolError("SQL_QUERY pre_action 配置无效")
        if action_type in {"GET_TOKEN", "API_SETUP"}:
            if (
                set(current) != allowed
                or current.get("source") not in {"JSONPATH", "HEADER", "COOKIE"}
                or not isinstance(current.get("expression"), str)
                or not 1 <= len(current["expression"]) <= 1000
                or type(current.get("required")) is not bool
                or not isinstance(current.get("request"), Mapping)
            ):
                raise ProtocolError("API request pre_action 配置无效")
            token_request = ApiRequestTemplate.from_mapping(current["request"])
            if token_request.retry_policy.max_retries != 0:
                raise ProtocolError("API request pre_action 不能配置普通重试")
        if action_type not in {"GET_TOKEN", "API_SETUP"} and _scenario_sensitive_context(current):
            raise ProtocolError("execution plan pre_actions 不能包含敏感数据")
        result.append(current)
    try:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("execution plan pre_actions 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 64_000:
        raise ProtocolError("execution plan pre_actions 超过大小限制")
    return result


def _api_extractors(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 200:
        raise ProtocolError("execution plan extractors 必须是有限数组")
    result: list[dict[str, Any]] = []
    allowed = {"name", "source", "expression", "required", "default_value", "enabled"}
    for extractor in value:
        if not isinstance(extractor, Mapping):
            raise ProtocolError("execution plan extractor 项必须是对象")
        current = dict(extractor)
        if set(current) != allowed:
            raise ProtocolError("execution plan extractor 字段无效")
        if (
            not isinstance(current.get("name"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", current["name"]) is None
            or current.get("source") not in {"JSONPATH", "HEADER", "COOKIE"}
            or not isinstance(current.get("expression"), str)
            or not 1 <= len(current["expression"]) <= 1000
            or type(current.get("required")) is not bool
            or type(current.get("enabled")) is not bool
        ):
            raise ProtocolError("execution plan extractor 配置无效")
        result.append(current)
    _validate_api_pipeline_json(result, "extractors")
    return result


def _api_post_actions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("execution plan post_actions 必须是有限数组")
    result: list[dict[str, Any]] = []
    for action in value:
        if not isinstance(action, Mapping):
            raise ProtocolError("execution plan post_action 项必须是对象")
        current = dict(action)
        action_type = current.get("type", "SET_VARIABLE")
        allowed = (
            {"type", "name", "value", "enabled"}
            if action_type == "SET_VARIABLE"
            else {"type", "script", "enabled"}
            if action_type == "PYTHON_SCRIPT"
            else {
                "type",
                "name",
                "source",
                "expression",
                "required",
                "default_value",
                "enabled",
            }
            if action_type == "EXTRACT_RESPONSE"
            else {
                "type",
                "name",
                "resource_type",
                "value",
                "metadata",
                "cleanup",
                "cleanup_ref",
                "enabled",
            }
            if action_type == "REGISTER_RESOURCE"
            else set()
        )
        if not allowed or set(current) - allowed:
            raise ProtocolError("execution plan post_action 类型或字段无效")
        if type(current.get("enabled")) is not bool:
            raise ProtocolError("execution plan post_action 通用字段无效")
        if action_type not in {"PYTHON_SCRIPT"} and (
            not isinstance(current.get("name"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", current["name"]) is None
        ):
            raise ProtocolError("execution plan post_action 通用字段无效")
        if action_type == "SET_VARIABLE" and "value" not in current:
            raise ProtocolError("SET_VARIABLE post_action 缺少 value")
        if action_type == "EXTRACT_RESPONSE" and (
            set(current) != allowed
            or current.get("source") not in {"JSONPATH", "HEADER", "COOKIE"}
            or not isinstance(current.get("expression"), str)
            or not 1 <= len(current["expression"]) <= 1000
            or type(current.get("required")) is not bool
        ):
            raise ProtocolError("EXTRACT_RESPONSE post_action 配置无效")
        if action_type == "PYTHON_SCRIPT" and (
            set(current) != allowed
            or not isinstance(current.get("script"), str)
            or not 1 <= len(current["script"]) <= 5000
        ):
            raise ProtocolError("PYTHON_SCRIPT post_action script 无效")
        if action_type == "REGISTER_RESOURCE" and (
            set(current) != allowed
            or not isinstance(current.get("resource_type"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", current["resource_type"]) is None
            or not isinstance(current.get("metadata"), Mapping)
            or (current.get("cleanup") is None and current.get("cleanup_ref") is None)
            or (current.get("cleanup") is not None and current.get("cleanup_ref") is not None)
            or (
                current.get("cleanup") is not None
                and not isinstance(current.get("cleanup"), Mapping)
            )
            or (
                current.get("cleanup_ref") is not None
                and (
                    not isinstance(current.get("cleanup_ref"), str)
                    or not 1 <= len(current["cleanup_ref"]) <= 100
                )
            )
        ):
            raise ProtocolError("REGISTER_RESOURCE post_action 配置无效")
        result.append(current)
    _validate_api_pipeline_json(result, "post_actions")
    return result


def _api_action_trace_payload(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 500:
        raise ConfigurationError("API action_traces 必须是有限数组")
    fields = {
        "sequence",
        "phase",
        "type",
        "name",
        "status",
        "duration_ms",
        "error_type",
    }
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise ConfigurationError("API action_trace 字段不符合协议")
        sequence = item.get("sequence")
        phase = item.get("phase")
        action_type = item.get("type")
        name = item.get("name")
        status = item.get("status")
        duration_ms = item.get("duration_ms")
        error_type = item.get("error_type")
        if (
            type(sequence) is not int
            or not 1 <= sequence <= 1000
            or phase not in {"PRE", "AUTH_REFRESH", "EXTRACTOR", "POST"}
            or not isinstance(action_type, str)
            or not 1 <= len(action_type) <= 64
            or (name is not None and (not isinstance(name, str) or len(name) > 255))
            or status not in {"SUCCESS", "FAILED", "SKIPPED"}
            or type(duration_ms) is not int
            or not 0 <= duration_ms <= 86_400_000
            or (
                error_type is not None
                and (not isinstance(error_type, str) or len(error_type) > 100)
            )
            or (status == "FAILED" and not error_type)
            or (status != "FAILED" and error_type is not None)
            or (phase == "AUTH_REFRESH" and action_type != "GET_TOKEN")
        ):
            raise ConfigurationError("API action_trace 无效")
        result.append(dict(item))
    for phase in ("PRE", "AUTH_REFRESH", "EXTRACTOR", "POST"):
        sequences = [item["sequence"] for item in result if item["phase"] == phase]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ConfigurationError("API action_trace sequence 无效")
    return result


def _validate_api_pipeline_json(value: object, label: str) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError(f"execution plan {label} 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 64_000:
        raise ProtocolError(f"execution plan {label} 超过大小限制")


def _api_cleanups(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("execution plan cleanups 必须是有限数组")
    result: list[dict[str, Any]] = []
    for cleanup in value:
        if not isinstance(cleanup, Mapping):
            raise ProtocolError("execution plan cleanup 项必须是对象")
        current = dict(cleanup)
        if current.get("cleanup_type") not in {"API", "SQL"}:
            raise ProtocolError("execution plan cleanup 类型无效")
        if current.get("policy") not in {"ALWAYS", "ON_SUCCESS", "ON_FAILURE", "NEVER"}:
            raise ProtocolError("execution plan cleanup policy 无效")
        if type(current.get("enabled")) is not bool:
            raise ProtocolError("execution plan cleanup enabled 无效")
        timeout_ms = current.get("timeout_ms")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 100 <= timeout_ms <= 120_000
        ):
            raise ProtocolError("execution plan cleanup timeout_ms 无效")
        result.append(current)
    _validate_api_pipeline_json(result, "cleanups")
    return result


def _scenario_context(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 200:
        raise ProtocolError("Scenario initial_context 必须是有限对象")
    if _scenario_sensitive_context(value):
        raise ProtocolError("Scenario initial_context 不能包含敏感变量")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("Scenario initial_context 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 64_000:
        raise ProtocolError("Scenario initial_context 超过大小限制")
    return dict(value)


def _scenario_sensitive_context(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _SCENARIO_SENSITIVE_KEY.search(str(key)) is not None
            or _scenario_sensitive_context(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_scenario_sensitive_context(child) for child in value)
    if isinstance(value, str):
        return _SCENARIO_SENSITIVE_VALUE.search(value) is not None
    return False


def _scenario_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError("Scenario settings 必须是对象")
    expected = {"stop_on_failure", "cleanup_policy", "max_loop_iterations", "initial_variables"}
    if set(value) != expected:
        raise ProtocolError("Scenario settings 字段不符合协议")
    stop_on_failure = value.get("stop_on_failure")
    cleanup_policy = value.get("cleanup_policy")
    max_loop_iterations = value.get("max_loop_iterations")
    initial_variables = value.get("initial_variables")
    if type(stop_on_failure) is not bool:
        raise ProtocolError("Scenario settings.stop_on_failure 必须是 bool")
    if cleanup_policy not in {"ALWAYS", "ON_SUCCESS", "ON_FAILURE", "NEVER"}:
        raise ProtocolError("Scenario settings.cleanup_policy 无效")
    if type(max_loop_iterations) is not int or not 1 <= max_loop_iterations <= 1000:
        raise ProtocolError("Scenario settings.max_loop_iterations 无效")
    if (
        not isinstance(initial_variables, list)
        or len(initial_variables) > 200
        or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in initial_variables)
    ):
        raise ProtocolError("Scenario settings.initial_variables 无效")
    return dict(value)


def _scenario_nodes(value: object) -> list[ScenarioNodePlan]:
    if not isinstance(value, list) or not 2 <= len(value) <= 500:
        raise ProtocolError("Scenario nodes 数量无效")
    allowed = {
        "node_id",
        "node_type",
        "name",
        "parent_id",
        "enabled",
        "timeout_ms",
        "failure_policy",
        "config",
    }
    result: list[ScenarioNodePlan] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != allowed:
            raise ProtocolError("Scenario node 字段不符合协议")
        node_id = item.get("node_id")
        node_type = item.get("node_type")
        name = item.get("name")
        parent_id = item.get("parent_id")
        enabled = item.get("enabled")
        timeout_ms = item.get("timeout_ms")
        failure_policy = item.get("failure_policy")
        config = item.get("config")
        if (
            not isinstance(node_id, str)
            or not 1 <= len(node_id) <= 64
            or node_id in seen
            or not node_id[0].isalpha()
            or not all(character.isalnum() or character in "_-" for character in node_id)
        ):
            raise ProtocolError("Scenario node_id 无效或重复")
        if not isinstance(node_type, str) or not 1 <= len(node_type) <= 64:
            raise ProtocolError("Scenario node_type 无效")
        if not isinstance(name, str) or not 1 <= len(name) <= 255:
            raise ProtocolError("Scenario node name 无效")
        if parent_id is not None and (
            not isinstance(parent_id, str) or not 1 <= len(parent_id) <= 64
        ):
            raise ProtocolError("Scenario node parent_id 无效")
        if type(enabled) is not bool:
            raise ProtocolError("Scenario node enabled 必须是 bool")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 600_000:
            raise ProtocolError("Scenario node timeout_ms 无效")
        if failure_policy is not None and failure_policy not in {"STOP", "CONTINUE", "RETRY_ONCE"}:
            raise ProtocolError("Scenario node failure_policy 无效")
        if not isinstance(config, Mapping) or len(config) > 100:
            raise ProtocolError("Scenario node config 必须是有限对象")
        try:
            encoded = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProtocolError("Scenario node config 不是安全 JSON") from exc
        if len(encoded.encode("utf-8")) > 64_000:
            raise ProtocolError("Scenario node config 超过 64KB")
        seen.add(node_id)
        result.append(
            ScenarioNodePlan(
                node_id,
                node_type,
                name,
                parent_id,
                enabled,
                timeout_ms,
                failure_policy,
                dict(config),
            )
        )
    if result[0].node_type != "START" or result[-1].node_type != "END":
        raise ProtocolError("Scenario nodes 必须以 START 开始并以 END 结束")
    node_ids = {item.node_id for item in result}
    if any(item.parent_id is not None and item.parent_id not in node_ids for item in result):
        raise ProtocolError("Scenario node parent_id 引用未知节点")
    return result


def _scenario_sql_connections(value: object) -> list[ScenarioSqlConnectionPlan]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("Scenario sql_connections 数量无效")
    allowed = {
        "connection_id",
        "db_type",
        "host",
        "port",
        "database_name",
        "username",
        "password_secret_id",
        "ssl_enabled",
    }
    result: list[ScenarioSqlConnectionPlan] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != allowed:
            raise ProtocolError("Scenario SQL connection 字段不符合协议")
        connection_id = _required_positive_int(item.get("connection_id"), "connection_id")
        if connection_id in seen:
            raise ProtocolError("Scenario SQL connection 不能重复")
        if item.get("db_type") != "MYSQL":
            raise ProtocolError("Scenario 仅支持 MYSQL connection")
        host = item.get("host")
        database_name = item.get("database_name")
        username = item.get("username")
        port = item.get("port")
        password_secret_id = _required_positive_int(
            item.get("password_secret_id"), "password_secret_id"
        )
        if not isinstance(host, str) or not 1 <= len(host) <= 255:
            raise ProtocolError("Scenario SQL host 无效")
        if not isinstance(database_name, str) or not 1 <= len(database_name) <= 128:
            raise ProtocolError("Scenario SQL database_name 无效")
        if not isinstance(username, str) or not 1 <= len(username) <= 128:
            raise ProtocolError("Scenario SQL username 无效")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise ProtocolError("Scenario SQL port 无效")
        ssl_enabled = item.get("ssl_enabled")
        if type(ssl_enabled) is not bool:
            raise ProtocolError("Scenario SQL ssl_enabled 必须是 bool")
        seen.add(connection_id)
        result.append(
            ScenarioSqlConnectionPlan(
                connection_id,
                "MYSQL",
                host,
                port,
                database_name,
                username,
                password_secret_id,
                ssl_enabled,
            )
        )
    return result


def _scenario_secrets(value: object) -> list[ScenarioExecutionSecret]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("Scenario secrets 数量无效")
    result: list[ScenarioExecutionSecret] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"secret_id", "value"}:
            raise ProtocolError("Scenario secret 字段不符合协议")
        secret_id = _required_positive_int(item.get("secret_id"), "secret_id")
        secret = item.get("value")
        if secret_id in seen or not isinstance(secret, str) or not 1 <= len(secret) <= 4096:
            raise ProtocolError("Scenario secret 无效或重复")
        seen.add(secret_id)
        result.append(ScenarioExecutionSecret(secret_id, secret))
    return result


def _scenario_trace_payload(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 500:
        raise ProtocolError("Scenario traces 必须是有限数组")
    allowed = {"node_id", "status", "duration_ms", "retry_count", "error_type", "error_message"}
    statuses = {"SUCCESS", "PASSED", "FAILED", "REVIEW", "SKIPPED", "TIMEOUT", "CANCELLED"}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != allowed:
            raise ProtocolError("Scenario trace 字段不符合协议")
        node_id = item.get("node_id")
        status = item.get("status")
        duration_ms = item.get("duration_ms")
        retry_count = item.get("retry_count")
        error_type = item.get("error_type")
        error_message = item.get("error_message")
        if not isinstance(node_id, str) or not 1 <= len(node_id) <= 64 or node_id in seen:
            raise ProtocolError("Scenario trace node_id 无效或重复")
        if status not in statuses:
            raise ProtocolError("Scenario trace status 无效")
        if type(duration_ms) is not int or not 0 <= duration_ms <= 86_400_000:
            raise ProtocolError("Scenario trace duration_ms 无效")
        if type(retry_count) is not int or not 0 <= retry_count <= 3:
            raise ProtocolError("Scenario trace retry_count 无效")
        if error_type is not None and (
            not isinstance(error_type, str) or not 1 <= len(error_type) <= 100
        ):
            raise ProtocolError("Scenario trace error_type 无效")
        if error_message is not None and (
            not isinstance(error_message, str) or not 1 <= len(error_message) <= 500
        ):
            raise ProtocolError("Scenario trace error_message 无效")
        seen.add(node_id)
        result.append(dict(item))
    return result


def _web_context(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 200:
        raise ProtocolError("Web initial_context 必须是有限对象")
    context = dict(value)
    try:
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("Web initial_context 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 64_000 or _contains_sensitive_context(context):
        raise ProtocolError("Web initial_context 不满足安全边界")
    return context


def _web_browser_config(value: object) -> WebExecutionBrowserConfig:
    if value is None:
        return WebExecutionBrowserConfig()
    expected = {
        "window_width",
        "window_height",
        "language",
        "user_agent",
        "proxy",
        "download_path",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ProtocolError("Web browser_config 字段不符合协议")
    width = value.get("window_width")
    height = value.get("window_height")
    language = value.get("language")
    user_agent = value.get("user_agent")
    download_path = value.get("download_path")
    if type(width) is not int or not 320 <= width <= 7680:
        raise ProtocolError("Web browser_config.window_width 无效")
    if type(height) is not int or not 240 <= height <= 4320:
        raise ProtocolError("Web browser_config.window_height 无效")
    if language is not None and (
        not isinstance(language, str)
        or re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language) is None
        or len(language) > 35
    ):
        raise ProtocolError("Web browser_config.language 无效")
    if user_agent is not None and (
        not isinstance(user_agent, str)
        or not 1 <= len(user_agent) <= 512
        or user_agent != user_agent.strip()
        or "\r" in user_agent
        or "\n" in user_agent
    ):
        raise ProtocolError("Web browser_config.user_agent 无效")
    if download_path is not None and (
        not isinstance(download_path, str)
        or not 1 <= len(download_path) <= 128
        or "\\" in download_path
        or download_path.startswith("/")
        or any(part in {"", ".", ".."} for part in download_path.split("/"))
        or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", download_path) is None
    ):
        raise ProtocolError("Web browser_config.download_path 无效")
    proxy_value = value.get("proxy")
    proxy: WebExecutionProxyConfig | None = None
    if proxy_value is not None:
        if not isinstance(proxy_value, Mapping) or set(proxy_value) != {
            "server",
            "username",
            "password",
        }:
            raise ProtocolError("Web browser_config.proxy 字段不符合协议")
        server = proxy_value.get("server")
        username = proxy_value.get("username")
        password = proxy_value.get("password")
        if not isinstance(server, str) or not 1 <= len(server) <= 2048:
            raise ProtocolError("Web browser_config.proxy.server 无效")
        try:
            from urllib.parse import urlsplit

            parsed = urlsplit(server)
        except ValueError as exc:
            raise ProtocolError("Web browser_config.proxy.server 无效") from exc
        if (
            parsed.scheme.lower() not in {"http", "https", "socks5"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or "\r" in server
            or "\n" in server
        ):
            raise ProtocolError("Web browser_config.proxy.server 无效")
        secret_reference = re.compile(r"\{\{secret\.[A-Za-z_][A-Za-z0-9_.-]*\}\}\Z")
        for credential in (username, password):
            if credential is not None and (
                not isinstance(credential, str)
                or len(credential) > 256
                or secret_reference.fullmatch(credential) is None
            ):
                raise ProtocolError("Web browser_config.proxy 认证信息无效")
        if (username is None) != (password is None):
            raise ProtocolError("Web browser_config.proxy 认证信息不完整")
        proxy = WebExecutionProxyConfig(server, username, password)
    return WebExecutionBrowserConfig(
        width,
        height,
        language,
        user_agent,
        proxy,
        download_path,
    )


def _contains_sensitive_context(value: object, *, key: str = "") -> bool:
    if key and _SCENARIO_SENSITIVE_KEY.search(key):
        return True
    if isinstance(value, Mapping):
        return any(_contains_sensitive_context(item, key=str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_context(item) for item in value)
    if isinstance(value, str) and _SCENARIO_SENSITIVE_VALUE.search(value):
        return True
    return False


def _web_url(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ProtocolError(f"Web {field_name} 无效")
    if "\r" in value or "\n" in value:
        raise ProtocolError(f"Web {field_name} 不能包含换行符")
    if value.startswith("{{"):
        return value
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
    except ValueError as exc:
        raise ProtocolError(f"Web {field_name} 格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ProtocolError(f"Web {field_name} 必须使用 http 或 https")
    if parsed.username is not None or parsed.password is not None:
        raise ProtocolError(f"Web {field_name} 不允许携带用户名或密码")
    return value


def _web_locator(value: object) -> WebLocatorPlan | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"element_version_id", "candidates"}:
        raise ProtocolError("Web locator 字段不符合协议")
    element_version_id = value.get("element_version_id")
    if element_version_id is not None:
        element_version_id = _required_positive_int(element_version_id, "element_version_id")
    candidates_value = value.get("candidates")
    if not isinstance(candidates_value, list) or not 1 <= len(candidates_value) <= 20:
        raise ProtocolError("Web locator candidates 数量无效")
    candidates: list[WebLocatorCandidate] = []
    allowed_strategies = {"css", "xpath", "text", "role", "label", "placeholder", "test_id"}
    for item in candidates_value:
        if not isinstance(item, Mapping) or set(item) != {"strategy", "value", "priority"}:
            raise ProtocolError("Web locator candidate 字段不符合协议")
        strategy = item.get("strategy")
        locator_value = item.get("value")
        priority = item.get("priority")
        if (
            not isinstance(strategy, str)
            or strategy not in allowed_strategies
            or not isinstance(locator_value, str)
            or not 1 <= len(locator_value) <= 2000
            or type(priority) is not int
            or not 1 <= priority <= 20
        ):
            raise ProtocolError("Web locator candidate 无效")
        candidates.append(WebLocatorCandidate(strategy, locator_value, priority))
    return WebLocatorPlan(element_version_id, tuple(candidates))


def _web_actions(value: object) -> list[WebExecutionActionPlan]:
    if not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise ProtocolError("Web actions 数量无效")
    allowed_types = {
        "GOTO",
        "RELOAD",
        "BACK",
        "FORWARD",
        "FILL",
        "CLICK",
        "DOUBLE_CLICK",
        "RIGHT_CLICK",
        "CLEAR",
        "HOVER",
        "SELECT",
        "CHECK",
        "UNCHECK",
        "RADIO",
        "PRESS",
        "ENTER",
        "TAB",
        "WAIT_ELEMENT",
        "WAIT_URL",
        "WAIT_NETWORK_IDLE",
        "WAIT_TIME",
        "WAIT_TEXT",
        "DRAG_DROP",
        "UPLOAD",
        "DOWNLOAD",
        "COOKIE",
        "LOCAL_STORAGE",
        "SESSION_STORAGE",
        "JS_EVAL",
        "NEW_TAB",
        "SWITCH_TAB",
        "CLOSE_TAB",
    }
    locator_types = {
        "FILL",
        "CLICK",
        "DOUBLE_CLICK",
        "RIGHT_CLICK",
        "CLEAR",
        "HOVER",
        "SELECT",
        "CHECK",
        "UNCHECK",
        "RADIO",
        "PRESS",
        "ENTER",
        "TAB",
        "WAIT_ELEMENT",
        "WAIT_TEXT",
        "DRAG_DROP",
        "UPLOAD",
        "DOWNLOAD",
    }
    new_locator_types = {
        "DOUBLE_CLICK",
        "RIGHT_CLICK",
        "CLEAR",
        "HOVER",
        "CHECK",
        "UNCHECK",
        "RADIO",
        "ENTER",
        "TAB",
    }
    new_no_field_types = {"RELOAD", "BACK", "FORWARD", "WAIT_NETWORK_IDLE"}
    result: list[WebExecutionActionPlan] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "type",
            "timeout_ms",
            "failure_policy",
            "url",
            "locator",
            "value",
            "key",
        }:
            raise ProtocolError("Web action 字段不符合协议")
        action_type = item.get("type")
        timeout_ms = item.get("timeout_ms")
        failure_policy = item.get("failure_policy")
        if (
            not isinstance(action_type, str)
            or action_type not in allowed_types
            or type(timeout_ms) is not int
            or not 100 <= timeout_ms <= 600_000
            or failure_policy not in {"STOP", "CONTINUE"}
        ):
            raise ProtocolError("Web action 基本字段无效")
        url = item.get("url")
        if url is not None:
            url = _web_url(url, "action.url")
        locator = _web_locator(item.get("locator"))
        text_value = item.get("value")
        key = item.get("key")
        max_value_length = 1_400_000 if action_type == "UPLOAD" else 10_000
        if text_value is not None and (
            not isinstance(text_value, str) or len(text_value) > max_value_length
        ):
            raise ProtocolError("Web action.value 无效")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 256):
            raise ProtocolError("Web action.key 无效")
        if action_type in {"GOTO", "WAIT_URL"} and url is None:
            raise ProtocolError("Web action 缺少 url")
        if action_type == "NEW_TAB" and url is None:
            raise ProtocolError("Web action 缺少 url")
        if action_type in locator_types and locator is None:
            raise ProtocolError("Web action 缺少 locator")
        if (
            action_type
            in {
                "FILL",
                "SELECT",
                "WAIT_TIME",
                "WAIT_TEXT",
                "DRAG_DROP",
                "UPLOAD",
                "COOKIE",
                "LOCAL_STORAGE",
                "SESSION_STORAGE",
                "JS_EVAL",
            }
            and text_value is None
        ):
            raise ProtocolError("Web action 缺少 value")
        if action_type in {"NEW_TAB", "SWITCH_TAB", "CLOSE_TAB"} and (
            not isinstance(text_value, str) or not _WEB_TAB_ALIAS.fullmatch(text_value)
        ):
            raise ProtocolError("Web action 标签别名无效")
        if action_type == "NEW_TAB" and text_value == "main":
            raise ProtocolError("Web action 标签别名无效")
        if action_type == "PRESS" and key is None:
            raise ProtocolError("Web action 缺少 key")
        if action_type == "PRESS" and isinstance(key, str) and len(key) > 64:
            raise ProtocolError("Web action.key 无效")
        if action_type in {"COOKIE", "LOCAL_STORAGE", "SESSION_STORAGE"} and key is None:
            raise ProtocolError("Web action 缺少 key")
        if action_type == "UPLOAD" and (
            not isinstance(key, str)
            or not 1 <= len(key) <= 255
            or key in {".", ".."}
            or "/" in key
            or "\\" in key
        ):
            raise ProtocolError("UPLOAD 文件名无效")
        if action_type in {"WAIT_TEXT", "DRAG_DROP", "UPLOAD", "JS_EVAL"} and (
            not isinstance(text_value, str) or not text_value
        ):
            raise ProtocolError("Web action.value 无效")
        if action_type == "WAIT_TIME" and (
            not isinstance(text_value, str)
            or re.fullmatch(r"[1-9][0-9]{0,5}", text_value) is None
            or int(text_value) > 600_000
        ):
            raise ProtocolError("WAIT_TIME action.value 无效")
        if action_type in new_locator_types and any(
            field is not None for field in (url, text_value, key)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type in new_no_field_types and any(
            field is not None for field in (url, locator, text_value, key)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type in {"WAIT_TIME", "JS_EVAL"} and any(
            field is not None for field in (url, locator, key)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type in {"COOKIE", "LOCAL_STORAGE", "SESSION_STORAGE"} and any(
            field is not None for field in (url, locator)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type in {"WAIT_TEXT", "DRAG_DROP", "DOWNLOAD"} and any(
            field is not None for field in (url, key)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type == "UPLOAD" and url is not None:
            raise ProtocolError("Web action 包含不适用字段")
        if action_type == "UPLOAD":
            try:
                decoded = base64.b64decode(text_value, validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ProtocolError("UPLOAD 文件内容无效") from exc
            if not decoded or len(decoded) > 1024 * 1024:
                raise ProtocolError("UPLOAD 文件大小无效")
        if action_type == "NEW_TAB" and any(field is not None for field in (locator, key)):
            raise ProtocolError("Web action 包含不适用字段")
        if action_type in {"SWITCH_TAB", "CLOSE_TAB"} and any(
            field is not None for field in (url, locator, key)
        ):
            raise ProtocolError("Web action 包含不适用字段")
        result.append(
            WebExecutionActionPlan(
                action_type,
                timeout_ms,
                failure_policy,
                url,
                locator,
                text_value,
                key,
            )
        )
    return result


def _web_assertions(value: object) -> list[WebExecutionAssertionPlan]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("Web assertions 数量无效")
    result: list[WebExecutionAssertionPlan] = []
    for item in value:
        old_fields = {"type", "timeout_ms", "locator", "expected"}
        new_fields = {*old_fields, "key"}
        ai_fields = {*new_fields, "prompt_id", "criteria", "confidence_threshold"}
        if not isinstance(item, Mapping) or frozenset(item) not in {
            frozenset(old_fields),
            frozenset(new_fields),
            frozenset(ai_fields),
        }:
            raise ProtocolError("Web assertion 字段不符合协议")
        assertion_type = item.get("type")
        timeout_ms = item.get("timeout_ms")
        if (
            not isinstance(assertion_type, str)
            or assertion_type
            not in {
                "ASSERT_VISIBLE",
                "ASSERT_TEXT",
                "ASSERT_URL",
                "ASSERT_EXISTS",
                "ASSERT_ENABLED",
                "ASSERT_TEXT_EQUAL",
                "ASSERT_INPUT_VALUE",
                "ASSERT_TITLE",
                "ASSERT_HIDDEN",
                "ASSERT_CLICKABLE",
                "ASSERT_ATTRIBUTE",
                "ASSERT_ELEMENT_COUNT",
                "ASSERT_DOWNLOAD_SUCCESS",
                "ASSERT_NETWORK_REQUEST",
                "ASSERT_SCREENSHOT_VISUAL_COMPARE",
                "ASSERT_AI_SEMANTIC",
            }
            or type(timeout_ms) is not int
            or not 100 <= timeout_ms <= 600_000
        ):
            raise ProtocolError("Web assertion 基本字段无效")
        locator = _web_locator(item.get("locator"))
        expected = item.get("expected")
        key = item.get("key")
        prompt_id = item.get("prompt_id")
        criteria = item.get("criteria")
        confidence_threshold = item.get("confidence_threshold")
        max_expected_length = (
            1_400_000 if assertion_type == "ASSERT_SCREENSHOT_VISUAL_COMPARE" else 10_000
        )
        if expected is not None and (
            not isinstance(expected, str) or len(expected) > max_expected_length
        ):
            raise ProtocolError("Web assertion.expected 无效")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 256):
            raise ProtocolError("Web assertion.key 无效")
        if (
            assertion_type
            in {
                "ASSERT_VISIBLE",
                "ASSERT_TEXT",
                "ASSERT_EXISTS",
                "ASSERT_ENABLED",
                "ASSERT_TEXT_EQUAL",
                "ASSERT_INPUT_VALUE",
                "ASSERT_HIDDEN",
                "ASSERT_CLICKABLE",
                "ASSERT_ATTRIBUTE",
                "ASSERT_ELEMENT_COUNT",
            }
            and locator is None
        ):
            raise ProtocolError("Web assertion 缺少 locator")
        if (
            assertion_type
            in {
                "ASSERT_TEXT",
                "ASSERT_URL",
                "ASSERT_TEXT_EQUAL",
                "ASSERT_INPUT_VALUE",
                "ASSERT_TITLE",
                "ASSERT_ATTRIBUTE",
                "ASSERT_ELEMENT_COUNT",
                "ASSERT_DOWNLOAD_SUCCESS",
                "ASSERT_NETWORK_REQUEST",
                "ASSERT_SCREENSHOT_VISUAL_COMPARE",
            }
            and expected is None
        ):
            raise ProtocolError("Web assertion 缺少 expected")
        if (
            assertion_type
            in {
                "ASSERT_EXISTS",
                "ASSERT_ENABLED",
                "ASSERT_HIDDEN",
                "ASSERT_CLICKABLE",
            }
            and expected is not None
        ):
            raise ProtocolError("Web assertion 包含不适用 expected")
        if assertion_type == "ASSERT_TITLE" and locator is not None:
            raise ProtocolError("Web assertion 包含不适用 locator")
        if assertion_type == "ASSERT_ATTRIBUTE" and key is None:
            raise ProtocolError("ASSERT_ATTRIBUTE 缺少 key")
        if assertion_type != "ASSERT_ATTRIBUTE" and key is not None:
            raise ProtocolError("Web assertion 包含不适用 key")
        if assertion_type == "ASSERT_AI_SEMANTIC":
            if (
                type(prompt_id) is not int
                or prompt_id <= 0
                or not isinstance(criteria, str)
                or not 1 <= len(criteria) <= 4000
                or criteria != criteria.strip()
                or isinstance(confidence_threshold, bool)
                or not isinstance(confidence_threshold, (int, float))
                or not 0 <= confidence_threshold <= 1
                or any(field is not None for field in (locator, expected, key))
            ):
                raise ProtocolError("ASSERT_AI_SEMANTIC 配置无效")
        elif any(field is not None for field in (prompt_id, criteria, confidence_threshold)):
            raise ProtocolError("Web assertion 包含不适用 AI 字段")
        if assertion_type == "ASSERT_ELEMENT_COUNT" and (
            not isinstance(expected, str) or re.fullmatch(r"(0|[1-9][0-9]{0,5})", expected) is None
        ):
            raise ProtocolError("ASSERT_ELEMENT_COUNT expected 无效")
        if (
            assertion_type
            in {
                "ASSERT_DOWNLOAD_SUCCESS",
                "ASSERT_NETWORK_REQUEST",
                "ASSERT_SCREENSHOT_VISUAL_COMPARE",
            }
            and locator is not None
        ):
            raise ProtocolError("Web assertion 包含不适用 locator")
        if assertion_type == "ASSERT_NETWORK_REQUEST" and not expected:
            raise ProtocolError("ASSERT_NETWORK_REQUEST expected 无效")
        if assertion_type == "ASSERT_SCREENSHOT_VISUAL_COMPARE":
            try:
                baseline = base64.b64decode(expected, validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ProtocolError("Screenshot baseline 无效") from exc
            if not baseline.startswith(b"\x89PNG\r\n\x1a\n") or len(baseline) > 1024 * 1024:
                raise ProtocolError("Screenshot baseline 无效")
        result.append(
            WebExecutionAssertionPlan(
                assertion_type,
                timeout_ms,
                locator,
                expected,
                key,
                prompt_id,
                criteria,
                float(confidence_threshold) if confidence_threshold is not None else None,
            )
        )
    return result


def _web_session(value: object) -> WebSessionPlan | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ProtocolError("Web session 字段不符合协议")
    old_fields = {"profile_id", "storage_state"}
    new_fields = {
        "profile_id",
        "revision",
        "storage_state_fingerprint",
        "storage_state",
        "recovery",
    }
    if set(value) not in {old_fields, new_fields}:
        raise ProtocolError("Web session 字段不符合协议")
    profile_id = _required_positive_int(value.get("profile_id"), "profile_id")
    storage_state = value.get("storage_state")
    if storage_state is not None and (
        not isinstance(storage_state, Mapping) or len(storage_state) > 100
    ):
        raise ProtocolError("Web session storage_state 无效")
    if set(value) == old_fields and storage_state is None:
        raise ProtocolError("旧版 Web session storage_state 不能为空")
    try:
        encoded = json.dumps(storage_state, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("Web session storage_state 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 2_000_000:
        raise ProtocolError("Web session storage_state 超过 2MB")
    if set(value) == old_fields:
        return WebSessionPlan(profile_id, dict(storage_state or {}))
    revision = _required_positive_int(value.get("revision"), "revision")
    fingerprint = value.get("storage_state_fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ProtocolError("Web session storage_state_fingerprint 无效")
    recovery = _web_session_recovery(value.get("recovery"))
    if storage_state is None and (recovery is None or not recovery.force_refresh):
        raise ProtocolError("Web session 缺少可用状态或强制恢复计划")
    return WebSessionPlan(
        profile_id,
        dict(storage_state) if storage_state is not None else None,
        revision,
        fingerprint,
        recovery,
    )


def _web_session_condition(value: object) -> WebSessionConditionPlan:
    if not isinstance(value, Mapping) or set(value) != {"type", "value", "locator"}:
        raise ProtocolError("Web session condition 字段不符合协议")
    condition_type = value.get("type")
    condition_value = value.get("value")
    locator = _web_locator(value.get("locator"))
    if condition_type not in {
        "URL_EQUALS",
        "URL_CONTAINS",
        "LOCATOR_VISIBLE",
        "LOCATOR_HIDDEN",
    }:
        raise ProtocolError("Web session condition type 无效")
    if str(condition_type).startswith("URL_"):
        if (
            not isinstance(condition_value, str)
            or not 1 <= len(condition_value) <= 2048
            or locator is not None
        ):
            raise ProtocolError("Web session URL condition 无效")
    elif condition_value is not None or locator is None:
        raise ProtocolError("Web session Locator condition 无效")
    return WebSessionConditionPlan(str(condition_type), condition_value, locator)


def _web_session_recovery(value: object) -> WebSessionRecoveryPlan | None:
    if value is None:
        return None
    fields = {
        "login_web_case_id",
        "login_web_case_version_id",
        "force_refresh",
        "expiry_condition",
        "success_condition",
        "start_url",
        "actions",
        "assertions",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ProtocolError("Web session recovery 字段不符合协议")
    login_case_id = _required_positive_int(value.get("login_web_case_id"), "login_web_case_id")
    login_version_id = _required_positive_int(
        value.get("login_web_case_version_id"), "login_web_case_version_id"
    )
    force_refresh = _required_bool(value.get("force_refresh"), "force_refresh")
    start_url = _required_text(value, "start_url", max_length=2048)
    return WebSessionRecoveryPlan(
        login_case_id,
        login_version_id,
        force_refresh,
        _web_session_condition(value.get("expiry_condition")),
        _web_session_condition(value.get("success_condition")),
        start_url,
        tuple(_web_actions(value.get("actions"))),
        tuple(_web_assertions(value.get("assertions"))),
    )


def _web_secrets(value: object) -> list[WebExecutionSecret]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("Web secrets 数量无效")
    result: list[WebExecutionSecret] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"name", "value"}:
            raise ProtocolError("Web secret 字段不符合协议")
        name = item.get("name")
        secret = item.get("value")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) is None
            or name in seen
            or not isinstance(secret, str)
            or not 1 <= len(secret) <= 4096
        ):
            raise ProtocolError("Web secret 无效或重复")
        seen.add(name)
        result.append(WebExecutionSecret(name, secret))
    return result


def _web_refreshed_session_payload(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = {
        "profile_id",
        "expected_revision",
        "expected_fingerprint",
        "storage_state",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ConfigurationError("Web refreshed_session 字段不符合协议")
    profile_id = _required_positive_int(value.get("profile_id"), "profile_id")
    expected_revision = _required_positive_int(value.get("expected_revision"), "expected_revision")
    fingerprint = value.get("expected_fingerprint")
    storage_state = value.get("storage_state")
    if (
        not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or not isinstance(storage_state, Mapping)
        or not storage_state
    ):
        raise ConfigurationError("Web refreshed_session 无效")
    try:
        _safe_json_mapping(
            storage_state,
            "refreshed_session.storage_state",
            max_items=100,
            max_bytes=1_000_000,
        )
    except ProtocolError as exc:
        raise ConfigurationError("Web refreshed_session storage_state 无效") from exc
    return {
        "profile_id": profile_id,
        "expected_revision": expected_revision,
        "expected_fingerprint": fingerprint,
        "storage_state": dict(storage_state),
    }


def _web_session_recovery_audit_payload(value: object, *, response: bool) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = {
        "status",
        "reason",
        "login_web_case_version_id",
        "duration_ms",
        "error_type",
        "persistence_status",
    }
    error_class = ProtocolError if response else ConfigurationError
    if not isinstance(value, Mapping) or set(value) != fields:
        raise error_class("Web session_recovery 字段不符合协议")
    status = value.get("status")
    reason = value.get("reason")
    version_id = value.get("login_web_case_version_id")
    duration_ms = value.get("duration_ms")
    error_type = value.get("error_type")
    persistence_status = value.get("persistence_status")
    if (
        status not in {"NOT_NEEDED", "SUCCESS", "FAILED"}
        or reason not in {"PROFILE_EXPIRED", "EXPIRY_CONDITION", "NOT_EXPIRED"}
        or type(version_id) is not int
        or version_id <= 0
        or type(duration_ms) is not int
        or not 0 <= duration_ms <= 86_400_000
        or (error_type is not None and (not isinstance(error_type, str) or len(error_type) > 100))
        or persistence_status
        not in ({"UPDATED", "CONFLICT", "NOT_ATTEMPTED"} if response else {None})
    ):
        raise error_class("Web session_recovery 无效")
    return dict(value)


def _nested_string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping):
        result: list[str] = []
        for child in value.values():
            result.extend(_nested_string_values(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_nested_string_values(child))
        return result
    return []


def _web_trace_payload(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 300:
        raise ProtocolError("Web traces 必须是有限数组")
    allowed = {
        "node_id",
        "status",
        "duration_ms",
        "error_type",
        "error_message",
        "locator_attempts",
        "healing_context",
    }
    statuses = {"SUCCESS", "FAILED", "REVIEW", "SKIPPED", "TIMEOUT", "CANCELLED"}
    locator_strategies = {"css", "xpath", "text", "role", "label", "placeholder", "test_id"}
    locator_statuses = {"NOT_FOUND", "ACTION_FAILED", "SUCCESS"}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) - allowed
            or not {"node_id", "status"} <= set(item)
        ):
            raise ProtocolError("Web trace 字段不符合协议")
        node_id = item.get("node_id")
        status = item.get("status")
        duration_ms = item.get("duration_ms", 0)
        error_type = item.get("error_type")
        error_message = item.get("error_message")
        locator_attempts = item.get("locator_attempts", [])
        healing_context_present = "healing_context" in item
        if healing_context_present and item.get("healing_context") is None:
            raise ProtocolError("Web healing_context 不得为 null")
        healing_context = _healing_context_payload(item.get("healing_context"))
        if (
            not isinstance(node_id, str)
            or not 1 <= len(node_id) <= 64
            or node_id in seen
            or not isinstance(status, str)
            or status not in statuses
            or type(duration_ms) is not int
            or not 0 <= duration_ms <= 86_400_000
        ):
            raise ProtocolError("Web trace 基本字段无效")
        if error_type is not None and (
            not isinstance(error_type, str) or not 1 <= len(error_type) <= 100
        ):
            raise ProtocolError("Web trace error_type 无效")
        if error_message is not None and (
            not isinstance(error_message, str) or not 1 <= len(error_message) <= 500
        ):
            raise ProtocolError("Web trace error_message 无效")
        if not isinstance(locator_attempts, list) or len(locator_attempts) > 20:
            raise ProtocolError("Web trace locator_attempts 无效")
        normalized_attempts: list[dict[str, Any]] = []
        for attempt in locator_attempts:
            if not isinstance(attempt, Mapping) or set(attempt) != {
                "strategy",
                "priority",
                "status",
            }:
                raise ProtocolError("Web trace locator_attempt 字段无效")
            strategy = attempt.get("strategy")
            priority = attempt.get("priority")
            attempt_status = attempt.get("status")
            if (
                not isinstance(strategy, str)
                or strategy not in locator_strategies
                or type(priority) is not int
                or not 1 <= priority <= 20
                or not isinstance(attempt_status, str)
                or attempt_status not in locator_statuses
            ):
                raise ProtocolError("Web trace locator_attempt 值无效")
            normalized_attempts.append(
                {"strategy": strategy, "priority": priority, "status": attempt_status}
            )
        if healing_context_present and (
            healing_context is None
            or status != "FAILED"
            or error_type not in {"WEB_LOCATOR_NOT_FOUND", "ASSERTION_FAILED"}
            or not normalized_attempts
            or any(attempt["status"] != "NOT_FOUND" for attempt in normalized_attempts)
        ):
            raise ProtocolError("Web healing_context 与失败定位语义不匹配")
        seen.add(node_id)
        normalized_trace = {
            "node_id": node_id,
            "status": status,
            "duration_ms": duration_ms,
            "error_type": redact_text(error_type) if error_type is not None else None,
            "error_message": redact_text(error_message) if error_message is not None else None,
            "locator_attempts": normalized_attempts,
        }
        if healing_context_present and healing_context is not None:
            normalized_trace["healing_context"] = healing_context
        result.append(normalized_trace)
    return result


def _healing_context_payload(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "trigger",
        "element_version_id",
        "page_url",
        "page_title",
        "dom_candidates",
    }:
        raise ProtocolError("Web healing_context 字段不符合协议")
    if value.get("schema_version") != 1 or value.get("trigger") != "ALL_LOCATORS_FAILED":
        raise ProtocolError("Web healing_context 版本或触发条件无效")
    element_version_id = value.get("element_version_id")
    if element_version_id is not None and (
        isinstance(element_version_id, bool)
        or not isinstance(element_version_id, int)
        or element_version_id <= 0
    ):
        raise ProtocolError("Web healing_context element_version_id 无效")
    page_url = value.get("page_url")
    if page_url is not None:
        if not isinstance(page_url, str) or not 1 <= len(page_url) <= 2048:
            raise ProtocolError("Web healing_context page_url 无效")
        from urllib.parse import urlsplit

        parsed = urlsplit(page_url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ProtocolError("Web healing_context page_url 必须是脱敏 URL")
    page_title = value.get("page_title")
    if not isinstance(page_title, str) or not 0 <= len(page_title) <= 200:
        raise ProtocolError("Web healing_context page_title 无效")
    if page_title != page_title.strip():
        raise ProtocolError("Web healing_context page_title 必须去除首尾空格")
    if "\r" in page_title or "\n" in page_title or _healing_sensitive_text(page_title):
        raise ProtocolError("Web healing_context page_title 不安全")
    candidates = value.get("dom_candidates")
    if not isinstance(candidates, list) or len(candidates) > 40:
        raise ProtocolError("Web healing_context dom_candidates 无效")
    allowed_candidate_fields = {
        "tag",
        "role",
        "id",
        "name",
        "aria-label",
        "placeholder",
        "data-testid",
        "type",
        "title",
    }
    candidate_field_order = (
        "tag",
        "role",
        "id",
        "name",
        "aria-label",
        "placeholder",
        "data-testid",
        "type",
        "title",
    )
    locating_fields = {"role", "id", "name", "aria-label", "placeholder", "data-testid", "title"}
    normalized_candidates: list[dict[str, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or set(candidate) - allowed_candidate_fields:
            raise ProtocolError("Web healing_context candidate 字段无效")
        normalized: dict[str, str] = {}
        for key in candidate_field_order:
            item = candidate.get(key)
            if item is None:
                continue
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= 200
                or item != item.strip()
                or "\r" in item
                or "\n" in item
                or _healing_sensitive_text(item)
            ):
                raise ProtocolError("Web healing_context candidate 值不安全")
            normalized[key] = item
        if "tag" not in normalized or not locating_fields.intersection(normalized):
            raise ProtocolError("Web healing_context candidate 缺少定位属性")
        normalized_candidates.append(
            {key: normalized[key] for key in candidate_field_order if key in normalized}
        )
    normalized_context: dict[str, Any] = {
        "schema_version": 1,
        "trigger": "ALL_LOCATORS_FAILED",
        "element_version_id": element_version_id,
        "page_url": page_url,
        "page_title": page_title,
        "dom_candidates": normalized_candidates,
    }
    try:
        encoded = json.dumps(normalized_context, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("Web healing_context 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ProtocolError("Web healing_context 超过 64KB")
    return normalized_context


def _healing_sensitive_text(value: str) -> bool:
    return (
        re.search(
            r"(?i)(authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)",
            value,
        )
        is not None
    )


def _valid_identifier(value: object, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= max_length
        and all(
            character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in value
        )
    )


def _validate_execution_identifiers(run_id: str, message_id: str) -> None:
    if not _valid_identifier(run_id, max_length=128):
        raise ConfigurationError("本地 run_id 无效")
    if not _valid_identifier(message_id, max_length=64):
        raise ConfigurationError("任务 message_id 无效")


def _validate_case_run_id(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError("case_run_id 必须是正整数")


def _required_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"服务端响应缺少有效字段：{field_name}")
    return value


def _required_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ProtocolError(f"服务端响应字段 {field_name} 必须是 bool")
    return value


def _required_retry_count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 3:
        raise ProtocolError("服务端响应字段 retry_count 必须是 0 到 3 的整数")
    return value


def _require_exact_keys(payload: Mapping[str, Any], expected: set[str], operation: str) -> None:
    if set(payload) != expected:
        raise ProtocolError(f"服务端 {operation} 响应字段不符合协议")


def _execution_response_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "status_code",
        "json_body",
        "text",
        "headers",
        "cookies",
        "elapsed_ms",
        "response_time_ms",
    }
    if set(value) - allowed:
        raise ConfigurationError("response 包含不支持的字段")
    status_code = value.get("status_code")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not 100 <= status_code <= 599
    ):
        raise ConfigurationError("response.status_code 无效")
    json_body = value.get("json_body")
    text = value.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > 2_000_000):
        raise ConfigurationError("response.text 长度无效")
    try:
        encoded_body = json.dumps(
            {"json_body": json_body, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigurationError("response body 无法安全编码") from exc
    if len(encoded_body) > 2_000_000:
        raise ConfigurationError("response body 超过 2,000,000 bytes")
    headers = _wire_response_map(value.get("headers", {}), "response.headers")
    cookies = _wire_response_map(value.get("cookies", {}), "response.cookies")
    elapsed_ms = _optional_nonnegative_int(value.get("elapsed_ms"), "elapsed_ms")
    response_time_ms = _optional_nonnegative_int(value.get("response_time_ms"), "response_time_ms")
    if elapsed_ms is not None and response_time_ms is not None and elapsed_ms != response_time_ms:
        raise ConfigurationError("response elapsed_ms 与 response_time_ms 必须一致")
    return {
        "status_code": status_code,
        "json_body": json_body,
        "text": text,
        "headers": headers,
        "cookies": cookies,
        "elapsed_ms": elapsed_ms,
        "response_time_ms": response_time_ms,
    }


def _wire_response_map(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 100:
        raise ConfigurationError(f"{field_name} 数量无效")
    result: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str):
            raise ConfigurationError(f"{field_name} 类型无效")
        if len(name) > 255 or len(item) > 10_000:
            raise ConfigurationError(f"{field_name} 长度无效")
        result[name] = item
    return result


def _optional_nonnegative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 86_400_000:
        raise ConfigurationError(f"response.{field_name} 无效")
    return value


def _optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ProtocolError(f"服务端响应字段 {field_name} 无效")
    return value


def _recording_schema(payload: Mapping[str, Any]) -> int:
    value = payload.get("schema_version")
    if type(value) is not int or value != 1:
        raise ProtocolError("Web 录制响应 schema_version 无效")
    return value


def _recording_text(payload: Mapping[str, Any], field_name: str, max_length: int) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ProtocolError(f"Web 录制响应字段 {field_name} 无效")
    return value


def _recording_message(payload: Mapping[str, Any], field_name: str) -> str:
    value = _recording_text(payload, field_name, 64)
    if not _valid_identifier(value, max_length=64):
        raise ProtocolError(f"Web 录制响应字段 {field_name} 无效")
    return value


def _recording_status(value: object) -> str:
    if value not in {
        "CREATED",
        "QUEUED",
        "RUNNING",
        "STOP_REQUESTED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }:
        raise ProtocolError("Web 录制响应 status 无效")
    return str(value)


def _recording_optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_positive_int(value, field_name)


def _recording_optional_text(value: object, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ProtocolError(f"Web 录制响应字段 {field_name} 无效")
    return value


def _safe_recording_error(value: object, credential: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigurationError("Web 录制错误信息无效")
    return redact_text(value, (credential,), limit=max_length)


def _recording_optional_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value, field_name=field_name)


def _recording_plan_url(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(c.isspace() for c in value):
        raise ProtocolError("Web 录制 start_url 无效")
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ProtocolError("Web 录制 start_url 必须是 http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ProtocolError("Web 录制 start_url 不允许凭据或 fragment")
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if re.search(
            r"(?i)(authorization|cookie|token|password|secret|credential|api[_-]?key)",
            name,
        ):
            raise ProtocolError("Web 录制 start_url 不能包含敏感查询参数")
    return value


def _recording_session(value: object) -> WebRecordingSession | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"profile_id", "storage_state"}:
        raise ProtocolError("Web 录制 session 字段不符合协议")
    profile_id = _required_positive_int(value.get("profile_id"), "profile_id")
    storage_state = value.get("storage_state")
    if not isinstance(storage_state, Mapping):
        raise ProtocolError("Web 录制 storage_state 无效")
    _safe_json_mapping(storage_state, "storage_state", max_items=100, max_bytes=2_000_000)
    return WebRecordingSession(profile_id, dict(storage_state))


def _exploration_session(value: object) -> WebExplorationSession | None:
    recording_session = _recording_session(value)
    if recording_session is None:
        return None
    return WebExplorationSession(
        profile_id=recording_session.profile_id,
        storage_state=recording_session.storage_state,
    )


def _exploration_text_list(
    value: object, field_name: str, *, maximum: int, item_max: int
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ProtocolError(f"Web 探索 {field_name} 数量无效")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item) <= item_max:
            raise ProtocolError(f"Web 探索 {field_name} 内容无效")
        normalized.append(item)
    return tuple(normalized)


def _exploration_origin(value: object) -> str:
    """Normalize one exact http(s) origin; paths, credentials and fragments are forbidden."""

    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ProtocolError("Web 探索 allowed origin 无效")
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProtocolError("Web 探索 allowed origin 必须是安全的 http(s) origin")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _web_url_origin(value: object) -> str:
    from urllib.parse import urlsplit

    normalized = _recording_plan_url(value)
    parsed = urlsplit(normalized)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _validate_exploration_observation(value: Mapping[str, Any]) -> None:
    expected = {
        "sequence",
        "page_url",
        "title",
        "accessibility_snapshot",
        "network_events",
        "previous_action",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ConfigurationError("Web 探索 observation 字段无效")
    sequence = value.get("sequence")
    if type(sequence) is not int or not 1 <= sequence <= 50:
        raise ConfigurationError("Web 探索 observation sequence 无效")
    _recording_plan_url(value.get("page_url"))
    title = value.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 500):
        raise ConfigurationError("Web 探索 observation title 无效")
    snapshot = value.get("accessibility_snapshot")
    if not isinstance(snapshot, str) or not 1 <= len(snapshot) <= 200_000:
        raise ConfigurationError("Web 探索 accessibility snapshot 无效")
    network = value.get("network_events")
    if not isinstance(network, list) or len(network) > 100:
        raise ConfigurationError("Web 探索 network events 无效")
    previous = value.get("previous_action")
    if previous is not None and (not isinstance(previous, Mapping) or len(previous) > 20):
        raise ConfigurationError("Web 探索 previous action 无效")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigurationError("Web 探索 observation 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > 256_000:
        raise ConfigurationError("Web 探索 observation 超过 256KB")


def _validate_exploration_decision(value: object) -> dict[str, Any]:
    expected = {
        "action",
        "reason",
        "element",
        "ref",
        "value",
        "url",
        "expected_observation",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ProtocolError("Web 探索 decision 字段无效")
    action = value.get("action")
    if action not in {"NAVIGATE", "CLICK", "TYPE", "SELECT", "PRESS", "WAIT", "FINISH"}:
        raise ProtocolError("Web 探索 decision action 无效")
    reason = value.get("reason")
    if not isinstance(reason, str) or not 1 <= len(reason) <= 1000:
        raise ProtocolError("Web 探索 decision reason 无效")
    for name, maximum in (("element", 500), ("ref", 128), ("value", 2000)):
        item = value.get(name)
        if item is not None and (not isinstance(item, str) or not 1 <= len(item) <= maximum):
            raise ProtocolError(f"Web 探索 decision {name} 无效")
    element = value.get("element")
    target = value.get("ref")
    decision_value = value.get("value")
    if action in {"CLICK", "TYPE", "SELECT"}:
        if not isinstance(element, str) or not isinstance(target, str):
            raise ProtocolError(f"Web 探索 {action} 缺少元素引用")
        if re.fullmatch(r"[A-Za-z0-9_-]+", target) is None:
            raise ProtocolError("Web 探索 ref 不是快照引用")
    if action in {"TYPE", "SELECT", "PRESS"} and not isinstance(decision_value, str):
        raise ProtocolError(f"Web 探索 {action} 缺少 value")
    url = value.get("url")
    if url is not None:
        _recording_plan_url(url)
    expected_observation = value.get("expected_observation")
    if expected_observation is not None and (
        not isinstance(expected_observation, str) or len(expected_observation) > 1000
    ):
        raise ProtocolError("Web 探索 expected_observation 无效")
    return dict(value)


def _safe_json_mapping(
    value: Mapping[str, Any], field_name: str, *, max_items: int, max_bytes: int
) -> None:
    if len(value) > max_items:
        raise ProtocolError(f"Web 录制 {field_name} 数量无效")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError(f"Web 录制 {field_name} 不是安全 JSON") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ProtocolError(f"Web 录制 {field_name} 超过大小限制")


def _recording_events_payload(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 1000:
        raise ConfigurationError("Web 录制 events 数量无效")
    allowed = {
        "event_type",
        "sequence",
        "relative_time_ms",
        "page_url",
        "title",
        "target_url",
        "locator_candidates",
        "value",
        "key",
    }
    event_types = {"NAVIGATE", "CLICK", "FILL", "SELECT", "PRESS"}
    keys = {
        "Enter",
        "Tab",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "PageUp",
        "PageDown",
    }
    result: list[dict[str, Any]] = []
    seen_sequences: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != allowed:
            raise ConfigurationError("Web 录制 event 字段不符合协议")
        event_type = item.get("event_type")
        sequence = item.get("sequence")
        relative_time_ms = item.get("relative_time_ms")
        if (
            event_type not in event_types
            or type(sequence) is not int
            or not 0 < sequence <= 1_000_000
        ):
            raise ConfigurationError("Web 录制 event 基本字段无效")
        if (
            sequence in seen_sequences
            or type(relative_time_ms) is not int
            or not 0 <= relative_time_ms <= 86_400_000
        ):
            raise ConfigurationError("Web 录制 event 序列或时间无效")
        seen_sequences.add(sequence)
        page_url = _recording_event_url(item.get("page_url"), "page_url")
        target_url = _recording_event_url(item.get("target_url"), "target_url")
        title = item.get("title")
        if title is not None and (
            not isinstance(title, str) or len(title) > 512 or "\n" in title or "\r" in title
        ):
            raise ConfigurationError("Web 录制 event title 无效")
        locators = item.get("locator_candidates")
        if not isinstance(locators, list) or len(locators) > 10:
            raise ConfigurationError("Web 录制 locator_candidates 无效")
        normalized_locators: list[dict[str, Any]] = []
        priorities: set[int] = set()
        for locator in locators:
            if not isinstance(locator, Mapping) or set(locator) != {
                "strategy",
                "value",
                "priority",
            }:
                raise ConfigurationError("Web 录制 locator 字段无效")
            strategy = locator.get("strategy")
            locator_value = locator.get("value")
            priority = locator.get("priority")
            if strategy not in {"css", "xpath", "text", "role", "label", "placeholder", "test_id"}:
                raise ConfigurationError("Web 录制 locator strategy 无效")
            if not isinstance(locator_value, str) or not 1 <= len(locator_value) <= 2000:
                raise ConfigurationError("Web 录制 locator value 无效")
            if type(priority) is not int or not 1 <= priority <= 20 or priority in priorities:
                raise ConfigurationError("Web 录制 locator priority 无效")
            priorities.add(priority)
            normalized_locators.append(
                {"strategy": strategy, "value": locator_value, "priority": priority}
            )
        event_value = item.get("value")
        if event_value is not None and (
            not isinstance(event_value, str) or len(event_value) > 10_000
        ):
            raise ConfigurationError("Web 录制 event value 无效")
        key = item.get("key")
        if key is not None and (not isinstance(key, str) or key not in keys):
            raise ConfigurationError("Web 录制 event key 无效")
        if event_type == "NAVIGATE" and (
            target_url is None or normalized_locators or event_value is not None or key is not None
        ):
            raise ConfigurationError("Web 录制 NAVIGATE 字段无效")
        if event_type == "FILL" and (
            event_value not in {"REDACTED"}
            and not (
                isinstance(event_value, str)
                and re.fullmatch(r"\{\{recording\.[A-Za-z][A-Za-z0-9_.-]*\}\}", event_value)
            )
        ):
            raise ConfigurationError("Web 录制 FILL value 必须是安全占位符")
        if event_type == "SELECT" and (
            not isinstance(event_value, str)
            or not event_value
            or re.search(
                r"(?i)(password|token|secret|cookie|authorization|credential|api[_-]?key)",
                event_value,
            )
        ):
            raise ConfigurationError("Web 录制 SELECT value 不安全")
        if event_type == "PRESS" and (
            key is None or event_value is not None or target_url is not None
        ):
            raise ConfigurationError("Web 录制 PRESS 字段无效")
        if event_type in {"CLICK", "FILL", "SELECT", "PRESS"} and not normalized_locators:
            raise ConfigurationError("Web 录制交互事件必须提供 locator")
        result.append(
            {
                "event_type": event_type,
                "sequence": sequence,
                "relative_time_ms": relative_time_ms,
                "page_url": page_url,
                "title": title,
                "target_url": target_url,
                "locator_candidates": normalized_locators,
                "value": event_value,
                "key": key,
            }
        )
    return result


def _recording_event_url(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(c.isspace() for c in value):
        raise ConfigurationError(f"Web 录制 event {field_name} 无效")
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ConfigurationError(f"Web 录制 event {field_name} 无效")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _parse_assertion_results(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ProtocolError("服务端 assertion_results 必须是有限数组")
    required = {"sequence", "name", "type", "status", "message", "duration_ms"}
    allowed = required | {
        "expected",
        "actual",
        "confidence",
        "reason",
        "ai_call_id",
        "actual_model",
        "fallback_used",
        "repair_used",
    }
    results: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or not required <= set(item) or set(item) - allowed:
            raise ProtocolError("服务端 assertion_results 字段不符合协议")
        sequence = item.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ProtocolError("服务端 assertion_results.sequence 无效")
        if not isinstance(item.get("name"), str) or not isinstance(item.get("type"), str):
            raise ProtocolError("服务端 assertion_results 名称或类型无效")
        if item.get("status") not in {"PASS", "FAIL", "REVIEW", "SKIPPED"}:
            raise ProtocolError("服务端 assertion_results.status 无效")
        if not isinstance(item.get("message"), str):
            raise ProtocolError("服务端 assertion_results.message 无效")
        duration_ms = item.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
            raise ProtocolError("服务端 assertion_results.duration_ms 无效")
        confidence = item.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ProtocolError("服务端 assertion_results.confidence 无效")
        for key in ("ai_call_id",):
            if (
                key in item
                and item[key] is not None
                and (
                    isinstance(item[key], bool) or not isinstance(item[key], int) or item[key] <= 0
                )
            ):
                raise ProtocolError(f"服务端 assertion_results.{key} 无效")
        for key in ("fallback_used", "repair_used"):
            if key in item and item[key] is not None and type(item[key]) is not bool:
                raise ProtocolError(f"服务端 assertion_results.{key} 无效")
        for key in ("reason", "actual_model"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise ProtocolError(f"服务端 assertion_results.{key} 无效")
        results.append(dict(item))
    return results
