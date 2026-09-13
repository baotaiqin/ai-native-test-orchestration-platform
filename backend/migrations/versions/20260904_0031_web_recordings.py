"""Add the independent deterministic Web recording control plane."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0031"
down_revision: str | None = "20260902_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_recordings",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("runner_id", sa.String(length=64), nullable=False),
        sa.Column("session_profile_id", sa.Integer(), nullable=True),
        sa.Column("saved_session_profile_id", sa.Integer(), nullable=True),
        sa.Column("start_url", sa.String(length=2048), nullable=False),
        sa.Column("browser", sa.String(length=16), nullable=False, server_default="CHROME"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CREATED"),
        sa.Column(
            "dispatch_status", sa.String(length=16), nullable=False, server_default="PENDING"
        ),
        sa.Column("message_id", sa.String(length=64), nullable=True),
        sa.Column("routing_key", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_runner_id", sa.String(length=64), nullable=True),
        sa.Column("save_session", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("save_session_name", sa.String(length=128), nullable=True),
        sa.Column("save_session_expires_at", sa.DateTime(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dom_context_ciphertext", sa.Text(), nullable=True),
        sa.Column("dom_context_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("dom_context_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_requested_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("confirmed_web_case_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_web_case_version_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','STOP_REQUESTED','COMPLETED',"
            "'FAILED','CANCELLED')",
            name="ck_web_recordings_web_recordings_status_values",
        ),
        sa.CheckConstraint(
            "dispatch_status IN ('PENDING','PUBLISHED','FAILED')",
            name="ck_web_recordings_web_recordings_dispatch_status_values",
        ),
        sa.CheckConstraint(
            "browser = 'CHROME'", name="ck_web_recordings_web_recordings_browser_values"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="ck_web_recordings_web_recordings_attempt_count_bounds",
        ),
        sa.CheckConstraint(
            "event_count >= 0 AND event_count <= 1000",
            name="ck_web_recordings_web_recordings_event_count_bounds",
        ),
        sa.CheckConstraint(
            "dom_context_size >= 0 AND dom_context_size <= 524288",
            name="ck_web_recordings_web_recordings_dom_context_size_bounds",
        ),
        sa.CheckConstraint(
            "save_session = 0 OR save_session_name IS NOT NULL",
            name="ck_web_recordings_web_recordings_save_session_name_required",
        ),
        sa.CheckConstraint(
            "save_session_expires_at IS NULL OR save_session_expires_at > created_at",
            name="ck_web_recordings_web_recordings_save_session_expiry_after_create",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_web_recordings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_web_recordings_environment_id_environments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_web_recordings_runner_id_runners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_profile_id"],
            ["session_profiles.id"],
            name=op.f("fk_web_recordings_session_profile_id_session_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["saved_session_profile_id"],
            ["session_profiles.id"],
            name=op.f("fk_web_recordings_saved_session_profile_id_session_profiles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["claimed_runner_id"],
            ["runners.id"],
            name=op.f("fk_web_recordings_claimed_runner_id_runners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_web_case_id"],
            ["web_cases.id"],
            name=op.f("fk_web_recordings_confirmed_web_case_id_web_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_web_case_version_id"],
            ["web_case_versions.id"],
            name=op.f(
                "fk_web_recordings_confirmed_web_case_version_id_web_case_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_recordings")),
        sa.UniqueConstraint("message_id", name="uq_web_recordings_message_id"),
    )
    op.create_index("ix_web_recordings_project_id", "web_recordings", ["project_id"])
    op.create_index("ix_web_recordings_environment_id", "web_recordings", ["environment_id"])
    op.create_index("ix_web_recordings_runner_id", "web_recordings", ["runner_id"])
    op.create_index(
        "ix_web_recordings_session_profile_id", "web_recordings", ["session_profile_id"]
    )
    op.create_index(
        "ix_web_recordings_saved_session_profile_id",
        "web_recordings",
        ["saved_session_profile_id"],
    )
    op.create_index(
        "ix_web_recordings_project_status_created",
        "web_recordings",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_web_recordings_runner_status", "web_recordings", ["runner_id", "status"]
    )
    op.create_index(
        "ix_web_recordings_project_confirmed",
        "web_recordings",
        ["project_id", "confirmed_web_case_id"],
    )


def downgrade() -> None:
    op.drop_table("web_recordings")
