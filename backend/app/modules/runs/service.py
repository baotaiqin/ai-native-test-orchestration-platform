import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    EvidenceStorageError,
    RedisUnavailableError,
    ResourceConflictError,
    ResourceNotFoundError,
    RunDispatchPendingError,
    RunExecutionUnsupportedError,
    RunStateConflictError,
    RunValidationError,
    SecurityConfigurationError,
)
from app.core.logging import get_logger
from app.core.metrics import RUNS_CREATED
from app.core.secret_cipher import decrypt_secret, encrypt_secret
from app.core.time import to_utc_aware, utc_now_aware, utc_now_naive
from app.infrastructure.object_store.client import ObjectStore
from app.infrastructure.rabbitmq.client import TaskPublisher, TaskPublishResult
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    RedisStoreUnavailableError,
)
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.datasets.models import Dataset, DatasetVersion
from app.modules.datasets.service import validate_iteration_mapping
from app.modules.environments.models import Environment
from app.modules.environments.resolver import build_environment_context, resolve_template
from app.modules.evidence.schemas import WebEvidenceUploadResponse
from app.modules.evidence.service import (
    create_execution_evidence,
    create_web_evidence,
    to_web_upload_response,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import (
    ensure_project_writable,
    get_project,
    is_admin,
)
from app.modules.resource_registry.models import ResourceRegistryEntry
from app.modules.resource_registry.schemas import CleanupStatus, validate_secret_free_payload
from app.modules.resource_registry.security import (
    validate_sql_cleanup,
    validate_sql_execute,
    validate_sql_query,
)
from app.modules.resource_registry.service import validate_cleanup_scope
from app.modules.run_requirement_snapshots.service import (
    capture_case_run_requirement_sources,
)
from app.modules.runners.models import Runner
from app.modules.runners.schemas import (
    OnlineStatus,
    RunnerCapabilityName,
    RunnerDisconnectReconcileResponse,
    RunnerSlotType,
    RunnerStatus,
)
from app.modules.runners.security import matches_digest
from app.modules.runners.service import get_runner_online_state
from app.modules.runs.enums import (
    TERMINAL_NODE_STATUSES,
    TERMINAL_RUN_STATUSES,
    DispatchOutboxStatus,
    RunNodeStatus,
    RunStatus,
    RunType,
)
from app.modules.runs.events import publish_run_event_best_effort
from app.modules.runs.models import (
    CaseRun,
    RunApiExecutionEvaluation,
    RunApiExecutionResult,
    RunDispatchOutbox,
    RunScenarioExecutionResult,
    RunWebExecutionResult,
    StepRun,
    TestRun,
)
from app.modules.runs.repository import (
    get_dispatch_outbox_for_run,
    get_run,
    get_run_with_children,
    list_case_runs,
    list_runs,
    list_step_runs,
    list_web_execution_results,
)
from app.modules.runs.schemas import (
    CaseRunDetailResponse,
    CaseRunListResponse,
    CaseRunResponse,
    ExecutionActionAuditResponse,
    ExecutionCancellationResponse,
    ExecutionCompleteRequest,
    ExecutionCompleteResponse,
    ExecutionEvaluationRequest,
    ExecutionEvaluationResponse,
    ExecutionOutcome,
    ExecutionPlanResponse,
    ExecutionResponseSnapshot,
    ExecutionStartRequest,
    ExecutionStartResponse,
    NodeStatusUpdateRequest,
    RunBatchDispatchItemResponse,
    RunBatchDispatchRequest,
    RunBatchDispatchResponse,
    RunClaimRequest,
    RunClaimResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunDispatchResponse,
    RunEventListResponse,
    RunEventResponse,
    RunListResponse,
    RunResponse,
    RunStatusUpdateRequest,
    RunTaskEnvelope,
    RunValidationIssue,
    RunValidationResponse,
    ScenarioExecutionCompleteRequest,
    ScenarioExecutionCompleteResponse,
    ScenarioExecutionEvaluationRequest,
    ScenarioExecutionEvaluationResponse,
    ScenarioExecutionNodePlan,
    ScenarioExecutionOutcome,
    ScenarioExecutionPlanResponse,
    ScenarioExecutionSecret,
    ScenarioExecutionStartResponse,
    ScenarioExecutionTrace,
    ScenarioSqlConnectionPlan,
    StepRunListResponse,
    StepRunResponse,
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionBrowserConfig,
    WebExecutionCompleteRequest,
    WebExecutionCompleteResponse,
    WebExecutionEvaluationRequest,
    WebExecutionEvaluationResponse,
    WebExecutionOutcome,
    WebExecutionPlanResponse,
    WebExecutionProxyConfig,
    WebExecutionSecret,
    WebExecutionStartResponse,
    WebExecutionTrace,
    WebHealingContext,
    WebLocatorCandidate,
    WebLocatorPlan,
    WebSessionConditionPlan,
    WebSessionPlan,
    WebSessionRecoveryAudit,
    WebSessionRecoveryPlan,
)
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.scenarios.safe_script import run_safe_script, validate_safe_script
from app.modules.scenarios.schemas import ScenarioDsl, ScenarioNodeType
from app.modules.scenarios.validator import validate_scenario_dsl
from app.modules.secrets.models import Secret
from app.modules.secrets.service import resolve_secret
from app.modules.test_cases.assertions import (
    run_assertion,
    sanitize_response_snapshot,
    summarize_final_status,
)
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.test_cases.retry_limit import (
    API_STEP_RETRY_LIMIT_ERROR_CODE,
    API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
    ensure_v1_api_step_retry_limit,
    is_v1_api_step_retry_limit,
)
from app.modules.test_cases.schemas import (
    ActionType,
    ApiRequestTemplate,
    ApiSetupAction,
    Assertion,
    AssertionKind,
    AssertionResult,
    AssertionStatus,
    AuthType,
    CaseType,
    GetTokenAction,
    SqlQueryAction,
    SuggestedCase,
    action_type,
)
from app.modules.test_cases.service import validate_ai_assertion_definition
from app.modules.web_cases.models import (
    SessionProfile,
    WebCase,
    WebCaseVersion,
    WebElement,
    WebElementVersion,
    WebPage,
)
from app.modules.web_cases.schemas import SessionRecoveryCondition, WebCaseContent
from app.modules.web_healing.models import WebHealingProposal, WebHealingValidation
from app.modules.web_healing.schemas import HealingLocator

logger = get_logger(__name__)

_RUNTIME_TEMPLATE = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$")
_API_SECRET_TEMPLATE = re.compile(r"^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|token|access[_-]?token|refresh[_-]?token|"
    r"authorization|cookie|secret|api[_-]?key|credential)\s*[:=]\s*[^\s,;]+)"
)
_UNSUPPORTED_CONFIG_KEYS = {
    "browser",
    "browser_name",
    "browser_version",
    "required_file",
    "required_files",
    "file_path",
    "model_id",
    "model_name",
    "ai_model",
    "ai_model_id",
}

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.QUEUED: frozenset(
        {RunStatus.ASSIGNED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.TIMEOUT}
    ),
    RunStatus.ASSIGNED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.TIMEOUT,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLING, RunStatus.TIMEOUT}
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED}),
    RunStatus.SUCCESS: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.TIMEOUT: frozenset(),
}

_NODE_TRANSITIONS: dict[RunNodeStatus, frozenset[RunNodeStatus]] = {
    RunNodeStatus.CREATED: frozenset(
        {
            RunNodeStatus.ASSIGNED,
            RunNodeStatus.RUNNING,
            RunNodeStatus.SUCCESS,
            RunNodeStatus.FAILED,
            RunNodeStatus.REVIEW,
            RunNodeStatus.CANCELLED,
            RunNodeStatus.TIMEOUT,
            RunNodeStatus.SKIPPED,
        }
    ),
    RunNodeStatus.ASSIGNED: frozenset(
        {
            RunNodeStatus.RUNNING,
            RunNodeStatus.SUCCESS,
            RunNodeStatus.FAILED,
            RunNodeStatus.REVIEW,
            RunNodeStatus.CANCELLED,
            RunNodeStatus.TIMEOUT,
            RunNodeStatus.SKIPPED,
        }
    ),
    RunNodeStatus.RUNNING: frozenset(
        {
            RunNodeStatus.SUCCESS,
            RunNodeStatus.FAILED,
            RunNodeStatus.REVIEW,
            RunNodeStatus.CANCELLING,
            RunNodeStatus.CANCELLED,
            RunNodeStatus.TIMEOUT,
            RunNodeStatus.SKIPPED,
        }
    ),
    RunNodeStatus.CANCELLING: frozenset({RunNodeStatus.CANCELLED}),
    RunNodeStatus.SUCCESS: frozenset(),
    RunNodeStatus.FAILED: frozenset(),
    RunNodeStatus.REVIEW: frozenset(),
    RunNodeStatus.CANCELLED: frozenset(),
    RunNodeStatus.TIMEOUT: frozenset(),
    RunNodeStatus.SKIPPED: frozenset(),
}


@dataclass
class _TargetSpec:
    case_id: int | None = None
    case_version_id: int | None = None
    scenario_id: int | None = None
    scenario_version_id: int | None = None
    web_case_id: int | None = None
    web_case_version_id: int | None = None
    required_capabilities: set[RunnerCapabilityName] | None = None
    steps: list[tuple[str, str, str]] | None = None
    iterations: list[dict[str, Any]] | None = None


def _safe_error(value: str | None, *, maximum: int = 1000) -> str | None:
    if value is None:
        return None
    sanitized = _SENSITIVE_TEXT.sub("<redacted>", value)
    return sanitized[:maximum]


def _issue(
    issues: list[RunValidationIssue], code: str, message: str, field: str | None = None
) -> None:
    issues.append(RunValidationIssue(code=code, message=message, field=field))


def _is_runtime_template(value: str | None) -> bool:
    return value is not None and bool(_RUNTIME_TEMPLATE.fullmatch(value.strip()))


def _unsupported_config_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in _UNSUPPORTED_CONFIG_KEYS:
                return normalized
            found = _unsupported_config_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _unsupported_config_key(child)
            if found:
                return found
    return None


def _validate_database_connection(
    session: Session,
    project_id: int,
    environment_id: int,
    connection_id: int,
    issues: list[RunValidationIssue],
) -> None:
    connection = session.get(DatabaseConnection, connection_id)
    if (
        connection is None
        or connection.project_id != project_id
        or connection.environment_id != environment_id
        or not connection.enabled
    ):
        _issue(
            issues,
            "DATABASE_CONNECTION_INVALID",
            "Database Connection 不存在、不属于当前项目/环境或已停用",
            "connection_id",
        )
        return
    secret = session.get(Secret, connection.password_secret_id)
    if (
        secret is None
        or secret.project_id != project_id
        or secret.environment_id not in {None, environment_id}
        or not secret.enabled
    ):
        _issue(
            issues,
            "SECRET_INVALID",
            "Database Connection 引用的 Secret 不存在、不属于当前项目/环境或已停用",
            "password_secret_id",
        )


def _validate_dataset(
    session: Session,
    project_id: int,
    dataset_id: int,
    dataset_version_id: int | None,
    issues: list[RunValidationIssue],
) -> None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id or dataset.status != "ACTIVE":
        _issue(issues, "DATA_SOURCE_INVALID", "Data Source 不存在、不属于当前项目或已归档")
        return
    version_id = dataset_version_id or dataset.current_version_id
    version = session.get(DatasetVersion, version_id) if version_id else None
    if (
        version is None
        or version.dataset_id != dataset.id
        or not isinstance(version.actual_data, list)
    ):
        _issue(issues, "DATA_SOURCE_INVALID", "Data Source 版本不存在、归属不匹配或不可读取")


def _safe_iteration_snapshot_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if _is_sensitive_request_name(str(key))
                else _safe_iteration_snapshot_value(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_safe_iteration_snapshot_value(child) for child in value]
    if isinstance(value, str):
        return _SENSITIVE_TEXT.sub("<redacted>", value)[:10_000]
    return value


def _dataset_iterations(
    session: Session,
    project_id: int,
    data_source: Any,
) -> list[dict[str, Any]]:
    dataset = session.get(Dataset, data_source.dataset_id)
    version_id = data_source.dataset_version_id or (
        dataset.current_version_id if dataset is not None else None
    )
    version = session.get(DatasetVersion, version_id) if version_id else None
    if (
        dataset is None
        or dataset.project_id != project_id
        or dataset.status != "ACTIVE"
        or version is None
        or version.dataset_id != dataset.id
        or not isinstance(version.actual_data, list)
        or not version.actual_data
    ):
        raise ResourceConflictError("Data Source 版本不可用")
    columns = [str(item["name"]) for item in version.columns]
    mapping = validate_iteration_mapping(columns, data_source.column_mapping, data_source.prefix)
    iterations: list[dict[str, Any]] = []
    for position, row in enumerate(version.actual_data, start=1):
        if (
            not isinstance(row, dict)
            or row.get("row_index") != position
            or not isinstance(row.get("data"), dict)
        ):
            raise ResourceConflictError("Data Source 行快照无效")
        values = row["data"]
        if set(values) != set(columns):
            raise ResourceConflictError("Data Source 行列集与固定版本不一致")
        context = {mapping[column]: values[column] for column in columns}
        snapshot = {
            mapping[column]: (
                "<redacted>"
                if _is_sensitive_request_name(column) or _is_sensitive_request_name(mapping[column])
                else _safe_iteration_snapshot_value(values[column])
            )
            for column in columns
        }
        iterations.append(
            {
                "dataset_id": dataset.id,
                "dataset_version_id": version.id,
                "row_index": position,
                "context": context,
                "parameter_snapshot": snapshot,
            }
        )
    return iterations


def _validate_case_target(
    session: Session,
    payload: RunCreateRequest,
    issues: list[RunValidationIssue],
    target: _TargetSpec,
) -> None:
    case = session.get(TestCase, payload.case_id) if payload.case_id else None
    if case is None or case.project_id != payload.project_id or case.status != "ACTIVE":
        _issue(issues, "CASE_INVALID", "API Case 不存在、不属于当前项目或已归档", "case_id")
        return
    version_id = payload.case_version_id or case.current_version_id
    version = session.get(TestCaseVersion, version_id) if version_id else None
    if version is None or version.case_id != case.id:
        _issue(
            issues,
            "CASE_VERSION_INVALID",
            "API Case Version 不存在或不属于当前 Case",
            "case_version_id",
        )
        return
    target.case_id = case.id
    target.case_version_id = version.id
    target.required_capabilities = {RunnerCapabilityName.API}
    try:
        content = SuggestedCase.model_validate(version.content)
    except ValidationError:
        _issue(issues, "CASE_VERSION_INVALID", "API Case Version 内容无法按当前协议解析")
        return
    if (
        content.case_type == CaseType.API
        and content.request is not None
        and not is_v1_api_step_retry_limit(content.request.retry_policy.max_retries)
    ):
        _issue(
            issues,
            API_STEP_RETRY_LIMIT_ERROR_CODE,
            API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
            "request.retry_policy.max_retries",
        )
    iteration_context: dict[str, Any] | None = None
    if content.data_source is not None:
        _validate_dataset(
            session,
            payload.project_id,
            content.data_source.dataset_id,
            content.data_source.dataset_version_id,
            issues,
        )
        try:
            target.iterations = _dataset_iterations(
                session, payload.project_id, content.data_source
            )
            iteration_context = dict(target.iterations[0]["context"])
        except (ResourceConflictError, TypeError, ValueError):
            _issue(
                issues,
                "DATA_SOURCE_INVALID",
                "Data Source 行快照或列映射无法固定为执行计划",
                "data_source",
            )
    issues.extend(
        _api_execution_support_issues(
            content,
            raw_content=version.content,
            runtime_names={
                *payload.runtime_variables,
                *(iteration_context or {}),
            },
        )
    )
    try:
        from app.modules.test_cases.service import _validate_assertions

        _validate_assertions(session, payload.project_id, content)
    except (ResourceConflictError, ValueError, TypeError):
        _issue(
            issues,
            "AI_ASSERTION_INVALID",
            "API Case 的 AI 断言 Prompt 或 Output Schema 无法解析",
            "assertions",
        )
    issues.extend(
        _api_secret_reference_issues(
            session,
            payload.project_id,
            payload.environment_id,
            content.request,
        )
        if content.request is not None
        else []
    )
    for action in content.pre_actions:
        if isinstance(action, GetTokenAction | ApiSetupAction) and action.request is not None:
            issues.extend(
                _api_secret_reference_issues(
                    session,
                    payload.project_id,
                    payload.environment_id,
                    action.request,
                )
            )
    issues.extend(
        _api_runtime_reference_issues(
            session,
            payload.project_id,
            payload.environment_id,
            content.request,
            content.pre_actions,
            content.extractors,
            content.post_actions,
            {**payload.runtime_variables, **(iteration_context or {})},
        )
        if content.request is not None
        else []
    )
    try:
        from app.modules.test_cases.service import _validate_cleanup_configs

        _validate_cleanup_configs(session, payload.project_id, content)
    except (ResourceConflictError, ValueError, TypeError):
        _issue(issues, "CLEANUP_REFERENCE_INVALID", "API Case Cleanup 引用无法在当前项目中解析")
    for action in [*content.pre_actions, *content.post_actions]:
        if isinstance(action, SqlQueryAction):
            target.required_capabilities.add(RunnerCapabilityName.SQL)
            _validate_database_connection(
                session,
                payload.project_id,
                payload.environment_id,
                action.connection_id,
                issues,
            )
        if getattr(action, "type", None) == ActionType.PYTHON_SCRIPT:
            target.required_capabilities.add(RunnerCapabilityName.SCRIPT)
    target.steps = [
        (f"step_{index}", step.action, "CASE_STEP")
        for index, step in enumerate(content.steps, start=1)
    ]


def _scenario_http_template(config: dict[str, Any]) -> ApiRequestTemplate:
    allowed = {
        "method",
        "url",
        "query_params",
        "headers",
        "cookies",
        "body",
        "auth",
        "timeout_ms",
        "follow_redirects",
        "retry_policy",
    }
    if set(config) - allowed:
        raise ValueError("Scenario HTTP 包含未知字段")
    normalized = dict(config)
    body = normalized.get("body")
    if isinstance(body, dict) and set(body) == {"json"}:
        normalized["body"] = {"type": "JSON", "content": body["json"]}
    normalized.setdefault("method", "GET")
    normalized.setdefault("query_params", [])
    normalized.setdefault("headers", [])
    normalized.setdefault("cookies", [])
    normalized.setdefault("body", {"type": "NONE"})
    normalized.setdefault("auth", {"type": "NONE"})
    if normalized["body"] is None:
        normalized["body"] = {"type": "NONE"}
    if normalized["auth"] is None:
        normalized["auth"] = {"type": "NONE"}
    normalized.setdefault("follow_redirects", True)
    normalized.setdefault("retry_policy", {"max_retries": 0})
    body = normalized["body"]
    deferred_json_content: Any | None = None
    if (
        isinstance(body, dict)
        and str(body.get("type", "")).upper() == "JSON"
        and _api_runtime_name(body.get("content")) is not None
    ):
        deferred_json_content = body["content"]
        normalized["body"] = {**body, "content": {}}
    template = ApiRequestTemplate.model_validate(normalized)
    if deferred_json_content is not None:
        template = template.model_copy(
            update={"body": template.body.model_copy(update={"content": deferred_json_content})}
        )
    return template


def _scenario_runtime_names(dsl: ScenarioDsl) -> set[str]:
    names = set(dsl.settings.initial_variables) | set(_SCENARIO_SYSTEM_CONTEXT_KEYS)
    for node in dsl.nodes:
        for key in ("name", "result_variable"):
            declared_name = node.config.get(key)
            if isinstance(declared_name, str):
                names.add(declared_name)
    return names


def _scenario_execution_support_issues(
    session: Session,
    project_id: int,
    environment_id: int,
    dsl: ScenarioDsl,
    issues: list[RunValidationIssue],
    *,
    target: _TargetSpec | None = None,
    runtime_variables: dict[str, Any] | None = None,
) -> None:
    """Single fail-closed capability gate shared by validate, create and plan."""

    validation = validate_scenario_dsl(dsl)
    credential_issue_nodes = {
        item.node_id
        for item in validation.issues
        if item.code == "SCENARIO_HTTP_CREDENTIAL_INVALID"
    }
    for item in validation.issues:
        _issue(issues, item.code, item.message, item.node_id)

    _scenario_initial_context_issues(
        session,
        project_id,
        environment_id,
        dsl,
        issues,
        runtime_variables=runtime_variables,
    )

    unsupported_key = _unsupported_config_key(dsl.model_dump(mode="json"))
    if unsupported_key:
        code = (
            "SCENARIO_WEB_UNSUPPORTED"
            if unsupported_key in {"browser", "browser_name", "browser_version"}
            else "SCENARIO_AI_UNSUPPORTED"
            if unsupported_key in {"model_id", "model_name", "ai_model", "ai_model_id"}
            else "SCENARIO_REQUIRED_FILE_UNSUPPORTED"
        )
        _issue(
            issues,
            code,
            "当前 Scenario Runner 不支持该 Web/Browser、AI 或 required file "
            "能力，预计 Phase 5 支持",
            "dsl",
        )

    runtime_names = _scenario_runtime_names(dsl)

    for node in dsl.nodes:
        config = node.config
        try:
            if node.type != ScenarioNodeType.HTTP:
                validate_secret_free_payload(config, allow_auth_reference=True)
            config_size = len(
                json.dumps(config, ensure_ascii=False, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            )
            if config_size > 64_000:
                raise ValueError("节点配置超过 64KB")
        except (TypeError, ValueError):
            _issue(
                issues,
                "SCENARIO_SENSITIVE_DATA_UNSUPPORTED",
                "Scenario 节点配置包含不允许下发的敏感数据或超过大小限制",
                node.id,
            )

        if node.type in {ScenarioNodeType.HTTP, ScenarioNodeType.API_CLEANUP}:
            if target is not None:
                target.required_capabilities.add(RunnerCapabilityName.API)
        elif node.type in {
            ScenarioNodeType.SQL_QUERY,
            ScenarioNodeType.SQL_EXECUTE,
            ScenarioNodeType.SQL_CLEANUP,
        }:
            if target is not None:
                target.required_capabilities.add(RunnerCapabilityName.SQL)
        elif node.type == ScenarioNodeType.PYTHON_SCRIPT and target is not None:
            target.required_capabilities.add(RunnerCapabilityName.SCRIPT)

        if node.type == ScenarioNodeType.HTTP:
            try:
                request = _scenario_http_template(config)
            except (ValidationError, TypeError, ValueError):
                _issue(
                    issues,
                    "SCENARIO_HTTP_CONFIG_INVALID",
                    "HTTP 节点请求配置无效",
                    node.id,
                )
            else:
                issues.extend(
                    _api_secret_reference_issues(session, project_id, environment_id, request)
                )
                if node.id not in credential_issue_nodes and not _api_request_credentials_valid(
                    request, runtime_names
                ):
                    _issue(
                        issues,
                        "SCENARIO_HTTP_CREDENTIAL_INVALID",
                        "Scenario HTTP 敏感字段必须使用 Secret 或受控 Runtime 引用",
                        node.id,
                    )

        if node.type in {
            ScenarioNodeType.SQL_QUERY,
            ScenarioNodeType.SQL_EXECUTE,
            ScenarioNodeType.SQL_CLEANUP,
        }:
            connection_id = node.config.get("connection_id")
            if isinstance(connection_id, int):
                _validate_database_connection(
                    session, project_id, environment_id, connection_id, issues
                )
            else:
                _issue(
                    issues,
                    "DATABASE_CONNECTION_INVALID",
                    "SQL 节点缺少有效的 MySQL Connection",
                    node.id,
                )
            statement = node.config.get("sql")
            validator = {
                ScenarioNodeType.SQL_QUERY: validate_sql_query,
                ScenarioNodeType.SQL_EXECUTE: validate_sql_execute,
                ScenarioNodeType.SQL_CLEANUP: validate_sql_cleanup,
            }[node.type]
            try:
                validator(statement)
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "SCENARIO_SQL_UNSUPPORTED",
                    "SQL 节点语句不符合当前安全执行范围",
                    node.id,
                )
            params = node.config.get("params", {})
            if not isinstance(params, (dict, list)):
                _issue(issues, "SCENARIO_SQL_PARAMS_INVALID", "SQL 参数必须是对象或数组", node.id)
            else:
                try:
                    validate_secret_free_payload(params)
                    encoded_params = json.dumps(
                        params,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                    if len(encoded_params) > 64_000:
                        raise ValueError("SQL 参数超过 64KB")
                except (TypeError, ValueError):
                    _issue(
                        issues,
                        "SCENARIO_SQL_PARAMS_INVALID",
                        "SQL 参数包含敏感数据或超过大小限制",
                        node.id,
                    )

        if node.type == ScenarioNodeType.PYTHON_SCRIPT:
            script = node.config.get("script")
            try:
                if not isinstance(script, str):
                    raise ResourceConflictError("缺少脚本")
                validate_safe_script(script)
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "SCENARIO_SCRIPT_INVALID",
                    "Python Script 不符合受限脚本规则",
                    node.id,
                )

        if node.type == ScenarioNodeType.AI_ASSERTION:
            try:
                assertion = Assertion(
                    kind=AssertionKind.AI_SEMANTIC,
                    type="AI_SEMANTIC",
                    name=node.name,
                    prompt_id=node.config.get("prompt_id"),
                    criteria=node.config.get("criteria"),
                    confidence_threshold=node.config.get("confidence_threshold", 0.8),
                )
                validate_ai_assertion_definition(session, project_id, assertion)
            except (ValidationError, ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "SCENARIO_AI_ASSERTION_INVALID",
                    "Scenario AI 断言的 Prompt、Schema 或配置无效",
                    node.id,
                )

        if node.type in {ScenarioNodeType.API_CLEANUP, ScenarioNodeType.SQL_CLEANUP}:
            try:
                validate_cleanup_scope(
                    session,
                    project_id,
                    node.cleanup_config(dsl.settings.cleanup_policy),
                )
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "CLEANUP_REFERENCE_INVALID",
                    "Scenario Cleanup 引用无法在当前项目/环境中解析",
                    node.id,
                )


_SCENARIO_SYSTEM_CONTEXT_KEYS = frozenset({"run_id", "scenario_id", "project_id", "environment_id"})


