import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_recordings.models import WebRecording


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260904_0031_web_recordings.py"
    )
    spec = importlib.util.spec_from_file_location("web_recordings_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.created_tables: dict[str, tuple[object, ...]] = {}
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []

    def f(self, name: str) -> str:
        return name

    def create_table(self, name: str, *elements: object) -> None:
        self.created_tables[name] = elements

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.created_indexes.append((name, table, tuple(columns)))

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)


def test_web_recordings_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260904_0031"
    assert migration.down_revision == "20260902_0030"
    assert WebRecording.__tablename__ in operations.created_tables
    elements = operations.created_tables[WebRecording.__tablename__]
    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    legacy_model_columns = set(WebRecording.__table__.columns.keys()) - {"plan_item_id"}
    assert set(columns) == legacy_model_columns
    for name, column in columns.items():
        model_column = WebRecording.__table__.columns[name]
        assert column.nullable == model_column.nullable
        assert type(column.type) is type(model_column.type)
        if isinstance(column.type, sa.String):
            assert column.type.length == model_column.type.length

    migration_constraints = {
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
    model_constraints = {
        element.name
        for element in WebRecording.__table__.constraints
        if element.name is not None
        and not any(column.name == "plan_item_id" for column in element.columns)
    }
    assert migration_constraints == model_constraints

    model_indexes = {
        (index.name, tuple(column.name for column in index.columns))
        for index in WebRecording.__table__.indexes
        if all(column.name != "plan_item_id" for column in index.columns)
    }
    migration_indexes = {
        (name, columns)
        for name, table, columns in operations.created_indexes
        if table == WebRecording.__tablename__
    }
    assert migration_indexes == model_indexes

    migration.downgrade()
    assert operations.dropped_tables == [WebRecording.__tablename__]
    assert operations.dropped_indexes == []
