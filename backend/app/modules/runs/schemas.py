import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.resource_registry.schemas import CleanupConfig, validate_secret_free_payload
from app.modules.runners.schemas import RunnerCapabilityName, RunnerSlotType
from app.modules.runs.enums import (
    DispatchOutboxStatus,
    RunNodeStatus,
    RunStatus,
    RunTriggerType,
    RunType,
)
from app.modules.test_cases.schemas import (
    ApiRequestTemplate,
    AssertionResult,
    RuntimeResponseSnapshot,
)

_RUNTIME_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_RESERVED_RUNTIME_VARIABLES = frozenset(
    {
        "run_id",
        "project_id",
        "environment_id",
        "case_id",
        "scenario_id",
        "web_case_id",
        "dataset_id",
        "dataset_version_id",
        "row_index",
    }
)
_SENSITIVE_RUNTIME_MARKERS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credential",
)


def validate_runtime_variables(values: dict[str, Any]) -> dict[str, Any]:
    """Keep user supplied runtime variables bounded and free of credentials."""

    if len(values) > 50:
        raise ValueError("runtime_variables 最多包含 50 个变量")
    normalized: dict[str, Any] = {}
    for raw_name, value in values.items():
        name = raw_name.strip()
        if not _RUNTIME_VARIABLE_NAME.fullmatch(name):
            raise ValueError("runtime_variables 变量名格式无效")
        lowered = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if name in _RESERVED_RUNTIME_VARIABLES:
            raise ValueError(f"runtime_variables 不能覆盖保留变量：{name}")
        if any(marker in lowered for marker in _SENSITIVE_RUNTIME_MARKERS):
            raise ValueError(f"runtime_variables 不允许包含敏感变量：{name}")
        normalized[name] = value
    validate_secret_free_payload(normalized)
    encoded = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    if len(encoded) > 64_000:
        raise ValueError("runtime_variables 不能超过 64KB")
    return normalized


