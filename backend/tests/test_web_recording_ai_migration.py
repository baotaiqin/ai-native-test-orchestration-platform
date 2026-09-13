import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_recording_ai.models import WebRecordingAiSuggestion


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260907_0032_web_recording_ai_suggestions.py"
    )
    spec = importlib.util.spec_from_file_location("web_recording_ai_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _SuggestionOperations:
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


def test_web_recording_ai_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _SuggestionOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260907_0032"
    assert migration.down_revision == "20260904_0031"
    assert WebRecordingAiSuggestion.__tablename__ in operations.created_tables
    elements = operations.created_tables[WebRecordingAiSuggestion.__tablename__]
    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    assert set(columns) == set(WebRecordingAiSuggestion.__table__.columns.keys())
    for name, column in columns.items():
        model_column = WebRecordingAiSuggestion.__table__.columns[name]
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
        for element in WebRecordingAiSuggestion.__table__.constraints
        if element.name is not None
    }
    assert migration_constraints == model_constraints
    model_indexes = {
        (index.name, tuple(column.name for column in index.columns))
        for index in WebRecordingAiSuggestion.__table__.indexes
    }
    migration_indexes = {
        (name, columns)
        for name, table, columns in operations.created_indexes
        if table == WebRecordingAiSuggestion.__tablename__
    }
    assert migration_indexes == model_indexes

    migration.downgrade()
    assert operations.dropped_tables == [WebRecordingAiSuggestion.__tablename__]
    assert operations.dropped_indexes == []
