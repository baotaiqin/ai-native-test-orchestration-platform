import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260829_0026_force_stop_and_total_timeout.py"
    )
    spec = importlib.util.spec_from_file_location("force_stop_and_total_timeout", path)
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
        default = (
            str(column.server_default.arg) if column.server_default is not None else None
        )
        self.added.append((table, column.name, column.nullable, default))

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped_constraints.append((name, table, type_))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created_constraints.append((name, table, condition))

    def drop_column(self, table: str, column: str) -> None:
        self.dropped_columns.append((table, column))


def test_force_stop_and_total_timeout_migration_adds_and_rolls_back(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert operations.added == [
        (migration._TABLE, "total_timeout_ms", True, None),
        (migration._TABLE, "force_stop_requested_at", True, None),
        (migration._TABLE, "force_stopped", False, "0"),
    ]
    assert operations.created_constraints == [
        (
            migration._TIMEOUT_CONSTRAINT,
            migration._TABLE,
            "total_timeout_ms IS NULL OR total_timeout_ms >= 1000",
        ),
        (migration._FORCE_STOPPED_CONSTRAINT, migration._TABLE, "force_stopped IN (0, 1)"),
    ]

    migration.downgrade()
    assert operations.dropped_constraints == [
        (migration._FORCE_STOPPED_CONSTRAINT, migration._TABLE, "check"),
        (migration._TIMEOUT_CONSTRAINT, migration._TABLE, "check"),
    ]
    assert operations.dropped_columns == [
        (migration._TABLE, "force_stopped"),
        (migration._TABLE, "force_stop_requested_at"),
        (migration._TABLE, "total_timeout_ms"),
    ]
