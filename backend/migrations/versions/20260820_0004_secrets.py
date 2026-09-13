"""创建 Secret 加密存储表。

Revision ID: 20260820_0004
Revises: 20260820_0003
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0004"
down_revision: str | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("secret_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_secrets_environment_id_environments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_secrets_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_secrets")),
        sa.UniqueConstraint("project_id", "name", name="uq_secrets_project_id_name"),
    )
    op.create_index(op.f("ix_secrets_project_id"), "secrets", ["project_id"])
    op.create_index(op.f("ix_secrets_environment_id"), "secrets", ["environment_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_secrets_environment_id"), table_name="secrets")
    op.drop_index(op.f("ix_secrets_project_id"), table_name="secrets")
    op.drop_table("secrets")
