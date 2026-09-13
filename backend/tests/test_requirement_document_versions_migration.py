import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260912_0060_requirement_document_versions.py"
    )
    spec = importlib.util.spec_from_file_location("requirement_document_versions", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_document_version_migration_backfills_one_numbered_snapshot_per_project(
    monkeypatch,
) -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE projects (id INTEGER PRIMARY KEY)"))
        connection.execute(
            sa.text("CREATE TABLE requirement_reviews (id INTEGER PRIMARY KEY)")
        )
        connection.execute(
            sa.text(
                "CREATE TABLE requirements ("
                "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
                "parent_id INTEGER, code VARCHAR(64), title VARCHAR(255), "
                "type VARCHAR(32), order_index INTEGER, status VARCHAR(32), "
                "current_version_id INTEGER, created_by VARCHAR(64))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE requirement_versions ("
                "id INTEGER PRIMARY KEY, requirement_id INTEGER NOT NULL, "
                "version_no INTEGER, markdown_content TEXT, source_type VARCHAR(32), "
                "source_filename VARCHAR(255))"
            )
        )
        connection.execute(sa.text("INSERT INTO projects (id) VALUES (1)"))
        connection.execute(
            sa.text(
                "INSERT INTO requirements VALUES "
                "(10, 1, NULL, 'REQ-0010', '根需求', 'SECTION', 0, 'ACTIVE', 20, 'admin'), "
                "(11, 1, 10, 'REQ-0011', '子需求', 'FEATURE', 0, 'ACTIVE', 21, 'admin')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO requirement_versions VALUES "
                "(20, 10, 3, '# 根需求', 'MARKDOWN', 'demo.md'), "
                "(21, 11, 1, '子需求正文', 'MARKDOWN', 'demo.md')"
            )
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        monkeypatch.setattr(
            migration, "context", SimpleNamespace(is_offline_mode=lambda: False)
        )

        migration.upgrade()

        row = connection.execute(
            sa.text(
                "SELECT project_id, version_no, snapshot, source_type, source_filename "
                "FROM requirement_document_versions"
            )
        ).mappings().one()
        assert row["project_id"] == 1
        assert row["version_no"] == 1
        assert row["source_type"] == "MARKDOWN"
        assert row["source_filename"] == "demo.md"
        snapshot = __import__("json").loads(row["snapshot"])
        assert [item["outline_number"] for item in snapshot] == ["1", "1.1"]
        assert snapshot[0]["technical_version_no"] == 3

        migration.downgrade()
        assert not sa.inspect(connection).has_table("requirement_document_versions")
