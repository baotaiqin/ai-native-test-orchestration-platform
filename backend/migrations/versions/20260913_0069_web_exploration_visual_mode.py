from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0069"
down_revision: str | None = "20260913_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_explorations",
        sa.Column("headless", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("web_explorations", "headless")
