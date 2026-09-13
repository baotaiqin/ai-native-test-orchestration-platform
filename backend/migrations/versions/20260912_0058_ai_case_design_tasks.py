"""Add asynchronous AI test-design planning tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0058"
down_revision: str | None = "20260912_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_case_design_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=True),
        sa.Column("api_contract_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("included_api_definition_ids", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name=op.f("ck_ai_case_design_tasks_status_values"),
        ),
        sa.CheckConstraint(
            "source IS NULL OR source IN ('AI','RULE_FALLBACK')",
            name=op.f("ck_ai_case_design_tasks_source_values"),
        ),
        sa.ForeignKeyConstraint(
            ["ai_call_id"], ["ai_call_logs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompt_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_case_design_tasks")),
        sa.UniqueConstraint("ai_call_id", name=op.f("uq_ai_case_design_tasks_ai_call_id")),
    )
    op.create_index(
        op.f("ix_ai_case_design_tasks_project_id"),
        "ai_case_design_tasks",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_case_design_tasks_requirement_id"),
        "ai_case_design_tasks",
        ["requirement_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_case_design_tasks_requirement_created",
        "ai_case_design_tasks",
        ["requirement_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_case_design_tasks_requirement_created",
        table_name="ai_case_design_tasks",
    )
    op.drop_index(
        op.f("ix_ai_case_design_tasks_requirement_id"),
        table_name="ai_case_design_tasks",
    )
    op.drop_index(
        op.f("ix_ai_case_design_tasks_project_id"),
        table_name="ai_case_design_tasks",
    )
    op.drop_table("ai_case_design_tasks")
