"""Persist idempotent API assertion evaluations before deferred cleanup."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0044"
down_revision: str | None = "20260911_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_api_execution_evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("response_summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assertion_results", sa.JSON(), nullable=True),
        sa.Column("evaluation_token", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','PASS','FAIL','REVIEW')",
            name="run_api_execution_evaluations_status_values",
        ),
        sa.ForeignKeyConstraint(["case_run_id"], ["case_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "case_run_id",
            name="uq_run_api_execution_evaluations_run_case",
        ),
    )
    op.create_index(
        "ix_run_api_execution_evaluations_run_status",
        "run_api_execution_evaluations",
        ["run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_run_api_execution_evaluations_run_status",
        table_name="run_api_execution_evaluations",
    )
    op.drop_table("run_api_execution_evaluations")
