import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.modules.test_cases.schemas import FakerGenerator


class DatasetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class DatasetSourceType(StrEnum):
    CSV = "CSV"
    EXCEL = "EXCEL"
    FAKER = "FAKER"
    MYSQL = "MYSQL"


class DatasetColumn(BaseModel):
    name: str = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")
    value_type: str = Field(default="STRING", min_length=1, max_length=32)


class FakerField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    generator: FakerGenerator


class FakerDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[FakerField] = Field(min_length=1, max_length=100)
    row_count: int = Field(ge=1, le=10_000)
    seed: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)

    @model_validator(mode="after")
    def unique_fields(self) -> "FakerDatasetConfig":
        names = [item.name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Faker 字段名不能重复")
        return self


class MySQLDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int = Field(gt=0)
    sql: str = Field(min_length=1, max_length=10_000)
    params: dict[str, Any] | list[Any] = Field(default_factory=dict)
    max_rows: int = Field(default=100, ge=1, le=10_000)

    @model_validator(mode="after")
    def reject_secret_fields(self) -> "MySQLDatasetConfig":
        # A config can contain query parameters, never connection credentials.
        forbidden = {"password", "secret", "secret_value", "encrypted_value"}
        keys = {str(key).lower() for key in self.params} if isinstance(self.params, dict) else set()
        if keys & forbidden:
            raise ValueError("MySQL 参数不能包含密码或 Secret 字段")
        return self


class CsvDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delimiter: Literal[",", ";", "\t"] = ","


class ExcelDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str | None = Field(default=None, max_length=255)
    header_row: int = Field(default=1, ge=1, le=10_000)


class DatasetCreate(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    source_type: DatasetSourceType
    config: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    columns: list[DatasetColumn] = Field(default_factory=list, max_length=100)
    snapshot: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)


class DatasetVersionCreate(BaseModel):
    source_type: DatasetSourceType
    config: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    columns: list[DatasetColumn] = Field(default_factory=list, max_length=100)
    snapshot: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    status: DatasetStatus
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class DatasetVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    version_no: int
    source_type: DatasetSourceType
    columns: list[DatasetColumn]
    row_count: int
    config: dict[str, Any]
    source_metadata: dict[str, Any]
    snapshot: list[dict[str, Any]] = Field(
        validation_alias=AliasChoices("snapshot", "actual_data")
    )
    created_by: str
    created_at: datetime


class DatasetDetailResponse(DatasetResponse):
    current_version: DatasetVersionResponse | None = None


class DatasetListResponse(BaseModel):
    items: list[DatasetResponse]
    total: int


class DatasetPreviewResponse(BaseModel):
    source_type: DatasetSourceType
    columns: list[DatasetColumn]
    row_count: int
    rows: list[dict[str, Any]]
    snapshot: list[dict[str, Any]]
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class DatasetMySQLPreviewRequest(BaseModel):
    project_id: int = Field(gt=0)
    config: MySQLDatasetConfig


class DatasetFakerPreviewRequest(BaseModel):
    config: FakerDatasetConfig


class DatasetIterationPreviewRequest(BaseModel):
    dataset_id: int = Field(gt=0)
    dataset_version_id: int | None = Field(default=None, gt=0)
    prefix: str = Field(default="", max_length=64, pattern=r"^(|[A-Za-z_][A-Za-z0-9_.-]*)$")
    column_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_mapping_targets(self) -> "DatasetIterationPreviewRequest":
        targets = list(self.column_mapping.values())
        if any(not target.strip() for target in targets):
            raise ValueError("列映射目标不能为空")
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", target)
            for target in targets
        ):
            raise ValueError("列映射目标名称非法")
        if any(target.startswith("__") for target in targets):
            raise ValueError("列映射目标不能以 __ 开头")
        if len(targets) != len(set(targets)):
            raise ValueError("列映射目标不能重复")
        return self


class DatasetIteration(BaseModel):
    dataset_id: int
    dataset_version_id: int
    row_index: int
    parameters: dict[str, Any]
    context: dict[str, Any]


class DatasetIterationPreviewResponse(BaseModel):
    items: list[DatasetIteration]
    total: int
    limit: int
    offset: int


class DatasetSheetListResponse(BaseModel):
    items: list[str]
