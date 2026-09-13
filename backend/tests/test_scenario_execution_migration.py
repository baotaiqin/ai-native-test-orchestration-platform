import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.runs.models import RunScenarioExecutionResult


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260830_0027_scenario_execution_results.py"
    )
    spec = importlib.util.spec_from_file_location("scenario_execution_results", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.created_tables: list[tuple[str, tuple[object, ...]]] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, name: str, *elements: object) -> None:
        self.created_tables.append((name, elements))

    def f(self, name: str) -> str:
        return name

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.created_indexes.append((name, table, tuple(columns)))

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)


def test_scenario_execution_migration_matches_model_and_rolls_back(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260830_0027"
    assert migration.down_revision == "20260829_0026"
    assert len(operations.created_tables) == 1
    table_name, elements = operations.created_tables[0]
    assert table_name == RunScenarioExecutionResult.__tablename__

    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    model_columns = RunScenarioExecutionResult.__table__.columns
    # assertion_results is added later by migration 0045.  This test records
    # the exact schema introduced by the original 0027 migration.
    assert set(columns) == set(model_columns.keys()) - {"assertion_results"}
    for name, column in columns.items():
        model_column = model_columns[name]
        assert column.nullable == model_column.nullable
        assert type(column.type) is type(model_column.type)
        if isinstance(column.type, sa.String):
            assert column.type.length == model_column.type.length

    constraint_names = {
        element.name
        for element in elements
        if isinstance(element, (sa.CheckConstraint, sa.ForeignKeyConstraint,
                                sa.PrimaryKeyConstraint, sa.UniqueConstraint))
    }
    model_constraint_names = {
        element.name
        for element in RunScenarioExecutionResult.__table__.constraints
        if element.name is not None
    }
    assert constraint_names == model_constraint_names
    assert operations.created_indexes == [
        (
            "ix_run_scenario_execution_results_run_status_completed",
            RunScenarioExecutionResult.__tablename__,
            ("run_id", "status", "completed_at"),
        )
    ]

    migration.downgrade()
    assert operations.dropped_indexes == [
        (
            "ix_run_scenario_execution_results_run_status_completed",
            RunScenarioExecutionResult.__tablename__,
        )
    ]
    assert operations.dropped_tables == [RunScenarioExecutionResult.__tablename__]