def _scenario_initial_context_issues(
    session: Session,
    project_id: int,
    environment_id: int,
    dsl: ScenarioDsl,
    issues: list[RunValidationIssue],
    *,
    runtime_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    environment = session.get(Environment, environment_id)
    if environment is None or environment.project_id != project_id or not environment.enabled:
        return {}
    try:
        values = build_environment_context(session, environment_id)
    except (TypeError, ValueError):
        _issue(
            issues,
            "SCENARIO_INITIAL_CONTEXT_INVALID",
            "Environment 中存在无法安全解析的 Runtime Context 变量",
            "settings.initial_variables",
        )
        return {}
    if environment.base_url:
        values["base_url"] = str(environment.base_url)
    values.update(runtime_variables or {})

    for variable_name in dsl.settings.initial_variables:
        if _is_sensitive_request_name(variable_name):
            _issue(
                issues,
                "SCENARIO_INITIAL_CONTEXT_SENSITIVE",
                "Runtime Context 敏感变量必须通过 Secret 机制引用",
                "settings.initial_variables",
            )
            continue
        if variable_name in _SCENARIO_SYSTEM_CONTEXT_KEYS:
            continue
        if variable_name not in values:
            _issue(
                issues,
                "SCENARIO_INITIAL_VARIABLE_MISSING",
                "Scenario 声明的 Runtime Context 变量在当前 Environment 中不存在",
                "settings.initial_variables",
            )
            continue
        try:
            validate_secret_free_payload({variable_name: values[variable_name]})
        except (TypeError, ValueError):
            _issue(
                issues,
                "SCENARIO_INITIAL_CONTEXT_SENSITIVE",
                "Runtime Context 敏感变量必须通过 Secret 机制引用",
                "settings.initial_variables",
            )
    return values


def _validate_scenario_target(
    session: Session,
    payload: RunCreateRequest,
    issues: list[RunValidationIssue],
    target: _TargetSpec,
) -> None:
    scenario = session.get(Scenario, payload.scenario_id) if payload.scenario_id else None
    if (
        scenario is None
        or scenario.project_id != payload.project_id
        or scenario.status == "ARCHIVED"
    ):
        _issue(issues, "SCENARIO_INVALID", "Scenario 不存在、不属于当前项目或已归档", "scenario_id")
        return
    version_id = payload.scenario_version_id or scenario.current_version_id
    version = session.get(ScenarioVersion, version_id) if version_id else None
    if version is None or version.scenario_id != scenario.id:
        _issue(
            issues,
            "SCENARIO_VERSION_INVALID",
            "Scenario Version 不存在或不属于当前 Scenario",
            "scenario_version_id",
        )
        return
    target.scenario_id = scenario.id
    target.scenario_version_id = version.id
    target.required_capabilities = set()
    try:
        dsl = ScenarioDsl.model_validate(version.dsl)
    except ValidationError:
        _issue(issues, "SCENARIO_DSL_INVALID", "Scenario DSL 无法按当前协议解析")
        return
    _scenario_execution_support_issues(
        session,
        payload.project_id,
        payload.environment_id,
        dsl,
        issues,
        target=target,
        runtime_variables=payload.runtime_variables,
    )
    target.steps = [(node.id, node.name, node.type.value) for node in dsl.nodes]


def _validate_web_locator_references(
    session: Session, project_id: int, content: WebCaseContent, issues: list[RunValidationIssue]
) -> None:
    locators = [getattr(item, "locator", None) for item in [*content.actions, *content.assertions]]
    for locator in locators:
        if locator is None or locator.element_version_id is None:
            continue
        version = session.get(WebElementVersion, locator.element_version_id)
        element = session.get(WebElement, version.element_id) if version else None
        page = session.get(WebPage, element.page_id) if element else None
        if (
            version is None
            or element is None
            or page is None
            or element.project_id != project_id
            or page.project_id != project_id
            or element.status != "ACTIVE"
            or page.status != "ACTIVE"
            or not version.locators
        ):
            _issue(
                issues,
                "WEB_ELEMENT_VERSION_INVALID",
                "Web Case 引用的 Element Version 不存在、不属于当前项目或没有 Locator",
                "content.locator",
            )


def _validate_web_target(
    session: Session,
    payload: RunCreateRequest,
    issues: list[RunValidationIssue],
    target: _TargetSpec,
) -> None:
    web_case = session.get(WebCase, payload.web_case_id) if payload.web_case_id else None
    if web_case is None or web_case.project_id != payload.project_id:
        _issue(issues, "WEB_CASE_INVALID", "Web Case 不存在或不属于当前项目", "web_case_id")
        return
    if web_case.status == "ARCHIVED":
        _issue(issues, "WEB_CASE_ARCHIVED", "归档 Web Case 不能创建新的 Run", "web_case_id")
    version_id = payload.web_case_version_id or web_case.current_version_id
    version = session.get(WebCaseVersion, version_id) if version_id else None
    if version is None or version.web_case_id != web_case.id:
        _issue(
            issues,
            "WEB_CASE_VERSION_INVALID",
            "Web Case Version 不存在或不属于当前 Web Case",
            "web_case_version_id",
        )
        return
    if version.status != "APPROVED":
        _issue(
            issues,
            "WEB_CASE_VERSION_NOT_APPROVED",
            "Web Case Version 必须先批准后才能执行",
            "web_case_version_id",
        )
    try:
        content = WebCaseContent.model_validate(version.content)
    except ValidationError:
        _issue(
            issues,
            "WEB_CASE_DSL_INVALID",
            "Web Case Version 内容无法按当前协议解析",
            "web_case_version_id",
        )
        return
    referenced_context = {
        name
        for name in set(
            re.findall(
                r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}", json.dumps(content.model_dump(mode="json"))
            )
        )
        if not name.startswith("secret.")
    }
    environment = session.get(Environment, payload.environment_id)
    environment_context: dict[str, Any] = {}
    if (
        referenced_context
        and environment is not None
        and environment.project_id == payload.project_id
    ):
        try:
            environment_context = build_environment_context(session, environment.id)
            validate_secret_free_payload(environment_context)
        except (ResourceConflictError, TypeError, ValueError):
            _issue(
                issues,
                "WEB_CONTEXT_INVALID",
                "Web Case 引用的 Environment Runtime Context 不满足安全边界",
                "environment_id",
            )
    available_context = set(environment_context)
    available_context.update(payload.runtime_variables)
    if environment is not None and environment.base_url:
        available_context.add("base_url")
    available_context.update({"run_id", "web_case_id", "project_id", "environment_id"})
    for name in sorted(referenced_context):
        if _is_sensitive_request_name(name):
            _issue(
                issues,
                "WEB_CONTEXT_SENSITIVE_UNSUPPORTED",
                "Web Case 不能直接引用敏感 Runtime Context",
                "content",
            )
        elif name not in available_context:
            _issue(
                issues,
                "WEB_CONTEXT_UNDEFINED",
                "Web Case 引用的 Runtime Context 在当前 Environment 中不存在",
                "content",
            )
    _validate_web_locator_references(session, payload.project_id, content, issues)
    for index, assertion in enumerate(content.assertions, start=1):
        if assertion.type != "ASSERT_AI_SEMANTIC":
            continue
        try:
            validate_ai_assertion_definition(
                session,
                payload.project_id,
                _web_ai_assertion(assertion, f"assertion_{index}"),
            )
        except (ResourceConflictError, RunExecutionUnsupportedError, TypeError, ValueError):
            _issue(
                issues,
                "WEB_AI_ASSERTION_INVALID",
                "Web AI 断言的 Prompt、Schema 或配置无效",
                f"assertion_{index}",
            )
    try:
        _web_secret_values_for_contents(
            session,
            payload.project_id,
            payload.environment_id,
            (content,),
            resolve=False,
        )
    except RunExecutionUnsupportedError as exc:
        _issue(
            issues,
            "WEB_SECRET_INVALID",
            exc.message,
            "content",
        )
    if content.session_profile_id is not None:
        profile = session.get(SessionProfile, content.session_profile_id)
        if (
            profile is None
            or profile.project_id != payload.project_id
            or profile.environment_id not in {None, payload.environment_id}
            or profile.status != "ACTIVE"
        ):
            _issue(
                issues,
                "SESSION_PROFILE_INVALID",
                "Session Profile 不存在、归属不匹配或已归档",
                "session_profile_id",
            )
        else:
            try:
                recovery = _web_session_recovery_binding(
                    session, payload.project_id, payload.environment_id, profile
                )
            except RunExecutionUnsupportedError:
                _issue(
                    issues,
                    "SESSION_RECOVERY_INVALID",
                    "Session Profile 登录恢复配置不可用",
                    "session_profile_id",
                )
                recovery = None
            if (
                profile.expires_at is not None
                and profile.expires_at <= utc_now_naive()
                and recovery is None
            ):
                _issue(
                    issues,
                    "SESSION_PROFILE_EXPIRED",
                    "Session Profile 已过期且未配置可用登录恢复流程",
                    "session_profile_id",
                )
    target.web_case_id = web_case.id
    target.web_case_version_id = version.id
    target.required_capabilities = {RunnerCapabilityName.WEB}
    target.steps = [
        *[
            (f"action_{index}", item.type, "WEB_ACTION")
            for index, item in enumerate(content.actions, start=1)
        ],
        *[
            (f"assertion_{index}", item.type, "WEB_ASSERTION")
            for index, item in enumerate(content.assertions, start=1)
        ],
    ]


def _validate_runner(
    session: Session,
    payload: RunCreateRequest,
    required_capabilities: set[RunnerCapabilityName],
    issues: list[RunValidationIssue],
    store: RedisRunnerHeartbeatStore,
) -> None:
    runner = session.get(Runner, payload.runner_id)
    if runner is None:
        _issue(issues, "RUNNER_NOT_FOUND", "Runner 不存在或不属于可用控制面", "runner_id")
        return
    if runner.status != RunnerStatus.ACTIVE.value:
        _issue(issues, "RUNNER_REVOKED", "Runner credential 已撤销或 Runner 未启用", "runner_id")
    if (
        not runner.credential_digest
        or len(runner.credential_digest) != 64
        or any(
            character not in "0123456789abcdef" for character in runner.credential_digest.lower()
        )
    ):
        _issue(
            issues,
            "RUNNER_CREDENTIAL_INVALID",
            "Runner credential 摘要不可用",
            "runner_id",
        )
    heartbeat_available = True
    try:
        heartbeat = store.get_heartbeat(runner.id)
    except RedisStoreUnavailableError:
        heartbeat_available = False
        _issue(
            issues,
            "RUNNER_STATE_UNAVAILABLE",
            "Runner 在线状态暂时无法验证，请稍后重试",
            "runner_id",
        )
        heartbeat = None
    if heartbeat_available and heartbeat is None:
        _issue(issues, "RUNNER_OFFLINE", "Runner 当前不在线，不能创建 Run", "runner_id")
    runner_tags = {item.tag for item in runner.tags}
    missing_tags = sorted(set(payload.required_tags) - runner_tags)
    if missing_tags:
        _issue(issues, "RUNNER_TAG_MISSING", "Runner 缺少要求的 Tag", "required_tags")
    ready_capabilities = {item.capability for item in runner.capabilities if item.status == "READY"}
    missing_capabilities = sorted(
        capability.value
        for capability in required_capabilities
        if capability.value not in ready_capabilities
    )
    if missing_capabilities:
        _issue(
            issues,
            "RUNNER_CAPABILITY_MISSING",
            "Runner 缺少可用 Capability",
            "required_capabilities",
        )
    slot = next(
        (item for item in runner.slots if item.slot_type == payload.required_slot_type.value),
        None,
    )
    if slot is None or slot.available < payload.required_slot_count:
        _issue(
            issues,
            "RUNNER_SLOT_UNAVAILABLE",
            "Runner 没有满足要求的可用 Slot",
            "required_slot_count",
        )
    if payload.run_type == RunType.WEB_CASE and not runner.chrome_version:
        _issue(
            issues,
            "RUNNER_CHROME_UNAVAILABLE",
            "Runner 未报告可用 Chrome 版本",
            "runner_id",
        )


def validate_run_request(
    session: Session,
    user: CurrentUser,
    payload: RunCreateRequest,
    store: RedisRunnerHeartbeatStore,
) -> tuple[RunValidationResponse, _TargetSpec]:
    project = get_project(session, user, payload.project_id)
    issues: list[RunValidationIssue] = []
    if project.status == ProjectStatus.ARCHIVED.value:
        _issue(issues, "PROJECT_ARCHIVED", "归档项目不能创建 Run", "project_id")
    environment = session.get(Environment, payload.environment_id)
    if (
        environment is None
        or environment.project_id != payload.project_id
        or not environment.enabled
    ):
        _issue(
            issues,
            "ENVIRONMENT_INVALID",
            "Environment 不存在、不属于当前项目或已停用",
            "environment_id",
        )

    target = _TargetSpec(required_capabilities=set(), steps=[])
    if payload.run_type == RunType.API_CASE:
        _validate_case_target(session, payload, issues, target)
    elif payload.run_type == RunType.SCENARIO:
        _validate_scenario_target(session, payload, issues, target)
    else:
        _validate_web_target(session, payload, issues, target)
        if payload.required_slot_type != RunnerSlotType.WEB:
            _issue(
                issues,
                "WEB_SLOT_REQUIRED",
                "WEB_CASE 必须使用 WEB Slot",
                "required_slot_type",
            )
    required_capabilities = set(target.required_capabilities or set())
    required_capabilities.update(payload.required_capabilities)
    _validate_runner(session, payload, required_capabilities, issues, store)
    return (
        RunValidationResponse(
            valid=not issues,
            issues=issues,
            run_type=payload.run_type,
            resolved_case_version_id=target.case_version_id,
            resolved_scenario_version_id=target.scenario_version_id,
            resolved_web_case_version_id=target.web_case_version_id,
            required_capabilities=sorted(required_capabilities, key=lambda item: item.value),
        ),
        target,
    )


def _effective_total_timeout_ms(run: TestRun) -> int:
    """Run 覆盖优先，否则使用系统默认 Run 总超时。"""

    return run.total_timeout_ms or get_settings().runner_total_timeout_default_ms


def _persisted_total_timeout_ms(run: TestRun) -> int:
    """Return the immutable budget captured when the task was first claimed."""

    if run.total_timeout_ms is None:
        raise RunStateConflictError("Run 缺少已持久化的总超时预算，不能生成执行计划")
    return run.total_timeout_ms


def _run_response(run: TestRun) -> RunResponse:
    return RunResponse.model_validate(
        {
            "id": run.id,
            "run_code": run.run_code,
            "run_type": run.run_type,
            "project_id": run.project_id,
            "environment_id": run.environment_id,
            "runner_id": run.runner_id,
            "case_id": run.case_id,
            "case_version_id": run.case_version_id,
            "scenario_id": run.scenario_id,
            "scenario_version_id": run.scenario_version_id,
            "web_case_id": run.web_case_id,
            "web_case_version_id": run.web_case_version_id,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "required_capabilities": run.required_capabilities,
            "required_tags": run.required_tags,
            "required_slot_type": run.required_slot_type,
            "required_slot_count": run.required_slot_count,
            "runtime_snapshot_id": run.runtime_snapshot_id,
            "runtime_variables": run.runtime_variables or {},
            "total_timeout_ms": run.total_timeout_ms,
            "effective_total_timeout_ms": _effective_total_timeout_ms(run),
            "force_stop_requested_at": run.force_stop_requested_at,
            "force_stopped": bool(run.force_stopped),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "total": run.total,
            "pass": run.pass_count,
            "fail": run.fail_count,
            "review": run.review_count,
            "timeout": run.timeout_count,
            "error_type": run.error_type,
            "error_message": run.error_message,
            "created_by": run.created_by,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }
    )


def _web_traces_for_run(session: Session, run: TestRun) -> list[WebExecutionTrace]:
    if run.run_type != RunType.WEB_CASE.value:
        return []
    traces: list[WebExecutionTrace] = []
    for result in list_web_execution_results(session, run.id):
        for item in result.traces:
            try:
                traces.append(WebExecutionTrace.model_validate(item))
            except ValidationError:
                # Never expose malformed or legacy unsafe audit data in a detail response.
                continue
    return traces


def _web_session_recoveries_for_run(
    session: Session, run: TestRun
) -> list[WebSessionRecoveryAudit]:
    if run.run_type != RunType.WEB_CASE.value:
        return []
    recoveries: list[WebSessionRecoveryAudit] = []
    for result in list_web_execution_results(session, run.id):
        if result.session_recovery is None:
            continue
        try:
            recoveries.append(WebSessionRecoveryAudit.model_validate(result.session_recovery))
        except ValidationError:
            continue
    return recoveries


def _run_detail(session: Session, run: TestRun) -> RunDetailResponse:
    return RunDetailResponse(
        **_run_response(run).model_dump(),
        case_runs=[
            CaseRunDetailResponse(
                **CaseRunResponse.model_validate(case_run).model_dump(),
                step_runs=[StepRunResponse.model_validate(step) for step in case_run.step_runs],
            )
            for case_run in run.case_runs
        ],
        web_traces=_web_traces_for_run(session, run),
        web_session_recoveries=_web_session_recoveries_for_run(session, run),
        api_action_traces=[
            ExecutionActionAuditResponse(
                case_run_id=result.case_run_id,
                **trace,
            )
            for result in session.scalars(
                select(RunApiExecutionResult)
                .where(RunApiExecutionResult.run_id == run.id)
                .order_by(RunApiExecutionResult.case_run_id.asc())
            ).all()
            for trace in result.action_traces
        ],
    )


