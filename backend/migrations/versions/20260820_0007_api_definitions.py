"""创建 API 定义与导入记录表。

Revision ID: 20260820_0007
Revises: 20260820_0006
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0007"
down_revision: str | None = "20260820_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_definition_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("spec_version", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("imported_by", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_filename", "version_no",
            name="uq_api_imports_project_file_version",
        ),
    )
    op.create_index(
        op.f("ix_api_definition_imports_project_id"),
        "api_definition_imports", ["project_id"],
    )
    op.create_table(
        "api_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("request_schema", sa.JSON(), nullable=True),
        sa.Column("response_schema", sa.JSON(), nullable=False),
        sa.Column("auth_info", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="SWAGGER"),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["import_id"], ["api_definition_imports.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "method", "path",
            name="uq_api_definitions_project_method_path",
        ),
    )
    op.create_index(op.f("ix_api_definitions_project_id"), "api_definitions", ["project_id"])
    op.create_index(op.f("ix_api_definitions_import_id"), "api_definitions", ["import_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_api_definitions_import_id"), table_name="api_definitions")
    op.drop_index(op.f("ix_api_definitions_project_id"), table_name="api_definitions")
    op.drop_table("api_definitions")
    op.drop_index(
        op.f("ix_api_definition_imports_project_id"), table_name="api_definition_imports"
    )
    op.drop_table("api_definition_imports")
