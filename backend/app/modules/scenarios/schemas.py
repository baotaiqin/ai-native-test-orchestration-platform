from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.resource_registry.schemas import CleanupConfig, CleanupOutcome, CleanupPolicy
from app.modules.test_cases.schemas import AssertionResult, RuntimeResponseSnapshot


class ScenarioNodeType(StrEnum):
    START = "START"
    END = "END"
    HTTP = "HTTP"
    IF = "IF"
    ELSE = "ELSE"
    LOOP = "LOOP"
    WAIT = "WAIT"
    SET_VARIABLE = "SET_VARIABLE"
    EXTRACT = "EXTRACT"
    ASSERT_STATUS = "ASSERT_STATUS"
    ASSERT_JSONPATH = "ASSERT_JSONPATH"
    AI_ASSERTION = "AI_ASSERTION"
    SQL_QUERY = "SQL_QUERY"
    SQL_EXECUTE = "SQL_EXECUTE"
    SQL_CLEANUP = "SQL_CLEANUP"
    PYTHON_SCRIPT = "PYTHON_SCRIPT"
    API_CLEANUP = "API_CLEANUP"


class FailurePolicy(StrEnum):
    STOP = "STOP"
    CONTINUE = "CONTINUE"
    RETRY_ONCE = "RETRY_ONCE"


class ScenarioPreviewMode(StrEnum):
    FULL = "FULL"
    NODE = "NODE"
    RUN_TO_HERE = "RUN_TO_HERE"
    RUN_FROM_HERE = "RUN_FROM_HERE"


