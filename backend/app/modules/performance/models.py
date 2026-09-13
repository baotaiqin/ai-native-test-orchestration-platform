from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class PerformanceProfile(Base):
    __tablename__ = "performance_profiles"
    __table_args__ = (
        CheckConstraint("concurrency >= 1 AND concurrency <= 100", name="perf_profile_concurrency"),
        CheckConstraint("iterations >= 1 AND iterations <= 10000", name="perf_profile_iterations"),
        CheckConstraint(
            "warmup_iterations >= 0 AND warmup_iterations <= 1000",
            name="perf_profile_warmup_iterations",
        ),
        CheckConstraint(
            "request_timeout_ms >= 100 AND request_timeout_ms <= 120000",
            name="perf_profile_timeout",
        ),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="perf_profile_status"),
        CheckConstraint(
            "load_mode IN ('FIXED_ITERATIONS','FIXED_RPS','FIXED_CONCURRENCY','STEP_LOAD')",
            name="perf_profile_load_mode",
        ),
        CheckConstraint(
            "(load_mode = 'FIXED_ITERATIONS' AND target_rps IS NULL AND duration_seconds IS NULL) "
            "OR (load_mode = 'FIXED_RPS' AND target_rps > 0 "
            "AND duration_seconds >= 1 AND duration_seconds <= 3600) "
            "OR (load_mode = 'FIXED_CONCURRENCY' AND target_rps IS NULL "
            "AND duration_seconds >= 1 AND duration_seconds <= 3600) "
            "OR (load_mode = 'STEP_LOAD' AND target_rps IS NULL AND duration_seconds IS NULL)",
            name="perf_profile_load_config",
        ),
        CheckConstraint("target_type IN ('API_CASE','SCENARIO')", name="perf_profile_target_type"),
        CheckConstraint(
            "(target_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
            "AND scenario_id IS NULL AND scenario_version_id IS NULL) OR "
            "(target_type = 'SCENARIO' AND case_id IS NULL AND case_version_id IS NULL "
            "AND scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL)",
            name="perf_profile_target_ref",
        ),
        CheckConstraint(
            "engine IN ('PYTHON_HTTP','PYTHON_STREAM','JMETER')", name="perf_profile_engine"
        ),
        CheckConstraint(
            "cleanup_mode IN ('NONE','RESOURCE_CLEANUP','BATCH_CLEANUP')",
            name="perf_profile_cleanup_mode",
        ),
        Index("ix_performance_profiles_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, default="API_CASE")
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
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    load_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="FIXED_ITERATIONS")
    target_rps: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_stages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    warmup_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=30000)
    engine: Mapped[str] = mapped_column(String(24), nullable=False, default="PYTHON_HTTP")
    cleanup_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="NONE")
    stream_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sla: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


class PerformanceRun(Base):
    __tablename__ = "performance_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','SUCCESS','FAILED',"
            "'CANCELLED','TIMEOUT')",
            name="performance_runs_status",
        ),
        Index("ix_performance_runs_profile_created", "profile_id", "created_at"),
        Index("ix_performance_runs_project_created", "project_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("performance_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sla_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PerformanceAnalysis(Base):
    __tablename__ = "performance_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','COMPLETED')",
            name="performance_analyses_status",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 65536",
            name="performance_analyses_snapshot_size",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="performance_analyses_snapshot_sha256",
        ),
        CheckConstraint(
            "source_sla_verdict IN ('MEETS_SLA','MISSES_SLA','NO_SLA')",
            name="performance_analyses_sla_verdict",
        ),
        UniqueConstraint("ai_call_id", name="uq_performance_analyses_ai_call"),
        UniqueConstraint("run_id", "draft_key", name="uq_performance_analyses_run_draft"),
        Index("ix_performance_analyses_run_created", "run_id", "created_at"),
        Index("ix_performance_analyses_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("performance_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    prompt_version_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    output_schema_id: Mapped[int] = mapped_column(
        ForeignKey("output_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    draft_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sla_verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actual_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repair_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    needs_human_review: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
