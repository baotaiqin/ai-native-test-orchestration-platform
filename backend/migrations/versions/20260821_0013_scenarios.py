"""创建 Scenario 与不可变版本表。

Revision ID: 20260821_0013
Revises: 20260821_0012
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0013"
down_revision: str | None = "20260821_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_scenarios_project_code"),
    )
    op.create_index(op.f("ix_scenarios_project_id"), "scenarios", ["project_id"])
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("dsl", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_id", "version_no", name="uq_scenario_versions_scenario_version"
        ),
    )
    op.create_index(
        op.f("ix_scenario_versions_scenario_id"),
        "scenario_versions",
        ["scenario_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_scenario_versions_scenario_id"), table_name="scenario_versions"
    )
    op.drop_table("scenario_versions")
    op.drop_index(op.f("ix_scenarios_project_id"), table_name="scenarios")
    op.drop_table("scenarios")
