from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.modules.model_center.schemas import AiTaskType


class AiGenerateRequest(BaseModel):
    project_id: int = Field(gt=0)
    task_type: AiTaskType
    prompt_id: int = Field(gt=0)
    variables: dict[str, Any] = Field(default_factory=dict)
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: str | None = Field(default=None, max_length=64)


class AiGenerateResponse(BaseModel):
    ai_call_id: int
    success: bool
    content: str
    parsed_result: Any = None
    actual_model: str
    fallback_used: bool
    repair_used: bool
    input_token: int
    output_token: int
    total_token: int
    estimated_cost: Decimal
    latency_ms: int
    response_id: str | None
