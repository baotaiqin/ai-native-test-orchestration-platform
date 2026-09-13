import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260912_0061_scenario_preview_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("scenario_preview_profiles", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_preview_profile_migration_is_linear_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260912_0061"
    assert migration.down_revision == "20260912_0060"
    create = next(
        args
        for name, args, _ in operations.calls
        if name == "create_table" and args[0] == "scenario_preview_profiles"
    )
    columns = {item.name: item for item in create[1:] if hasattr(item, "name")}
    assert {"scenario_version_id", "context", "responses_by_node", "updated_by"} <= (
        columns.keys()
    )
    assert columns["created_at"].server_default is not None

    operations.calls.clear()
    migration.downgrade()
    assert ("drop_table", ("scenario_preview_profiles",), {}) in operations.calls
