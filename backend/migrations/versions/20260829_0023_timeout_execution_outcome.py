"""允许 API_CASE 执行结果记录目标超时。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260829_0023"
down_revision: str | None = "20260828_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "run_api_execution_results"
_OUTCOME_CONSTRAINT = "run_api_execution_results_outcome_values"
_STATUS_CONSTRAINT = "run_api_execution_results_status_values"
_WITH_TIMEOUT = "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR','TIMEOUT')"
_WITHOUT_TIMEOUT = "outcome IN ('HTTP_RESPONSE','EXECUTION_ERROR')"
_STATUS_WITH_TIMEOUT = "status IN ('SUCCESS','FAILED','TIMEOUT')"
_STATUS_WITHOUT_TIMEOUT = "status IN ('SUCCESS','FAILED')"


def upgrade() -> None:
    op.drop_constraint(_OUTCOME_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_OUTCOME_CONSTRAINT, _TABLE, _WITH_TIMEOUT)
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CONSTRAINT, _TABLE, _STATUS_WITH_TIMEOUT)


def downgrade() -> None:
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CONSTRAINT, _TABLE, _STATUS_WITHOUT_TIMEOUT)
    op.drop_constraint(_OUTCOME_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_OUTCOME_CONSTRAINT, _TABLE, _WITHOUT_TIMEOUT)
