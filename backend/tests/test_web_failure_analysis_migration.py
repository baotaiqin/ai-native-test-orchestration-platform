import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_failure_analysis.models import WebFailureAnalysis


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0034_web_failure_analyses.py"
    )
    spec = importlib.util.spec_from_file_location("web_failure_analysis_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _Operations:
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


def test_web_failure_analysis_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260908_0034"
    assert migration.down_revision == "20260908_0033"
    elements = operations.created_tables[WebFailureAnalysis.__tablename__]
    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    assert set(columns) == set(WebFailureAnalysis.__table__.columns.keys())
    for name, column in columns.items():
        model_column = WebFailureAnalysis.__table__.columns[name]
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
        for element in WebFailureAnalysis.__table__.constraints
        if element.name is not None
    }
    assert migration_constraints == model_constraints

    model_indexes = {
        (index.name, tuple(column.name for column in index.columns))
        for index in WebFailureAnalysis.__table__.indexes
    }
    migration_indexes = {
        (name, columns)
        for name, table, columns in operations.created_indexes
        if table == WebFailureAnalysis.__tablename__
    }
    assert migration_indexes == model_indexes

    migration.downgrade()
    assert operations.dropped_tables == [WebFailureAnalysis.__tablename__]
    assert operations.dropped_indexes == []
