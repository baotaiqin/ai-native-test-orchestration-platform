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
from app.modules.web_cases.schemas import WebCaseContent

_IDENTIFIER = r"^[A-Za-z0-9_-]+$"
_SENSITIVE = re.compile(
    r"(?i)(?:bearer\s+(?!\[redacted\]|<redacted>)\S+|"
    r"(?:password|passwd|token|secret|credential|authorization|cookie|api[_-]?key)"
    r"\s*[:=]\s*(?!\[redacted\]|<redacted>)\S+)"
)


def _http_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        not normalized
        or "\r" in normalized
        or "\n" in normalized
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL 无效")
    return normalized


class WebPlanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_key: str = Field(min_length=2, max_length=64, pattern=_IDENTIFIER)
    name: str = Field(min_length=2, max_length=255)
    objective: str = Field(min_length=1, max_length=2000)
    category: Literal["POSITIVE", "NEGATIVE", "BOUNDARY", "RECOVERY"]
    priority: Literal["P0", "P1", "P2", "P3"]
    rationale: str = Field(min_length=1, max_length=2000)
    requirement_ids: list[int] = Field(min_length=1, max_length=200)
    api_definition_ids: list[int] = Field(default_factory=list, max_length=200)
    preconditions: list[str] = Field(default_factory=list, max_length=30)
    planned_steps: list[str] = Field(min_length=1, max_length=50)
    expected_outcomes: list[str] = Field(min_length=1, max_length=30)
    start_url_hint: str | None = Field(default=None, max_length=2048)

    @field_validator("requirement_ids", "api_definition_ids")
    @classmethod
    def unique_positive_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values) or len(values) != len(set(values)):
            raise ValueError("引用编号必须为不重复的正整数")
        return values

    @field_validator("preconditions", "planned_steps", "expected_outcomes")
    @classmethod
    def normalize_text_list(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 2000 for value in normalized):
            raise ValueError("步骤文本不能为空且不能超过 2000 字符")
        return normalized

    @field_validator("start_url_hint")
    @classmethod
    def validate_url_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if (
            normalized.startswith("/")
            and not normalized.startswith("//")
            and "\\" not in normalized
            and "\r" not in normalized
            and "\n" not in normalized
            and not parsed.scheme
            and not parsed.netloc
        ):
            return normalized
        try:
            return _http_url(normalized)
        except (TypeError, ValueError):
            raise ValueError(
                "start_url_hint 必须为安全的 http/https URL、站点根相对路径或 null"
            ) from None


class WebPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    candidates: list[WebPlanCandidate] = Field(min_length=1, max_length=30)
    uncovered_requirement_ids: list[int] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_candidates(self) -> "WebPlanResult":
        keys = [item.candidate_key for item in self.candidates]
        names = [item.name.strip().casefold() for item in self.candidates]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError("Web 候选用例标识和名称必须唯一")
        return self


class WebPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    prompt_id: int = Field(gt=0)
    requirement_document_version_id: int = Field(gt=0)
    requirement_id: int | None = Field(default=None, gt=0)
    api_import_id: int | None = Field(default=None, gt=0)
    api_definition_ids: list[int] = Field(default_factory=list, max_length=200)
    additional_instructions: str | None = Field(default=None, max_length=5000)

    @field_validator("api_definition_ids")
    @classmethod
    def unique_api_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values) or len(values) != len(set(values)):
            raise ValueError("API Definition ID 必须为不重复的正整数")
        return values


class WebPlanItemResponse(BaseModel):
    id: int
    candidate_key: str
    order_index: int
    name: str
    objective: str
    category: str
    priority: str
    rationale: str
    requirement_ids: list[int]
    api_definition_ids: list[int]
    preconditions: list[str]
    planned_steps: list[str]
    expected_outcomes: list[str]
    start_url_hint: str | None
    latest_exploration_id: str | None = None
    latest_revision_id: int | None = None


