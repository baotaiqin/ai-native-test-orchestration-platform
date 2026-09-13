"""Persist bounded Web AI semantic assertion results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0050"
down_revision: str | None = "20260911_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_web_execution_results",
        sa.Column("assertion_results", sa.JSON(), nullable=True),
    )
    results = sa.table(
        "run_web_execution_results",
        sa.column("assertion_results", sa.JSON()),
    )
    op.execute(
        results.update()
        .where(results.c.assertion_results.is_(None))
        .values(assertion_results=[])
    )
    op.alter_column(
        "run_web_execution_results",
        "assertion_results",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("run_web_execution_results", "assertion_results")
