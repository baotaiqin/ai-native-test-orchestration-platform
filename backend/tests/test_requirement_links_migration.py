import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import CheckConstraint

from app.modules.test_cases.models import RequirementCaseLink


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260910_0039_requirement_impact_links.py"
    )
    spec = importlib.util.spec_from_file_location("requirement_impact_links", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record

    @staticmethod
    def f(name: str) -> str:
        return name


def test_requirement_link_model_and_upgrade_keep_strong_separate_foreign_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = RequirementCaseLink.__table__
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert {
        ("requirement_id", "requirements.id", "CASCADE"),
        ("requirement_version_id", "requirement_versions.id", "SET NULL"),
        ("case_id", "test_cases.id", "CASCADE"),
        ("case_version_id", "test_case_versions.id", "RESTRICT"),
        ("web_case_id", "web_cases.id", "CASCADE"),
        ("web_case_version_id", "web_case_versions.id", "RESTRICT"),
        ("supersedes_link_id", "requirement_case_links.id", "RESTRICT"),
    } <= foreign_keys
    constraint_names = {item.name for item in table.constraints}
    assert "uq_requirement_case_links_active_test_case" in constraint_names
    assert "uq_requirement_case_links_active_web_case" in constraint_names
    assert any("target" in str(name) for name in constraint_names)
    assert any("status_active_slot" in str(name) for name in constraint_names)

    migration = _load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert migration.revision == "20260910_0039"
    assert migration.down_revision == "20260909_0038"
    call_names = [item[0] for item in operations.calls]
    assert call_names[0] == "drop_constraint"
    assert call_names.count("add_column") == 11
    assert call_names.count("create_foreign_key") == 4
    assert call_names.count("create_check_constraint") == 4
    assert call_names.count("create_unique_constraint") == 2
    foreign_key_names = {
        item[1][0] for item in operations.calls if item[0] == "create_foreign_key"
    }
    assert "fk_req_case_links_supersedes" in foreign_key_names
    assert all(len(name) <= 64 for name in foreign_key_names)


def test_status_check_uses_real_sql_null_semantics() -> None:
    constraint = next(
        item
        for item in RequirementCaseLink.__table__.constraints
        if isinstance(item, CheckConstraint) and "status_active_slot" in str(item.name)
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE probe(status TEXT NOT NULL, active_slot INTEGER, "
            "removed_by TEXT, removed_at TEXT, CHECK ("
            f"{constraint.sqltext}))"
        )
        connection.execute("INSERT INTO probe VALUES ('ACTIVE', 1, NULL, NULL)")
        connection.execute(
            "INSERT INTO probe VALUES ('REMOVED', NULL, 'owner', '2026-09-10')"
        )
        invalid_states = (
            ("ACTIVE", None, None, None),
            ("ACTIVE", 2, None, None),
            ("REMOVED", 1, "owner", "2026-09-10"),
            ("REMOVED", None, None, "2026-09-10"),
        )
        for state in invalid_states:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO probe VALUES (?, ?, ?, ?)", state)


def test_migration_never_guesses_versions_and_refuses_lossy_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert "UPDATE requirement_case_links" not in source
    assert "current_version_id" not in source
    assert "Cannot downgrade requirement links" in source

    class ScalarResult:
        def scalar_one(self) -> int:
            return 1

    class Bind:
        def execute(self, statement: Any) -> ScalarResult:
            assert "asset_type <> 'TEST_CASE'" in str(statement)
            return ScalarResult()

    class RefusingOperations:
        def get_bind(self) -> Bind:
            return Bind()

    monkeypatch.setattr(migration, "op", RefusingOperations())
    with pytest.raises(RuntimeError, match="Cannot downgrade requirement links"):
        migration.downgrade()


def test_downgrade_drops_foreign_keys_before_their_supporting_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()

    class ScalarResult:
        def scalar_one(self) -> int:
            return 0

    class Bind:
        def execute(self, statement: Any) -> ScalarResult:
            assert "created_at_time_basis <> 'LEGACY_UNKNOWN'" in str(statement)
            return ScalarResult()

    class DowngradeOperations(RecordingOperations):
        def get_bind(self) -> Bind:
            return Bind()

    operations = DowngradeOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()
    foreign_key_positions = [
        index
        for index, (name, _args, kwargs) in enumerate(operations.calls)
        if name == "drop_constraint" and kwargs.get("type_") == "foreignkey"
    ]
    dependency_indexes = {
        "ix_requirement_case_links_supersedes_link_id",
        "ix_requirement_case_links_web_case_version_id",
        "ix_requirement_case_links_web_case_id",
        "ix_requirement_case_links_case_version_id",
    }
    index_positions = [
        index
        for index, (name, args, _kwargs) in enumerate(operations.calls)
        if name == "drop_index" and args[0] in dependency_indexes
    ]
    assert foreign_key_positions and index_positions
    assert max(foreign_key_positions) < min(index_positions)
