"""创建 AI 调用审计日志表。

Revision ID: 20260820_0010
Revises: 20260820_0009
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0010"
down_revision: str | None = "20260820_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_call_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("model_config_id", sa.Integer(), nullable=False),
        sa.Column("actual_model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version_id", sa.Integer(), nullable=False),
        sa.Column("output_schema_id", sa.Integer(), nullable=True),
        sa.Column("input_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(16, 8), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repair_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("response_id", sa.String(length=128), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("repair_response", sa.Text(), nullable=True),
        sa.Column("parsed_result", sa.JSON(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_config_id"], ["model_configurations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["prompt_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["output_schema_id"], ["output_schemas.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_call_logs_project_id"), "ai_call_logs", ["project_id"])
    op.create_index(op.f("ix_ai_call_logs_task_type"), "ai_call_logs", ["task_type"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_call_logs_task_type"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_project_id"), table_name="ai_call_logs")
    op.drop_table("ai_call_logs")
