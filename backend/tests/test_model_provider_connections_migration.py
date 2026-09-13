import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import Select

from app.modules.model_center.models import ModelConfiguration, ModelProviderConnection


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0036_model_provider_connections.py"
    )
    spec = importlib.util.spec_from_file_location("model_provider_connections", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _EmptyResult:
    def mappings(self) -> list[dict[str, object]]:
        return []


class _Bind:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, _statement: object) -> _EmptyResult:
        self.statements.append(_statement)
        if isinstance(_statement, TextClause):
            return _EmptyResult()
        _statement.compile(dialect=sqlite.dialect())
        if isinstance(_statement, Select):
            return _MappingResult(
                [
                    {
                        "id": 1,
                        "provider": "OPENAI",
                        "base_url": "https://provider.example/v1",
                        "api_key_encrypted_value": "encrypted-value",
                        "api_key_fingerprint": "fingerprint",
                        "api_key_rotated_at": None,
                    }
                ]
            )
        return _EmptyResult()


class _MappingResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class _RecordingOperations:
    def __init__(self) -> None:
        self.bind = _Bind()
        self.created_tables: dict[str, tuple[object, ...]] = {}
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.altered_columns: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.foreign_keys: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped: list[tuple[str, str]] = []

    def f(self, name: str) -> str:
        return name

    def get_bind(self) -> _Bind:
        return self.bind

    def create_table(self, name: str, *elements: object) -> None:
        self.created_tables[name] = elements

    def add_column(self, table: str, column: sa.Column) -> None:
        self.added_columns.append((table, column))

    def alter_column(self, *args: object, **kwargs: object) -> None:
        self.altered_columns.append((args, kwargs))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        _remote_cols: list[str],
        **_kwargs: object,
    ) -> None:
        self.foreign_keys.append((name, source_table, referent_table, tuple(local_cols)))

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.indexes.append((name, table, tuple(columns)))

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.dropped.append(("index", name))
        assert table_name == "model_configurations"

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped.append((type_, name))
        assert table == "model_configurations"

    def drop_column(self, table: str, column: str) -> None:
        self.dropped.append((table, column))

    def drop_table(self, table: str) -> None:
        self.dropped.append(("table", table))


def test_model_provider_connection_migration_matches_models_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260908_0036"
    assert migration.down_revision == "20260908_0035"

    connection_elements = operations.created_tables[ModelProviderConnection.__tablename__]
    connection_columns = {
        element.name: element
        for element in connection_elements
        if isinstance(element, sa.Column)
    }
    assert set(connection_columns) == set(ModelProviderConnection.__table__.columns.keys())
    for name, column in connection_columns.items():
        model_column = ModelProviderConnection.__table__.columns[name]
        assert column.nullable == model_column.nullable
        assert type(column.type) is type(model_column.type)
        if isinstance(column.type, sa.String):
            assert column.type.length == model_column.type.length

    connection_constraints = {
        element.name
        for element in connection_elements
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
        for element in ModelProviderConnection.__table__.constraints
        if element.name is not None
    }
    assert connection_constraints == model_constraints
    assert all(len(name) <= 64 for name in model_constraints)

    added = {
        column.name: column
        for table, column in operations.added_columns
        if table == "model_configurations"
    }
    assert set(added) == {"connection_id", "model_vendor"}
    assert added["connection_id"].nullable is True
    assert added["model_vendor"].nullable is False
    assert operations.foreign_keys == [
        (
            migration._MODEL_CONNECTION_FK,
            "model_configurations",
            "model_provider_connections",
            ("connection_id",),
        )
    ]
    assert operations.indexes == [
        (migration._MODEL_CONNECTION_INDEX, "model_configurations", ("connection_id",))
    ]
    assert set(ModelConfiguration.__table__.columns.keys()) >= {
        "connection_id", "model_vendor", "provider", "base_url"
    }

    migration.downgrade()
    foreign_key_drop = operations.dropped.index(
        ("foreignkey", migration._MODEL_CONNECTION_FK)
    )
    index_drop = operations.dropped.index(("index", migration._MODEL_CONNECTION_INDEX))
    assert foreign_key_drop < index_drop
    assert ("index", migration._MODEL_CONNECTION_INDEX) in operations.dropped
    assert ("foreignkey", migration._MODEL_CONNECTION_FK) in operations.dropped
    assert ("table", "model_provider_connections") in operations.dropped
    assert any(
        isinstance(statement, sa.sql.dml.Update)
        and {"provider", "base_url", "api_key_encrypted_value"}.issubset(
            statement.table.c.keys()
        )
        for statement in operations.bind.statements
    )


