"""Store provider catalog metadata on model configurations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0037"
down_revision: str | None = "20260908_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_configurations"
_CHECKS = (
    "ck_model_configurations_max_input_tokens_nonnegative",
    "ck_model_configurations_max_output_tokens_nonnegative",
    "ck_model_configurations_max_reasoning_tokens_nonnegative",
    "ck_model_configurations_reasoning_max_input_tokens_nonnegative",
    "ck_model_configurations_reasoning_max_output_tokens_nonnegative",
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("metadata_source", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("metadata_synced_at", sa.DateTime(), nullable=True))
    op.add_column(_TABLE, sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("supports_reasoning", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(_TABLE, sa.Column("max_input_tokens", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("max_reasoning_tokens", sa.Integer(), nullable=True))
    op.add_column(
        _TABLE, sa.Column("reasoning_max_input_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("reasoning_max_output_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("input_modalities", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("output_modalities", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("capabilities", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("features", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("pricing_tiers", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("published_at", sa.DateTime(), nullable=True))
    op.alter_column(
        _TABLE,
        "max_context",
        existing_type=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("128000"),
        nullable=True,
        server_default=None,
    )
    for name, column in zip(
        _CHECKS,
        (
            "max_input_tokens",
            "max_output_tokens",
            "max_reasoning_tokens",
            "reasoning_max_input_tokens",
            "reasoning_max_output_tokens",
        ),
        strict=True,
    ):
        op.create_check_constraint(
            name,
            _TABLE,
            f"{column} IS NULL OR {column} >= 0",
        )
    op.alter_column(_TABLE, "supports_reasoning", server_default=None)


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE model_configurations SET max_context = 128000 "
            "WHERE max_context IS NULL"
        )
    )
    for name in _CHECKS:
        op.drop_constraint(name, _TABLE, type_="check")
    op.alter_column(
        _TABLE,
        "max_context",
        existing_type=sa.Integer(),
        existing_nullable=True,
        existing_server_default=None,
        nullable=False,
        server_default=sa.text("128000"),
    )
    for column in (
        "published_at",
        "pricing_tiers",
        "features",
        "capabilities",
        "output_modalities",
        "input_modalities",
        "reasoning_max_output_tokens",
        "reasoning_max_input_tokens",
        "max_reasoning_tokens",
        "max_output_tokens",
        "max_input_tokens",
        "supports_reasoning",
        "description",
        "metadata_synced_at",
        "metadata_source",
    ):
        op.drop_column(_TABLE, column)