def list_project_runs(
    session: Session, user: CurrentUser, project_id: int, *, page: int, page_size: int
) -> RunListResponse:
    get_project(session, user, project_id)
    items, total = list_runs(session, project_id, offset=(page - 1) * page_size, limit=page_size)
    return RunListResponse(
        items=[_run_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_run_detail(session: Session, user: CurrentUser, run_id: str) -> RunDetailResponse:
    run = get_run_with_children(session, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    return _run_detail(session, run)


def _decode_event_value(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _parse_run_event(
    stream_id: str, fields: dict[str, Any], *, project_id: int, run_id: str
) -> RunEventResponse | None:
    def field(name: str, default: str = "") -> str:
        return _decode_event_value(fields.get(name, default))

    def optional_int(name: str) -> int | None:
        value = field(name)
        return int(value) if value else None

    try:
        parsed = RunEventResponse.model_validate(
            {
                "id": stream_id,
                "schema_version": int(field("schema_version")),
                "event_type": field("event_type"),
                "project_id": int(field("project_id")),
                "run_id": field("run_id"),
                "case_run_id": optional_int("case_run_id"),
                "step_run_id": optional_int("step_run_id"),
                "from_status": field("from_status") or None,
                "to_status": field("to_status"),
                "occurred_at": field("occurred_at"),
                "total": optional_int("total"),
                "pass_count": optional_int("pass_count"),
                "fail_count": optional_int("fail_count"),
                "review_count": optional_int("review_count"),
                "timeout_count": optional_int("timeout_count"),
            }
        )
    except (TypeError, ValueError, ValidationError):
        return None
    if parsed.project_id != project_id or parsed.run_id != run_id:
        return None
    return parsed


def list_run_events(
    session: Session,
    user: CurrentUser,
    run_id: str,
    stream: RedisRunEventStream,
    *,
    after_id: str,
    limit: int,
) -> RunEventListResponse:
    run = get_run(session, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    try:
        entries = stream.read_events(
            run.project_id,
            run.id,
            after_id=after_id,
            limit=limit + 1,
        )
    except RedisStoreUnavailableError as exc:
        raise RedisUnavailableError("Run 事件流暂不可用，请稍后重试") from exc
    has_more = len(entries) > limit
    items = [
        parsed
        for stream_id, fields in entries[:limit]
        if (parsed := _parse_run_event(stream_id, fields, project_id=run.project_id, run_id=run.id))
        is not None
    ]
    return RunEventListResponse(
        items=items,
        next_after_id=items[-1].id if items else None,
        has_more=has_more,
    )


def authorize_run_event_stream(session: Session, user: CurrentUser, run_id: str) -> tuple[int, str]:
    run = get_run(session, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    return run.project_id, run.id


def run_event_stream_generator(
    stream: RedisRunEventStream,
    project_id: int,
    run_id: str,
    *,
    after_id: str,
    limit: int,
    block_ms: int = 15_000,
) -> Iterator[str]:
    """Yield SSE frames while keeping database state outside the generator."""

    cursor = after_id
    while True:
        try:
            entries = stream.read_events_blocking(
                project_id,
                run_id,
                after_id=cursor,
                limit=limit,
                block_ms=block_ms,
            )
        except RedisStoreUnavailableError:
            yield (
                "event: stream_error\n"
                'data: {"code":"REDIS_UNAVAILABLE",'
                '"message":"Run 事件流暂不可用，请稍后重试"}\n\n'
            )
            return
        if not entries:
            yield ": heartbeat\n\n"
            continue
        for stream_id, fields in entries:
            cursor = stream_id
            event = _parse_run_event(stream_id, fields, project_id=project_id, run_id=run_id)
            if event is None:
                continue
            data = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.id}\nevent: run_status\ndata: {data}\n\n"


def create_run(
    session: Session,
    user: CurrentUser,
    payload: RunCreateRequest,
    store: RedisRunnerHeartbeatStore,
    event_stream: RedisRunEventStream,
) -> RunDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    validation, target = validate_run_request(session, user, payload, store)
    if not validation.valid:
        raise RunValidationError([item.model_dump() for item in validation.issues])
    run_id = f"run_{uuid4().hex}"
    run = TestRun(
        id=run_id,
        run_code=f"RUN-{uuid4().hex[:12].upper()}",
        run_type=payload.run_type.value,
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        runner_id=payload.runner_id,
        case_id=target.case_id,
        case_version_id=target.case_version_id,
        scenario_id=target.scenario_id,
        scenario_version_id=target.scenario_version_id,
        web_case_id=target.web_case_id,
        web_case_version_id=target.web_case_version_id,
        status=RunStatus.CREATED.value,
        trigger_type=payload.trigger_type.value,
        required_capabilities=[item.value for item in validation.required_capabilities],
        required_tags=list(payload.required_tags),
        required_slot_type=payload.required_slot_type.value,
        required_slot_count=payload.required_slot_count,
        runtime_snapshot_id=run_id,
        runtime_variables=dict(payload.runtime_variables),
        total_timeout_ms=payload.total_timeout_ms,
        total=len(target.iterations or [None]),
        created_by=user.id,
    )
    iteration_specs = target.iterations or [None]
    for case_sequence, iteration in enumerate(iteration_specs, start=1):
        case_run = CaseRun(
            run_id=run_id,
            sequence_no=case_sequence,
            case_id=target.case_id,
            case_version_id=target.case_version_id,
            scenario_id=target.scenario_id,
            scenario_version_id=target.scenario_version_id,
            web_case_id=target.web_case_id,
            web_case_version_id=target.web_case_version_id,
            dataset_id=(iteration["dataset_id"] if iteration is not None else None),
            dataset_version_id=(iteration["dataset_version_id"] if iteration is not None else None),
            row_index=(iteration["row_index"] if iteration is not None else None),
            parameter_snapshot=(iteration["parameter_snapshot"] if iteration is not None else None),
            status=RunNodeStatus.CREATED.value,
        )
        run.case_runs.append(case_run)
        for sequence_no, (node_id, step_name, step_type) in enumerate(target.steps or [], start=1):
            case_run.step_runs.append(
                StepRun(
                    sequence_no=sequence_no,
                    node_id=node_id,
                    step_name=step_name,
                    step_type=step_type,
                    status=RunNodeStatus.CREATED.value,
                )
            )
    session.add(run)
    try:
        # Flush assigns CaseRun.id, then requirement-source capture and all run
        # records commit atomically. No event or dispatch message is emitted until
        # this transaction succeeds.
        session.flush()
        for case_run in run.case_runs:
            capture_case_run_requirement_sources(session, run, case_run)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.exception(
            "run_create_failed",
            extra={"event": "RUN_CREATE_FAILED", "project_id": payload.project_id},
        )
        raise ResourceConflictError("Run 创建失败") from exc
    except Exception:
        session.rollback()
        raise
    RUNS_CREATED.labels(
        run_type=run.run_type,
        trigger_type=run.trigger_type,
    ).inc()
    logger.info(
        "run_created",
        extra={
            "event": "RUN_CREATED",
            "run_id": run.id,
            "project_id": run.project_id,
            "runner_id": run.runner_id,
            "status": run.status,
        },
    )
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=None,
        to_status=RunStatus.CREATED,
        case_run_id=run.case_runs[0].id if run.case_runs else None,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
    )
    created = get_run_with_children(session, run.id)
    if created is None:
        raise ResourceConflictError("Run 创建后读取失败")
    return _run_detail(session, created)


def cancel_run(
    session: Session,
    user: CurrentUser,
    run_id: str,
    event_stream: RedisRunEventStream,
) -> RunDetailResponse:
    """Cancel a Run at a safe checkpoint, sharing the Run row lock with claim."""

    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    _authorize_run_write(session, user, run)
    current_status = RunStatus(run.status)
    if current_status == RunStatus.CANCELLED:
        detail = get_run_with_children(session, run.id)
        if detail is None:
            raise ResourceNotFoundError("Run 不存在")
        return _run_detail(session, detail)
    if current_status == RunStatus.CANCELLING:
        detail = get_run_with_children(session, run.id)
        if detail is None:
            raise ResourceNotFoundError("Run 不存在")
        return _run_detail(session, detail)
    if current_status not in {
        RunStatus.CREATED,
        RunStatus.QUEUED,
        RunStatus.ASSIGNED,
        RunStatus.RUNNING,
    }:
        raise RunStateConflictError("只有 CREATED/QUEUED/ASSIGNED/RUNNING Run 可以请求取消")

    case_runs = list(
        session.scalars(select(CaseRun).where(CaseRun.run_id == run.id).with_for_update()).all()
    )
    case_run_ids = [case_run.id for case_run in case_runs]
    step_runs = (
        list(
            session.scalars(
                select(StepRun).where(StepRun.case_run_id.in_(case_run_ids)).with_for_update()
            ).all()
        )
        if case_run_ids
        else []
    )
    if current_status in {RunStatus.CREATED, RunStatus.QUEUED}:
        node_status = RunNodeStatus.CANCELLED
        run_status = RunStatus.CANCELLED
    else:
        node_status = RunNodeStatus.CANCELLING
        run_status = RunStatus.CANCELLING
    cancel_payload = NodeStatusUpdateRequest(status=node_status)
    for step_run in step_runs:
        if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES and (
            node_status == RunNodeStatus.CANCELLED
            or RunNodeStatus(step_run.status) == RunNodeStatus.RUNNING
        ):
            _set_node_status(step_run, node_status, cancel_payload)
    for case_run in case_runs:
        if RunNodeStatus(case_run.status) not in TERMINAL_NODE_STATUSES and (
            node_status == RunNodeStatus.CANCELLED
            or RunNodeStatus(case_run.status) == RunNodeStatus.RUNNING
        ):
            _set_node_status(case_run, node_status, cancel_payload)
    _recompute_summary(session, run)
    _set_run_status(
        run,
        run_status,
        RunStatusUpdateRequest(status=run_status),
    )
    session.commit()
    event_type = run_status
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=current_status,
        to_status=event_type,
        case_run_id=case_runs[0].id if case_runs else None,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
    )
    logger.info(
        "run_cancellation_requested" if run_status == RunStatus.CANCELLING else "run_cancelled",
        extra={
            "event": ("RUN_CANCELLING" if run_status == RunStatus.CANCELLING else "RUN_CANCELLED"),
            "run_id": run.id,
            "project_id": run.project_id,
            "from_status": current_status.value,
        },
    )
    detail = get_run_with_children(session, run.id)
    if detail is None:
        raise ResourceNotFoundError("Run 不存在")
    return _run_detail(session, detail)


def force_stop_run(
    session: Session,
    user: CurrentUser,
    run_id: str,
    event_stream: RedisRunEventStream,
) -> RunDetailResponse:
    """请求立即终止执行；Runner 在隔离边界上终止执行子进程后上报。

    CREATED/QUEUED 尚未开始执行，直接取消；ASSIGNED/RUNNING 进入 CANCELLING
    并记录 force_stop_requested_at；重复请求与已终态 Run 保持幂等。
    """

    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    _authorize_run_write(session, user, run)
    current_status = RunStatus(run.status)
    if current_status in TERMINAL_RUN_STATUSES:
        detail = get_run_with_children(session, run.id)
        if detail is None:
            raise ResourceNotFoundError("Run 不存在")
        return _run_detail(session, detail)
    if current_status in {RunStatus.CREATED, RunStatus.QUEUED}:
        case_runs = list(
            session.scalars(select(CaseRun).where(CaseRun.run_id == run.id).with_for_update()).all()
        )
        case_run_ids = [case_run.id for case_run in case_runs]
        step_runs = (
            list(
                session.scalars(
                    select(StepRun).where(StepRun.case_run_id.in_(case_run_ids)).with_for_update()
                ).all()
            )
            if case_run_ids
            else []
        )
        cancel_payload = NodeStatusUpdateRequest(status=RunNodeStatus.CANCELLED)
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(step_run, RunNodeStatus.CANCELLED, cancel_payload)
        for case_run in case_runs:
            if RunNodeStatus(case_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(case_run, RunNodeStatus.CANCELLED, cancel_payload)
        _recompute_summary(session, run)
        _set_run_status(
            run,
            RunStatus.CANCELLED,
            RunStatusUpdateRequest(status=RunStatus.CANCELLED),
        )
        session.commit()
        publish_run_event_best_effort(
            event_stream,
            project_id=run.project_id,
            run_id=run.id,
            from_status=current_status,
            to_status=RunStatus.CANCELLED,
            case_run_id=case_runs[0].id if case_runs else None,
            total=run.total,
            pass_count=run.pass_count,
            fail_count=run.fail_count,
            review_count=run.review_count,
            timeout_count=run.timeout_count,
        )
        detail = get_run_with_children(session, run.id)
        if detail is None:
            raise ResourceNotFoundError("Run 不存在")
        return _run_detail(session, detail)

    # ASSIGNED/RUNNING/CANCELLING：进入 CANCELLING 并持久化强制停止请求标记。
    if current_status in {RunStatus.ASSIGNED, RunStatus.RUNNING}:
        _set_run_status(
            run,
            RunStatus.CANCELLING,
            RunStatusUpdateRequest(status=RunStatus.CANCELLING),
        )
    if run.force_stop_requested_at is None:
        run.force_stop_requested_at = utc_now_naive()
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=current_status,
        to_status=RunStatus.CANCELLING,
        case_run_id=run.case_runs[0].id if run.case_runs else None,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
        event_type="RUN_FORCE_STOP_REQUESTED",
    )
    logger.info(
        "run_force_stop_requested",
        extra={
            "event": "RUN_FORCE_STOP_REQUESTED",
            "run_id": run.id,
            "project_id": run.project_id,
            "from_status": current_status.value,
        },
    )
    detail = get_run_with_children(session, run.id)
    if detail is None:
        raise ResourceNotFoundError("Run 不存在")
    return _run_detail(session, detail)


def _authorize_run_write(session: Session, user: CurrentUser, run: TestRun) -> None:
    project = get_project(session, user, run.project_id)
    ensure_project_writable(session, project, user)


def _authenticate_runner_for_run(session: Session, run: TestRun, credential: str) -> Runner:
    runner = session.get(Runner, run.runner_id) if run.runner_id else None
    if (
        runner is None
        or runner.status != RunnerStatus.ACTIVE.value
        or not credential
        or len(credential) > 512
        or not matches_digest(credential, runner.credential_digest)
    ):
        raise AuthenticationError("Runner credential 无效或不属于该 Run")
    return runner


_DISCONNECT_RUN_STATUSES = frozenset({RunStatus.ASSIGNED, RunStatus.RUNNING})
_RUNNER_DISCONNECTED_ERROR_TYPE = "RUNNER_DISCONNECTED"
_RUNNER_DISCONNECTED_ERROR_MESSAGE = "Runner 心跳超时，执行已异常终止"
_RUN_TOTAL_TIMEOUT_ERROR_TYPE = "TOTAL_TIMEOUT"
_RUN_TOTAL_TIMEOUT_ERROR_MESSAGE = "Run 总超时且超过上报宽限，执行结果未确认"
_RUN_CANCEL_OVERDUE_ERROR_MESSAGE = "取消请求已超过上报宽限，远端执行与 Cleanup 状态未确认"
_RUN_FORCE_STOP_OVERDUE_ERROR_MESSAGE = "强制停止请求已超过上报宽限，远端执行与 Cleanup 状态未确认"
_WEB_EVIDENCE_ERROR_TYPE = "WEB_EVIDENCE_ERROR"
_WEB_EVIDENCE_ERROR_MESSAGE = "Web 执行证据处理失败"


def _heartbeat_expired(runner: Runner, now: datetime, ttl_seconds: float) -> bool:
    """True only when MySQL can prove the heartbeat is past the TTL deadline."""

    if runner.last_heartbeat_at is None:
        return False
    return now >= runner.last_heartbeat_at + timedelta(seconds=ttl_seconds)


def _disconnect_gate_blocks(
    runner: Runner,
    online_status: OnlineStatus,
    redis_available: bool,
    require_heartbeat_expiry: bool,
    now: datetime,
    ttl_seconds: float,
) -> bool:
    """All protection gates that must pass before any Run is touched.

    Redis must positively report OFFLINE with Redis available; when the caller
    requires double-expiry protection (automatic coordinator), MySQL
    ``last_heartbeat_at`` must additionally be provably older than the TTL so a
    transient key loss or an in-flight heartbeat write cannot misfire.
    """

    if online_status != OnlineStatus.OFFLINE or not redis_available:
        return True
    if require_heartbeat_expiry and not _heartbeat_expired(runner, now, ttl_seconds):
        return True
    return runner.status != RunnerStatus.ACTIVE.value


def _disconnect_skip(
    runner_id: str, online_status: OnlineStatus, redis_available: bool
) -> RunnerDisconnectReconcileResponse:
    return RunnerDisconnectReconcileResponse(
        runner_id=runner_id,
        online_status=online_status,
        redis_available=redis_available,
        reconciled_run_count=0,
    )


def reconcile_disconnected_runner_runs(
    session: Session,
    user: CurrentUser,
    runner_id: str,
    store: RedisRunnerHeartbeatStore,
    event_stream: RedisRunEventStream,
) -> RunnerDisconnectReconcileResponse:
    """Admin-triggered reconciliation with the existing Redis-positive-OFFLINE contract."""

    if not is_admin(user):
        raise AuthorizationError("只有管理员可以收口 Runner 断线任务")
    return reconcile_disconnected_runner_runs_core(session, runner_id, store, event_stream)


def reconcile_disconnected_runner_runs_core(
    session: Session,
    runner_id: str,
    store: RedisRunnerHeartbeatStore,
    event_stream: RedisRunEventStream,
    *,
    require_heartbeat_expiry: bool = False,
    heartbeat_ttl_seconds: float | None = None,
    now: datetime | None = None,
) -> RunnerDisconnectReconcileResponse:
    """Fail active Runs only after Redis positively reports the Runner as offline.

    The Runner and each Run are locked in the same database transaction so a
    normal completion that wins the Run lock is observed as terminal and is not
    overwritten. Redis remains an observation boundary, never an authority.
    ``require_heartbeat_expiry`` adds the MySQL ``last_heartbeat_at`` TTL gate
    used by the automatic coordinator; the manual admin path keeps the
    Redis-only contract for backwards compatibility.
    """

    resolved_now = now or utc_now_naive()
    resolved_ttl = (
        heartbeat_ttl_seconds
        if heartbeat_ttl_seconds is not None
        else get_settings().runner_heartbeat_ttl_seconds
    )
    runner = session.get(Runner, runner_id)
    if runner is None:
        raise ResourceNotFoundError("Runner 不存在")

    online_status, _, redis_available = get_runner_online_state(runner, store)
    if _disconnect_gate_blocks(
        runner,
        online_status,
        redis_available,
        require_heartbeat_expiry,
        resolved_now,
        resolved_ttl,
    ):
        return _disconnect_skip(runner_id, online_status, redis_available)

    runs = list(
        session.scalars(
            select(TestRun)
            .where(
                TestRun.runner_id == runner.id,
                TestRun.status.in_(status.value for status in _DISCONNECT_RUN_STATUSES),
            )
            .order_by(TestRun.created_at.asc(), TestRun.id.asc())
            .with_for_update()
        ).all()
    )
    runner = session.scalar(select(Runner).where(Runner.id == runner_id).with_for_update())
    if runner is None:
        raise ResourceNotFoundError("Runner 不存在")
    online_status, _, redis_available = get_runner_online_state(runner, store)
    if _disconnect_gate_blocks(
        runner,
        online_status,
        redis_available,
        require_heartbeat_expiry,
        resolved_now,
        resolved_ttl,
    ):
        return _disconnect_skip(runner_id, online_status, redis_available)
    reconciled: list[tuple[TestRun, RunStatus]] = []
    failure_payload = NodeStatusUpdateRequest(
        status=RunNodeStatus.FAILED,
        error_type=_RUNNER_DISCONNECTED_ERROR_TYPE,
        error_message=_RUNNER_DISCONNECTED_ERROR_MESSAGE,
    )
    for run in runs:
        previous_status = RunStatus(run.status)
        case_runs = list(
            session.scalars(select(CaseRun).where(CaseRun.run_id == run.id).with_for_update()).all()
        )
        case_run_ids = [case_run.id for case_run in case_runs]
        step_runs = (
            list(
                session.scalars(
                    select(StepRun).where(StepRun.case_run_id.in_(case_run_ids)).with_for_update()
                ).all()
            )
            if case_run_ids
            else []
        )
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(step_run, RunNodeStatus.FAILED, failure_payload)
        for case_run in case_runs:
            if RunNodeStatus(case_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(case_run, RunNodeStatus.FAILED, failure_payload)
        _recompute_summary(session, run)
        _ensure_terminal_run_matches_children(session, run, RunStatus.FAILED)
        _set_run_status(
            run,
            RunStatus.FAILED,
            RunStatusUpdateRequest(
                status=RunStatus.FAILED,
                error_type=_RUNNER_DISCONNECTED_ERROR_TYPE,
                error_message=_RUNNER_DISCONNECTED_ERROR_MESSAGE,
            ),
        )
        reconciled.append((run, previous_status))

    if reconciled:
        session.commit()
        for run, previous_status in reconciled:
            publish_run_event_best_effort(
                event_stream,
                project_id=run.project_id,
                run_id=run.id,
                from_status=previous_status,
                to_status=RunStatus.FAILED,
                case_run_id=run.case_runs[0].id if run.case_runs else None,
                total=run.total,
                pass_count=run.pass_count,
                fail_count=run.fail_count,
                review_count=run.review_count,
                timeout_count=run.timeout_count,
            )
    return RunnerDisconnectReconcileResponse(
        runner_id=runner.id,
        online_status=online_status,
        redis_available=redis_available,
        reconciled_run_count=len(reconciled),
        reconciled_run_ids=[run.id for run, _ in reconciled],
    )


def _overdue_run_deadline(run: TestRun, grace_seconds: float) -> datetime | None:
    if run.started_at is None or run.total_timeout_ms is None:
        return None
    return run.started_at + timedelta(
        milliseconds=run.total_timeout_ms,
        seconds=grace_seconds,
    )


def _overdue_cancellation_error(run: TestRun) -> tuple[str, str]:
    if run.force_stop_requested_at is not None:
        return "FORCE_STOP_REQUESTED", _RUN_FORCE_STOP_OVERDUE_ERROR_MESSAGE
    return "CANCEL_REQUESTED", _RUN_CANCEL_OVERDUE_ERROR_MESSAGE


def reconcile_overdue_run_core(
    session: Session,
    run_id: str,
    event_stream: RedisRunEventStream,
    *,
    grace_seconds: float,
    now: datetime | None = None,
) -> RunStatus | None:
    """Close one execution whose persisted deadline and reporting grace have elapsed.

    The Run row is locked before the status and deadline are rechecked. Existing
    terminal child results and Evidence are never rewritten; only still-active
    nodes are closed. This is a control-plane fallback and deliberately does not
    create an executor result, a Web trace, or a Cleanup-success claim.
    """

    resolved_now = now or utc_now_naive()
    run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
    if run is None:
        return None
    previous_status = RunStatus(run.status)
    if previous_status not in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        return None
    deadline = _overdue_run_deadline(run, grace_seconds)
    if deadline is None or resolved_now <= deadline:
        return None

    case_runs = list(
        session.scalars(
            select(CaseRun)
            .where(CaseRun.run_id == run.id)
            .order_by(CaseRun.sequence_no.asc())
            .with_for_update()
        ).all()
    )
    if not case_runs:
        return None
    case_run_ids = [case_run.id for case_run in case_runs]
    step_runs = list(
        session.scalars(
            select(StepRun)
            .where(StepRun.case_run_id.in_(case_run_ids))
            .order_by(StepRun.case_run_id.asc(), StepRun.sequence_no.asc())
            .with_for_update()
        ).all()
    )

    if previous_status == RunStatus.RUNNING:
        run_status = RunStatus.TIMEOUT
        run_error_type = _RUN_TOTAL_TIMEOUT_ERROR_TYPE
        run_error_message = _RUN_TOTAL_TIMEOUT_ERROR_MESSAGE
        timeout_payload = NodeStatusUpdateRequest(
            status=RunNodeStatus.TIMEOUT,
            error_type=run_error_type,
            error_message=run_error_message,
        )
        cancellation_payload = NodeStatusUpdateRequest(
            status=RunNodeStatus.CANCELLED,
            error_type="CANCEL_REQUESTED",
            error_message=_RUN_CANCEL_OVERDUE_ERROR_MESSAGE,
        )
        for step_run in step_runs:
            current = RunNodeStatus(step_run.status)
            if current in TERMINAL_NODE_STATUSES:
                continue
            if current == RunNodeStatus.CANCELLING:
                _set_node_status(step_run, RunNodeStatus.CANCELLED, cancellation_payload)
            else:
                _set_node_status(step_run, RunNodeStatus.TIMEOUT, timeout_payload)
        for case_run in case_runs:
            current = RunNodeStatus(case_run.status)
            if current in TERMINAL_NODE_STATUSES:
                continue
            if current == RunNodeStatus.CANCELLING:
                _set_node_status(case_run, RunNodeStatus.CANCELLED, cancellation_payload)
            else:
                _set_node_status(case_run, RunNodeStatus.TIMEOUT, timeout_payload)
    else:
        run_status = RunStatus.CANCELLED
        run_error_type, run_error_message = _overdue_cancellation_error(run)
        cancellation_payload = NodeStatusUpdateRequest(
            status=RunNodeStatus.CANCELLED,
            error_type=run_error_type,
            error_message=run_error_message,
        )
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(step_run, RunNodeStatus.CANCELLED, cancellation_payload)
        for case_run in case_runs:
            if RunNodeStatus(case_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(case_run, RunNodeStatus.CANCELLED, cancellation_payload)

    if any(RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES for item in case_runs):
        raise RunStateConflictError("超期 Run 仍有无法安全收口的 CaseRun")
    _recompute_summary(session, run)
    _set_run_status(
        run,
        run_status,
        RunStatusUpdateRequest(
            status=run_status,
            error_type=run_error_type,
            error_message=run_error_message,
        ),
    )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=previous_status,
        to_status=run_status,
        case_run_id=case_runs[0].id,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
    )
    logger.info(
        "overdue_run_reconciled",
        extra={
            "event": "OVERDUE_RUN_RECONCILED",
            "run_id": run.id,
            "project_id": run.project_id,
            "from_status": previous_status.value,
            "to_status": run_status.value,
            "error_type": run_error_type,
        },
    )
    return run_status


_DISPATCH_MAX_ATTEMPTS = 10
_SAFE_RUNNER_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PUBLISH_ERROR_MESSAGES = {
    "BROKER_NACK": "RabbitMQ 未确认任务消息",
    "BROKER_UNROUTABLE": "RabbitMQ 未找到可接收该任务的 Runner 路由",
    "BROKER_UNAVAILABLE": "RabbitMQ 暂不可用，任务已保留待重试",
    "BROKER_ERROR": "RabbitMQ 任务投递失败，任务已保留待重试",
}


def _dispatch_runner_issues(
    session: Session,
    run: TestRun,
    store: RedisRunnerHeartbeatStore,
) -> Runner:
    runner = session.scalar(select(Runner).where(Runner.id == run.runner_id).with_for_update())
    issues: list[RunValidationIssue] = []
    if runner is None:
        _issue(issues, "RUNNER_NOT_FOUND", "Runner 不存在或不可用", "runner_id")
        raise RunValidationError([item.model_dump() for item in issues])
    if runner.status != RunnerStatus.ACTIVE.value:
        _issue(issues, "RUNNER_REVOKED", "Runner credential 已撤销或 Runner 未启用", "runner_id")
    if not runner.credential_digest or len(runner.credential_digest) != 64:
        _issue(issues, "RUNNER_CREDENTIAL_INVALID", "Runner credential 摘要不可用", "runner_id")
    try:
        heartbeat = store.get_heartbeat(runner.id)
    except RedisStoreUnavailableError as exc:
        raise RedisUnavailableError("Runner 在线状态暂不可用，请稍后重试") from exc
    if heartbeat is None:
        _issue(issues, "RUNNER_OFFLINE", "Runner 当前不在线，不能投递 Run", "runner_id")
    runner_tags = {item.tag for item in runner.tags}
    if set(run.required_tags) - runner_tags:
        _issue(issues, "RUNNER_TAG_MISSING", "Runner 缺少 Run 要求的 Tag", "required_tags")
    ready_capabilities = {item.capability for item in runner.capabilities if item.status == "READY"}
    if set(run.required_capabilities) - ready_capabilities:
        _issue(
            issues,
            "RUNNER_CAPABILITY_MISSING",
            "Runner 缺少 Run 要求的可用 Capability",
            "required_capabilities",
        )
    slot = next(
        (item for item in runner.slots if item.slot_type == run.required_slot_type),
        None,
    )
    if slot is None or slot.available < run.required_slot_count:
        _issue(
            issues,
            "RUNNER_SLOT_UNAVAILABLE",
            "Runner 没有满足要求的可用 Slot",
            "required_slot_count",
        )
    if not _SAFE_RUNNER_ID.fullmatch(runner.id):
        _issue(issues, "RUNNER_ID_INVALID", "Runner ID 不符合任务路由协议", "runner_id")
    if issues:
        raise RunValidationError([item.model_dump() for item in issues])
    return runner


def _task_envelope(
    run: TestRun, *, message_id: str, attempt: int, enqueued_at: datetime
) -> dict[str, Any]:
    envelope = RunTaskEnvelope(
        schema_version=1,
        message_id=message_id,
        run_id=run.id,
        runner_id=run.runner_id,
        run_type=run.run_type,
        required_slot_type=run.required_slot_type,
        attempt=attempt,
        enqueued_at=enqueued_at,
    )
    return envelope.model_dump(mode="json")


def _expected_dispatch_routing_key(run: TestRun) -> str:
    return f"runner.{run.runner_id}.{run.required_slot_type.lower()}"


def _validate_dispatch_payload(run: TestRun, outbox: RunDispatchOutbox) -> RunTaskEnvelope | None:
    if (
        outbox.schema_version != 1
        or outbox.run_id != run.id
        or outbox.runner_id != run.runner_id
        or outbox.routing_key != _expected_dispatch_routing_key(run)
    ):
        return None
    try:
        envelope = RunTaskEnvelope.model_validate(outbox.payload)
    except ValidationError:
        return None
    if (
        envelope.run_id != run.id
        or envelope.runner_id != run.runner_id
        or envelope.run_type.value != run.run_type
        or envelope.required_slot_type.value != run.required_slot_type
        or envelope.message_id != outbox.message_id
    ):
        return None
    return envelope


def _dispatch_response(run: TestRun, outbox: RunDispatchOutbox) -> RunDispatchResponse:
    return RunDispatchResponse(
        run_id=run.id,
        run_status=RunStatus(run.status),
        outbox_status=DispatchOutboxStatus(outbox.status),
        message_id=outbox.message_id,
        routing_key=outbox.routing_key,
        attempt_count=outbox.attempt_count,
        last_error=outbox.last_error,
        published_at=outbox.published_at,
    )


def _record_publish_result(
    session: Session,
    outbox_id: int,
    result: TaskPublishResult,
) -> RunDispatchOutbox:
    outbox = session.scalar(
        select(RunDispatchOutbox).where(RunDispatchOutbox.id == outbox_id).with_for_update()
    )
    if outbox is None:
        raise ResourceConflictError("Run Dispatch Outbox 不存在")
    if result.published:
        if outbox.status != DispatchOutboxStatus.PUBLISHED.value:
            outbox.status = DispatchOutboxStatus.PUBLISHED.value
            outbox.published_at = utc_now_naive()
            outbox.last_error = None
    elif outbox.status != DispatchOutboxStatus.PUBLISHED.value:
        outbox.status = DispatchOutboxStatus.FAILED.value
        code = result.code if result.code in _PUBLISH_ERROR_MESSAGES else "BROKER_ERROR"
        outbox.last_error = f"{code}: {_PUBLISH_ERROR_MESSAGES[code]}"
    session.commit()
    session.refresh(outbox)
    return outbox


def dispatch_run(
    session: Session,
    user: CurrentUser,
    run_id: str,
    store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
    *,
    runner_prevalidated: bool = False,
) -> RunDispatchResponse:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    _authorize_run_write(session, user, run)
    outbox = get_dispatch_outbox_for_run(session, run.id, for_update=True)
    if outbox is not None and outbox.status == DispatchOutboxStatus.PUBLISHED.value:
        if _validate_dispatch_payload(run, outbox) is None:
            raise ResourceConflictError("Run 任务信封无效，不能重复投递")
        return _dispatch_response(run, outbox)
    if RunStatus(run.status) not in {RunStatus.CREATED, RunStatus.QUEUED}:
        raise RunStateConflictError("只有 CREATED 或待重试的 QUEUED Run 可以投递")
    if RunStatus(run.status) == RunStatus.QUEUED and outbox is None:
        raise RunStateConflictError("QUEUED Run 缺少 Dispatch Outbox，不能重新投递")

    if run.run_type == RunType.API_CASE.value:
        _load_supported_api_case(session, run)
    if not runner_prevalidated:
        _dispatch_runner_issues(session, run, store)
    if outbox is None:
        message_id = f"msg_{uuid4().hex}"
        enqueued_at = utc_now_aware()
        payload = _task_envelope(
            run,
            message_id=message_id,
            attempt=1,
            enqueued_at=enqueued_at,
        )
        outbox = RunDispatchOutbox(
            message_id=message_id,
            run_id=run.id,
            runner_id=run.runner_id,
            routing_key=_expected_dispatch_routing_key(run),
            schema_version=1,
            payload=payload,
            status=DispatchOutboxStatus.PENDING.value,
            attempt_count=0,
        )
        run.dispatch_outbox = outbox
        status_changed = _set_run_status(
            run,
            RunStatus.QUEUED,
            RunStatusUpdateRequest(status=RunStatus.QUEUED),
        )
        session.add(outbox)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ResourceConflictError("Run Dispatch Outbox 创建冲突，请重试") from exc
        session.refresh(run)
        if status_changed:
            publish_run_event_best_effort(
                event_stream,
                project_id=run.project_id,
                run_id=run.id,
                from_status=RunStatus.CREATED,
                to_status=RunStatus.QUEUED,
                case_run_id=run.case_runs[0].id if run.case_runs else None,
                total=run.total,
                pass_count=run.pass_count,
                fail_count=run.fail_count,
                review_count=run.review_count,
                timeout_count=run.timeout_count,
            )
    if _validate_dispatch_payload(run, outbox) is None:
        outbox.status = DispatchOutboxStatus.FAILED.value
        outbox.last_error = "TASK_ENVELOPE_INVALID: 任务信封不符合 V1 协议"
        session.commit()
        return _dispatch_response(run, outbox)
    if outbox.attempt_count >= _DISPATCH_MAX_ATTEMPTS:
        outbox.status = DispatchOutboxStatus.FAILED.value
        outbox.last_error = "DISPATCH_ATTEMPTS_EXHAUSTED: 超过任务投递重试上限"
        session.commit()
        return _dispatch_response(run, outbox)

    next_attempt = outbox.attempt_count + 1
    envelope = _validate_dispatch_payload(run, outbox)
    if envelope is None:
        outbox.status = DispatchOutboxStatus.FAILED.value
        outbox.last_error = "TASK_ENVELOPE_INVALID: 任务信封不符合 V1 协议"
        session.commit()
        return _dispatch_response(run, outbox)
    outbox.payload = envelope.model_copy(update={"attempt": next_attempt}).model_dump(mode="json")
    outbox.attempt_count = next_attempt
    outbox.status = DispatchOutboxStatus.PENDING.value
    outbox.last_error = None
    session.commit()
    result = publisher.publish(routing_key=outbox.routing_key, payload=outbox.payload)
    outbox = _record_publish_result(session, outbox.id, result)
    logger.info(
        "run_dispatch_result",
        extra={
            "event": "RUN_DISPATCH_RESULT",
            "run_id": run.id,
            "message_id": outbox.message_id,
            "runner_id": outbox.runner_id,
            "outbox_status": outbox.status,
            "attempt_count": outbox.attempt_count,
        },
    )
    return _dispatch_response(run, outbox)


def _batch_dispatch_error_item(run_id: str, error: AppError) -> RunBatchDispatchItemResponse:
    messages: list[str] = []
    if isinstance(error.details, list):
        for detail in error.details:
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                messages.append(detail["message"])
    message = "；".join(messages) or error.message
    return RunBatchDispatchItemResponse(
        run_id=run_id,
        dispatched=False,
        error_code=error.code,
        message=message[:1000],
    )


def dispatch_runs_batch(
    session: Session,
    user: CurrentUser,
    payload: RunBatchDispatchRequest,
    store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> RunBatchDispatchResponse:
    """Preflight all runs before publishing so one Runner slot can safely queue the batch."""

    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    ready_run_ids: list[str] = []
    item_by_run_id: dict[str, RunBatchDispatchItemResponse] = {}

    # This phase deliberately completes before the first RabbitMQ publish. Once the
    # Runner claims item 1 its live slot becomes unavailable, but the remaining
    # prevalidated messages must still enter the same prefetch=1 queue.
    for run_id in payload.run_ids:
        try:
            run = get_run(session, run_id, for_update=True)
            if run is None or run.project_id != payload.project_id:
                raise ResourceNotFoundError("运行不存在或不属于当前项目")
            if RunStatus(run.status) != RunStatus.CREATED:
                raise RunStateConflictError("只有“已创建”的运行可以加入本次批量执行")
            if run.run_type == RunType.API_CASE.value:
                _load_supported_api_case(session, run)
            _dispatch_runner_issues(session, run, store)
            ready_run_ids.append(run_id)
        except AppError as error:
            item_by_run_id[run_id] = _batch_dispatch_error_item(run_id, error)

    for run_id in ready_run_ids:
        try:
            response = dispatch_run(
                session,
                user,
                run_id,
                store,
                publisher,
                event_stream,
                runner_prevalidated=True,
            )
            dispatched = response.outbox_status == DispatchOutboxStatus.PUBLISHED
            item_by_run_id[run_id] = RunBatchDispatchItemResponse(
                run_id=run_id,
                dispatched=dispatched,
                run_status=response.run_status,
                outbox_status=response.outbox_status,
                error_code=None if dispatched else "RUN_DISPATCH_NOT_PUBLISHED",
                message=(
                    "已加入 Runner 执行队列"
                    if dispatched
                    else (response.last_error or "运行未能加入执行队列")
                ),
            )
        except AppError as error:
            session.rollback()
            item_by_run_id[run_id] = _batch_dispatch_error_item(run_id, error)
        except Exception:  # noqa: BLE001 - 批次边界必须隔离单条基础设施异常
            session.rollback()
            logger.exception(
                "run_batch_dispatch_unexpected_error",
                extra={"event": "RUN_BATCH_DISPATCH_ERROR", "run_id": run_id},
            )
            item_by_run_id[run_id] = RunBatchDispatchItemResponse(
                run_id=run_id,
                dispatched=False,
                error_code="RUN_DISPATCH_ERROR",
                message="运行投递异常，请稍后重试",
            )

    items = [item_by_run_id[run_id] for run_id in payload.run_ids]
    dispatched_count = sum(1 for item in items if item.dispatched)
    return RunBatchDispatchResponse(
        requested=len(items),
        dispatched=dispatched_count,
        failed=len(items) - dispatched_count,
        items=items,
    )


def _finalize_cancelling_run_on_claim(
    session: Session, run: TestRun, event_stream: RedisRunEventStream
) -> None:
    """Runner 重启重新认领取消中任务时，把 Run 幂等收口为 CANCELLED。"""

    force_stopped = run.force_stop_requested_at is not None
    error_type = "FORCE_STOP_REQUESTED" if force_stopped else "CANCEL_REQUESTED"
    error_message = "执行已被强制停止，Cleanup 可能未完成" if force_stopped else "执行已取消"
    case_runs = list(
        session.scalars(select(CaseRun).where(CaseRun.run_id == run.id).with_for_update()).all()
    )
    case_run_ids = [case_run.id for case_run in case_runs]
    step_runs = (
        list(
            session.scalars(
                select(StepRun).where(StepRun.case_run_id.in_(case_run_ids)).with_for_update()
            ).all()
        )
        if case_run_ids
        else []
    )
    node_payload = NodeStatusUpdateRequest(
        status=RunNodeStatus.CANCELLED,
        error_type=error_type,
        error_message=error_message,
    )
    for step_run in step_runs:
        if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
            _set_node_status(step_run, RunNodeStatus.CANCELLED, node_payload)
    for case_run in case_runs:
        if RunNodeStatus(case_run.status) not in TERMINAL_NODE_STATUSES:
            _set_node_status(case_run, RunNodeStatus.CANCELLED, node_payload)
    if force_stopped:
        run.force_stopped = True
    _recompute_summary(session, run)
    _ensure_terminal_run_matches_children(session, run, RunStatus.CANCELLED)
    _set_run_status(
        run,
        RunStatus.CANCELLED,
        RunStatusUpdateRequest(
            status=RunStatus.CANCELLED,
            error_type=error_type,
            error_message=error_message,
        ),
    )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=RunStatus.CANCELLING,
        to_status=RunStatus.CANCELLED,
        case_run_id=case_runs[0].id if case_runs else None,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
    )


def claim_run(
    session: Session,
    run_id: str,
    credential: str,
    payload: RunClaimRequest,
    event_stream: RedisRunEventStream,
) -> RunClaimResponse:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    runner = _authenticate_runner_for_run(session, run, credential)
    outbox = get_dispatch_outbox_for_run(session, run.id, for_update=True)
    if outbox is None or outbox.message_id != payload.message_id:
        raise RunStateConflictError("任务消息与 Run 不匹配")
    if _validate_dispatch_payload(run, outbox) is None:
        raise RunStateConflictError("任务信封无效，不能认领")
    if (
        RunStatus(run.status) in {RunStatus.ASSIGNED, RunStatus.RUNNING}
        and outbox.status == DispatchOutboxStatus.PUBLISHED.value
        and outbox.claimed_runner_id == runner.id
        and outbox.claimed_at is not None
    ):
        return RunClaimResponse(
            run_id=run.id,
            message_id=outbox.message_id,
            runner_id=runner.id,
            status=RunStatus.ASSIGNED,
            claimed_at=to_utc_aware(outbox.claimed_at),
            idempotent=True,
        )
    if RunStatus(run.status) == RunStatus.CANCELLED:
        if (
            outbox.status != DispatchOutboxStatus.PUBLISHED.value
            or outbox.claimed_runner_id is not None
            or outbox.claimed_at is not None
        ):
            raise RunStateConflictError("已取消 Run 的任务消息不可认领")
        return RunClaimResponse(
            run_id=run.id,
            message_id=outbox.message_id,
            runner_id=runner.id,
            status=RunStatus.CANCELLED,
            claimed_at=None,
            idempotent=True,
        )
    if RunStatus(run.status) == RunStatus.CANCELLING:
        # Runner 重启后重新认领取消中任务：安全收口为 CANCELLED 并让消息 ACK。
        if outbox.status != DispatchOutboxStatus.PUBLISHED.value or (
            outbox.claimed_runner_id is not None and outbox.claimed_runner_id != runner.id
        ):
            raise RunStateConflictError("取消中 Run 的任务消息不可认领")
        if outbox.claimed_runner_id is None:
            outbox.claimed_runner_id = runner.id
            outbox.claimed_at = utc_now_naive()
        _finalize_cancelling_run_on_claim(session, run, event_stream)
        return RunClaimResponse(
            run_id=run.id,
            message_id=outbox.message_id,
            runner_id=runner.id,
            status=RunStatus.CANCELLED,
            claimed_at=None,
            idempotent=True,
        )
    if RunStatus(run.status) != RunStatus.QUEUED:
        raise RunStateConflictError("只有 QUEUED Run 可以被 Runner 认领")
    if run.run_type == RunType.API_CASE.value:
        _load_supported_api_case(session, run)
    if (
        outbox.status == DispatchOutboxStatus.PENDING.value
        and outbox.claimed_runner_id is None
        and outbox.claimed_at is None
    ):
        raise RunDispatchPendingError()
    if outbox.status != DispatchOutboxStatus.PUBLISHED.value:
        raise RunStateConflictError("任务尚未被 RabbitMQ 确认发布")
    if outbox.claimed_runner_id is not None:
        raise RunStateConflictError("任务已被其他消息认领")
    now = utc_now_naive()
    outbox.claimed_runner_id = runner.id
    outbox.claimed_at = now
    if run.total_timeout_ms is None:
        # Capture the configured default exactly once, before any execution plan
        # can be issued. Later configuration changes must not alter this task's
        # Runner budget or the control-plane overdue deadline derived from it.
        run.total_timeout_ms = get_settings().runner_total_timeout_default_ms
    _set_run_status(
        run,
        RunStatus.ASSIGNED,
        RunStatusUpdateRequest(status=RunStatus.ASSIGNED),
    )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=run.project_id,
        run_id=run.id,
        from_status=RunStatus.QUEUED,
        to_status=RunStatus.ASSIGNED,
        case_run_id=run.case_runs[0].id if run.case_runs else None,
        total=run.total,
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        review_count=run.review_count,
        timeout_count=run.timeout_count,
    )
    logger.info(
        "run_claimed",
        extra={
            "event": "RUN_CLAIMED",
            "run_id": run.id,
            "message_id": outbox.message_id,
            "runner_id": runner.id,
        },
    )
    return RunClaimResponse(
        run_id=run.id,
        message_id=outbox.message_id,
        runner_id=runner.id,
        status=RunStatus.ASSIGNED,
        claimed_at=to_utc_aware(now),
        idempotent=False,
    )


@dataclass
class _ApiExecutionContext:
    run: TestRun
    runner: Runner
    outbox: RunDispatchOutbox
    case_run: CaseRun


_RUNTIME_TEMPLATE_MARKER = "{{"
_SENSITIVE_REQUEST_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
        "api_key",
        "access_token",
        "refresh_token",
        "credential",
    }
)


def _is_sensitive_request_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized in _SENSITIVE_REQUEST_KEYS or any(
        marker in normalized
        for marker in (
            "authorization",
            "cookie",
            "password",
            "secret",
            "token",
            "api_key",
            "access_key",
            "credential",
        )
    )


def _raise_execution_unsupported() -> None:
    raise RunExecutionUnsupportedError()


def _contains_runtime_template(value: Any) -> bool:
    if isinstance(value, str):
        return _RUNTIME_TEMPLATE_MARKER in value or "}}" in value
    if isinstance(value, dict):
        return any(
            _contains_runtime_template(key) or _contains_runtime_template(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_runtime_template(child) for child in value)
    return False


def _api_secret_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    matched = _API_SECRET_TEMPLATE.fullmatch(value)
    return matched.group(1) if matched else None


def _api_runtime_name(value: Any) -> str | None:
    if not isinstance(value, str) or _api_secret_name(value) is not None:
        return None
    matched = _RUNTIME_TEMPLATE.fullmatch(value)
    return value[2:-2] if matched else None


def _api_secret_references(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            reference for child in value.values() for reference in _api_secret_references(child)
        ]
    if isinstance(value, list | tuple):
        return [reference for child in value for reference in _api_secret_references(child)]
    return [value] if _api_secret_name(value) is not None else []


def _api_secret_reference_values(request: ApiRequestTemplate) -> list[str]:
    return _api_secret_references(request.model_dump(mode="json"))


def _api_sensitive_value_is_reference(value: Any, runtime_names: set[str]) -> bool:
    if _api_secret_name(value) is not None:
        return True
    runtime_name = _api_runtime_name(value)
    return runtime_name is not None and runtime_name in runtime_names


def _contains_unsafe_sensitive_request_value(value: Any, runtime_names: set[str]) -> bool:
    if isinstance(value, dict):
        named_field = value.get("name")
        if (
            isinstance(named_field, str)
            and _is_sensitive_request_name(named_field)
            and not _api_sensitive_value_is_reference(value.get("value"), runtime_names)
        ):
            return True
        for key, child in value.items():
            if _is_sensitive_request_name(str(key)) and not _api_sensitive_value_is_reference(
                child, runtime_names
            ):
                return True
            if _contains_unsafe_sensitive_request_value(child, runtime_names):
                return True
    elif isinstance(value, list | tuple):
        return any(
            _contains_unsafe_sensitive_request_value(child, runtime_names) for child in value
        )
    return False


def _api_request_credentials_valid(request: ApiRequestTemplate, runtime_names: set[str]) -> bool:
    auth = request.auth
    if auth.type == AuthType.NONE:
        auth_valid = (
            all(
                value is None
                for value in (
                    auth.token,
                    auth.username,
                    auth.password,
                    auth.key_name,
                    auth.key_value,
                )
            )
            and auth.placement.value == "HEADER"
        )
    elif auth.type == AuthType.BEARER:
        auth_valid = (
            _api_sensitive_value_is_reference(auth.token, runtime_names)
            and all(
                value is None
                for value in (auth.username, auth.password, auth.key_name, auth.key_value)
            )
            and auth.placement.value == "HEADER"
        )
    elif auth.type == AuthType.BASIC:
        auth_valid = (
            bool(auth.username)
            and ":" not in (auth.username or "")
            and not _contains_runtime_template(auth.username)
            and _api_sensitive_value_is_reference(auth.password, runtime_names)
            and all(value is None for value in (auth.token, auth.key_name, auth.key_value))
            and auth.placement.value == "HEADER"
        )
    elif auth.type == AuthType.API_KEY:
        auth_valid = (
            bool(auth.key_name)
            and not _contains_runtime_template(auth.key_name)
            and _api_sensitive_value_is_reference(auth.key_value, runtime_names)
            and all(value is None for value in (auth.token, auth.username, auth.password))
        )
    else:
        auth_valid = False
    if not auth_valid:
        return False
    if any(
        item.enabled and not _api_sensitive_value_is_reference(item.value, runtime_names)
        for item in request.cookies
    ):
        return False
    if any(
        item.enabled
        and _is_sensitive_request_name(item.name)
        and not _api_sensitive_value_is_reference(item.value, runtime_names)
        for item in [*request.headers, *request.query_params]
    ):
        return False
    return not _contains_unsafe_sensitive_request_value(request.body.content, runtime_names)


def _api_secret_for_run(
    session: Session,
    project_id: int,
    environment_id: int | None,
    reference: str,
) -> Secret:
    name = _api_secret_name(reference)
    if name is None:
        raise RunExecutionUnsupportedError("API 请求 Secret 引用格式无效")
    secret = session.scalar(
        select(Secret).where(
            Secret.project_id == project_id,
            Secret.name == name,
            Secret.enabled.is_(True),
        )
    )
    if secret is None or secret.environment_id not in {None, environment_id}:
        raise RunExecutionUnsupportedError("API 请求 Secret 不存在、已停用或环境不匹配")
    return secret


def _api_secret_reference_issues(
    session: Session,
    project_id: int,
    environment_id: int | None,
    request: ApiRequestTemplate,
) -> list[RunValidationIssue]:
    issues: list[RunValidationIssue] = []
    for reference in set(_api_secret_reference_values(request)):
        try:
            _api_secret_for_run(session, project_id, environment_id, reference)
        except RunExecutionUnsupportedError:
            _issue(
                issues,
                "API_SECRET_INVALID",
                "API 请求引用的 Secret 不存在、已停用或不属于当前项目/环境",
                "request",
            )
            break
    return issues


def _api_runtime_context(
    session: Session,
    project_id: int,
    environment_id: int | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "project_id": project_id,
        "environment_id": environment_id,
    }
    if environment_id is None:
        return context
    environment = session.get(Environment, environment_id)
    if environment is None or environment.project_id != project_id or not environment.enabled:
        raise RunExecutionUnsupportedError("API Runtime Environment 不可用")
    try:
        values = build_environment_context(session, environment_id)
    except (TypeError, ValueError) as exc:
        raise RunExecutionUnsupportedError("API Runtime Context 无法安全读取") from exc
    if environment.base_url:
        values["base_url"] = str(environment.base_url)
    for name, value in values.items():
        if _is_sensitive_request_name(name):
            continue
        try:
            validate_secret_free_payload({name: value})
        except (TypeError, ValueError):
            continue
        context[name] = value
    return context


def _resolve_api_runtime_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_api_runtime_value(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_api_runtime_value(child, context) for child in value]
    if isinstance(value, str):
        if _api_secret_name(value) is not None:
            return value
        if "{{$" in value:
            raise RunExecutionUnsupportedError("动态 Runtime 值必须由 Runner 现场动作生成")
        try:
            resolved = resolve_template(value, context)
        except ResourceConflictError as exc:
            raise RunExecutionUnsupportedError("API Runtime Context 引用无法解析") from exc
        if isinstance(resolved, str) and _contains_runtime_template(resolved):
            raise RunExecutionUnsupportedError("API Runtime Context 模板格式无效")
        return resolved
    return value


def _api_runtime_reference_issues(
    session: Session,
    project_id: int,
    environment_id: int | None,
    request: ApiRequestTemplate,
    pre_actions: list[Any],
    extractors: list[Any],
    post_actions: list[Any],
    extra_context: dict[str, Any] | None = None,
) -> list[RunValidationIssue]:
    try:
        context = _api_runtime_context(session, project_id, environment_id)
        context.update(extra_context or {})
        for action in pre_actions:
            if not action.enabled:
                continue
            if action_type(action) == ActionType.SET_VARIABLE:
                context[action.name] = _resolve_api_runtime_value(action.value, context)
            elif action_type(action) == ActionType.FAKER:
                context[action.name] = (
                    False
                    if action.generator.value == "boolean"
                    else (0 if action.generator.value == "integer" else "generated-preview")
                )
            elif action_type(action) == ActionType.PYTHON_SCRIPT:
                try:
                    run_safe_script(action.script, context)
                except ResourceConflictError as exc:
                    raise RunExecutionUnsupportedError(
                        "API Python Script Runtime Context 无法解析"
                    ) from exc
            elif action_type(action) == ActionType.SQL_QUERY:
                context[action.result_variable] = []
            elif action_type(action) in {ActionType.GET_TOKEN, ActionType.API_SETUP}:
                if action.request is None:
                    raise RunExecutionUnsupportedError("API 前置请求缺少真实请求模板")
                _resolve_api_runtime_value(action.request.model_dump(mode="json"), context)
                context[action.name] = "dynamic-token-preview"
        _resolve_api_runtime_value(request.model_dump(mode="json"), context)
        for extractor in extractors:
            if extractor.enabled:
                context[extractor.name] = "extracted-preview"
        for action in post_actions:
            if not action.enabled:
                continue
            if action_type(action) == ActionType.SET_VARIABLE:
                context[action.name] = _resolve_api_runtime_value(action.value, context)
            elif action_type(action) == ActionType.EXTRACT_RESPONSE:
                context[action.name] = "extracted-preview"
            elif action_type(action) == ActionType.PYTHON_SCRIPT:
                try:
                    run_safe_script(action.script, context)
                except ResourceConflictError as exc:
                    raise RunExecutionUnsupportedError(
                        "API Python Script Runtime Context 无法解析"
                    ) from exc
    except RunExecutionUnsupportedError:
        issues: list[RunValidationIssue] = []
        _issue(
            issues,
            "API_RUNTIME_TEMPLATE_UNSUPPORTED",
            "API Runtime Context 引用缺失、敏感、动态或格式无效",
            "request",
        )
        return issues
    return []


def _contains_sensitive_request_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if _is_sensitive_request_name(str(key)):
                return True
            if (
                normalized in {"name", "key", "field", "parameter"}
                and isinstance(child, str)
                and _is_sensitive_request_name(child)
            ):
                return True
            if _contains_sensitive_request_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_request_key(child) for child in value)
    elif isinstance(value, str):
        return _SENSITIVE_TEXT.search(value) is not None
    return False


_API_UNSUPPORTED_CONTENT_KEYS = frozenset(
    {
        "browser",
        "browser_name",
        "browser_version",
        "required_file",
        "required_files",
        "file_path",
        "model_id",
        "model_name",
        "ai_model",
        "ai_model_id",
    }
)


def _api_execution_support_issues(
    content: SuggestedCase,
    *,
    raw_content: Any | None = None,
    runtime_names: set[str] | None = None,
) -> list[RunValidationIssue]:
    """Return the single source of truth for the currently supported API executor."""

    issues: list[RunValidationIssue] = []
    if isinstance(raw_content, dict):
        unsupported_keys = {
            re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_") for key in raw_content
        }.intersection(_API_UNSUPPORTED_CONTENT_KEYS)
        if unsupported_keys:
            _issue(
                issues,
                "EXECUTION_FEATURE_UNSUPPORTED",
                "当前 API_CASE 使用了尚不支持的 Browser、文件或 AI 执行能力",
                "case_version",
            )

    if content.case_type != CaseType.API:
        _issue(
            issues,
            "UNSUPPORTED_CASE_TYPE",
            "当前 API_CASE Executor 仅支持 API Case，Browser/Web Case 暂不支持",
            "case_type",
        )
    if content.request is None:
        _issue(issues, "API_REQUEST_MISSING", "API Case 缺少可验证的请求模板", "request")
        return issues

    request = content.request
    auth = request.auth
    token_runtime_names = set(runtime_names or {}) | {
        action.name
        for action in content.pre_actions
        if action.enabled
        and (
            (isinstance(action, GetTokenAction | ApiSetupAction) and action.request is not None)
            or action_type(action) == ActionType.FAKER
        )
    }
    auth_invalid = False
    if auth.type == AuthType.NONE:
        auth_invalid = (
            any(
                value is not None
                for value in (
                    auth.token,
                    auth.username,
                    auth.password,
                    auth.key_name,
                    auth.key_value,
                )
            )
            or auth.placement.value != "HEADER"
        )
    elif auth.type == AuthType.BEARER:
        auth_invalid = (
            not _api_sensitive_value_is_reference(auth.token, token_runtime_names)
            or any(
                value is not None
                for value in (auth.username, auth.password, auth.key_name, auth.key_value)
            )
            or auth.placement.value != "HEADER"
        )
    elif auth.type == AuthType.BASIC:
        auth_invalid = (
            not auth.username
            or ":" in auth.username
            or _contains_runtime_template(auth.username)
            or not _api_sensitive_value_is_reference(auth.password, token_runtime_names)
            or any(value is not None for value in (auth.token, auth.key_name, auth.key_value))
            or auth.placement.value != "HEADER"
        )
    elif auth.type == AuthType.API_KEY:
        auth_invalid = (
            not auth.key_name
            or _contains_runtime_template(auth.key_name)
            or not _api_sensitive_value_is_reference(auth.key_value, token_runtime_names)
            or any(value is not None for value in (auth.token, auth.username, auth.password))
        )
    else:
        auth_invalid = True
    if auth_invalid:
        _issue(
            issues,
            "API_AUTH_UNSUPPORTED",
            "目标 API 认证字段组合无效，凭据必须使用 Secret 或受控 Runtime 引用",
            "request.auth",
        )
    if any(
        item.enabled and not _api_sensitive_value_is_reference(item.value, token_runtime_names)
        for item in request.cookies
    ):
        _issue(
            issues,
            "API_COOKIE_UNSUPPORTED",
            "正式 API_CASE 的 Cookie 值必须使用 Secret 或受控 Runtime 引用",
            "request.cookies",
        )
    if any(
        item.enabled
        and _is_sensitive_request_name(item.name)
        and not _api_sensitive_value_is_reference(item.value, token_runtime_names)
        for item in [*request.headers, *request.query_params]
    ):
        _issue(
            issues,
            "API_SENSITIVE_FIELD_UNSUPPORTED",
            "敏感 Header 或 Query 值必须使用 Secret 或受控 Runtime 引用",
            "request.headers",
        )
    header_names = {item.name.lower() for item in request.headers if item.enabled}
    query_names = {item.name.lower() for item in request.query_params if item.enabled}
    if (auth.type in {AuthType.BEARER, AuthType.BASIC} and "authorization" in header_names) or (
        auth.type == AuthType.API_KEY
        and auth.key_name is not None
        and auth.key_name.lower()
        in (header_names if auth.placement.value == "HEADER" else query_names)
    ):
        _issue(
            issues,
            "API_AUTH_CONFLICT",
            "目标 API 认证与显式 Header/Query 名称冲突",
            "request.auth",
        )
    if (
        _contains_unsafe_sensitive_request_value(request.body.content, token_runtime_names)
        or _SENSITIVE_TEXT.search(request.url)
        or any(
            _SENSITIVE_TEXT.search(item.value)
            and not _api_sensitive_value_is_reference(item.value, token_runtime_names)
            for item in request.headers
        )
        or any(
            _SENSITIVE_TEXT.search(item.value)
            and not _api_sensitive_value_is_reference(item.value, token_runtime_names)
            for item in request.query_params
        )
    ):
        _issue(
            issues,
            "API_SENSITIVE_FIELD_UNSUPPORTED",
            "当前 API_CASE Executor 不支持敏感请求字段",
            "request",
        )
    supported_pre_actions = {
        ActionType.SET_VARIABLE,
        ActionType.FAKER,
        ActionType.PYTHON_SCRIPT,
        ActionType.SQL_QUERY,
        ActionType.GET_TOKEN,
        ActionType.API_SETUP,
    }
    if any(action_type(action) not in supported_pre_actions for action in content.pre_actions):
        _issue(
            issues,
            "API_PRE_ACTION_UNSUPPORTED",
            "当前 API_CASE Runner 仅支持 SET_VARIABLE、FAKER、PYTHON_SCRIPT 与 "
            "SQL_QUERY、GET_TOKEN 与 API_SETUP Pre Action",
            "pre_actions",
        )
    pre_action_runtime_names = set(runtime_names or {})
    for action in content.pre_actions:
        action_payload = action.model_dump(mode="json")
        if isinstance(action, GetTokenAction | ApiSetupAction):
            action_payload.pop("response", None)
            action_payload.pop("request", None)
        try:
            validate_secret_free_payload(action_payload)
        except (TypeError, ValueError):
            _issue(
                issues,
                "API_PRE_ACTION_SENSITIVE",
                "API Pre Action 不能携带明文敏感数据",
                "pre_actions",
            )
            break
        if action_type(action) == ActionType.PYTHON_SCRIPT:
            try:
                validate_safe_script(action.script)
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "API_SCRIPT_INVALID",
                    "API Python Script 不符合受限 AST 执行规则",
                    "pre_actions",
                )
                break
        if action_type(action) == ActionType.SQL_QUERY:
            try:
                validate_sql_query(action.sql)
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "API_SQL_QUERY_INVALID",
                    "API SQL_QUERY 只允许有界只读查询",
                    "pre_actions",
                )
                break
        if isinstance(action, GetTokenAction | ApiSetupAction):
            if action.request is None:
                _issue(
                    issues,
                    "API_TOKEN_REQUEST_MISSING",
                    "正式 API_CASE 的 API 前置动作必须配置真实请求",
                    "pre_actions",
                )
                break
            token_request = action.request
            if token_request.retry_policy.max_retries > 0:
                _issue(
                    issues,
                    "API_TOKEN_RETRY_UNSUPPORTED",
                    "API 前置请求不能叠加普通请求重试",
                    "pre_actions",
                )
                break
            if not _api_request_credentials_valid(token_request, pre_action_runtime_names):
                _issue(
                    issues,
                    "API_TOKEN_REQUEST_SENSITIVE",
                    "API 前置请求的认证、Cookie 与敏感字段必须使用 Secret "
                    "或更早动作的受控 Runtime 引用",
                    "pre_actions",
                )
                break
        if action.enabled and (
            isinstance(action, GetTokenAction | ApiSetupAction)
            or action_type(action) == ActionType.FAKER
        ):
            pre_action_runtime_names.add(action.name)
    supported_post_actions = {
        ActionType.SET_VARIABLE,
        ActionType.PYTHON_SCRIPT,
        ActionType.EXTRACT_RESPONSE,
        ActionType.REGISTER_RESOURCE,
    }
    if any(action_type(action) not in supported_post_actions for action in content.post_actions):
        _issue(
            issues,
            "API_POST_ACTION_UNSUPPORTED",
            "当前 API_CASE Runner 仅支持 SET_VARIABLE、PYTHON_SCRIPT 与 "
            "EXTRACT_RESPONSE 与 REGISTER_RESOURCE Post Action",
            "post_actions",
        )
    for action in content.post_actions:
        try:
            validate_secret_free_payload(action.model_dump(mode="json"))
        except (TypeError, ValueError):
            _issue(
                issues,
                "API_POST_ACTION_SENSITIVE",
                "API Post Action 不能携带明文敏感数据",
                "post_actions",
            )
            break
        if action_type(action) == ActionType.PYTHON_SCRIPT:
            try:
                validate_safe_script(action.script)
            except (ResourceConflictError, TypeError, ValueError):
                _issue(
                    issues,
                    "API_SCRIPT_INVALID",
                    "API Python Script 不符合受限 AST 执行规则",
                    "post_actions",
                )
                break
        if action_type(action) == ActionType.REGISTER_RESOURCE and (
            action.cleanup is None and action.cleanup_ref is None
        ):
            _issue(
                issues,
                "API_RESOURCE_CLEANUP_MISSING",
                "正式 API_CASE 的 REGISTER_RESOURCE 必须绑定 Cleanup",
                "post_actions",
            )
            break
    for extractor in content.extractors:
        try:
            validate_secret_free_payload(extractor.model_dump(mode="json"))
        except (TypeError, ValueError):
            _issue(
                issues,
                "API_EXTRACTOR_SENSITIVE",
                "API Response Extractor 不能携带明文敏感默认值",
                "extractors",
            )
            break
    return issues


