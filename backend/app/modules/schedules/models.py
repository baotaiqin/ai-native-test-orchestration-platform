from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class TestSchedule(Base):
    __tablename__ = "test_schedules"
    __table_args__ = (
        CheckConstraint("schedule_type IN ('DAILY','WEEKLY','CRON')", name="test_schedules_type"),
        CheckConstraint("enabled IN (0, 1)", name="test_schedules_enabled"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="test_schedules_status"),
        Index("ix_test_schedules_due", "status", "enabled", "next_run_at"),
        Index("ix_test_schedules_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    weekdays: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_trigger_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    triggers: Mapped[list["ScheduleTrigger"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduleTrigger(Base):
    __tablename__ = "schedule_triggers"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for_at", name="uq_schedule_triggers_occurrence"),
        CheckConstraint(
            "status IN ('CLAIMED','DISPATCHED','FAILED','SKIPPED')",
            name="schedule_triggers_status",
        ),
        Index("ix_schedule_triggers_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("test_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_for_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CLAIMED")
    plan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("test_plan_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    schedule: Mapped[TestSchedule] = relationship(back_populates="triggers")
