"""Persist safe, human-reviewed Web locator healing proposals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0033"
down_revision: str | None = "20260907_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "locator_healing_proposals"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("web_case_id", sa.Integer(), nullable=False),
        sa.Column("web_case_version_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("draft_key", sa.String(length=16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.String(length=5000), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("old_locator", sa.JSON(), nullable=False),
        sa.Column("proposed_locator", sa.JSON(), nullable=True),
        sa.Column("human_locator", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("evidence_candidate_index", sa.Integer(), nullable=False),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("created_element_version_id", sa.Integer(), nullable=True),
        sa.Column("created_web_case_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_status_values"),
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_size_bounds"),
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_snapshot_sha256_length"),
        ),
        sa.CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_draft_key_values"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_confidence_bounds"),
        ),
        sa.CheckConstraint(
            "evidence_candidate_index >= 0 AND evidence_candidate_index < 40",
            name=op.f(f"ck_{_TABLE}_{_TABLE}_candidate_index_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f(f"fk_{_TABLE}_project_id_projects"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"],
            name=op.f(f"fk_{_TABLE}_run_id_runs"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"], ["case_runs.id"],
            name=op.f(f"fk_{_TABLE}_case_run_id_case_runs"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["web_case_id"], ["web_cases.id"],
            name=op.f(f"fk_{_TABLE}_web_case_id_web_cases"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["web_case_version_id"], ["web_case_versions.id"],
            name=op.f(f"fk_{_TABLE}_web_case_version_id_web_case_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ai_call_id"], ["ai_call_logs.id"],
            name=op.f(f"fk_{_TABLE}_ai_call_id_ai_call_logs"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_element_version_id"], ["web_element_versions.id"],
            name=op.f(f"fk_{_TABLE}_created_element_version_id_web_element_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_web_case_version_id"], ["web_case_versions.id"],
            name=op.f(f"fk_{_TABLE}_created_web_case_version_id_web_case_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.UniqueConstraint("ai_call_id", name=f"uq_{_TABLE}_ai_call"),
        sa.UniqueConstraint(
            "run_id", "case_run_id", "node_id", "draft_key",
            name=f"uq_{_TABLE}_run_case_node_draft",
        ),
    )
    op.create_index(f"ix_{_TABLE}_project_id", _TABLE, ["project_id"])
    op.create_index(f"ix_{_TABLE}_run_id", _TABLE, ["run_id"])
    op.create_index(f"ix_{_TABLE}_case_run_id", _TABLE, ["case_run_id"])
    op.create_index(f"ix_{_TABLE}_web_case_id", _TABLE, ["web_case_id"])
    op.create_index(
        f"ix_{_TABLE}_run_case_node", _TABLE,
        ["run_id", "case_run_id", "node_id", "created_at"],
    )
    op.create_index(f"ix_{_TABLE}_project_created", _TABLE, ["project_id", "created_at"])
    op.create_index(f"ix_{_TABLE}_status_created", _TABLE, ["status", "created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