def _resolved_api_request(
    session: Session,
    run: TestRun,
    request: ApiRequestTemplate,
    *,
    resolve_runtime: bool = True,
    runtime_context: dict[str, Any] | None = None,
    allow_deferred_json_body: bool = False,
) -> ApiRequestTemplate:
    payload = request.model_dump(mode="json")

    def resolve_references(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: resolve_references(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve_references(child) for child in value]
        if _api_secret_name(value) is None:
            return value
        secret = _api_secret_for_run(session, run.project_id, run.environment_id, value)
        try:
            return resolve_secret(session, secret.id, run.project_id)
        except Exception as exc:
            raise RunExecutionUnsupportedError("API 请求 Secret 暂时无法安全解析") from exc

    payload = resolve_references(payload)
    if resolve_runtime:
        payload = _resolve_api_runtime_value(
            payload,
            runtime_context
            if runtime_context is not None
            else _api_runtime_context(session, run.project_id, run.environment_id),
        )
    try:
        return (
            _scenario_http_template(payload)
            if allow_deferred_json_body
            else ApiRequestTemplate.model_validate(payload)
        )
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("API 请求无法生成受控执行计划") from exc


def _api_case_run_context(
    session: Session,
    run: TestRun,
    case_run: CaseRun,
    content: SuggestedCase,
) -> dict[str, Any]:
    context = _api_runtime_context(session, run.project_id, run.environment_id)
    context.update(run.runtime_variables or {})
    if case_run.dataset_version_id is None:
        return context
    data_source = content.data_source
    if (
        data_source is None
        or case_run.dataset_id != data_source.dataset_id
        or case_run.row_index is None
    ):
        raise RunExecutionUnsupportedError("CaseRun Data Source 身份与固定 Case Version 不匹配")
    version = session.get(DatasetVersion, case_run.dataset_version_id)
    if version is None or version.dataset_id != case_run.dataset_id:
        raise RunExecutionUnsupportedError("CaseRun 固定 Data Source Version 不存在")
    columns = [str(item["name"]) for item in version.columns]
    try:
        mapping = validate_iteration_mapping(
            columns, data_source.column_mapping, data_source.prefix
        )
        row = version.actual_data[case_run.row_index - 1]
        if row.get("row_index") != case_run.row_index or not isinstance(row.get("data"), dict):
            raise ValueError("row identity mismatch")
        values = row["data"]
        context.update({mapping[column]: values[column] for column in columns})
    except (IndexError, KeyError, ResourceConflictError, TypeError, ValueError) as exc:
        raise RunExecutionUnsupportedError("CaseRun Data Source 行快照无法读取") from exc
    context.update(
        {
            "dataset_id": case_run.dataset_id,
            "dataset_version_id": case_run.dataset_version_id,
            "row_index": case_run.row_index,
        }
    )
    return context


def _execution_context(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int | None,
) -> _ApiExecutionContext:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    runner = _authenticate_runner_for_run(session, run, credential)
    outbox = get_dispatch_outbox_for_run(session, run.id, for_update=True)
    if (
        outbox is None
        or outbox.message_id != message_id
        or outbox.status != DispatchOutboxStatus.PUBLISHED.value
        or outbox.claimed_runner_id != runner.id
        or outbox.claimed_at is None
    ):
        raise RunStateConflictError("Run 尚未由当前 Runner 认领")
    case_statement = select(CaseRun).where(CaseRun.run_id == run.id)
    if case_run_id is not None:
        case_statement = case_statement.where(CaseRun.id == case_run_id)
    case_runs = list(
        session.scalars(case_statement.order_by(CaseRun.sequence_no.asc()).with_for_update()).all()
    )
    if case_run_id is None and run.run_type == RunType.API_CASE.value:
        case_runs = [
            item for item in case_runs if RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES
        ][:1]
    if len(case_runs) != 1:
        raise ResourceNotFoundError("CaseRun 不存在")
    return _ApiExecutionContext(run, runner, outbox, case_runs[0])


def _load_supported_api_case(
    session: Session,
    run: TestRun,
    *,
    enforce_v1_retry_limit: bool = True,
) -> tuple[TestCaseVersion, SuggestedCase]:
    if run.run_type != RunType.API_CASE.value or run.case_id is None or run.case_version_id is None:
        _raise_execution_unsupported()
    version = session.get(TestCaseVersion, run.case_version_id)
    if version is None or version.case_id != run.case_id:
        _raise_execution_unsupported()
    try:
        content = SuggestedCase.model_validate(version.content)
    except ValidationError as exc:
        raise RunExecutionUnsupportedError() from exc
    if enforce_v1_retry_limit and content.case_type == CaseType.API and content.request is not None:
        ensure_v1_api_step_retry_limit(content.request.retry_policy.max_retries)
    runtime_names: set[str] = set()
    if content.data_source is not None:
        try:
            iterations = _dataset_iterations(session, run.project_id, content.data_source)
            runtime_names = set(iterations[0]["context"])
        except (ResourceConflictError, TypeError, ValueError) as exc:
            raise RunExecutionUnsupportedError("固定 Data Source 无法读取") from exc
    if _api_execution_support_issues(
        content,
        raw_content=version.content,
        runtime_names=runtime_names,
    ):
        _raise_execution_unsupported()
    try:
        from app.modules.test_cases.service import _validate_assertions

        _validate_assertions(session, run.project_id, content)
    except (ResourceConflictError, ValueError, TypeError) as exc:
        raise RunExecutionUnsupportedError("AI 断言引用无法解析") from exc
    return version, content


def _execution_plan_response(
    session: Session,
    context: _ApiExecutionContext,
    version: TestCaseVersion,
    content: SuggestedCase,
) -> ExecutionPlanResponse:
    if content.request is None:
        _raise_execution_unsupported()
    runtime_context = _api_case_run_context(session, context.run, context.case_run, content)
    connections: dict[int, ScenarioSqlConnectionPlan] = {}
    secrets: dict[int, ScenarioExecutionSecret] = {}
    for action in content.pre_actions:
        if isinstance(action, SqlQueryAction) and action.enabled:
            _scenario_sql_connection(
                session,
                context.run,
                action.connection_id,
                connections,
                secrets,
            )
    cleanup_configs = [*content.cleanup]
    cleanup_configs.extend(
        action.cleanup
        for action in content.post_actions
        if action_type(action) == ActionType.REGISTER_RESOURCE and action.cleanup is not None
    )
    for cleanup in cleanup_configs:
        if not cleanup.enabled:
            continue
        if cleanup.cleanup_type.value == "SQL" and cleanup.connection_id is not None:
            _scenario_sql_connection(
                session,
                context.run,
                cleanup.connection_id,
                connections,
                secrets,
            )
        if cleanup.auth.secret_id is not None:
            _append_scenario_secret(
                session,
                context.run.project_id,
                cleanup.auth.secret_id,
                secrets,
            )
    pre_actions: list[dict[str, Any]] = []
    for action in content.pre_actions:
        action_payload = action.model_dump(mode="json")
        if isinstance(action, GetTokenAction | ApiSetupAction):
            action_payload.pop("response", None)
            if action.request is not None:
                action_payload["request"] = _resolved_api_request(
                    session,
                    context.run,
                    action.request,
                    resolve_runtime=False,
                ).model_dump(mode="json")
        pre_actions.append(action_payload)
    return ExecutionPlanResponse(
        schema_version=1,
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        case_version_id=version.id,
        request=_resolved_api_request(
            session,
            context.run,
            content.request,
            resolve_runtime=not content.pre_actions,
            runtime_context=runtime_context,
        ),
        total_timeout_ms=_persisted_total_timeout_ms(context.run),
        initial_context=(
            runtime_context
            if content.pre_actions or content.extractors or content.post_actions
            else {}
        ),
        pre_actions=pre_actions,
        extractors=[item.model_dump(mode="json") for item in content.extractors],
        post_actions=[action.model_dump(mode="json") for action in content.post_actions],
        sql_connections=list(connections.values()),
        secrets=list(secrets.values()),
        cleanups=[item.model_dump(mode="json") for item in content.cleanup],
        defer_cleanup_until_assertions=bool(content.cleanup and content.assertions),
    )


def get_execution_plan(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int | None = None,
) -> ExecutionPlanResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    if RunStatus(context.run.status) not in {
        RunStatus.ASSIGNED,
        RunStatus.RUNNING,
        RunStatus.CANCELLING,
    }:
        raise RunStateConflictError("只有 ASSIGNED/RUNNING/CANCELLING Run 可以读取执行计划")
    version, content = _load_supported_api_case(
        session,
        context.run,
        enforce_v1_retry_limit=RunStatus(context.run.status) == RunStatus.ASSIGNED,
    )
    return _execution_plan_response(session, context, version, content)


def _load_supported_web_case(
    session: Session, run: TestRun
) -> tuple[WebCaseVersion, WebCaseContent]:
    if (
        run.run_type != RunType.WEB_CASE.value
        or run.web_case_id is None
        or run.web_case_version_id is None
    ):
        raise RunExecutionUnsupportedError("当前 Run 不是可执行的 Web Case")
    web_case = session.get(WebCase, run.web_case_id)
    version = session.get(WebCaseVersion, run.web_case_version_id)
    if (
        web_case is None
        or version is None
        or version.web_case_id != web_case.id
        or version.status != "APPROVED"
    ):
        raise RunExecutionUnsupportedError("固定 Web Case Version 不存在、未批准或已漂移")
    try:
        content = WebCaseContent.model_validate(version.content)
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("固定 Web Case DSL 无法按当前协议解析") from exc
    issues: list[RunValidationIssue] = []
    _validate_web_locator_references(session, run.project_id, content, issues)
    if issues:
        raise RunExecutionUnsupportedError("固定 Web Case Locator 不满足当前执行边界")
    if content.session_profile_id is not None:
        profile = session.get(SessionProfile, content.session_profile_id)
        if (
            profile is None
            or profile.project_id != run.project_id
            or profile.environment_id not in {None, run.environment_id}
            or profile.status != "ACTIVE"
        ):
            raise RunExecutionUnsupportedError("固定 Web Case Session Profile 不可用")
        recovery = _web_session_recovery_binding(
            session, run.project_id, run.environment_id, profile
        )
        if (
            profile.expires_at is not None
            and profile.expires_at <= utc_now_naive()
            and recovery is None
        ):
            raise RunExecutionUnsupportedError("固定 Web Case Session Profile 已过期且无法恢复")
    return version, content


def _web_session_recovery_binding(
    session: Session,
    project_id: int,
    environment_id: int | None,
    profile: SessionProfile,
) -> (
    tuple[
        WebCase,
        WebCaseVersion,
        WebCaseContent,
        SessionRecoveryCondition,
        SessionRecoveryCondition,
    ]
    | None
):
    raw = (
        profile.login_web_case_id,
        profile.login_web_case_version_id,
        profile.expiry_condition,
        profile.success_condition,
    )
    if all(item is None for item in raw):
        return None
    if any(item is None for item in raw):
        raise RunExecutionUnsupportedError("Session Profile 登录恢复配置不完整")
    login_case = session.get(WebCase, profile.login_web_case_id)
    login_version = session.get(WebCaseVersion, profile.login_web_case_version_id)
    if (
        login_case is None
        or login_case.project_id != project_id
        or login_case.status == "ARCHIVED"
        or login_version is None
        or login_version.web_case_id != login_case.id
        or login_version.status != "APPROVED"
    ):
        raise RunExecutionUnsupportedError("Session Profile 登录恢复版本不可用")
    try:
        login_content = WebCaseContent.model_validate(login_version.content)
        expiry = SessionRecoveryCondition.model_validate(profile.expiry_condition)
        success = SessionRecoveryCondition.model_validate(profile.success_condition)
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("Session Profile 登录恢复协议无效") from exc
    if login_content.session_profile_id is not None:
        raise RunExecutionUnsupportedError("Session Profile 登录恢复不能递归引用会话")
    if any(item.type == "ASSERT_AI_SEMANTIC" for item in login_content.assertions):
        raise RunExecutionUnsupportedError("Session Profile 登录恢复不支持 AI 断言")
    issues: list[RunValidationIssue] = []
    _validate_web_locator_references(session, project_id, login_content, issues)
    for condition in (expiry, success):
        if condition.locator is not None:
            probe = WebCaseContent(
                start_url=login_content.start_url,
                actions=[
                    {
                        "type": "WAIT_ELEMENT",
                        "locator": condition.locator.model_dump(mode="json"),
                        "timeout_ms": 100,
                        "failure_policy": "STOP",
                    }
                ],
            )
            _validate_web_locator_references(session, project_id, probe, issues)
    if issues:
        raise RunExecutionUnsupportedError("Session Profile 登录恢复 Locator 不可用")
    _web_secret_values_for_contents(
        session, project_id, environment_id, (login_content,), resolve=False
    )
    return login_case, login_version, login_content, expiry, success


def _web_secret_values_for_contents(
    session: Session,
    project_id: int,
    environment_id: int | None,
    contents: tuple[WebCaseContent, ...],
    *,
    resolve: bool,
) -> list[WebExecutionSecret]:
    names: set[str] = set()
    for content in contents:
        for action in content.actions:
            value = getattr(action, "value", None)
            match = _API_SECRET_TEMPLATE.fullmatch(value) if isinstance(value, str) else None
            if match:
                names.add(match.group(1))
        proxy = content.browser_config.proxy
        if proxy is not None:
            for value in (proxy.username, proxy.password):
                match = _API_SECRET_TEMPLATE.fullmatch(value) if isinstance(value, str) else None
                if match:
                    names.add(match.group(1))
    result: list[WebExecutionSecret] = []
    for name in sorted(names):
        secret = session.scalar(
            select(Secret).where(
                Secret.project_id == project_id,
                Secret.name == name,
                Secret.enabled.is_(True),
            )
        )
        if secret is None or secret.environment_id not in {None, environment_id}:
            raise RunExecutionUnsupportedError(
                f"Web Case 引用的托管 Secret“{name}”不存在、已停用或与运行环境不匹配"
            )
        if resolve:
            try:
                result.append(
                    WebExecutionSecret(
                        name=name,
                        value=resolve_secret(session, secret.id, project_id),
                    )
                )
            except (ResourceConflictError, SecurityConfigurationError) as exc:
                raise RunExecutionUnsupportedError("Web 登录流程 Secret 无法安全解析") from exc
    return result


def _web_locator_plan(session: Session, locator: Any) -> WebLocatorPlan:
    if locator.element_version_id is not None:
        version = session.get(WebElementVersion, locator.element_version_id)
        if version is None or not version.locators:
            raise RunExecutionUnsupportedError("Web Element Version Locator 不可用")
        candidates = [
            WebLocatorCandidate(
                strategy=item.strategy,
                value=item.value,
                priority=item.priority,
            )
            for item in sorted(version.locators, key=lambda item: (item.priority, item.id))
        ]
        return WebLocatorPlan(
            element_version_id=version.id,
            candidates=candidates,
        )
    return WebLocatorPlan(
        candidates=[
            WebLocatorCandidate(
                strategy=locator.strategy.value,
                value=locator.value,
                priority=1,
            )
        ]
    )


def _web_context_for_plan(
    session: Session, run: TestRun, content: WebCaseContent
) -> dict[str, Any]:
    references = set(
        re.findall(
            r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}", json.dumps(content.model_dump(mode="json"))
        )
    )
    environment = session.get(Environment, run.environment_id) if run.environment_id else None
    raw_context = {}
    if references and environment is not None and environment.project_id == run.project_id:
        try:
            raw_context = build_environment_context(session, environment.id)
        except (ResourceConflictError, TypeError, ValueError) as exc:
            raise RunExecutionUnsupportedError(
                "Web Environment Runtime Context 不可安全读取"
            ) from exc
    context: dict[str, Any] = {
        "run_id": run.id,
        "web_case_id": run.web_case_id,
        "project_id": run.project_id,
        "environment_id": run.environment_id,
    }
    if "base_url" in references and environment is not None and environment.base_url:
        context["base_url"] = environment.base_url
    for name in references:
        if name in raw_context and not _is_sensitive_request_name(name):
            context[name] = raw_context[name]
        if name in (run.runtime_variables or {}) and not _is_sensitive_request_name(name):
            context[name] = run.runtime_variables[name]
    context.update(
        {
            "run_id": run.id,
            "web_case_id": run.web_case_id,
            "project_id": run.project_id,
            "environment_id": run.environment_id,
        }
    )
    try:
        validate_secret_free_payload(context)
    except (TypeError, ValueError) as exc:
        raise RunExecutionUnsupportedError("Web Runtime Context 不满足安全下发边界") from exc
    return context


