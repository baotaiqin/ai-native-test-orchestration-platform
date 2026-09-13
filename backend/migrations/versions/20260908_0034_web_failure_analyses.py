"""Persist safe, read-only AI analyses of terminal Web failures."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0034"
down_revision: str | None = "20260908_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "web_failure_analyses"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version_id", sa.Integer(), nullable=False),
        sa.Column("output_schema_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("draft_key", sa.String(length=16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.String(length=5000), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=True),
        sa.Column("actual_model", sa.String(length=128), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
        sa.Column("repair_used", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT','COMPLETED')",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_status_values"),
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 131072",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_size_bounds"),
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_sha256_length"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f(f"fk_{_TABLE}_project_id_projects"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"],
            name=op.f(f"fk_{_TABLE}_run_id_runs"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"], ["case_runs.id"],
            name=op.f(f"fk_{_TABLE}_case_run_id_case_runs"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["prompt_versions.id"],
            name=op.f(f"fk_{_TABLE}_prompt_version_id_prompt_versions"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_schema_id"], ["output_schemas.id"],
            name=op.f(f"fk_{_TABLE}_output_schema_id_output_schemas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ai_call_id"], ["ai_call_logs.id"],
            name=op.f(f"fk_{_TABLE}_ai_call_id_ai_call_logs"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.UniqueConstraint("ai_call_id", name=f"uq_{_TABLE}_ai_call"),
        sa.UniqueConstraint(
            "run_id", "case_run_id", "draft_key",
            name=f"uq_{_TABLE}_run_case_draft",
        ),
    )
    op.create_index(f"ix_{_TABLE}_project_id", _TABLE, ["project_id"])
    op.create_index(f"ix_{_TABLE}_run_id", _TABLE, ["run_id"])
    op.create_index(f"ix_{_TABLE}_case_run_id", _TABLE, ["case_run_id"])
    op.create_index(
        f"ix_{_TABLE}_run_case_created", _TABLE,
        ["run_id", "case_run_id", "created_at"],
    )
    op.create_index(f"ix_{_TABLE}_project_created", _TABLE, ["project_id", "created_at"])
    op.create_index(f"ix_{_TABLE}_status_created", _TABLE, ["status", "created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
