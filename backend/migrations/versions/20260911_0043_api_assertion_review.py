"""Allow auditable REVIEW results for formal API AI assertions."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260911_0043"
down_revision: str | None = "20260911_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "run_api_execution_results_status_values",
        "run_api_execution_results",
        type_="check",
    )
    op.create_check_constraint(
        "run_api_execution_results_status_values",
        "run_api_execution_results",
        "status IN ('SUCCESS','FAILED','REVIEW','TIMEOUT','CANCELLED')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE run_api_execution_results SET status = 'FAILED' "
        "WHERE status = 'REVIEW'"
    )
    op.drop_constraint(
        "run_api_execution_results_status_values",
        "run_api_execution_results",
        type_="check",
    )
    op.create_check_constraint(
        "run_api_execution_results_status_values",
        "run_api_execution_results",
        "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
    )
