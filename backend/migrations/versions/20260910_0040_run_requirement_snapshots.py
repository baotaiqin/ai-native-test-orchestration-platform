"""Add immutable requirement-source snapshots for CaseRun reports."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0040"
down_revision: str | None = "20260910_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAPTURES = "run_requirement_captures"
_SOURCES = "run_requirement_sources"


def upgrade() -> None:
    op.create_table(
        _CAPTURES,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("capture_status", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column(
            "captured_at_time_basis",
            sa.String(length=16),
            nullable=False,
            server_default="UTC",
        ),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_asset_id", sa.Integer(), nullable=False),
        sa.Column("target_version_id", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consistency_basis",
            sa.String(length=32),
            nullable=False,
            server_default="CREATE_RUN_TRANSACTION",
        ),
        sa.CheckConstraint(
            "capture_status IN ('CAPTURED','CAPTURED_EMPTY','UNSUPPORTED_TARGET')",
            name=op.f("ck_run_requirement_captures_capture_status_values"),
        ),
        sa.CheckConstraint(
            "target_type IN ('API_CASE','WEB_CASE','SCENARIO')",
            name=op.f("ck_run_requirement_captures_target_type_values"),
        ),
        sa.CheckConstraint(
            "captured_at_time_basis = 'UTC'",
            name=op.f("ck_run_requirement_captures_captured_time_basis_utc"),
        ),
        sa.CheckConstraint(
            "consistency_basis = 'CREATE_RUN_TRANSACTION'",
            name=op.f("ck_run_requirement_captures_consistency_basis_value"),
        ),
        sa.CheckConstraint(
            "target_asset_id > 0 AND target_version_id > 0",
            name=op.f("ck_run_requirement_captures_target_ids_positive"),
        ),
        sa.CheckConstraint(
            "item_count >= 0 AND item_count <= 1000",
            name=op.f("ck_run_requirement_captures_item_count_bounds"),
        ),
        sa.CheckConstraint(
            "(capture_status = 'CAPTURED' AND item_count > 0) OR "
            "(capture_status IN ('CAPTURED_EMPTY','UNSUPPORTED_TARGET') "
            "AND item_count = 0)",
            name=op.f("ck_run_requirement_captures_status_count_consistent"),
        ),
        sa.CheckConstraint(
            "(target_type = 'SCENARIO' AND capture_status = 'UNSUPPORTED_TARGET') OR "
            "(target_type IN ('API_CASE','WEB_CASE') "
            "AND capture_status IN ('CAPTURED','CAPTURED_EMPTY'))",
            name=op.f("ck_run_requirement_captures_target_status_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name="fk_run_req_captures_case_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_requirement_captures")),
        sa.UniqueConstraint(
            "case_run_id", name="uq_run_req_captures_case_run"
        ),
    )
    op.create_index(
        "ix_run_req_captures_target",
        _CAPTURES,
        ["target_type", "target_asset_id"],
    )

    op.create_table(
        _SOURCES,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("capture_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("original_link_id", sa.Integer(), nullable=False),
        sa.Column("supersedes_link_id", sa.Integer(), nullable=True),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("requirement_code", sa.String(length=64), nullable=False),
        sa.Column("requirement_title", sa.String(length=255), nullable=False),
        sa.Column("requirement_type", sa.String(length=32), nullable=False),
        sa.Column("requirement_status", sa.String(length=32), nullable=False),
        sa.Column("requirement_version_id", sa.Integer(), nullable=True),
        sa.Column("requirement_version_no", sa.Integer(), nullable=True),
        sa.Column("requirement_content_hash", sa.String(length=64), nullable=True),
        sa.Column("requirement_source_type", sa.String(length=32), nullable=True),
        sa.Column("requirement_version_binding", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("link_asset_type", sa.String(length=32), nullable=False),
        sa.Column("target_asset_id", sa.Integer(), nullable=False),
        sa.Column("target_version_id", sa.Integer(), nullable=False),
        sa.Column("link_asset_version_id", sa.Integer(), nullable=True),
        sa.Column("asset_version_binding", sa.String(length=40), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("link_created_by", sa.String(length=64), nullable=True),
        sa.Column("link_created_at", sa.DateTime(), nullable=False),
        sa.Column("link_created_at_time_basis", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column(
            "captured_at_time_basis",
            sa.String(length=16),
            nullable=False,
            server_default="UTC",
        ),
        sa.CheckConstraint(
            "sequence_no > 0 AND sequence_no <= 1000",
            name=op.f("ck_run_requirement_sources_sequence_bounds"),
        ),
        sa.CheckConstraint(
            "target_type IN ('API_CASE','WEB_CASE')",
            name=op.f("ck_run_requirement_sources_target_type_values"),
        ),
        sa.CheckConstraint(
            "(target_type = 'API_CASE' AND link_asset_type = 'TEST_CASE') OR "
            "(target_type = 'WEB_CASE' AND link_asset_type = 'WEB_CASE')",
            name=op.f("ck_run_requirement_sources_asset_type_consistent"),
        ),
        sa.CheckConstraint(
            "target_asset_id > 0 AND target_version_id > 0 "
            "AND original_link_id > 0",
            name=op.f("ck_run_requirement_sources_ids_positive"),
        ),
        sa.CheckConstraint(
            "supersedes_link_id IS NULL OR supersedes_link_id > 0",
            name=op.f("ck_run_requirement_sources_supersedes_positive"),
        ),
        sa.CheckConstraint(
            "requirement_id > 0",
            name=op.f("ck_run_requirement_sources_requirement_id_positive"),
        ),
        sa.CheckConstraint(
            "requirement_version_binding IN "
            "('EXACT_REQUIREMENT_VERSION','REQUIREMENT_VERSION_UNKNOWN')",
            name=op.f("ck_run_requirement_sources_requirement_binding_values"),
        ),
        sa.CheckConstraint(
            "(requirement_version_binding = 'EXACT_REQUIREMENT_VERSION' "
            "AND requirement_version_id IS NOT NULL "
            "AND requirement_version_no IS NOT NULL "
            "AND requirement_content_hash IS NOT NULL "
            "AND requirement_source_type IS NOT NULL) OR "
            "(requirement_version_binding = 'REQUIREMENT_VERSION_UNKNOWN' "
            "AND requirement_version_id IS NULL "
            "AND requirement_version_no IS NULL "
            "AND requirement_content_hash IS NULL "
            "AND requirement_source_type IS NULL)",
            name=op.f("ck_run_requirement_sources_requirement_binding_shape"),
        ),
        sa.CheckConstraint(
            "asset_version_binding IN "
            "('EXACT_EXECUTION_VERSION','ASSET_VERSION_UNKNOWN')",
            name=op.f("ck_run_requirement_sources_asset_binding_values"),
        ),
        sa.CheckConstraint(
            "(asset_version_binding = 'EXACT_EXECUTION_VERSION' "
            "AND link_asset_version_id IS NOT NULL "
            "AND link_asset_version_id = target_version_id) OR "
            "(asset_version_binding = 'ASSET_VERSION_UNKNOWN' "
            "AND link_asset_version_id IS NULL)",
            name=op.f("ck_run_requirement_sources_asset_binding_shape"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_run_requirement_sources_confidence_bounds"),
        ),
        sa.CheckConstraint(
            "link_created_at_time_basis IN ('UTC','LEGACY_UNKNOWN')",
            name=op.f("ck_run_requirement_sources_link_time_basis_values"),
        ),
        sa.CheckConstraint(
            "captured_at_time_basis = 'UTC'",
            name=op.f("ck_run_requirement_sources_captured_time_basis_utc"),
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["run_requirement_captures.id"],
            name="fk_run_req_sources_capture",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_requirement_sources")),
        sa.UniqueConstraint(
            "capture_id", "sequence_no", name="uq_run_req_sources_capture_seq"
        ),
        sa.UniqueConstraint(
            "capture_id", "original_link_id", name="uq_run_req_sources_capture_link"
        ),
    )
    op.create_index(
        "ix_run_req_sources_requirement", _SOURCES, ["requirement_id"]
    )
    op.create_index(
        "ix_run_req_sources_capture_order",
        _SOURCES,
        ["capture_id", "sequence_no", "id"],
    )


def downgrade() -> None:
    captured = int(
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM run_requirement_captures"))
        .scalar_one()
    )
    if captured:
        raise RuntimeError(
            "Cannot downgrade run requirement snapshots while captured data exists"
        )

    # Drop the child first. This also removes its FK and supporting indexes before
    # the parent table is removed, which is valid on MySQL and SQLite.
    op.drop_index("ix_run_req_sources_capture_order", table_name=_SOURCES)
    op.drop_index("ix_run_req_sources_requirement", table_name=_SOURCES)
    op.drop_table(_SOURCES)
    op.drop_index("ix_run_req_captures_target", table_name=_CAPTURES)
    op.drop_table(_CAPTURES)