class RunTaskEnvelope(BaseModel):
    """The exact V1 task body defined by contracts/run-task-envelope-v1.schema.json."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    run_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    runner_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    run_type: RunType
    required_slot_type: RunnerSlotType
    attempt: int = Field(ge=1, le=10)
    enqueued_at: datetime

    @field_serializer("enqueued_at", when_used="json")
    def serialize_enqueued_at(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    environment_id: int = Field(gt=0)
    runner_id: str = Field(min_length=1, max_length=64)
    run_type: RunType
    case_id: int | None = Field(default=None, gt=0)
    case_version_id: int | None = Field(default=None, gt=0)
    scenario_id: int | None = Field(default=None, gt=0)
    scenario_version_id: int | None = Field(default=None, gt=0)
    web_case_id: int | None = Field(default=None, gt=0)
    web_case_version_id: int | None = Field(default=None, gt=0)
    trigger_type: RunTriggerType = RunTriggerType.MANUAL
    required_capabilities: list[RunnerCapabilityName] = Field(default_factory=list, max_length=6)
    required_tags: list[str] = Field(default_factory=list, max_length=20)
    required_slot_type: RunnerSlotType = RunnerSlotType.API
    required_slot_count: int = Field(default=1, ge=1, le=10000)
    total_timeout_ms: int | None = Field(default=None, ge=1000, le=86_400_000)
    runtime_variables: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runner_id")
    @classmethod
    def normalize_runner_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("required_tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = value.strip().lower()
            if not tag or len(tag) > 64:
                raise ValueError("required_tags 包含无效 Tag")
            if not all(character.isalnum() or character in "_.-" for character in tag):
                raise ValueError("required_tags 仅支持字母、数字、下划线、短横线和点")
            if tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized

    @field_validator("runtime_variables")
    @classmethod
    def validate_variables(cls, values: dict[str, Any]) -> dict[str, Any]:
        return validate_runtime_variables(values)

    @model_validator(mode="after")
    def validate_target(self) -> "RunCreateRequest":
        if self.run_type == RunType.API_CASE:
            if (
                self.case_id is None
                or self.scenario_id is not None
                or self.scenario_version_id is not None
                or self.web_case_id is not None
                or self.web_case_version_id is not None
            ):
                raise ValueError("API_CASE Run 必须且只能指定 case_id/case_version_id")
        elif self.run_type == RunType.SCENARIO and (
            self.scenario_id is None
            or self.case_id is not None
            or self.case_version_id is not None
            or self.web_case_id is not None
            or self.web_case_version_id is not None
        ):
            raise ValueError("SCENARIO Run 必须且只能指定 scenario_id/scenario_version_id")
        elif self.run_type == RunType.WEB_CASE and (
            self.web_case_id is None
            or self.case_id is not None
            or self.case_version_id is not None
            or self.scenario_id is not None
            or self.scenario_version_id is not None
        ):
            raise ValueError("WEB_CASE Run 必须且只能指定 web_case_id/web_case_version_id")
        return self


class RunValidationIssue(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    field: str | None = Field(default=None, max_length=100)


class RunValidationResponse(BaseModel):
    valid: bool
    issues: list[RunValidationIssue]
    run_type: RunType
    resolved_case_version_id: int | None = None
    resolved_scenario_version_id: int | None = None
    resolved_web_case_version_id: int | None = None
    required_capabilities: list[RunnerCapabilityName] = Field(default_factory=list)


class RunDispatchResponse(BaseModel):
    run_id: str
    run_status: RunStatus
    outbox_status: DispatchOutboxStatus
    message_id: str
    routing_key: str
    attempt_count: int
    last_error: str | None
    published_at: datetime | None

    @field_serializer("published_at", when_used="json")
    def serialize_published_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class RunBatchDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    run_ids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("run_ids")
    @classmethod
    def normalize_run_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            run_id = value.strip()
            if not run_id or len(run_id) > 128:
                raise ValueError("run_ids 包含无效运行标识")
            if run_id in seen:
                raise ValueError("run_ids 不能包含重复运行")
            normalized.append(run_id)
            seen.add(run_id)
        return normalized


class RunBatchDispatchItemResponse(BaseModel):
    run_id: str
    dispatched: bool
    run_status: RunStatus | None = None
    outbox_status: DispatchOutboxStatus | None = None
    error_code: str | None = None
    message: str


class RunBatchDispatchResponse(BaseModel):
    requested: int
    dispatched: int
    failed: int
    items: list[RunBatchDispatchItemResponse]


class RunClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class RunClaimResponse(BaseModel):
    run_id: str
    message_id: str
    runner_id: str
    status: Literal[RunStatus.ASSIGNED, RunStatus.CANCELLED]
    claimed_at: datetime | None
    idempotent: bool

    @field_serializer("claimed_at", when_used="json")
    def serialize_claimed_at(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class ExecutionCancellationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    case_run_id: int
    status: RunStatus
    cancellation_requested: bool
    force_stop_requested: bool


class ExecutionOutcome(StrEnum):
    HTTP_RESPONSE = "HTTP_RESPONSE"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ExecutionResponseSnapshot(RuntimeResponseSnapshot):
    model_config = ConfigDict(extra="forbid")

    headers: dict[str, str] = Field(default_factory=dict, max_length=100)
    cookies: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("headers", "cookies")
    @classmethod
    def validate_header_values(cls, values: dict[str, str]) -> dict[str, str]:
        for name, value in values.items():
            if len(name) > 255:
                raise ValueError("响应 Header/Cookie 名称不能超过 255 个字符")
            if len(value) > 10_000:
                raise ValueError("响应 Header/Cookie 值不能超过 10000 个字符")
        return values

    @model_validator(mode="after")
    def validate_body_size(self) -> "ExecutionResponseSnapshot":
        body = json.dumps(
            {"json_body": self.json_body, "text": self.text},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        if len(body) > 2_000_000:
            raise ValueError("响应体不能超过 2MB")
        return self


class ScenarioExecutionSecret(BaseModel):
    """Only returned by the Runner-bound HTTPS plan; repr stays masked."""

    model_config = ConfigDict(extra="forbid")

    secret_id: int = Field(gt=0)
    value: SecretStr

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: SecretStr) -> str:
        return value.get_secret_value()


class ScenarioSqlConnectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int = Field(gt=0)
    db_type: Literal["MYSQL"] = "MYSQL"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65_535)
    database_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password_secret_id: int = Field(gt=0)
    ssl_enabled: bool = False


class ExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    case_version_id: int
    request: ApiRequestTemplate
    total_timeout_ms: int = Field(ge=1000, le=86_400_000)
    initial_context: dict[str, Any] = Field(
        default_factory=dict,
        max_length=200,
        exclude_if=lambda value: not value,
    )
    pre_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=100,
        exclude_if=lambda value: not value,
        repr=False,
    )
    extractors: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=200,
        exclude_if=lambda value: not value,
    )
    post_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=100,
        exclude_if=lambda value: not value,
    )
    sql_connections: list[ScenarioSqlConnectionPlan] = Field(
        default_factory=list,
        max_length=100,
        exclude_if=lambda value: not value,
    )
    secrets: list[ScenarioExecutionSecret] = Field(
        default_factory=list,
        max_length=100,
        exclude_if=lambda value: not value,
    )
    cleanups: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=100,
        exclude_if=lambda value: not value,
    )
    defer_cleanup_until_assertions: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
    )


class ScenarioExecutionNodePlan(BaseModel):
    """固定 Scenario 版本的无状态节点执行数据。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    node_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    timeout_ms: int = Field(ge=100, le=600_000)
    failure_policy: str | None = Field(default=None, max_length=32)
    config: dict = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def validate_config_size(self) -> "ScenarioExecutionNodePlan":
        encoded = json.dumps(self.config, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) > 64_000:
            raise ValueError("Scenario 节点执行配置不能超过 64KB")
        return self


class ScenarioExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    scenario_id: int
    scenario_version_id: int
    total_timeout_ms: int = Field(ge=1000, le=86_400_000)
    initial_context: dict[str, object] = Field(default_factory=dict, max_length=200)
    settings: dict = Field(max_length=20)
    nodes: list[ScenarioExecutionNodePlan] = Field(min_length=2, max_length=500)
    sql_connections: list[ScenarioSqlConnectionPlan] = Field(default_factory=list, max_length=100)
    secrets: list[ScenarioExecutionSecret] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_plan_size(self) -> "ScenarioExecutionPlanResponse":
        try:
            validate_secret_free_payload(self.initial_context)
        except (TypeError, ValueError) as exc:
            raise ValueError("Scenario initial_context 不能包含敏感变量") from exc
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("Scenario 执行计划不能超过 2MB")
        return self


class ExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    case_run_id: int = Field(gt=0)


class ExecutionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: Literal[RunStatus.RUNNING, RunStatus.CANCELLING]
    started_at: datetime | None
    idempotent: bool

    @field_serializer("started_at", when_used="json")
    def serialize_started_at(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class ScenarioExecutionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: Literal[RunStatus.RUNNING, RunStatus.CANCELLING]
    started_at: datetime | None
    idempotent: bool

    @field_serializer("started_at", when_used="json")
    def serialize_started_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


_SCENARIO_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|https?://[^\s,;]+|(?:password|passwd|token|"
    r"access[_-]?token|refresh[_-]?token|authorization|cookie|set-cookie|secret|"
    r"credential|api[_-]?key)\s*[:=]\s*[^\s,;]+)"
)


def _sanitize_scenario_text(value: object) -> str:
    return _SCENARIO_SENSITIVE_TEXT.sub("<redacted>", str(value).strip())[:500]


class ScenarioExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    status: Literal[
        "SUCCESS", "PASSED", "FAILED", "REVIEW", "SKIPPED", "TIMEOUT", "CANCELLED"
    ]
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    retry_count: int = Field(default=0, ge=0, le=3)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)

    @field_validator("error_type", "error_message", mode="before")
    @classmethod
    def sanitize_error_text(cls, value: object) -> object:
        return None if value is None else _sanitize_scenario_text(value)


class ScenarioExecutionOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ScenarioExecutionCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    case_run_id: int = Field(gt=0)
    outcome: ScenarioExecutionOutcome
    traces: list[ScenarioExecutionTrace] = Field(default_factory=list, max_length=500)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    evaluation_token: str | None = Field(default=None, min_length=1, max_length=64_000, repr=False)

    @field_validator("error_type", "error_message", mode="before")
    @classmethod
    def sanitize_error_text(cls, value: object) -> object:
        return None if value is None else _sanitize_scenario_text(value)


class ScenarioExecutionCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: ScenarioExecutionOutcome
    run_status: Literal[
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    ]
    case_run_status: Literal[
        RunNodeStatus.SUCCESS,
        RunNodeStatus.FAILED,
        RunNodeStatus.REVIEW,
        RunNodeStatus.TIMEOUT,
        RunNodeStatus.CANCELLED,
    ]
    traces: list[ScenarioExecutionTrace]
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    error_type: str | None
    error_message: str | None
    completed_at: datetime
    idempotent: bool

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class ScenarioExecutionEvaluationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    response: ExecutionResponseSnapshot


class ScenarioExecutionEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    case_run_id: int = Field(gt=0)
    evaluations: list[ScenarioExecutionEvaluationItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_nodes(self) -> "ScenarioExecutionEvaluationRequest":
        node_ids = [item.node_id for item in self.evaluations]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Scenario AI evaluation node_id 不能重复")
        return self


class ScenarioExecutionEvaluationNodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: Literal["PASS", "FAIL", "REVIEW"]


class ScenarioExecutionEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: Literal["PASS", "FAIL", "REVIEW"]
    cleanup_outcome: Literal["SUCCESS", "FAILURE"]
    assertion_results: list[AssertionResult]
    node_results: list[ScenarioExecutionEvaluationNodeResult]
    evaluation_token: str = Field(min_length=1, max_length=64_000, repr=False)


class WebExecutionOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class WebLocatorCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=2000)
    priority: int = Field(ge=1, le=20)


class WebLocatorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_version_id: int | None = Field(default=None, gt=0)
    candidates: list[WebLocatorCandidate] = Field(min_length=1, max_length=20)


class WebExecutionActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=32)
    timeout_ms: int = Field(ge=100, le=600000)
    failure_policy: str = Field(min_length=1, max_length=32)
    url: str | None = Field(default=None, max_length=2048)
    locator: WebLocatorPlan | None = None
    value: str | None = Field(default=None, max_length=1_400_000, repr=False)
    key: str | None = Field(default=None, max_length=256)


class WebExecutionAssertionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    timeout_ms: int = Field(ge=100, le=600000)
    locator: WebLocatorPlan | None = None
    expected: str | None = Field(default=None, max_length=1_400_000, repr=False)
    key: str | None = Field(default=None, max_length=256)
    prompt_id: int | None = Field(default=None, gt=0)
    criteria: str | None = Field(default=None, min_length=1, max_length=4000)
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)


class WebExecutionSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    value: SecretStr

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: SecretStr) -> str:
        return value.get_secret_value()


class WebExecutionProxyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=256, repr=False)
    password: str | None = Field(default=None, max_length=256, repr=False)


class WebExecutionBrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_width: int = Field(ge=320, le=7680)
    window_height: int = Field(ge=240, le=4320)
    language: str | None = Field(default=None, max_length=35)
    user_agent: str | None = Field(default=None, max_length=512, repr=False)
    proxy: WebExecutionProxyConfig | None = None
    download_path: str | None = Field(default=None, max_length=128)


class WebSessionConditionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["URL_EQUALS", "URL_CONTAINS", "LOCATOR_VISIBLE", "LOCATOR_HIDDEN"]
    value: str | None = Field(default=None, max_length=2048)
    locator: WebLocatorPlan | None = None


class WebSessionRecoveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_web_case_id: int = Field(gt=0)
    login_web_case_version_id: int = Field(gt=0)
    force_refresh: bool
    expiry_condition: WebSessionConditionPlan
    success_condition: WebSessionConditionPlan
    start_url: str = Field(min_length=1, max_length=2048)
    actions: list[WebExecutionActionPlan] = Field(min_length=1, max_length=200)
    assertions: list[WebExecutionAssertionPlan] = Field(default_factory=list, max_length=100)


class WebSessionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: int = Field(gt=0)
    revision: int = Field(gt=0)
    storage_state_fingerprint: str = Field(min_length=64, max_length=64, repr=False)
    storage_state: dict[str, Any] | None = Field(default=None, max_length=100, repr=False)
    recovery: WebSessionRecoveryPlan | None = None


class WebExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    web_case_id: int
    web_case_version_id: int
    start_url: str = Field(min_length=1, max_length=2048)
    browser: Literal["CHROME"]
    headless: bool
    browser_config: WebExecutionBrowserConfig
    total_timeout_ms: int = Field(ge=1000, le=86_400_000)
    initial_context: dict[str, Any] = Field(default_factory=dict, max_length=200)
    actions: list[WebExecutionActionPlan] = Field(min_length=1, max_length=200)
    assertions: list[WebExecutionAssertionPlan] = Field(default_factory=list, max_length=100)
    session: WebSessionPlan | None = None
    secrets: list[WebExecutionSecret] = Field(default_factory=list, max_length=100, repr=False)

    @model_validator(mode="after")
    def validate_plan_size(self) -> "WebExecutionPlanResponse":
        try:
            validate_secret_free_payload(
                self.model_dump(mode="python", exclude={"session", "secrets"}),
                allow_auth_reference=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Web 执行计划不能包含明文敏感数据") from exc
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("Web 执行计划不能超过 2MB")
        return self


class WebExecutionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: Literal[RunStatus.RUNNING, RunStatus.CANCELLING]
    started_at: datetime | None
    idempotent: bool

    @field_serializer("started_at", when_used="json")
    def serialize_started_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebLocatorAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["css", "xpath", "text", "role", "label", "placeholder", "test_id"]
    priority: int = Field(ge=1, le=20)
    status: Literal["NOT_FOUND", "ACTION_FAILED", "SUCCESS"]


_HEALING_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|bearer|cookie|token|password|passwd|secret|"
    r"credential|api[-_]?key)"
)
_HEALING_LOCATING_FIELDS = (
    "role",
    "id",
    "name",
    "aria_label",
    "placeholder",
    "data_testid",
    "title",
)


