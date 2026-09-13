import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260829_0025_run_retry_count.py"
    )
    spec = importlib.util.spec_from_file_location("run_retry_count", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, bool, str | None]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def add_column(self, table: str, column) -> None:
        self.added.append(
            (table, column.name, column.nullable, str(column.server_default.arg))
        )

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped_constraints.append((name, table, type_))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created_constraints.append((name, table, condition))

    def drop_column(self, table: str, column: str) -> None:
        self.dropped_columns.append((table, column))


def test_retry_count_migration_adds_bounded_default_and_rolls_back(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert operations.added == [(migration._TABLE, "retry_count", False, "0")]
    assert operations.created_constraints == [
        (
            migration._CONSTRAINT,
            migration._TABLE,
            "retry_count >= 0 AND retry_count <= 3",
        )
    ]

    migration.downgrade()
    assert operations.dropped_constraints == [
        (migration._CONSTRAINT, migration._TABLE, "check")
    ]
    assert operations.dropped_columns == [(migration._TABLE, "retry_count")]