def _web_execution_plan_response(
    session: Session,
    context: _ApiExecutionContext,
    version: WebCaseVersion,
    content: WebCaseContent,
) -> WebExecutionPlanResponse:
    validation_override: tuple[str, WebLocatorPlan] | None = None
    validation = session.scalar(
        select(WebHealingValidation).where(WebHealingValidation.run_id == context.run.id)
    )
    if validation is not None:
        proposal = session.get(WebHealingProposal, validation.proposal_id)
        if (
            proposal is None
            or proposal.project_id != context.run.project_id
            or proposal.web_case_id != context.run.web_case_id
            or proposal.web_case_version_id != version.id
        ):
            raise RunExecutionUnsupportedError("Healing 候选验证绑定无效")
        try:
            healing_locator = HealingLocator.model_validate(validation.locator)
        except (ValidationError, TypeError, ValueError) as exc:
            raise RunExecutionUnsupportedError("Healing 候选验证 Locator 无效") from exc
        validation_override = (
            proposal.node_id,
            WebLocatorPlan(
                candidates=[
                    WebLocatorCandidate(
                        strategy=healing_locator.strategy,
                        value=healing_locator.value,
                        priority=1,
                    )
                ]
            ),
        )

    def action_plans(
        source: WebCaseContent, *, apply_validation_override: bool = False
    ) -> list[WebExecutionActionPlan]:
        result: list[WebExecutionActionPlan] = []
        for index, action in enumerate(source.actions, start=1):
            locator = getattr(action, "locator", None)
            planned_locator = _web_locator_plan(session, locator) if locator is not None else None
            if (
                apply_validation_override
                and validation_override is not None
                and validation_override[0] == f"action_{index}"
            ):
                planned_locator = validation_override[1]
            result.append(
                WebExecutionActionPlan(
                    type=action.type,
                    timeout_ms=action.timeout_ms,
                    failure_policy=action.failure_policy.value,
                    url=getattr(action, "url", None),
                    locator=planned_locator,
                    value=getattr(action, "value", None),
                    key=getattr(action, "key", None),
                )
            )
        return result

    def assertion_plans(
        source: WebCaseContent, *, apply_validation_override: bool = False
    ) -> list[WebExecutionAssertionPlan]:
        result: list[WebExecutionAssertionPlan] = []
        for index, assertion in enumerate(source.assertions, start=1):
            locator = getattr(assertion, "locator", None)
            planned_locator = _web_locator_plan(session, locator) if locator is not None else None
            if (
                apply_validation_override
                and validation_override is not None
                and validation_override[0] == f"assertion_{index}"
            ):
                planned_locator = validation_override[1]
            result.append(
                WebExecutionAssertionPlan(
                    type=assertion.type,
                    timeout_ms=assertion.timeout_ms,
                    locator=planned_locator,
                    expected=getattr(assertion, "expected", None),
                    key=getattr(assertion, "key", None),
                    prompt_id=getattr(assertion, "prompt_id", None),
                    criteria=getattr(assertion, "criteria", None),
                    confidence_threshold=getattr(assertion, "confidence_threshold", None),
                )
            )
        return result

    def condition_plan(condition: SessionRecoveryCondition) -> WebSessionConditionPlan:
        return WebSessionConditionPlan(
            type=condition.type,
            value=condition.value,
            locator=(
                _web_locator_plan(session, condition.locator)
                if condition.locator is not None
                else None
            ),
        )

    actions = action_plans(content, apply_validation_override=True)
    assertions = assertion_plans(content, apply_validation_override=True)
    session_plan: WebSessionPlan | None = None
    contents = [content]
    if (
        content.session_profile_id is not None
        and RunStatus(context.run.status) != RunStatus.CANCELLING
    ):
        profile = session.get(SessionProfile, content.session_profile_id)
        if profile is None:
            raise RunExecutionUnsupportedError("Web Session Profile 不存在")
        expired = profile.expires_at is not None and profile.expires_at <= utc_now_naive()
        recovery_binding = _web_session_recovery_binding(
            session, context.run.project_id, context.run.environment_id, profile
        )
        state: dict[str, Any] | None = None
        if not expired:
            try:
                loaded_state = json.loads(
                    decrypt_secret(profile.storage_state_ciphertext, get_settings().secret_key)
                )
            except (SecurityConfigurationError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RunExecutionUnsupportedError("Web Session Profile 无法安全读取") from exc
            if not isinstance(loaded_state, dict):
                raise RunExecutionUnsupportedError("Web Session Profile Storage State 格式无效")
            state = loaded_state
        recovery_plan: WebSessionRecoveryPlan | None = None
        if recovery_binding is not None:
            login_case, login_version, login_content, expiry, success = recovery_binding
            contents.append(login_content)
            recovery_plan = WebSessionRecoveryPlan(
                login_web_case_id=login_case.id,
                login_web_case_version_id=login_version.id,
                force_refresh=expired,
                expiry_condition=condition_plan(expiry),
                success_condition=condition_plan(success),
                start_url=login_content.start_url,
                actions=action_plans(login_content),
                assertions=assertion_plans(login_content),
            )
        session_plan = WebSessionPlan(
            profile_id=profile.id,
            revision=profile.revision,
            storage_state_fingerprint=profile.storage_state_fingerprint,
            storage_state=state,
            recovery=recovery_plan,
        )
    secrets = _web_secret_values_for_contents(
        session,
        context.run.project_id,
        context.run.environment_id,
        tuple(contents),
        resolve=True,
    )
    proxy = content.browser_config.proxy
    return WebExecutionPlanResponse(
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        web_case_id=context.run.web_case_id or 0,
        web_case_version_id=version.id,
        start_url=content.start_url,
        browser=content.browser,
        headless=content.headless,
        browser_config=WebExecutionBrowserConfig(
            window_width=content.browser_config.window_width,
            window_height=content.browser_config.window_height,
            language=content.browser_config.language,
            user_agent=content.browser_config.user_agent,
            proxy=(
                WebExecutionProxyConfig(
                    server=proxy.server,
                    username=proxy.username,
                    password=proxy.password,
                )
                if proxy is not None
                else None
            ),
            download_path=content.browser_config.download_path,
        ),
        total_timeout_ms=_persisted_total_timeout_ms(context.run),
        initial_context=_web_context_for_plan(session, context.run, content),
        actions=actions,
        assertions=assertions,
        session=session_plan,
        secrets=secrets,
    )


def get_web_execution_plan(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int | None = None,
) -> WebExecutionPlanResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    if RunStatus(context.run.status) not in {
        RunStatus.ASSIGNED,
        RunStatus.RUNNING,
        RunStatus.CANCELLING,
    }:
        raise RunStateConflictError("只有 ASSIGNED/RUNNING/CANCELLING Run 可以读取 Web 执行计划")
    version, content = _load_supported_web_case(session, context.run)
    return _web_execution_plan_response(session, context, version, content)


def start_web_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ExecutionStartRequest,
    event_stream: RedisRunEventStream,
) -> WebExecutionStartResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    run_status = RunStatus(context.run.status)
    if run_status == RunStatus.CANCELLING:
        return WebExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.CANCELLING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    _load_supported_web_case(session, context.run)
    case_status = RunNodeStatus(context.case_run.status)
    if (
        run_status == RunStatus.RUNNING
        and case_status == RunNodeStatus.RUNNING
        and context.run.started_at is not None
    ):
        return WebExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.RUNNING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    if run_status == RunStatus.RUNNING:
        raise RunStateConflictError("Run 与 CaseRun 状态不一致，不能幂等开始 Web_CASE 执行")
    if run_status != RunStatus.ASSIGNED:
        raise RunStateConflictError("只有 ASSIGNED Run 可以开始 Web_CASE 执行")
    if case_status not in {RunNodeStatus.CREATED, RunNodeStatus.ASSIGNED}:
        raise RunStateConflictError("CaseRun 状态不允许开始 Web_CASE 执行")
    status_payload = NodeStatusUpdateRequest(status=RunNodeStatus.RUNNING)
    for step_run in context.case_run.step_runs:
        if RunNodeStatus(step_run.status) in TERMINAL_NODE_STATUSES:
            raise RunStateConflictError("Web Case StepRun 已结束，不能开始执行")
        _set_node_status(step_run, RunNodeStatus.RUNNING, status_payload)
    _set_node_status(context.case_run, RunNodeStatus.RUNNING, status_payload)
    _set_run_status(
        context.run,
        RunStatus.RUNNING,
        RunStatusUpdateRequest(status=RunStatus.RUNNING),
    )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=RunStatus.ASSIGNED,
        to_status=RunStatus.RUNNING,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return WebExecutionStartResponse(
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        status=RunStatus.RUNNING,
        started_at=to_utc_aware(context.run.started_at),
        idempotent=False,
    )


def _web_execution_response(
    result: RunWebExecutionResult,
    runner_id: str,
    *,
    idempotent: bool,
    case_run_status: RunNodeStatus | None = None,
) -> WebExecutionCompleteResponse:
    return WebExecutionCompleteResponse(
        run_id=result.run_id,
        message_id=result.message_id,
        runner_id=runner_id,
        case_run_id=result.case_run_id,
        outcome=WebExecutionOutcome(result.outcome),
        run_status=RunStatus(result.status),
        case_run_status=case_run_status or RunNodeStatus(result.status),
        traces=[WebExecutionTrace.model_validate(item) for item in result.traces],
        assertion_results=[
            AssertionResult.model_validate(item) for item in result.assertion_results
        ],
        error_type=result.error_type,
        error_message=result.error_message,
        session_recovery=(
            WebSessionRecoveryAudit.model_validate(result.session_recovery)
            if result.session_recovery is not None
            else None
        ),
        completed_at=to_utc_aware(result.completed_at),
        idempotent=idempotent,
    )


def _web_ai_assertion(assertion: Any, node_id: str) -> Assertion:
    try:
        return Assertion(
            kind=AssertionKind.AI_SEMANTIC,
            type="AI_SEMANTIC",
            name=node_id,
            prompt_id=assertion.prompt_id,
            criteria=assertion.criteria,
            confidence_threshold=assertion.confidence_threshold,
        )
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("Web AI 断言配置无效") from exc


def _web_ai_assertions(content: WebCaseContent) -> list[tuple[str, Any]]:
    return [
        (f"assertion_{index}", assertion)
        for index, assertion in enumerate(content.assertions, start=1)
        if assertion.type == "ASSERT_AI_SEMANTIC"
    ]


def _web_ai_response_summary(payload: WebExecutionEvaluationRequest) -> dict[str, Any]:
    return {
        "web_ai": [
            {
                "node_id": item.node_id,
                "response": _execution_response_summary(item.response),
            }
            for item in payload.evaluations
        ]
    }


