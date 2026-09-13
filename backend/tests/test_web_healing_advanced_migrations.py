import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from app.modules.web_healing.models import WebHealingValidation


def _load(filename: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _Operations:
    def __init__(self) -> None:
        self.created_tables: dict[str, tuple[object, ...]] = {}
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.added_columns: list[tuple[str, sa.Column[object]]] = []
        self.created_checks: list[tuple[str, str, str]] = []
        self.created_foreign_keys: list[
            tuple[str, str, str, tuple[str, ...], tuple[str, ...], str | None]
        ] = []
        self.downgrade: list[tuple[str, str, str | None]] = []

    def f(self, name: str) -> str:
        return name

    def create_table(self, name: str, *elements: object) -> None:
        self.created_tables[name] = elements

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.created_indexes.append((name, table, tuple(columns)))

    def add_column(self, table: str, column: sa.Column[object]) -> None:
        self.added_columns.append((table, column))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created_checks.append((name, table, condition))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        *,
        ondelete: str | None = None,
    ) -> None:
        self.created_foreign_keys.append(
            (
                name,
                source_table,
                referent_table,
                tuple(local_cols),
                tuple(remote_cols),
                ondelete,
            )
        )

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.downgrade.append(("index", name, table_name))

    def drop_constraint(
        self, name: str, table: str, *, type_: str | None = None
    ) -> None:
        self.downgrade.append((type_ or "constraint", name, table))

    def drop_column(self, table: str, name: str) -> None:
        self.downgrade.append(("column", name, table))

    def drop_table(self, name: str) -> None:
        self.downgrade.append(("table", name, None))


def test_locator_healing_validation_migration_matches_model(monkeypatch) -> None:
    migration = _load("20260911_0052_locator_healing_validations.py")
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260911_0052"
    assert migration.down_revision == "20260911_0051"
    elements = operations.created_tables[WebHealingValidation.__tablename__]
    columns = {item.name: item for item in elements if isinstance(item, sa.Column)}
    model_columns = WebHealingValidation.__table__.columns
    assert set(columns) == set(model_columns.keys())
    for name, column in columns.items():
        assert column.nullable == model_columns[name].nullable
        assert type(column.type) is type(model_columns[name].type)
        if isinstance(column.type, sa.String):
            assert column.type.length == model_columns[name].type.length

    expected_indexes = {
        (index.name, tuple(column.name for column in index.columns))
        for index in WebHealingValidation.__table__.indexes
    }
    actual_indexes = {
        (name, fields)
        for name, table, fields in operations.created_indexes
        if table == WebHealingValidation.__tablename__
    }
    assert actual_indexes == expected_indexes

    migration.downgrade()
    assert operations.downgrade == [
        ("table", WebHealingValidation.__tablename__, None)
    ]


def test_locator_healing_visual_stage_migration_is_reversible(monkeypatch) -> None:
    migration = _load("20260911_0053_locator_healing_visual_stages.py")
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260911_0053"
    assert migration.down_revision == "20260911_0052"
    assert [(table, column.name) for table, column in operations.added_columns] == [
        ("locator_healing_proposals", "analysis_stage"),
        ("locator_healing_proposals", "screenshot_artifact_id"),
    ]
    analysis_stage = operations.added_columns[0][1]
    assert isinstance(analysis_stage.type, sa.String)
    assert analysis_stage.type.length == 24
    assert analysis_stage.nullable is False
    assert str(analysis_stage.server_default.arg) == "LLM_DOM"
    assert operations.created_checks == [
        (
            "ck_locator_healing_proposals_locator_healing_proposals_analysis_stage_values",
            "locator_healing_proposals",
            "analysis_stage IN ('LLM_DOM','VISION_SCREENSHOT','AGENT_LOCATE')",
        )
    ]
    assert operations.created_foreign_keys == [
        (
            "fk_locator_healing_proposals_screenshot_artifact_id_artifacts",
            "locator_healing_proposals",
            "artifacts",
            ("screenshot_artifact_id",),
            ("id",),
            "RESTRICT",
        )
    ]
    assert operations.created_indexes == [
        (
            "ix_locator_healing_proposals_screenshot_artifact_id",
            "locator_healing_proposals",
            ("screenshot_artifact_id",),
        )
    ]

    migration.downgrade()
    assert operations.downgrade == [
        (
            "index",
            "ix_locator_healing_proposals_screenshot_artifact_id",
            "locator_healing_proposals",
        ),
        (
            "foreignkey",
            "fk_locator_healing_proposals_screenshot_artifact_id_artifacts",
            "locator_healing_proposals",
        ),
        (
            "check",
            "ck_locator_healing_proposals_locator_healing_proposals_analysis_stage_values",
            "locator_healing_proposals",
        ),
        ("column", "screenshot_artifact_id", "locator_healing_proposals"),
        ("column", "analysis_stage", "locator_healing_proposals"),
    ]
