from pydantic import BaseModel, ConfigDict, Field


class DemoBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: int = Field(gt=0)


class DemoProbeResponse(BaseModel):
    success: bool
    status: str
    duration_ms: int
    summary: str
    error_type: str | None = None


class DemoAssetStatus(BaseModel):
    project: bool
    environment: bool
    model: bool
    prompts: int
    prompt_total: int
    bindings: int
    binding_total: int
    requirement: bool
    requirement_count: int = 0
    openapi: bool
    api_definition_count: int = 0
    runtime_secret: bool = False


class DemoBootstrapStatus(BaseModel):
    available: bool = True
    ready: bool
    project_id: int | None = None
    requirement_id: int | None = None
    model_id: int | None = None
    model_name: str | None = None
    provider_base_url: str | None = None
    demo_url: str
    has_api_key: bool = False
    assets: DemoAssetStatus
    prompt_ids: dict[str, int] = Field(default_factory=dict)
    next_path: str = "/demo"
    message: str


class DemoBootstrapResponse(DemoBootstrapStatus):
    probe: DemoProbeResponse


class DemoResetCounts(BaseModel):
    ai_records: int = 0
    test_cases: int = 0
    scenarios: int = 0
    runs: int = 0
    evidence_files: int = 0
    web_assets: int = 0
    defects: int = 0
    data_sources: int = 0
    secrets: int = 0


class DemoResetPreview(BaseModel):
    available: bool = True
    project_id: int | None = None
    can_reset: bool
    active_operations: int = 0
    blockers: list[str] = Field(default_factory=list)
    delete_counts: DemoResetCounts = Field(default_factory=DemoResetCounts)
    preserved: list[str] = Field(default_factory=list)


class DemoResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=32)


class DemoResetResponse(BaseModel):
    reset: bool = True
    deleted: DemoResetCounts
    status: DemoBootstrapStatus
    message: str
