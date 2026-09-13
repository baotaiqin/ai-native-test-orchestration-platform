from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0073"
down_revision: str | None = "20260913_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("test_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schedule_type", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("daily_time", sa.String(5), nullable=True),
        sa.Column("weekdays", sa.JSON(), nullable=True),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("last_trigger_status", sa.String(16), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.String(1000), nullable=True),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "schedule_type IN ('DAILY','WEEKLY','CRON')", name="test_schedules_type"
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name="test_schedules_enabled"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="test_schedules_status"),
    )
    op.create_index("ix_test_schedules_project_id", "test_schedules", ["project_id"])
    op.create_index("ix_test_schedules_plan_id", "test_schedules", ["plan_id"])
    op.create_index("ix_test_schedules_due", "test_schedules", ["status", "enabled", "next_run_at"])
    op.create_index(
        "ix_test_schedules_project_created", "test_schedules", ["project_id", "created_at"]
    )

    op.add_column(
        "test_plan_runs",
        sa.Column("trigger_type", sa.String(16), nullable=False, server_default="MANUAL"),
    )
    op.add_column("test_plan_runs", sa.Column("schedule_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_test_plan_runs_schedule_id_test_schedules",
        "test_plan_runs",
        "test_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_test_plan_runs_schedule_id", "test_plan_runs", ["schedule_id"])
    op.create_check_constraint(
        "test_plan_runs_trigger_type",
        "test_plan_runs",
        "trigger_type IN ('MANUAL','SCHEDULE','API')",
    )

    op.create_table(
        "schedule_triggers",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("test_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_for_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="CLAIMED"),
        sa.Column(
            "plan_run_id",
            sa.String(128),
            sa.ForeignKey("test_plan_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "schedule_id", "scheduled_for_at", name="uq_schedule_triggers_occurrence"
        ),
        sa.CheckConstraint(
            "status IN ('CLAIMED','DISPATCHED','FAILED','SKIPPED')", name="schedule_triggers_status"
        ),
    )
    op.create_index("ix_schedule_triggers_schedule_id", "schedule_triggers", ["schedule_id"])
    op.create_index("ix_schedule_triggers_project_id", "schedule_triggers", ["project_id"])
    op.create_index("ix_schedule_triggers_plan_run_id", "schedule_triggers", ["plan_run_id"])
    op.create_index(
        "ix_schedule_triggers_project_created", "schedule_triggers", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("schedule_triggers")
    op.drop_constraint("test_plan_runs_trigger_type", "test_plan_runs", type_="check")
    op.drop_index("ix_test_plan_runs_schedule_id", table_name="test_plan_runs")
    op.drop_constraint(
        "fk_test_plan_runs_schedule_id_test_schedules", "test_plan_runs", type_="foreignkey"
    )
    op.drop_column("test_plan_runs", "schedule_id")
    op.drop_column("test_plan_runs", "trigger_type")
    op.drop_table("test_schedules")
