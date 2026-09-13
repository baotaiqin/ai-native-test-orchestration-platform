from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class WebRecordingAiSuggestion(Base):
    """A reviewable, auditable AI proposal derived only from safe recording events."""

    __tablename__ = "web_recording_ai_suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="web_recording_ai_suggestions_status_values",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="web_recording_ai_suggestions_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="web_recording_ai_suggestions_snapshot_sha256_length",
        ),
        CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name="web_recording_ai_suggestions_draft_key_values",
        ),
        UniqueConstraint("ai_call_id", name="uq_web_recording_ai_suggestions_ai_call"),
        UniqueConstraint(
            "recording_id",
            "draft_key",
            name="uq_web_recording_ai_suggestions_recording_draft_key",
        ),
        Index(
            "ix_web_recording_ai_suggestions_recording_status",
            "recording_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_web_recording_ai_suggestions_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("web_recordings.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    draft_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    structured_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    canonical_suggested_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    human_content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confirmed_web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=True
    )
    confirmed_web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
