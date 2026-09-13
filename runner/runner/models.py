"""Runner 客户端内部模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RunnerIdentity:
    runner_id: str
    credential: str


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any


@dataclass(frozen=True)
class RegistrationResult:
    runner_id: str
    credential: str
    heartbeat_interval_seconds: int


@dataclass(frozen=True)
class HeartbeatResult:
    runner_id: str
    status: str
    heartbeat_interval_seconds: int
    server_time: datetime


@dataclass(frozen=True)
class ClaimResult:
    run_id: str
    message_id: str
    runner_id: str
    status: str
    claimed_at: datetime | None
    idempotent: bool


@dataclass(frozen=True)
class ExecutionCancellationResult:
    schema_version: int
    run_id: str
    message_id: str
    case_run_id: int
    status: str
    cancellation_requested: bool
    force_stop_requested: bool


@dataclass(frozen=True, repr=False)
class ExecutionPlanResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    case_version_id: int
    request: Any
    total_timeout_ms: int
    initial_context: Mapping[str, Any] | None = None
    pre_actions: tuple[Mapping[str, Any], ...] = ()
    extractors: tuple[Mapping[str, Any], ...] = ()
    post_actions: tuple[Mapping[str, Any], ...] = ()
    sql_connections: tuple[ScenarioSqlConnectionPlan, ...] = ()
    secrets: tuple[ScenarioExecutionSecret, ...] = ()
    cleanups: tuple[Mapping[str, Any], ...] = ()
    defer_cleanup_until_assertions: bool = False

    @property
    def has_pipeline(self) -> bool:
        return bool(self.pre_actions or self.extractors or self.post_actions or self.cleanups)

    def __repr__(self) -> str:
        return (
            "ExecutionPlanResult("
            f"run_id={self.run_id!r}, case_run_id={self.case_run_id!r}, "
            f"case_version_id={self.case_version_id!r}, "
            f"pre_actions={len(self.pre_actions)}, extractors={len(self.extractors)}, "
            f"post_actions={len(self.post_actions)}, cleanups={len(self.cleanups)}, "
            f"deferred_cleanup={self.defer_cleanup_until_assertions!r}, "
            "request=<redacted>, secrets=<redacted>)"
        )


@dataclass(frozen=True)
class ExecutionStartResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: str
    started_at: datetime | None
    idempotent: bool


@dataclass(frozen=True, repr=False)
class PerformanceExecutionPlanResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    request: Any
    concurrency: int
    iterations: int
    load_mode: str
    target_rps: float | None
    duration_seconds: int | None
    warmup_iterations: int
    request_timeout_ms: int
    total_timeout_ms: int
    target_type: str = "API_CASE"
    engine: str = "PYTHON_HTTP"
    step_stages: tuple[Mapping[str, int], ...] = ()
    cleanup_mode: str = "NONE"
    stream_config: Mapping[str, Any] = field(default_factory=dict)
    target_plan: Any | None = None

    def __repr__(self) -> str:
        return (
            "PerformanceExecutionPlanResult("
            f"run_id={self.run_id!r}, case_run_id={self.case_run_id!r}, "
            f"load_mode={self.load_mode!r}, concurrency={self.concurrency!r}, "
            f"iterations={self.iterations!r}, "
            f"engine={self.engine!r}, target_type={self.target_type!r}, "
            f"warmup_iterations={self.warmup_iterations!r}, request=<redacted>)"
        )


@dataclass(frozen=True)
class PerformanceCompleteResult:
    run_id: str
    status: str
    idempotent: bool


@dataclass(frozen=True, repr=False)
class ExecutionEvaluationResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: str
    cleanup_outcome: str
    assertion_results: list[Mapping[str, Any]]
    evaluation_token: str

    def __repr__(self) -> str:
        return (
            "ExecutionEvaluationResult("
            f"run_id={self.run_id!r}, case_run_id={self.case_run_id!r}, "
            f"assertion_status={self.assertion_status!r}, "
            f"cleanup_outcome={self.cleanup_outcome!r}, token=<redacted>)"
        )


@dataclass(frozen=True)
class ExecutionCompleteResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: str
    run_status: str
    case_run_status: str
    assertion_results: list[Mapping[str, Any]]
    error_type: str | None
    error_message: str | None
    completed_at: datetime
    idempotent: bool
    retry_count: int = 0


@dataclass(frozen=True, repr=False)
class ScenarioExecutionSecret:
    """Secret delivered only in memory for one Scenario execution."""

    secret_id: int
    value: str

    def __repr__(self) -> str:
        return f"ScenarioExecutionSecret(secret_id={self.secret_id}, value='[REDACTED]')"


@dataclass(frozen=True, repr=False)
class ScenarioNodePlan:
    node_id: str
    node_type: str
    name: str
    parent_id: str | None
    enabled: bool
    timeout_ms: int
    failure_policy: str | None
    config: Mapping[str, Any]

    def __repr__(self) -> str:
        return (
            "ScenarioNodePlan("
            f"node_id={self.node_id!r}, node_type={self.node_type!r}, "
            f"name={self.name!r}, parent_id={self.parent_id!r}, "
            f"enabled={self.enabled!r}, timeout_ms={self.timeout_ms!r}, "
            f"failure_policy={self.failure_policy!r}, config={{'[REDACTED]'}})"
        )


@dataclass(frozen=True, repr=False)
class ScenarioSqlConnectionPlan:
    connection_id: int
    db_type: str
    host: str
    port: int
    database_name: str
    username: str
    password_secret_id: int
    ssl_enabled: bool

    def __repr__(self) -> str:
        return (
            "ScenarioSqlConnectionPlan("
            f"connection_id={self.connection_id!r}, db_type={self.db_type!r}, "
            f"host={self.host!r}, port={self.port!r}, "
            f"database_name={self.database_name!r}, username={self.username!r}, "
            f"password_secret_id={self.password_secret_id!r}, "
            f"ssl_enabled={self.ssl_enabled!r})"
        )


@dataclass(frozen=True, repr=False)
class ScenarioExecutionPlanResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    scenario_id: int
    scenario_version_id: int
    total_timeout_ms: int
    initial_context: Mapping[str, Any]
    settings: Mapping[str, Any]
    nodes: tuple[ScenarioNodePlan, ...]
    sql_connections: tuple[ScenarioSqlConnectionPlan, ...]
    secrets: tuple[ScenarioExecutionSecret, ...]

    def __repr__(self) -> str:
        return (
            "ScenarioExecutionPlanResult("
            f"schema_version={self.schema_version!r}, run_id={self.run_id!r}, "
            f"message_id={self.message_id!r}, runner_id={self.runner_id!r}, "
            f"case_run_id={self.case_run_id!r}, scenario_id={self.scenario_id!r}, "
            f"scenario_version_id={self.scenario_version_id!r}, "
            f"total_timeout_ms={self.total_timeout_ms!r}, "
            f"initial_context={{'[REDACTED]'}}, settings={{'[REDACTED]'}}, "
            f"nodes={len(self.nodes)}, sql_connections={len(self.sql_connections)}, "
            f"secrets={len(self.secrets)})"
        )


@dataclass(frozen=True)
class ScenarioExecutionStartResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: str
    started_at: datetime | None
    idempotent: bool


@dataclass(frozen=True, repr=False)
class ScenarioExecutionEvaluationResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: str
    cleanup_outcome: str
    assertion_results: list[Mapping[str, Any]]
    node_results: list[Mapping[str, str]]
    evaluation_token: str

    def __repr__(self) -> str:
        return (
            "ScenarioExecutionEvaluationResult("
            f"run_id={self.run_id!r}, case_run_id={self.case_run_id!r}, "
            f"assertion_status={self.assertion_status!r}, "
            f"cleanup_outcome={self.cleanup_outcome!r}, token=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class ScenarioExecutionCompleteResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: str
    run_status: str
    case_run_status: str
    traces: list[Mapping[str, Any]]
    error_type: str | None
    error_message: str | None
    completed_at: datetime
    idempotent: bool
    assertion_results: list[Mapping[str, Any]] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            "ScenarioExecutionCompleteResult("
            f"run_id={self.run_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, case_run_id={self.case_run_id!r}, "
            f"outcome={self.outcome!r}, run_status={self.run_status!r}, "
            f"case_run_status={self.case_run_status!r}, traces={len(self.traces)}, "
            f"assertions={len(self.assertion_results)}, "
            f"error_type={self.error_type!r}, "
            f"error_message={('[REDACTED]' if self.error_message else None)!r}, "
            f"completed_at={self.completed_at!r}, idempotent={self.idempotent!r})"
        )


@dataclass(frozen=True, repr=False)
class WebLocatorCandidate:
    strategy: str
    value: str
    priority: int

    def __repr__(self) -> str:
        return f"WebLocatorCandidate(strategy={self.strategy!r}, priority={self.priority!r})"


@dataclass(frozen=True, repr=False)
class WebLocatorPlan:
    element_version_id: int | None
    candidates: tuple[WebLocatorCandidate, ...]

    def __repr__(self) -> str:
        return (
            "WebLocatorPlan("
            f"element_version_id={self.element_version_id!r}, "
            f"candidates={len(self.candidates)})"
        )


@dataclass(frozen=True, repr=False)
class WebExecutionActionPlan:
    type: str
    timeout_ms: int
    failure_policy: str
    url: str | None
    locator: WebLocatorPlan | None
    value: str | None
    key: str | None

    def __repr__(self) -> str:
        return (
            "WebExecutionActionPlan("
            f"type={self.type!r}, timeout_ms={self.timeout_ms!r}, "
            f"failure_policy={self.failure_policy!r}, locator={self.locator!r})"
        )


@dataclass(frozen=True, repr=False)
class WebExecutionAssertionPlan:
    type: str
    timeout_ms: int
    locator: WebLocatorPlan | None
    expected: str | None
    key: str | None = None
    prompt_id: int | None = None
    criteria: str | None = None
    confidence_threshold: float | None = None

    def __repr__(self) -> str:
        return f"WebExecutionAssertionPlan(type={self.type!r}, timeout_ms={self.timeout_ms!r})"


@dataclass(frozen=True, repr=False)
class WebExecutionSecret:
    name: str
    value: str

    def __repr__(self) -> str:
        return f"WebExecutionSecret(name={self.name!r}, value='[REDACTED]')"


@dataclass(frozen=True, repr=False)
class WebExecutionProxyConfig:
    server: str
    username: str | None = None
    password: str | None = None

    def __repr__(self) -> str:
        return "WebExecutionProxyConfig(server='[REDACTED]', credentials='[REDACTED]')"


@dataclass(frozen=True, repr=False)
class WebExecutionBrowserConfig:
    window_width: int = 1280
    window_height: int = 720
    language: str | None = None
    user_agent: str | None = None
    proxy: WebExecutionProxyConfig | None = None
    download_path: str | None = None

    def __repr__(self) -> str:
        return (
            "WebExecutionBrowserConfig("
            f"window={self.window_width}x{self.window_height}, "
            f"language={self.language!r}, user_agent='[REDACTED]', "
            f"proxy={self.proxy is not None}, download_path={self.download_path!r})"
        )


@dataclass(frozen=True, repr=False)
class WebSessionConditionPlan:
    type: str
    value: str | None
    locator: WebLocatorPlan | None


@dataclass(frozen=True, repr=False)
class WebSessionRecoveryPlan:
    login_web_case_id: int
    login_web_case_version_id: int
    force_refresh: bool
    expiry_condition: WebSessionConditionPlan
    success_condition: WebSessionConditionPlan
    start_url: str
    actions: tuple[WebExecutionActionPlan, ...]
    assertions: tuple[WebExecutionAssertionPlan, ...]

    def __repr__(self) -> str:
        return (
            "WebSessionRecoveryPlan("
            f"login_web_case_id={self.login_web_case_id!r}, "
            f"login_web_case_version_id={self.login_web_case_version_id!r}, "
            f"force_refresh={self.force_refresh!r}, actions={len(self.actions)}, "
            f"assertions={len(self.assertions)})"
        )


@dataclass(frozen=True, repr=False)
class WebSessionPlan:
    profile_id: int
    storage_state: Mapping[str, Any] | None
    revision: int = 1
    storage_state_fingerprint: str = "0" * 64
    recovery: WebSessionRecoveryPlan | None = None

    def __repr__(self) -> str:
        return (
            f"WebSessionPlan(profile_id={self.profile_id!r}, revision={self.revision!r}, "
            "storage_state='[REDACTED]', "
            f"recovery={self.recovery is not None})"
        )


@dataclass(frozen=True, repr=False)
class WebRecordingLocator:
    strategy: str
    value: str
    priority: int

    def __repr__(self) -> str:
        return f"WebRecordingLocator(strategy={self.strategy!r}, priority={self.priority!r})"


@dataclass(frozen=True, repr=False)
class WebRecordingSession:
    profile_id: int
    storage_state: Mapping[str, Any]

    def __repr__(self) -> str:
        return f"WebRecordingSession(profile_id={self.profile_id!r}, storage_state='[REDACTED]')"


@dataclass(frozen=True, repr=False)
class WebRecordingClaimResult:
    schema_version: int
    recording_id: str
    message_id: str
    runner_id: str
    status: str
    claimed_at: datetime | None
    idempotent: bool

    def __repr__(self) -> str:
        return (
            "WebRecordingClaimResult("
            f"recording_id={self.recording_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, status={self.status!r}, "
            f"idempotent={self.idempotent!r})"
        )


@dataclass(frozen=True, repr=False)
class WebRecordingExecutionPlanResult:
    schema_version: int
    task_type: str
    recording_id: str
    message_id: str
    runner_id: str
    project_id: int
    environment_id: int | None
    start_url: str
    browser: str
    headless: bool
    session: WebRecordingSession | None
    save_session: bool
    save_session_name: str | None
    save_session_expires_at: datetime | None

    def __repr__(self) -> str:
        return (
            "WebRecordingExecutionPlanResult("
            f"recording_id={self.recording_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, project_id={self.project_id!r}, "
            "start_url='[REDACTED]', session="
            f"{self.session is not None}, save_session={self.save_session!r})"
        )


@dataclass(frozen=True)
class WebRecordingExecutionStartResult:
    schema_version: int
    recording_id: str
    message_id: str
    runner_id: str
    status: str
    started_at: datetime | None
    idempotent: bool


@dataclass(frozen=True)
class WebRecordingControlResult:
    schema_version: int
    recording_id: str
    message_id: str
    runner_id: str
    status: str
    stop_requested: bool
    cancellation_requested: bool


@dataclass(frozen=True, repr=False)
class WebRecordingCompleteResult:
    schema_version: int
    recording_id: str
    message_id: str
    runner_id: str
    outcome: str
    status: str
    event_count: int
    saved_session_profile_id: int | None
    completed_at: datetime | None
    idempotent: bool

    def __repr__(self) -> str:
        return (
            "WebRecordingCompleteResult("
            f"recording_id={self.recording_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, outcome={self.outcome!r}, "
            f"status={self.status!r}, event_count={self.event_count!r}, "
            f"saved_session_profile_id={self.saved_session_profile_id!r}, "
            f"idempotent={self.idempotent!r})"
        )


@dataclass(frozen=True, repr=False)
class WebExplorationSession:
    profile_id: int
    storage_state: Mapping[str, Any]

    def __repr__(self) -> str:
        return f"WebExplorationSession(profile_id={self.profile_id!r}, storage_state='[REDACTED]')"


@dataclass(frozen=True, repr=False)
class WebExplorationExecutionPlanResult:
    schema_version: int
    task_type: str
    exploration_id: str
    message_id: str
    runner_id: str
    project_id: int
    start_url: str
    allowed_origins: tuple[str, ...]
    max_steps: int
    objective: str
    planned_steps: tuple[str, ...]
    expected_outcomes: tuple[str, ...]
    session: WebExplorationSession | None
    permissions: tuple[str, ...] = ("PAGE_READ",)
    headless: bool = True
    secrets: tuple[WebExecutionSecret, ...] = ()

    def __repr__(self) -> str:
        return (
            "WebExplorationExecutionPlanResult("
            f"exploration_id={self.exploration_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, project_id={self.project_id!r}, "
            "start_url='[REDACTED]', "
            f"allowed_origins={len(self.allowed_origins)}, max_steps={self.max_steps!r}, "
            f"permissions={len(self.permissions)}, "
            f"headless={self.headless!r}, session={self.session is not None}, "
            f"secrets={len(self.secrets)})"
        )


@dataclass(frozen=True)
class WebExplorationClaimResult:
    schema_version: int
    exploration_id: str
    message_id: str
    runner_id: str
    status: str
    idempotent: bool


@dataclass(frozen=True)
class WebExplorationStartResult:
    schema_version: int
    exploration_id: str
    message_id: str
    runner_id: str
    status: str
    idempotent: bool


@dataclass(frozen=True)
class WebExplorationControlResult:
    schema_version: int
    exploration_id: str
    message_id: str
    runner_id: str
    status: str
    stop_requested: bool
    cancellation_requested: bool


@dataclass(frozen=True, repr=False)
class WebExplorationDecisionResult:
    schema_version: int
    exploration_id: str
    message_id: str
    sequence: int
    ai_call_id: int
    decision: Mapping[str, Any]

    def __repr__(self) -> str:
        return (
            "WebExplorationDecisionResult("
            f"exploration_id={self.exploration_id!r}, sequence={self.sequence!r}, "
            f"ai_call_id={self.ai_call_id!r}, action={self.decision.get('action')!r})"
        )


@dataclass(frozen=True, repr=False)
class WebExplorationEvidenceUploadResult:
    schema_version: int
    exploration_id: str
    message_id: str
    runner_id: str
    evidence_id: str
    sequence: int
    kind: str
    label: str
    file_name: str
    mime: str
    size: int
    sha256: str
    created_at: datetime
    download_path: str
    idempotent: bool

    def __repr__(self) -> str:
        return (
            "WebExplorationEvidenceUploadResult("
            f"exploration_id={self.exploration_id!r}, sequence={self.sequence!r}, "
            f"kind={self.kind!r}, evidence_id={self.evidence_id!r}, "
            f"size={self.size!r}, idempotent={self.idempotent!r})"
        )


@dataclass(frozen=True)
class WebExplorationCompleteResult:
    schema_version: int
    exploration_id: str
    message_id: str
    runner_id: str
    status: str
    idempotent: bool


@dataclass(frozen=True, repr=False)
class WebExecutionPlanResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    web_case_id: int
    web_case_version_id: int
    start_url: str
    browser: str
    headless: bool
    total_timeout_ms: int
    initial_context: Mapping[str, Any]
    actions: tuple[WebExecutionActionPlan, ...]
    assertions: tuple[WebExecutionAssertionPlan, ...]
    session: WebSessionPlan | None
    secrets: tuple[WebExecutionSecret, ...] = ()
    browser_config: WebExecutionBrowserConfig = field(default_factory=WebExecutionBrowserConfig)

    def __repr__(self) -> str:
        return (
            "WebExecutionPlanResult("
            f"run_id={self.run_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, case_run_id={self.case_run_id!r}, "
            f"web_case_id={self.web_case_id!r}, web_case_version_id={self.web_case_version_id!r}, "
            f"start_url='[REDACTED]', actions={len(self.actions)}, "
            f"assertions={len(self.assertions)}, session={self.session is not None}, "
            f"secrets={len(self.secrets)})"
        )


@dataclass(frozen=True)
class WebExecutionStartResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    status: str
    started_at: datetime | None
    idempotent: bool


@dataclass(frozen=True, repr=False)
class WebExecutionCompleteResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    outcome: str
    run_status: str
    case_run_status: str
    traces: list[Mapping[str, Any]]
    error_type: str | None
    error_message: str | None
    completed_at: datetime
    idempotent: bool
    session_recovery: Mapping[str, Any] | None = None
    assertion_results: tuple[Mapping[str, Any], ...] = ()

    def __repr__(self) -> str:
        return (
            "WebExecutionCompleteResult("
            f"run_id={self.run_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, case_run_id={self.case_run_id!r}, "
            f"outcome={self.outcome!r}, traces={len(self.traces)}, "
            f"error_type={self.error_type!r}, "
            f"error_message={'[REDACTED]' if self.error_message else None!r}, "
            f"completed_at={self.completed_at!r}, idempotent={self.idempotent!r})"
        )


@dataclass(frozen=True, repr=False)
class WebExecutionEvaluationResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    assertion_status: str
    assertion_results: tuple[Mapping[str, Any], ...]
    node_results: tuple[Mapping[str, Any], ...]
    evaluation_token: str

    def __repr__(self) -> str:
        return (
            "WebExecutionEvaluationResult("
            f"run_id={self.run_id!r}, case_run_id={self.case_run_id!r}, "
            f"assertion_status={self.assertion_status!r}, "
            f"assertions={len(self.assertion_results)}, evaluation_token='[REDACTED]')"
        )


@dataclass(frozen=True, repr=False)
class WebEvidenceUploadResult:
    schema_version: int
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    evidence_id: str
    artifact_type: str
    artifact_name: str
    file_name: str
    mime: str
    size: int
    sha256: str
    metadata: Mapping[str, Any]
    created_at: datetime
    idempotent: bool

    def __repr__(self) -> str:
        return (
            "WebEvidenceUploadResult("
            f"run_id={self.run_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, case_run_id={self.case_run_id!r}, "
            f"evidence_id={self.evidence_id!r}, artifact_type={self.artifact_type!r}, "
            f"artifact_name={self.artifact_name!r}, file_name='[REDACTED]', "
            f"mime={self.mime!r}, size={self.size!r}, sha256='[REDACTED]', "
            f"metadata_keys={len(self.metadata)}, created_at={self.created_at!r}, "
            f"idempotent={self.idempotent!r})"
        )
