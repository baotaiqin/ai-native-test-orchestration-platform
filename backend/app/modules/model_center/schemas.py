from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ModelType(StrEnum):
    TEXT = "TEXT"
    VISION = "VISION"
    EMBEDDING = "EMBEDDING"


class ModelCategory(StrEnum):
    LLM = "LLM"
    VISION = "VISION"
    OMNI = "OMNI"
    AUDIO = "AUDIO"
    EMBEDDING = "EMBEDDING"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    THREE_D = "THREE_D"
    OTHER = "OTHER"


class ModelCatalogCapability(StrEnum):
    TG = "TG"
    REASONING = "Reasoning"
    VU = "VU"
    IG = "IG"
    VG = "VG"
    ASR = "ASR"
    TTS = "TTS"
    TR = "TR"
    ME = "ME"
    REALTIME_OMNI = "Realtime-Omni"
    MULTIMODAL_OMNI = "Multimodal-Omni"
    REALTIME_TEXT_TO_SPEECH = "Realtime-Text-to-Speech"
    REALTIME_ASR = "Realtime-ASR"
    REALTIME_AUDIO_TRANSLATE = "Realtime-Audio-Translate"
    THREE_D_GENERATION = "3D-generation"
    REALTIME_CHATTING = "Realtime-Chatting"


class AiTaskType(StrEnum):
    REQUIREMENT_REVIEW = "REQUIREMENT_REVIEW"
    API_DOC_REVIEW = "API_DOC_REVIEW"
    API_TEST_DESIGN = "API_TEST_DESIGN"
    API_CASE_GENERATE = "API_CASE_GENERATE"
    API_SCENARIO_PLAN = "API_SCENARIO_PLAN"
    API_SCENARIO_GENERATE = "API_SCENARIO_GENERATE"
    WEB_CASE_GENERATE = "WEB_CASE_GENERATE"
    WEB_TEST_PLAN = "WEB_TEST_PLAN"
    WEB_EXPLORATION_DECISION = "WEB_EXPLORATION_DECISION"
    WEB_PLAN_RECONCILE = "WEB_PLAN_RECONCILE"
    AI_ASSERTION = "AI_ASSERTION"
    LOCATOR_HEALING = "LOCATOR_HEALING"
    WEB_FAILURE_ANALYSIS = "WEB_FAILURE_ANALYSIS"
    PERFORMANCE_ANALYSIS = "PERFORMANCE_ANALYSIS"
    DEFECT_DRAFT = "DEFECT_DRAFT"
    IMPACT_ANALYSIS = "IMPACT_ANALYSIS"


class ModelConnectionAccessType(StrEnum):
    DIRECT = "DIRECT"
    AGGREGATOR = "AGGREGATOR"
    SELF_HOSTED = "SELF_HOSTED"


class ModelConnectionProtocolType(StrEnum):
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class ModelProviderConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=128)
    access_type: ModelConnectionAccessType = ModelConnectionAccessType.DIRECT
    provider: str = Field(min_length=2, max_length=64)
    protocol_type: ModelConnectionProtocolType = (
        ModelConnectionProtocolType.OPENAI_COMPATIBLE
    )
    base_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    enabled: bool = True


class ModelProviderConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=128)
    access_type: ModelConnectionAccessType | None = None
    provider: str | None = Field(default=None, min_length=2, max_length=64)
    protocol_type: ModelConnectionProtocolType | None = None
    base_url: HttpUrl | None = None
    api_key: str | None = Field(
        default=None, min_length=1, max_length=4096, repr=False
    )
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_empty_update(self) -> "ModelProviderConnectionUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class ModelProviderConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    access_type: ModelConnectionAccessType
    provider: str
    protocol_type: ModelConnectionProtocolType
    base_url: str
    has_api_key: bool
    api_key_masked: str | None
    api_key_rotated_at: datetime | None
    enabled: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class ModelProviderConnectionListResponse(BaseModel):
    items: list[ModelProviderConnectionResponse]
    total: int


class ModelConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=128)
    connection_id: int = Field(gt=0)
    model_vendor: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    model_type: ModelType = ModelType.TEXT
    supports_tool_call: bool = False
    supports_structured_output: bool = True
    max_context: int | None = Field(default=128000, ge=1024, le=10_000_000)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    input_price: Decimal = Field(default=Decimal("0"), ge=0)
    output_price: Decimal = Field(default=Decimal("0"), ge=0)
    enabled: bool = True


class ModelConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=128)
    connection_id: int | None = Field(default=None, gt=0)
    model_vendor: str | None = Field(default=None, min_length=1, max_length=64)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    model_type: ModelType | None = None
    supports_tool_call: bool | None = None
    supports_structured_output: bool | None = None
    max_context: int | None = Field(default=None, ge=1024, le=10_000_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    input_price: Decimal | None = Field(default=None, ge=0)
    output_price: Decimal | None = Field(default=None, ge=0)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_empty_update(self) -> "ModelConfigurationUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的字段")
        return self


class ModelConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    connection_id: int
    model_vendor: str
    model_name: str
    model_type: ModelType
    model_category: ModelCategory | None
    metadata_source: str | None
    metadata_synced_at: datetime | None
    description: str | None
    supports_reasoning: bool
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_reasoning_tokens: int | None
    reasoning_max_input_tokens: int | None
    reasoning_max_output_tokens: int | None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    pricing_tiers: list[dict] = Field(default_factory=list)
    published_at: datetime | None
    supports_tool_call: bool
    supports_structured_output: bool
    max_context: int | None
    timeout_seconds: int
    input_price: Decimal
    output_price: Decimal
    enabled: bool
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "input_modalities",
        "output_modalities",
        "capabilities",
        "features",
        "pricing_tiers",
        mode="before",
    )
    @classmethod
    def empty_json_lists_are_empty(cls, value: object) -> object:
        return value or []


class ModelConfigurationListResponse(BaseModel):
    items: list[ModelConfigurationResponse]
    total: int


class ModelCatalogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, min_length=1, max_length=128)
    model_type: ModelType | None = None
    reasoning: bool | None = None
    tool_call: bool | None = None
    structured_output: bool | None = None
    capabilities: list[ModelCatalogCapability] = Field(default_factory=list, max_length=16)
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=50)
    exact_model: str | None = Field(default=None, min_length=1, max_length=128, repr=False)

    @field_validator("capabilities", mode="before")
    @classmethod
    def deduplicate_capabilities(
        cls, values: object
    ) -> object:
        if isinstance(values, (list, tuple)):
            return list(dict.fromkeys(values))
        return values


class ModelCatalogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_id: str
    name: str
    description: str | None
    provider: str
    inference_provider: str | None
    model_category: ModelCategory
    model_type: ModelType
    capabilities: list[str]
    features: list[str]
    input_modalities: list[str]
    output_modalities: list[str]
    supports_reasoning: bool
    supports_tool_call: bool
    supports_structured_output: bool
    context_window: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_reasoning_tokens: int | None
    reasoning_max_input_tokens: int | None
    reasoning_max_output_tokens: int | None
    pricing_tiers: list[dict]
    published_time: str | None
    supported_by_platform: bool
    unsupported_reason: str | None


class ModelCatalogListResponse(BaseModel):
    items: list[ModelCatalogItemResponse]
    total: int
    page: int
    page_size: int
    connection_id: int
    source: str
    fetched_at: datetime


class ModelCatalogImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=128)
    configuration_name: str | None = Field(default=None, min_length=2, max_length=128)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    enabled: bool = True


class ModelConnectionVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: int
    success: bool
    status: Literal["SUCCESS", "FAILED"]
    duration_ms: int = Field(ge=0, le=60_000)
    summary: str = Field(min_length=1, max_length=128)
    error_type: str | None = Field(default=None, max_length=64)


class ProjectModelBindingUpsert(BaseModel):
    project_id: int = Field(gt=0)
    task_type: AiTaskType
    primary_model_id: int = Field(gt=0)
    fallback_model_id: int | None = Field(default=None, gt=0)
    max_fallback: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_fallback(self) -> "ProjectModelBindingUpsert":
        if self.fallback_model_id == self.primary_model_id:
            raise ValueError("主模型与 fallback 模型不能相同")
        if self.fallback_model_id is None and self.max_fallback != 0:
            self.max_fallback = 0
        return self


class ProjectModelBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    task_type: AiTaskType
    primary_model_id: int
    fallback_model_id: int | None
    max_fallback: int
    updated_by: str
    created_at: datetime
    updated_at: datetime


class ProjectModelBindingListResponse(BaseModel):
    items: list[ProjectModelBindingResponse]
    total: int


class ProjectModelBindingBulkApplyRequest(BaseModel):
    project_id: int = Field(gt=0)
    model_id: int = Field(gt=0)
    role: Literal["PRIMARY", "FALLBACK"]


class ProjectModelBindingBulkApplyItem(BaseModel):
    task_type: AiTaskType
    status: Literal["APPLIED", "UNCHANGED", "SKIPPED"]
    message: str = Field(min_length=1, max_length=255)


class ProjectModelBindingBulkApplyResponse(BaseModel):
    project_id: int
    model_id: int
    role: Literal["PRIMARY", "FALLBACK"]
    applied_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    items: list[ProjectModelBindingBulkApplyItem]
