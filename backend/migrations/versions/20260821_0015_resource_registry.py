"""创建项目级 Resource Registry，支持注册顺序和 LIFO Cleanup。

Revision ID: 20260821_0015
Revises: 20260821_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0015"
down_revision: str | None = "20260821_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=False),
        sa.Column("cleanup_type", sa.String(length=16), nullable=False),
        sa.Column("cleanup_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("registration_sequence", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="RUNTIME"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','CLEANING','CLEANED','FAILED','SKIPPED')",
            name="resource_registry_status_values",
        ),
        sa.CheckConstraint(
            "cleanup_type IN ('API','SQL')",
            name="resource_registry_cleanup_type_values",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="resource_registry_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "registration_sequence > 0",
            name="resource_registry_registration_sequence_positive",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "registration_sequence",
            name="uq_resource_registry_project_run_sequence",
        ),
    )
    op.create_index(op.f("ix_resource_registry_project_id"), "resource_registry", ["project_id"])
    op.create_index(op.f("ix_resource_registry_run_id"), "resource_registry", ["run_id"])
    op.create_index(op.f("ix_resource_registry_status"), "resource_registry", ["status"])
    op.create_index(
        "ix_resource_registry_project_run_sequence",
        "resource_registry",
        ["project_id", "run_id", "registration_sequence"],
    )
    op.create_index(
        "ix_resource_registry_project_status",
        "resource_registry",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_registry_project_status", table_name="resource_registry")
    op.drop_index(
        "ix_resource_registry_project_run_sequence", table_name="resource_registry"
    )
    op.drop_index(op.f("ix_resource_registry_status"), table_name="resource_registry")
    op.drop_index(op.f("ix_resource_registry_run_id"), table_name="resource_registry")
    op.drop_index(op.f("ix_resource_registry_project_id"), table_name="resource_registry")
    op.drop_table("resource_registry")
