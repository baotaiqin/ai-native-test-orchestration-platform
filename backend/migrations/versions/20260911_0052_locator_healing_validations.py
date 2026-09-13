"""Add auditable temporary Run validation for locator healing candidates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0052"
down_revision: str | None = "20260911_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "locator_healing_validations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["locator_healing_proposals.id"],
            name=op.f(f"fk_{_TABLE}_proposal_id_locator_healing_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f(f"fk_{_TABLE}_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.UniqueConstraint("run_id", name=f"uq_{_TABLE}_run"),
    )
    op.create_index(f"ix_{_TABLE}_proposal_id", _TABLE, ["proposal_id"])
    op.create_index(f"ix_{_TABLE}_run_id", _TABLE, ["run_id"])
    op.create_index(
        f"ix_{_TABLE}_proposal_created",
        _TABLE,
        ["proposal_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
