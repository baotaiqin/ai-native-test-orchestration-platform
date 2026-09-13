from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class WebFailureAnalysis(Base):
    """Immutable audit record for a safe Web failure analysis."""

    __tablename__ = "web_failure_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','COMPLETED')",
            name="web_failure_analyses_status_values",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 131072",
            name="web_failure_analyses_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="web_failure_analyses_snapshot_sha256_length",
        ),
        UniqueConstraint("ai_call_id", name="uq_web_failure_analyses_ai_call"),
        UniqueConstraint(
            "run_id",
            "case_run_id",
            "draft_key",
            name="uq_web_failure_analyses_run_case_draft",
        ),
        Index(
            "ix_web_failure_analyses_run_case_created",
            "run_id",
            "case_run_id",
            "created_at",
        ),
        Index(
            "ix_web_failure_analyses_project_created",
            "project_id",
            "created_at",
        ),
        Index(
            "ix_web_failure_analyses_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="RESTRICT"), nullable=False, index=True
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
    additional_instructions: Mapped[str | None] = mapped_column(
        String(5000), nullable=True
    )
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actual_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repair_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    needs_human_review: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive
    )