class WebPlanResponse(BaseModel):
    id: int
    project_id: int
    requirement_document_version_id: int
    requirement_document_version_no: int
    api_import_id: int | None
    prompt_id: int
    ai_call_id: int | None
    generation_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    source_snapshot_sha256: str
    summary: str | None
    items: list[WebPlanItemResponse]
    actual_model: str | None
    fallback_used: bool
    repair_used: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    reused: bool = False

    @field_serializer("created_at", "started_at", "completed_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebPlanListResponse(BaseModel):
    items: list[WebPlanResponse]
    total: int


class ExplorationStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExplorationDispatchStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class ExplorationPermission(StrEnum):
    """User-approved capabilities captured as an immutable exploration snapshot."""

    PAGE_READ = "PAGE_READ"
    MANAGED_LOGIN = "MANAGED_LOGIN"
    FORM_SUBMIT = "FORM_SUBMIT"
    TEST_DATA_CREATE = "TEST_DATA_CREATE"
    DIRECT_API_NAVIGATION = "DIRECT_API_NAVIGATION"
    SCREENSHOT_CAPTURE = "SCREENSHOT_CAPTURE"


class ExplorationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1, max_length=64)
    decision_prompt_id: int = Field(gt=0)
    environment_id: int | None = Field(default=None, gt=0)
    session_profile_id: int | None = Field(default=None, gt=0)
    use_login_credentials: bool = False
    headless: bool = True
    start_url: str = Field(min_length=1, max_length=2048)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    max_steps: int = Field(default=20, ge=1, le=50)
    permissions: list[ExplorationPermission] = Field(
        default_factory=lambda: [ExplorationPermission.PAGE_READ], max_length=6
    )

    @field_validator("start_url")
    @classmethod
    def validate_start_url(cls, value: str) -> str:
        try:
            return _http_url(value)
        except (TypeError, ValueError):
            raise ValueError("start_url 必须为安全的 http/https URL") from None

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parsed = urlsplit(value.strip())
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("allowed_origins 必须只包含安全的 http/https origin")
            origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            if origin not in normalized:
                normalized.append(origin)
        return normalized

    @model_validator(mode="after")
    def default_start_origin(self) -> "ExplorationCreateRequest":
        start = urlsplit(self.start_url)
        start_origin = f"{start.scheme.lower()}://{start.netloc.lower()}"
        if not self.allowed_origins:
            self.allowed_origins = [start_origin]
        elif start_origin not in self.allowed_origins:
            raise ValueError("allowed_origins 必须包含 start_url 的 origin")
        self.permissions = list(dict.fromkeys(self.permissions))
        if ExplorationPermission.PAGE_READ not in self.permissions:
            raise ValueError("探索必须授权 PAGE_READ")
        if (
            self.use_login_credentials
            and ExplorationPermission.MANAGED_LOGIN not in self.permissions
        ):
            raise ValueError("使用项目登录凭据必须授权 MANAGED_LOGIN")
        return self


class ExplorationTaskEnvelopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_type: Literal["WEB_EXPLORATION"] = "WEB_EXPLORATION"
    exploration_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER)
    runner_id: str = Field(min_length=1, max_length=64)
    project_id: int = Field(gt=0)
    attempt: int = Field(ge=1, le=10)
    enqueued_at: datetime


class ExplorationEvidenceKind(StrEnum):
    CHECKPOINT = "CHECKPOINT"
    FINAL = "FINAL"
    FAILURE = "FAILURE"


class ExplorationEvidenceResponse(BaseModel):
    id: str
    exploration_id: str
    sequence: int
    kind: ExplorationEvidenceKind
    label: str
    file_name: str
    mime: Literal["image/png"] = "image/png"
    size: int = Field(gt=0, le=5_000_000)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    download_path: str

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return to_utc_isoformat(value)


class ExplorationEvidenceUploadResponse(ExplorationEvidenceResponse):
    schema_version: Literal[1] = 1
    message_id: str
    runner_id: str
    idempotent: bool


class ExplorationResponse(BaseModel):
    id: str
    project_id: int
    plan_item_id: int
    plan_item_name: str | None = None
    plan_item_order: int | None = None
    runner_id: str
    environment_id: int | None
    session_profile_id: int | None
    use_login_credentials: bool
    headless: bool
    decision_prompt_id: int
    start_url: str
    allowed_origins: list[str]
    permissions: list[ExplorationPermission]
    max_steps: int
    current_step: int
    status: ExplorationStatus
    dispatch_status: ExplorationDispatchStatus
    message_id: str | None
    observation_count: int
    action_count: int
    steps: list[dict[str, Any]]
    evidence: list[ExplorationEvidenceResponse]
    result_summary: dict[str, Any] | None
    error_type: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @field_serializer("created_at", "updated_at", "started_at", "completed_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class ExplorationListResponse(BaseModel):
    items: list[ExplorationResponse]
    total: int


class ExplorationDispatchResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    runner_id: str
    status: ExplorationStatus
    dispatch_status: ExplorationDispatchStatus
    attempt_count: int
    published: bool
    idempotent: bool


class ExplorationMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER)


class ExplorationClaimResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    runner_id: str
    status: ExplorationStatus
    idempotent: bool


class ExplorationExecutionPlanSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: int
    storage_state: dict[str, Any] = Field(max_length=100, repr=False)


