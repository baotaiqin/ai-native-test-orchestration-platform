import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260828_0021_run_utc_timestamps.py"
    )
    spec = importlib.util.spec_from_file_location("run_utc_timestamps", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOperations:
    def __init__(self) -> None:
        self.alter_columns: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.statements: list[str] = []

    def alter_column(self, *args: object, **kwargs: object) -> None:
        self.alter_columns.append((args, kwargs))

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


def test_run_timestamp_migration_only_converts_audit_columns(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert len(operations.alter_columns) == 10
    assert len(operations.statements) == 5
    assert {
        (args[0], args[1]) for args, _ in operations.alter_columns
    } == {
        (table, column)
        for table in migration.RUN_AUDIT_TABLES
        for column in migration.AUDIT_TIMESTAMP_COLUMNS
    }
    assert all("DATE_SUB" in statement for statement in operations.statements)
    assert all(
        "created_at" in statement and "updated_at" in statement
        for statement in operations.statements
    )
    assert all(
        lifecycle not in " ".join(operations.statements)
        for lifecycle in migration.LIFECYCLE_TIMESTAMP_COLUMNS
    )


def test_run_timestamp_migration_downgrade_is_symmetric(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert len(operations.alter_columns) == 10
    assert len(operations.statements) == 5
    assert all("DATE_ADD" in statement for statement in operations.statements)
    assert all(
        kwargs["server_default"] is not None
        for _, kwargs in operations.alter_columns
    )
