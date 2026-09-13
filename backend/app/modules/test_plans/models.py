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


class TestPlan(Base):
    __tablename__ = "test_plans"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="test_plans_status"),
        CheckConstraint("execution_mode = 'PARALLEL'", name="test_plans_execution_mode"),
        CheckConstraint("failure_strategy = 'CONTINUE'", name="test_plans_failure_strategy"),
        Index("ix_test_plans_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    runtime_variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PARALLEL")
    failure_strategy: Mapped[str] = mapped_column(String(16), nullable=False, default="CONTINUE")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    items: Mapped[list["TestPlanItem"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="TestPlanItem.sequence_no",
    )


class TestPlanItem(Base):
    __tablename__ = "test_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence_no", name="uq_test_plan_items_plan_sequence"),
        CheckConstraint("sequence_no > 0", name="test_plan_items_sequence_positive"),
        CheckConstraint(
            "target_type IN ('API_CASE','SCENARIO','WEB_CASE')",
            name="test_plan_items_target_type",
        ),
        CheckConstraint("enabled IN (0, 1)", name="test_plan_items_enabled"),
        CheckConstraint(
            "(target_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
            "AND scenario_id IS NULL AND scenario_version_id IS NULL AND web_case_id IS NULL "
            "AND web_case_version_id IS NULL) OR "
            "(target_type = 'SCENARIO' AND scenario_id IS NOT NULL "
            "AND scenario_version_id IS NOT NULL "
            "AND case_id IS NULL AND case_version_id IS NULL AND web_case_id IS NULL "
            "AND web_case_version_id IS NULL) OR "
            "(target_type = 'WEB_CASE' AND web_case_id IS NOT NULL "
            "AND web_case_version_id IS NOT NULL AND case_id IS NULL AND case_version_id IS NULL "
            "AND scenario_id IS NULL AND scenario_version_id IS NULL)",
            name="test_plan_items_target_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=True
    )
    case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=True
    )
    scenario_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="RESTRICT"), nullable=True
    )
    web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=True
    )
    web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)

    plan: Mapped[TestPlan] = relationship(back_populates="items")


class TestPlanRun(Base):
    __tablename__ = "test_plan_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED','TIMEOUT')",
            name="test_plan_runs_status",
        ),
        CheckConstraint(
            "trigger_type IN ('MANUAL','SCHEDULE','API')",
            name="test_plan_runs_trigger_type",
        ),
        Index("ix_test_plan_runs_project_created", "project_id", "created_at"),
        Index("ix_test_plan_runs_plan_created", "plan_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plan_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    items: Mapped[list["TestPlanRunItem"]] = relationship(
        back_populates="plan_run",
        cascade="all, delete-orphan",
        order_by="TestPlanRunItem.sequence_no",
    )


class TestPlanRunItem(Base):
    __tablename__ = "test_plan_run_items"
    __table_args__ = (
        UniqueConstraint(
            "plan_run_id", "sequence_no", name="uq_test_plan_run_items_run_sequence"
        ),
        UniqueConstraint("run_id", name="uq_test_plan_run_items_run_id"),
        CheckConstraint("sequence_no > 0", name="test_plan_run_items_sequence_positive"),
        CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','CANCELLING','SUCCESS',"
            "'FAILED','CANCELLED','TIMEOUT')",
            name="test_plan_run_items_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_run_id: Mapped[str] = mapped_column(
        ForeignKey("test_plan_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_plan_items.id", ondelete="SET NULL"), nullable=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    plan_run: Mapped[TestPlanRun] = relationship(back_populates="items")
