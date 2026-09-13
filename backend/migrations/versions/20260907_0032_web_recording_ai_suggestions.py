"""Persist safe, human-reviewable AI suggestions for Web Recordings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0032"
down_revision: str | None = "20260904_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_recording_ai_suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("recording_id", sa.String(length=128), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("draft_key", sa.String(length=16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.String(length=5000), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("canonical_suggested_content", sa.JSON(), nullable=False),
        sa.Column("human_content", sa.JSON(), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("confirmed_web_case_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_web_case_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="ck_web_recording_ai_suggestions_web_recording_ai_suggestions_status_values",
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="ck_web_recording_ai_suggestions_web_recording_ai_suggestions_snapshot_size_bounds",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="ck_web_recording_ai_suggestions_web_recording_ai_suggestions_snapshot_sha256_length",
        ),
        sa.CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name="ck_web_recording_ai_suggestions_web_recording_ai_suggestions_draft_key_values",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_web_recording_ai_suggestions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"],
            ["web_recordings.id"],
            name=op.f("fk_web_recording_ai_suggestions_recording_id_web_recordings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ai_call_id"],
            ["ai_call_logs.id"],
            name=op.f("fk_web_recording_ai_suggestions_ai_call_id_ai_call_logs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_web_case_id"],
            ["web_cases.id"],
            name=op.f("fk_web_recording_ai_suggestions_confirmed_web_case_id_web_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_web_case_version_id"],
            ["web_case_versions.id"],
            name=op.f(
                "fk_web_recording_ai_suggestions_confirmed_web_case_version_id_web_case_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_recording_ai_suggestions")),
        sa.UniqueConstraint("ai_call_id", name="uq_web_recording_ai_suggestions_ai_call"),
        sa.UniqueConstraint(
            "recording_id",
            "draft_key",
            name="uq_web_recording_ai_suggestions_recording_draft_key",
        ),
    )
    op.create_index(
        "ix_web_recording_ai_suggestions_project_id",
        "web_recording_ai_suggestions",
        ["project_id"],
    )
    op.create_index(
        "ix_web_recording_ai_suggestions_recording_id",
        "web_recording_ai_suggestions",
        ["recording_id"],
    )
    op.create_index(
        "ix_web_recording_ai_suggestions_recording_status",
        "web_recording_ai_suggestions",
        ["recording_id", "status", "created_at"],
    )
    op.create_index(
        "ix_web_recording_ai_suggestions_project_created",
        "web_recording_ai_suggestions",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("web_recording_ai_suggestions")
