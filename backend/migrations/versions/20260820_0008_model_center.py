"""创建模型中心与项目模型绑定表。

Revision ID: 20260820_0008
Revises: 20260820_0007
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0008"
down_revision: str | None = "20260820_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_configurations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_secret_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_type", sa.String(length=32), nullable=False),
        sa.Column("supports_tool_call", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "supports_structured_output", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("max_context", sa.Integer(), nullable=False, server_default="128000"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("input_price", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("output_price", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["api_key_secret_id"], ["secrets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        op.f("ix_model_configurations_api_key_secret_id"),
        "model_configurations", ["api_key_secret_id"],
    )
    op.create_table(
        "project_model_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("primary_model_id", sa.Integer(), nullable=False),
        sa.Column("fallback_model_id", sa.Integer(), nullable=True),
        sa.Column("max_fallback", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["primary_model_id"], ["model_configurations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fallback_model_id"], ["model_configurations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "task_type", name="uq_project_model_bindings_project_task"
        ),
    )
    op.create_index(
        op.f("ix_project_model_bindings_project_id"),
        "project_model_bindings", ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_project_model_bindings_project_id"),
        table_name="project_model_bindings",
    )
    op.drop_table("project_model_bindings")
    op.drop_index(
        op.f("ix_model_configurations_api_key_secret_id"),
        table_name="model_configurations",
    )
    op.drop_table("model_configurations")
