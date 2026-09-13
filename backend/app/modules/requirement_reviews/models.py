from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class RequirementReview(Base):
    __tablename__ = "requirement_reviews"
    __table_args__ = (
        CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="generation_status_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=True
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    generation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUCCEEDED", server_default="SUCCEEDED"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    human_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
