"""创建 AI Requirement Review 人工治理表。

Revision ID: 20260820_0011
Revises: 20260820_0010
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0011"
down_revision: str | None = "20260820_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirement_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("human_result", sa.JSON(), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
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
        op.f("ix_requirement_reviews_project_id"), "requirement_reviews", ["project_id"]
    )
    op.create_index(
        op.f("ix_requirement_reviews_requirement_id"),
        "requirement_reviews", ["requirement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_requirement_reviews_requirement_id"), table_name="requirement_reviews"
    )
    op.drop_index(
        op.f("ix_requirement_reviews_project_id"), table_name="requirement_reviews"
    )
    op.drop_table("requirement_reviews")
