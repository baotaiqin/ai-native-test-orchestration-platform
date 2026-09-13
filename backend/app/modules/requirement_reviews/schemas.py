from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class RequirementReviewResult(BaseModel):
    clarity_issues: list[str] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)
    missing_rules: list[str] = Field(default_factory=list)
    exception_gaps: list[str] = Field(default_factory=list)
    testability: list[str] = Field(default_factory=list)
    acceptance_criteria_suggestions: list[str] = Field(default_factory=list)
    overall_summary: str = Field(min_length=1)


class RequirementRevisionContentGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_requirement_code: str = Field(min_length=1, max_length=64)
    suggestion_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    proposed_markdown: str = Field(min_length=1, max_length=200000)


class RequirementRevisionAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=64)
    suggestion_ids: list[str] = Field(min_length=1)
    parent_requirement_code: str = Field(min_length=1, max_length=64)
    insert_after_requirement_code: str = Field(default="", max_length=64)
    title: str = Field(min_length=2, max_length=255)
    requirement_type: str = Field(pattern="^(FEATURE|RULE|ACCEPTANCE_CRITERIA|SECTION)$")
    proposed_markdown: str = Field(min_length=1, max_length=200000)
    reason: str = Field(min_length=1, max_length=2000)


class RequirementRevisionDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_requirement_code: str = Field(min_length=1, max_length=64)
    suggestion_ids: list[str] = Field(min_length=1)
    delete_mode: str = Field(pattern="^(SUBTREE|PROMOTE_CHILDREN)$")
    reason: str = Field(min_length=1, max_length=2000)


class RequirementRevisionUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


class RequirementRevisionPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_revisions: list[RequirementRevisionContentGroup] = Field(default_factory=list)
    additions: list[RequirementRevisionAddition] = Field(default_factory=list)
    deletions: list[RequirementRevisionDeletion] = Field(default_factory=list)
    unresolved: list[RequirementRevisionUnresolved] = Field(default_factory=list)


class RequirementReviewGenerate(BaseModel):
    prompt_id: int = Field(gt=0)
    include_parent: bool = True
    include_siblings: bool = False
    additional_instructions: str | None = Field(default=None, max_length=5000)


class RequirementReviewEdit(BaseModel):
    human_result: RequirementReviewResult
    decision_note: str | None = Field(default=None, max_length=500)


class ReviewDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class RequirementReviewDecision(BaseModel):
    action: ReviewDecision
    decision_note: str | None = Field(default=None, max_length=500)


class RequirementReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requirement_id: int
    requirement_version_id: int
    requirement_code: str
    requirement_title: str
    requirement_version_no: int
    prompt_id: int | None
    ai_call_id: int | None
    generation_status: str
    status: ReviewStatus
    additional_instructions: str | None
    context_snapshot: dict
    raw_response: str | None
    structured_result: RequirementReviewResult | None
    human_result: RequirementReviewResult | None
    error_message: str | None
    decision_note: str | None
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    decided_at: datetime | None
    reused: bool = False


class RequirementReviewListResponse(BaseModel):
    items: list[RequirementReviewResponse]
    total: int
    page: int = 1
    page_size: int = 10
    active_count: int = 0
