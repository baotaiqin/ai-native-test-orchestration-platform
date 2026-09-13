import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa

from app.modules.evidence.models import EvidenceArtifact


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260901_0029_web_evidence_uploads.py"
    )
    spec = importlib.util.spec_from_file_location("web_evidence_uploads", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self, *, has_web_rows: bool = False) -> None:
        self.dropped: list[tuple[str, str, str]] = []
        self.checks: list[tuple[str, str, str]] = []
        self.uniques: list[tuple[str, str, tuple[str, ...]]] = []
        self.has_web_rows = has_web_rows
        self.executed_statements: list[object] = []

    class _Result:
        def __init__(self, has_rows: bool) -> None:
            self.has_rows = has_rows

        def first(self) -> tuple[int] | None:
            return (1,) if self.has_rows else None

    class _Connection:
        def __init__(self, owner: "_RecordingOperations") -> None:
            self.owner = owner

        def execute(self, statement: object) -> "_RecordingOperations._Result":
            self.owner.executed_statements.append(statement)
            return _RecordingOperations._Result(self.owner.has_web_rows)

    def get_bind(self) -> "_RecordingOperations._Connection":
        return self._Connection(self)

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped.append((name, table, type_))

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.checks.append((name, table, condition))

    def create_unique_constraint(
        self, name: str, table: str, columns: list[str]
    ) -> None:
        self.uniques.append((name, table, tuple(columns)))


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_web_evidence_migration_rewrites_only_artifact_constraints(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    migration = _load_migration()
    recorded = _RecordingOperations()
    monkeypatch.setattr(migration, "op", recorded)

    getattr(migration, operation)()

    assert migration.revision == "20260901_0029"
    assert migration.down_revision == "20260831_0028"
    assert recorded.dropped == [
        ("artifacts_type_values", "artifacts", "check"),
        ("artifacts_size_bounds", "artifacts", "check"),
        ("uq_artifacts_run_case_type", "artifacts", "unique"),
    ] if operation == "upgrade" else [
        ("uq_artifacts_run_case_type", "artifacts", "unique"),
        ("artifacts_type_values", "artifacts", "check"),
        ("artifacts_size_bounds", "artifacts", "check"),
    ]
    assert recorded.uniques == [
        (
            "uq_artifacts_run_case_type",
            "artifacts",
            (
                "run_id",
                "case_run_id",
                "artifact_type",
                "file_name",
            )
            if operation == "upgrade"
            else ("run_id", "case_run_id", "artifact_type"),
        )
    ]
    assert recorded.checks[0][:2] == ("artifacts_type_values", "artifacts")
    assert recorded.checks[1][:2] == ("artifacts_size_bounds", "artifacts")
    if operation == "upgrade":
        assert "WEB_SUMMARY" in recorded.checks[0][2]
        assert "20000000" in recorded.checks[1][2]
    else:
        assert "WEB_SUMMARY" not in recorded.checks[0][2]
        assert "2000000" in recorded.checks[1][2]


def test_model_supports_multi_file_web_evidence_and_twenty_mb_bound() -> None:
    unique = next(
        item
        for item in EvidenceArtifact.__table__.constraints
        if item.name == "uq_artifacts_run_case_type"
    )
    assert tuple(column.name for column in unique.columns) == (
        "run_id",
        "case_run_id",
        "artifact_type",
        "file_name",
    )
    checks = {
        item.name: str(item.sqltext)
        for item in EvidenceArtifact.__table__.constraints
        if isinstance(item, sa.CheckConstraint) and item.name
    }
    assert "WEB_SUMMARY" in checks["ck_artifacts_artifacts_type_values"]
    assert "20000000" in checks["ck_artifacts_artifacts_size_bounds"]


def test_downgrade_refuses_when_new_web_evidence_rows_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    recorded = _RecordingOperations(has_web_rows=True)
    monkeypatch.setattr(migration, "op", recorded)

    with pytest.raises(RuntimeError, match="不能 downgrade"):
        migration.downgrade()
    assert recorded.dropped == []
    assert len(recorded.executed_statements) == 1
