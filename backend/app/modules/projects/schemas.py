from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.core.time import to_utc_isoformat


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ProjectRole(StrEnum):
    PROJECT_OWNER = "PROJECT_OWNER"
    TESTER = "TESTER"
    VIEWER = "VIEWER"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    description: str | None = Field(default=None, max_length=2000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    code: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"
    )
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def reject_empty_update(self) -> "ProjectUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: str | None
    status: ProjectStatus
    owner_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    archived_at_basis: str | None
    current_user_role: ProjectRole | None = None

    @field_serializer("archived_at", when_used="json")
    def serialize_archived_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if self.archived_at_basis == "UTC":
            return to_utc_isoformat(value)
        # Historical values were written with datetime.now() and have no reliable
        # timezone provenance. Preserve their literal wall-clock value and expose
        # the basis explicitly instead of falsely attaching a UTC offset.
        return value.isoformat()


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int


class ProjectMemberCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    role: ProjectRole


class ProjectMemberUpdate(BaseModel):
    role: ProjectRole


class ProjectMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    user_id: str
    role: ProjectRole
    created_at: datetime


class ProjectMemberListResponse(BaseModel):
    items: list[ProjectMemberResponse]
    total: int
