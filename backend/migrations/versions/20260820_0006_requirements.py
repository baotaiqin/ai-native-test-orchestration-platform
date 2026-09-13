"""创建需求树与版本表。

Revision ID: 20260820_0006
Revises: 20260820_0005
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0006"
down_revision: str | None = "20260820_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False, server_default="FEATURE"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["requirements.id"],
            name=op.f("fk_requirements_parent_id_requirements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_requirements_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirements")),
        sa.UniqueConstraint("project_id", "code", name="uq_requirements_project_id_code"),
    )
    op.create_index(op.f("ix_requirements_project_id"), "requirements", ["project_id"])
    op.create_index(op.f("ix_requirements_parent_id"), "requirements", ["parent_id"])
    op.create_table(
        "requirement_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("change_summary", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_requirement_versions_requirement_id_requirements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_versions")),
        sa.UniqueConstraint(
            "requirement_id", "version_no", name="uq_requirement_versions_requirement_id_version_no"
        ),
    )
    op.create_index(
        op.f("ix_requirement_versions_requirement_id"),
        "requirement_versions",
        ["requirement_id"],
    )
    op.create_foreign_key(
        "fk_requirements_current_version_id_requirement_versions",
        "requirements",
        "requirement_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_requirements_current_version_id_requirement_versions",
        "requirements",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_requirement_versions_requirement_id"), table_name="requirement_versions"
    )
    op.drop_table("requirement_versions")
    op.drop_index(op.f("ix_requirements_parent_id"), table_name="requirements")
    op.drop_index(op.f("ix_requirements_project_id"), table_name="requirements")
    op.drop_table("requirements")
