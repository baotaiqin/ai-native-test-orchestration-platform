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
        / "20260909_0037_model_catalog_metadata.py"
    )
    spec = importlib.util.spec_from_file_location("model_catalog_metadata", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.added: list[sa.Column] = []
        self.altered: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.checks: list[tuple[str, str, str]] = []
        self.dropped: list[tuple[str, str]] = []
        self.executed: list[str] = []

    def add_column(self, _table: str, column: sa.Column) -> None:
        self.added.append(column)

    def alter_column(self, *args: object, **kwargs: object) -> None:
        self.altered.append((args, kwargs))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.checks.append((name, table, condition))

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        assert table == "model_configurations"
        assert type_ == "check"
        self.dropped.append(("constraint", name))

    def drop_column(self, table: str, column: str) -> None:
        self.dropped.append((table, column))

    def execute(self, statement: object) -> None:
        self.executed.append(str(statement))


def test_model_catalog_migration_matches_model_and_rolls_back(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260909_0037"
    assert migration.down_revision == "20260908_0036"
    model_columns = ModelConfiguration.__table__.columns
    added = {column.name: column for column in operations.added}
    expected = {
        "metadata_source",
        "metadata_synced_at",
        "description",
        "supports_reasoning",
        "max_input_tokens",
        "max_output_tokens",
        "max_reasoning_tokens",
        "reasoning_max_input_tokens",
        "reasoning_max_output_tokens",
        "input_modalities",
        "output_modalities",
        "capabilities",
        "features",
        "pricing_tiers",
        "published_at",
    }
    assert set(added) == expected
    for name in expected:
        assert added[name].nullable == model_columns[name].nullable
        assert type(added[name].type) is type(model_columns[name].type)
        if isinstance(added[name].type, sa.String):
            assert added[name].type.length == model_columns[name].type.length

    model_checks = set(migration._CHECKS)
    assert {name for name, _table, _condition in operations.checks} == model_checks
    assert all(len(name) <= 64 for name in model_checks)
    assert operations.checks == [
        (
            name,
            "model_configurations",
            f"{column} IS NULL OR {column} >= 0",
        )
        for name, column in zip(
            migration._CHECKS,
            (
                "max_input_tokens",
                "max_output_tokens",
                "max_reasoning_tokens",
                "reasoning_max_input_tokens",
                "reasoning_max_output_tokens",
            ),
            strict=True,
        )
    ]
    upgrade_max_context = operations.altered[0][1]
    assert upgrade_max_context["nullable"] is True
    assert str(upgrade_max_context["existing_server_default"]) == "128000"
    assert upgrade_max_context["server_default"] is None

    migration.downgrade()
    downgrade_max_context = next(
        kwargs
        for args, kwargs in operations.altered
        if args[:2] == ("model_configurations", "max_context")
        and kwargs["nullable"] is False
    )
    assert downgrade_max_context["nullable"] is False
    assert downgrade_max_context["existing_server_default"] is None
    assert str(downgrade_max_context["server_default"]) == "128000"
    assert operations.executed
    assert operations.dropped[:5] == [("constraint", name) for name in migration._CHECKS]
    assert [column for table, column in operations.dropped[5:]] == [
        "published_at",
        "pricing_tiers",
        "features",
        "capabilities",
        "output_modalities",
        "input_modalities",
        "reasoning_max_output_tokens",
        "reasoning_max_input_tokens",
        "max_reasoning_tokens",
        "max_output_tokens",
        "max_input_tokens",
        "supports_reasoning",
        "description",
        "metadata_synced_at",
        "metadata_source",
    ]

    metadata = sa.MetaData()
    legacy_models = sa.Table(
        "legacy_model_configurations",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "max_context",
            sa.Integer(),
            nullable=False,
            server_default=downgrade_max_context["server_default"],
        ),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(legacy_models.insert().values(id=1))
        assert connection.scalar(
            sa.select(legacy_models.c.max_context).where(legacy_models.c.id == 1)
        ) == 128000
    engine.dispose()
