"""创建 MySQL 数据库连接配置表。

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0005"
down_revision: str | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "database_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="3306"),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_secret_id", sa.Integer(), nullable=False),
        sa.Column("ssl_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_database_connections_environment_id_environments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["password_secret_id"],
            ["secrets.id"],
            name=op.f("fk_database_connections_password_secret_id_secrets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_database_connections_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_database_connections")),
        sa.UniqueConstraint(
            "environment_id", "name", name="uq_database_connections_environment_id_name"
        ),
    )
    op.create_index(
        op.f("ix_database_connections_project_id"), "database_connections", ["project_id"]
    )
    op.create_index(
        op.f("ix_database_connections_environment_id"),
        "database_connections",
        ["environment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_database_connections_environment_id"), table_name="database_connections"
    )
    op.drop_index(
        op.f("ix_database_connections_project_id"), table_name="database_connections"
    )
    op.drop_table("database_connections")
