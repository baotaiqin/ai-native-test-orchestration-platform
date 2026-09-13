from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0077"
down_revision: str | None = "20260913_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "performance_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(128),
            sa.ForeignKey("performance_runs.run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "prompt_version_id",
            sa.Integer(),
            sa.ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "output_schema_id",
            sa.Integer(),
            sa.ForeignKey("output_schemas.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ai_call_id",
            sa.Integer(),
            sa.ForeignKey("ai_call_logs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("draft_key", sa.String(16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("source_sla_verdict", sa.String(16), nullable=False),
        sa.Column("additional_instructions", sa.String(2000), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=True),
        sa.Column("actual_model", sa.String(128), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
        sa.Column("repair_used", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','COMPLETED')", name="performance_analyses_status"),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 65536",
            name="performance_analyses_snapshot_size",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="performance_analyses_snapshot_sha256",
        ),
        sa.CheckConstraint(
            "source_sla_verdict IN ('MEETS_SLA','MISSES_SLA','NO_SLA')",
            name="performance_analyses_sla_verdict",
        ),
        sa.UniqueConstraint("ai_call_id", name="uq_performance_analyses_ai_call"),
        sa.UniqueConstraint("run_id", "draft_key", name="uq_performance_analyses_run_draft"),
    )
    op.create_index("ix_performance_analyses_project_id", "performance_analyses", ["project_id"])
    op.create_index("ix_performance_analyses_run_id", "performance_analyses", ["run_id"])
    op.create_index(
        "ix_performance_analyses_run_created",
        "performance_analyses",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_performance_analyses_project_created",
        "performance_analyses",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("performance_analyses")
