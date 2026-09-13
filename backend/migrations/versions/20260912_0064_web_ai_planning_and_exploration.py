"""Add AI Web planning, restricted MCP exploration, and reconciliation drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0064"
down_revision: str | None = "20260912_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_test_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requirement_document_version_id", sa.Integer(), nullable=False),
        sa.Column("api_import_id", sa.Integer(), nullable=True),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("generation_status", sa.String(16), server_default="QUEUED", nullable=False),
        sa.Column("active_key", sa.String(16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="ck_web_test_plans_generation_status",
        ),
        sa.CheckConstraint(
            "active_key IS NULL OR active_key = 'ACTIVE'",
            name="ck_web_test_plans_active_key",
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="ck_web_test_plans_snapshot_size",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="ck_web_test_plans_snapshot_sha",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requirement_document_version_id"],
            ["requirement_document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["api_import_id"], ["api_definition_imports.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompt_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_logs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_call_id", name="uq_web_test_plans_ai_call"),
        sa.UniqueConstraint("project_id", "active_key", name="uq_web_test_plans_active"),
    )
    for name, columns in (
        ("ix_web_test_plans_project_id", ["project_id"]),
        ("ix_web_test_plans_requirement_document_version_id", ["requirement_document_version_id"]),
        ("ix_web_test_plans_api_import_id", ["api_import_id"]),
        ("ix_web_test_plans_project_created", ["project_id", "created_at"]),
    ):
        op.create_index(name, "web_test_plans", columns)

    op.create_table(
        "web_test_plan_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.String(64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(8), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("requirement_ids", sa.JSON(), nullable=False),
        sa.Column("api_definition_ids", sa.JSON(), nullable=False),
        sa.Column("preconditions", sa.JSON(), nullable=False),
        sa.Column("planned_steps", sa.JSON(), nullable=False),
        sa.Column("expected_outcomes", sa.JSON(), nullable=False),
        sa.Column("start_url_hint", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["web_test_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "candidate_key", name="uq_web_plan_items_key"),
    )
    op.create_index("ix_web_test_plan_items_plan_id", "web_test_plan_items", ["plan_id"])
    op.create_index(
        "ix_web_plan_items_plan_order", "web_test_plan_items", ["plan_id", "order_index"]
    )

    op.create_table(
        "web_explorations",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("plan_item_id", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.String(64), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("session_profile_id", sa.Integer(), nullable=True),
        sa.Column("decision_prompt_id", sa.Integer(), nullable=False),
        sa.Column("start_url", sa.String(2048), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="CREATED", nullable=False),
        sa.Column("dispatch_status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("routing_key", sa.String(128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("claimed_runner_id", sa.String(64), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("action_trace", sa.JSON(), nullable=False),
        sa.Column("decision_ai_call_ids", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','STOP_REQUESTED',"
            "'COMPLETED','FAILED','CANCELLED')",
            name="ck_web_explorations_status",
        ),
        sa.CheckConstraint(
            "dispatch_status IN ('PENDING','PUBLISHED','FAILED')",
            name="ck_web_explorations_dispatch_status",
        ),
        sa.CheckConstraint(
            "max_steps >= 1 AND max_steps <= 50",
            name="ck_web_explorations_max_steps",
        ),
        sa.CheckConstraint(
            "current_step >= 0 AND current_step <= max_steps",
            name="ck_web_explorations_current_step",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="ck_web_explorations_attempt_count",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_item_id"], ["web_test_plan_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runner_id"], ["runners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_profile_id"], ["session_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_prompt_id"], ["prompt_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["claimed_runner_id"], ["runners.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_web_explorations_message_id"),
    )
    for name, columns in (
        ("ix_web_explorations_project_id", ["project_id"]),
        ("ix_web_explorations_plan_item_id", ["plan_item_id"]),
        ("ix_web_explorations_runner_id", ["runner_id"]),
        ("ix_web_explorations_environment_id", ["environment_id"]),
        ("ix_web_explorations_session_profile_id", ["session_profile_id"]),
        ("ix_web_explorations_item_created", ["plan_item_id", "created_at"]),
        ("ix_web_explorations_runner_status", ["runner_id", "status"]),
    ):
        op.create_index(name, "web_explorations", columns)

    op.add_column("web_recordings", sa.Column("plan_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_web_recordings_plan_item",
        "web_recordings",
        "web_test_plan_items",
        ["plan_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_web_recordings_plan_item_id", "web_recordings", ["plan_item_id"])

    op.create_table(
        "web_design_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("plan_item_id", sa.Integer(), nullable=False),
        sa.Column("exploration_id", sa.String(128), nullable=True),
        sa.Column("recording_id", sa.String(128), nullable=True),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("generation_status", sa.String(16), server_default="QUEUED", nullable=False),
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("structured_result", sa.JSON(), nullable=True),
        sa.Column("human_content", sa.JSON(), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
        sa.Column("created_web_case_id", sa.Integer(), nullable=True),
        sa.Column("created_web_case_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('MCP_EXPLORATION','MANUAL_RECORDING')",
            name="ck_web_design_revisions_source_type",
        ),
        sa.CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="ck_web_design_revisions_generation_status",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="ck_web_design_revisions_status",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="ck_web_design_revisions_snapshot_sha",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_item_id"], ["web_test_plan_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["exploration_id"], ["web_explorations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recording_id"], ["web_recordings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompt_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_logs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_web_case_id"], ["web_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_web_case_version_id"], ["web_case_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_call_id", name="uq_web_design_revisions_ai_call"),
    )
    for name, columns in (
        ("ix_web_design_revisions_project_id", ["project_id"]),
        ("ix_web_design_revisions_plan_item_id", ["plan_item_id"]),
        ("ix_web_design_revisions_exploration_id", ["exploration_id"]),
        ("ix_web_design_revisions_recording_id", ["recording_id"]),
        ("ix_web_design_revisions_item_created", ["plan_item_id", "created_at"]),
    ):
        op.create_index(name, "web_design_revisions", columns)


def downgrade() -> None:
    op.drop_table("web_design_revisions")
    op.drop_index("ix_web_recordings_plan_item_id", table_name="web_recordings")
    op.drop_constraint("fk_web_recordings_plan_item", "web_recordings", type_="foreignkey")
    op.drop_column("web_recordings", "plan_item_id")
    op.drop_table("web_explorations")
    op.drop_table("web_test_plan_items")
    op.drop_table("web_test_plans")
