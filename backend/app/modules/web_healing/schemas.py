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

_SENSITIVE_LOCATOR_TEXT = re.compile(
    r"(?i)(authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)"
)
_SAFE_LOCATOR_STRATEGIES = {"css", "test_id", "placeholder", "label", "role"}
MAX_HEALING_CANDIDATE_LOCATORS = 360


def _safe_locator_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value or "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} 不能为空且不能换行")
    if _SENSITIVE_LOCATOR_TEXT.search(value):
        raise ValueError(f"{field_name} 不能包含凭据")
    return value


class WebHealingProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class HealingAnalysisStage(StrEnum):
    LLM_DOM = "LLM_DOM"
    VISION_SCREENSHOT = "VISION_SCREENSHOT"
    AGENT_LOCATE = "AGENT_LOCATE"


class HealingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["css", "test_id", "placeholder", "label", "role"]
    value: str = Field(min_length=1, max_length=2000)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _safe_locator_text(value, "locator.value")


class HealingSourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["css", "xpath", "text", "role", "label", "placeholder", "test_id"]
    value: str = Field(min_length=1, max_length=2000)
    priority: int | None = Field(
        default=None, ge=1, le=20, exclude_if=lambda value: value is None
    )
    source: Literal["MANUAL", "IMPORTED", "HEALED"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _safe_locator_text(value, "source_locator.value")


class HealingLocatorReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_version_id: int = Field(gt=0)
    locators: list[HealingSourceLocator] = Field(default_factory=list, max_length=20)


class HealingCandidateLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_index: int = Field(ge=0, lt=40)
    locator: HealingLocator
    similarity_score: float = Field(default=0, ge=0, le=1)


class WebHealingProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_run_id: int = Field(gt=0)
    node_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^(action|assertion)_[1-9][0-9]*$",
    )
    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000, repr=False)
    analysis_stage: HealingAnalysisStage = HealingAnalysisStage.LLM_DOM
    screenshot_artifact_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("additional_instructions")
    @classmethod
    def validate_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_locator_text(value, "additional_instructions")

    @model_validator(mode="after")
    def validate_analysis_evidence(self) -> "WebHealingProposalCreateRequest":
        if self.analysis_stage == HealingAnalysisStage.LLM_DOM:
            if self.screenshot_artifact_id is not None:
                raise ValueError("LLM_DOM 阶段不能携带截图")
        elif self.screenshot_artifact_id is None:
            raise ValueError("Vision/Agent Locate 阶段必须选择失败 Run 的截图 Evidence")
        return self


class WebHealingProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: HealingLocator | None = None
    decision_note: str | None = Field(default=None, max_length=500, repr=False)

    @field_validator("decision_note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_locator_text(value, "decision_note")


class WebHealingProposalValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: HealingLocator | None = None


class WebHealingValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: str
    run_code: str
    run_status: str
    locator: HealingLocator
    created_by: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebHealingProposalRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_note: str | None = Field(default=None, max_length=500, repr=False)

    @field_validator("decision_note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_locator_text(value, "decision_note")


class HealingStructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: HealingLocator
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)
    evidence_candidate_index: int = Field(ge=0, lt=40)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _safe_locator_text(value, "reason")


class WebHealingProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: int
    project_id: int
    run_id: str
    case_run_id: int
    web_case_id: int
    web_case_version_id: int
    node_id: str
    status: WebHealingProposalStatus
    ai_call_id: int | None
    analysis_stage: HealingAnalysisStage
    screenshot_artifact_id: str | None
    actual_model: str | None
    prompt_version_id: int | None
    output_schema_id: int | None
    fallback_used: bool | None
    repair_used: bool | None
    source_snapshot_sha256: str
    source_snapshot_size: int = Field(ge=0, le=1_000_000)
    old_locator: HealingLocator | HealingLocatorReference | HealingSourceLocator
    proposed_locator: HealingLocator | None
    human_locator: HealingLocator | None
    candidate_locators: list[HealingCandidateLocator] = Field(
        min_length=1, max_length=MAX_HEALING_CANDIDATE_LOCATORS
    )
    confidence: float = Field(ge=0, le=1)
    reason: str
    evidence_candidate_index: int = Field(ge=0, lt=40)
    decision_note: str | None
    created_element_version_id: int | None
    created_web_case_version_id: int | None
    latest_validation: WebHealingValidationResponse | None
    validated_locators: list[HealingLocator] = Field(
        default_factory=list, max_length=MAX_HEALING_CANDIDATE_LOCATORS
    )
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    reviewed_at: datetime | None
    idempotent: bool = False

    @field_serializer("created_at", "reviewed_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebHealingProposalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WebHealingProposalResponse]
    total: int
