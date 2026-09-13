import re
from datetime import datetime
from enum import StrEnum
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
from app.modules.resource_registry.schemas import validate_secret_free_payload
from app.modules.web_cases.schemas import WebAssertion, WebCaseContent
from app.modules.web_recordings.schemas import WebRecordingLocator

_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|token|access[_-]?token|secret|"
    r"credential|authorization|cookie|api[_-]?key)\s*[:=]\s*[^\s,;]+)"
)


class WebRecordingAiSuggestionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class WebRecordingAiSuggestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000, repr=False)

    @field_validator("additional_instructions")
    @classmethod
    def validate_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("additional_instructions 不能为空且不能换行")
        if _SENSITIVE_TEXT.search(normalized):
            raise ValueError("additional_instructions 不能包含凭据")
        try:
            validate_secret_free_payload(normalized)
        except ValueError as exc:
            raise ValueError("additional_instructions 不能包含凭据") from exc
        return normalized


class WebRecordingAiSuggestionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_sequence: int = Field(gt=0)
    natural_language_step: str = Field(min_length=1, max_length=2000)
    action: Literal["GOTO", "CLICK", "FILL", "SELECT", "PRESS"]
    locator: WebRecordingLocator | None = None
    element_name: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("natural_language_step", "element_name", "reason")
    @classmethod
    def validate_step_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("AI 建议步骤文本无效")
        if _SENSITIVE_TEXT.search(normalized):
            raise ValueError("AI 建议步骤文本不安全")
        return normalized


class WebRecordingAiSuggestionAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_sequence: int = Field(gt=0)
    assertion: WebAssertion
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("AI 建议断言说明无效")
        if _SENSITIVE_TEXT.search(normalized):
            raise ValueError("AI 建议断言说明不安全")
        return normalized


class WebRecordingAiSuggestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_name: str = Field(min_length=2, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    steps: list[WebRecordingAiSuggestionStep] = Field(min_length=1, max_length=200)
    assertions: list[WebRecordingAiSuggestionAssertion] = Field(
        default_factory=list, max_length=100
    )
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("suggested_name", "summary")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if "\r" in normalized or "\n" in normalized or _SENSITIVE_TEXT.search(normalized):
            raise ValueError("AI 建议文本不安全")
        return normalized

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or "\r" in normalized or "\n" in normalized:
                raise ValueError("AI 建议 warning 无效")
            if _SENSITIVE_TEXT.search(normalized):
                raise ValueError("AI 建议 warning 不安全")
            result.append(normalized)
        return result

    @model_validator(mode="after")
    def validate_secret_free_result(self) -> "WebRecordingAiSuggestionResult":
        try:
            validate_secret_free_payload(self.model_dump(mode="python"))
        except ValueError as exc:
            raise ValueError("AI 建议结果不能包含凭据") from exc
        return self


class WebRecordingAiSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: int
    recording_id: str
    project_id: int
    status: WebRecordingAiSuggestionStatus
    ai_call_id: int | None
    actual_model: str | None
    prompt_version_id: int | None
    output_schema_id: int | None
    fallback_used: bool | None
    repair_used: bool | None
    source_snapshot_sha256: str
    source_snapshot_size: int = Field(ge=0, le=1_000_000)
    additional_instructions: str | None = Field(default=None, repr=False)
    structured_result: WebRecordingAiSuggestionResult | None = None
    canonical_suggested_content: WebCaseContent | None = None
    human_content: WebCaseContent | None = None
    decision_note: str | None
    confirmed_web_case_id: int | None
    confirmed_web_case_version_id: int | None
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    reviewed_at: datetime | None
    idempotent: bool = False

    @field_serializer("created_at", "reviewed_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebRecordingAiSuggestionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WebRecordingAiSuggestionResponse]
    total: int


class WebRecordingAiSuggestionRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_note: str | None = Field(default=None, max_length=500, repr=False)

    @field_validator("decision_note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("decision_note 不能为空且不能换行")
        if _SENSITIVE_TEXT.search(normalized):
            raise ValueError("decision_note 不能包含凭据")
        return normalized
