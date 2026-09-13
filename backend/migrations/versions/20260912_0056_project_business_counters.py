"""Add project-scoped business code counters."""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0056"
down_revision: str | None = "20260912_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTER_TABLE = "project_business_counters"
_SOURCES = (
    ("REQUIREMENT", "requirements", "REQ"),
    ("TEST_CASE", "test_cases", "TC"),
    ("SCENARIO", "scenarios", "SC"),
    ("WEB_CASE", "web_cases", "WC"),
)


def _next_value(codes: list[str], prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    values = [
        int(match.group(1))
        for code in codes
        if (match := pattern.fullmatch(str(code))) is not None
    ]
    return max(values, default=0) + 1


def upgrade() -> None:
    op.create_table(
        _COUNTER_TABLE,
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=32), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "next_value > 0", name=op.f("ck_project_business_counters_next_value")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_business_counters_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "project_id", "namespace", name=op.f("pk_project_business_counters")
        ),
    )

    bind = op.get_bind()
    project_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM projects"))]
    for project_id in project_ids:
        for namespace, table_name, prefix in _SOURCES:
            codes = [
                row[0]
                for row in bind.execute(
                    sa.text(f"SELECT code FROM {table_name} WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
            ]
            bind.execute(
                sa.text(
                    "INSERT INTO project_business_counters "
                    "(project_id, namespace, next_value) "
                    "VALUES (:project_id, :namespace, :next_value)"
                ),
                {
                    "project_id": project_id,
                    "namespace": namespace,
                    "next_value": _next_value(codes, prefix),
                },
            )


def downgrade() -> None:
    op.drop_table(_COUNTER_TABLE)
