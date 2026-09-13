from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.runs.enums import RunNodeStatus, RunStatus, RunTriggerType, RunType

DATABASE_INTEGER_MAX = 2_147_483_647
REPORT_PAGE_MAX = DATABASE_INTEGER_MAX
REPORT_PAGE_SIZE_MAX = 100
REPORT_DATE_MIN = date(1000, 1, 2)
REPORT_DATE_MAX = date(9999, 12, 30)
_REPORT_DATE_SCHEMA = {
    "x-minimum": REPORT_DATE_MIN.isoformat(),
    "x-maximum": REPORT_DATE_MAX.isoformat(),
}


class RecordingAvailability(StrEnum):
    RECORDED = "RECORDED"
    NOT_RECORDED = "NOT_RECORDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DurationKind(StrEnum):
    COMPLETED = "COMPLETED"
    OBSERVED = "OBSERVED"
    UNAVAILABLE = "UNAVAILABLE"


class ReportListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0, le=DATABASE_INTEGER_MAX)
    run_type: RunType | None = None
    status: RunStatus | None = None
    environment_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )
    created_from: date | None = Field(
        default=None,
        ge=REPORT_DATE_MIN,
        le=REPORT_DATE_MAX,
        description=(
            "Asia/Shanghai 起始自然日；有效范围 1000-01-02 至 9999-12-30"
        ),
        json_schema_extra=_REPORT_DATE_SCHEMA,
    )
    created_to: date | None = Field(
        default=None,
        ge=REPORT_DATE_MIN,
        le=REPORT_DATE_MAX,
        description=(
            "Asia/Shanghai 结束自然日（包含整日）；有效范围 1000-01-02 至 9999-12-30"
        ),
        json_schema_extra=_REPORT_DATE_SCHEMA,
    )
    page: int = Field(default=1, ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(default=20, ge=1, le=REPORT_PAGE_SIZE_MAX)

    @model_validator(mode="after")
    def validate_date_range(self) -> "ReportListQuery":
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from 不能晚于 created_to")
        return self


class ReportPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(default=20, ge=1, le=REPORT_PAGE_SIZE_MAX)


class ReportStepPageQuery(ReportPageQuery):
    case_run_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )


class ReportEvidencePageQuery(ReportPageQuery):
    case_run_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )
    step_run_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )


class ReportRequirementSourcePageQuery(ReportPageQuery):
    case_run_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )


class RequirementCaptureStatus(StrEnum):
    NOT_RECORDED = "NOT_RECORDED"
    CAPTURED_EMPTY = "CAPTURED_EMPTY"
    CAPTURED = "CAPTURED"
    UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"


class RequirementVersionBinding(StrEnum):
    EXACT_REQUIREMENT_VERSION = "EXACT_REQUIREMENT_VERSION"
    REQUIREMENT_VERSION_UNKNOWN = "REQUIREMENT_VERSION_UNKNOWN"


class RequirementAssetVersionBinding(StrEnum):
    EXACT_EXECUTION_VERSION = "EXACT_EXECUTION_VERSION"
    ASSET_VERSION_UNKNOWN = "ASSET_VERSION_UNKNOWN"


class ReportProjectReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: str
    metadata_basis: Literal["CURRENT"] = "CURRENT"


class ReportEnvironmentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str | None
    enabled: bool | None
    available: bool
    metadata_basis: Literal["CURRENT"] = "CURRENT"


class ReportRunnerReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None
    status: str | None
    available: bool
    metadata_basis: Literal["CURRENT"] = "CURRENT"


class ReportTargetReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RunType
    asset_id: int
    version_id: int
    asset_name: str | None
    asset_code: str | None
    asset_status: str | None
    current_version_id: int | None
    version_no: int | None
    version_status: str | None
    version_available: bool
    is_current_version: bool | None
    asset_metadata_basis: Literal["CURRENT"] = "CURRENT"
    version_basis: Literal["LOCKED_BY_RUN"] = "LOCKED_BY_RUN"
    definition_exposure: Literal["REFERENCE_ONLY"] = "REFERENCE_ONLY"


class ReportRecordedCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    review: int = Field(ge=0)
    timeout: int = Field(ge=0)
    source: Literal["RUN_RECORD"] = "RUN_RECORD"


class ReportCaseStatusCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int = Field(ge=0)
    assigned: int = Field(ge=0)
    running: int = Field(ge=0)
    cancelling: int = Field(ge=0)
    success: int = Field(ge=0)
    failed: int = Field(ge=0)
    review: int = Field(ge=0)
    timeout: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    skipped: int = Field(ge=0)
    unfinished: int = Field(ge=0)
    total: int = Field(ge=0)
    source: Literal["CASE_RUN_GROUPING"] = "CASE_RUN_GROUPING"


class ReportRate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1)
    unit: Literal["RATIO"] = "RATIO"
    definition: str


class ReportDuration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    milliseconds: int | None = Field(default=None, ge=0)
    kind: DurationKind
    observed_at: datetime


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_code: str
    run_type: RunType
    project: ReportProjectReference
    environment: ReportEnvironmentReference | None
    runner: ReportRunnerReference | None
    target: ReportTargetReference
    status: RunStatus
    trigger_type: RunTriggerType
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    duration: ReportDuration
    error_type: str | None
    error_message: str | None
    recorded_counts: ReportRecordedCounts
    case_status_counts: ReportCaseStatusCounts
    case_success_rate: ReportRate


class ReportListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReportSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(ge=1, le=REPORT_PAGE_SIZE_MAX)
    has_more: bool


class ReportDataField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: RecordingAvailability
    value: Any = None
    note: str


class ReportExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RunType
    availability: RecordingAvailability
    message_id: str | None
    outcome: str | None
    status: str | None
    retry_count: int | None = Field(default=None, ge=0)
    error_type: str | None
    error_message: str | None
    completed_at: datetime | None
    actual_request: ReportDataField
    response: ReportDataField
    extractions: ReportDataField
    assertions: ReportDataField
    traces: ReportDataField


class ReportCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    sequence_no: int = Field(gt=0)
    run_id: str
    target: ReportTargetReference
    status: RunNodeStatus
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    started_at: datetime | None
    ended_at: datetime | None
    error_type: str | None
    error_message: str | None
    execution_result: ReportExecutionResult
    requirement_capture: "ReportRequirementCapture"


class ReportStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    case_run_id: int
    sequence_no: int = Field(gt=0)
    node_id: str
    name: str
    type: str
    status: RunNodeStatus
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    started_at: datetime | None
    ended_at: datetime | None
    error_type: str | None
    error_message: str | None


class ReportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    case_run_id: int
    step_run_id: int | None
    artifact_type: str
    file_name: str
    mime: str
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    metadata: Any = None
    created_at: datetime
    download_path: str


class ReportCasePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    items: list[ReportCase]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(ge=1, le=REPORT_PAGE_SIZE_MAX)
    has_more: bool
    next_page: int | None = Field(ge=1, le=REPORT_PAGE_MAX)
    continuation_path: str | None


class ReportStepPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_run_id: int | None
    items: list[ReportStep]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(ge=1, le=REPORT_PAGE_SIZE_MAX)
    has_more: bool
    next_page: int | None = Field(ge=1, le=REPORT_PAGE_MAX)
    continuation_path: str | None


class ReportEvidencePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_run_id: int | None
    step_run_id: int | None
    items: list[ReportEvidence]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(ge=1, le=REPORT_PAGE_SIZE_MAX)
    has_more: bool
    next_page: int | None = Field(ge=1, le=REPORT_PAGE_MAX)
    continuation_path: str | None


class ReportRequirementCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_run_id: int
    status: RequirementCaptureStatus
    captured_at: datetime | None
    captured_at_time_basis: Literal["UTC"] | None
    target_type: RunType
    target_asset_id: int
    target_version_id: int
    total: int = Field(ge=0, le=1000)
    consistency_basis: Literal["CREATE_RUN_TRANSACTION"] | None
    note: str


class ReportRequirementSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    case_run_id: int
    sequence_no: int = Field(gt=0, le=1000)
    original_link_id: int
    supersedes_link_id: int | None
    requirement_id: int
    requirement_code: str
    requirement_title: str
    requirement_type: str
    requirement_status: str
    requirement_version_id: int | None
    requirement_version_no: int | None
    requirement_content_hash: str | None
    requirement_source_type: str | None
    requirement_version_binding: RequirementVersionBinding
    target_type: RunType
    link_asset_type: Literal["TEST_CASE", "WEB_CASE"]
    target_asset_id: int
    target_version_id: int
    link_asset_version_id: int | None
    asset_version_binding: RequirementAssetVersionBinding
    binding_note: str
    relation_type: str
    source: str
    confidence: float = Field(ge=0, le=1)
    link_created_by: str | None
    link_created_at: datetime
    link_created_at_time_basis: Literal["UTC", "LEGACY_UNKNOWN"]
    captured_at: datetime
    captured_at_time_basis: Literal["UTC"]
    historical_scope: Literal["RUN_CREATION_SNAPSHOT"] = "RUN_CREATION_SNAPSHOT"


class ReportRequirementSourcePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_run_id: int | None
    captures: list[ReportRequirementCapture]
    items: list[ReportRequirementSource]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=REPORT_PAGE_MAX)
    page_size: int = Field(ge=1, le=REPORT_PAGE_SIZE_MAX)
    has_more: bool
    next_page: int | None = Field(ge=1, le=REPORT_PAGE_MAX)
    continuation_path: str | None


class ReportAiAuditPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    kind: Literal["WEB_FAILURE_ANALYSIS", "WEB_HEALING_PROPOSAL"]
    case_run_id: int
    status: str
    ai_call_id: int | None
    created_at: datetime
    summary: Any = None


class ReportAiAuditGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    latest: ReportAiAuditPreview | None
    has_more: bool
    continuation_path: str


class ReportRelatedAi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_analyses: ReportAiAuditGroup
    healing_proposals: ReportAiAuditGroup
    raw_ai_content_exposed: Literal[False] = False


class ReportDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: ReportSummary
    cases: ReportCasePage
    steps: ReportStepPage
    evidence: ReportEvidencePage
    requirement_sources: ReportRequirementSourcePage
    related_ai: ReportRelatedAi
    pagination_note: str
