"""创建 API_CASE Evidence 元数据表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0022"
down_revision: str | None = "20260828_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("step_run_id", sa.Integer(), nullable=True),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("minio_bucket", sa.String(length=63), nullable=False),
        sa.Column("minio_key", sa.String(length=512), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "artifact_type IN ('RESPONSE','ASSERTION_RESULT')",
            name="artifacts_type_values",
        ),
        sa.CheckConstraint(
            "size >= 0 AND size <= 2000000",
            name="artifacts_size_bounds",
        ),
        sa.CheckConstraint(
            "length(sha256) = 64",
            name="artifacts_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_artifacts_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_artifacts_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name=op.f("fk_artifacts_case_run_id_case_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
            name=op.f("fk_artifacts_step_run_id_step_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint(
            "run_id",
            "case_run_id",
            "artifact_type",
            name=op.f("uq_artifacts_run_case_type"),
        ),
        sa.UniqueConstraint(
            "minio_bucket",
            "minio_key",
            name=op.f("uq_artifacts_bucket_key"),
        ),
    )
    op.create_index(
        "ix_artifacts_project_created",
        "artifacts",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_artifacts_run_case",
        "artifacts",
        ["run_id", "case_run_id"],
    )
    op.create_index(
        "ix_artifacts_project_run",
        "artifacts",
        ["project_id", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_project_run", table_name="artifacts")
    op.drop_index("ix_artifacts_run_case", table_name="artifacts")
    op.drop_index("ix_artifacts_project_created", table_name="artifacts")
    op.drop_table("artifacts")
