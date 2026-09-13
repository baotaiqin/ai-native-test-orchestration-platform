from datetime import datetime

import sqlalchemy as sa
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
from app.modules.runners.models import Runner


class TestRun(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('API_CASE','SCENARIO','WEB_CASE')",
            name="runs_run_type_values",
        ),
        CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','CANCELLING',"
            "'SUCCESS','FAILED','CANCELLED','TIMEOUT')",
            name="runs_status_values",
        ),
        CheckConstraint(
            "trigger_type IN ('MANUAL','API','SYSTEM')",
            name="runs_trigger_type_values",
        ),
        CheckConstraint(
            "required_slot_type IN ('API','WEB','PERFORMANCE')",
            name="runs_slot_type_values",
        ),
        CheckConstraint("total >= 0", name="runs_total_nonnegative"),
        CheckConstraint("pass_count >= 0", name="runs_pass_nonnegative"),
        CheckConstraint("fail_count >= 0", name="runs_fail_nonnegative"),
        CheckConstraint("review_count >= 0", name="runs_review_nonnegative"),
        CheckConstraint("timeout_count >= 0", name="runs_timeout_nonnegative"),
        CheckConstraint("required_slot_count > 0", name="runs_slot_count_positive"),
        CheckConstraint(
            "total_timeout_ms IS NULL OR total_timeout_ms >= 1000",
            name="runs_total_timeout_ms_bounds",
        ),
        CheckConstraint("force_stopped IN (0, 1)", name="runs_force_stopped_values"),
        CheckConstraint(
            "(run_type = 'API_CASE' AND case_id IS NOT NULL AND "
            "case_version_id IS NOT NULL AND scenario_id IS NULL AND "
            "scenario_version_id IS NULL AND web_case_id IS NULL AND "
            "web_case_version_id IS NULL) OR "
            "(run_type = 'SCENARIO' AND scenario_id IS NOT NULL AND "
            "scenario_version_id IS NOT NULL AND case_id IS NULL AND "
            "case_version_id IS NULL AND web_case_id IS NULL AND "
            "web_case_version_id IS NULL) OR "
            "(run_type = 'WEB_CASE' AND web_case_id IS NOT NULL AND "
            "web_case_version_id IS NOT NULL AND case_id IS NULL AND "
            "case_version_id IS NULL AND scenario_id IS NULL AND "
            "scenario_version_id IS NULL)",
            name="runs_target_versions_consistent",
        ),
        Index("ix_runs_project_status_created", "project_id", "status", "created_at"),
        Index("ix_runs_runner_status", "runner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    runner_id: Mapped[str | None] = mapped_column(
        ForeignKey("runners.id", ondelete="SET NULL"), nullable=True, index=True
    )
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    scenario_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="RESTRICT"), nullable=True
    )
    web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    required_capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_slot_type: Mapped[str] = mapped_column(String(16), nullable=False)
    required_slot_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    runtime_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runtime_variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    total_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    force_stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    force_stopped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    timeout_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    case_runs: Mapped[list["CaseRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="CaseRun.sequence_no"
    )
    dispatch_outbox: Mapped["RunDispatchOutbox | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class CaseRun(Base):
    __tablename__ = "case_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_no", name="uq_case_runs_run_sequence"),
        CheckConstraint(
            "status IN ('CREATED','ASSIGNED','RUNNING','CANCELLING','SUCCESS','FAILED',"
            "'REVIEW','CANCELLED','TIMEOUT','SKIPPED')",
            name="case_runs_status_values",
        ),
        CheckConstraint("duration >= 0", name="case_runs_duration_nonnegative"),
        CheckConstraint("retry_count >= 0", name="case_runs_retry_nonnegative"),
        CheckConstraint("sequence_no > 0", name="case_runs_sequence_positive"),
        CheckConstraint(
            "(case_id IS NOT NULL AND scenario_id IS NULL AND web_case_id IS NULL) OR "
            "(case_id IS NULL AND scenario_id IS NOT NULL AND web_case_id IS NULL) OR "
            "(case_id IS NULL AND scenario_id IS NULL AND web_case_id IS NOT NULL)",
            name="case_runs_target_exclusive",
        ),
        CheckConstraint(
            "(case_id IS NOT NULL AND case_version_id IS NOT NULL AND "
            "scenario_id IS NULL AND scenario_version_id IS NULL AND "
            "web_case_id IS NULL AND web_case_version_id IS NULL) OR "
            "(scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL AND "
            "case_id IS NULL AND case_version_id IS NULL AND "
            "web_case_id IS NULL AND web_case_version_id IS NULL) OR "
            "(web_case_id IS NOT NULL AND web_case_version_id IS NOT NULL AND "
            "case_id IS NULL AND case_version_id IS NULL AND scenario_id IS NULL AND "
            "scenario_version_id IS NULL)",
            name="case_runs_target_versions_consistent",
        ),
        Index("ix_case_runs_run_status", "run_id", "status"),
        UniqueConstraint(
            "run_id", "dataset_version_id", "row_index",
            name="uq_case_runs_run_dataset_row",
        ),
        CheckConstraint(
            "(dataset_id IS NULL AND dataset_version_id IS NULL AND row_index IS NULL "
            "AND parameter_snapshot IS NULL) OR "
            "(dataset_id IS NOT NULL AND dataset_version_id IS NOT NULL "
            "AND row_index IS NOT NULL AND row_index > 0 AND parameter_snapshot IS NOT NULL)",
            name="case_runs_dataset_identity_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
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
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameter_snapshot: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    run: Mapped[TestRun] = relationship(back_populates="case_runs")
    step_runs: Mapped[list["StepRun"]] = relationship(
        back_populates="case_run", cascade="all, delete-orphan", order_by="StepRun.sequence_no"
    )


class StepRun(Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("case_run_id", "node_id", name="uq_step_runs_case_run_node"),
        CheckConstraint(
            "status IN ('CREATED','ASSIGNED','RUNNING','CANCELLING','SUCCESS','FAILED',"
            "'REVIEW','CANCELLED','TIMEOUT','SKIPPED')",
            name="step_runs_status_values",
        ),
        CheckConstraint("duration >= 0", name="step_runs_duration_nonnegative"),
        CheckConstraint("retry_count >= 0", name="step_runs_retry_nonnegative"),
        CheckConstraint("sequence_no > 0", name="step_runs_sequence_positive"),
        Index("ix_step_runs_case_run_status", "case_run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    case_run: Mapped[CaseRun] = relationship(back_populates="step_runs")


class RunDispatchOutbox(Base):
    __tablename__ = "run_dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_run_dispatch_outbox_message_id"),
        UniqueConstraint("run_id", name="uq_run_dispatch_outbox_run_id"),
        CheckConstraint("schema_version = 1", name="run_dispatch_outbox_schema_version_v1"),
        CheckConstraint(
            "status IN ('PENDING','PUBLISHED','FAILED')",
            name="run_dispatch_outbox_status_values",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="run_dispatch_outbox_attempt_count_bounds",
        ),
        Index(
            "ix_run_dispatch_outbox_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_run_dispatch_outbox_runner_status",
            "runner_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=False
    )
    routing_key: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_runner_id: Mapped[str | None] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=True
    )

    run: Mapped[TestRun] = relationship(back_populates="dispatch_outbox")
    runner: Mapped[Runner] = relationship(foreign_keys=[runner_id])
    claimed_runner: Mapped[Runner | None] = relationship(foreign_keys=[claimed_runner_id])


class RunApiExecutionResult(Base):
    __tablename__ = "run_api_execution_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_run_id",
            name="uq_run_api_execution_results_run_case",
        ),
        CheckConstraint(
            "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR','TIMEOUT','CANCELLED')",
            name="run_api_execution_results_outcome_values",
        ),
        CheckConstraint(
            "status IN ('SUCCESS','FAILED','REVIEW','TIMEOUT','CANCELLED')",
            name="run_api_execution_results_status_values",
        ),
        CheckConstraint(
            "retry_count >= 0 AND retry_count <= 3",
            name="run_api_execution_results_retry_count_bounds",
        ),
        Index(
            "ix_run_api_execution_results_run_status_completed",
            "run_id",
            "status",
            "completed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    response_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assertion_results: Mapped[list] = mapped_column(JSON, nullable=False)
    action_traces: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


class RunApiExecutionEvaluation(Base):
    """Idempotent assertion decision created before deferred API cleanup."""

    __tablename__ = "run_api_execution_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_run_id",
            name="uq_run_api_execution_evaluations_run_case",
        ),
        CheckConstraint(
            "status IN ('PENDING','PASS','FAIL','REVIEW')",
            name="run_api_execution_evaluations_status_values",
        ),
        Index(
            "ix_run_api_execution_evaluations_run_status",
            "run_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    response_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    assertion_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evaluation_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


class RunScenarioExecutionResult(Base):
    """Auditable, secret-free terminal result for the Scenario runner contract."""

    __tablename__ = "run_scenario_execution_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_run_id",
            name="uq_run_scenario_execution_results_run_case",
        ),
        UniqueConstraint("message_id", name="uq_run_scenario_execution_results_message"),
        CheckConstraint(
            "outcome IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="run_scenario_execution_results_outcome_values",
        ),
        CheckConstraint(
            "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="run_scenario_execution_results_status_values",
        ),
        Index(
            "ix_run_scenario_execution_results_run_status_completed",
            "run_id",
            "status",
            "completed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    traces: Mapped[list] = mapped_column(JSON, nullable=False)
    assertion_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


class RunWebExecutionResult(Base):
    """Bounded, secret-free audit result for the Web Case runner contract."""

    __tablename__ = "run_web_execution_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_run_id", name="uq_run_web_execution_results_run_case"),
        UniqueConstraint("message_id", name="uq_run_web_execution_results_message"),
        CheckConstraint(
            "outcome IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="run_web_execution_results_outcome_values",
        ),
        CheckConstraint(
            "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="run_web_execution_results_status_values",
        ),
        Index(
            "ix_run_web_execution_results_run_status_completed",
            "run_id",
            "status",
            "completed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    traces: Mapped[list] = mapped_column(JSON, nullable=False)
    assertion_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    session_recovery: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


Run = TestRun
