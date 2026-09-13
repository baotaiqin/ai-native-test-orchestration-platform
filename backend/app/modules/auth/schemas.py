import re
import unicodedata
from datetime import datetime
from enum import StrEnum

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

_USERNAME = re.compile(r"^[a-z0-9_.@-]+$")


def normalize_username(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 1 <= len(normalized) <= 64 or _USERNAME.fullmatch(normalized) is None:
        raise ValueError("用户名只能包含字母、数字及 _ . @ -")
    return normalized


def _normalize_display_name(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 128:
        raise ValueError("显示名称长度必须在 1 到 128 个字符之间")
    return normalized


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class PlatformRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256, repr=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    display_name: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUser


class LogoutResponse(BaseModel):
    revoked: bool = True


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=1, max_length=256, repr=False)
    new_password: SecretStr = Field(min_length=8, max_length=256, repr=False)


class ChangePasswordResponse(BaseModel):
    changed: bool = True
    reauthentication_required: bool = True


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=8, max_length=256, repr=False)
    platform_role: PlatformRole = PlatformRole.USER
    status: UserStatus = UserStatus.ACTIVE

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return _normalize_display_name(value)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    platform_role: PlatformRole | None = None
    status: UserStatus | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return _normalize_display_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "UserUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("用户更新字段不能为 null")
        return self


class UserListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    status: UserStatus
    platform_role: PlatformRole
    auth_version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int