def test_non_empty_legacy_connection_backfill_and_restore_preserves_credentials(
    monkeypatch,
) -> None:
    migration = _load_migration()
    metadata = sa.MetaData()
    secrets = sa.Table(
        "secrets",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("rotated_at", sa.DateTime(), nullable=True),
    )
    models = sa.Table(
        migration._MODEL_TABLE,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("api_key_secret_id", sa.Integer(), nullable=True),
        sa.Column("api_key_encrypted_value", sa.Text(), nullable=True),
        sa.Column("api_key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("api_key_rotated_at", sa.DateTime(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("model_vendor", sa.String(length=64), nullable=True),
    )
    connections = sa.Table(
        migration._CONNECTION_TABLE,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("access_type", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("protocol_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_encrypted_value", sa.Text(), nullable=True),
        sa.Column("api_key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("api_key_rotated_at", sa.DateTime(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            secrets.insert().values(
                id=7,
                encrypted_value="legacy-encrypted",
                fingerprint="a" * 64,
                rotated_at=None,
            )
        )
        connection.execute(
            models.insert().values(
                id=11,
                name="Legacy model",
                provider="OPENAI_COMPATIBLE",
                base_url="https://provider.example/v1",
                model_name="qwen-plus",
                created_by="legacy-user",
                api_key_secret_id=7,
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            SimpleNamespace(get_bind=lambda: connection),
        )

        migration._backfill_connections()
        model_row = connection.execute(
            sa.select(models).where(models.c.id == 11)
        ).mappings().one()
        connection_row = connection.execute(sa.select(connections)).mappings().one()
        assert model_row["connection_id"] == connection_row["id"]
        assert model_row["model_vendor"] == "ALIBABA_QWEN"
        assert connection_row["api_key_encrypted_value"] == "legacy-encrypted"
        assert connection_row["api_key_fingerprint"] == "a" * 64

        connection.execute(
            connections.update()
            .where(connections.c.id == connection_row["id"])
            .values(
                provider="OPENAI",
                base_url="https://restored.example/v1",
                api_key_encrypted_value="restored-encrypted",
                api_key_fingerprint="b" * 64,
            )
        )
        migration._restore_legacy_model_values()
        restored = connection.execute(
            sa.select(models).where(models.c.id == 11)
        ).mappings().one()
        assert restored["provider"] == "OPENAI"
        assert restored["base_url"] == "https://restored.example/v1"
        assert restored["api_key_encrypted_value"] == "restored-encrypted"
        assert restored["api_key_fingerprint"] == "b" * 64
    engine.dispose()


@pytest.mark.parametrize(
    ("provider", "model_name", "expected"),
    [
        ("OPENAI_COMPATIBLE", "qwen-plus", "ALIBABA_QWEN"),
        ("OPENAI_COMPATIBLE", "deepseek-chat", "DEEPSEEK"),
        ("OPENAI_COMPATIBLE", "gpt-4o", "OPENAI"),
        ("OPENAI_COMPATIBLE", "custom-model", "OTHER"),
        ("OPENAI", "custom-model", "OPENAI"),
        ("DASHSCOPE", "custom-model", "ALIBABA_QWEN"),
    ],
)
def test_legacy_vendor_classification_is_deterministic(
    provider: str, model_name: str, expected: str
) -> None:
    migration = _load_migration()
    assert migration._legacy_model_vendor(provider, model_name) == expected
