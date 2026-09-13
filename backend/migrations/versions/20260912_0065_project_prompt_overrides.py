"""Add system prompt templates and project-specific prompt overrides."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0065"
down_revision: str | None = "20260912_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prompt_definitions",
        sa.Column("scope", sa.String(length=16), server_default="SYSTEM", nullable=False),
    )
    op.add_column(
        "prompt_definitions",
        sa.Column("project_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prompt_definitions",
        sa.Column("base_prompt_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prompt_definitions",
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        "prompt_definitions_scope_values",
        "prompt_definitions",
        "scope IN ('SYSTEM','PROJECT')",
    )
    op.create_foreign_key(
        "fk_prompt_definitions_project",
        "prompt_definitions",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_prompt_definitions_base",
        "prompt_definitions",
        "prompt_definitions",
        ["base_prompt_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_prompt_definitions_project_base",
        "prompt_definitions",
        ["project_id", "base_prompt_id"],
    )
    op.create_index(
        "ix_prompt_definitions_project_id",
        "prompt_definitions",
        ["project_id"],
    )
    op.create_index(
        "ix_prompt_definitions_base_prompt_id",
        "prompt_definitions",
        ["base_prompt_id"],
    )
    op.execute(
        sa.text(
            "UPDATE prompt_definitions "
            "SET is_builtin = true, "
            "name = REPLACE(name, '[内置 Demo] ', '[系统默认] ') "
            "WHERE code LIKE 'DEMO_%'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE output_schemas "
            "SET name = REPLACE(name, '[内置 Demo] ', '[系统默认] ') "
            "WHERE name LIKE '[内置 Demo] %'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_definitions_base_prompt_id", table_name="prompt_definitions")
    op.drop_index("ix_prompt_definitions_project_id", table_name="prompt_definitions")
    op.drop_constraint(
        "uq_prompt_definitions_project_base", "prompt_definitions", type_="unique"
    )
    op.drop_constraint("fk_prompt_definitions_base", "prompt_definitions", type_="foreignkey")
    op.drop_constraint("fk_prompt_definitions_project", "prompt_definitions", type_="foreignkey")
    op.drop_constraint(
        "prompt_definitions_scope_values", "prompt_definitions", type_="check"
    )
    op.drop_column("prompt_definitions", "is_builtin")
    op.drop_column("prompt_definitions", "base_prompt_id")
    op.drop_column("prompt_definitions", "project_id")
    op.drop_column("prompt_definitions", "scope")
