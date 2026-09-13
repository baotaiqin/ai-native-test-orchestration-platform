import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.web_cases.schemas import LocatorStrategy, WebCaseContent

_SAFE_NAME = r"^[A-Za-z][A-Za-z0-9_.-]*$"
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "cookie",
        "password",
        "passwd",
        "secret",
        "session",
        "session_id",
        "token",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|token|access[_-]?token|secret|"
    r"credential|authorization|cookie|api[_-]?key)\s*[:=]\s*[^\s,;]+)"
)
_RUNTIME_PLACEHOLDER = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9 _+.-]{1,64}$")
_SENSITIVE_VALUE_WORD = re.compile(
    r"(?i)(?:password|passwd|token|access[_-]?token|secret|credential|"
    r"authorization|cookie|api[_-]?key)"
)


def _validate_safe_url(value: str | None, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("录制事件必须提供 URL")
        return None
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("录制 URL 不能为空且不能包含空白字符")
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("录制 URL 必须是 http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("录制 URL 不允许携带凭据或 fragment")
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if normalized_name in _SENSITIVE_QUERY_NAMES or any(
            marker in normalized_name
            for marker in ("token", "secret", "password", "credential", "authorization")
        ):
            raise ValueError("录制 URL 不能包含敏感查询参数")
    return normalized


def sanitize_public_url(value: str | None) -> str | None:
    """Keep only the stable origin/path in ordinary user-facing responses."""

    if value is None:
        return None
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _validate_safe_text(value: str | None, *, maximum: int, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} 超过长度限制")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError(f"{label} 不能包含换行")
    if _SENSITIVE_TEXT.search(normalized):
        raise ValueError(f"{label} 不能包含明文凭据")
    return normalized


class WebRecordingStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WebRecordingDispatchStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class WebRecordingEventType(StrEnum):
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    FILL = "FILL"
    SELECT = "SELECT"
    PRESS = "PRESS"


class WebRecordingOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WebRecordingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: LocatorStrategy
    value: str = Field(min_length=1, max_length=2000)
    priority: int = Field(ge=1, le=20)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        return _validate_safe_text(value, maximum=2000, label="Locator") or ""


class WebRecordingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: WebRecordingEventType
    sequence: int = Field(gt=0, le=1_000_000)
    relative_time_ms: int = Field(ge=0, le=86_400_000)
    page_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    target_url: str | None = Field(default=None, max_length=2048)
    locator_candidates: list[WebRecordingLocator] = Field(default_factory=list, max_length=10)
    value: str | None = Field(default=None, max_length=10_000, repr=False)
    key: str | None = Field(default=None, max_length=64)

    @field_validator("page_url", "target_url")
    @classmethod
    def normalize_urls(cls, value: str | None) -> str | None:
        return _validate_safe_url(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return _validate_safe_text(value, maximum=512, label="页面标题")

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        normalized = _validate_safe_text(value, maximum=64, label="按键")
        if normalized is not None and not _SAFE_KEY.fullmatch(normalized):
            raise ValueError("按键包含不支持的字符")
        return normalized

    @model_validator(mode="after")
    def validate_event_shape(self) -> "WebRecordingEvent":
        priorities = [item.priority for item in self.locator_candidates]
        if len(priorities) != len(set(priorities)):
            raise ValueError("同一录制事件的 Locator priority 必须唯一")
        if self.event_type == WebRecordingEventType.NAVIGATE:
            if self.target_url is None:
                raise ValueError("NAVIGATE 事件必须提供 target_url")
            if self.locator_candidates or self.value is not None or self.key is not None:
                raise ValueError("NAVIGATE 事件字段不匹配")
        elif self.event_type == WebRecordingEventType.FILL:
            if self.value is None or not (
                self.value == "REDACTED" or _RUNTIME_PLACEHOLDER.fullmatch(self.value)
            ):
                raise ValueError("FILL 事件只能使用 Runtime 占位符或 REDACTED")
            if self.key is not None or self.target_url is not None:
                raise ValueError("FILL 事件字段不匹配")
        elif self.event_type == WebRecordingEventType.SELECT:
            if self.value is None or not self.value.strip():
                raise ValueError("SELECT 事件必须提供非空 value")
            if _SENSITIVE_VALUE_WORD.search(self.value):
                raise ValueError("SELECT 事件不能包含敏感值")
            if self.key is not None or self.target_url is not None:
                raise ValueError("SELECT 事件字段不匹配")
        elif self.event_type == WebRecordingEventType.PRESS:
            if self.key is None:
                raise ValueError("PRESS 事件必须提供 key")
            if self.value is not None or self.target_url is not None:
                raise ValueError("PRESS 事件字段不匹配")
        else:
            if self.value is not None or self.key is not None or self.target_url is not None:
                raise ValueError("CLICK 事件字段不匹配")
        return self


class WebRecordingTaskEnvelopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_type: Literal["WEB_RECORDING"] = "WEB_RECORDING"
    recording_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    runner_id: str = Field(min_length=1, max_length=64)
    project_id: int = Field(gt=0)
    attempt: int = Field(ge=1, le=10)
    enqueued_at: datetime


class WebRecordingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    runner_id: str = Field(min_length=1, max_length=64)
    environment_id: int | None = Field(default=None, gt=0)
    start_url: str = Field(min_length=1, max_length=2048)
    session_profile_id: int | None = Field(default=None, gt=0)
    plan_item_id: int | None = Field(default=None, gt=0)
    save_session: bool = False
    save_session_name: str | None = Field(
        default=None, min_length=2, max_length=128, pattern=_SAFE_NAME
    )
    save_session_expires_at: datetime | None = None

    @field_validator("start_url")
    @classmethod
    def normalize_start_url(cls, value: str) -> str:
        return _validate_safe_url(value, required=True) or ""

    @model_validator(mode="after")
    def validate_save_session(self) -> "WebRecordingCreateRequest":
        if self.save_session and self.save_session_name is None:
            raise ValueError("save_session=true 时必须提供 save_session_name")
        if not self.save_session and (
            self.save_session_name is not None or self.save_session_expires_at is not None
        ):
            raise ValueError("未启用保存 Session 时不能提供保存配置")
        return self


class WebRecordingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: int
    runner_id: str
    environment_id: int | None
    session_profile_id: int | None
    plan_item_id: int | None
    saved_session_profile_id: int | None
    start_url: str
    browser: Literal["CHROME"]
    status: WebRecordingStatus
    dispatch_status: WebRecordingDispatchStatus
    message_id: str | None
    event_count: int = Field(ge=0, le=1000)
    save_session: bool
    confirmed_web_case_id: int | None
    confirmed_web_case_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebRecordingEventPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: WebRecordingEventType
    sequence: int
    relative_time_ms: int
    page_url: str | None
    title: str | None
    target_url: str | None
    locator_candidates: list[WebRecordingLocator] = Field(default_factory=list, max_length=10)
    value: str | None = Field(default=None, repr=False)
    key: str | None = None


class WebRecordingDetailResponse(WebRecordingListItem):
    events: list[WebRecordingEventPreview] = Field(default_factory=list, max_length=1000)
    dom_context_available: bool
    stop_requested_at: datetime | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_type: str | None
    error_message: str | None
    confirmed_by: str | None
    confirmed_at: datetime | None

    @field_serializer(
        "stop_requested_at",
        "cancel_requested_at",
        "started_at",
        "completed_at",
        "confirmed_at",
        when_used="json",
    )
    def serialize_optional_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WebRecordingListItem]
    total: int
    page: int
    page_size: int


class WebRecordingDispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    message_id: str
    runner_id: str
    status: WebRecordingStatus
    dispatch_status: WebRecordingDispatchStatus
    attempt_count: int = Field(ge=0, le=10)
    published: bool
    idempotent: bool


class WebRecordingClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class WebRecordingClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    message_id: str
    runner_id: str
    status: WebRecordingStatus
    claimed_at: datetime | None
    idempotent: bool

    @field_serializer("claimed_at", when_used="json")
    def serialize_claimed_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingExecutionPlanSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: int
    storage_state: dict[str, Any] = Field(max_length=100, repr=False)


class WebRecordingExecutionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_type: Literal["WEB_RECORDING"] = "WEB_RECORDING"
    recording_id: str
    message_id: str
    runner_id: str
    project_id: int
    environment_id: int | None
    start_url: str
    browser: Literal["CHROME"] = "CHROME"
    headless: Literal[False] = False
    session: WebRecordingExecutionPlanSession | None = None
    save_session: bool
    save_session_name: str | None
    save_session_expires_at: datetime | None

    @field_serializer("save_session_expires_at", when_used="json")
    def serialize_expiry(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class WebRecordingExecutionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    message_id: str
    runner_id: str
    status: WebRecordingStatus
    started_at: datetime | None
    idempotent: bool

    @field_serializer("started_at", when_used="json")
    def serialize_started_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    message_id: str
    runner_id: str
    status: WebRecordingStatus
    stop_requested: bool
    cancellation_requested: bool


class WebRecordingCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    outcome: WebRecordingOutcome
    events: list[WebRecordingEvent] = Field(default_factory=list, max_length=1000)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1000)
    storage_state: dict[str, Any] | None = Field(default=None, max_length=100, repr=False)
    dom_context: dict[str, Any] | None = Field(default=None, max_length=100, repr=False)

    @field_validator("error_type", "error_message")
    @classmethod
    def normalize_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _SENSITIVE_TEXT.sub("<redacted>", value.strip())
        return normalized[:1000]

    @model_validator(mode="after")
    def validate_complete_payload(self) -> "WebRecordingCompleteRequest":
        sequences = [item.sequence for item in self.events]
        if len(sequences) != len(set(sequences)):
            raise ValueError("录制事件 sequence 必须唯一")
        if self.outcome == WebRecordingOutcome.COMPLETED:
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("COMPLETED 不能提供错误字段")
        elif self.outcome == WebRecordingOutcome.FAILED and (
            not self.error_type or not self.error_message
        ):
            raise ValueError(f"{self.outcome.value} 必须提供 error_type 和 error_message")
        if self.dom_context is not None:
            encoded = json.dumps(self.dom_context, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 524_288:
                raise ValueError("DOM context 不能超过 512KB")
        if self.storage_state is not None:
            encoded = json.dumps(self.storage_state, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 1_000_000:
                raise ValueError("Storage State 不能超过 1MB")
        return self


class WebRecordingCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    message_id: str
    runner_id: str
    outcome: WebRecordingOutcome
    status: WebRecordingStatus
    event_count: int = Field(ge=0, le=1000)
    saved_session_profile_id: int | None
    completed_at: datetime | None
    idempotent: bool

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web_case_id: int | None = Field(default=None, gt=0)
    ai_suggestion_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=2, max_length=255)
    content: WebCaseContent | None = None
    decision_note: str | None = Field(default=None, max_length=500, repr=False)

    @field_validator("decision_note")
    @classmethod
    def validate_decision_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("decision_note 不能为空且不能换行")
        if _SENSITIVE_TEXT.search(normalized):
            raise ValueError("decision_note 不能包含凭据")
        return normalized

    @model_validator(mode="after")
    def validate_target(self) -> "WebRecordingConfirmRequest":
        if self.ai_suggestion_id is None and self.content is None:
            raise ValueError("手工确认必须提供 Web Case 内容")
        if self.ai_suggestion_id is None and self.web_case_id is None and not self.name:
            raise ValueError("创建新 Web Case 时必须提供 name")
        if self.web_case_id is not None and self.name is not None:
            raise ValueError("指定 Web Case 时不能同时提供 name")
        return self


class WebRecordingConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str
    web_case_id: int
    web_case_version_id: int
    status: Literal["DRAFT"]
    idempotent: bool
