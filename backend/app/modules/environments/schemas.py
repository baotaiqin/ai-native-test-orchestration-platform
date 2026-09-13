from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class VariableType(StrEnum):
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    JSON = "JSON"
    LIST = "LIST"


class EnvironmentCreate(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    base_url: HttpUrl | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool = False


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    code: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"
    )
    base_url: HttpUrl | None = None
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_empty_update(self) -> "EnvironmentUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class EnvironmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    code: str
    base_url: str | None
    description: str | None
    is_default: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class EnvironmentListResponse(BaseModel):
    items: list[EnvironmentResponse]
    total: int


class VariableUpsert(BaseModel):
    value: str = Field(max_length=10000)
    value_type: VariableType = VariableType.STRING
    enabled: bool = True


class VariableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    environment_id: int
    key: str
    value: str
    value_type: VariableType
    enabled: bool
    created_at: datetime
    updated_at: datetime


class VariableListResponse(BaseModel):
    items: list[VariableResponse]
    total: int
