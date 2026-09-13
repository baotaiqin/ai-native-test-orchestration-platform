from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0075"
down_revision: str | None = "20260913_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ci_access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("allowed_plan_ids", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="ci_tokens_status"),
    )
    op.create_index("ix_ci_access_tokens_project_id", "ci_access_tokens", ["project_id"])
    op.create_index("ix_ci_access_tokens_expires_at", "ci_access_tokens", ["expires_at"])
    op.create_index("ix_ci_tokens_project_status", "ci_access_tokens", ["project_id", "status"])

    op.create_table(
        "ci_run_invocations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("ci_access_tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("test_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_digest", sa.String(64), nullable=False),
        sa.Column("pipeline_ref", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="STARTING"),
        sa.Column(
            "plan_run_id",
            sa.String(128),
            sa.ForeignKey("test_plan_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_id", "idempotency_digest", name="uq_ci_invocations_idempotency"),
        sa.CheckConstraint(
            "status IN ('STARTING','STARTED','FAILED')", name="ci_invocations_status"
        ),
    )
    op.create_index("ix_ci_run_invocations_token_id", "ci_run_invocations", ["token_id"])
    op.create_index("ix_ci_run_invocations_project_id", "ci_run_invocations", ["project_id"])
    op.create_index("ix_ci_run_invocations_plan_id", "ci_run_invocations", ["plan_id"])
    op.create_index("ix_ci_run_invocations_plan_run_id", "ci_run_invocations", ["plan_run_id"])
    op.create_index(
        "ix_ci_invocations_project_created", "ci_run_invocations", ["project_id", "created_at"]
    )

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("encrypted_url", sa.Text(), nullable=False),
        sa.Column("target_hint", sa.String(255), nullable=False),
        sa.Column("encrypted_signing_secret", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="webhook_endpoints_enabled"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="webhook_endpoints_status"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 5", name="webhook_endpoints_attempts"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="webhook_endpoints_timeout"),
    )
    op.create_index("ix_webhook_endpoints_project_id", "webhook_endpoints", ["project_id"])
    op.create_index(
        "ix_webhook_endpoints_project_status", "webhook_endpoints", ["project_id", "status"]
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "endpoint_id",
            sa.Integer(),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_run_id",
            sa.String(128),
            sa.ForeignKey("test_plan_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.String(1000), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_deliveries_event"),
        sa.CheckConstraint(
            "status IN ('PENDING','SENDING','RETRY','SUCCEEDED','FAILED')",
            name="webhook_deliveries_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="webhook_deliveries_attempts"),
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_project_id", "webhook_deliveries", ["project_id"])
    op.create_index("ix_webhook_deliveries_plan_run_id", "webhook_deliveries", ["plan_run_id"])
    op.create_index(
        "ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_project_created", "webhook_deliveries", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
    op.drop_table("ci_run_invocations")
    op.drop_table("ci_access_tokens")
