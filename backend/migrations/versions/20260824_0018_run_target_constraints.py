"""加强 Run 与 CaseRun 目标版本的一致性约束。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0018"
down_revision: str | None = "20260824_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _replace_target_foreign_keys(ondelete="RESTRICT")
    op.create_check_constraint(
        "runs_target_versions_consistent",
        "runs",
        "(run_type = 'API_CASE' AND case_id IS NOT NULL AND "
        "case_version_id IS NOT NULL AND scenario_id IS NULL AND "
        "scenario_version_id IS NULL) OR "
        "(run_type = 'SCENARIO' AND scenario_id IS NOT NULL AND "
        "scenario_version_id IS NOT NULL AND case_id IS NULL AND "
        "case_version_id IS NULL)",
    )
    op.create_check_constraint(
        "case_runs_target_versions_consistent",
        "case_runs",
        "(case_id IS NOT NULL AND case_version_id IS NOT NULL AND "
        "scenario_id IS NULL AND scenario_version_id IS NULL) OR "
        "(scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL AND "
        "case_id IS NULL AND case_version_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "case_runs_target_versions_consistent", "case_runs", type_="check"
    )
    op.drop_constraint("runs_target_versions_consistent", "runs", type_="check")
    _replace_target_foreign_keys(ondelete="SET NULL")


def _replace_target_foreign_keys(*, ondelete: str) -> None:
    targets = (
        ("runs", "case_id", "test_cases", "id"),
        ("runs", "case_version_id", "test_case_versions", "id"),
        ("runs", "scenario_id", "scenarios", "id"),
        ("runs", "scenario_version_id", "scenario_versions", "id"),
        ("case_runs", "case_version_id", "test_case_versions", "id"),
        ("case_runs", "scenario_version_id", "scenario_versions", "id"),
    )
    for table, column, referred_table, referred_column in targets:
        constraint_name = f"fk_{table}_{column}_{referred_table}"
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            referred_table,
            [column],
            [referred_column],
            ondelete=ondelete,
        )
