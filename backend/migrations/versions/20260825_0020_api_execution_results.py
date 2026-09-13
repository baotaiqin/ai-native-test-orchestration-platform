"""保存 API_CASE 执行完成的脱敏审计结果。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0020"
down_revision: str | None = "20260825_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_api_execution_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_summary", sa.JSON(), nullable=True),
        sa.Column("assertion_results", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR')",
            name="run_api_execution_results_outcome_values",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILED')",
            name="run_api_execution_results_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_api_execution_results_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name=op.f("fk_run_api_execution_results_case_run_id_case_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_api_execution_results")),
        sa.UniqueConstraint(
            "run_id",
            "case_run_id",
            name=op.f("uq_run_api_execution_results_run_case"),
        ),
        sa.UniqueConstraint(
            "message_id",
            name=op.f("uq_run_api_execution_results_message"),
        ),
    )
    op.create_index(
        "ix_run_api_execution_results_run_status_completed",
        "run_api_execution_results",
        ["run_id", "status", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_run_api_execution_results_run_status_completed",
        table_name="run_api_execution_results",
    )
    op.drop_table("run_api_execution_results")
