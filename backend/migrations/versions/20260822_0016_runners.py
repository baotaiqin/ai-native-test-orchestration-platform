"""创建 Runner 注册、凭证、能力与心跳控制面表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0016"
down_revision: str | None = "20260821_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runners",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("os", sa.String(length=128), nullable=True),
        sa.Column("cpu", sa.String(length=128), nullable=True),
        sa.Column("ram", sa.String(length=128), nullable=True),
        sa.Column("disk", sa.String(length=128), nullable=True),
        sa.Column("python_version", sa.String(length=64), nullable=True),
        sa.Column("chrome_version", sa.String(length=64), nullable=True),
        sa.Column("playwright_version", sa.String(length=64), nullable=True),
        sa.Column("java_version", sa.String(length=64), nullable=True),
        sa.Column("jmeter_version", sa.String(length=64), nullable=True),
        sa.Column("credential_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "heartbeat_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="runners_status_values",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runners")),
        sa.UniqueConstraint("credential_digest", name=op.f("uq_runners_credential_digest")),
    )
    op.create_index(
        "ix_runners_status_last_heartbeat",
        "runners",
        ["status", "last_heartbeat_at"],
    )

    op.create_table(
        "runner_registration_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_runner_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','REVOKED','EXPIRED')",
            name="runner_registration_tokens_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_runner_id"],
            ["runners.id"],
            name=op.f("fk_runner_registration_tokens_consumed_runner_id_runners"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_registration_tokens")),
        sa.UniqueConstraint(
            "token_digest", name=op.f("uq_runner_registration_tokens_token_digest")
        ),
    )
    op.create_index(
        op.f("ix_runner_registration_tokens_expires_at"),
        "runner_registration_tokens",
        ["expires_at"],
    )
    op.create_index(
        "ix_runner_registration_tokens_status_expires_at",
        "runner_registration_tokens",
        ["status", "expires_at"],
    )

    op.create_table(
        "runner_tags",
        sa.Column("runner_id", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_runner_tags_runner_id_runners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("runner_id", "tag", name=op.f("pk_runner_tags")),
    )
    op.create_index(op.f("ix_runner_tags_tag"), "runner_tags", ["tag"])

    op.create_table(
        "runner_capabilities",
        sa.Column("runner_id", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "capability IN ('API','WEB','SQL','SCRIPT','SSE','JMETER')",
            name="runner_capabilities_name_values",
        ),
        sa.CheckConstraint(
            "status IN ('READY','UNAVAILABLE')",
            name="runner_capabilities_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_runner_capabilities_runner_id_runners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "runner_id", "capability", name=op.f("pk_runner_capabilities")
        ),
    )
    op.create_index(
        op.f("ix_runner_capabilities_status"), "runner_capabilities", ["status"]
    )

    op.create_table(
        "runner_slots",
        sa.Column("runner_id", sa.String(length=64), nullable=False),
        sa.Column("slot_type", sa.String(length=16), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "slot_type IN ('API','WEB','PERFORMANCE')",
            name="runner_slots_type_values",
        ),
        sa.CheckConstraint("total >= 0", name="runner_slots_total_nonnegative"),
        sa.CheckConstraint(
            "available >= 0 AND available <= total",
            name="runner_slots_available_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
            name=op.f("fk_runner_slots_runner_id_runners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("runner_id", "slot_type", name=op.f("pk_runner_slots")),
    )


def downgrade() -> None:
    op.drop_table("runner_slots")
    op.drop_index(op.f("ix_runner_capabilities_status"), table_name="runner_capabilities")
    op.drop_table("runner_capabilities")
    op.drop_index(op.f("ix_runner_tags_tag"), table_name="runner_tags")
    op.drop_table("runner_tags")
    op.drop_index(
        "ix_runner_registration_tokens_status_expires_at",
        table_name="runner_registration_tokens",
    )
    op.drop_index(
        op.f("ix_runner_registration_tokens_expires_at"),
        table_name="runner_registration_tokens",
    )
    op.drop_table("runner_registration_tokens")
    op.drop_index("ix_runners_status_last_heartbeat", table_name="runners")
    op.drop_table("runners")
