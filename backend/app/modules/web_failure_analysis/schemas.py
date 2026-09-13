import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.time import to_utc_isoformat

_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|token|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|secret|api[_-]?key|credential)"
    r"\s*[:=]\s*[^\s,;]+|https?://[^\s,;]+)"
)
_SENSITIVE_WORD = re.compile(
    r"(?i)\b(?:password|passwd|token|access[_-]?token|refresh[_-]?token|"
    r"authorization|cookie|secret|api[_-]?key|credential)\b"
)
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _safe_text(value: str, field_name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        raise ValueError(f"{field_name} 不能为空且不能换行")
    if _SENSITIVE_TEXT.search(normalized) or _SENSITIVE_WORD.search(normalized):
        raise ValueError(f"{field_name} 不能包含敏感信息")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} 超过长度限制")
    return normalized


class WebFailureAnalysisStatus(StrEnum):
    DRAFT = "DRAFT"
    COMPLETED = "COMPLETED"


class WebFailureCategory(StrEnum):
    LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
    ACTION_FAILED = "ACTION_FAILED"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    TIMEOUT = "TIMEOUT"
    NAVIGATION = "NAVIGATION"
    NETWORK = "NETWORK"
    SESSION = "SESSION"
    PAGE_CHANGED = "PAGE_CHANGED"
    UNKNOWN = "UNKNOWN"


class WebFailureSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class WebFailureAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_category: WebFailureCategory
    severity: WebFailureSeverity
    summary: str = Field(min_length=1, max_length=1000)
    root_cause: str = Field(min_length=1, max_length=2000)
    recommendations: list[str] = Field(min_length=1, max_length=8)
    evidence_node_ids: list[str] = Field(default_factory=list, max_length=40)
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool

    @field_validator("summary", "root_cause")
    @classmethod
    def validate_long_text(cls, value: str, info) -> str:
        return _safe_text(value, info.field_name, maximum=2000)

    @field_validator("recommendations")
    @classmethod
    def validate_recommendations(cls, value: list[str]) -> list[str]:
        return [_safe_text(item, "recommendations", maximum=500) for item in value]

    @field_validator("evidence_node_ids")
    @classmethod
    def validate_node_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not _NODE_ID.fullmatch(item):
                raise ValueError("evidence_node_ids 含有无效节点")
            if item in normalized:
                raise ValueError("evidence_node_ids 不能重复")
            normalized.append(item)
        return normalized


class WebFailureAnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_run_id: int = Field(gt=0)
    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(
        default=None, max_length=5000, repr=False
    )

    @field_validator("additional_instructions")
    @classmethod
    def validate_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_text(value, "additional_instructions", maximum=5000)


class WebFailureAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: int
    project_id: int
    run_id: str
    case_run_id: int
    status: WebFailureAnalysisStatus
    ai_call_id: int | None
    actual_model: str | None
    prompt_version_id: int
    output_schema_id: int
    fallback_used: bool | None
    repair_used: bool | None
    source_snapshot_sha256: str = Field(min_length=64, max_length=64)
    source_snapshot_size: int = Field(ge=0, le=131072)
    structured_result: WebFailureAnalysisResult | None
    created_by: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebFailureAnalysisListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WebFailureAnalysisResponse]
    total: int
