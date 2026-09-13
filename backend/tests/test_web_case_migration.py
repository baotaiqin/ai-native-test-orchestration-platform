import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.runs.models import RunWebExecutionResult
from app.modules.web_cases.models import WebCaseVersion


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260831_0028_web_case_assets_and_execution.py"
    )
    spec = importlib.util.spec_from_file_location("web_case_assets_and_execution", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.created_tables: dict[str, tuple[object, ...]] = {}
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.created_foreign_keys: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.created_checks: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.downgrade_operations: list[tuple[str, str, str | None]] = []
        self.dropped_tables: list[str] = []

    def f(self, name: str) -> str:
        return name

    def create_table(self, name: str, *elements: object) -> None:
        self.created_tables[name] = elements

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.created_indexes.append((name, table, tuple(columns)))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        _remote_cols: list[str],
        **_kwargs: object,
    ) -> None:
        self.created_foreign_keys.append((name, source_table, referent_table, tuple(local_cols)))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created_checks.append((name, table, condition))

    def drop_constraint(self, *args: object, **kwargs: object) -> None:
        self.dropped_constraints.append((args, kwargs))
        if len(args) >= 2:
            self.downgrade_operations.append(("constraint", str(args[0]), str(args[1])))
        return None

    def add_column(self, *_args: object, **_kwargs: object) -> None:
        return None

    def drop_column(self, *args: object, **_kwargs: object) -> None:
        if len(args) >= 2:
            self.downgrade_operations.append(("column", str(args[1]), str(args[0])))
        return None

    def drop_index(self, *args: object, **kwargs: object) -> None:
        if args:
            table = kwargs.get("table_name")
            self.downgrade_operations.append(("index", str(args[0]), str(table)))
        return None

    def drop_table(self, name: str) -> None:
        self.downgrade_operations.append(("table", name, None))
        self.dropped_tables.append(name)


