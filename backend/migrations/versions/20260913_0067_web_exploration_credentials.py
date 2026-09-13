from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0067"
down_revision: str | None = "20260913_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_explorations",
        sa.Column(
            "use_login_credentials",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("web_explorations", "use_login_credentials")
