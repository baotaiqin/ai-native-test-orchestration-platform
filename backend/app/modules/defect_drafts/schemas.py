from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat


def _clean_text(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} 不能为空")
    return cleaned


class DefectDraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    module: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=500)
    preconditions: list[str] = Field(default_factory=list, max_length=30)
    reproduction_steps: list[str] = Field(min_length=1, max_length=50)
    expected_result: str = Field(min_length=1, max_length=8000)
    actual_result: str = Field(min_length=1, max_length=8000)
    evidence: list[str] = Field(default_factory=list, max_length=50)
    ai_analysis: str = Field(min_length=1, max_length=8000)

    @field_validator(
        "title", "module", "environment", "expected_result", "actual_result", "ai_analysis"
    )
    @classmethod
    def normalize_text(cls, value: str, info) -> str:
        return _clean_text(value, field=info.field_name)

    @field_validator("preconditions", "reproduction_steps", "evidence")
    @classmethod
    def normalize_items(cls, value: list[str], info) -> list[str]:
        maximum = 2000 if info.field_name == "evidence" else 1000
        cleaned = [_clean_text(item, field=info.field_name) for item in value]
        if any(len(item) > maximum for item in cleaned):
            raise ValueError(f"{info.field_name} 单项超过长度限制")
        return cleaned


class DefectDraftGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: int = Field(gt=0)
    case_run_id: int | None = Field(default=None, gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000)

    @field_validator("additional_instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class DefectDraftAiResult(DefectDraftContent):
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_human_review: bool = True


class DefectDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    module: str | None = Field(default=None, min_length=1, max_length=200)
    environment: str | None = Field(default=None, min_length=1, max_length=500)
    preconditions: list[str] | None = Field(default=None, max_length=30)
    reproduction_steps: list[str] | None = Field(default=None, min_length=1, max_length=50)
    expected_result: str | None = Field(default=None, min_length=1, max_length=8000)
    actual_result: str | None = Field(default=None, min_length=1, max_length=8000)
    evidence: list[str] | None = Field(default=None, max_length=50)
    ai_analysis: str | None = Field(default=None, min_length=1, max_length=8000)

    @model_validator(mode="after")
    def validate_update(self) -> "DefectDraftUpdateRequest":
        changes = self.model_dump(exclude_unset=True, exclude={"revision"})
        if not changes:
            raise ValueError("至少提供一个需要更新的字段")
        DefectDraftContent.model_validate(
            {
                "title": changes.get("title", "placeholder"),
                "module": changes.get("module", "placeholder"),
                "environment": changes.get("environment", "placeholder"),
                "preconditions": changes.get("preconditions", []),
                "reproduction_steps": changes.get("reproduction_steps", ["placeholder"]),
                "expected_result": changes.get("expected_result", "placeholder"),
                "actual_result": changes.get("actual_result", "placeholder"),
                "evidence": changes.get("evidence", []),
                "ai_analysis": changes.get("ai_analysis", "placeholder"),
            }
        )
        return self


class DefectDraftResponse(DefectDraftContent):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: int
    project_id: int
    run_id: str
    case_run_id: int | None
    prompt_version_id: int
    output_schema_id: int
    ai_call_id: int
    actual_model: str
    fallback_used: bool
    repair_used: bool
    confidence: float | None
    needs_human_review: bool
    source_snapshot_sha256: str = Field(min_length=64, max_length=64)
    source_snapshot_size: int = Field(ge=0, le=131072)
    revision: int = Field(gt=0)
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    external_submission_supported: Literal[False] = False

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_time(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class DefectDraftListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DefectDraftResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    has_more: bool
