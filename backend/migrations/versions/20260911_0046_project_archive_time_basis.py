"""Mark UTC project archive timestamps without relabeling legacy local values."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0046"
down_revision: str | None = "20260911_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("archived_at_basis", sa.String(length=24), nullable=True),
    )
    projects = sa.table(
        "projects",
        sa.column("archived_at", sa.DateTime()),
        sa.column("archived_at_basis", sa.String(length=24)),
    )
    op.execute(
        projects.update()
        .where(projects.c.archived_at.is_not(None))
        .values(archived_at_basis="LEGACY_LOCAL_UNKNOWN")
    )
    op.create_check_constraint(
        "projects_archived_at_basis_values",
        "projects",
        "archived_at_basis IN ('UTC','LEGACY_LOCAL_UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "projects_archived_at_basis_values", "projects", type_="check"
    )
    op.drop_column("projects", "archived_at_basis")