class ScenarioNode(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    type: ScenarioNodeType
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = True
    parent_id: str | None = Field(default=None, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    # ``None`` means the legacy ScenarioSettings.stop_on_failure value is the
    # effective policy.  A populated value is an explicit node-level override.
    failure_policy: FailurePolicy | None = None
    timeout_ms: int = Field(default=30000, ge=100, le=600000)

    def cleanup_config(
        self, default_policy: CleanupPolicy | str = CleanupPolicy.ALWAYS
    ) -> CleanupConfig:
        if self.type not in {ScenarioNodeType.API_CLEANUP, ScenarioNodeType.SQL_CLEANUP}:
            raise ValueError("当前节点不是 Cleanup 节点")
        config = dict(self.config)
        expected_type = "API" if self.type == ScenarioNodeType.API_CLEANUP else "SQL"
        if config.get("cleanup_type", expected_type) != expected_type:
            raise ValueError(f"{self.type.value} 节点的 cleanup_type 必须是 {expected_type}")
        config.setdefault("cleanup_type", expected_type)
        config.setdefault("policy", default_policy)
        if "timeout_ms" in config:
            try:
                explicit_timeout = int(config["timeout_ms"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Scenario Cleanup timeout_ms 必须是整数") from exc
            if explicit_timeout != self.timeout_ms:
                raise ValueError("Scenario Cleanup 只允许使用节点 timeout_ms，不能配置冲突值")
        else:
            config["timeout_ms"] = self.timeout_ms
        return CleanupConfig.model_validate(config)


class ScenarioSettings(BaseModel):
    stop_on_failure: bool = True
    cleanup_policy: Literal["ALWAYS", "ON_SUCCESS", "ON_FAILURE", "NEVER"] = "ALWAYS"
    max_loop_iterations: int = Field(default=100, ge=1, le=1000)
    initial_variables: list[str] = Field(default_factory=list, max_length=200)


class ScenarioDsl(BaseModel):
    version: Literal["1.0"] = "1.0"
    nodes: list[ScenarioNode] = Field(min_length=2, max_length=500)
    settings: ScenarioSettings = Field(default_factory=ScenarioSettings)


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ScenarioValidationIssue(BaseModel):
    severity: ValidationSeverity
    code: str
    message: str
    node_id: str | None = None


class ScenarioValidationResponse(BaseModel):
    valid: bool
    issues: list[ScenarioValidationIssue]
    node_count: int


class ScenarioExecutionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int | None = Field(default=None, gt=0)
    dsl: ScenarioDsl
    context: dict[str, Any] = Field(default_factory=dict)
    response: RuntimeResponseSnapshot = Field(default_factory=RuntimeResponseSnapshot)
    responses_by_node: dict[str, RuntimeResponseSnapshot] = Field(
        default_factory=dict, max_length=500
    )
    cleanup_outcome: CleanupOutcome = CleanupOutcome.SUCCESS
    mode: ScenarioPreviewMode = ScenarioPreviewMode.FULL
    target_node_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_debug_target(self) -> "ScenarioExecutionPreviewRequest":
        if self.mode != ScenarioPreviewMode.FULL and self.target_node_id is None:
            raise ValueError("NODE/RUN_TO_HERE/RUN_FROM_HERE 必须提供 target_node_id")
        if self.mode == ScenarioPreviewMode.FULL and self.target_node_id is not None:
            raise ValueError("FULL 预览不能提供 target_node_id")
        return self


class ScenarioPreviewProfileSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_version_id: int = Field(gt=0)
    context: dict[str, Any] = Field(default_factory=dict)
    responses_by_node: dict[str, RuntimeResponseSnapshot] = Field(
        default_factory=dict, max_length=500
    )
    cleanup_outcome: CleanupOutcome = CleanupOutcome.SUCCESS


class ScenarioPreviewProfileResponse(BaseModel):
    scenario_id: int
    scenario_version_id: int
    context: dict[str, Any]
    responses_by_node: dict[str, RuntimeResponseSnapshot]
    cleanup_outcome: CleanupOutcome
    source: Literal["SAVED", "GENERATED"]
    saved_at: datetime | None = None


class ScenarioAiBaselineResponse(BaseModel):
    scenario_id: int
    suggestion_id: int
    source_version_id: int
    name: str
    dsl: ScenarioDsl


class ScenarioNodeTrace(BaseModel):
    sequence: int
    node_id: str
    node_type: ScenarioNodeType
    status: Literal["PASSED", "FAILED", "SKIPPED", "TIMEOUT"]
    iteration_path: list[int] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=1, ge=1, le=2)
    duration_ms: int = Field(default=0, ge=0)
    timeout_ms: int = Field(default=30000, ge=100, le=600000)
    failure_policy: FailurePolicy = FailurePolicy.STOP
    error_code: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=500)
    terminal: bool = True
    retryable: bool = False


class ScenarioExecutionPreviewResponse(BaseModel):
    status: Literal["PASS", "FAIL", "REVIEW"] = "PASS"
    traces: list[ScenarioNodeTrace]
    context: dict[str, Any]
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    final_status: Literal["PASS", "FAIL", "REVIEW"] = "PASS"
    mode: ScenarioPreviewMode = ScenarioPreviewMode.FULL
    target_node_id: str | None = None
    target_reached: bool | None = None
    stop_reason: str | None = Field(default=None, max_length=100)
    stop_message: str | None = Field(default=None, max_length=500)
    execution_boundary: Literal["SCENARIO_PREVIEW_ONLY"] = "SCENARIO_PREVIEW_ONLY"


class ScenarioCreate(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    dsl: ScenarioDsl
    change_note: str | None = Field(default="创建 Scenario", max_length=500)


class ScenarioVersionCreate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    dsl: ScenarioDsl
    change_note: str = Field(min_length=1, max_length=500)


class ScenarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    name: str
    status: str
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ScenarioVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    version_no: int
    dsl: ScenarioDsl
    change_note: str | None
    created_by: str
    created_at: datetime


class ScenarioDetailResponse(ScenarioResponse):
    current_version: ScenarioVersionResponse | None = None


class ScenarioListResponse(BaseModel):
    items: list[ScenarioResponse]
    total: int
