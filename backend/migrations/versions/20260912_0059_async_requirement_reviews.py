"""Make requirement reviews persistent asynchronous tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0059"
down_revision: str | None = "20260912_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requirement_reviews",
        sa.Column("prompt_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "requirement_reviews",
        sa.Column(
            "generation_status",
            sa.String(length=16),
            server_default="SUCCEEDED",
            nullable=False,
        ),
    )
    op.add_column(
        "requirement_reviews",
        sa.Column("error_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "requirement_reviews",
        sa.Column("started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "requirement_reviews",
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.alter_column(
        "requirement_reviews",
        "ai_call_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "requirement_reviews",
        "raw_response",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "requirement_reviews",
        "structured_result",
        existing_type=sa.JSON(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_requirement_reviews_prompt_id_prompt_definitions"),
        "requirement_reviews",
        "prompt_definitions",
        ["prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_requirement_reviews_generation_status_values"),
        "requirement_reviews",
        "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
    )
    op.execute(
        sa.text(
            "UPDATE requirement_reviews SET completed_at = updated_at "
            "WHERE generation_status = 'SUCCEEDED' AND completed_at IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM requirement_reviews "
            "WHERE ai_call_id IS NULL OR raw_response IS NULL OR structured_result IS NULL"
        )
    )
    op.drop_constraint(
        op.f("ck_requirement_reviews_generation_status_values"),
        "requirement_reviews",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_requirement_reviews_prompt_id_prompt_definitions"),
        "requirement_reviews",
        type_="foreignkey",
    )
    op.alter_column(
        "requirement_reviews",
        "structured_result",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.alter_column(
        "requirement_reviews",
        "raw_response",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "requirement_reviews",
        "ai_call_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("requirement_reviews", "completed_at")
    op.drop_column("requirement_reviews", "started_at")
    op.drop_column("requirement_reviews", "error_message")
    op.drop_column("requirement_reviews", "generation_status")
    op.drop_column("requirement_reviews", "prompt_id")
