"""Extend requirement links for explicit asset versions and retained history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0039"
down_revision: str | None = "20260909_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "requirement_case_links"


def upgrade() -> None:
    op.drop_constraint(
        "uq_requirement_case_links_requirement_case_relation",
        _TABLE,
        type_="unique",
    )
    op.alter_column(
        _TABLE,
        "case_type",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        _TABLE,
        "case_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "asset_type",
            sa.String(length=32),
            nullable=False,
            server_default="TEST_CASE",
        ),
    )
    op.add_column(_TABLE, sa.Column("case_version_id", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("web_case_id", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("web_case_version_id", sa.Integer(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        _TABLE,
        sa.Column("active_slot", sa.Integer(), nullable=True, server_default="1"),
    )
    op.add_column(_TABLE, sa.Column("supersedes_link_id", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("created_by", sa.String(length=64), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "created_at_time_basis",
            sa.String(length=32),
            nullable=False,
            server_default="LEGACY_UNKNOWN",
        ),
    )
    op.add_column(_TABLE, sa.Column("removed_by", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("removed_at", sa.DateTime(), nullable=True))

    op.create_foreign_key(
        "fk_requirement_case_links_case_version_id_test_case_versions",
        _TABLE,
        "test_case_versions",
        ["case_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirement_case_links_web_case_id_web_cases",
        _TABLE,
        "web_cases",
        ["web_case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_requirement_case_links_web_case_version_id_web_case_versions",
        _TABLE,
        "web_case_versions",
        ["web_case_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_req_case_links_supersedes"),
        _TABLE,
        _TABLE,
        ["supersedes_link_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for column in (
        "case_version_id",
        "web_case_id",
        "web_case_version_id",
        "supersedes_link_id",
    ):
        op.create_index(f"ix_{_TABLE}_{column}", _TABLE, [column])
    op.create_index(
        "ix_requirement_case_links_requirement_status_id",
        _TABLE,
        ["requirement_id", "status", "id"],
    )
    op.create_check_constraint(
        op.f("ck_requirement_case_links_asset_type_values"),
        _TABLE,
        "asset_type IN ('TEST_CASE','WEB_CASE')",
    )
    op.create_check_constraint(
        op.f("ck_requirement_case_links_target_shape"),
        _TABLE,
        "(asset_type = 'TEST_CASE' AND case_id IS NOT NULL "
        "AND web_case_id IS NULL AND web_case_version_id IS NULL "
        "AND case_type IS NOT NULL) OR "
        "(asset_type = 'WEB_CASE' AND case_id IS NULL "
        "AND case_version_id IS NULL AND web_case_id IS NOT NULL "
        "AND case_type IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_requirement_case_links_status_active_slot"),
        _TABLE,
        "(status = 'ACTIVE' AND active_slot IS NOT NULL AND active_slot = 1 "
        "AND removed_by IS NULL AND removed_at IS NULL) OR "
        "(status = 'REMOVED' AND active_slot IS NULL "
        "AND removed_by IS NOT NULL AND removed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_requirement_case_links_created_time_basis"),
        _TABLE,
        "created_at_time_basis IN ('UTC','LEGACY_UNKNOWN')",
    )
    op.create_unique_constraint(
        "uq_requirement_case_links_active_test_case",
        _TABLE,
        ["requirement_id", "case_id", "relation_type", "active_slot"],
    )
    op.create_unique_constraint(
        "uq_requirement_case_links_active_web_case",
        _TABLE,
        ["requirement_id", "web_case_id", "relation_type", "active_slot"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    incompatible = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM requirement_case_links "
                "WHERE asset_type <> 'TEST_CASE' OR status <> 'ACTIVE' "
                "OR case_version_id IS NOT NULL OR web_case_id IS NOT NULL "
                "OR web_case_version_id IS NOT NULL OR supersedes_link_id IS NOT NULL "
                "OR created_by IS NOT NULL OR created_at_time_basis <> 'LEGACY_UNKNOWN' "
                "OR removed_by IS NOT NULL OR removed_at IS NOT NULL"
            )
        ).scalar_one()
    )
    if incompatible:
        raise RuntimeError(
            "Cannot downgrade requirement links while versioned, WebCase, removed, "
            "or newly audited associations exist"
        )

    op.drop_constraint(
        "uq_requirement_case_links_active_web_case",
        _TABLE,
        type_="unique",
    )
    op.drop_constraint(
        "uq_requirement_case_links_active_test_case",
        _TABLE,
        type_="unique",
    )
    for name in (
        "ck_requirement_case_links_created_time_basis",
        "ck_requirement_case_links_status_active_slot",
        "ck_requirement_case_links_target_shape",
        "ck_requirement_case_links_asset_type_values",
    ):
        op.drop_constraint(op.f(name), _TABLE, type_="check")
    op.drop_index("ix_requirement_case_links_requirement_status_id", table_name=_TABLE)
    for name in (
        "fk_req_case_links_supersedes",
        "fk_requirement_case_links_web_case_version_id_web_case_versions",
        "fk_requirement_case_links_web_case_id_web_cases",
        "fk_requirement_case_links_case_version_id_test_case_versions",
    ):
        op.drop_constraint(op.f(name), _TABLE, type_="foreignkey")
    for column in (
        "supersedes_link_id",
        "web_case_version_id",
        "web_case_id",
        "case_version_id",
    ):
        op.drop_index(f"ix_{_TABLE}_{column}", table_name=_TABLE)
    for column in (
        "removed_at",
        "removed_by",
        "created_at_time_basis",
        "created_by",
        "supersedes_link_id",
        "active_slot",
        "status",
        "web_case_version_id",
        "web_case_id",
        "case_version_id",
        "asset_type",
    ):
        op.drop_column(_TABLE, column)
    op.alter_column(
        _TABLE,
        "case_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        _TABLE,
        "case_type",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_requirement_case_links_requirement_case_relation",
        _TABLE,
        ["requirement_id", "case_id", "relation_type"],
    )
