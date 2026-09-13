from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0070"
down_revision: str | None = "20260913_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "performance_profiles",
        sa.Column(
            "load_mode",
            sa.String(24),
            nullable=False,
            server_default="FIXED_ITERATIONS",
        ),
    )
    op.add_column(
        "performance_profiles", sa.Column("target_rps", sa.Float(), nullable=True)
    )
    op.add_column(
        "performance_profiles", sa.Column("duration_seconds", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "perf_profile_load_mode",
        "performance_profiles",
        "load_mode IN ('FIXED_ITERATIONS','FIXED_RPS')",
    )
    op.create_check_constraint(
        "perf_profile_load_config",
        "performance_profiles",
        "(load_mode = 'FIXED_ITERATIONS' AND target_rps IS NULL "
        "AND duration_seconds IS NULL) OR (load_mode = 'FIXED_RPS' "
        "AND target_rps > 0 AND duration_seconds >= 1 AND duration_seconds <= 3600)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "perf_profile_load_config", "performance_profiles", type_="check"
    )
    op.drop_constraint("perf_profile_load_mode", "performance_profiles", type_="check")
    op.drop_column("performance_profiles", "duration_seconds")
    op.drop_column("performance_profiles", "target_rps")
    op.drop_column("performance_profiles", "load_mode")