def _web_evaluation_response(
    context: _ApiExecutionContext,
    payload: WebExecutionEvaluationRequest,
    results: list[AssertionResult],
    token: str,
) -> WebExecutionEvaluationResponse:
    status = summarize_final_status(results)
    return WebExecutionEvaluationResponse(
        run_id=context.run.id,
        message_id=payload.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        assertion_status=status,
        assertion_results=results,
        node_results=[
            {"node_id": item.node_id, "status": result.status.value}
            for item, result in zip(payload.evaluations, results, strict=True)
        ],
        evaluation_token=token,
    )


def evaluate_web_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: WebExecutionEvaluationRequest,
) -> WebExecutionEvaluationResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    if (
        RunStatus(context.run.status) != RunStatus.RUNNING
        or RunNodeStatus(context.case_run.status) != RunNodeStatus.RUNNING
    ):
        raise RunStateConflictError("只有 RUNNING Run/CaseRun 可以评估 Web AI 断言")
    _, content = _load_supported_web_case(session, context.run)
    assertions = _web_ai_assertions(content)
    if not assertions or [node_id for node_id, _ in assertions] != [
        item.node_id for item in payload.evaluations
    ]:
        raise RunStateConflictError("Web AI evaluation 与固定 Web Case 不匹配")
    response_summary = _web_ai_response_summary(payload)
    existing = session.scalar(
        select(RunApiExecutionEvaluation)
        .where(
            RunApiExecutionEvaluation.run_id == context.run.id,
            RunApiExecutionEvaluation.case_run_id == context.case_run.id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.message_id != payload.message_id
            or existing.response_summary != response_summary
        ):
            raise RunStateConflictError("Web AI 断言评估已绑定其他页面快照")
        if (
            existing.status == "PENDING"
            or existing.assertion_results is None
            or existing.evaluation_token is None
        ):
            raise RunStateConflictError("Web AI 断言评估正在进行")
        results = [AssertionResult.model_validate(item) for item in existing.assertion_results]
        return _web_evaluation_response(context, payload, results, existing.evaluation_token)
    reservation = RunApiExecutionEvaluation(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        response_summary=response_summary,
        status="PENDING",
        assertion_results=None,
        evaluation_token=None,
    )
    session.add(reservation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise RunStateConflictError("Web AI 断言评估已由另一请求启动") from exc
    formal_user = CurrentUser(
        id=context.run.created_by,
        username="run-executor",
        display_name="Run Executor",
        roles=["ADMIN"],
    )
    results = [
        run_assertion(
            _web_ai_assertion(assertion, node_id),
            sequence,
            item.response,
            project_id=context.run.project_id,
            session=session,
            user=formal_user,
            commit=True,
        )
        for sequence, ((node_id, assertion), item) in enumerate(
            zip(assertions, payload.evaluations, strict=True), start=1
        )
    ]
    assertion_status = summarize_final_status(results)
    audited = [_execution_assertion_audit(item) for item in results]
    claims = {
        "version": 1,
        "kind": "WEB_AI_ASSERTION",
        "issued_at": int(utc_now_aware().timestamp()),
        "run_id": context.run.id,
        "message_id": payload.message_id,
        "case_run_id": context.case_run.id,
        "web_case_version_id": context.run.web_case_version_id,
        "response_summary": response_summary,
        "assertion_status": assertion_status,
        "assertion_results": audited,
        "node_results": [
            {"node_id": node_id, "status": result.status.value}
            for (node_id, _), result in zip(assertions, results, strict=True)
        ],
    }
    token = _encode_execution_evaluation(claims)
    persisted = session.get(RunApiExecutionEvaluation, reservation.id)
    if persisted is None or persisted.status != "PENDING":
        raise RunStateConflictError("Web AI 断言评估记录不可用")
    persisted.status = assertion_status
    persisted.assertion_results = audited
    persisted.evaluation_token = token
    session.commit()
    return _web_evaluation_response(context, payload, results, token)


def _web_healing_context_audit(context: WebHealingContext) -> dict[str, Any]:
    return {
        "schema_version": context.schema_version,
        "trigger": context.trigger,
        "element_version_id": context.element_version_id,
        "page_url": context.page_url,
        "page_title": context.page_title,
        "dom_candidates": [
            {
                key: value
                for key, value in (
                    ("tag", candidate.tag),
                    ("role", candidate.role),
                    ("id", candidate.id),
                    ("name", candidate.name),
                    ("aria-label", candidate.aria_label),
                    ("placeholder", candidate.placeholder),
                    ("data-testid", candidate.data_testid),
                    ("type", candidate.type),
                    ("title", candidate.title),
                )
                if value is not None
            }
            for candidate in context.dom_candidates
        ],
    }


def _web_trace_audit(trace: WebExecutionTrace) -> dict[str, Any]:
    audited = {
        "node_id": trace.node_id,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "error_type": _safe_error(trace.error_type, maximum=100),
        "error_message": _safe_error(trace.error_message, maximum=500),
        "locator_attempts": [
            {
                "strategy": attempt.strategy,
                "priority": attempt.priority,
                "status": attempt.status,
            }
            for attempt in trace.locator_attempts
        ],
    }
    if trace.healing_context is not None:
        audited["healing_context"] = _web_healing_context_audit(trace.healing_context)
    return audited


def _apply_web_session_refresh(
    session: Session,
    context: _ApiExecutionContext,
    content: WebCaseContent,
    payload: WebExecutionCompleteRequest,
) -> dict[str, Any] | None:
    audit = payload.session_recovery
    refreshed = payload.refreshed_session
    if audit is None and refreshed is None:
        return None
    if content.session_profile_id is None:
        raise RunStateConflictError("当前 Web Case 未绑定 Session Profile")
    profile = session.scalar(
        select(SessionProfile)
        .where(SessionProfile.id == content.session_profile_id)
        .with_for_update()
    )
    if profile is None or profile.project_id != context.run.project_id:
        raise RunStateConflictError("Session Profile 不存在或归属不匹配")
    binding = _web_session_recovery_binding(
        session, context.run.project_id, context.run.environment_id, profile
    )
    if binding is None or binding[1].id != audit.login_web_case_version_id:
        raise RunStateConflictError("Session 恢复审计与固定登录版本不匹配")
    persisted = audit.model_dump(mode="json", exclude={"persistence_status"})
    persisted["persistence_status"] = "NOT_ATTEMPTED"
    if refreshed is None:
        return persisted
    if refreshed.profile_id != profile.id:
        raise RunStateConflictError("刷新回传的 Session Profile 身份不匹配")
    if (
        profile.status != "ACTIVE"
        or profile.revision != refreshed.expected_revision
        or profile.storage_state_fingerprint != refreshed.expected_fingerprint
        or profile.login_web_case_version_id != audit.login_web_case_version_id
    ):
        persisted["persistence_status"] = "CONFLICT"
        return persisted
    encoded = json.dumps(
        refreshed.storage_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        profile.storage_state_ciphertext = encrypt_secret(encoded, get_settings().secret_key)
    except SecurityConfigurationError as exc:
        raise RunStateConflictError("刷新后的 Session Profile 无法安全加密") from exc
    profile.storage_state_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    profile.revision += 1
    profile.expires_at = utc_now_naive() + timedelta(seconds=profile.refresh_ttl_seconds)
    persisted["persistence_status"] = "UPDATED"
    return persisted


def complete_web_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: WebExecutionCompleteRequest,
    event_stream: RedisRunEventStream,
) -> WebExecutionCompleteResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    existing = session.scalar(
        select(RunWebExecutionResult).where(
            RunWebExecutionResult.run_id == run_id,
            RunWebExecutionResult.case_run_id == payload.case_run_id,
        )
    )
    if existing is not None:
        if existing.message_id != payload.message_id:
            raise RunStateConflictError("重复完成的 message_id 不匹配")
        return _web_execution_response(
            existing,
            context.runner.id,
            idempotent=True,
            case_run_status=RunNodeStatus(context.case_run.status),
        )

    _, content = _load_supported_web_case(session, context.run)
    ai_assertions = _web_ai_assertions(content)
    assertion_results: list[AssertionResult] = []
    audited_assertions: list[dict[str, Any]] = []
    run_status = RunStatus(context.run.status)
    case_status = RunNodeStatus(context.case_run.status)
    cancellation_first = run_status in {RunStatus.CANCELLING, RunStatus.CANCELLED}
    effective_outcome = WebExecutionOutcome.CANCELLED if cancellation_first else payload.outcome
    if payload.evaluation_token is not None:
        if effective_outcome in {
            WebExecutionOutcome.CANCELLED,
            WebExecutionOutcome.TIMEOUT,
        }:
            raise RunStateConflictError("取消或超时 Web Run 不能提供 AI evaluation token")
        claims = _decode_execution_evaluation(payload.evaluation_token)
        if (
            claims.get("kind") != "WEB_AI_ASSERTION"
            or claims.get("run_id") != context.run.id
            or claims.get("message_id") != payload.message_id
            or claims.get("case_run_id") != context.case_run.id
            or claims.get("web_case_version_id") != context.run.web_case_version_id
        ):
            raise RunStateConflictError("Web AI evaluation token 与本次执行不匹配")
        raw_audits = claims.get("assertion_results")
        node_results = claims.get("node_results")
        assertion_status = claims.get("assertion_status")
        if (
            assertion_status not in {"PASS", "FAIL", "REVIEW"}
            or not isinstance(raw_audits, list)
            or not isinstance(node_results, list)
            or len(node_results) != len(ai_assertions)
        ):
            raise RunStateConflictError("Web AI evaluation token 内容无效")
        try:
            assertion_results = [AssertionResult.model_validate(item) for item in raw_audits]
        except ValidationError as exc:
            raise RunStateConflictError("Web AI evaluation token 断言结果无效") from exc
        if len(assertion_results) != len(ai_assertions):
            raise RunStateConflictError("Web AI evaluation token 断言数量无效")
        expected_trace_status = {
            "PASS": "SUCCESS",
            "FAIL": "FAILED",
            "REVIEW": "REVIEW",
        }
        trace_by_node_for_ai = {item.node_id: item for item in payload.traces}
        for (node_id, _), node_result in zip(ai_assertions, node_results, strict=True):
            if (
                not isinstance(node_result, dict)
                or node_result.get("node_id") != node_id
                or node_result.get("status") not in expected_trace_status
                or trace_by_node_for_ai.get(node_id) is None
                or trace_by_node_for_ai[node_id].status
                != expected_trace_status[node_result["status"]]
            ):
                raise RunStateConflictError("Web AI evaluation 与 trace 不匹配")
        if assertion_status != "PASS" and effective_outcome != WebExecutionOutcome.FAILED:
            raise RunStateConflictError("Web AI 断言未通过时 outcome 必须为 FAILED")
        audited_assertions = [_execution_assertion_audit(item) for item in assertion_results]
    elif ai_assertions and effective_outcome not in {
        WebExecutionOutcome.CANCELLED,
        WebExecutionOutcome.TIMEOUT,
    }:
        ai_node_ids = {node_id for node_id, _ in ai_assertions}
        if effective_outcome == WebExecutionOutcome.SUCCESS or any(
            trace.node_id in ai_node_ids and trace.status != "SKIPPED" for trace in payload.traces
        ):
            raise RunStateConflictError("Web AI 断言缺少受信评估结果")
    evidence_failure = (
        not cancellation_first
        and payload.outcome == WebExecutionOutcome.FAILED
        and payload.error_type == _WEB_EVIDENCE_ERROR_TYPE
    )
    session_recovery_failure = (
        not cancellation_first
        and payload.outcome == WebExecutionOutcome.FAILED
        and payload.error_type == "WEB_SESSION_RECOVERY_FAILED"
    )
    if session_recovery_failure and (
        payload.session_recovery is None or payload.session_recovery.status != "FAILED"
    ):
        raise RunStateConflictError("Session 恢复失败必须携带匹配的恢复审计")
    if (
        payload.session_recovery is not None
        and payload.session_recovery.status == "FAILED"
        and not session_recovery_failure
    ):
        raise RunStateConflictError("Session 恢复失败审计与 Web outcome 不一致")
    if (
        not cancellation_first
        and payload.error_type == _WEB_EVIDENCE_ERROR_TYPE
        and payload.outcome != WebExecutionOutcome.FAILED
    ):
        raise RunStateConflictError("WEB_EVIDENCE_ERROR 只允许与 FAILED outcome 一起上报")
    if effective_outcome == WebExecutionOutcome.CANCELLED:
        if run_status not in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            raise RunStateConflictError("只有 CANCELLING/CANCELLED Web Run 可以完成取消")
    elif run_status != RunStatus.RUNNING or case_status != RunNodeStatus.RUNNING:
        raise RunStateConflictError("只有 RUNNING Web Run/CaseRun 可以完成执行")

    step_runs = list(
        session.scalars(
            select(StepRun).where(StepRun.case_run_id == context.case_run.id).with_for_update()
        ).all()
    )
    expected_nodes = {item.node_id for item in step_runs}
    trace_by_node = {item.node_id: item for item in payload.traces}
    if effective_outcome != WebExecutionOutcome.CANCELLED and (
        len(payload.traces) != len(expected_nodes) or set(trace_by_node) != expected_nodes
    ):
        raise RunStateConflictError("Web 执行 trace 必须且只能完整覆盖固定 Action/Assertion 节点")

    error_type = (
        _WEB_EVIDENCE_ERROR_TYPE
        if evidence_failure
        else _safe_error(payload.error_type, maximum=100)
    )
    error_message = (
        _WEB_EVIDENCE_ERROR_MESSAGE
        if evidence_failure
        else _safe_error(payload.error_message, maximum=500)
    )
    audited_traces: list[dict[str, Any]] = []
    if effective_outcome == WebExecutionOutcome.CANCELLED:
        error_type = (
            "FORCE_STOP_REQUESTED"
            if context.run.force_stop_requested_at is not None
            else "CANCEL_REQUESTED"
        )
        error_message = "执行已被强制停止" if error_type == "FORCE_STOP_REQUESTED" else "执行已取消"
        status_payload = NodeStatusUpdateRequest(
            status=RunNodeStatus.CANCELLED,
            error_type=error_type,
            error_message=error_message,
        )
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(step_run, RunNodeStatus.CANCELLED, status_payload)
        if RunNodeStatus(context.case_run.status) not in TERMINAL_NODE_STATUSES:
            _set_node_status(context.case_run, RunNodeStatus.CANCELLED, status_payload)
        result_status = RunStatus.CANCELLED
    else:
        status_by_node = {
            node_id: {
                "SUCCESS": RunNodeStatus.SUCCESS,
                "FAILED": RunNodeStatus.FAILED,
                "REVIEW": RunNodeStatus.REVIEW,
                "SKIPPED": RunNodeStatus.SKIPPED,
                "TIMEOUT": RunNodeStatus.TIMEOUT,
                "CANCELLED": RunNodeStatus.CANCELLED,
            }[trace.status]
            for node_id, trace in trace_by_node.items()
        }
        if effective_outcome == WebExecutionOutcome.SUCCESS and any(
            status in {RunNodeStatus.FAILED, RunNodeStatus.TIMEOUT, RunNodeStatus.CANCELLED}
            for status in status_by_node.values()
        ):
            raise RunStateConflictError("Web trace 与 SUCCESS outcome 不一致")
        if (
            effective_outcome == WebExecutionOutcome.FAILED
            and not evidence_failure
            and payload.error_type != "WEB_SESSION_RECOVERY_FAILED"
            and not any(
                status in {RunNodeStatus.FAILED, RunNodeStatus.REVIEW}
                for status in status_by_node.values()
            )
        ):
            raise RunStateConflictError("Web FAILED outcome 必须包含失败节点 trace")
        if effective_outcome == WebExecutionOutcome.TIMEOUT and not any(
            status == RunNodeStatus.TIMEOUT for status in status_by_node.values()
        ):
            raise RunStateConflictError("Web TIMEOUT outcome 必须包含超时节点 trace")
        for step_run in step_runs:
            trace = trace_by_node[step_run.node_id]
            desired = status_by_node[step_run.node_id]
            _set_node_status(
                step_run,
                desired,
                NodeStatusUpdateRequest(
                    status=desired,
                    duration=trace.duration_ms,
                    error_type=_safe_error(trace.error_type, maximum=100),
                    error_message=_safe_error(trace.error_message, maximum=500),
                ),
            )
            audited_traces.append(_web_trace_audit(trace))
        if evidence_failure or session_recovery_failure:
            failure_type = (
                _WEB_EVIDENCE_ERROR_TYPE if evidence_failure else "WEB_SESSION_RECOVERY_FAILED"
            )
            failure_message = (
                _WEB_EVIDENCE_ERROR_MESSAGE if evidence_failure else "Web 登录态恢复失败"
            )
            _set_node_status(
                context.case_run,
                RunNodeStatus.FAILED,
                NodeStatusUpdateRequest(
                    status=RunNodeStatus.FAILED,
                    error_type=failure_type,
                    error_message=failure_message,
                ),
            )
        else:
            _aggregate_case_status(context.case_run)
        result_status = {
            WebExecutionOutcome.SUCCESS: RunStatus.SUCCESS,
            WebExecutionOutcome.FAILED: RunStatus.FAILED,
            WebExecutionOutcome.TIMEOUT: RunStatus.TIMEOUT,
        }[effective_outcome]
        if result_status == RunStatus.SUCCESS:
            error_type = None
            error_message = None
        elif result_status == RunStatus.TIMEOUT:
            error_type = error_type or "WEB_EXECUTION_TIMEOUT"
            error_message = error_message or "Web 执行超时"
        else:
            error_type = error_type or "WEB_EXECUTION_FAILED"
            error_message = error_message or "Web 执行失败"

    recovery_audit = _apply_web_session_refresh(session, context, content, payload)

    _recompute_summary(session, context.run)
    _ensure_terminal_run_matches_children(session, context.run, result_status)
    _set_run_status(
        context.run,
        result_status,
        RunStatusUpdateRequest(
            status=result_status, error_type=error_type, error_message=error_message
        ),
    )
    result = RunWebExecutionResult(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        outcome=result_status.value,
        status=result_status.value,
        traces=audited_traces,
        assertion_results=audited_assertions,
        session_recovery=recovery_audit,
        error_type=error_type,
        error_message=error_message,
        completed_at=utc_now_naive(),
    )
    session.add(result)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(RunWebExecutionResult).where(
                RunWebExecutionResult.run_id == run_id,
                RunWebExecutionResult.case_run_id == payload.case_run_id,
            )
        )
        if existing is not None and existing.message_id == payload.message_id:
            return _web_execution_response(
                existing,
                context.runner.id,
                idempotent=True,
                case_run_status=RunNodeStatus(context.case_run.status),
            )
        raise ResourceConflictError("Web 执行完成结果保存冲突") from exc
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=run_status,
        to_status=result_status,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return _web_execution_response(
        result,
        context.runner.id,
        idempotent=False,
        case_run_status=RunNodeStatus(context.case_run.status),
    )


def upload_web_evidence(
    session: Session,
    run_id: str,
    credential: str,
    *,
    message_id: str,
    case_run_id: int,
    artifact_type: str,
    artifact_name: str,
    supplied_sha256: str,
    content: bytes,
    uploaded_mime: str | None,
    metadata_json: str | None,
    evidence_store: ObjectStore,
) -> WebEvidenceUploadResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    run_status = RunStatus(context.run.status)
    if context.run.run_type != RunType.WEB_CASE.value:
        raise RunExecutionUnsupportedError("只有 WEB_CASE Run 可以上传 Web Evidence")
    if run_status in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        _load_supported_web_case(session, context.run)
        allow_upload = True
    elif run_status in TERMINAL_RUN_STATUSES:
        allow_upload = False
    else:
        raise RunStateConflictError("只有 RUNNING/CANCELLING Web Run 可以上传 Evidence")

    try:
        artifact, idempotent, resolved_name = create_web_evidence(
            session,
            evidence_store,
            project_id=context.run.project_id,
            run_id=context.run.id,
            case_run_id=context.case_run.id,
            message_id=context.outbox.message_id,
            artifact_type=artifact_type,
            artifact_name=artifact_name,
            supplied_sha256=supplied_sha256,
            content=content,
            uploaded_mime=uploaded_mime,
            metadata_json=metadata_json,
            allow_upload=allow_upload,
        )
        if not idempotent:
            session.commit()
    except IntegrityError as exc:
        session.rollback()
        try:
            artifact, idempotent, resolved_name = create_web_evidence(
                session,
                evidence_store,
                project_id=context.run.project_id,
                run_id=context.run.id,
                case_run_id=context.case_run.id,
                message_id=context.outbox.message_id,
                artifact_type=artifact_type,
                artifact_name=artifact_name,
                supplied_sha256=supplied_sha256,
                content=content,
                uploaded_mime=uploaded_mime,
                metadata_json=metadata_json,
                allow_upload=False,
            )
        except (ResourceConflictError, RunExecutionUnsupportedError):
            raise ResourceConflictError("Web Evidence 保存冲突") from exc
        if not idempotent:
            raise ResourceConflictError("Web Evidence 保存冲突") from exc
    return to_web_upload_response(
        artifact,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        artifact_name=resolved_name,
        idempotent=idempotent,
    )


def get_execution_cancellation(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int,
) -> ExecutionCancellationResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    status = RunStatus(context.run.status)
    return ExecutionCancellationResponse(
        schema_version=1,
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        case_run_id=context.case_run.id,
        status=status,
        cancellation_requested=status in {RunStatus.CANCELLING, RunStatus.CANCELLED},
        force_stop_requested=context.run.force_stop_requested_at is not None,
    )


def start_api_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ExecutionStartRequest,
    event_stream: RedisRunEventStream,
) -> ExecutionStartResponse:
    context = _execution_context(
        session,
        run_id,
        credential,
        payload.message_id,
        payload.case_run_id,
    )
    run_status = RunStatus(context.run.status)
    if run_status == RunStatus.CANCELLING:
        return ExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.CANCELLING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    case_status = RunNodeStatus(context.case_run.status)
    if (
        run_status == RunStatus.RUNNING
        and case_status == RunNodeStatus.RUNNING
        and context.run.started_at is not None
    ):
        return ExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.RUNNING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    _load_supported_api_case(session, context.run)
    if run_status not in {RunStatus.ASSIGNED, RunStatus.RUNNING}:
        raise RunStateConflictError("只有 ASSIGNED/RUNNING Run 可以开始 API_CASE 迭代")
    if case_status not in {RunNodeStatus.CREATED, RunNodeStatus.ASSIGNED}:
        raise RunStateConflictError("CaseRun 状态不允许开始 API_CASE 执行")
    status_payload = NodeStatusUpdateRequest(status=RunNodeStatus.RUNNING)
    for step_run in context.case_run.step_runs:
        if RunNodeStatus(step_run.status) in TERMINAL_NODE_STATUSES:
            raise RunStateConflictError("StepRun 已结束，不能开始 API_CASE 执行")
        _set_node_status(step_run, RunNodeStatus.RUNNING, status_payload)
    _set_node_status(context.case_run, RunNodeStatus.RUNNING, status_payload)
    if run_status == RunStatus.ASSIGNED:
        _set_run_status(
            context.run,
            RunStatus.RUNNING,
            RunStatusUpdateRequest(status=RunStatus.RUNNING),
        )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=run_status,
        to_status=RunStatus.RUNNING,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    if context.run.started_at is None:
        raise ResourceConflictError("API_CASE 执行开始时间保存失败")
    return ExecutionStartResponse(
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        status=RunStatus.RUNNING,
        started_at=to_utc_aware(context.run.started_at),
        idempotent=False,
    )


def _execution_complete_response(
    result: RunApiExecutionResult,
    runner_id: str,
    *,
    idempotent: bool,
    run_status: RunStatus | None = None,
) -> ExecutionCompleteResponse:
    return ExecutionCompleteResponse(
        run_id=result.run_id,
        message_id=result.message_id,
        runner_id=runner_id,
        case_run_id=result.case_run_id,
        outcome=ExecutionOutcome(result.outcome),
        run_status=(
            run_status
            or (
                RunStatus.FAILED
                if result.status == RunNodeStatus.REVIEW.value
                else RunStatus(result.status)
            )
        ),
        case_run_status=RunNodeStatus(result.status),
        retry_count=result.retry_count,
        assertion_results=[
            AssertionResult.model_validate(item) for item in result.assertion_results
        ],
        error_type=result.error_type,
        error_message=result.error_message,
        completed_at=to_utc_aware(result.completed_at),
        idempotent=idempotent,
    )


_EXECUTION_ASSERTION_AUDIT_FIELDS = (
    "sequence",
    "name",
    "type",
    "status",
    "message",
    "duration_ms",
    "confidence",
    "reason",
    "ai_call_id",
    "actual_model",
    "fallback_used",
    "repair_used",
)


def _execution_assertion_audit(result: AssertionResult) -> dict[str, Any]:
    status = result.status.value
    safe_messages = {
        AssertionStatus.PASS.value: "断言通过",
        AssertionStatus.FAIL.value: "断言未通过",
        AssertionStatus.REVIEW.value: "断言待复核",
        AssertionStatus.SKIPPED.value: "断言已停用",
    }
    values = {
        "sequence": result.sequence,
        "name": result.name,
        "type": result.type,
        "status": status,
        "message": safe_messages.get(status, "断言结果已记录"),
        "duration_ms": result.duration_ms,
        "confidence": result.confidence,
        "reason": _safe_error(result.reason, maximum=1000),
        "ai_call_id": result.ai_call_id,
        "actual_model": _safe_error(result.actual_model, maximum=255),
        "fallback_used": result.fallback_used,
        "repair_used": result.repair_used,
    }
    return {field: values[field] for field in _EXECUTION_ASSERTION_AUDIT_FIELDS}


