from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class RunnerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class RegistrationTokenStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class OnlineStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class RunnerCapabilityName(StrEnum):
    API = "API"
    WEB = "WEB"
    SQL = "SQL"
    SCRIPT = "SCRIPT"
    SSE = "SSE"
    JMETER = "JMETER"


class RunnerCapabilityStatus(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


class RunnerSlotType(StrEnum):
    API = "API"
    WEB = "WEB"
    PERFORMANCE = "PERFORMANCE"


class RunnerCapabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RunnerCapabilityName
    status: RunnerCapabilityStatus
    reason: str | None = Field(default=None, max_length=256)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_reason(self) -> "RunnerCapabilityInput":
        if self.status == RunnerCapabilityStatus.UNAVAILABLE and not self.reason:
            raise ValueError("UNAVAILABLE capability 必须提供 reason")
        return self


class RunnerSlotInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: RunnerSlotType = Field(validation_alias=AliasChoices("type", "slot_type"))
    total: int = Field(ge=0, le=10000)
    available: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def validate_available(self) -> "RunnerSlotInput":
        if self.available > self.total:
            raise ValueError("slot available 不能大于 total")
        return self


class RunnerSnapshotFields(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=255)
    ip_address: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("ip_address", "ip"),
    )
    os: str | None = Field(default=None, max_length=128)
    cpu: str | None = Field(default=None, max_length=128)
    ram: str | None = Field(default=None, max_length=128)
    disk: str | None = Field(default=None, max_length=128)
    python: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("python", "python_version"),
    )
    chrome: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("chrome", "chrome_version"),
    )
    playwright: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("playwright", "playwright_version"),
    )
    java: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("java", "java_version"),
    )
    jmeter: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("jmeter", "jmeter_version"),
    )
    tags: list[str] = Field(default_factory=list, max_length=50)
    capabilities: list[RunnerCapabilityInput] = Field(default_factory=list, max_length=20)
    slots: list[RunnerSlotInput] = Field(default_factory=list, max_length=10)

    @field_validator("name", "hostname")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Runner name/hostname 不能为空")
        return value

    @field_validator(
        "ip_address",
        "os",
        "cpu",
        "ram",
        "disk",
        "python",
        "chrome",
        "playwright",
        "java",
        "jmeter",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Runner 系统信息必须是字符串或 null")
        value = value.strip()
        return value or None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise ValueError("Runner tag 必须是字符串")
            tag = value.strip().lower()
            if not tag or len(tag) > 64:
                raise ValueError("Runner tag 长度无效")
            if not all(character.isalnum() or character in "_.-" for character in tag):
                raise ValueError("Runner tag 仅支持字母、数字、下划线、短横线和点")
            if tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized

    @model_validator(mode="after")
    def validate_unique_collections(self) -> "RunnerSnapshotFields":
        capabilities = [item.name.value for item in self.capabilities]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("Runner capabilities 不允许重复")
        slot_types = [item.type.value for item in self.slots]
        if len(slot_types) != len(set(slot_types)):
            raise ValueError("Runner slots 不允许重复")
        return self


class RunnerRegistrationRequest(RunnerSnapshotFields):
    registration_token: str = Field(min_length=16, max_length=512)


class RunnerHeartbeatRequest(RunnerSnapshotFields):
    pass


class RegistrationTokenCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_in_seconds: int | None = Field(default=None, ge=60, le=3600)


class RegistrationTokenCreateResponse(BaseModel):
    id: int
    token: str
    status: RegistrationTokenStatus
    expires_at: datetime


class RegistrationTokenMetadataResponse(BaseModel):
    id: int
    status: RegistrationTokenStatus
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    consumed_runner_id: str | None
    created_by: str
    created_at: datetime


class RegistrationTokenListResponse(BaseModel):
    items: list[RegistrationTokenMetadataResponse]
    total: int


class RunnerCapabilityResponse(BaseModel):
    name: RunnerCapabilityName
    status: RunnerCapabilityStatus
    reason: str | None


class RunnerSlotResponse(BaseModel):
    type: RunnerSlotType
    total: int
    available: int


class RunnerResponse(BaseModel):
    id: str
    name: str
    hostname: str
    ip_address: str | None
    os: str | None
    cpu: str | None
    ram: str | None
    disk: str | None
    python: str | None
    chrome: str | None
    playwright: str | None
    java: str | None
    jmeter: str | None
    status: RunnerStatus
    online: bool
    online_status: OnlineStatus
    redis_available: bool
    heartbeat_interval_seconds: int
    last_heartbeat_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tags: list[str]
    capabilities: list[RunnerCapabilityResponse]
    slots: list[RunnerSlotResponse]


class RunnerDisconnectReconcileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: str
    online_status: OnlineStatus
    redis_available: bool
    reconciled_run_count: int = Field(ge=0)
    reconciled_run_ids: list[str] = Field(default_factory=list)


class RunnerListResponse(BaseModel):
    items: list[RunnerResponse]
    total: int


class RunnerRegisterResponse(BaseModel):
    runner_id: str
    credential: str
    heartbeat_interval_seconds: int


class RunnerHeartbeatResponse(BaseModel):
    runner_id: str
    status: RunnerStatus
    heartbeat_interval_seconds: int
    server_time: datetime
