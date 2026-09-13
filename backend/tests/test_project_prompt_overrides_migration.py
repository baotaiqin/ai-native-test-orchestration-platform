import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260912_0065_project_prompt_overrides.py"
    )
    spec = importlib.util.spec_from_file_location("project_prompt_overrides", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_project_prompt_override_migration_contract() -> None:
    migration = _load_migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert migration.revision == "20260912_0065"
    assert migration.down_revision == "20260912_0064"
    assert 'sa.Column("scope"' in source
    assert 'sa.Column("project_id"' in source
    assert 'sa.Column("base_prompt_id"' in source
    assert 'sa.Column("is_builtin"' in source
    assert "uq_prompt_definitions_project_base" in source
    assert "prompt_definitions_scope_values" in source
