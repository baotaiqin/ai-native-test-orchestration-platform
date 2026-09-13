from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0074"
down_revision: str | None = "20260913_0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_explorations",
        sa.Column(
            "permissions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("('[\"PAGE_READ\"]')"),
        ),
    )


def downgrade() -> None:
    op.drop_column("web_explorations", "permissions")
