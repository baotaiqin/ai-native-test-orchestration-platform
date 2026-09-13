"""Add editable internal defect drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0051"
down_revision: str | None = "20260911_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "defect_drafts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=True),
        sa.Column("prompt_version_id", sa.Integer(), nullable=False),
        sa.Column("output_schema_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("module", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=500), nullable=False),
        sa.Column("preconditions", sa.JSON(), nullable=False),
        sa.Column("reproduction_steps", sa.JSON(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=False),
        sa.Column("actual_result", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("ai_analysis", sa.Text(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("actual_model", sa.String(length=128), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("repair_used", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision > 0", name=op.f(f"ck_{_TABLE}_{_TABLE}_revision_positive")),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 131072",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_size_bounds"),
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_sha256_length"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_run_id"], ["case_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["prompt_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["output_schema_id"], ["output_schemas.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_logs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_call_id", name=f"uq_{_TABLE}_ai_call"),
    )
    op.create_index(f"ix_{_TABLE}_project_id", _TABLE, ["project_id"])
    op.create_index(f"ix_{_TABLE}_run_id", _TABLE, ["run_id"])
    op.create_index(f"ix_{_TABLE}_case_run_id", _TABLE, ["case_run_id"])
    op.create_index(f"ix_{_TABLE}_project_updated", _TABLE, ["project_id", "updated_at"])
    op.create_index(f"ix_{_TABLE}_run_updated", _TABLE, ["run_id", "updated_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
