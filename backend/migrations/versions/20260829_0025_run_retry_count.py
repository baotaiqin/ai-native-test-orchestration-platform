"""记录 API_CASE 执行使用的重试次数。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0025"
down_revision: str | None = "20260829_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "run_api_execution_results"
_CONSTRAINT = "run_api_execution_results_retry_count_bounds"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "retry_count >= 0 AND retry_count <= 3",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.drop_column(_TABLE, "retry_count")
