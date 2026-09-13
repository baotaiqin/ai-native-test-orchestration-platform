import math
import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.runs.schemas import ApiRequestTemplate, RunDetailResponse, RunDispatchResponse


class PerformanceSla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_error_rate: float | None = Field(default=None, ge=0, le=1)
    max_p95_ms: int | None = Field(default=None, ge=1, le=120000)
    max_p99_ms: int | None = Field(default=None, ge=1, le=120000)
    max_average_ms: int | None = Field(default=None, ge=1, le=120000)
    max_ttft_p95_ms: int | None = Field(default=None, ge=1, le=120000)
    min_rps: float | None = Field(default=None, gt=0, le=100000)
    min_success_rate: float | None = Field(default=None, ge=0, le=1)


class PerformanceStepStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(ge=1, le=100)
    duration_seconds: int = Field(ge=1, le=3600)


class PerformanceStreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["SSE", "NDJSON"] = "SSE"
    completion_marker: str = Field(default="[DONE]", min_length=1, max_length=200)
    require_completion_marker: bool = True


class PerformanceProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    target_type: Literal["API_CASE", "SCENARIO"] = "API_CASE"
    case_id: int | None = Field(default=None, gt=0)
    case_version_id: int | None = Field(default=None, gt=0)
    scenario_id: int | None = Field(default=None, gt=0)
    scenario_version_id: int | None = Field(default=None, gt=0)
    concurrency: int = Field(default=1, ge=1, le=100)
    iterations: int = Field(default=10, ge=1, le=10000)
    load_mode: Literal["FIXED_ITERATIONS", "FIXED_RPS", "FIXED_CONCURRENCY", "STEP_LOAD"] = (
        "FIXED_ITERATIONS"
    )
    target_rps: float | None = Field(default=None, gt=0, le=10000)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)
    step_stages: list[PerformanceStepStage] | None = Field(
        default=None, min_length=1, max_length=20
    )
    warmup_iterations: int = Field(default=0, ge=0, le=1000)
    request_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    engine: Literal["PYTHON_HTTP", "PYTHON_STREAM", "JMETER"] = "PYTHON_HTTP"
    cleanup_mode: Literal["NONE", "RESOURCE_CLEANUP", "BATCH_CLEANUP"] = "NONE"
    stream_config: PerformanceStreamConfig = Field(default_factory=PerformanceStreamConfig)
    sla: PerformanceSla = Field(default_factory=PerformanceSla)

    @model_validator(mode="after")
    def validate_profile(self) -> "PerformanceProfileCreate":
        if self.target_type == "API_CASE":
            if (
                self.case_id is None
                or self.scenario_id is not None
                or self.scenario_version_id is not None
            ):
                raise ValueError("API_CASE 目标必须且只能设置 case_id/case_version_id")
        elif (
            self.scenario_id is None or self.case_id is not None or self.case_version_id is not None
        ):
            raise ValueError("SCENARIO 目标必须且只能设置 scenario_id/scenario_version_id")
        if self.engine in {"PYTHON_STREAM", "JMETER"} and self.target_type != "API_CASE":
            raise ValueError("PYTHON_STREAM/JMETER 仅支持 API_CASE 目标")
        if self.engine != "PYTHON_STREAM" and self.stream_config != PerformanceStreamConfig():
            raise ValueError("非流式引擎不能设置 stream_config")
        if self.load_mode == "FIXED_ITERATIONS":
            if self.target_rps is not None or self.duration_seconds is not None or self.step_stages:
                raise ValueError("FIXED_ITERATIONS 不能设置速率、时长或阶梯")
            if self.iterations < self.concurrency:
                raise ValueError("iterations 不能小于 concurrency")
            worst_duration_ms = (
                math.ceil((self.iterations + self.warmup_iterations) / self.concurrency)
                * self.request_timeout_ms
            )
        elif self.load_mode == "FIXED_RPS":
            if self.target_rps is None or self.duration_seconds is None:
                raise ValueError("FIXED_RPS 必须设置 target_rps 和 duration_seconds")
            if self.step_stages:
                raise ValueError("FIXED_RPS 不能设置 step_stages")
            planned_iterations = math.ceil(self.target_rps * self.duration_seconds)
            if planned_iterations > 10_000:
                raise ValueError("FIXED_RPS 计划请求数不能超过 10000")
            self.iterations = planned_iterations
            worst_duration_ms = (
                self.duration_seconds * 1000
                + self.request_timeout_ms
                + math.ceil(self.warmup_iterations / self.concurrency) * self.request_timeout_ms
            )
        elif self.load_mode == "FIXED_CONCURRENCY":
            if self.target_rps is not None or self.duration_seconds is None or self.step_stages:
                raise ValueError("FIXED_CONCURRENCY 必须仅设置 duration_seconds")
            worst_duration_ms = (
                self.duration_seconds * 1000
                + self.request_timeout_ms
                + math.ceil(self.warmup_iterations / self.concurrency) * self.request_timeout_ms
            )
        else:
            if (
                self.target_rps is not None
                or self.duration_seconds is not None
                or not self.step_stages
            ):
                raise ValueError("STEP_LOAD 必须仅设置 step_stages")
            total_duration = sum(stage.duration_seconds for stage in self.step_stages)
            if total_duration > 3600:
                raise ValueError("STEP_LOAD 总持续时长不能超过 3600 秒")
            self.concurrency = max(stage.concurrency for stage in self.step_stages)
            worst_duration_ms = (
                total_duration * 1000
                + self.request_timeout_ms
                + math.ceil(self.warmup_iterations / self.concurrency) * self.request_timeout_ms
            )
        if worst_duration_ms + 30_000 > 86_400_000:
            raise ValueError("性能配置的最坏执行时间不能超过 24 小时")
        return self


class PerformanceProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    target_type: Literal["API_CASE", "SCENARIO"]
    case_id: int | None
    case_version_id: int | None
    scenario_id: int | None
    scenario_version_id: int | None
    concurrency: int
    iterations: int
    load_mode: Literal["FIXED_ITERATIONS", "FIXED_RPS", "FIXED_CONCURRENCY", "STEP_LOAD"]
    target_rps: float | None
    duration_seconds: int | None
    step_stages: list[PerformanceStepStage] | None
    warmup_iterations: int
    request_timeout_ms: int
    engine: Literal["PYTHON_HTTP", "PYTHON_STREAM", "JMETER"]
    cleanup_mode: Literal["NONE", "RESOURCE_CLEANUP", "BATCH_CLEANUP"]
    stream_config: PerformanceStreamConfig
    sla: PerformanceSla
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_datetime(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class PerformanceProfileListResponse(BaseModel):
    items: list[PerformanceProfileResponse]
    total: int


class PerformanceRunnerOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    performance_slots_available: int = Field(ge=1)
    ready_capabilities: list[str] = Field(default_factory=list)


class PerformanceRunnerOptionListResponse(BaseModel):
    items: list[PerformanceRunnerOption]
    total: int


class PerformanceRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: int = Field(gt=0)
    runner_id: str = Field(min_length=1, max_length=64)


class PerformanceRunStartResponse(BaseModel):
    run: RunDetailResponse
    dispatch: RunDispatchResponse


class PerformanceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(ge=0, le=120000)
    status_code: int | None = Field(default=None, ge=100, le=599)
    bytes_received: int = Field(default=0, ge=0, le=100000000)
    bytes_sent: int = Field(default=0, ge=0, le=100000000)
    started_offset_ms: int = Field(default=0, ge=0, le=86400000)
    ttft_ms: int | None = Field(default=None, ge=0, le=120000)
    stream_duration_ms: int | None = Field(default=None, ge=0, le=120000)
    input_tokens: int | None = Field(default=None, ge=0, le=10000000)
    output_tokens: int | None = Field(default=None, ge=0, le=10000000)
    chunk_count: int | None = Field(default=None, ge=0, le=1000000)
    max_chunk_gap_ms: int | None = Field(default=None, ge=0, le=120000)
    stream_completed: bool | None = None
    error_type: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> "PerformanceSample":
        if (self.status_code is None) == (self.error_type is None):
            raise ValueError("sample 必须且只能包含 status_code 或 error_type")
        return self


class PerformanceCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)
    case_run_id: int = Field(gt=0)
    wall_duration_ms: int = Field(ge=1, le=86400000)
    samples: list[PerformanceSample] = Field(min_length=1, max_length=10000)


class PerformanceMetricSummary(BaseModel):
    request_count: int
    success_count: int
    fail_count: int
    success_rate: float
    error_rate: float
    rps: float
    tps: float = 0
    min_ms: int
    max_ms: int
    average_ms: float
    p50_ms: int
    p75_ms: int = 0
    p90_ms: int = 0
    p95_ms: int
    p99_ms: int
    received_bytes: int
    sent_bytes: int = 0
    active_users_peak: int = 0
    wall_duration_ms: int
    ttft_average_ms: float | None = None
    ttft_p95_ms: int | None = None
    stream_duration_average_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    chunk_count: int | None = None
    max_chunk_gap_ms: int | None = None
    interrupted_streams: int | None = None
    trends: list[dict] = Field(default_factory=list)


class PerformanceSlaResult(BaseModel):
    metric: str
    operator: str
    threshold: float
    actual: float
    passed: bool


class PerformanceRunResponse(BaseModel):
    run_id: str
    profile_id: int
    project_id: int
    run_code: str
    status: str
    config_snapshot: dict
    metrics: PerformanceMetricSummary | None
    sla_results: list[PerformanceSlaResult]
    error_distribution: dict[str, int]
    created_at: datetime
    completed_at: datetime | None

    @field_serializer("created_at", "completed_at", when_used="json")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class PerformanceRunListResponse(BaseModel):
    items: list[PerformanceRunResponse]
    total: int


class PerformanceExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 3
    run_id: str
    message_id: str
    runner_id: str
    case_run_id: int
    target_type: Literal["API_CASE", "SCENARIO"]
    engine: Literal["PYTHON_HTTP", "PYTHON_STREAM", "JMETER"]
    request: ApiRequestTemplate | None
    concurrency: int
    iterations: int
    load_mode: Literal["FIXED_ITERATIONS", "FIXED_RPS", "FIXED_CONCURRENCY", "STEP_LOAD"]
    target_rps: float | None
    duration_seconds: int | None
    step_stages: list[PerformanceStepStage] | None
    warmup_iterations: int
    request_timeout_ms: int
    cleanup_mode: Literal["NONE", "RESOURCE_CLEANUP", "BATCH_CLEANUP"]
    stream_config: PerformanceStreamConfig
    total_timeout_ms: int


class PerformanceCompleteResponse(BaseModel):
    run_id: str
    status: str
    metrics: PerformanceMetricSummary
    sla_results: list[PerformanceSlaResult]
    error_distribution: dict[str, int]
    idempotent: bool


class PerformanceComparisonItem(BaseModel):
    run_id: str
    run_code: str
    status: str
    metrics: PerformanceMetricSummary
    delta_from_baseline: dict[str, float]


class PerformanceComparisonResponse(BaseModel):
    baseline_run_id: str
    items: list[PerformanceComparisonItem]


_ANALYSIS_CREDENTIAL = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|secret|api[_-]?key|credential)"
    r"\s*[:=]\s*[^\s,;]+|https?://[^\s,;]+)"
)


def _safe_analysis_text(value: str, field_name: str, maximum: int) -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} 超过长度限制")
    if _ANALYSIS_CREDENTIAL.search(normalized):
        raise ValueError(f"{field_name} 不能包含地址或敏感凭据")
    return normalized


class PerformanceAnalysisFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["LATENCY", "THROUGHPUT", "ERRORS", "STABILITY", "STREAMING", "CAPACITY"]
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    metric: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    observed: float
    observation: str = Field(min_length=1, max_length=600)
    recommendation: str = Field(min_length=1, max_length=600)

    @field_validator("observation", "recommendation")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _safe_analysis_text(value, info.field_name, 600)


class PerformanceBottleneckHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=600)
    evidence_metrics: list[str] = Field(min_length=1, max_length=6)
    confidence: float = Field(ge=0, le=1)
    validation_steps: list[str] = Field(min_length=1, max_length=6)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _safe_analysis_text(value, "description", 600)

    @field_validator("evidence_metrics")
    @classmethod
    def validate_evidence_metrics(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if len(set(normalized)) != len(normalized) or any(
            re.fullmatch(r"[A-Za-z0-9_.:-]+", item) is None for item in normalized
        ):
            raise ValueError("evidence_metrics 必须是唯一的合法指标名")
        return normalized

    @field_validator("validation_steps")
    @classmethod
    def validate_steps(cls, value: list[str]) -> list[str]:
        return [_safe_analysis_text(item, "validation_steps", 500) for item in value]


class PerformanceAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["MEETS_SLA", "MISSES_SLA", "NO_SLA"]
    summary: str = Field(min_length=1, max_length=1000)
    findings: list[PerformanceAnalysisFinding] = Field(min_length=1, max_length=8)
    bottleneck_hypotheses: list[PerformanceBottleneckHypothesis] = Field(
        default_factory=list, max_length=5
    )
    recommendations: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _safe_analysis_text(value, "summary", 1000)

    @field_validator("recommendations")
    @classmethod
    def validate_recommendations(cls, value: list[str]) -> list[str]:
        return [_safe_analysis_text(item, "recommendations", 600) for item in value]


class PerformanceAnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(default=None, max_length=2000)

    @field_validator("additional_instructions")
    @classmethod
    def validate_additional_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_analysis_text(value, "additional_instructions", 2000)


class PerformanceAnalysisResponse(BaseModel):
    id: int
    project_id: int
    run_id: str
    status: Literal["DRAFT", "COMPLETED"]
    ai_call_id: int | None
    actual_model: str | None
    prompt_version_id: int
    output_schema_id: int
    fallback_used: bool | None
    repair_used: bool | None
    source_snapshot_sha256: str = Field(min_length=64, max_length=64)
    source_snapshot_size: int = Field(ge=0, le=65536)
    source_sla_verdict: Literal["MEETS_SLA", "MISSES_SLA", "NO_SLA"]
    structured_result: PerformanceAnalysisResult | None
    created_by: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class PerformanceAnalysisListResponse(BaseModel):
    items: list[PerformanceAnalysisResponse]
    total: int
