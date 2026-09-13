"""Persist secret-free Web session recovery audit summaries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0048"
down_revision: str | None = "20260911_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_web_execution_results",
        sa.Column("session_recovery", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_web_execution_results", "session_recovery")
