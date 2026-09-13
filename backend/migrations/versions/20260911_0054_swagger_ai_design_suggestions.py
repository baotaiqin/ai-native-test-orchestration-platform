"""Add Swagger AI design review, Case provenance, and complete Case bulk review."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0054"
down_revision: str | None = "20260911_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "api_design_suggestions"


def upgrade() -> None:
    op.add_column(
        "ai_case_suggestions",
        sa.Column("linked_requirement_ids", sa.JSON(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE ai_case_suggestions "
            "SET linked_requirement_ids = '[]' WHERE linked_requirement_ids IS NULL"
        )
    )
    op.alter_column(
        "ai_case_suggestions",
        "linked_requirement_ids",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.add_column(
        "test_cases",
        sa.Column("api_definition_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_test_cases_api_definition_id_api_definitions"),
        "test_cases",
        "api_definitions",
        ["api_definition_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_test_cases_api_definition_id"),
        "test_cases",
        ["api_definition_id"],
    )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("ai_call_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("draft_key", sa.String(length=16), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_size", sa.Integer(), nullable=False),
        sa.Column("additional_instructions", sa.Text(), nullable=True),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("human_result", sa.JSON(), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("created_test_case_ids", sa.JSON(), nullable=False),
        sa.Column("created_scenario_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('CASE_SET','SCENARIO')",
            name=op.f("ck_api_design_suggestions_api_design_suggestions_kind_values"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name=op.f("ck_api_design_suggestions_api_design_suggestions_status_values"),
        ),
        sa.CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name=op.f("ck_api_design_suggestions_api_design_suggestions_draft_key_values"),
        ),
        sa.CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name=op.f("ck_api_design_suggestions_api_design_suggestions_snapshot_size_bounds"),
        ),
        sa.CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name=op.f("ck_api_design_suggestions_api_design_suggestions_snapshot_sha256_length"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_api_design_suggestions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["api_definition_imports.id"],
            name=op.f("fk_api_design_suggestions_import_id_api_definition_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_call_id"],
            ["ai_call_logs.id"],
            name=op.f("fk_api_design_suggestions_ai_call_id_ai_call_logs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_api_design_suggestions_created_scenario_id_scenarios"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_design_suggestions")),
        sa.UniqueConstraint("ai_call_id", name="uq_api_design_suggestions_ai_call"),
        sa.UniqueConstraint(
            "import_id",
            "kind",
            "draft_key",
            name="uq_api_design_suggestions_import_kind_draft",
        ),
    )
    op.create_index(op.f("ix_api_design_suggestions_project_id"), _TABLE, ["project_id"])
    op.create_index(op.f("ix_api_design_suggestions_import_id"), _TABLE, ["import_id"])
    op.create_index(op.f("ix_api_design_suggestions_ai_call_id"), _TABLE, ["ai_call_id"])
    op.create_index(
        "ix_api_design_suggestions_import_created",
        _TABLE,
        ["import_id", "created_at"],
    )
    op.create_index(
        "ix_api_design_suggestions_project_status",
        _TABLE,
        ["project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
    op.drop_index(op.f("ix_test_cases_api_definition_id"), table_name="test_cases")
    op.drop_constraint(
        op.f("fk_test_cases_api_definition_id_api_definitions"),
        "test_cases",
        type_="foreignkey",
    )
    op.drop_column("test_cases", "api_definition_id")
    op.drop_column("ai_case_suggestions", "linked_requirement_ids")
