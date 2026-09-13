"""创建 Test Case、AI Case 建议与需求关联表。

Revision ID: 20260821_0012
Revises: 20260820_0011
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0012"
down_revision: str | None = "20260820_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_test_cases_project_code"),
    )
    op.create_index(op.f("ix_test_cases_project_id"), "test_cases", ["project_id"])
    op.create_table(
        "test_case_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "version_no", name="uq_test_case_versions_case_version"),
    )
    op.create_index(
        op.f("ix_test_case_versions_case_id"), "test_case_versions", ["case_id"]
    )
    op.create_foreign_key(
        "fk_test_cases_current_version_id_test_case_versions",
        "test_cases", "test_case_versions", ["current_version_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "ai_case_generations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"], ["requirement_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_logs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_call_id"),
    )
    op.create_index(
        op.f("ix_ai_case_generations_project_id"), "ai_case_generations", ["project_id"]
    )
    op.create_index(
        op.f("ix_ai_case_generations_requirement_id"),
        "ai_case_generations", ["requirement_id"],
    )
    op.create_table(
        "ai_case_suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("human_result", sa.JSON(), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("test_case_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ai_case_generations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_id", "sequence_no",
            name="uq_ai_case_suggestions_generation_sequence",
        ),
    )
    op.create_index(
        op.f("ix_ai_case_suggestions_generation_id"),
        "ai_case_suggestions", ["generation_id"],
    )
    op.create_table(
        "requirement_case_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=True),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"], ["requirement_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requirement_id", "case_id", "relation_type",
            name="uq_requirement_case_links_requirement_case_relation",
        ),
    )
    op.create_index(
        op.f("ix_requirement_case_links_requirement_id"),
        "requirement_case_links", ["requirement_id"],
    )
    op.create_index(
        op.f("ix_requirement_case_links_case_id"), "requirement_case_links", ["case_id"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_requirement_case_links_case_id"), table_name="requirement_case_links"
    )
    op.drop_index(
        op.f("ix_requirement_case_links_requirement_id"),
        table_name="requirement_case_links",
    )
    op.drop_table("requirement_case_links")
    op.drop_index(
        op.f("ix_ai_case_suggestions_generation_id"), table_name="ai_case_suggestions"
    )
    op.drop_table("ai_case_suggestions")
    op.drop_index(
        op.f("ix_ai_case_generations_requirement_id"), table_name="ai_case_generations"
    )
    op.drop_index(
        op.f("ix_ai_case_generations_project_id"), table_name="ai_case_generations"
    )
    op.drop_table("ai_case_generations")
    op.drop_constraint(
        "fk_test_cases_current_version_id_test_case_versions",
        "test_cases", type_="foreignkey",
    )
    op.drop_index(op.f("ix_test_case_versions_case_id"), table_name="test_case_versions")
    op.drop_table("test_case_versions")
    op.drop_index(op.f("ix_test_cases_project_id"), table_name="test_cases")
    op.drop_table("test_cases")
