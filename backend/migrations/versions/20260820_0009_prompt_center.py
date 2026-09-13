"""创建 Output Schema、Prompt 与版本表。

Revision ID: 20260820_0009
Revises: 20260820_0008
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0009"
down_revision: str | None = "20260820_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "output_schemas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version_no", name="uq_output_schemas_name_version"),
    )
    op.create_table(
        "prompt_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        op.f("ix_prompt_definitions_task_type"), "prompt_definitions", ["task_type"]
    )
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=False),
        sa.Column("output_schema_id", sa.Integer(), nullable=True),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompt_definitions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["output_schema_id"], ["output_schemas.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prompt_id", "version_no", name="uq_prompt_versions_prompt_version"
        ),
    )
    op.create_index(
        op.f("ix_prompt_versions_prompt_id"), "prompt_versions", ["prompt_id"]
    )
    op.create_foreign_key(
        "fk_prompt_definitions_current_version_id_prompt_versions",
        "prompt_definitions", "prompt_versions",
        ["current_version_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_prompt_definitions_current_version_id_prompt_versions",
        "prompt_definitions", type_="foreignkey",
    )
    op.drop_index(op.f("ix_prompt_versions_prompt_id"), table_name="prompt_versions")
    op.drop_table("prompt_versions")
    op.drop_index(
        op.f("ix_prompt_definitions_task_type"), table_name="prompt_definitions"
    )
    op.drop_table("prompt_definitions")
    op.drop_table("output_schemas")
