from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.model_center.schemas import AiTaskType


class PromptCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    task_type: AiTaskType
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=100000)
    user_template: str = Field(min_length=1, max_length=100000)
    output_schema_id: int | None = Field(default=None, gt=0)


class PromptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_empty(self) -> "PromptUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class PromptVersionCreate(BaseModel):
    system_prompt: str = Field(min_length=1, max_length=100000)
    user_template: str = Field(min_length=1, max_length=100000)
    output_schema_id: int | None = Field(default=None, gt=0)
    change_note: str | None = Field(default=None, max_length=500)


class PromptVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    prompt_id: int
    version_no: int
    system_prompt: str
    user_template: str
    output_schema_id: int | None
    change_note: str | None
    created_by: str
    created_at: datetime


class PromptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    task_type: AiTaskType
    scope: str = "SYSTEM"
    project_id: int | None = None
    base_prompt_id: int | None = None
    is_builtin: bool = False
    description: str | None
    enabled: bool
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    current_version: PromptVersionResponse | None = None


class PromptListResponse(BaseModel):
    items: list[PromptResponse]
    total: int


class ProjectPromptVersionCreate(BaseModel):
    system_prompt: str = Field(min_length=1, max_length=100_000)
    user_template: str = Field(min_length=1, max_length=100_000)
    output_schema_id: int | None = Field(default=None, gt=0)
    change_note: str = Field(min_length=1, max_length=500)


class SystemPromptTemplateUpdate(BaseModel):
    system_prompt: str = Field(min_length=1, max_length=100_000)
    user_template: str = Field(min_length=1, max_length=100_000)
    output_schema_id: int | None = Field(default=None, gt=0)


class ProjectPromptTemplateResponse(BaseModel):
    base_prompt_id: int
    name: str
    code: str
    task_type: AiTaskType
    description: str | None
    using_system_default: bool
    project_prompt_id: int | None
    project_version_no: int | None
    system_prompt: str
    user_template: str
    output_schema_id: int | None
    change_note: str | None
    updated_at: datetime


class ProjectPromptTemplateListResponse(BaseModel):
    items: list[ProjectPromptTemplateResponse]
    total: int


class PromptCopyRequest(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


class PromptRenderRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class PromptRenderResponse(BaseModel):
    prompt_id: int
    prompt_version_id: int
    system_prompt: str
    user_prompt: str
    required_variables: list[str]
    missing_variables: list[str]


class OutputSchemaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    json_schema: dict[str, Any] = Field(alias="schema_json")


class OutputSchemaVersionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str | None = Field(default=None, max_length=500)
    json_schema: dict[str, Any] = Field(alias="schema_json")


class OutputSchemaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    name: str
    version_no: int
    description: str | None
    json_schema: dict[str, Any] = Field(alias="schema_json")
    enabled: bool
    created_by: str
    created_at: datetime


class OutputSchemaListResponse(BaseModel):
    items: list[OutputSchemaResponse]
    total: int


class StructuredOutputValidateRequest(BaseModel):
    project_id: int = Field(gt=0)
    task_type: AiTaskType
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: str | None = Field(default=None, max_length=64)
    model_config_id: int = Field(gt=0)
    prompt_version_id: int = Field(gt=0)
    output_schema_id: int = Field(gt=0)
    raw_output: str = Field(min_length=1, max_length=5_000_000)
    repair_output: str | None = Field(default=None, max_length=5_000_000)
    input_token: int = Field(default=0, ge=0)
    output_token: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    fallback_used: bool = False
    retry_count: int = Field(default=0, ge=0, le=3)
    response_id: str | None = Field(default=None, max_length=128)


class StructuredOutputValidateResponse(BaseModel):
    ai_call_id: int
    success: bool
    repair_used: bool
    parsed_result: Any = None
    validation_errors: list[str]
    estimated_cost: Decimal


class AiCallLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    task_type: AiTaskType
    entity_type: str | None
    entity_id: str | None
    model_config_id: int
    actual_model: str
    prompt_version_id: int
    output_schema_id: int | None
    input_token: int
    output_token: int
    total_token: int
    estimated_cost: Decimal
    latency_ms: int
    success: bool
    fallback_used: bool
    retry_count: int
    repair_used: bool
    error_type: str | None
    response_id: str | None
    raw_response: str
    repair_response: str | None
    parsed_result: Any = None
    validation_errors: list[str]
    created_at: datetime


class AiCallLogListResponse(BaseModel):
    items: list[AiCallLogResponse]
    total: int
