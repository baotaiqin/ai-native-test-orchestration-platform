import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_healing.models import WebHealingProposal


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0033_locator_healing_proposals.py"
    )
    spec = importlib.util.spec_from_file_location("locator_healing_migration", path)
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


def test_locator_healing_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260908_0033"
    assert migration.down_revision == "20260907_0032"
    elements = operations.created_tables[WebHealingProposal.__tablename__]
    columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
    # The two visual-stage fields are introduced by migration 0053, not by
    # this original proposal-table migration.
    assert set(columns) == set(WebHealingProposal.__table__.columns.keys()) - {
        "analysis_stage",
        "screenshot_artifact_id",
    }
    for name, column in columns.items():
        model_column = WebHealingProposal.__table__.columns[name]
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
        for element in WebHealingProposal.__table__.constraints
        if element.name is not None
        and element.name
        not in {
            "ck_locator_healing_proposals_locator_healing_proposals_analysis_stage_values",
            "fk_locator_healing_proposals_screenshot_artifact_id_artifacts",
        }
    }
    assert migration_constraints == model_constraints
    model_indexes = {
        (index.name, tuple(column.name for column in index.columns))
        for index in WebHealingProposal.__table__.indexes
        if index.name != "ix_locator_healing_proposals_screenshot_artifact_id"
    }
    migration_indexes = {
        (name, columns)
        for name, table, columns in operations.created_indexes
        if table == WebHealingProposal.__tablename__
    }
    assert migration_indexes == model_indexes

    migration.downgrade()
    assert operations.dropped_tables == [WebHealingProposal.__tablename__]
    assert operations.dropped_indexes == []
