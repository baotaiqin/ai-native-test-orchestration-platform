"""Run 级总超时与 Force Stop 标记。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0026"
down_revision: str | None = "20260829_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runs"
_TIMEOUT_CONSTRAINT = "runs_total_timeout_ms_bounds"
_FORCE_STOPPED_CONSTRAINT = "runs_force_stopped_values"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("total_timeout_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        _TIMEOUT_CONSTRAINT,
        _TABLE,
        "total_timeout_ms IS NULL OR total_timeout_ms >= 1000",
    )
    op.add_column(
        _TABLE, sa.Column("force_stop_requested_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "force_stopped",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        _FORCE_STOPPED_CONSTRAINT,
        _TABLE,
        "force_stopped IN (0, 1)",
    )


def downgrade() -> None:
    op.drop_constraint(_FORCE_STOPPED_CONSTRAINT, _TABLE, type_="check")
    op.drop_column(_TABLE, "force_stopped")
    op.drop_column(_TABLE, "force_stop_requested_at")
    op.drop_constraint(_TIMEOUT_CONSTRAINT, _TABLE, type_="check")
    op.drop_column(_TABLE, "total_timeout_ms")
