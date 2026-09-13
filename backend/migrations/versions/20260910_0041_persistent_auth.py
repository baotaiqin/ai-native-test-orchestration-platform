"""Add persistent platform users and revocable authentication sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0041"
down_revision: str | None = "20260910_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _legacy_auth_tables() -> set[str]:
    return {"users", "roles", "user_roles"}


def _prepare_legacy_auth_replacement() -> None:
    """Replace the unused Phase-0 auth placeholder without losing real rows."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    present = _legacy_auth_tables() & table_names
    if not present:
        return
    if present != _legacy_auth_tables():
        raise RuntimeError("Legacy authentication tables are incomplete; refusing migration")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "platform_role" in user_columns or "auth_version" in user_columns:
        raise RuntimeError("Persistent authentication schema is partially applied")

    counts = {
        table_name: int(
            bind.execute(sa.text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar_one()
        )
        for table_name in sorted(_legacy_auth_tables())
    }
    if any(counts.values()):
        raise RuntimeError(
            "Legacy authentication placeholder contains rows; explicit data migration is required"
        )

    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")


def _create_legacy_auth_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("code", name=op.f("uq_roles_code")),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_user_roles_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_roles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name=op.f("pk_user_roles")),
    )


def upgrade() -> None:
    _prepare_legacy_auth_replacement()
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("platform_role", sa.String(length=16), nullable=False),
        sa.Column("auth_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name=op.f("ck_users_users_status_values"),
        ),
        sa.CheckConstraint(
            "platform_role IN ('ADMIN','USER')",
            name=op.f("ck_users_users_platform_role_values"),
        ),
        sa.CheckConstraint(
            "auth_version >= 1",
            name=op.f("ck_users_users_auth_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index(
        "ix_users_status_platform_role",
        "users",
        ["status", "platform_role"],
    )

    op.create_table(
        "auth_sessions",
        sa.Column("session_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "auth_version >= 1",
            name=op.f("ck_auth_sessions_auth_sessions_auth_version_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_auth_sessions_auth_sessions_expiry_after_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_digest", name=op.f("pk_auth_sessions")
        ),
    )
    op.create_index(
        "ix_auth_sessions_user_revoked_expires",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    guard_table = op.create_table(
        "auth_admin_guards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_admin_guards")),
    )
    op.bulk_insert(guard_table, [{"id": 1}])


def downgrade() -> None:
    user_count = int(
        op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one()
    )
    if user_count:
        raise RuntimeError("Cannot downgrade persistent authentication while users exist")

    op.drop_table("auth_admin_guards")
    op.drop_index(
        "ix_auth_sessions_user_revoked_expires", table_name="auth_sessions"
    )
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_status_platform_role", table_name="users")
    op.drop_table("users")
    _create_legacy_auth_tables()
