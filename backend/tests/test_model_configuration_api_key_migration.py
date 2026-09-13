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
        / "20260908_0035_model_configuration_api_keys.py"
    )
    spec = importlib.util.spec_from_file_location("model_configuration_api_keys", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.added: list[sa.Column] = []
        self.dropped: list[tuple[str, str]] = []
        self.checks: list[tuple[str, str, str]] = []
        self.statements: list[str] = []

    def add_column(self, _table: str, column: sa.Column) -> None:
        self.added.append(column)

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.checks.append((name, table, condition))

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped.append(("constraint", name))
        assert table == "model_configurations"
        assert type_ == "check"

    def drop_column(self, table: str, column: str) -> None:
        self.dropped.append((table, column))


def test_model_api_key_migration_matches_model_and_is_reversible(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert migration.revision == "20260908_0035"
    assert migration.down_revision == "20260908_0034"
    model_columns = ModelConfiguration.__table__.columns
    added = {column.name: column for column in operations.added}
    expected = {
        "api_key_encrypted_value",
        "api_key_fingerprint",
        "api_key_rotated_at",
    }
    assert set(added) == expected
    for name in expected:
        assert added[name].nullable == model_columns[name].nullable
        assert type(added[name].type) is type(model_columns[name].type)
        if isinstance(added[name].type, sa.String):
            assert added[name].type.length == model_columns[name].type.length
    assert operations.checks == [
        (
            migration._FINGERPRINT_CHECK,
            "model_configurations",
            "api_key_fingerprint IS NULL OR length(api_key_fingerprint) = 64",
        )
    ]
    assert "api_key_secret_id" in operations.statements[0]
    assert "secrets" in operations.statements[0]
    assert "encrypted_value" in operations.statements[0]
    assert "fingerprint" in operations.statements[0]
    assert "rotated_at" in operations.statements[0]

    migration.downgrade()
    assert operations.dropped == [
        ("constraint", migration._FINGERPRINT_CHECK),
        (migration._TABLE, "api_key_rotated_at"),
        (migration._TABLE, "api_key_fingerprint"),
        (migration._TABLE, "api_key_encrypted_value"),
    ]
