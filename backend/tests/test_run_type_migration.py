import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260902_0030_allow_web_case_run_type.py"
    )
    spec = importlib.util.spec_from_file_location("allow_web_case_run_type", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self, *, has_web_case_rows: bool = False) -> None:
        self.dropped: list[tuple[str, str, str]] = []
        self.checks: list[tuple[str, str, str]] = []
        self.has_web_case_rows = has_web_case_rows
        self.executed_statements: list[object] = []

    class _Result:
        def __init__(self, has_rows: bool) -> None:
            self.has_rows = has_rows

        def first(self) -> tuple[int] | None:
            return (1,) if self.has_rows else None

    class _Connection:
        def __init__(self, owner: "_RecordingOperations") -> None:
            self.owner = owner

        def execute(self, statement: object) -> "_RecordingOperations._Result":
            self.owner.executed_statements.append(statement)
            return _RecordingOperations._Result(self.owner.has_web_case_rows)

    def get_bind(self) -> "_RecordingOperations._Connection":
        return self._Connection(self)

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped.append((name, table, type_))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.checks.append((name, table, condition))


def test_upgrade_replaces_run_type_check_and_allows_web_case(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260902_0030"
    assert migration.down_revision == "20260901_0029"
    assert operations.dropped == [("runs_run_type_values", "runs", "check")]
    assert operations.checks == [
        ("runs_run_type_values", "runs", migration._WITH_WEB_CASE)
    ]
    assert "'WEB_CASE'" in migration._WITH_WEB_CASE


def test_downgrade_without_web_case_restores_original_check(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert len(operations.executed_statements) == 1
    assert operations.dropped == [("runs_run_type_values", "runs", "check")]
    assert operations.checks == [
        ("runs_run_type_values", "runs", migration._WITHOUT_WEB_CASE)
    ]
    assert "'WEB_CASE'" not in migration._WITHOUT_WEB_CASE


def test_downgrade_refuses_when_web_case_rows_exist(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations(has_web_case_rows=True)
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError, match="不能 downgrade"):
        migration.downgrade()

    assert len(operations.executed_statements) == 1
    assert operations.dropped == []
    assert operations.checks == []
