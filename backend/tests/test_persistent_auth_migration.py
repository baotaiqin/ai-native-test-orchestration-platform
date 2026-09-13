from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260910_0041_persistent_auth.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("persistent_auth_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    sa.Table(
        "user_roles",
        metadata,
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(users.c.id), primary_key=True),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey(roles.c.id), primary_key=True),
    )
    return metadata


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def test_upgrade_replaces_empty_legacy_placeholder_and_downgrade_restores_it() -> None:
    migration = _migration_module()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_metadata().create_all(connection)
        migration.op = _operations(connection)

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "roles" not in inspector.get_table_names()
        assert "user_roles" not in inspector.get_table_names()
        assert {"platform_role", "auth_version"}.issubset(
            {column["name"] for column in inspector.get_columns("users")}
        )
        assert {"auth_sessions", "auth_admin_guards"}.issubset(inspector.get_table_names())

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert {"users", "roles", "user_roles"}.issubset(inspector.get_table_names())
        assert "platform_role" not in {
            column["name"] for column in inspector.get_columns("users")
        }
        assert "auth_sessions" not in inspector.get_table_names()
        assert "auth_admin_guards" not in inspector.get_table_names()


def test_upgrade_refuses_to_drop_nonempty_legacy_authentication_rows() -> None:
    migration = _migration_module()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        metadata = _legacy_metadata()
        metadata.create_all(connection)
        connection.execute(
            metadata.tables["users"].insert().values(
                id=1,
                username="legacy",
                password_hash="hash",
                display_name="Legacy User",
                status="ACTIVE",
            )
        )
        migration.op = _operations(connection)

        with pytest.raises(RuntimeError, match="explicit data migration"):
            migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"users", "roles", "user_roles"}.issubset(inspector.get_table_names())
        assert "auth_sessions" not in inspector.get_table_names()
