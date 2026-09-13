from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0071"
down_revision: str | None = "20260913_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("perf_profile_load_config", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_load_mode", "performance_profiles", type_="check")
    op.alter_column("performance_profiles", "case_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "performance_profiles", "case_version_id", existing_type=sa.Integer(), nullable=True
    )
    op.add_column(
        "performance_profiles",
        sa.Column("target_type", sa.String(16), nullable=False, server_default="API_CASE"),
    )
    op.add_column(
        "performance_profiles",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "performance_profiles",
        sa.Column(
            "scenario_version_id",
            sa.Integer(),
            sa.ForeignKey("scenario_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("performance_profiles", sa.Column("step_stages", sa.JSON(), nullable=True))
    op.add_column(
        "performance_profiles",
        sa.Column("engine", sa.String(24), nullable=False, server_default="PYTHON_HTTP"),
    )
    op.add_column(
        "performance_profiles",
        sa.Column("cleanup_mode", sa.String(24), nullable=False, server_default="NONE"),
    )
    op.add_column(
        "performance_profiles",
        sa.Column("stream_config", sa.JSON(), nullable=False, server_default=sa.text("('{}')")),
    )
    op.create_index("ix_performance_profiles_scenario_id", "performance_profiles", ["scenario_id"])
    op.create_check_constraint(
        "perf_profile_load_mode",
        "performance_profiles",
        "load_mode IN ('FIXED_ITERATIONS','FIXED_RPS','FIXED_CONCURRENCY','STEP_LOAD')",
    )
    op.create_check_constraint(
        "perf_profile_load_config",
        "performance_profiles",
        "(load_mode = 'FIXED_ITERATIONS' AND target_rps IS NULL AND duration_seconds IS NULL) "
        "OR (load_mode = 'FIXED_RPS' AND target_rps > 0 AND duration_seconds >= 1 "
        "AND duration_seconds <= 3600) OR (load_mode = 'FIXED_CONCURRENCY' "
        "AND target_rps IS NULL AND duration_seconds >= 1 AND duration_seconds <= 3600) "
        "OR (load_mode = 'STEP_LOAD' AND target_rps IS NULL AND duration_seconds IS NULL)",
    )
    op.create_check_constraint(
        "perf_profile_target_type",
        "performance_profiles",
        "target_type IN ('API_CASE','SCENARIO')",
    )
    op.create_check_constraint(
        "perf_profile_target_ref",
        "performance_profiles",
        "(target_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
        "AND scenario_id IS NULL AND scenario_version_id IS NULL) OR "
        "(target_type = 'SCENARIO' AND case_id IS NULL AND case_version_id IS NULL "
        "AND scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "perf_profile_engine",
        "performance_profiles",
        "engine IN ('PYTHON_HTTP','PYTHON_STREAM','JMETER')",
    )
    op.create_check_constraint(
        "perf_profile_cleanup_mode",
        "performance_profiles",
        "cleanup_mode IN ('NONE','RESOURCE_CLEANUP','BATCH_CLEANUP')",
    )


def downgrade() -> None:
    op.drop_constraint("perf_profile_cleanup_mode", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_engine", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_target_ref", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_target_type", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_load_config", "performance_profiles", type_="check")
    op.drop_constraint("perf_profile_load_mode", "performance_profiles", type_="check")
    op.drop_index("ix_performance_profiles_scenario_id", table_name="performance_profiles")
    op.drop_column("performance_profiles", "stream_config")
    op.drop_column("performance_profiles", "cleanup_mode")
    op.drop_column("performance_profiles", "engine")
    op.drop_column("performance_profiles", "step_stages")
    op.drop_column("performance_profiles", "scenario_version_id")
    op.drop_column("performance_profiles", "scenario_id")
    op.drop_column("performance_profiles", "target_type")
    op.alter_column(
        "performance_profiles", "case_version_id", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column("performance_profiles", "case_id", existing_type=sa.Integer(), nullable=False)
    op.create_check_constraint(
        "perf_profile_load_mode",
        "performance_profiles",
        "load_mode IN ('FIXED_ITERATIONS','FIXED_RPS')",
    )
    op.create_check_constraint(
        "perf_profile_load_config",
        "performance_profiles",
        "(load_mode = 'FIXED_ITERATIONS' AND target_rps IS NULL AND duration_seconds IS NULL) "
        "OR (load_mode = 'FIXED_RPS' AND target_rps > 0 AND duration_seconds >= 1 "
        "AND duration_seconds <= 3600)",
    )
