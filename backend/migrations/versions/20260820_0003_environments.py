"""创建环境与环境变量表。

Revision ID: 20260820_0003
Revises: 20260820_0002
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0003"
down_revision: str | None = "20260820_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_environments_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
        sa.UniqueConstraint("project_id", "code", name="uq_environments_project_id_code"),
    )
    op.create_index(op.f("ix_environments_project_id"), "environments", ["project_id"])
    op.create_table(
        "environment_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(length=32), nullable=False, server_default="STRING"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_environment_variables_environment_id_environments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environment_variables")),
        sa.UniqueConstraint(
            "environment_id", "key", name="uq_environment_variables_environment_id_key"
        ),
    )
    op.create_index(
        op.f("ix_environment_variables_environment_id"),
        "environment_variables",
        ["environment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_environment_variables_environment_id"), table_name="environment_variables"
    )
    op.drop_table("environment_variables")
    op.drop_index(op.f("ix_environments_project_id"), table_name="environments")
    op.drop_table("environments")