class ExplorationExecutionPlanSecret(BaseModel):
    """A named credential delivered only to the authenticated Runner."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["AI_TEST_USERNAME", "AI_TEST_PASSWORD"]
    value: SecretStr

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: SecretStr) -> str:
        return value.get_secret_value()


class ExplorationExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_type: Literal["WEB_EXPLORATION"] = "WEB_EXPLORATION"
    exploration_id: str
    message_id: str
    runner_id: str
    project_id: int
    start_url: str
    allowed_origins: list[str]
    permissions: list[ExplorationPermission]
    max_steps: int
    headless: bool
    objective: str
    planned_steps: list[str]
    expected_outcomes: list[str]
    session: ExplorationExecutionPlanSession | None = None
    secrets: list[ExplorationExecutionPlanSecret] = Field(
        default_factory=list, max_length=2, repr=False
    )


class ExplorationStartResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    runner_id: str
    status: ExplorationStatus
    idempotent: bool


class ExplorationControlResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    runner_id: str
    status: ExplorationStatus
    stop_requested: bool
    cancellation_requested: bool


class ExplorationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=50)
    page_url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    accessibility_snapshot: str = Field(min_length=1, max_length=200_000, repr=False)
    network_events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    previous_action: dict[str, Any] | None = Field(default=None, max_length=20)

    @field_validator("page_url")
    @classmethod
    def validate_page_url(cls, value: str) -> str:
        try:
            return _http_url(value)
        except (TypeError, ValueError):
            raise ValueError("page_url 必须为安全的 http/https URL") from None

    @model_validator(mode="after")
    def validate_size_and_secrets(self) -> "ExplorationObservation":
        if _SENSITIVE.search(self.accessibility_snapshot):
            raise ValueError("accessibility_snapshot 疑似包含凭据")
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 256_000:
            raise ValueError("探索观察不能超过 256KB")
        return self


class ExplorationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER)
    observation: ExplorationObservation


class ExplorationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["NAVIGATE", "CLICK", "TYPE", "SELECT", "PRESS", "WAIT", "FINISH"]
    reason: str = Field(min_length=1, max_length=1000)
    element: str | None = Field(default=None, min_length=1, max_length=500)
    ref: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    value: str | None = Field(default=None, max_length=2000, repr=False)
    url: str | None = Field(default=None, max_length=2048)
    expected_observation: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_shape(self) -> "ExplorationDecision":
        if self.action in {"CLICK", "TYPE", "SELECT"} and (not self.element or not self.ref):
            raise ValueError(f"{self.action} 必须引用当前快照中的 element/ref")
        if self.action in {"TYPE", "SELECT", "PRESS"} and self.value is None:
            raise ValueError(f"{self.action} 必须提供 value")
        if self.action == "NAVIGATE":
            if self.url is None:
                raise ValueError("NAVIGATE 必须提供 url")
            try:
                self.url = _http_url(self.url)
            except (TypeError, ValueError):
                raise ValueError("NAVIGATE url 无效") from None
        if self.value and _SENSITIVE.search(self.value):
            raise ValueError("探索决策不能携带凭据")
        return self


class ExplorationDecisionResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    sequence: int
    ai_call_id: int
    decision: ExplorationDecision


class ExplorationCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER)
    outcome: Literal["COMPLETED", "FAILED", "CANCELLED"]
    observations: list[ExplorationObservation] = Field(default_factory=list, max_length=50)
    action_trace: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    result_summary: dict[str, Any] | None = Field(default=None, max_length=100)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ExplorationCompleteRequest":
        if self.outcome == "COMPLETED" and (self.error_type or self.error_message):
            raise ValueError("COMPLETED 不能携带错误")
        if self.outcome == "FAILED" and (not self.error_type or not self.error_message):
            raise ValueError("FAILED 必须携带安全错误")
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 5_000_000:
            raise ValueError("探索结果不能超过 5MB")
        if _SENSITIVE.search(encoded):
            raise ValueError("探索结果疑似包含凭据")
        return self


class ExplorationCompleteResponse(BaseModel):
    schema_version: Literal[1] = 1
    exploration_id: str
    message_id: str
    runner_id: str
    status: ExplorationStatus
    idempotent: bool


class RevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: int = Field(gt=0)
    exploration_id: str | None = Field(default=None, min_length=1, max_length=128)
    recording_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "RevisionCreateRequest":
        if (self.exploration_id is None) == (self.recording_id is None):
            raise ValueError("必须且只能选择一次 MCP 探索或人工录制")
        return self


class WebReconcileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_name: str = Field(min_length=2, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    coverage_notes: list[str] = Field(default_factory=list, max_length=50)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=50)
    content: WebCaseContent


class RevisionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_content: WebCaseContent
    decision_note: str | None = Field(default=None, max_length=500)


class RevisionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ACCEPT", "REJECT"]
    name: str | None = Field(default=None, min_length=2, max_length=255)
    web_case_id: int | None = Field(default=None, gt=0)
    decision_note: str | None = Field(default=None, max_length=500)


class RevisionResponse(BaseModel):
    id: int
    project_id: int
    plan_item_id: int
    exploration_id: str | None
    recording_id: str | None
    prompt_id: int
    ai_call_id: int | None
    source_type: Literal["MCP_EXPLORATION", "MANUAL_RECORDING"]
    generation_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    status: Literal["DRAFT", "ACCEPTED", "REJECTED"]
    structured_result: WebReconcileResult | None
    human_content: WebCaseContent | None
    decision_note: str | None
    created_web_case_id: int | None
    created_web_case_version_id: int | None
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    reviewed_at: datetime | None
    error_message: str | None

    @field_serializer("created_at", "started_at", "completed_at", "reviewed_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class RevisionListResponse(BaseModel):
    items: list[RevisionResponse]
    total: int
