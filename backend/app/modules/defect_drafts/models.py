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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class DefectDraft(Base):
    """A user-editable draft. V1 never submits it to an external system."""

    __tablename__ = "defect_drafts"
    __table_args__ = (
        CheckConstraint("revision > 0", name="defect_drafts_revision_positive"),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 131072",
            name="defect_drafts_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="defect_drafts_snapshot_sha256_length",
        ),
        UniqueConstraint("ai_call_id", name="uq_defect_drafts_ai_call"),
        Index("ix_defect_drafts_project_updated", "project_id", "updated_at"),
        Index("ix_defect_drafts_run_updated", "run_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    prompt_version_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    output_schema_id: Mapped[int] = mapped_column(
        ForeignKey("output_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(500), nullable=False)
    preconditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reproduction_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)
    actual_result: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ai_analysis: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repair_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )
