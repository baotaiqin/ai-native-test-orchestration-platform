from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.runs.enums import RunStatus, RunType
from app.modules.runs.schemas import RunValidationIssue, validate_runtime_variables


class TestPlanItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: RunType
    case_id: int | None = Field(default=None, gt=0)
    case_version_id: int | None = Field(default=None, gt=0)
    scenario_id: int | None = Field(default=None, gt=0)
    scenario_version_id: int | None = Field(default=None, gt=0)
    web_case_id: int | None = Field(default=None, gt=0)
    web_case_version_id: int | None = Field(default=None, gt=0)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_target(self) -> "TestPlanItemInput":
        if self.target_type == RunType.API_CASE:
            valid = self.case_id is not None and all(
                value is None
                for value in (
                    self.scenario_id,
                    self.scenario_version_id,
                    self.web_case_id,
                    self.web_case_version_id,
                )
            )
        elif self.target_type == RunType.SCENARIO:
            valid = self.scenario_id is not None and all(
                value is None
                for value in (
                    self.case_id,
                    self.case_version_id,
                    self.web_case_id,
                    self.web_case_version_id,
                )
            )
        else:
            valid = self.web_case_id is not None and all(
                value is None
                for value in (
                    self.case_id,
                    self.case_version_id,
                    self.scenario_id,
                    self.scenario_version_id,
                )
            )
        if not valid:
            raise ValueError("Test Plan Item 必须且只能指定一种执行目标")
        return self


class TestPlanWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    environment_id: int = Field(gt=0)
    runner_id: str = Field(min_length=1, max_length=64)
    runtime_variables: dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["PARALLEL"] = "PARALLEL"
    failure_strategy: Literal["CONTINUE"] = "CONTINUE"
    items: list[TestPlanItemInput] = Field(min_length=1, max_length=100)

    @field_validator("name", "runner_id")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @field_validator("runtime_variables")
    @classmethod
    def validate_variables(cls, values: dict[str, Any]) -> dict[str, Any]:
        return validate_runtime_variables(values)

    @model_validator(mode="after")
    def require_enabled_item(self) -> "TestPlanWrite":
        if not any(item.enabled for item in self.items):
            raise ValueError("Test Plan 至少需要一个启用的执行项")
        return self


class TestPlanCreate(TestPlanWrite):
    pass


class TestPlanUpdate(TestPlanWrite):
    pass


class TestPlanItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence_no: int
    target_type: RunType
    target_name: str
    case_id: int | None
    case_version_id: int | None
    scenario_id: int | None
    scenario_version_id: int | None
    web_case_id: int | None
    web_case_version_id: int | None
    enabled: bool


class TestPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    environment_id: int
    runner_id: str
    runtime_variables: dict[str, Any]
    execution_mode: Literal["PARALLEL"]
    failure_strategy: Literal["CONTINUE"]
    status: Literal["ACTIVE", "ARCHIVED"]
    items: list[TestPlanItemResponse]
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_datetime(self, value: datetime) -> str | None:
        return to_utc_isoformat(value)


class TestPlanListResponse(BaseModel):
    items: list[TestPlanResponse]
    total: int


class TestPlanItemValidation(BaseModel):
    sequence_no: int = Field(gt=0)
    target_type: RunType
    target_name: str
    valid: bool
    issues: list[RunValidationIssue]


class TestPlanValidationResponse(BaseModel):
    valid: bool
    item_count: int = Field(ge=1)
    items: list[TestPlanItemValidation]


class TestPlanRunnerSlot(BaseModel):
    type: Literal["API", "WEB"]
    available: int = Field(ge=0)


class TestPlanRunnerOption(BaseModel):
    id: str
    name: str
    ready_capabilities: list[str]
    slots: list[TestPlanRunnerSlot]


class TestPlanRunnerOptionListResponse(BaseModel):
    items: list[TestPlanRunnerOption]
    total: int


class TestPlanRunItemResponse(BaseModel):
    id: int
    sequence_no: int
    target_type: RunType
    target_name: str
    run_id: str | None
    run_code: str | None
    status: RunStatus
    error_code: str | None
    error_message: str | None


class TestPlanRunResponse(BaseModel):
    id: str
    plan_id: int
    project_id: int
    plan_name: str
    status: RunStatus
    trigger_type: Literal["MANUAL", "SCHEDULE", "API"]
    schedule_id: int | None
    total: int
    passed: int
    failed: int
    running: int
    pending: int
    items: list[TestPlanRunItemResponse]
    created_by: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    updated_at: datetime

    @field_serializer(
        "created_at", "started_at", "ended_at", "updated_at", when_used="json"
    )
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class TestPlanRunListResponse(BaseModel):
    items: list[TestPlanRunResponse]
    total: int


class TestPlanRunStartResponse(BaseModel):
    plan_run: TestPlanRunResponse
    validation: TestPlanValidationResponse
