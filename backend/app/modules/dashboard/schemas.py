from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.reports.schemas import DATABASE_INTEGER_MAX, ReportRate
from app.modules.runs.enums import RunStatus, RunType


class DashboardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int | None = Field(
        default=None, gt=0, le=DATABASE_INTEGER_MAX
    )


class DashboardDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    start_utc: datetime
    end_utc_exclusive: datetime
    run_time_field: Literal["created_at"] = "created_at"


class DashboardCaseInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_cases: int = Field(ge=0)
    web_cases: int = Field(ge=0)
    case_total: int = Field(ge=0)
    scenarios: int = Field(ge=0)
    definition: str


class DashboardTodayRuns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    success: int = Field(ge=0)
    failed: int = Field(ge=0)
    timeout: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    unfinished: int = Field(ge=0)
    success_rate: ReportRate


class DashboardPendingReviews(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_reviews: int = Field(ge=0)
    ai_case_suggestions: int = Field(ge=0)
    web_recording_ai_suggestions: int = Field(ge=0)
    web_healing_proposals: int = Field(ge=0)
    total: int = Field(ge=0)
    definition: str


class DashboardRunnerOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: Literal["ADMIN_ONLY", "VISIBLE"]
    available: bool | None
    status: Literal["KNOWN", "UNKNOWN", "HIDDEN"]
    registered_total: int | None = Field(default=None, ge=0)
    active_total: int | None = Field(default=None, ge=0)
    online: int | None = Field(default=None, ge=0)
    offline: int | None = Field(default=None, ge=0)
    unknown: int | None = Field(default=None, ge=0)


class DashboardRecentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_code: str
    run_type: RunType
    project_id: int
    project_name: str
    status: RunStatus
    created_at: datetime
    ended_at: datetime | None
    report_path: str


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    project_scope_id: int | None
    active_project_count: int = Field(ge=0)
    date_range: DashboardDateRange
    cases: DashboardCaseInventory
    today_runs: DashboardTodayRuns
    pending_reviews: DashboardPendingReviews
    runners: DashboardRunnerOverview
    recent_runs: list[DashboardRecentRun]
    read_only: Literal[True] = True
