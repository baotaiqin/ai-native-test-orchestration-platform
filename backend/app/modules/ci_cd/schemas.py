import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.test_plans.schemas import TestPlanRunResponse

_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


class CiTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    expires_in_days: int = Field(default=90, ge=1, le=365)
    allowed_plan_ids: list[int] | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_plan_ids")
    @classmethod
    def normalize_plan_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(item <= 0 for item in value):
            raise ValueError("Test Plan ID 必须为正整数")
        return sorted(set(value))


class CiTokenMetadataResponse(BaseModel):
    id: int
    project_id: int
    name: str
    token_prefix: str
    allowed_plan_ids: list[int] | None
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"]
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: str
    created_at: datetime

    @field_serializer("expires_at", "last_used_at", "revoked_at", "created_at", when_used="json")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class CiTokenCreateResponse(BaseModel):
    token: str
    metadata: CiTokenMetadataResponse


class CiTokenListResponse(BaseModel):
    items: list[CiTokenMetadataResponse]
    total: int


class CiRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_ref: str | None = Field(default=None, max_length=255)
    commit_sha: str | None = Field(default=None, max_length=64)

    @field_validator("pipeline_ref", "commit_sha")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str | None) -> str | None:
        if value is not None and _COMMIT_SHA.fullmatch(value) is None:
            raise ValueError("commit_sha 必须是 7 到 64 位十六进制提交哈希")
        return value.lower() if value else None


class CiRunStartResponse(BaseModel):
    plan_run_id: str
    status: str
    status_url: str
    idempotent_replay: bool


class CiRunStatusResponse(BaseModel):
    invocation_id: str
    pipeline_ref: str | None
    commit_sha: str | None
    plan_run: TestPlanRunResponse


class WebhookEndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    url: SecretStr = Field(min_length=8, max_length=2048)
    signing_secret: SecretStr | None = Field(default=None, min_length=16, max_length=256)
    events: list[Literal["TEST_PLAN_RUN_COMPLETED"]] = Field(
        default_factory=lambda: ["TEST_PLAN_RUN_COMPLETED"], min_length=1, max_length=1
    )
    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=5)
    timeout_seconds: int = Field(default=10, ge=1, le=30)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username:
            raise ValueError("Webhook URL 必须是无内嵌凭据的 HTTPS 地址")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Webhook URL 端口无效") from exc
        if parsed.fragment:
            raise ValueError("Webhook URL 不能包含 fragment")
        return value

    @field_validator("events")
    @classmethod
    def normalize_events(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class WebhookEndpointResponse(BaseModel):
    id: int
    project_id: int
    name: str
    target_hint: str
    has_signing_secret: bool
    events: list[str]
    enabled: bool
    status: Literal["ACTIVE", "ARCHIVED"]
    max_attempts: int
    timeout_seconds: int
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_datetimes(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class WebhookEndpointListResponse(BaseModel):
    items: list[WebhookEndpointResponse]
    total: int


class WebhookDeliveryResponse(BaseModel):
    id: str
    endpoint_id: int
    endpoint_name: str
    project_id: int
    plan_run_id: str
    event_id: str
    event_type: str
    status: Literal["PENDING", "SENDING", "RETRY", "SUCCEEDED", "FAILED"]
    attempt_count: int
    next_attempt_at: datetime
    response_status: int | None
    last_error_code: str | None
    last_error_message: str | None
    last_attempt_at: datetime | None
    created_at: datetime
    completed_at: datetime | None

    @field_serializer(
        "next_attempt_at",
        "last_attempt_at",
        "created_at",
        "completed_at",
        when_used="json",
    )
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebhookDeliveryListResponse(BaseModel):
    items: list[WebhookDeliveryResponse]
    total: int