def _validate_healing_text(
    value: str | None,
    label: str,
    *,
    allow_empty: bool = False,
    require_trimmed: bool = False,
) -> str | None:
    if value is None:
        raise ValueError(f"{label} 不能为空")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{label} 不能包含换行")
    normalized = value.strip()
    if require_trimmed and normalized != value:
        raise ValueError(f"{label} 必须已 trim")
    if not normalized:
        if allow_empty and not normalized:
            return ""
        raise ValueError(f"{label} 不能为空且不能包含换行")
    if _HEALING_SENSITIVE_TEXT.search(normalized):
        raise ValueError(f"{label} 不能包含凭据")
    return normalized


class WebHealingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)
    id: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)
    name: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)
    aria_label: str | None = Field(
        default=None,
        alias="aria-label",
        max_length=200,
        exclude_if=lambda value: value is None,
    )
    placeholder: str | None = Field(
        default=None, max_length=200, exclude_if=lambda value: value is None
    )
    data_testid: str | None = Field(
        default=None,
        alias="data-testid",
        max_length=200,
        exclude_if=lambda value: value is None,
    )
    type: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)
    title: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)

    @field_validator(
        "tag",
        "role",
        "id",
        "name",
        "aria_label",
        "placeholder",
        "data_testid",
        "type",
        "title",
    )
    @classmethod
    def normalize_candidate_text(cls, value: str | None, info: Any) -> str | None:
        return _validate_healing_text(value, f"dom_candidates.{info.field_name}")

    @model_validator(mode="after")
    def require_locator_field(self) -> "WebHealingCandidate":
        if not any(getattr(self, field) for field in _HEALING_LOCATING_FIELDS):
            raise ValueError("dom_candidates 至少需要一个定位字段")
        return self


class WebHealingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    trigger: Literal["ALL_LOCATORS_FAILED"] = "ALL_LOCATORS_FAILED"
    element_version_id: int | None = Field(default=None, gt=0)
    page_url: str | None = Field(default=None, max_length=2048)
    page_title: str = Field(max_length=200)
    dom_candidates: list[WebHealingCandidate] = Field(default_factory=list, max_length=40)

    @field_validator("page_url")
    @classmethod
    def validate_page_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("healing_context.page_url 无效")
        try:
            parsed = urlsplit(normalized)
            hostname = parsed.hostname
        except ValueError as exc:
            raise ValueError("healing_context.page_url 无效") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not hostname
            or "@" in parsed.netloc
            or parsed.query
            or parsed.fragment
            or "?" in normalized
            or "#" in normalized
        ):
            raise ValueError("healing_context.page_url 只允许无查询的 http(s) URL")
        return normalized

    @field_validator("page_title")
    @classmethod
    def normalize_page_title(cls, value: str | None) -> str | None:
        return _validate_healing_text(
            value,
            "healing_context.page_title",
            allow_empty=True,
            require_trimmed=True,
        )

    @model_validator(mode="after")
    def validate_context_size(self) -> "WebHealingContext":
        encoded = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("healing_context 不能超过 64KB")
        return self


class WebExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    status: Literal["SUCCESS", "FAILED", "REVIEW", "SKIPPED", "TIMEOUT", "CANCELLED"]
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    locator_attempts: list[WebLocatorAttempt] = Field(default_factory=list, max_length=20)
    healing_context: WebHealingContext | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("error_type", "error_message", mode="before")
    @classmethod
    def sanitize_error_text(cls, value: object) -> object:
        if value is None:
            return None
        text = re.sub(
            r"(?i)(?:bearer\s+[^\s,;]+|https?://[^\s,;]+|(?:password|token|authorization|cookie|secret|credential|api[_-]?key)\s*[:=]\s*[^\s,;]+)",
            "<redacted>",
            str(value).strip(),
        )
        return text[:500]

    @model_validator(mode="after")
    def validate_healing_context_semantics(self) -> "WebExecutionTrace":
        if self.healing_context is not None and not (
            self.status == "FAILED"
            and self.error_type in {"WEB_LOCATOR_NOT_FOUND", "ASSERTION_FAILED"}
            and self.locator_attempts
            and all(item.status == "NOT_FOUND" for item in self.locator_attempts)
        ):
            raise ValueError(
                "healing_context 仅允许在定位失败且全部 Locator 候选 NOT_FOUND 时提供"
            )
        return self


class WebExecutionCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    case_run_id: int = Field(gt=0)
    outcome: WebExecutionOutcome
    traces: list[WebExecutionTrace] = Field(default_factory=list, max_length=300)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    refreshed_session: "WebRefreshedSession | None" = Field(default=None, repr=False)
    session_recovery: "WebSessionRecoveryAudit | None" = None
    evaluation_token: str | None = Field(default=None, min_length=1, max_length=64_000, repr=False)

    @model_validator(mode="after")
    def validate_outcome(self) -> "WebExecutionCompleteRequest":
        if self.outcome == WebExecutionOutcome.CANCELLED:
            if self.traces or self.error_type != "CANCEL_REQUESTED" or not self.error_message:
                raise ValueError("CANCELLED 必须只携带 CANCEL_REQUESTED 和安全消息")
        elif self.outcome == WebExecutionOutcome.SUCCESS:
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("SUCCESS 不能携带错误字段")
        else:
            if not self.error_type or not self.error_message:
                raise ValueError("失败类 outcome 必须携带 error_type 和 error_message")
        if self.refreshed_session is not None and (
            self.session_recovery is None or self.session_recovery.status != "SUCCESS"
        ):
            raise ValueError("刷新 Storage State 必须携带成功的 Session 恢复审计")
        if (
            self.session_recovery is not None
            and self.session_recovery.status == "SUCCESS"
            and self.refreshed_session is None
        ):
            raise ValueError("成功的 Session 恢复必须回传刷新后的 Storage State")
        return self


class WebRefreshedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: int = Field(gt=0)
    expected_revision: int = Field(gt=0)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    storage_state: dict[str, Any] = Field(min_length=1, max_length=100, repr=False)

    @model_validator(mode="after")
    def validate_size(self) -> "WebRefreshedSession":
        encoded = json.dumps(self.storage_state, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("刷新后的 Storage State 不能超过 1MB")
        return self


class WebSessionRecoveryAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["NOT_NEEDED", "SUCCESS", "FAILED"]
    reason: Literal["PROFILE_EXPIRED", "EXPIRY_CONDITION", "NOT_EXPIRED"]
    login_web_case_version_id: int = Field(gt=0)
    duration_ms: int = Field(ge=0, le=86_400_000)
    error_type: str | None = Field(default=None, max_length=100)
    persistence_status: Literal["UPDATED", "CONFLICT", "NOT_ATTEMPTED"] | None = None


WebExecutionCompleteRequest.model_rebuild()


class WebExecutionCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: WebExecutionOutcome
    run_status: Literal[RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.TIMEOUT, RunStatus.CANCELLED]
    case_run_status: Literal[
        RunNodeStatus.SUCCESS,
        RunNodeStatus.FAILED,
        RunNodeStatus.REVIEW,
        RunNodeStatus.TIMEOUT,
        RunNodeStatus.CANCELLED,
    ]
    traces: list[WebExecutionTrace]
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    error_type: str | None
    error_message: str | None
    session_recovery: WebSessionRecoveryAudit | None = None
    completed_at: datetime
    idempotent: bool

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebExecutionEvaluationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^assertion_[1-9][0-9]{0,2}$", max_length=16)
    response: ExecutionResponseSnapshot = Field(repr=False)


class WebExecutionEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    case_run_id: int = Field(gt=0)
    evaluations: list[WebExecutionEvaluationItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_nodes(self) -> "WebExecutionEvaluationRequest":
        node_ids = [item.node_id for item in self.evaluations]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Web AI evaluation node_id 不能重复")
        return self


class WebExecutionEvaluationNodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: Literal["PASS", "FAIL", "REVIEW"]


class WebExecutionEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: Literal["PASS", "FAIL", "REVIEW"]
    assertion_results: list[AssertionResult]
    node_results: list[WebExecutionEvaluationNodeResult]
    evaluation_token: str = Field(min_length=1, max_length=64_000, repr=False)


class ExecutionResourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_sequence: int = Field(gt=0, le=100)
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    resource_type: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"
    )
    resource_id: str = Field(min_length=1, max_length=512)
    cleanup: CleanupConfig
    status: Literal["PENDING", "CLEANED", "FAILED", "SKIPPED"]
    attempt_count: int = Field(ge=0, le=2)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=1000)


class ExecutionEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    case_run_id: int = Field(gt=0)
    response: ExecutionResponseSnapshot


class ExecutionEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: Literal["PASS", "FAIL", "REVIEW"]
    cleanup_outcome: Literal["SUCCESS", "FAILURE"]
    assertion_results: list[AssertionResult]
    evaluation_token: str = Field(min_length=1, max_length=64_000, repr=False)


class ExecutionActionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(gt=0, le=1000)
    phase: Literal["PRE", "AUTH_REFRESH", "EXTRACTOR", "POST"]
    type: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=255)
    status: Literal["SUCCESS", "FAILED", "SKIPPED"]
    duration_ms: int = Field(ge=0, le=86_400_000)
    error_type: str | None = Field(default=None, max_length=100)


class ExecutionActionAuditResponse(ExecutionActionTrace):
    case_run_id: int = Field(gt=0)


class ExecutionCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    case_run_id: int = Field(gt=0)
    outcome: ExecutionOutcome
    response: ExecutionResponseSnapshot | None = None
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    retry_count: int = Field(default=0, ge=0, le=3)
    resources: list[ExecutionResourceResult] = Field(default_factory=list, max_length=100)
    action_traces: list[ExecutionActionTrace] = Field(default_factory=list, max_length=500)
    evaluation_token: str | None = Field(default=None, min_length=1, max_length=64_000, repr=False)

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "ExecutionCompleteRequest":
        sequences = [item.registration_sequence for item in self.resources]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("resources registration_sequence 必须连续且从 1 开始")
        for phase in ("PRE", "AUTH_REFRESH", "EXTRACTOR", "POST"):
            phase_traces = [item for item in self.action_traces if item.phase == phase]
            phase_sequences = [item.sequence for item in phase_traces]
            if phase_sequences != list(range(1, len(phase_sequences) + 1)):
                raise ValueError(f"{phase} action_traces sequence 必须连续且从 1 开始")
        for trace in self.action_traces:
            if trace.phase == "AUTH_REFRESH" and trace.type != "GET_TOKEN":
                raise ValueError("AUTH_REFRESH action_trace 必须为 GET_TOKEN")
            if trace.status == "FAILED" and not trace.error_type:
                raise ValueError("FAILED action_trace 必须提供 error_type")
            if trace.status != "FAILED" and trace.error_type is not None:
                raise ValueError("非 FAILED action_trace 不能提供 error_type")
        if (
            self.outcome != ExecutionOutcome.HTTP_RESPONSE
            and self.evaluation_token is not None
        ):
            raise ValueError("只有 HTTP_RESPONSE 可以提供 evaluation_token")
        if self.outcome == ExecutionOutcome.HTTP_RESPONSE:
            if self.response is None:
                raise ValueError("HTTP_RESPONSE 必须提供 response")
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("HTTP_RESPONSE 不能提供执行异常字段")
        elif self.outcome == ExecutionOutcome.CANCELLED:
            if self.response is not None:
                raise ValueError("CANCELLED 不能提供 response")
            if self.error_type not in {"CANCEL_REQUESTED", "FORCE_STOP_REQUESTED"}:
                raise ValueError(
                    "CANCELLED 的 error_type 必须为 CANCEL_REQUESTED 或 FORCE_STOP_REQUESTED"
                )
            if not self.error_message or not self.error_message.strip():
                raise ValueError("CANCELLED 必须提供 error_message")
        else:
            if self.response is not None:
                raise ValueError(f"{self.outcome.value} 不能提供 response")
            if (
                not self.error_type
                or not self.error_type.strip()
                or not self.error_message
                or not self.error_message.strip()
            ):
                raise ValueError(f"{self.outcome.value} 必须提供 error_type 和 error_message")
        return self


class ExecutionCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: ExecutionOutcome
    run_status: Literal[
        RunStatus.RUNNING,
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    ]
    case_run_status: Literal[
        RunNodeStatus.SUCCESS,
        RunNodeStatus.FAILED,
        RunNodeStatus.REVIEW,
        RunNodeStatus.TIMEOUT,
        RunNodeStatus.CANCELLED,
    ]
    retry_count: int
    assertion_results: list[AssertionResult]
    error_type: str | None
    error_message: str | None
    completed_at: datetime
    idempotent: bool

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class RunStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RunStatus
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)


class NodeStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RunNodeStatus
    duration: int | None = Field(default=None, ge=0, le=86_400_000)
    retry_count: int | None = Field(default=None, ge=0, le=100)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)


class StepRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_run_id: int
    sequence_no: int
    node_id: str
    step_name: str
    step_type: str
    status: RunNodeStatus
    started_at: datetime | None
    ended_at: datetime | None
    duration: int
    error_type: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("started_at", "ended_at", "created_at", "updated_at", when_used="json")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class CaseRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    sequence_no: int
    case_id: int | None
    case_version_id: int | None
    scenario_id: int | None
    scenario_version_id: int | None
    web_case_id: int | None
    web_case_version_id: int | None
    dataset_id: int | None
    dataset_version_id: int | None
    row_index: int | None
    parameter_snapshot: dict[str, Any] | None
    status: RunNodeStatus
    duration: int
    retry_count: int
    started_at: datetime | None
    ended_at: datetime | None
    error_type: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("started_at", "ended_at", "created_at", "updated_at", when_used="json")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class CaseRunDetailResponse(CaseRunResponse):
    step_runs: list[StepRunResponse]


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    run_code: str
    run_type: RunType
    project_id: int
    environment_id: int | None
    runner_id: str | None
    case_id: int | None
    case_version_id: int | None
    scenario_id: int | None
    scenario_version_id: int | None
    web_case_id: int | None
    web_case_version_id: int | None
    status: RunStatus
    trigger_type: RunTriggerType
    required_capabilities: list[RunnerCapabilityName]
    required_tags: list[str]
    required_slot_type: RunnerSlotType
    required_slot_count: int
    runtime_snapshot_id: str | None
    runtime_variables: dict[str, Any]
    total_timeout_ms: int | None
    effective_total_timeout_ms: int = Field(ge=1000, le=86_400_000)
    force_stop_requested_at: datetime | None
    force_stopped: bool
    started_at: datetime | None
    ended_at: datetime | None
    total: int
    pass_: int = Field(alias="pass")
    fail_: int = Field(alias="fail")
    review_: int = Field(alias="review")
    timeout_: int = Field(alias="timeout")
    error_type: str | None
    error_message: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "started_at",
        "ended_at",
        "force_stop_requested_at",
        "created_at",
        "updated_at",
        when_used="json",
    )
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class RunDetailResponse(RunResponse):
    case_runs: list[CaseRunDetailResponse]
    web_traces: list[WebExecutionTrace] = Field(default_factory=list, max_length=300)
    web_session_recoveries: list[WebSessionRecoveryAudit] = Field(
        default_factory=list, max_length=100
    )
    api_action_traces: list[ExecutionActionAuditResponse] = Field(
        default_factory=list, max_length=5000
    )


class RunListResponse(BaseModel):
    items: list[RunResponse]
    total: int
    page: int
    page_size: int


class RunEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=64, pattern=r"^\d+-\d+$")
    schema_version: Literal[1]
    event_type: str = Field(min_length=1, max_length=64)
    project_id: int
    run_id: str
    case_run_id: int | None = None
    step_run_id: int | None = None
    from_status: RunStatus | None = None
    to_status: RunStatus
    occurred_at: datetime
    total: int | None = Field(default=None, ge=0)
    pass_count: int | None = Field(default=None, ge=0)
    fail_count: int | None = Field(default=None, ge=0)
    review_count: int | None = Field(default=None, ge=0)
    timeout_count: int | None = Field(default=None, ge=0)

    @field_serializer("occurred_at", when_used="json")
    def serialize_occurred_at(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class RunEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunEventResponse]
    next_after_id: str | None = None
    has_more: bool
    source: Literal["REDIS_STREAM"] = "REDIS_STREAM"


class CaseRunListResponse(BaseModel):
    items: list[CaseRunResponse]
    total: int


class StepRunListResponse(BaseModel):
    items: list[StepRunResponse]
    total: int