def test_web_case_migration_matches_model_and_has_reversible_operations(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260831_0028"
    assert migration.down_revision == "20260830_0027"
    assert {
        "web_cases",
        "web_case_versions",
        "web_pages",
        "web_elements",
        "web_element_versions",
        "element_locators",
        "session_profiles",
        "run_web_execution_results",
    } <= set(operations.created_tables)

    elements = operations.created_tables[RunWebExecutionResult.__tablename__]
    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    model_columns = RunWebExecutionResult.__table__.columns
    # session_recovery and assertion_results are introduced by 0048 and 0050.
    assert set(columns) == set(model_columns.keys()) - {
        "session_recovery",
        "assertion_results",
    }
    for name, column in columns.items():
        model_column = model_columns[name]
        assert column.nullable == model_column.nullable
        assert type(column.type) is type(model_column.type)
        if isinstance(column.type, sa.String):
            assert column.type.length == model_column.type.length

    constraint_names = {
        element.name
        for element in elements
        if isinstance(
            element,
            (
                sa.CheckConstraint,
                sa.ForeignKeyConstraint,
                sa.PrimaryKeyConstraint,
                sa.UniqueConstraint,
            ),
        )
    }
    model_constraint_names = {
        element.name
        for element in RunWebExecutionResult.__table__.constraints
        if element.name is not None
    }
    assert constraint_names == model_constraint_names
    assert ("ix_run_web_execution_results_run_status_completed", "run_web_execution_results", (
        "run_id",
        "status",
        "completed_at",
    )) in operations.created_indexes

    version_elements = operations.created_tables[WebCaseVersion.__tablename__]
    version_columns = {
        element.name: element for element in version_elements if isinstance(element, sa.Column)
    }
    assert {"status", "approved_by", "approved_at"} <= set(version_columns)
    version_constraint_names = {
        element.name
        for element in version_elements
        if isinstance(element, sa.CheckConstraint)
    }
    model_check_names = {
        element.name
        for element in WebCaseVersion.__table__.constraints
        if isinstance(element, sa.CheckConstraint)
    }
    assert version_constraint_names == model_check_names
    assert ("ix_web_case_versions_case_status", "web_case_versions", (
        "web_case_id",
        "status",
    )) in operations.created_indexes
    assert (
        "fk_web_cases_current_version_id_web_case_versions",
        "web_cases",
        "web_case_versions",
        ("current_version_id",),
    ) in operations.created_foreign_keys
    assert (
        "fk_web_elements_current_version_id_web_element_versions",
        "web_elements",
        "web_element_versions",
        ("current_version_id",),
    ) in operations.created_foreign_keys

    migration.downgrade()
    assert "run_web_execution_results" in operations.dropped_tables
    dropped_constraint_names = {
        args[0] for args, _kwargs in operations.dropped_constraints if args
    }
    assert {
        "fk_web_cases_current_version_id_web_case_versions",
        "fk_web_elements_current_version_id_web_element_versions",
    } <= dropped_constraint_names

    def position(kind: str, name: str, table: str | None = None) -> int:
        for index, operation in enumerate(operations.downgrade_operations):
            if operation == (kind, name, table):
                return index
        raise AssertionError(f"missing downgrade operation: {kind}/{name}/{table}")

    def assert_before(
        constraint: str,
        table: str,
        dependent_operations: list[tuple[str, str, str | None]],
    ) -> None:
        constraint_position = position("constraint", constraint, table)
        for kind, name, dependent_table in dependent_operations:
            assert constraint_position < position(kind, name, dependent_table)

    assert_before(
        "fk_run_web_execution_results_run_id_runs",
            "run_web_execution_results",
            [
                (
                    "index",
                    "ix_run_web_execution_results_run_status_completed",
                    "run_web_execution_results",
                ),
                ("table", "run_web_execution_results", None),
            ],
    )
    assert_before(
        "fk_run_web_execution_results_case_run_id_case_runs",
        "run_web_execution_results",
        [
            (
                "index",
                "ix_run_web_execution_results_run_status_completed",
                "run_web_execution_results",
            )
        ],
    )
    assert_before(
        "fk_runs_web_case_id_web_cases",
        "runs",
        [
            ("index", "ix_runs_web_case_id", "runs"),
            ("column", "web_case_id", "runs"),
        ],
    )
    assert_before(
        "fk_runs_web_case_version_id_web_case_versions",
        "runs",
        [("column", "web_case_version_id", "runs")],
    )
    assert_before(
        "fk_case_runs_web_case_id_web_cases",
        "case_runs",
        [("column", "web_case_id", "case_runs")],
    )
    assert_before(
        "fk_case_runs_web_case_version_id_web_case_versions",
        "case_runs",
        [("column", "web_case_version_id", "case_runs")],
    )
    assert_before(
        "fk_session_profiles_environment_id_environments",
        "session_profiles",
        [
            ("index", "ix_session_profiles_environment_id", "session_profiles"),
            ("table", "session_profiles", None),
        ],
    )
    assert_before(
        "fk_session_profiles_project_id_projects",
        "session_profiles",
        [
            ("index", "ix_session_profiles_project_id", "session_profiles"),
            ("table", "session_profiles", None),
        ],
    )
    assert_before(
        "fk_element_locators_element_version_id_web_element_versions",
        "element_locators",
        [
            ("index", "ix_element_locators_element_version_id", "element_locators"),
            ("table", "element_locators", None),
        ],
    )
    assert_before(
        "fk_web_elements_current_version_id_web_element_versions",
        "web_elements",
        [
            ("table", "web_element_versions", None),
            ("table", "web_elements", None),
        ],
    )
    assert_before(
        "fk_web_elements_page_id_web_pages",
        "web_elements",
        [
            ("index", "ix_web_elements_page_id", "web_elements"),
            ("table", "web_elements", None),
        ],
    )
    assert_before(
        "fk_web_elements_project_id_projects",
        "web_elements",
        [
            ("index", "ix_web_elements_project_id", "web_elements"),
            ("table", "web_elements", None),
        ],
    )
    assert_before(
        "fk_web_element_versions_element_id_web_elements",
        "web_element_versions",
        [
            ("index", "ix_web_element_versions_element_id", "web_element_versions"),
            ("table", "web_element_versions", None),
        ],
    )
    assert_before(
        "fk_web_pages_project_id_projects",
        "web_pages",
        [
            ("index", "ix_web_pages_project_id", "web_pages"),
            ("table", "web_pages", None),
        ],
    )
    assert_before(
        "fk_web_cases_current_version_id_web_case_versions",
        "web_cases",
        [
            ("table", "web_case_versions", None),
            ("table", "web_cases", None),
        ],
    )
    assert_before(
        "fk_web_case_versions_web_case_id_web_cases",
        "web_case_versions",
        [
            ("index", "ix_web_case_versions_web_case_id", "web_case_versions"),
            ("table", "web_case_versions", None),
        ],
    )
    assert_before(
        "fk_web_cases_project_id_projects",
        "web_cases",
        [
            ("index", "ix_web_cases_project_id", "web_cases"),
            ("table", "web_cases", None),
        ],
    )
