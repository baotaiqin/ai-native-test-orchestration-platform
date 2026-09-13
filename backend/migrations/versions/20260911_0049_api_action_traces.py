"""Persist bounded action-level API execution traces."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0049"
down_revision: str | None = "20260911_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_api_execution_results",
        sa.Column("action_traces", sa.JSON(), nullable=True),
    )
    results = sa.table(
        "run_api_execution_results",
        sa.column("action_traces", sa.JSON()),
    )
    op.execute(
        results.update()
        .where(results.c.action_traces.is_(None))
        .values(action_traces=[])
    )
    op.alter_column(
        "run_api_execution_results",
        "action_traces",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("run_api_execution_results", "action_traces")
