"""Add immutable API dataset iteration identity to CaseRun."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0042"
down_revision: str | None = "20260910_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_run_api_execution_results_message",
        "run_api_execution_results",
        type_="unique",
    )
    op.add_column("case_runs", sa.Column("dataset_id", sa.Integer(), nullable=True))
    op.add_column(
        "case_runs", sa.Column("dataset_version_id", sa.Integer(), nullable=True)
    )
    op.add_column("case_runs", sa.Column("row_index", sa.Integer(), nullable=True))
    op.add_column("case_runs", sa.Column("parameter_snapshot", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_case_runs_dataset",
        "case_runs",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_case_runs_dataset_version",
        "case_runs",
        "dataset_versions",
        ["dataset_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_case_runs_dataset_id", "case_runs", ["dataset_id"])
    op.create_index(
        "ix_case_runs_dataset_version_id", "case_runs", ["dataset_version_id"]
    )
    op.create_unique_constraint(
        "uq_case_runs_run_dataset_row",
        "case_runs",
        ["run_id", "dataset_version_id", "row_index"],
    )
    op.create_check_constraint(
        "case_runs_dataset_identity_consistent",
        "case_runs",
        "(dataset_id IS NULL AND dataset_version_id IS NULL AND row_index IS NULL "
        "AND parameter_snapshot IS NULL) OR "
        "(dataset_id IS NOT NULL AND dataset_version_id IS NOT NULL "
        "AND row_index IS NOT NULL AND row_index > 0 AND parameter_snapshot IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "case_runs_dataset_identity_consistent", "case_runs", type_="check"
    )
    op.drop_constraint(
        "uq_case_runs_run_dataset_row", "case_runs", type_="unique"
    )
    op.drop_index("ix_case_runs_dataset_version_id", table_name="case_runs")
    op.drop_index("ix_case_runs_dataset_id", table_name="case_runs")
    op.drop_constraint("fk_case_runs_dataset_version", "case_runs", type_="foreignkey")
    op.drop_constraint("fk_case_runs_dataset", "case_runs", type_="foreignkey")
    op.drop_column("case_runs", "parameter_snapshot")
    op.drop_column("case_runs", "row_index")
    op.drop_column("case_runs", "dataset_version_id")
    op.drop_column("case_runs", "dataset_id")
    op.create_unique_constraint(
        "uq_run_api_execution_results_message",
        "run_api_execution_results",
        ["message_id"],
    )
