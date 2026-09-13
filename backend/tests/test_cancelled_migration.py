import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260829_0024_cancelled_execution_outcome.py"
    )
    spec = importlib.util.spec_from_file_location("cancelled_execution_outcome", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.dropped: list[tuple[str, str, str]] = []
        self.created: list[tuple[str, str, str]] = []

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped.append((name, table, type_))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created.append((name, table, condition))


def test_cancelled_migration_extends_both_checks_and_downgrades_symmetrically(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert operations.dropped == [
        (migration._OUTCOME_CONSTRAINT, migration._TABLE, "check"),
        (migration._STATUS_CONSTRAINT, migration._TABLE, "check"),
    ]
    assert operations.created == [
        (migration._OUTCOME_CONSTRAINT, migration._TABLE, migration._WITH_CANCELLED),
        (migration._STATUS_CONSTRAINT, migration._TABLE, migration._STATUS_WITH_CANCELLED),
    ]
    assert "CANCELLED" in migration._WITH_CANCELLED
    assert "CANCELLED" in migration._STATUS_WITH_CANCELLED

    operations.dropped.clear()
    operations.created.clear()
    migration.downgrade()
    assert operations.dropped == [
        (migration._STATUS_CONSTRAINT, migration._TABLE, "check"),
        (migration._OUTCOME_CONSTRAINT, migration._TABLE, "check"),
    ]
    assert operations.created == [
        (
            migration._STATUS_CONSTRAINT,
            migration._TABLE,
            migration._STATUS_WITHOUT_CANCELLED,
        ),
        (migration._OUTCOME_CONSTRAINT, migration._TABLE, migration._WITHOUT_CANCELLED),
    ]
