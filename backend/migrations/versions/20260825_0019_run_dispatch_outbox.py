"""创建 Run RabbitMQ dispatch outbox。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0019"
down_revision: str | None = "20260824_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_dispatch_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("runner_id", sa.String(length=64), nullable=False),
        sa.Column("routing_key", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_runner_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "schema_version = 1",
            name="run_dispatch_outbox_schema_version_v1",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PUBLISHED','FAILED')",
            name="run_dispatch_outbox_status_values",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 10",
            name="run_dispatch_outbox_attempt_count_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_dispatch_outbox_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_run_dispatch_outbox_runner_id_runners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claimed_runner_id"],
            ["runners.id"],
            name=op.f("fk_run_dispatch_outbox_claimed_runner_id_runners"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_dispatch_outbox")),
        sa.UniqueConstraint("message_id", name=op.f("uq_run_dispatch_outbox_message_id")),
        sa.UniqueConstraint("run_id", name=op.f("uq_run_dispatch_outbox_run_id")),
    )
    op.create_index(
        "ix_run_dispatch_outbox_status_created",
        "run_dispatch_outbox",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_run_dispatch_outbox_runner_status",
        "run_dispatch_outbox",
        ["runner_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_dispatch_outbox_runner_status", table_name="run_dispatch_outbox")
    op.drop_index("ix_run_dispatch_outbox_status_created", table_name="run_dispatch_outbox")
    op.drop_table("run_dispatch_outbox")
