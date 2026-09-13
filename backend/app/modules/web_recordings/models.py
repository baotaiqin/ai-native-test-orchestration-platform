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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class WebRecording(Base):
    """A bounded, auditable browser recording session, independent from TestRun."""

    __tablename__ = "web_recordings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','STOP_REQUESTED','COMPLETED',"
            "'FAILED','CANCELLED')",
            name="web_recordings_status_values",
        ),
        CheckConstraint(
            "dispatch_status IN ('PENDING','PUBLISHED','FAILED')",
            name="web_recordings_dispatch_status_values",
        ),
        CheckConstraint("browser = 'CHROME'", name="web_recordings_browser_values"),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="web_recordings_attempt_count_bounds",
        ),
        CheckConstraint(
            "event_count >= 0 AND event_count <= 1000",
            name="web_recordings_event_count_bounds",
        ),
        CheckConstraint(
            "dom_context_size >= 0 AND dom_context_size <= 524288",
            name="web_recordings_dom_context_size_bounds",
        ),
        CheckConstraint(
            "save_session = 0 OR save_session_name IS NOT NULL",
            name="web_recordings_save_session_name_required",
        ),
        CheckConstraint(
            "save_session_expires_at IS NULL OR save_session_expires_at > created_at",
            name="web_recordings_save_session_expiry_after_create",
        ),
        Index(
            "ix_web_recordings_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
        Index("ix_web_recordings_runner_status", "runner_id", "status"),
        Index(
            "ix_web_recordings_project_confirmed",
            "project_id",
            "confirmed_web_case_id",
        ),
        UniqueConstraint("message_id", name="uq_web_recordings_message_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    session_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("session_profiles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    plan_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_test_plan_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    saved_session_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("session_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    start_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    browser: Mapped[str] = mapped_column(String(16), nullable=False, default="CHROME")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")
    dispatch_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    routing_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_runner_id: Mapped[str | None] = mapped_column(
        ForeignKey("runners.id", ondelete="RESTRICT"), nullable=True
    )
    save_session: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    save_session_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    save_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dom_context_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    dom_context_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dom_context_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confirmed_web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=True
    )
    confirmed_web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )
