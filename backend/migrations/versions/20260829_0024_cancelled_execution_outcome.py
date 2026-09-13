"""允许 API_CASE 执行结果记录协作取消。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260829_0024"
down_revision: str | None = "20260829_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "run_api_execution_results"
_OUTCOME_CONSTRAINT = "run_api_execution_results_outcome_values"
_STATUS_CONSTRAINT = "run_api_execution_results_status_values"
_WITH_CANCELLED = "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR','TIMEOUT','CANCELLED')"
_WITHOUT_CANCELLED = "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR','TIMEOUT')"
_STATUS_WITH_CANCELLED = "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')"
_STATUS_WITHOUT_CANCELLED = "status IN ('SUCCESS','FAILED','TIMEOUT')"


def upgrade() -> None:
    op.drop_constraint(_OUTCOME_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_OUTCOME_CONSTRAINT, _TABLE, _WITH_CANCELLED)
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CONSTRAINT, _TABLE, _STATUS_WITH_CANCELLED)


def downgrade() -> None:
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CONSTRAINT, _TABLE, _STATUS_WITHOUT_CANCELLED)
    op.drop_constraint(_OUTCOME_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_OUTCOME_CONSTRAINT, _TABLE, _WITHOUT_CANCELLED)
