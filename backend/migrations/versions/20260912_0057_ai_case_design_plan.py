"""Persist the requirement-to-API AI test design plan."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0057"
down_revision: str | None = "20260912_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_case_generation_tasks",
        sa.Column("selected_api_definition_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ai_case_generation_tasks",
        sa.Column("coverage_plan", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ai_case_generations",
        sa.Column("selected_api_definition_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ai_case_generations",
        sa.Column("coverage_plan", sa.JSON(), nullable=True),
    )
    for table_name in ("ai_case_generation_tasks", "ai_case_generations"):
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET selected_api_definition_ids = :empty_list, "
                "coverage_plan = :empty_object"
            ).bindparams(empty_list="[]", empty_object="{}")
        )
        op.alter_column(
            table_name,
            "selected_api_definition_ids",
            existing_type=sa.JSON(),
            nullable=False,
        )
        op.alter_column(
            table_name,
            "coverage_plan",
            existing_type=sa.JSON(),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_column("ai_case_generations", "coverage_plan")
    op.drop_column("ai_case_generations", "selected_api_definition_ids")
    op.drop_column("ai_case_generation_tasks", "coverage_plan")
    op.drop_column("ai_case_generation_tasks", "selected_api_definition_ids")
