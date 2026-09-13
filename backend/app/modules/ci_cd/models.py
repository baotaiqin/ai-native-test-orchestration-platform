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


class CiAccessToken(Base):
    __tablename__ = "ci_access_tokens"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="ci_tokens_status"),
        Index("ix_ci_tokens_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    allowed_plan_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)


class CiRunInvocation(Base):
    __tablename__ = "ci_run_invocations"
    __table_args__ = (
        UniqueConstraint("token_id", "idempotency_digest", name="uq_ci_invocations_idempotency"),
        CheckConstraint("status IN ('STARTING','STARTED','FAILED')", name="ci_invocations_status"),
        Index("ix_ci_invocations_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_id: Mapped[int] = mapped_column(
        ForeignKey("ci_access_tokens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTING")
    plan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("test_plan_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        CheckConstraint("enabled IN (0, 1)", name="webhook_endpoints_enabled"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="webhook_endpoints_status"),
        CheckConstraint("max_attempts BETWEEN 1 AND 5", name="webhook_endpoints_attempts"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="webhook_endpoints_timeout"),
        Index("ix_webhook_endpoints_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_hint: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_signing_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_deliveries_event"),
        CheckConstraint(
            "status IN ('PENDING','SENDING','RETRY','SUCCEEDED','FAILED')",
            name="webhook_deliveries_status",
        ),
        CheckConstraint("attempt_count >= 0", name="webhook_deliveries_attempts"),
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_run_id: Mapped[str] = mapped_column(
        ForeignKey("test_plan_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