def _execution_response_body_bytes(
    snapshot: ExecutionResponseSnapshot,
) -> bytes | None:
    if snapshot.json_body is not None:
        return json.dumps(
            snapshot.json_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    if snapshot.text is not None:
        return snapshot.text.encode("utf-8")
    return None


def _execution_response_summary(snapshot: ExecutionResponseSnapshot) -> dict[str, Any]:
    sanitized = sanitize_response_snapshot(snapshot)
    headers = {
        name: value
        for name, value in sanitized.get("headers", {}).items()
        if not _is_sensitive_request_name(name)
    }
    body = _execution_response_body_bytes(snapshot)
    return {
        "status_code": sanitized["status_code"],
        "headers": headers,
        "body_size_bytes": len(body) if body is not None else 0,
        "body_sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "response_time_ms": sanitized.get("response_time_ms"),
        "elapsed_ms": sanitized.get("elapsed_ms"),
    }


def _evaluate_formal_api_assertions(
    session: Session,
    context: _ApiExecutionContext,
    content: SuggestedCase,
    response: ExecutionResponseSnapshot,
    *,
    commit: bool,
) -> list[AssertionResult]:
    indexed_assertions = list(enumerate(content.assertions, start=1))
    deterministic_results = {
        sequence: run_assertion(assertion, sequence, response)
        for sequence, assertion in indexed_assertions
        if assertion.kind == AssertionKind.DETERMINISTIC
    }
    deterministic_failed = any(
        item.status == AssertionStatus.FAIL for item in deterministic_results.values()
    )
    formal_user = CurrentUser(
        id=context.run.created_by,
        username="run-executor",
        display_name="Run Executor",
        roles=["ADMIN"],
    )
    results: list[AssertionResult] = []
    for sequence, assertion in indexed_assertions:
        deterministic = deterministic_results.get(sequence)
        if deterministic is not None:
            results.append(deterministic)
        elif deterministic_failed and assertion.enabled:
            results.append(
                AssertionResult(
                    sequence=sequence,
                    name=assertion.name,
                    type=assertion.type,
                    status=AssertionStatus.SKIPPED,
                    expected=None,
                    actual=None,
                    message="确定性断言失败，AI 断言未执行",
                    duration_ms=0,
                )
            )
        else:
            results.append(
                run_assertion(
                    assertion,
                    sequence,
                    response,
                    project_id=context.run.project_id,
                    session=session,
                    user=formal_user,
                    commit=commit,
                )
            )
    return results


def _encode_execution_evaluation(claims: dict[str, Any]) -> str:
    payload = json.dumps(
        claims,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(
        get_settings().secret_key.encode("utf-8"), encoded, hashlib.sha256
    ).digest()
    return (
        encoded.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def _decode_execution_evaluation(token: str) -> dict[str, Any]:
    try:
        encoded_text, signature_text = token.split(".", 1)
        encoded = encoded_text.encode("ascii")
        signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        expected = hmac.new(
            get_settings().secret_key.encode("utf-8"), encoded, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature mismatch")
        raw = base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4))
        claims = json.loads(raw)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RunStateConflictError("execution evaluation token 无效") from exc
    if not isinstance(claims, dict) or claims.get("version") != 1:
        raise RunStateConflictError("execution evaluation token 无效")
    issued_at = claims.get("issued_at")
    now = int(utc_now_aware().timestamp())
    if type(issued_at) is not int or issued_at > now + 300 or now - issued_at > 172_800:
        raise RunStateConflictError("execution evaluation token 已过期")
    return claims


def evaluate_api_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ExecutionEvaluationRequest,
) -> ExecutionEvaluationResponse:
    context = _execution_context(
        session,
        run_id,
        credential,
        payload.message_id,
        payload.case_run_id,
    )
    if (
        RunStatus(context.run.status) != RunStatus.RUNNING
        or RunNodeStatus(context.case_run.status) != RunNodeStatus.RUNNING
    ):
        raise RunStateConflictError("只有 RUNNING Run/CaseRun 可以评估 API_CASE 断言")
    response_summary = _execution_response_summary(payload.response)
    existing = session.scalar(
        select(RunApiExecutionEvaluation)
        .where(
            RunApiExecutionEvaluation.run_id == context.run.id,
            RunApiExecutionEvaluation.case_run_id == context.case_run.id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.message_id != payload.message_id
            or existing.response_summary != response_summary
        ):
            raise RunStateConflictError("API 断言评估已绑定其他响应")
        if (
            existing.status == "PENDING"
            or existing.assertion_results is None
            or existing.evaluation_token is None
        ):
            raise RunStateConflictError("API 断言评估正在进行")
        return ExecutionEvaluationResponse(
            run_id=context.run.id,
            message_id=payload.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            assertion_status=existing.status,
            cleanup_outcome=("SUCCESS" if existing.status == "PASS" else "FAILURE"),
            assertion_results=[
                AssertionResult.model_validate(item) for item in existing.assertion_results
            ],
            evaluation_token=existing.evaluation_token,
        )
    reservation = RunApiExecutionEvaluation(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        response_summary=response_summary,
        status="PENDING",
        assertion_results=None,
        evaluation_token=None,
    )
    session.add(reservation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise RunStateConflictError("API 断言评估已由另一请求启动") from exc

    _, content = _load_supported_api_case(session, context.run, enforce_v1_retry_limit=False)
    results = _evaluate_formal_api_assertions(
        session, context, content, payload.response, commit=True
    )
    assertion_status = summarize_final_status(results)
    audited = [_execution_assertion_audit(item) for item in results]
    claims = {
        "version": 1,
        "issued_at": int(utc_now_aware().timestamp()),
        "run_id": context.run.id,
        "message_id": payload.message_id,
        "case_run_id": context.case_run.id,
        "response_summary": response_summary,
        "assertion_status": assertion_status,
        "assertion_results": audited,
    }
    evaluation_token = _encode_execution_evaluation(claims)
    persisted = session.get(RunApiExecutionEvaluation, reservation.id)
    if persisted is None or persisted.status != "PENDING":
        raise RunStateConflictError("API 断言评估记录不可用")
    persisted.status = assertion_status
    persisted.assertion_results = audited
    persisted.evaluation_token = evaluation_token
    session.commit()
    return ExecutionEvaluationResponse(
        run_id=context.run.id,
        message_id=payload.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        assertion_status=assertion_status,
        cleanup_outcome="SUCCESS" if assertion_status == "PASS" else "FAILURE",
        assertion_results=[AssertionResult.model_validate(item) for item in audited],
        evaluation_token=evaluation_token,
    )


def complete_api_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ExecutionCompleteRequest,
    evidence_store: ObjectStore,
    event_stream: RedisRunEventStream,
) -> ExecutionCompleteResponse:
    context = _execution_context(
        session,
        run_id,
        credential,
        payload.message_id,
        payload.case_run_id,
    )
    existing = session.scalar(
        select(RunApiExecutionResult)
        .where(
            RunApiExecutionResult.run_id == context.run.id,
            RunApiExecutionResult.case_run_id == context.case_run.id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.message_id != payload.message_id:
            raise RunStateConflictError("重复完成的 message_id 不匹配")
        return _execution_complete_response(
            existing,
            context.runner.id,
            idempotent=True,
            run_status=RunStatus(context.run.status),
        )

    run_status = RunStatus(context.run.status)
    case_status = RunNodeStatus(context.case_run.status)
    effective_outcome = (
        ExecutionOutcome.CANCELLED if run_status == RunStatus.CANCELLING else payload.outcome
    )
    is_cancellation = effective_outcome == ExecutionOutcome.CANCELLED
    if is_cancellation:
        if run_status not in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            raise RunStateConflictError("只有 CANCELLING Run 可以完成取消")
    elif run_status != RunStatus.RUNNING or case_status != RunNodeStatus.RUNNING:
        raise RunStateConflictError("只有 RUNNING Run/CaseRun 可以完成 API_CASE 执行")
    content: SuggestedCase | None = None
    _, content = _load_supported_api_case(
        session,
        context.run,
        enforce_v1_retry_limit=False,
    )
    if content.request is None:
        raise RunExecutionUnsupportedError()
    if payload.retry_count > content.request.retry_policy.max_retries:
        raise RunStateConflictError("retry_count 超过当前 API Case 的重试策略上限")

    response_summary: dict[str, Any] | None = None
    assertion_results: list[AssertionResult] = []
    audited_assertions: list[dict[str, Any]] = []
    error_type: str | None = None
    error_message: str | None = None
    if effective_outcome == ExecutionOutcome.CANCELLED:
        result_status = RunStatus.CANCELLED
        if (
            payload.error_type == "FORCE_STOP_REQUESTED"
            or context.run.force_stop_requested_at is not None
        ):
            error_type = "FORCE_STOP_REQUESTED"
            error_message = "执行已被强制停止，Cleanup 可能未完成"
            context.run.force_stopped = True
        else:
            error_type = "CANCEL_REQUESTED"
            error_message = "执行已取消"
    elif effective_outcome == ExecutionOutcome.HTTP_RESPONSE:
        if payload.response is None:
            raise RunExecutionUnsupportedError("HTTP_RESPONSE 缺少 response")
        response_summary = _execution_response_summary(payload.response)
        if content is None:
            raise RunExecutionUnsupportedError()
        if payload.evaluation_token is not None:
            claims = _decode_execution_evaluation(payload.evaluation_token)
            if (
                claims.get("run_id") != context.run.id
                or claims.get("message_id") != payload.message_id
                or claims.get("case_run_id") != context.case_run.id
                or claims.get("response_summary") != response_summary
            ):
                raise RunStateConflictError("execution evaluation token 与本次执行不匹配")
            assertion_status = claims.get("assertion_status")
            raw_audits = claims.get("assertion_results")
            if assertion_status not in {"PASS", "FAIL", "REVIEW"} or not isinstance(
                raw_audits, list
            ):
                raise RunStateConflictError("execution evaluation token 内容无效")
            try:
                assertion_results = [AssertionResult.model_validate(item) for item in raw_audits]
            except ValidationError as exc:
                raise RunStateConflictError("execution evaluation token 断言结果无效") from exc
            audited_assertions = [_execution_assertion_audit(item) for item in assertion_results]
        else:
            assertion_results = _evaluate_formal_api_assertions(
                session, context, content, payload.response, commit=False
            )
            assertion_status = summarize_final_status(assertion_results)
            audited_assertions = [_execution_assertion_audit(item) for item in assertion_results]
        if assertion_status == "PASS":
            result_status = RunStatus.SUCCESS
        elif assertion_status == "REVIEW":
            result_status = RunNodeStatus.REVIEW
            error_type = "ASSERTION_REVIEW"
            error_message = "一个或多个 AI 断言需要人工复核"
        else:
            result_status = RunStatus.FAILED
            error_type = "ASSERTION_FAILED"
            error_message = "一个或多个确定性断言未通过"
    elif effective_outcome == ExecutionOutcome.TIMEOUT:
        result_status = RunStatus.TIMEOUT
        # 超时错误信息由服务端归一生成，不接受 Runner 上报的自由文本。
        if payload.error_type == "TOTAL_TIMEOUT":
            error_type = "TOTAL_TIMEOUT"
            error_message = "Run 总超时已终止执行"
        else:
            error_type = "TARGET_TIMEOUT"
            error_message = "目标执行超时"
    else:
        result_status = RunStatus.FAILED
        error_type = payload.error_type.strip() if payload.error_type else None
        error_message = _safe_error(payload.error_message)

    node_payload = NodeStatusUpdateRequest(
        status=(
            RunNodeStatus.SUCCESS
            if result_status == RunStatus.SUCCESS
            else RunNodeStatus.TIMEOUT
            if result_status == RunStatus.TIMEOUT
            else RunNodeStatus.CANCELLED
            if result_status == RunStatus.CANCELLED
            else RunNodeStatus.FAILED
        ),
        error_type=error_type,
        error_message=error_message,
        retry_count=payload.retry_count,
    )
    for step_run in context.case_run.step_runs:
        if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
            _set_node_status(step_run, node_payload.status, node_payload)
    _set_node_status(context.case_run, node_payload.status, node_payload)
    if is_cancellation:
        other_case_runs = list(
            session.scalars(
                select(CaseRun).where(CaseRun.run_id == context.run.id).with_for_update()
            ).all()
        )
        for other_case_run in other_case_runs:
            if other_case_run.id == context.case_run.id:
                continue
            other_status = RunNodeStatus(other_case_run.status)
            if other_status in TERMINAL_NODE_STATUSES:
                continue
            for step_run in other_case_run.step_runs:
                if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                    _set_node_status(step_run, RunNodeStatus.CANCELLED, node_payload)
            _set_node_status(other_case_run, RunNodeStatus.CANCELLED, node_payload)
    _recompute_summary(session, context.run)
    case_runs = list(
        session.scalars(
            select(CaseRun)
            .where(CaseRun.run_id == context.run.id)
            .order_by(CaseRun.sequence_no.asc())
            .with_for_update()
        ).all()
    )
    case_statuses = {RunNodeStatus(item.status) for item in case_runs}
    if any(status not in TERMINAL_NODE_STATUSES for status in case_statuses):
        effective_run_status = RunStatus.RUNNING
    else:
        effective_run_status = (
            RunStatus.FAILED
            if case_statuses & {RunNodeStatus.FAILED, RunNodeStatus.REVIEW}
            else RunStatus.TIMEOUT
            if RunNodeStatus.TIMEOUT in case_statuses
            else RunStatus.CANCELLED
            if RunNodeStatus.CANCELLED in case_statuses
            else RunStatus.SUCCESS
        )
        final_error_type = (
            error_type
            if effective_run_status == result_status
            else "ITERATION_FAILED"
            if effective_run_status == RunStatus.FAILED
            else "ITERATION_TIMEOUT"
            if effective_run_status == RunStatus.TIMEOUT
            else "ITERATION_CANCELLED"
            if effective_run_status == RunStatus.CANCELLED
            else None
        )
        final_error_message = (
            error_message
            if effective_run_status == result_status
            else "一个或多个数据驱动迭代未成功"
            if effective_run_status != RunStatus.SUCCESS
            else None
        )
        _ensure_terminal_run_matches_children(session, context.run, effective_run_status)
        _set_run_status(
            context.run,
            effective_run_status,
            RunStatusUpdateRequest(
                status=effective_run_status,
                error_type=final_error_type,
                error_message=final_error_message,
            ),
        )
    existing_resource_count = int(
        session.scalar(
            select(func.count(ResourceRegistryEntry.id)).where(
                ResourceRegistryEntry.run_id == context.run.id
            )
        )
        or 0
    )
    for resource in payload.resources:
        session.add(
            ResourceRegistryEntry(
                project_id=context.run.project_id,
                run_id=context.run.id,
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                cleanup_type=resource.cleanup.cleanup_type.value,
                cleanup_config=resource.cleanup.model_dump(mode="json"),
                status=resource.status,
                registration_sequence=(existing_resource_count + resource.registration_sequence),
                attempt_count=resource.attempt_count,
                last_error=resource.error_message,
                error_code=resource.error_code,
                source="API_CASE_RUNTIME",
                cleaned_at=(
                    utc_now_naive() if resource.status == CleanupStatus.CLEANED.value else None
                ),
            )
        )
    if response_summary is not None:
        try:
            create_execution_evidence(
                session,
                evidence_store,
                project_id=context.run.project_id,
                run_id=context.run.id,
                case_run_id=context.case_run.id,
                message_id=payload.message_id,
                response_summary=response_summary,
                assertion_results=audited_assertions,
            )
        except EvidenceStorageError:
            session.rollback()
            logger.warning(
                "run_evidence_upload_failed",
                extra={
                    "event": "RUN_EVIDENCE_UPLOAD_FAILED",
                    "run_id": context.run.id,
                    "case_run_id": context.case_run.id,
                    "message_id": payload.message_id,
                },
            )
            raise
    result = RunApiExecutionResult(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        outcome=effective_outcome.value,
        status=result_status.value,
        retry_count=payload.retry_count,
        response_summary=response_summary,
        assertion_results=audited_assertions,
        action_traces=[item.model_dump(mode="json") for item in payload.action_traces],
        error_type=error_type,
        error_message=error_message,
        completed_at=utc_now_naive(),
    )
    session.add(result)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(RunApiExecutionResult).where(
                RunApiExecutionResult.run_id == run_id,
                RunApiExecutionResult.case_run_id == payload.case_run_id,
            )
        )
        if existing is not None and existing.message_id == payload.message_id:
            current_run = get_run(session, run_id)
            return _execution_complete_response(
                existing,
                context.runner.id,
                idempotent=True,
                run_status=(RunStatus(current_run.status) if current_run is not None else None),
            )
        raise ResourceConflictError("API_CASE 执行完成结果保存冲突") from exc
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=run_status,
        to_status=effective_run_status,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return _execution_complete_response(
        result,
        context.runner.id,
        idempotent=False,
        run_status=effective_run_status,
    )


def _load_supported_scenario(session: Session, run: TestRun) -> tuple[ScenarioVersion, ScenarioDsl]:
    if (
        run.run_type != RunType.SCENARIO.value
        or run.scenario_id is None
        or run.scenario_version_id is None
    ):
        raise RunExecutionUnsupportedError("当前 Run 不是可执行的 Scenario")
    version = session.get(ScenarioVersion, run.scenario_version_id)
    if version is None or version.scenario_id != run.scenario_id:
        raise RunExecutionUnsupportedError("固定 Scenario Version 不存在或归属不匹配")
    try:
        dsl = ScenarioDsl.model_validate(version.dsl)
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("固定 Scenario DSL 无法按当前协议解析") from exc
    issues: list[RunValidationIssue] = []
    _scenario_execution_support_issues(
        session,
        run.project_id,
        run.environment_id or 0,
        dsl,
        issues,
        runtime_variables=run.runtime_variables or {},
    )
    if issues:
        raise RunExecutionUnsupportedError("固定 Scenario 当前不满足安全执行范围")
    return version, dsl


def _append_scenario_secret(
    session: Session,
    project_id: int,
    secret_id: int,
    secrets: dict[int, ScenarioExecutionSecret],
) -> None:
    if secret_id in secrets:
        return
    try:
        value = resolve_secret(session, secret_id, project_id)
    except Exception as exc:
        raise RunExecutionUnsupportedError("Scenario 所需 Secret 暂时无法安全解析") from exc
    secrets[secret_id] = ScenarioExecutionSecret(secret_id=secret_id, value=value)


def _scenario_sql_connection(
    session: Session,
    run: TestRun,
    connection_id: int,
    connections: dict[int, ScenarioSqlConnectionPlan],
    secrets: dict[int, ScenarioExecutionSecret],
) -> None:
    connection = session.get(DatabaseConnection, connection_id)
    if (
        connection is None
        or connection.project_id != run.project_id
        or connection.environment_id != run.environment_id
        or not connection.enabled
    ):
        raise RunExecutionUnsupportedError("Scenario SQL Connection 不存在、停用或归属不匹配")
    if connection_id not in connections:
        _append_scenario_secret(session, run.project_id, connection.password_secret_id, secrets)
        connections[connection_id] = ScenarioSqlConnectionPlan(
            connection_id=connection.id,
            db_type="MYSQL",
            host=connection.host,
            port=connection.port,
            database_name=connection.database_name,
            username=connection.username,
            password_secret_id=connection.password_secret_id,
            ssl_enabled=connection.ssl_enabled,
        )


def _scenario_plan_node_config(
    session: Session,
    run: TestRun,
    node: Any,
    connections: dict[int, ScenarioSqlConnectionPlan],
    secrets: dict[int, ScenarioExecutionSecret],
    dsl: ScenarioDsl,
) -> dict[str, Any]:
    config = dict(node.config)
    if node.type in {
        ScenarioNodeType.SQL_QUERY,
        ScenarioNodeType.SQL_EXECUTE,
        ScenarioNodeType.SQL_CLEANUP,
    }:
        connection_id = config.get("connection_id")
        if not isinstance(connection_id, int):
            raise RunExecutionUnsupportedError("Scenario SQL Connection 无效")
        _scenario_sql_connection(session, run, connection_id, connections, secrets)
    if node.type in {ScenarioNodeType.API_CLEANUP, ScenarioNodeType.SQL_CLEANUP}:
        try:
            config = node.cleanup_config(dsl.settings.cleanup_policy).model_dump(mode="json")
        except (TypeError, ValueError, ValidationError) as exc:
            raise RunExecutionUnsupportedError("Scenario Cleanup 配置无效") from exc
        secret_id = (config.get("auth") or {}).get("secret_id")
        if isinstance(secret_id, int):
            _append_scenario_secret(session, run.project_id, secret_id, secrets)
    try:
        if node.type != ScenarioNodeType.HTTP:
            validate_secret_free_payload(config, allow_auth_reference=True)
        encoded_config = json.dumps(
            config, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
        if len(encoded_config) > 64_000:
            raise ValueError("节点配置超过 64KB")
    except (TypeError, ValueError) as exc:
        raise RunExecutionUnsupportedError(
            f"Scenario 节点配置不满足安全下发边界：{node.id}"
        ) from exc
    if node.type == ScenarioNodeType.HTTP:
        try:
            request = _scenario_http_template(config)
            if not _api_request_credentials_valid(request, _scenario_runtime_names(dsl)):
                raise ValueError("HTTP 敏感字段不是受控引用")
            config = _resolved_api_request(
                session,
                run,
                request,
                resolve_runtime=False,
                allow_deferred_json_body=True,
            ).model_dump(mode="json")
            if (
                len(
                    json.dumps(
                        config,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                )
                > 64_000
            ):
                raise ValueError("HTTP 节点配置超过 64KB")
        except (RunExecutionUnsupportedError, ValidationError, TypeError, ValueError) as exc:
            raise RunExecutionUnsupportedError(
                f"Scenario HTTP 节点无法生成安全请求：{node.id}"
            ) from exc
    return config


def _scenario_plan_response(
    session: Session,
    context: _ApiExecutionContext,
    version: ScenarioVersion,
    dsl: ScenarioDsl,
) -> ScenarioExecutionPlanResponse:
    connections: dict[int, ScenarioSqlConnectionPlan] = {}
    secrets: dict[int, ScenarioExecutionSecret] = {}
    issues: list[RunValidationIssue] = []
    environment_values = _scenario_initial_context_issues(
        session,
        context.run.project_id,
        context.run.environment_id or 0,
        dsl,
        issues,
        runtime_variables=context.run.runtime_variables or {},
    )
    if issues:
        raise RunExecutionUnsupportedError("Scenario Runtime Context 当前不满足安全执行范围")
    initial_context = {
        name: environment_values[name]
        for name in dsl.settings.initial_variables
        if name in environment_values
    }
    initial_context.update(
        {
            name: context.run.runtime_variables[name]
            for name in dsl.settings.initial_variables
            if name in (context.run.runtime_variables or {})
        }
    )
    initial_context.update(
        {
            "run_id": context.run.id,
            "scenario_id": context.run.scenario_id,
            "project_id": context.run.project_id,
            "environment_id": context.run.environment_id,
        }
    )
    nodes = [
        ScenarioExecutionNodePlan(
            node_id=node.id,
            node_type=node.type.value,
            name=node.name,
            parent_id=node.parent_id,
            enabled=node.enabled,
            timeout_ms=node.timeout_ms,
            failure_policy=node.failure_policy.value if node.failure_policy else None,
            config=_scenario_plan_node_config(
                session, context.run, node, connections, secrets, dsl
            ),
        )
        for node in dsl.nodes
    ]
    return ScenarioExecutionPlanResponse(
        schema_version=1,
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        scenario_id=context.run.scenario_id,
        scenario_version_id=version.id,
        total_timeout_ms=_persisted_total_timeout_ms(context.run),
        initial_context=initial_context,
        settings=dsl.settings.model_dump(mode="json"),
        nodes=nodes,
        sql_connections=list(connections.values()),
        secrets=list(secrets.values()),
    )


def get_scenario_execution_plan(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int | None = None,
) -> ScenarioExecutionPlanResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    if RunStatus(context.run.status) not in {
        RunStatus.ASSIGNED,
        RunStatus.RUNNING,
        RunStatus.CANCELLING,
    }:
        raise RunStateConflictError(
            "只有 ASSIGNED/RUNNING/CANCELLING Scenario Run 可以读取执行计划"
        )
    version, dsl = _load_supported_scenario(session, context.run)
    return _scenario_plan_response(session, context, version, dsl)


def _scenario_ai_assertion(node: Any) -> Assertion:
    try:
        return Assertion(
            kind=AssertionKind.AI_SEMANTIC,
            type="AI_SEMANTIC",
            name=node.name,
            prompt_id=node.config.get("prompt_id"),
            criteria=node.config.get("criteria"),
            confidence_threshold=node.config.get("confidence_threshold", 0.8),
        )
    except ValidationError as exc:
        raise RunExecutionUnsupportedError("Scenario AI 断言配置无效") from exc


def _scenario_ai_response_summary(
    payload: ScenarioExecutionEvaluationRequest,
) -> dict[str, Any]:
    return {
        "scenario_ai": [
            {
                "node_id": item.node_id,
                "response": _execution_response_summary(item.response),
            }
            for item in payload.evaluations
        ]
    }


def _scenario_evaluation_response(
    context: _ApiExecutionContext,
    payload: ScenarioExecutionEvaluationRequest,
    results: list[AssertionResult],
    token: str,
) -> ScenarioExecutionEvaluationResponse:
    status = summarize_final_status(results)
    return ScenarioExecutionEvaluationResponse(
        run_id=context.run.id,
        message_id=payload.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        assertion_status=status,
        cleanup_outcome="SUCCESS" if status == "PASS" else "FAILURE",
        assertion_results=results,
        node_results=[
            {"node_id": item.node_id, "status": result.status.value}
            for item, result in zip(payload.evaluations, results, strict=True)
        ],
        evaluation_token=token,
    )


def evaluate_scenario_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ScenarioExecutionEvaluationRequest,
) -> ScenarioExecutionEvaluationResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    if (
        RunStatus(context.run.status) != RunStatus.RUNNING
        or RunNodeStatus(context.case_run.status) != RunNodeStatus.RUNNING
    ):
        raise RunStateConflictError("只有 RUNNING Run/CaseRun 可以评估 Scenario AI 断言")
    _, dsl = _load_supported_scenario(session, context.run)
    nodes = [
        node for node in dsl.nodes if node.enabled and node.type == ScenarioNodeType.AI_ASSERTION
    ]
    if not nodes or [node.id for node in nodes] != [item.node_id for item in payload.evaluations]:
        raise RunStateConflictError("Scenario AI evaluation 与固定 DSL 不匹配")
    response_summary = _scenario_ai_response_summary(payload)
    existing = session.scalar(
        select(RunApiExecutionEvaluation)
        .where(
            RunApiExecutionEvaluation.run_id == context.run.id,
            RunApiExecutionEvaluation.case_run_id == context.case_run.id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.message_id != payload.message_id
            or existing.response_summary != response_summary
        ):
            raise RunStateConflictError("Scenario AI 断言评估已绑定其他响应")
        if (
            existing.status == "PENDING"
            or existing.assertion_results is None
            or existing.evaluation_token is None
        ):
            raise RunStateConflictError("Scenario AI 断言评估正在进行")
        results = [AssertionResult.model_validate(item) for item in existing.assertion_results]
        return _scenario_evaluation_response(context, payload, results, existing.evaluation_token)

    reservation = RunApiExecutionEvaluation(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        response_summary=response_summary,
        status="PENDING",
        assertion_results=None,
        evaluation_token=None,
    )
    session.add(reservation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise RunStateConflictError("Scenario AI 断言评估已由另一请求启动") from exc

    formal_user = CurrentUser(
        id=context.run.created_by,
        username="run-executor",
        display_name="Run Executor",
        roles=["ADMIN"],
    )
    results = [
        run_assertion(
            _scenario_ai_assertion(node),
            sequence,
            item.response,
            project_id=context.run.project_id,
            session=session,
            user=formal_user,
            commit=True,
        )
        for sequence, (node, item) in enumerate(
            zip(nodes, payload.evaluations, strict=True), start=1
        )
    ]
    assertion_status = summarize_final_status(results)
    audited = [_execution_assertion_audit(item) for item in results]
    claims = {
        "version": 1,
        "kind": "SCENARIO_AI_ASSERTION",
        "issued_at": int(utc_now_aware().timestamp()),
        "run_id": context.run.id,
        "message_id": payload.message_id,
        "case_run_id": context.case_run.id,
        "scenario_version_id": context.run.scenario_version_id,
        "response_summary": response_summary,
        "assertion_status": assertion_status,
        "assertion_results": audited,
        "node_results": [
            {"node_id": node.id, "status": result.status.value}
            for node, result in zip(nodes, results, strict=True)
        ],
    }
    token = _encode_execution_evaluation(claims)
    persisted = session.get(RunApiExecutionEvaluation, reservation.id)
    if persisted is None or persisted.status != "PENDING":
        raise RunStateConflictError("Scenario AI 断言评估记录不可用")
    persisted.status = assertion_status
    persisted.assertion_results = audited
    persisted.evaluation_token = token
    session.commit()
    return _scenario_evaluation_response(context, payload, results, token)


def start_scenario_execution(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
    case_run_id: int,
    event_stream: RedisRunEventStream,
) -> ScenarioExecutionStartResponse:
    context = _execution_context(session, run_id, credential, message_id, case_run_id)
    run_status = RunStatus(context.run.status)
    if run_status == RunStatus.CANCELLING:
        return ScenarioExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.CANCELLING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    _load_supported_scenario(session, context.run)
    case_status = RunNodeStatus(context.case_run.status)
    if (
        run_status == RunStatus.RUNNING
        and case_status == RunNodeStatus.RUNNING
        and context.run.started_at is not None
    ):
        return ScenarioExecutionStartResponse(
            run_id=context.run.id,
            message_id=context.outbox.message_id,
            runner_id=context.runner.id,
            case_run_id=context.case_run.id,
            status=RunStatus.RUNNING,
            started_at=to_utc_aware(context.run.started_at),
            idempotent=True,
        )
    if run_status != RunStatus.ASSIGNED:
        raise RunStateConflictError("只有 ASSIGNED Scenario Run 可以开始执行")
    if case_status not in {RunNodeStatus.CREATED, RunNodeStatus.ASSIGNED}:
        raise RunStateConflictError("Scenario CaseRun 状态不允许开始执行")
    step_runs = list(
        session.scalars(
            select(StepRun).where(StepRun.case_run_id == context.case_run.id).with_for_update()
        ).all()
    )
    status_payload = NodeStatusUpdateRequest(status=RunNodeStatus.RUNNING)
    for step_run in step_runs:
        if RunNodeStatus(step_run.status) in TERMINAL_NODE_STATUSES:
            raise RunStateConflictError("Scenario StepRun 已结束，不能开始执行")
        _set_node_status(step_run, RunNodeStatus.RUNNING, status_payload)
    _set_node_status(context.case_run, RunNodeStatus.RUNNING, status_payload)
    _set_run_status(
        context.run,
        RunStatus.RUNNING,
        RunStatusUpdateRequest(status=RunStatus.RUNNING),
    )
    session.commit()
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=RunStatus.ASSIGNED,
        to_status=RunStatus.RUNNING,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return ScenarioExecutionStartResponse(
        run_id=context.run.id,
        message_id=context.outbox.message_id,
        runner_id=context.runner.id,
        case_run_id=context.case_run.id,
        status=RunStatus.RUNNING,
        started_at=to_utc_aware(context.run.started_at),
        idempotent=False,
    )


def _scenario_completion_response(
    result: RunScenarioExecutionResult,
    runner_id: str,
    *,
    idempotent: bool,
    case_run_status: RunNodeStatus | None = None,
) -> ScenarioExecutionCompleteResponse:
    return ScenarioExecutionCompleteResponse(
        run_id=result.run_id,
        message_id=result.message_id,
        runner_id=runner_id,
        case_run_id=result.case_run_id,
        outcome=ScenarioExecutionOutcome(result.outcome),
        run_status=RunStatus(result.status),
        case_run_status=case_run_status or RunNodeStatus(result.status),
        traces=[ScenarioExecutionTrace.model_validate(item) for item in result.traces],
        assertion_results=[
            AssertionResult.model_validate(item) for item in (result.assertion_results or [])
        ],
        error_type=result.error_type,
        error_message=result.error_message,
        completed_at=to_utc_aware(result.completed_at),
        idempotent=idempotent,
    )


def _scenario_trace_status(value: str) -> RunNodeStatus:
    return RunNodeStatus.SUCCESS if value == "PASSED" else RunNodeStatus(value)


def _scenario_trace_audit(trace: ScenarioExecutionTrace) -> dict[str, Any]:
    status = _scenario_trace_status(trace.status).value
    message = _safe_error(trace.error_message, maximum=500)
    if message is None and status == RunNodeStatus.SUCCESS.value:
        message = "节点执行成功"
    return {
        "node_id": trace.node_id,
        "status": status,
        "duration_ms": trace.duration_ms,
        "retry_count": trace.retry_count,
        "error_type": _safe_error(trace.error_type, maximum=100),
        "error_message": message,
    }


def _validate_scenario_traces(
    dsl: ScenarioDsl,
    payload: ScenarioExecutionCompleteRequest,
) -> dict[str, ScenarioExecutionTrace]:
    node_ids = {node.id for node in dsl.nodes}
    traces: dict[str, ScenarioExecutionTrace] = {}
    for trace in payload.traces:
        if trace.node_id not in node_ids:
            raise RunStateConflictError("Scenario trace 包含未知 node_id")
        if trace.node_id in traces:
            raise RunStateConflictError("Scenario trace 不能重复上报同一 node_id")
        traces[trace.node_id] = trace
    if payload.outcome == ScenarioExecutionOutcome.SUCCESS and set(traces) != node_ids:
        raise RunStateConflictError("Scenario SUCCESS 必须为固定 DSL 的每个节点提供 trace")
    return traces


def complete_scenario_execution(
    session: Session,
    run_id: str,
    credential: str,
    payload: ScenarioExecutionCompleteRequest,
    event_stream: RedisRunEventStream,
) -> ScenarioExecutionCompleteResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    existing = session.scalar(
        select(RunScenarioExecutionResult)
        .where(
            RunScenarioExecutionResult.run_id == context.run.id,
            RunScenarioExecutionResult.case_run_id == context.case_run.id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.message_id != payload.message_id:
            raise RunStateConflictError("重复完成的 message_id 不匹配")
        return _scenario_completion_response(
            existing,
            context.runner.id,
            idempotent=True,
            case_run_status=RunNodeStatus(context.case_run.status),
        )

    _, dsl = _load_supported_scenario(session, context.run)
    run_status = RunStatus(context.run.status)
    case_status = RunNodeStatus(context.case_run.status)
    effective_outcome = (
        ScenarioExecutionOutcome.CANCELLED
        if run_status == RunStatus.CANCELLING
        else payload.outcome
    )
    if effective_outcome == ScenarioExecutionOutcome.CANCELLED:
        if run_status not in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            raise RunStateConflictError("只有 CANCELLING/CANCELLED Scenario Run 可以完成取消")
    elif run_status != RunStatus.RUNNING or case_status != RunNodeStatus.RUNNING:
        raise RunStateConflictError("只有 RUNNING Scenario Run/CaseRun 可以完成执行")

    traces = _validate_scenario_traces(dsl, payload)
    ai_nodes = [
        node for node in dsl.nodes if node.enabled and node.type == ScenarioNodeType.AI_ASSERTION
    ]
    assertion_results: list[AssertionResult] = []
    audited_assertions: list[dict[str, Any]] = []
    if payload.evaluation_token is not None:
        if effective_outcome in {
            ScenarioExecutionOutcome.CANCELLED,
            ScenarioExecutionOutcome.TIMEOUT,
        }:
            raise RunStateConflictError("取消或超时 Scenario 不能提供 AI evaluation token")
        claims = _decode_execution_evaluation(payload.evaluation_token)
        if (
            claims.get("kind") != "SCENARIO_AI_ASSERTION"
            or claims.get("run_id") != context.run.id
            or claims.get("message_id") != payload.message_id
            or claims.get("case_run_id") != context.case_run.id
            or claims.get("scenario_version_id") != context.run.scenario_version_id
        ):
            raise RunStateConflictError("Scenario AI evaluation token 与本次执行不匹配")
        raw_audits = claims.get("assertion_results")
        node_results = claims.get("node_results")
        assertion_status = claims.get("assertion_status")
        if (
            assertion_status not in {"PASS", "FAIL", "REVIEW"}
            or not isinstance(raw_audits, list)
            or not isinstance(node_results, list)
            or len(node_results) != len(ai_nodes)
        ):
            raise RunStateConflictError("Scenario AI evaluation token 内容无效")
        try:
            assertion_results = [AssertionResult.model_validate(item) for item in raw_audits]
        except ValidationError as exc:
            raise RunStateConflictError("Scenario AI evaluation token 断言结果无效") from exc
        if len(assertion_results) != len(ai_nodes):
            raise RunStateConflictError("Scenario AI evaluation token 断言数量无效")
        expected_trace_status = {"PASS": "PASSED", "FAIL": "FAILED", "REVIEW": "REVIEW"}
        for node, node_result in zip(ai_nodes, node_results, strict=True):
            if (
                not isinstance(node_result, dict)
                or node_result.get("node_id") != node.id
                or node_result.get("status") not in expected_trace_status
                or traces.get(node.id) is None
                or traces[node.id].status != expected_trace_status[node_result["status"]]
            ):
                raise RunStateConflictError("Scenario AI evaluation 与 trace 不匹配")
        if assertion_status != "PASS" and effective_outcome != ScenarioExecutionOutcome.FAILED:
            raise RunStateConflictError("Scenario AI 断言未通过时 outcome 必须为 FAILED")
        audited_assertions = [_execution_assertion_audit(item) for item in assertion_results]
    elif ai_nodes:
        if effective_outcome == ScenarioExecutionOutcome.SUCCESS or any(
            traces.get(node.id) is not None and traces[node.id].status != "SKIPPED"
            for node in ai_nodes
        ):
            raise RunStateConflictError("Scenario AI 断言缺少受信评估结果")
    step_runs = list(
        session.scalars(
            select(StepRun).where(StepRun.case_run_id == context.case_run.id).with_for_update()
        ).all()
    )
    trace_by_node = {item.node_id: item for item in step_runs}
    if set(trace_by_node) != {node.id for node in dsl.nodes}:
        raise RunStateConflictError("固定 DSL 与 StepRun 节点不一致")

    error_type = _safe_error(payload.error_type, maximum=100)
    error_message = _safe_error(payload.error_message, maximum=500)
    status_payload = NodeStatusUpdateRequest(
        status=RunNodeStatus.CANCELLED,
        error_type=error_type,
        error_message=error_message,
    )
    if effective_outcome == ScenarioExecutionOutcome.CANCELLED:
        error_type = (
            "FORCE_STOP_REQUESTED"
            if context.run.force_stop_requested_at is not None
            else "CANCEL_REQUESTED"
        )
        error_message = (
            "执行已被强制停止，Cleanup 可能未完成"
            if error_type == "FORCE_STOP_REQUESTED"
            else "执行已取消"
        )
        status_payload = NodeStatusUpdateRequest(
            status=RunNodeStatus.CANCELLED,
            error_type=error_type,
            error_message=error_message,
        )
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) not in TERMINAL_NODE_STATUSES:
                _set_node_status(step_run, RunNodeStatus.CANCELLED, status_payload)
        if RunNodeStatus(context.case_run.status) not in TERMINAL_NODE_STATUSES:
            _set_node_status(context.case_run, RunNodeStatus.CANCELLED, status_payload)
        context.run.force_stopped = context.run.force_stopped or (
            error_type == "FORCE_STOP_REQUESTED"
        )
        result_status = RunStatus.CANCELLED
    else:
        status_by_node = {
            node_id: _scenario_trace_status(trace.status) for node_id, trace in traces.items()
        }
        if effective_outcome == ScenarioExecutionOutcome.SUCCESS and any(
            status in {RunNodeStatus.FAILED, RunNodeStatus.TIMEOUT, RunNodeStatus.CANCELLED}
            for status in status_by_node.values()
        ):
            raise RunStateConflictError("Scenario trace 与 SUCCESS outcome 不一致")
        if effective_outcome == ScenarioExecutionOutcome.FAILED and not any(
            status in {RunNodeStatus.FAILED, RunNodeStatus.REVIEW}
            for status in status_by_node.values()
        ):
            first_unfinished = next(
                (
                    item
                    for item in step_runs
                    if RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES
                ),
                None,
            )
            if first_unfinished is not None:
                status_by_node[first_unfinished.node_id] = RunNodeStatus.FAILED
        if effective_outcome == ScenarioExecutionOutcome.TIMEOUT and not any(
            status == RunNodeStatus.TIMEOUT for status in status_by_node.values()
        ):
            first_unfinished = next(
                (
                    item
                    for item in step_runs
                    if RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES
                ),
                None,
            )
            if first_unfinished is not None:
                status_by_node[first_unfinished.node_id] = RunNodeStatus.TIMEOUT
        for step_run in step_runs:
            if RunNodeStatus(step_run.status) in TERMINAL_NODE_STATUSES:
                continue
            desired = status_by_node.get(step_run.node_id)
            if desired is None:
                if effective_outcome == ScenarioExecutionOutcome.FAILED:
                    desired = RunNodeStatus.SKIPPED
                elif effective_outcome == ScenarioExecutionOutcome.TIMEOUT:
                    desired = RunNodeStatus.TIMEOUT
                else:
                    raise RunStateConflictError("Scenario SUCCESS 缺少节点 trace")
            trace = traces.get(step_run.node_id)
            node_payload = NodeStatusUpdateRequest(
                status=desired,
                duration=trace.duration_ms if trace else None,
                retry_count=trace.retry_count if trace else None,
                error_type=(_safe_error(trace.error_type, maximum=100) if trace else error_type),
                error_message=(
                    _safe_error(trace.error_message, maximum=500) if trace else error_message
                ),
            )
            _set_node_status(step_run, desired, node_payload)
        _aggregate_case_status(context.case_run)
        result_status = {
            ScenarioExecutionOutcome.SUCCESS: RunStatus.SUCCESS,
            ScenarioExecutionOutcome.FAILED: RunStatus.FAILED,
            ScenarioExecutionOutcome.TIMEOUT: RunStatus.TIMEOUT,
        }[effective_outcome]
        if result_status == RunStatus.SUCCESS:
            error_type = None
            error_message = None
        elif result_status == RunStatus.TIMEOUT:
            error_type = error_type or "SCENARIO_TIMEOUT"
            error_message = error_message or "Scenario 执行超时"
        else:
            error_type = error_type or "SCENARIO_EXECUTION_FAILED"
            error_message = error_message or "Scenario 执行失败"

    _recompute_summary(session, context.run)
    _ensure_terminal_run_matches_children(session, context.run, result_status)
    _set_run_status(
        context.run,
        result_status,
        RunStatusUpdateRequest(
            status=result_status,
            error_type=error_type,
            error_message=error_message,
        ),
    )
    audited_traces = [_scenario_trace_audit(trace) for trace in payload.traces]
    result = RunScenarioExecutionResult(
        run_id=context.run.id,
        case_run_id=context.case_run.id,
        message_id=payload.message_id,
        outcome=result_status.value,
        status=result_status.value,
        traces=audited_traces,
        assertion_results=audited_assertions,
        error_type=error_type,
        error_message=error_message,
        completed_at=utc_now_naive(),
    )
    session.add(result)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(RunScenarioExecutionResult).where(
                RunScenarioExecutionResult.run_id == run_id,
                RunScenarioExecutionResult.case_run_id == payload.case_run_id,
            )
        )
        if existing is not None and existing.message_id == payload.message_id:
            return _scenario_completion_response(
                existing,
                context.runner.id,
                idempotent=True,
                case_run_status=RunNodeStatus(context.case_run.status),
            )
        raise ResourceConflictError("Scenario 执行完成结果保存冲突") from exc
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=run_status,
        to_status=result_status,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return _scenario_completion_response(
        result,
        context.runner.id,
        idempotent=False,
        case_run_status=RunNodeStatus(context.case_run.status),
    )


def _set_run_status(run: TestRun, requested: RunStatus, payload: RunStatusUpdateRequest) -> bool:
    current = RunStatus(run.status)
    if current == requested:
        return False
    if requested not in _RUN_TRANSITIONS[current]:
        raise RunStateConflictError(f"不允许从 {current.value} 更新为 {requested.value}")
    now = utc_now_naive()
    run.status = requested.value
    if requested == RunStatus.RUNNING and run.started_at is None:
        run.started_at = now
    if requested in TERMINAL_RUN_STATUSES and run.ended_at is None:
        run.ended_at = now
    if payload.error_type is not None:
        run.error_type = payload.error_type.strip() or None
    if payload.error_message is not None:
        run.error_message = _safe_error(payload.error_message)
    return True


def _authorize_user_run_transition(run: TestRun, requested: RunStatus) -> None:
    current = RunStatus(run.status)
    if current == requested == RunStatus.CANCELLED:
        return
    if current == RunStatus.CREATED and requested == RunStatus.CANCELLED:
        return
    raise RunStateConflictError("项目用户只能通过取消接口请求取消，不能直接伪造 Run 执行状态")


def _authorize_runner_run_transition(run: TestRun, requested: RunStatus) -> None:
    current = RunStatus(run.status)
    if current == requested and requested in {
        RunStatus.RUNNING,
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMEOUT,
    }:
        return
    allowed = {
        RunStatus.ASSIGNED: {RunStatus.RUNNING},
        RunStatus.RUNNING: {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.TIMEOUT},
        RunStatus.CANCELLING: {RunStatus.CANCELLED},
    }
    if requested not in allowed.get(current, set()):
        raise RunStateConflictError(f"Runner 不允许从 {current.value} 上报为 {requested.value}")


def _ensure_terminal_run_matches_children(
    session: Session, run: TestRun, requested: RunStatus
) -> None:
    if requested not in TERMINAL_RUN_STATUSES:
        return
    case_runs = list(
        session.scalars(select(CaseRun).where(CaseRun.run_id == run.id).with_for_update()).all()
    )
    if not case_runs or any(
        RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES for item in case_runs
    ):
        raise RunStateConflictError("Run 仍有未结束的 CaseRun")
    statuses = {RunNodeStatus(item.status) for item in case_runs}
    if requested == RunStatus.SUCCESS and statuses - {
        RunNodeStatus.SUCCESS,
        RunNodeStatus.SKIPPED,
    }:
        raise RunStateConflictError("存在非成功 CaseRun，不能上报 Run SUCCESS")
    if requested == RunStatus.FAILED and not statuses.intersection(
        {RunNodeStatus.FAILED, RunNodeStatus.REVIEW}
    ):
        raise RunStateConflictError("不存在失败或待复核 CaseRun，不能上报 Run FAILED")
    if requested == RunStatus.TIMEOUT and RunNodeStatus.TIMEOUT not in statuses:
        raise RunStateConflictError("不存在超时 CaseRun，不能上报 Run TIMEOUT")
    if requested == RunStatus.CANCELLED and (
        RunStatus(run.status) not in {RunStatus.CANCELLING, RunStatus.CANCELLED}
        or RunNodeStatus.CANCELLED not in statuses
    ):
        raise RunStateConflictError("Run 未在取消中或不存在已取消 CaseRun")


def update_run_status(
    session: Session,
    run_id: str,
    payload: RunStatusUpdateRequest,
    *,
    user: CurrentUser | None = None,
    runner_credential: str | None = None,
    event_stream: RedisRunEventStream,
) -> RunDetailResponse:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    if user is not None:
        _authorize_run_write(session, user, run)
        _authorize_user_run_transition(run, payload.status)
    elif runner_credential is not None:
        _authenticate_runner_for_run(session, run, runner_credential)
        _authorize_runner_run_transition(run, payload.status)
        _ensure_terminal_run_matches_children(session, run, payload.status)
    else:
        raise AuthenticationError("Run 状态更新需要认证")
    previous_status = RunStatus(run.status)
    changed = _set_run_status(run, payload.status, payload)
    if changed:
        session.commit()
        publish_run_event_best_effort(
            event_stream,
            project_id=run.project_id,
            run_id=run.id,
            from_status=previous_status,
            to_status=payload.status,
            case_run_id=run.case_runs[0].id if run.case_runs else None,
            total=run.total,
            pass_count=run.pass_count,
            fail_count=run.fail_count,
            review_count=run.review_count,
            timeout_count=run.timeout_count,
        )
    detail = get_run_with_children(session, run_id)
    if detail is None:
        raise ResourceNotFoundError("Run 不存在")
    return _run_detail(session, detail)


def _set_node_status(
    node: CaseRun | StepRun, requested: RunNodeStatus, payload: NodeStatusUpdateRequest
) -> bool:
    current = RunNodeStatus(node.status)
    if current == requested:
        return False
    if requested not in _NODE_TRANSITIONS[current]:
        raise RunStateConflictError(f"不允许从 {current.value} 更新为 {requested.value}")
    now = utc_now_naive()
    node.status = requested.value
    if requested == RunNodeStatus.RUNNING and node.started_at is None:
        node.started_at = now
    if requested in TERMINAL_NODE_STATUSES:
        if node.ended_at is None:
            node.ended_at = now
        if payload.duration is not None:
            node.duration = payload.duration
        elif node.started_at is not None:
            node.duration = max(0, int((node.ended_at - node.started_at).total_seconds() * 1000))
    if payload.retry_count is not None:
        node.retry_count = payload.retry_count
    if payload.error_type is not None:
        node.error_type = payload.error_type.strip() or None
    if payload.error_message is not None:
        node.error_message = _safe_error(payload.error_message)
    return True


def _aggregate_case_status(case_run: CaseRun) -> None:
    if not case_run.step_runs or RunNodeStatus(case_run.status) in TERMINAL_NODE_STATUSES:
        return
    statuses = [RunNodeStatus(item.status) for item in case_run.step_runs]
    if any(status == RunNodeStatus.CANCELLING for status in statuses):
        desired = RunNodeStatus.CANCELLING
    elif any(status in {RunNodeStatus.ASSIGNED, RunNodeStatus.RUNNING} for status in statuses):
        desired = RunNodeStatus.RUNNING
    elif not all(status in TERMINAL_NODE_STATUSES for status in statuses):
        desired = RunNodeStatus.CREATED
    elif any(status == RunNodeStatus.CANCELLED for status in statuses):
        desired = RunNodeStatus.CANCELLED
    elif any(status == RunNodeStatus.TIMEOUT for status in statuses):
        desired = RunNodeStatus.TIMEOUT
    elif any(status == RunNodeStatus.FAILED for status in statuses):
        desired = RunNodeStatus.FAILED
    elif any(status == RunNodeStatus.REVIEW for status in statuses):
        desired = RunNodeStatus.REVIEW
    else:
        desired = RunNodeStatus.SUCCESS
    current = RunNodeStatus(case_run.status)
    if current == desired:
        return
    if desired == RunNodeStatus.CREATED and current != RunNodeStatus.CREATED:
        return
    case_run.status = desired.value
    if desired == RunNodeStatus.RUNNING and case_run.started_at is None:
        case_run.started_at = utc_now_naive()
    if desired in TERMINAL_NODE_STATUSES:
        case_run.ended_at = case_run.ended_at or utc_now_naive()
        case_run.duration = sum(item.duration for item in case_run.step_runs)


def _recompute_summary(session: Session, run: TestRun) -> None:
    case_runs = list(
        session.scalars(
            select(CaseRun).where(CaseRun.run_id == run.id).order_by(CaseRun.sequence_no.asc())
        ).all()
    )
    run.total = len(case_runs)
    run.pass_count = sum(item.status == RunNodeStatus.SUCCESS.value for item in case_runs)
    run.fail_count = sum(item.status == RunNodeStatus.FAILED.value for item in case_runs)
    run.review_count = sum(item.status == RunNodeStatus.REVIEW.value for item in case_runs)
    run.timeout_count = sum(item.status == RunNodeStatus.TIMEOUT.value for item in case_runs)


def _authorize_child_update(
    session: Session,
    run: TestRun,
    *,
    user: CurrentUser | None,
    runner_credential: str | None,
) -> None:
    if user is not None:
        raise AuthenticationError("CaseRun/StepRun 执行状态只能由 Runner 上报")
    if runner_credential is not None:
        _authenticate_runner_for_run(session, run, runner_credential)
    else:
        raise AuthenticationError("Run 状态更新需要认证")
    if RunStatus(run.status) not in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        raise RunStateConflictError("Run 尚未进入可执行状态，不能上报子任务状态")


def update_case_run_status(
    session: Session,
    run_id: str,
    case_run_id: int,
    payload: NodeStatusUpdateRequest,
    *,
    user: CurrentUser | None = None,
    runner_credential: str | None = None,
) -> RunDetailResponse:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    _authorize_child_update(session, run, user=user, runner_credential=runner_credential)
    case_run = session.scalar(
        select(CaseRun).where(CaseRun.id == case_run_id, CaseRun.run_id == run_id).with_for_update()
    )
    if case_run is None:
        raise ResourceNotFoundError("CaseRun 不存在")
    if case_run.step_runs and RunNodeStatus(case_run.status) != payload.status:
        raise RunStateConflictError("包含 StepRun 的 CaseRun 状态只能由服务端聚合")
    if payload.status in TERMINAL_NODE_STATUSES and any(
        RunNodeStatus(item.status) not in TERMINAL_NODE_STATUSES for item in case_run.step_runs
    ):
        raise RunStateConflictError("CaseRun 仍有未结束的 StepRun")
    if _set_node_status(case_run, payload.status, payload):
        _recompute_summary(session, run)
        session.commit()
    detail = get_run_with_children(session, run_id)
    if detail is None:
        raise ResourceNotFoundError("Run 不存在")
    return _run_detail(session, detail)


def update_step_run_status(
    session: Session,
    run_id: str,
    case_run_id: int,
    step_run_id: int,
    payload: NodeStatusUpdateRequest,
    *,
    user: CurrentUser | None = None,
    runner_credential: str | None = None,
) -> RunDetailResponse:
    run = get_run(session, run_id, for_update=True)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    _authorize_child_update(session, run, user=user, runner_credential=runner_credential)
    case_run = session.scalar(
        select(CaseRun).where(CaseRun.id == case_run_id, CaseRun.run_id == run_id).with_for_update()
    )
    if case_run is None:
        raise ResourceNotFoundError("CaseRun 不存在")
    step_run = session.scalar(
        select(StepRun)
        .where(StepRun.id == step_run_id, StepRun.case_run_id == case_run_id)
        .with_for_update()
    )
    if step_run is None:
        raise ResourceNotFoundError("StepRun 不存在")
    if (
        RunNodeStatus(case_run.status) in TERMINAL_NODE_STATUSES
        and RunNodeStatus(step_run.status) != payload.status
    ):
        raise RunStateConflictError("CaseRun 已进入终态，不能再更新 StepRun")
    if _set_node_status(step_run, payload.status, payload):
        _aggregate_case_status(case_run)
        _recompute_summary(session, run)
        session.commit()
    detail = get_run_with_children(session, run_id)
    if detail is None:
        raise ResourceNotFoundError("Run 不存在")
    return _run_detail(session, detail)


def list_run_case_runs(session: Session, user: CurrentUser, run_id: str) -> CaseRunListResponse:
    run = get_run(session, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    items = list_case_runs(session, run_id)
    return CaseRunListResponse(
        items=[CaseRunResponse.model_validate(item) for item in items], total=len(items)
    )


def list_case_run_steps(
    session: Session, user: CurrentUser, run_id: str, case_run_id: int
) -> StepRunListResponse:
    run = get_run(session, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    case_run = session.scalar(
        select(CaseRun).where(CaseRun.id == case_run_id, CaseRun.run_id == run_id)
    )
    if case_run is None:
        raise ResourceNotFoundError("CaseRun 不存在")
    items = list_step_runs(session, case_run_id)
    return StepRunListResponse(
        items=[StepRunResponse.model_validate(item) for item in items], total=len(items)
    )
