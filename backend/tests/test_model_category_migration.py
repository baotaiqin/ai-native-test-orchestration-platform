import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.model_center.models import ModelConfiguration


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260909_0038_model_category.py"
    )
    spec = importlib.util.spec_from_file_location("model_category", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.added: list[sa.Column] = []
        self.checks: list[tuple[str, str, str]] = []
        self.dropped: list[tuple[str, str]] = []

    def add_column(self, _table: str, column: sa.Column) -> None:
        self.added.append(column)

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.checks.append((name, table, condition))

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        assert table == "model_configurations"
        assert type_ == "check"
        self.dropped.append(("constraint", name))

    def drop_column(self, table: str, column: str) -> None:
        self.dropped.append((table, column))


def test_model_category_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260909_0038"
    assert migration.down_revision == "20260909_0037"
    assert len(operations.added) == 1
    column = operations.added[0]
    model_column = ModelConfiguration.__table__.columns["model_category"]
    assert column.name == "model_category"
    assert column.nullable is True
    assert model_column.nullable is True
    assert type(column.type) is type(model_column.type)
    assert isinstance(column.type, sa.String)
    assert column.type.length == model_column.type.length == 32
    assert operations.checks == [
        (
            migration._CHECK,
            "model_configurations",
            "model_category IS NULL OR model_category IN "
            "('LLM','VISION','OMNI','AUDIO','EMBEDDING','IMAGE_GENERATION',"
            "'VIDEO_GENERATION','THREE_D','OTHER')",
        )
    ]
    assert len(migration._CHECK) <= 64

    migration.downgrade()
    assert operations.dropped == [
        ("constraint", migration._CHECK),
        ("model_configurations", "model_category"),
    ]
