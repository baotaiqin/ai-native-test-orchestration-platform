from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.scenarios.schemas import ScenarioDsl


class ApiDefinitionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class DiffStatus(StrEnum):
    ADDED = "ADDED"
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"
    REMOVED = "REMOVED"


class ApiDesignSuggestionKind(StrEnum):
    CASE_SET = "CASE_SET"
    SCENARIO = "SCENARIO"


class OpenApiImportRequest(BaseModel):
    project_id: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=5_000_000)
    mark_missing_removed: bool = True


class ParsedOperation(BaseModel):
    name: str
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] = Field(default_factory=dict)
    auth_info: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    contract_hash: str
    diff_status: DiffStatus = DiffStatus.ADDED
    existing_definition_id: int | None = None


class OpenApiPreviewResponse(BaseModel):
    filename: str
    title: str | None
    spec_version: str
    operations: list[ParsedOperation]
    removed: list[ParsedOperation]
    added_count: int
    changed_count: int
    unchanged_count: int
    removed_count: int


class ApiDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    import_id: int | None
    name: str
    method: str
    path: str
    operation_id: str | None
    summary: str | None
    description: str | None
    parameters: list[dict[str, Any]]
    request_schema: dict[str, Any] | None
    response_schema: dict[str, Any]
    auth_info: dict[str, Any]
    tags: list[str]
    source: str
    source_filename: str | None
    source_version: int | None
    contract_hash: str
    status: ApiDefinitionStatus
    created_by: str
    created_at: datetime
    updated_at: datetime


class ApiDefinitionListResponse(BaseModel):
    items: list[ApiDefinitionResponse]
    total: int


class OpenApiImportResponse(BaseModel):
    import_id: int
    version_no: int
    created_count: int
    updated_count: int
    unchanged_count: int
    removed_count: int


class ApiScenarioRecommendation(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    rationale: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.8, ge=0, le=1)
    dsl: ScenarioDsl


class ApiScenarioPlanCandidate(BaseModel):
    candidate_key: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=255)
    objective: str = Field(min_length=1, max_length=2000)
    category: Literal["POSITIVE", "NEGATIVE", "BOUNDARY", "RECOVERY"]
    priority: Literal["P0", "P1", "P2", "P3"]
    rationale: str = Field(min_length=1, max_length=2000)
    requirement_ids: list[int] = Field(min_length=1, max_length=200)
    api_definition_ids: list[int] = Field(min_length=1, max_length=200)
    api_flow: list[str] = Field(min_length=1, max_length=50)
    preconditions: list[str] = Field(default_factory=list, max_length=30)
    expected_outcomes: list[str] = Field(min_length=1, max_length=30)
    cleanup_required: bool = False

    @field_validator("requirement_ids", "api_definition_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("引用编号必须为不重复的正整数")
        return value


class ApiScenarioPlanResult(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    candidates: list[ApiScenarioPlanCandidate] = Field(min_length=1, max_length=20)
    uncovered_requirement_ids: list[int] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_candidates(self) -> "ApiScenarioPlanResult":
        keys = [item.candidate_key for item in self.candidates]
        names = [item.name.strip().casefold() for item in self.candidates]
        if len(set(keys)) != len(keys) or len(set(names)) != len(names):
            raise ValueError("候选场景的标识和名称必须唯一")
        return self


class ApiDesignSuggestionCreate(BaseModel):
    kind: ApiDesignSuggestionKind
    prompt_id: int = Field(gt=0)
    requirement_document_version_id: int | None = Field(default=None, gt=0)
    requirement_id: int | None = Field(default=None, gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000)


class ApiDesignSuggestionEdit(BaseModel):
    human_result: dict[str, Any]
    decision_note: str | None = Field(default=None, max_length=500)


class ApiDesignSuggestionDecision(BaseModel):
    action: Literal["ACCEPT", "REJECT"]
    decision_note: str | None = Field(default=None, max_length=500)


class ApiDesignScenarioReference(BaseModel):
    id: int
    code: str
    name: str
    status: str
    version_no: int | None


class ApiDesignRequirementReference(BaseModel):
    id: int | None
    code: str
    title: str
    document_version_id: int
    document_version_no: int
    scope_count: int
    scope_mode: Literal["DOCUMENT", "SUBTREE"] = "SUBTREE"


class ApiDesignSuggestionResponse(BaseModel):
    id: int
    project_id: int
    import_id: int
    source_filename: str
    source_version: int
    prompt_id: int | None
    plan_item_id: int | None
    ai_call_id: int | None
    kind: ApiDesignSuggestionKind
    status: Literal["DRAFT", "ACCEPTED", "REJECTED"]
    generation_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    source_snapshot_sha256: str
    source_snapshot_size: int
    additional_instructions: str | None
    structured_result: dict[str, Any] | None
    human_result: dict[str, Any] | None
    decision_note: str | None
    created_test_case_ids: list[int]
    created_scenario_id: int | None
    created_scenario: ApiDesignScenarioReference | None
    requirement_source: ApiDesignRequirementReference | None
    actual_model: str | None
    prompt_version_id: int | None
    output_schema_id: int | None
    fallback_used: bool
    repair_used: bool
    raw_response: str
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    reviewed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    idempotent: bool = False
    reused: bool = False


class ApiDesignSuggestionListResponse(BaseModel):
    items: list[ApiDesignSuggestionResponse]
    total: int


class ApiScenarioPlanCreate(BaseModel):
    prompt_id: int = Field(gt=0)
    requirement_document_version_id: int = Field(gt=0)
    requirement_id: int | None = Field(default=None, gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000)


class ApiScenarioPlanItemResponse(BaseModel):
    id: int
    candidate_key: str
    order_index: int
    name: str
    objective: str
    category: Literal["POSITIVE", "NEGATIVE", "BOUNDARY", "RECOVERY"]
    priority: Literal["P0", "P1", "P2", "P3"]
    rationale: str
    requirement_ids: list[int]
    api_definition_ids: list[int]
    api_flow: list[str]
    preconditions: list[str]
    expected_outcomes: list[str]
    cleanup_required: bool
    latest_suggestion: ApiDesignSuggestionResponse | None = None


class ApiScenarioPlanResponse(BaseModel):
    id: int
    project_id: int
    import_id: int
    source_filename: str
    source_version: int
    prompt_id: int
    ai_call_id: int | None
    generation_status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    requirement_source: ApiDesignRequirementReference
    summary: str | None
    items: list[ApiScenarioPlanItemResponse]
    actual_model: str | None
    fallback_used: bool
    repair_used: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    reused: bool = False


class ApiScenarioPlanListResponse(BaseModel):
    items: list[ApiScenarioPlanResponse]
    total: int


class ApiScenarioPlanGenerate(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=20)
    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000)

    @field_validator("item_ids")
    @classmethod
    def unique_item_ids(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("候选场景不能重复选择")
        return value


class ApiScenarioPlanGenerateResponse(BaseModel):
    suggestions: list[ApiDesignSuggestionResponse]
