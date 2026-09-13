from datetime import datetime
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
from app.modules.schedules.calendar import (
    ScheduleExpressionError,
    next_occurrence,
    parse_cron,
    validate_timezone,
)


class ScheduleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    schedule_type: Literal["DAILY", "WEEKLY", "CRON"]
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    daily_time: str | None = Field(default=None, max_length=5)
    weekdays: list[int] | None = Field(default=None, max_length=7)
    cron_expression: str | None = Field(default=None, max_length=100)
    enabled: bool = True

    @field_validator("name", "timezone")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("cron_expression")
    @classmethod
    def normalize_cron(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else None

    @model_validator(mode="after")
    def validate_expression(self) -> "ScheduleWrite":
        try:
            self.timezone = validate_timezone(self.timezone)
            if self.schedule_type == "DAILY":
                if (
                    not self.daily_time
                    or self.weekdays is not None
                    or self.cron_expression is not None
                ):
                    raise ScheduleExpressionError("Daily 只允许配置执行时间")
            elif self.schedule_type == "WEEKLY":
                if not self.daily_time or not self.weekdays or self.cron_expression is not None:
                    raise ScheduleExpressionError("Weekly 必须配置执行时间和星期")
                self.weekdays = sorted(set(self.weekdays))
            elif (
                not self.cron_expression or self.daily_time is not None or self.weekdays is not None
            ):
                raise ScheduleExpressionError("Cron 只允许配置 5 段 Cron 表达式")
            if self.cron_expression:
                parse_cron(self.cron_expression)
            next_occurrence(
                schedule_type=self.schedule_type,
                timezone=self.timezone,
                after=datetime(2026, 1, 1),
                daily_time=self.daily_time,
                weekdays=self.weekdays,
                cron_expression=self.cron_expression,
            )
        except ScheduleExpressionError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ScheduleCreate(ScheduleWrite):
    pass


class ScheduleUpdate(ScheduleWrite):
    pass


class SchedulePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_type: Literal["DAILY", "WEEKLY", "CRON"]
    timezone: str = "Asia/Shanghai"
    daily_time: str | None = None
    weekdays: list[int] | None = None
    cron_expression: str | None = None
    count: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def validate_expression(self) -> "SchedulePreviewRequest":
        ScheduleCreate(
            project_id=1,
            plan_id=1,
            name="Schedule preview",
            schedule_type=self.schedule_type,
            timezone=self.timezone,
            daily_time=self.daily_time,
            weekdays=self.weekdays,
            cron_expression=self.cron_expression,
        )
        return self


class SchedulePreviewResponse(BaseModel):
    occurrences: list[datetime]

    @field_serializer("occurrences", when_used="json")
    def serialize_occurrences(self, values: list[datetime]) -> list[str]:
        return [to_utc_isoformat(value) or "" for value in values]


class ScheduleResponse(BaseModel):
    id: int
    project_id: int
    plan_id: int
    plan_name: str
    name: str
    schedule_type: Literal["DAILY", "WEEKLY", "CRON"]
    timezone: str
    daily_time: str | None
    weekdays: list[int] | None
    cron_expression: str | None
    enabled: bool
    status: Literal["ACTIVE", "ARCHIVED"]
    next_run_at: datetime | None
    last_scheduled_at: datetime | None
    last_trigger_status: Literal["CLAIMED", "DISPATCHED", "FAILED", "SKIPPED"] | None
    last_error_code: str | None
    last_error_message: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "next_run_at", "last_scheduled_at", "created_at", "updated_at", when_used="json"
    )
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int


class ScheduleTriggerResponse(BaseModel):
    id: str
    schedule_id: int
    schedule_name: str
    project_id: int
    scheduled_for_at: datetime
    status: Literal["CLAIMED", "DISPATCHED", "FAILED", "SKIPPED"]
    plan_run_id: str | None
    plan_run_status: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    @field_serializer("scheduled_for_at", "created_at", "completed_at", when_used="json")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class ScheduleTriggerListResponse(BaseModel):
    items: list[ScheduleTriggerResponse]
    total: int
