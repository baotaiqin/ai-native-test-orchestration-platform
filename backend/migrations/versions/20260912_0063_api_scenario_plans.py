"""Add two-stage API scenario planning and candidate generation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0063"
down_revision: str | None = "20260912_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_scenario_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column(
            "generation_status",
            sa.String(length=16),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("active_key", sa.String(length=16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="ck_api_scenario_plans_generation_status",
        ),
        sa.CheckConstraint(
            "active_key IS NULL OR active_key = 'ACTIVE'",
            name="ck_api_scenario_plans_active_key",
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="ck_api_scenario_plans_snapshot_size",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="ck_api_scenario_plans_snapshot_sha",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["api_definition_imports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompt_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_logs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_call_id", name="uq_api_scenario_plans_ai_call"),
        sa.UniqueConstraint("import_id", "active_key", name="uq_api_scenario_plans_active"),
    )
    op.create_index("ix_api_scenario_plans_project_id", "api_scenario_plans", ["project_id"])
    op.create_index("ix_api_scenario_plans_import_id", "api_scenario_plans", ["import_id"])
    op.create_index(
        "ix_api_scenario_plans_import_created",
        "api_scenario_plans",
        ["import_id", "created_at"],
    )
    op.create_table(
        "api_scenario_plan_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.String(length=64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("requirement_ids", sa.JSON(), nullable=False),
        sa.Column("api_definition_ids", sa.JSON(), nullable=False),
        sa.Column("api_flow", sa.JSON(), nullable=False),
        sa.Column("preconditions", sa.JSON(), nullable=False),
        sa.Column("expected_outcomes", sa.JSON(), nullable=False),
        sa.Column("cleanup_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["api_scenario_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "candidate_key", name="uq_api_plan_items_key"),
    )
    op.create_index("ix_api_plan_items_plan_id", "api_scenario_plan_items", ["plan_id"])
    op.create_index(
        "ix_api_plan_items_plan_order",
        "api_scenario_plan_items",
        ["plan_id", "order_index"],
    )
    op.add_column(
        "api_design_suggestions",
        sa.Column("plan_item_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_suggestions_plan_item",
        "api_design_suggestions",
        "api_scenario_plan_items",
        ["plan_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_design_suggestions_plan_item_id",
        "api_design_suggestions",
        ["plan_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_design_suggestions_plan_item_id", table_name="api_design_suggestions")
    op.drop_constraint("fk_api_suggestions_plan_item", "api_design_suggestions", type_="foreignkey")
    op.drop_column("api_design_suggestions", "plan_item_id")
    op.drop_table("api_scenario_plan_items")
    op.drop_table("api_scenario_plans")
