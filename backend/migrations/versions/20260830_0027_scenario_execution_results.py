"""持久化 Scenario Runner 安全完成结果。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0027"
down_revision: str | None = "20260829_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "run_scenario_execution_results"
_OUTCOME_CONSTRAINT = (
    "ck_run_scenario_execution_results_run_scenario_execution_results_outcome_values"
)
_STATUS_CONSTRAINT = (
    "ck_run_scenario_execution_results_run_scenario_execution_results_status_values"
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("traces", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name=op.f(_OUTCOME_CONSTRAINT),
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name=op.f(_STATUS_CONSTRAINT),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_run_scenario_execution_results_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name="fk_run_scenario_execution_results_case_run_id_case_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_scenario_execution_results"),
        sa.UniqueConstraint(
            "run_id",
            "case_run_id",
            name="uq_run_scenario_execution_results_run_case",
        ),
        sa.UniqueConstraint(
            "message_id", name="uq_run_scenario_execution_results_message"
        ),
    )
    op.create_index(
        "ix_run_scenario_execution_results_run_status_completed",
        _TABLE,
        ["run_id", "status", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_run_scenario_execution_results_run_status_completed", table_name=_TABLE
    )
    op.drop_table(_TABLE)
