"""Add Web Case assets, Web Run targets, and bounded Web execution audit."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0028"
down_revision: str | None = "20260830_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT','APPROVED','ARCHIVED')",
            name="ck_web_cases_web_cases_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_web_cases_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_cases")),
        sa.UniqueConstraint("project_id", "code", name="uq_web_cases_project_code"),
    )
    op.create_index("ix_web_cases_project_id", "web_cases", ["project_id"])
    op.create_index(
        "ix_web_cases_project_status_updated", "web_cases", ["project_id", "status", "updated_at"]
    )

    op.create_table(
        "web_case_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("web_case_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version_no > 0", name="ck_web_case_versions_web_case_versions_version_positive"
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','APPROVED','RETIRED')",
            name="ck_web_case_versions_web_case_versions_status_values",
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_web_case_versions_web_case_versions_approval_metadata_required",
        ),
        sa.ForeignKeyConstraint(
            ["web_case_id"],
            ["web_cases.id"],
            name=op.f("fk_web_case_versions_web_case_id_web_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_case_versions")),
        sa.UniqueConstraint("web_case_id", "version_no", name="uq_web_case_versions_case_version"),
    )
    op.create_index("ix_web_case_versions_web_case_id", "web_case_versions", ["web_case_id"])
    op.create_index(
        "ix_web_case_versions_case_created", "web_case_versions", ["web_case_id", "created_at"]
    )
    op.create_index(
        "ix_web_case_versions_case_status", "web_case_versions", ["web_case_id", "status"]
    )
    op.create_foreign_key(
        "fk_web_cases_current_version_id_web_case_versions",
        "web_cases",
        "web_case_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "web_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url_pattern", sa.String(length=2048), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ARCHIVED')", name="ck_web_pages_web_pages_status_values"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_web_pages_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_pages")),
        sa.UniqueConstraint("project_id", "code", name="uq_web_pages_project_code"),
    )
    op.create_index("ix_web_pages_project_id", "web_pages", ["project_id"])
    op.create_index("ix_web_pages_project_status", "web_pages", ["project_id", "status"])

    op.create_table(
        "web_elements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("element_type", sa.String(length=32), nullable=False, server_default="OTHER"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ARCHIVED')", name="ck_web_elements_web_elements_status_values"
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["web_pages.id"],
            name=op.f("fk_web_elements_page_id_web_pages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_web_elements_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_elements")),
        sa.UniqueConstraint("page_id", "name", name="uq_web_elements_page_name"),
    )
    op.create_index("ix_web_elements_project_id", "web_elements", ["project_id"])
    op.create_index("ix_web_elements_page_id", "web_elements", ["page_id"])
    op.create_index("ix_web_elements_project_status", "web_elements", ["project_id", "status"])

    op.create_table(
        "web_element_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("element_type", sa.String(length=32), nullable=False, server_default="OTHER"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version_no > 0",
            name="ck_web_element_versions_web_element_versions_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["element_id"],
            ["web_elements.id"],
            name=op.f("fk_web_element_versions_element_id_web_elements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_element_versions")),
        sa.UniqueConstraint(
            "element_id", "version_no", name="uq_web_element_versions_element_version"
        ),
    )
    op.create_index("ix_web_element_versions_element_id", "web_element_versions", ["element_id"])
    op.create_index(
        "ix_web_element_versions_element_created",
        "web_element_versions",
        ["element_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_web_elements_current_version_id_web_element_versions",
        "web_elements",
        "web_element_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "element_locators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("element_version_id", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=2000), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="MANUAL"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "priority > 0 AND priority <= 20",
            name="ck_element_locators_element_locators_priority_bounds",
        ),
        sa.CheckConstraint(
            "strategy IN ('css','xpath','text','role','label','placeholder','test_id')",
            name="ck_element_locators_element_locators_strategy_values",
        ),
        sa.CheckConstraint(
            "source IN ('MANUAL','IMPORTED','HEALED')",
            name="ck_element_locators_element_locators_source_values",
        ),
        sa.ForeignKeyConstraint(
            ["element_version_id"],
            ["web_element_versions.id"],
            name=op.f("fk_element_locators_element_version_id_web_element_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_element_locators")),
        sa.UniqueConstraint(
            "element_version_id", "priority", name="uq_element_locators_version_priority"
        ),
    )
    op.create_index(
        "ix_element_locators_element_version_id", "element_locators", ["element_version_id"]
    )
    op.create_index(
        "ix_element_locators_version_priority",
        "element_locators",
        ["element_version_id", "priority"],
    )

    op.create_table(
        "session_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("storage_state_ciphertext", sa.Text(), nullable=False),
        sa.Column("storage_state_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ARCHIVED')",
            name="ck_session_profiles_session_profiles_status_values",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_session_profiles_session_profiles_expiry_after_create",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_session_profiles_environment_id_environments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_session_profiles_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_profiles")),
        sa.UniqueConstraint("project_id", "name", name="uq_session_profiles_project_name"),
    )
    op.create_index("ix_session_profiles_project_id", "session_profiles", ["project_id"])
    op.create_index("ix_session_profiles_environment_id", "session_profiles", ["environment_id"])
    op.create_index(
        "ix_session_profiles_project_environment_status",
        "session_profiles",
        ["project_id", "environment_id", "status"],
    )

    op.drop_constraint("runs_target_versions_consistent", "runs", type_="check")
    op.add_column("runs", sa.Column("web_case_id", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("web_case_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_runs_web_case_id_web_cases",
        "runs",
        "web_cases",
        ["web_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_runs_web_case_version_id_web_case_versions",
        "runs",
        "web_case_versions",
        ["web_case_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_runs_web_case_id", "runs", ["web_case_id"])
    op.create_check_constraint(
        "runs_target_versions_consistent",
        "runs",
        "(run_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
        "AND scenario_id IS NULL AND scenario_version_id IS NULL AND web_case_id IS NULL "
        "AND web_case_version_id IS NULL) OR "
        "(run_type = 'SCENARIO' AND scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL "
        "AND case_id IS NULL AND case_version_id IS NULL AND web_case_id IS NULL "
        "AND web_case_version_id IS NULL) OR "
        "(run_type = 'WEB_CASE' AND web_case_id IS NOT NULL AND web_case_version_id IS NOT NULL "
        "AND case_id IS NULL AND case_version_id IS NULL AND scenario_id IS NULL "
        "AND scenario_version_id IS NULL)",
    )
    op.drop_constraint("case_runs_target_exclusive", "case_runs", type_="check")
    op.drop_constraint("case_runs_target_versions_consistent", "case_runs", type_="check")
    op.add_column("case_runs", sa.Column("web_case_id", sa.Integer(), nullable=True))
    op.add_column("case_runs", sa.Column("web_case_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_case_runs_web_case_id_web_cases",
        "case_runs",
        "web_cases",
        ["web_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_case_runs_web_case_version_id_web_case_versions",
        "case_runs",
        "web_case_versions",
        ["web_case_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "case_runs_target_exclusive",
        "case_runs",
        "(case_id IS NOT NULL AND scenario_id IS NULL AND web_case_id IS NULL) OR "
        "(case_id IS NULL AND scenario_id IS NOT NULL AND web_case_id IS NULL) OR "
        "(case_id IS NULL AND scenario_id IS NULL AND web_case_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "case_runs_target_versions_consistent",
        "case_runs",
        "(case_id IS NOT NULL AND case_version_id IS NOT NULL AND scenario_id IS NULL "
        "AND scenario_version_id IS NULL AND web_case_id IS NULL "
        "AND web_case_version_id IS NULL) OR "
        "(scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL AND case_id IS NULL "
        "AND case_version_id IS NULL AND web_case_id IS NULL "
        "AND web_case_version_id IS NULL) OR "
        "(web_case_id IS NOT NULL AND web_case_version_id IS NOT NULL AND case_id IS NULL "
        "AND case_version_id IS NULL AND scenario_id IS NULL "
        "AND scenario_version_id IS NULL)",
    )

    op.create_table(
        "run_web_execution_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("case_run_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("traces", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="ck_run_web_execution_results_run_web_execution_results_outcome_values",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILED','TIMEOUT','CANCELLED')",
            name="ck_run_web_execution_results_run_web_execution_results_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_web_execution_results_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_run_id"],
            ["case_runs.id"],
            name=op.f("fk_run_web_execution_results_case_run_id_case_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_web_execution_results")),
        sa.UniqueConstraint("run_id", "case_run_id", name="uq_run_web_execution_results_run_case"),
        sa.UniqueConstraint("message_id", name="uq_run_web_execution_results_message"),
    )
    op.create_index(
        "ix_run_web_execution_results_run_status_completed",
        "run_web_execution_results",
        ["run_id", "status", "completed_at"],
    )


def downgrade() -> None:
    # Remove child-table dependencies before dropping supporting indexes or parent tables.
    op.drop_constraint(
        "fk_run_web_execution_results_run_id_runs",
        "run_web_execution_results",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_run_web_execution_results_case_run_id_case_runs",
        "run_web_execution_results",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_run_web_execution_results_run_status_completed", table_name="run_web_execution_results"
    )
    op.drop_table("run_web_execution_results")
    op.drop_constraint("case_runs_target_exclusive", "case_runs", type_="check")
    op.drop_constraint("case_runs_target_versions_consistent", "case_runs", type_="check")
    op.drop_constraint(
        "fk_case_runs_web_case_version_id_web_case_versions", "case_runs", type_="foreignkey"
    )
    op.drop_constraint("fk_case_runs_web_case_id_web_cases", "case_runs", type_="foreignkey")
    op.drop_column("case_runs", "web_case_version_id")
    op.drop_column("case_runs", "web_case_id")
    op.create_check_constraint(
        "case_runs_target_versions_consistent",
        "case_runs",
        "(case_id IS NOT NULL AND case_version_id IS NOT NULL AND scenario_id IS NULL "
        "AND scenario_version_id IS NULL) OR "
        "(scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL "
        "AND case_id IS NULL AND case_version_id IS NULL)",
    )
    op.create_check_constraint(
        "case_runs_target_exclusive",
        "case_runs",
        "(case_id IS NOT NULL AND scenario_id IS NULL) OR "
        "(case_id IS NULL AND scenario_id IS NOT NULL)",
    )
    op.drop_constraint("runs_target_versions_consistent", "runs", type_="check")
    op.drop_constraint("fk_runs_web_case_version_id_web_case_versions", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_web_case_id_web_cases", "runs", type_="foreignkey")
    op.drop_index("ix_runs_web_case_id", table_name="runs")
    op.drop_column("runs", "web_case_version_id")
    op.drop_column("runs", "web_case_id")
    op.create_check_constraint(
        "runs_target_versions_consistent",
        "runs",
        "(run_type = 'API_CASE' AND case_id IS NOT NULL AND case_version_id IS NOT NULL "
        "AND scenario_id IS NULL AND scenario_version_id IS NULL) OR "
        "(run_type = 'SCENARIO' AND scenario_id IS NOT NULL AND scenario_version_id IS NOT NULL "
        "AND case_id IS NULL AND case_version_id IS NULL)",
    )
    op.drop_constraint(
        "fk_session_profiles_environment_id_environments",
        "session_profiles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_session_profiles_project_id_projects",
        "session_profiles",
        type_="foreignkey",
    )
    for index_name, table_name in (
        ("ix_session_profiles_project_environment_status", "session_profiles"),
        ("ix_session_profiles_environment_id", "session_profiles"),
        ("ix_session_profiles_project_id", "session_profiles"),
    ):
        op.drop_index(index_name, table_name=table_name)
    op.drop_table("session_profiles")
    op.drop_constraint(
        "fk_element_locators_element_version_id_web_element_versions",
        "element_locators",
        type_="foreignkey",
    )
    op.drop_index("ix_element_locators_version_priority", table_name="element_locators")
    op.drop_index("ix_element_locators_element_version_id", table_name="element_locators")
    op.drop_table("element_locators")
    op.drop_constraint(
        "fk_web_elements_current_version_id_web_element_versions",
        "web_elements",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_web_element_versions_element_id_web_elements",
        "web_element_versions",
        type_="foreignkey",
    )
    op.drop_index("ix_web_element_versions_element_created", table_name="web_element_versions")
    op.drop_index("ix_web_element_versions_element_id", table_name="web_element_versions")
    op.drop_table("web_element_versions")
    op.drop_constraint("fk_web_elements_page_id_web_pages", "web_elements", type_="foreignkey")
    op.drop_constraint("fk_web_elements_project_id_projects", "web_elements", type_="foreignkey")
    for index_name in (
        "ix_web_elements_project_status",
        "ix_web_elements_page_id",
        "ix_web_elements_project_id",
    ):
        op.drop_index(index_name, table_name="web_elements")
    op.drop_table("web_elements")
    op.drop_constraint("fk_web_pages_project_id_projects", "web_pages", type_="foreignkey")
    op.drop_index("ix_web_pages_project_status", table_name="web_pages")
    op.drop_index("ix_web_pages_project_id", table_name="web_pages")
    op.drop_table("web_pages")
    op.drop_constraint(
        "fk_web_case_versions_web_case_id_web_cases",
        "web_case_versions",
        type_="foreignkey",
    )
    op.drop_index("ix_web_case_versions_case_created", table_name="web_case_versions")
    op.drop_index("ix_web_case_versions_case_status", table_name="web_case_versions")
    op.drop_index("ix_web_case_versions_web_case_id", table_name="web_case_versions")
    op.drop_constraint(
        "fk_web_cases_current_version_id_web_case_versions",
        "web_cases",
        type_="foreignkey",
    )
    op.drop_table("web_case_versions")
    op.drop_constraint("fk_web_cases_project_id_projects", "web_cases", type_="foreignkey")
    op.drop_index("ix_web_cases_project_status_updated", table_name="web_cases")
    op.drop_index("ix_web_cases_project_id", table_name="web_cases")
    op.drop_table("web_cases")
