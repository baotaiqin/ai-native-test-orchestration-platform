"""Add saved offline preview fixtures for Scenario versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0061"
down_revision: str | None = "20260912_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_preview_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scenario_version_id", sa.Integer(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("responses_by_node", sa.JSON(), nullable=False),
        sa.Column("cleanup_outcome", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "cleanup_outcome IN ('SUCCESS','FAILURE','CANCELLED','TIMEOUT')",
            name=op.f("ck_scenario_preview_profiles_cleanup_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"],
            ["scenario_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_preview_profiles")),
        sa.UniqueConstraint(
            "scenario_version_id", name="uq_scenario_preview_profiles_version"
        ),
    )
    op.create_index(
        op.f("ix_scenario_preview_profiles_scenario_version_id"),
        "scenario_preview_profiles",
        ["scenario_version_id"],
    )


def downgrade() -> None:
    op.drop_table("scenario_preview_profiles")
