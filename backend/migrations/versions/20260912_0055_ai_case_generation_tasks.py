"""Add persistent asynchronous AI case generation tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0055"
down_revision: str | None = "20260911_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ai_case_generation_tasks"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name=op.f("ck_ai_case_generation_tasks_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_ai_case_gen_tasks_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"],
            name="fk_ai_case_gen_tasks_requirement",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"], ["requirement_versions.id"],
            name="fk_ai_case_gen_tasks_req_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompt_definitions.id"],
            name="fk_ai_case_gen_tasks_prompt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ai_case_generations.id"],
            name="fk_ai_case_gen_tasks_generation",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_case_generation_tasks")),
        sa.UniqueConstraint(
            "generation_id", name=op.f("uq_ai_case_generation_tasks_generation_id")
        ),
    )
    op.create_index(op.f("ix_ai_case_generation_tasks_project_id"), _TABLE, ["project_id"])
    op.create_index(
        op.f("ix_ai_case_generation_tasks_requirement_id"), _TABLE, ["requirement_id"]
    )
    op.create_index(
        "ix_ai_case_generation_tasks_requirement_created",
        _TABLE,
        ["requirement_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
