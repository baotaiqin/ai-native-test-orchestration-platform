"""创建正式 Run、CaseRun 与 StepRun 持久化基础。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0017"
down_revision: str | None = "20260822_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("run_code", sa.String(length=64), nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("runner_id", sa.String(length=64), nullable=True),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_version_id", sa.Integer(), nullable=True),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("scenario_version_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("trigger_type", sa.String(length=16), nullable=False, server_default="MANUAL"),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("required_tags", sa.JSON(), nullable=False),
        sa.Column("required_slot_type", sa.String(length=16), nullable=False),
        sa.Column("required_slot_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("runtime_snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "run_type IN ('API_CASE','SCENARIO')", name="runs_run_type_values"
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','CANCELLING',"
            "'SUCCESS','FAILED','CANCELLED','TIMEOUT')",
            name="runs_status_values",
        ),
        sa.CheckConstraint(
            "trigger_type IN ('MANUAL','API','SYSTEM')", name="runs_trigger_type_values"
        ),
        sa.CheckConstraint(
            "required_slot_type IN ('API','WEB','PERFORMANCE')",
            name="runs_slot_type_values",
        ),
        sa.CheckConstraint("total >= 0", name="runs_total_nonnegative"),
        sa.CheckConstraint("pass_count >= 0", name="runs_pass_nonnegative"),
        sa.CheckConstraint("fail_count >= 0", name="runs_fail_nonnegative"),
        sa.CheckConstraint("review_count >= 0", name="runs_review_nonnegative"),
        sa.CheckConstraint("timeout_count >= 0", name="runs_timeout_nonnegative"),
        sa.CheckConstraint("required_slot_count > 0", name="runs_slot_count_positive"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_runs_environment_id_environments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_runs_runner_id_runners"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_runs_case_id_test_cases"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_version_id"],
            ["test_case_versions.id"],
            name=op.f("fk_runs_case_version_id_test_case_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_runs_scenario_id_scenarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"],
            ["scenario_versions.id"],
            name=op.f("fk_runs_scenario_version_id_scenario_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("run_code", name=op.f("uq_runs_run_code")),
    )
    op.create_index(op.f("ix_runs_project_id"), "runs", ["project_id"])
    op.create_index(op.f("ix_runs_environment_id"), "runs", ["environment_id"])
    op.create_index(op.f("ix_runs_runner_id"), "runs", ["runner_id"])
    op.create_index(op.f("ix_runs_case_id"), "runs", ["case_id"])
    op.create_index(op.f("ix_runs_scenario_id"), "runs", ["scenario_id"])
    op.create_index(
        "ix_runs_project_status_created", "runs", ["project_id", "status", "created_at"]
    )
    op.create_index("ix_runs_runner_status", "runs", ["runner_id", "status"])

    op.create_table(
        "case_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_version_id", sa.Integer(), nullable=True),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("scenario_version_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','ASSIGNED','RUNNING','CANCELLING','SUCCESS','FAILED',"
            "'REVIEW','CANCELLED','TIMEOUT','SKIPPED')",
            name="case_runs_status_values",
        ),
        sa.CheckConstraint("duration >= 0", name="case_runs_duration_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="case_runs_retry_nonnegative"),
        sa.CheckConstraint("sequence_no > 0", name="case_runs_sequence_positive"),
        sa.CheckConstraint(
            "(case_id IS NOT NULL AND scenario_id IS NULL) OR "
            "(case_id IS NULL AND scenario_id IS NOT NULL)",
            name="case_runs_target_exclusive",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_case_runs_run_id_runs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_case_runs_case_id_test_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_version_id"],
            ["test_case_versions.id"],
            name=op.f("fk_case_runs_case_version_id_test_case_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_case_runs_scenario_id_scenarios"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"],
            ["scenario_versions.id"],
            name=op.f("fk_case_runs_scenario_version_id_scenario_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_runs")),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_case_runs_run_sequence"),
    )
    op.create_index(op.f("ix_case_runs_run_id"), "case_runs", ["run_id"])
    op.create_index("ix_case_runs_run_status", "case_runs", ["run_id", "status"])

    op.create_table(
        "step_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("step_name", sa.String(length=255), nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','ASSIGNED','RUNNING','CANCELLING','SUCCESS','FAILED',"
            "'REVIEW','CANCELLED','TIMEOUT','SKIPPED')",
            name="step_runs_status_values",
        ),
        sa.CheckConstraint("duration >= 0", name="step_runs_duration_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="step_runs_retry_nonnegative"),
        sa.CheckConstraint("sequence_no > 0", name="step_runs_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name=op.f("fk_step_runs_case_run_id_case_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_step_runs")),
        sa.UniqueConstraint("case_run_id", "node_id", name="uq_step_runs_case_run_node"),
    )
    op.create_index(op.f("ix_step_runs_case_run_id"), "step_runs", ["case_run_id"])
    op.create_index("ix_step_runs_case_run_status", "step_runs", ["case_run_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_step_runs_case_run_status", table_name="step_runs")
    op.drop_index(op.f("ix_step_runs_case_run_id"), table_name="step_runs")
    op.drop_table("step_runs")
    op.drop_index("ix_case_runs_run_status", table_name="case_runs")
    op.drop_index(op.f("ix_case_runs_run_id"), table_name="case_runs")
    op.drop_table("case_runs")
    op.drop_index("ix_runs_runner_status", table_name="runs")
    op.drop_index("ix_runs_project_status_created", table_name="runs")
    op.drop_index(op.f("ix_runs_scenario_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_case_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_runner_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_environment_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_project_id"), table_name="runs")
    op.drop_table("runs")
