"""Make OpenAPI AI design suggestions persistent asynchronous tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0062"
down_revision: str | None = "20260912_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_design_suggestions", sa.Column("prompt_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "api_design_suggestions",
        sa.Column(
            "generation_status",
            sa.String(length=16),
            server_default="SUCCEEDED",
            nullable=False,
        ),
    )
    op.add_column(
        "api_design_suggestions",
        sa.Column("started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "api_design_suggestions",
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "api_design_suggestions",
        sa.Column("error_message", sa.String(length=500), nullable=True),
    )
    op.alter_column(
        "api_design_suggestions",
        "ai_call_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "api_design_suggestions",
        "structured_result",
        existing_type=sa.JSON(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_api_design_suggestions_prompt_id_prompt_definitions"),
        "api_design_suggestions",
        "prompt_definitions",
        ["prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_api_design_suggestions_generation_status_values"),
        "api_design_suggestions",
        "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
    )
    op.execute(
        sa.text(
            "UPDATE api_design_suggestions "
            "SET completed_at = COALESCE(reviewed_at, created_at) "
            "WHERE generation_status = 'SUCCEEDED' AND completed_at IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM api_design_suggestions "
            "WHERE ai_call_id IS NULL OR structured_result IS NULL"
        )
    )
    op.drop_constraint(
        op.f("ck_api_design_suggestions_generation_status_values"),
        "api_design_suggestions",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_api_design_suggestions_prompt_id_prompt_definitions"),
        "api_design_suggestions",
        type_="foreignkey",
    )
    op.alter_column(
        "api_design_suggestions",
        "structured_result",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.alter_column(
        "api_design_suggestions",
        "ai_call_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("api_design_suggestions", "error_message")
    op.drop_column("api_design_suggestions", "completed_at")
    op.drop_column("api_design_suggestions", "started_at")
    op.drop_column("api_design_suggestions", "generation_status")
    op.drop_column("api_design_suggestions", "prompt_id")
