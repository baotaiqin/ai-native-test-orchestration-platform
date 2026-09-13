import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_design.models import (
    WebDesignRevision,
    WebExploration,
    WebExplorationEvidence,
    WebTestPlan,
    WebTestPlanItem,
)


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260912_0064_web_ai_planning_and_exploration.py"
    )
    spec = importlib.util.spec_from_file_location("web_design_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _Operations:
    def __init__(self) -> None:
        self.tables: dict[str, tuple[object, ...]] = {}
        self.indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.foreign_keys: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...]]] = []
        self.drops: list[tuple[str, str]] = []

    def create_table(self, name: str, *elements: object) -> None:
        self.tables[name] = elements

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.indexes.append((name, table, tuple(columns)))

    def add_column(self, table: str, column: sa.Column) -> None:
        self.added_columns.append((table, column))

    def create_foreign_key(
        self,
        name: str,
        source: str,
        target: str,
        local: list[str],
        remote: list[str],
        **_: object,
    ) -> None:
        self.foreign_keys.append((name, source, target, tuple(local), tuple(remote)))

    def drop_table(self, name: str) -> None:
        self.drops.append(("table", name))

    def drop_index(self, name: str, **_: object) -> None:
        self.drops.append(("index", name))

    def drop_constraint(self, name: str, *_: object, **__: object) -> None:
        self.drops.append(("constraint", name))

    def drop_column(self, table: str, name: str) -> None:
        self.drops.append(("column", f"{table}.{name}"))


def test_web_design_migration_matches_models_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()

    assert migration.revision == "20260912_0064"
    assert migration.down_revision == "20260912_0063"
    models = (WebTestPlan, WebTestPlanItem, WebExploration, WebDesignRevision)
    for model in models:
        elements = operations.tables[model.__tablename__]
        columns = {element.name for element in elements if isinstance(element, sa.Column)}
        model_columns = set(model.__table__.columns.keys())
        if model is WebExploration:
            model_columns.remove("use_login_credentials")
            model_columns.remove("headless")
            model_columns.remove("permissions")
        assert columns == model_columns
        migration_names = {
            element.name
            for element in elements
            if isinstance(
                element,
                (sa.CheckConstraint, sa.UniqueConstraint),
            )
            and element.name is not None
        }
        model_names = {
            element.name
            for element in model.__table__.constraints
            if isinstance(
                element,
                (sa.CheckConstraint, sa.UniqueConstraint),
            )
            and element.name is not None
        }
        assert migration_names == model_names
        migration_indexes = {
            (name, columns)
            for name, table, columns in operations.indexes
            if table == model.__tablename__
        }
        model_indexes = {
            (index.name, tuple(column.name for column in index.columns))
            for index in model.__table__.indexes
        }
        assert migration_indexes == model_indexes

    assert [(table, column.name) for table, column in operations.added_columns] == [
        ("web_recordings", "plan_item_id")
    ]
    assert operations.foreign_keys == [
        (
            "fk_web_recordings_plan_item",
            "web_recordings",
            "web_test_plan_items",
            ("plan_item_id",),
            ("id",),
        )
    ]
    migration.downgrade()
    assert operations.drops == [
        ("table", "web_design_revisions"),
        ("index", "ix_web_recordings_plan_item_id"),
        ("constraint", "fk_web_recordings_plan_item"),
        ("column", "web_recordings.plan_item_id"),
        ("table", "web_explorations"),
        ("table", "web_test_plan_items"),
        ("table", "web_test_plans"),
    ]


def test_web_exploration_credentials_migration_is_reversible(monkeypatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260913_0067_web_exploration_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("web_exploration_credentials_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260913_0067"
    assert migration.down_revision == "20260913_0066"
    assert [(table, column.name) for table, column in operations.added_columns] == [
        ("web_explorations", "use_login_credentials")
    ]
    migration.downgrade()
    assert operations.drops == [("column", "web_explorations.use_login_credentials")]


def test_web_exploration_visual_mode_migration_is_reversible(monkeypatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260913_0069_web_exploration_visual_mode.py"
    )
    spec = importlib.util.spec_from_file_location("web_exploration_visual_mode_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260913_0069"
    assert migration.down_revision == "20260913_0068"
    assert [(table, column.name) for table, column in operations.added_columns] == [
        ("web_explorations", "headless")
    ]
    migration.downgrade()
    assert operations.drops == [("column", "web_explorations.headless")]


def test_web_exploration_permissions_migration_is_reversible(monkeypatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260913_0074_web_exploration_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("web_exploration_permissions_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260913_0074"
    assert migration.down_revision == "20260913_0073"
    assert [(table, column.name) for table, column in operations.added_columns] == [
        ("web_explorations", "permissions")
    ]
    migration.downgrade()
    assert operations.drops == [("column", "web_explorations.permissions")]


def test_web_exploration_evidence_migration_matches_model(monkeypatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260913_0076_web_exploration_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("web_exploration_evidence_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260913_0076"
    assert migration.down_revision == "20260913_0075"
    elements = operations.tables[WebExplorationEvidence.__tablename__]
    assert {item.name for item in elements if isinstance(item, sa.Column)} == set(
        WebExplorationEvidence.__table__.columns.keys()
    )
    assert {
        item.name
        for item in elements
        if isinstance(item, (sa.CheckConstraint, sa.UniqueConstraint))
    } == {
        item.name
        for item in WebExplorationEvidence.__table__.constraints
        if isinstance(item, (sa.CheckConstraint, sa.UniqueConstraint))
    }
    migration.downgrade()
    assert operations.drops[-1] == ("table", "web_exploration_evidence")
