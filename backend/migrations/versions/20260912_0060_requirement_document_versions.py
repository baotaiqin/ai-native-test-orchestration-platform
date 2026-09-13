"""Add project-level versions for complete requirement documents."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260912_0060"
down_revision: str | None = "20260912_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _build_snapshot(rows: list[dict]) -> list[dict]:
    by_parent: dict[int | None, list[dict]] = defaultdict(list)
    for row in rows:
        parent_id = row["parent_id"]
        if parent_id is not None and not any(item["id"] == parent_id for item in rows):
            parent_id = None
        by_parent[parent_id].append(row)
    for items in by_parent.values():
        items.sort(key=lambda item: (item["order_index"], item["id"]))

    snapshot: list[dict] = []

    def append_nodes(parent_id: int | None, prefix: str = "") -> None:
        for index, row in enumerate(by_parent.get(parent_id, []), start=1):
            outline_number = f"{prefix}.{index}" if prefix else str(index)
            snapshot.append(
                {
                    "requirement_id": row["id"],
                    "code": row["code"],
                    "parent_id": row["parent_id"],
                    "outline_number": outline_number,
                    "title": row["title"],
                    "type": row["type"],
                    "order_index": row["order_index"],
                    "markdown_content": row["markdown_content"] or "",
                    "technical_version_id": row["current_version_id"],
                    "technical_version_no": row["version_no"],
                }
            )
            append_nodes(row["id"], outline_number)

    append_nodes(None)
    return snapshot


def upgrade() -> None:
    op.create_table(
        "requirement_document_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("change_summary", sa.String(length=500), nullable=True),
        sa.Column("source_review_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_review_id"], ["requirement_reviews.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_document_versions")),
        sa.UniqueConstraint(
            "project_id",
            "version_no",
            name=op.f(
                "uq_requirement_document_versions_project_id_version_no"
            ),
        ),
    )
    op.create_index(
        op.f("ix_requirement_document_versions_project_id"),
        "requirement_document_versions",
        ["project_id"],
        unique=False,
    )

    # Snapshot construction depends on reading the existing hierarchy. It runs
    # during normal online upgrades; static SQL generation can only emit DDL.
    if context.is_offline_mode():
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT r.id, r.project_id, r.parent_id, r.code, r.title, r.type, "
            "r.order_index, r.current_version_id, r.created_by, "
            "rv.version_no, rv.markdown_content, rv.source_type, rv.source_filename "
            "FROM requirements r "
            "LEFT JOIN requirement_versions rv ON rv.id = r.current_version_id "
            "WHERE r.status = 'ACTIVE' "
            "ORDER BY r.project_id, r.order_index, r.id"
        )
    ).mappings()
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["project_id"])].append(dict(row))

    document_versions = sa.table(
        "requirement_document_versions",
        sa.column("project_id", sa.Integer()),
        sa.column("version_no", sa.Integer()),
        sa.column("snapshot", sa.JSON()),
        sa.column("content_hash", sa.String()),
        sa.column("source_type", sa.String()),
        sa.column("source_filename", sa.String()),
        sa.column("change_summary", sa.String()),
        sa.column("source_review_id", sa.Integer()),
        sa.column("created_by", sa.String()),
    )
    for project_id, project_rows in grouped.items():
        snapshot = _build_snapshot(project_rows)
        serialized = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        filenames = {
            row["source_filename"] for row in project_rows if row["source_filename"]
        }
        bind.execute(
            document_versions.insert().values(
                project_id=project_id,
                version_no=1,
                snapshot=snapshot,
                content_hash=hashlib.sha256(serialized.encode()).hexdigest(),
                source_type=(
                    "MARKDOWN"
                    if any(row["source_type"] == "MARKDOWN" for row in project_rows)
                    else "MANUAL"
                ),
                source_filename=next(iter(filenames)) if len(filenames) == 1 else None,
                change_summary="迁移现有完整需求树",
                source_review_id=None,
                created_by=project_rows[0]["created_by"],
            )
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_requirement_document_versions_project_id"),
        table_name="requirement_document_versions",
    )
    op.drop_table("requirement_document_versions")
