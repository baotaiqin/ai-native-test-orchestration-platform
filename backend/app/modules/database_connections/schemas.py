from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatabaseConnectionCreate(BaseModel):
    project_id: int = Field(gt=0)
    environment_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password_secret_id: int = Field(gt=0)
    ssl_enabled: bool = False


class DatabaseConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password_secret_id: int | None = Field(default=None, gt=0)
    ssl_enabled: bool | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_empty_update(self) -> "DatabaseConnectionUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class DatabaseConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    environment_id: int
    name: str
    host: str
    port: int
    database_name: str
    username: str
    password_secret_id: int
    password_masked: str = "••••••••"
    ssl_enabled: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DatabaseConnectionListResponse(BaseModel):
    items: list[DatabaseConnectionResponse]
    total: int


class ConnectionTestResponse(BaseModel):
    status: Literal["ok", "failed"]
    latency_ms: float
    database: str | None = None
    server_version: str | None = None
    message: str
