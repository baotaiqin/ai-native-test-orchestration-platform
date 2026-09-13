from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0072"
down_revision: str | None = "20260913_0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "runtime_variables",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("('{}')"),
        ),
    )
    op.create_table(
        "test_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "environment_id",
            sa.Integer(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "runner_id",
            sa.String(64),
            sa.ForeignKey("runners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("runtime_variables", sa.JSON(), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default="PARALLEL"),
        sa.Column("failure_strategy", sa.String(16), nullable=False, server_default="CONTINUE"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="test_plans_status"),
        sa.CheckConstraint("execution_mode = 'PARALLEL'", name="test_plans_execution_mode"),
        sa.CheckConstraint("failure_strategy = 'CONTINUE'", name="test_plans_failure_strategy"),
    )
    op.create_index("ix_test_plans_project_id", "test_plans", ["project_id"])
    op.create_index("ix_test_plans_environment_id", "test_plans", ["environment_id"])
    op.create_index("ix_test_plans_runner_id", "test_plans", ["runner_id"])
    op.create_index(
        "ix_test_plans_project_created", "test_plans", ["project_id", "created_at"]
    )
    op.create_table(
        "test_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("test_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_name_snapshot", sa.String(255), nullable=False),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "case_version_id",
            sa.Integer(),
            sa.ForeignKey("test_case_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "scenario_version_id",
            sa.Integer(),
            sa.ForeignKey("scenario_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "web_case_id",
            sa.Integer(),
            sa.ForeignKey("web_cases.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "web_case_version_id",
            sa.Integer(),
            sa.ForeignKey("web_case_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("plan_id", "sequence_no", name="uq_test_plan_items_plan_sequence"),
        sa.CheckConstraint("sequence_no > 0", name="test_plan_items_sequence_positive"),
        sa.CheckConstraint(
            "target_type IN ('API_CASE','SCENARIO','WEB_CASE')",
            name="test_plan_items_target_type",
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name="test_plan_items_enabled"),
        sa.CheckConstraint(
            "(target_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
            "AND scenario_id IS NULL AND scenario_version_id IS NULL AND web_case_id IS NULL "
            "AND web_case_version_id IS NULL) OR "
            "(target_type = 'SCENARIO' AND scenario_id IS NOT NULL "
            "AND scenario_version_id IS NOT NULL "
            "AND case_id IS NULL AND case_version_id IS NULL AND web_case_id IS NULL "
            "AND web_case_version_id IS NULL) OR "
            "(target_type = 'WEB_CASE' AND web_case_id IS NOT NULL "
            "AND web_case_version_id IS NOT NULL AND case_id IS NULL AND case_version_id IS NULL "
            "AND scenario_id IS NULL AND scenario_version_id IS NULL)",
            name="test_plan_items_target_ref",
        ),
    )
    op.create_index("ix_test_plan_items_plan_id", "test_plan_items", ["plan_id"])
    op.create_table(
        "test_plan_runs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("test_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATED"),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED','TIMEOUT')",
            name="test_plan_runs_status",
        ),
    )
    op.create_index("ix_test_plan_runs_plan_id", "test_plan_runs", ["plan_id"])
    op.create_index("ix_test_plan_runs_project_id", "test_plan_runs", ["project_id"])
    op.create_index(
        "ix_test_plan_runs_project_created", "test_plan_runs", ["project_id", "created_at"]
    )
    op.create_index(
        "ix_test_plan_runs_plan_created", "test_plan_runs", ["plan_id", "created_at"]
    )
    op.create_table(
        "test_plan_run_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_run_id",
            sa.String(128),
            sa.ForeignKey("test_plan_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_item_id",
            sa.Integer(),
            sa.ForeignKey("test_plan_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_name_snapshot", sa.String(255), nullable=False),
        sa.Column(
            "run_id",
            sa.String(128),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATED"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "plan_run_id", "sequence_no", name="uq_test_plan_run_items_run_sequence"
        ),
        sa.UniqueConstraint("run_id", name="uq_test_plan_run_items_run_id"),
        sa.CheckConstraint("sequence_no > 0", name="test_plan_run_items_sequence_positive"),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','CANCELLING','SUCCESS',"
            "'FAILED','CANCELLED','TIMEOUT')",
            name="test_plan_run_items_status",
        ),
    )
    op.create_index("ix_test_plan_run_items_plan_run_id", "test_plan_run_items", ["plan_run_id"])
    op.create_index("ix_test_plan_run_items_run_id", "test_plan_run_items", ["run_id"])


def downgrade() -> None:
    op.drop_table("test_plan_run_items")
    op.drop_table("test_plan_runs")
    op.drop_table("test_plan_items")
    op.drop_table("test_plans")
    op.drop_column("runs", "runtime_variables")
