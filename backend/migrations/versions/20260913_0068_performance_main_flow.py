from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0068"
down_revision: str | None = "20260913_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "performance_profiles",
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
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "case_version_id",
            sa.Integer(),
            sa.ForeignKey("test_case_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("warmup_iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_timeout_ms", sa.Integer(), nullable=False, server_default="30000"),
        sa.Column("sla", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "concurrency >= 1 AND concurrency <= 100", name="perf_profile_concurrency"
        ),
        sa.CheckConstraint(
            "iterations >= 1 AND iterations <= 10000", name="perf_profile_iterations"
        ),
        sa.CheckConstraint(
            "warmup_iterations >= 0 AND warmup_iterations <= 1000",
            name="perf_profile_warmup_iterations",
        ),
        sa.CheckConstraint(
            "request_timeout_ms >= 100 AND request_timeout_ms <= 120000",
            name="perf_profile_timeout",
        ),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="perf_profile_status"),
    )
    op.create_index("ix_performance_profiles_project_id", "performance_profiles", ["project_id"])
    op.create_index("ix_performance_profiles_case_id", "performance_profiles", ["case_id"])
    op.create_index(
        "ix_performance_profiles_project_created",
        "performance_profiles",
        ["project_id", "created_at"],
    )
    op.create_table(
        "performance_runs",
        sa.Column(
            "run_id", sa.String(128), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("performance_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATED"),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("sla_results", sa.JSON(), nullable=True),
        sa.Column("error_distribution", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('CREATED','QUEUED','ASSIGNED','RUNNING','SUCCESS','FAILED',"
            "'CANCELLED','TIMEOUT')",
            name="performance_runs_status",
        ),
    )
    op.create_index("ix_performance_runs_profile_id", "performance_runs", ["profile_id"])
    op.create_index("ix_performance_runs_project_id", "performance_runs", ["project_id"])
    op.create_index(
        "ix_performance_runs_profile_created", "performance_runs", ["profile_id", "created_at"]
    )
    op.create_index(
        "ix_performance_runs_project_created", "performance_runs", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("performance_runs")
    op.drop_table("performance_profiles")
