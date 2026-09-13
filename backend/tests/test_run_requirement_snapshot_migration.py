import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import CheckConstraint

from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260910_0040_run_requirement_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_requirement_snapshots", path
    )
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


def test_snapshot_models_keep_sources_independent_from_live_domain_rows() -> None:
    capture_fks = {
        (item.parent.name, item.target_fullname, item.ondelete)
        for item in RunRequirementCapture.__table__.foreign_keys
    }
    source_fks = {
        (item.parent.name, item.target_fullname, item.ondelete)
        for item in RunRequirementSource.__table__.foreign_keys
    }
    assert capture_fks == {("case_run_id", "case_runs.id", "CASCADE")}
    assert source_fks == {
        ("capture_id", "run_requirement_captures.id", "CASCADE")
    }
    assert all(
        len(str(item.name)) <= 64
        for table in (
            RunRequirementCapture.__table__,
            RunRequirementSource.__table__,
        )
        for item in (*table.constraints, *table.indexes)
        if item.name is not None
    )


def test_nullable_binding_checks_reject_partial_shapes() -> None:
    requirement_check = next(
        item
        for item in RunRequirementSource.__table__.constraints
        if isinstance(item, CheckConstraint)
        and "requirement_binding_shape" in str(item.name)
    )
    asset_check = next(
        item
        for item in RunRequirementSource.__table__.constraints
        if isinstance(item, CheckConstraint) and "asset_binding_shape" in str(item.name)
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE req_probe(requirement_version_binding TEXT NOT NULL, "
            "requirement_version_id INTEGER, requirement_version_no INTEGER, "
            "requirement_content_hash TEXT, requirement_source_type TEXT, CHECK ("
            f"{requirement_check.sqltext}))"
        )
        connection.execute(
            "INSERT INTO req_probe VALUES "
            "('EXACT_REQUIREMENT_VERSION', 1, 1, 'hash', 'UPLOAD')"
        )
        connection.execute(
            "INSERT INTO req_probe VALUES "
            "('REQUIREMENT_VERSION_UNKNOWN', NULL, NULL, NULL, NULL)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO req_probe VALUES "
                "('EXACT_REQUIREMENT_VERSION', 1, NULL, NULL, NULL)"
            )

        connection.execute(
            "CREATE TABLE asset_probe(asset_version_binding TEXT NOT NULL, "
            "link_asset_version_id INTEGER, target_version_id INTEGER NOT NULL, CHECK ("
            f"{asset_check.sqltext}))"
        )
        connection.execute(
            "INSERT INTO asset_probe VALUES ('EXACT_EXECUTION_VERSION', 7, 7)"
        )
        connection.execute(
            "INSERT INTO asset_probe VALUES ('ASSET_VERSION_UNKNOWN', NULL, 7)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO asset_probe VALUES ('EXACT_EXECUTION_VERSION', NULL, 7)"
            )


def test_migration_has_single_head_parent_and_refuses_lossy_downgrade_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    assert migration.revision == "20260910_0040"
    assert migration.down_revision == "20260910_0039"

    class ScalarResult:
        def scalar_one(self) -> int:
            return 1

    class Bind:
        def execute(self, statement: Any) -> ScalarResult:
            assert str(statement) == "SELECT COUNT(*) FROM run_requirement_captures"
            return ScalarResult()

    class RefusingOperations(RecordingOperations):
        def get_bind(self) -> Bind:
            return Bind()

    operations = RefusingOperations()
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="Cannot downgrade run requirement snapshots"):
        migration.downgrade()
    assert operations.calls == []


def test_empty_downgrade_drops_child_before_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()

    class ScalarResult:
        def scalar_one(self) -> int:
            return 0

    class Bind:
        def execute(self, _statement: Any) -> ScalarResult:
            return ScalarResult()

    class Operations(RecordingOperations):
        def get_bind(self) -> Bind:
            return Bind()

    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()
    dropped_tables = [args[0] for name, args, _kwargs in operations.calls if name == "drop_table"]
    assert dropped_tables == [
        "run_requirement_sources",
        "run_requirement_captures",
    ]
