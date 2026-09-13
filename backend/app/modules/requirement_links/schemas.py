from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.core.time import to_utc_isoformat


class RequirementAssetType(StrEnum):
    TEST_CASE = "TEST_CASE"
    WEB_CASE = "WEB_CASE"


class RequirementLinkStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class RequirementAuditTimeBasis(StrEnum):
    UTC = "UTC"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class RequirementImpactStatus(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    POSSIBLY_OUTDATED = "POSSIBLY_OUTDATED"
    BASELINE_UNKNOWN = "BASELINE_UNKNOWN"


class RequirementLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: RequirementAssetType
    asset_id: int = Field(gt=0, le=2_147_483_647)
    requirement_version_id: int = Field(gt=0, le=2_147_483_647)
    asset_version_id: int = Field(gt=0, le=2_147_483_647)
    relation_type: str = Field(
        default="COVERAGE",
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    confidence: float = Field(default=1.0, ge=0, le=1)


class RequirementLinkQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=100)
    include_removed: bool = False


class RequirementImpactQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version_id: int = Field(gt=0, le=2_147_483_647)
    to_version_id: int = Field(gt=0, le=2_147_483_647)
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=100)


class RequirementVersionReference(BaseModel):
    id: int
    version_no: int
    content_hash: str


class LinkedAssetReference(BaseModel):
    asset_type: RequirementAssetType
    id: int
    code: str
    name: str
    status: str
    case_type: str | None
    current_version_id: int | None


class LinkedAssetVersionReference(BaseModel):
    id: int
    version_no: int
    status: str | None


class RequirementLinkResponse(BaseModel):
    id: int
    requirement_id: int
    requirement_code: str
    requirement_title: str
    requirement_status: str
    requirement_version_id: int | None
    requirement_version: RequirementVersionReference | None
    requirement_baseline_known: bool
    asset_type: RequirementAssetType
    asset_id: int
    asset: LinkedAssetReference
    asset_version_id: int | None
    asset_version: LinkedAssetVersionReference | None
    asset_version_known: bool
    binding_note: str
    relation_type: str
    source: str
    confidence: float
    status: RequirementLinkStatus
    is_current_relation: bool
    supersedes_link_id: int | None
    created_by: str | None
    created_at_time_basis: RequirementAuditTimeBasis
    created_at: datetime
    removed_by: str | None
    removed_at: datetime | None
    removed_at_time_basis: Literal["UTC"] | None
    historical_scope: Literal["CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT"] = (
        "CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT"
    )


class RequirementLinkPage(BaseModel):
    items: list[RequirementLinkResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class RequirementImpactComparison(BaseModel):
    requirement_id: int
    from_version: RequirementVersionReference
    to_version: RequirementVersionReference
    content_changed: bool
    additions: int
    deletions: int


class RequirementImpactItem(RequirementLinkResponse):
    selected_scope_status: RequirementImpactStatus
    selected_scope_reason: str
    recorded_baseline_status: RequirementImpactStatus
    recorded_baseline_reason: str


class RequirementImpactPage(BaseModel):
    comparison: RequirementImpactComparison
    items: list[RequirementImpactItem]
    total: int
    page: int
    page_size: int
    has_more: bool
    scope_note: str = "确定性关联范围，尚未经人工确认实际受影响"


class RequirementTraceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=100)


class RequirementTraceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int
    capture_id: int
    requirement_id: int
    requirement_version_id: int | None
    requirement_version_no: int | None
    requirement_version_binding: str
    target_type: str
    target_asset_id: int
    target_version_id: int
    asset_version_binding: str
    run_id: str
    run_code: str
    run_type: str
    run_status: str
    run_created_at: datetime
    run_ended_at: datetime | None
    case_run_id: int
    case_sequence_no: int
    case_status: str
    evidence_count: int = Field(ge=0)
    captured_at: datetime
    report_path: str
    evidence_path: str
    historical_scope: Literal["RUN_CREATION_SNAPSHOT"] = "RUN_CREATION_SNAPSHOT"

    @field_serializer("run_created_at", "run_ended_at", "captured_at", when_used="json")
    def serialize_time(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class RequirementTracePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RequirementTraceItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    has_more: bool
    scope_note: str = "历史执行来源快照；不使用当前关联补造旧 Run"
