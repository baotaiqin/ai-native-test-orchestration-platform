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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class WebTestPlan(Base):
    """An immutable requirement/API snapshot analyzed into abstract Web cases."""

    __tablename__ = "web_test_plans"
    __table_args__ = (
        CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="generation_status",
        ),
        CheckConstraint(
            "active_key IS NULL OR active_key = 'ACTIVE'",
            name="active_key",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="snapshot_size",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="snapshot_sha",
        ),
        UniqueConstraint("ai_call_id", name="uq_web_test_plans_ai_call"),
        UniqueConstraint("project_id", "active_key", name="uq_web_test_plans_active"),
        Index("ix_web_test_plans_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_document_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_document_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    api_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_definition_imports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    generation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    active_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class WebTestPlanItem(Base):
    """Abstract intent; it deliberately contains no invented UI locator."""

    __tablename__ = "web_test_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "candidate_key", name="uq_web_plan_items_key"),
        Index("ix_web_plan_items_plan_order", "plan_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("web_test_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    api_definition_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    preconditions: Mapped[list] = mapped_column(JSON, nullable=False)
    planned_steps: Mapped[list] = mapped_column(JSON, nullable=False)
    expected_outcomes: Mapped[list] = mapped_column(JSON, nullable=False)
    start_url_hint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class WebExploration(Base):
    """A bounded Runner job backed by a restricted Playwright MCP session."""

    __tablename__ = "web_explorations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','STOP_REQUESTED','COMPLETED',"
            "'FAILED','CANCELLED')",
            name="status",
        ),
        CheckConstraint(
            "dispatch_status IN ('PENDING','PUBLISHED','FAILED')",
            name="dispatch_status",
        ),
        CheckConstraint("max_steps >= 1 AND max_steps <= 50", name="max_steps"),
        CheckConstraint(
            "current_step >= 0 AND current_step <= max_steps",
            name="current_step",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="attempt_count",
        ),
        UniqueConstraint("message_id", name="uq_web_explorations_message_id"),
        Index("ix_web_explorations_item_created", "plan_item_id", "created_at"),
        Index("ix_web_explorations_runner_status", "runner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_item_id: Mapped[int] = mapped_column(
        ForeignKey("web_test_plan_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    session_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("session_profiles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    use_login_credentials: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    headless: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decision_prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    start_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    allowed_origins: Mapped[list] = mapped_column(JSON, nullable=False)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")
    dispatch_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    routing_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    claimed_runner_id: Mapped[str | None] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=True
    )
    observations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action_trace: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    decision_ai_call_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    evidence_artifacts: Mapped[list["WebExplorationEvidence"]] = relationship(
        back_populates="exploration",
        cascade="all, delete-orphan",
        order_by="WebExplorationEvidence.sequence, WebExplorationEvidence.created_at",
    )


class WebExplorationEvidence(Base):
    """A bounded PNG captured by Runner at an exploration checkpoint."""

    __tablename__ = "web_exploration_evidence"
    __table_args__ = (
        CheckConstraint("sequence >= 1 AND sequence <= 50", name="sequence"),
        CheckConstraint("kind IN ('CHECKPOINT','FINAL','FAILURE')", name="kind"),
        CheckConstraint("size > 0 AND size <= 5000000", name="size"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        UniqueConstraint(
            "exploration_id", "file_name", name="uq_web_exploration_evidence_file"
        ),
        UniqueConstraint(
            "minio_bucket", "minio_key", name="uq_web_exploration_evidence_object"
        ),
        Index("ix_web_exploration_evidence_sequence", "exploration_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    exploration_id: Mapped[str] = mapped_column(
        ForeignKey("web_explorations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False, default="image/png")
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    minio_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    minio_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    exploration: Mapped[WebExploration] = relationship(back_populates="evidence_artifacts")


class WebDesignRevision(Base):
    """Reviewable executable WebCaseContent reconciled from observed page facts."""

    __tablename__ = "web_design_revisions"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('MCP_EXPLORATION','MANUAL_RECORDING')",
            name="source_type",
        ),
        CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="generation_status",
        ),
        CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="status",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="snapshot_sha",
        ),
        UniqueConstraint("ai_call_id", name="uq_web_design_revisions_ai_call"),
        Index("ix_web_design_revisions_item_created", "plan_item_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_item_id: Mapped[int] = mapped_column(
        ForeignKey("web_test_plan_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exploration_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_explorations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recording_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_recordings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    generation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    human_content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="SET NULL"), nullable=True
    )
    created_web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
