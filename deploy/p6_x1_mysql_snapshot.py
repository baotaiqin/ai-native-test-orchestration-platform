"""Validate P6-X1 report snapshots against an isolated real MySQL 8.4."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
EVIDENCE_ROOT = WORKSPACE_ROOT / ".codex-validation" / "p6-x1-mysql"
RESULT_FILE = EVIDENCE_ROOT / "result.json"
PACKAGE = "P6-X1M/r1"
IMAGE = "mysql:8.4"
LABEL_PACKAGE = "com.openai.codex.package"
LABEL_RUN = "com.openai.codex.run"
LABEL_PACKAGE_VALUE = "P6-X1M-r1"
RUN_ID_VALUE = "p6-x1m-run"
PROJECT_ID = 960_001
ENVIRONMENT_ID = 960_001
CASE_ID = 960_001
CASE_VERSION_ID = 960_001
FIRST_CASE_RUN_ID = 961_001
LAST_CASE_RUN_ID = 961_105
CASE_COUNT = 105
ORIGINAL_LATE_FIELD = "P6_X1M_ORIGINAL_SECOND_PAGE_VALUE"
CHANGED_LATE_FIELD = "P6_X1M_CHANGED_AFTER_FIRST_PAGE"
EXPECTED_SOURCE_HASHES = {
    "backend/app/modules/reports/exports.py": (
        "4b10127f9eb252a00ff0059a25b9ed12e9cdecb243ca0bf73a338b2d6569d836"
    ),
    "backend/app/modules/reports/router.py": (
        "7ef7f789d283aa29eb33ea00aac0f76d8d28c1df7ebd68af3e16edc2fe86be2a"
    ),
    "backend/app/modules/reports/service.py": (
        "57c3d5fb028bde48706b573b6e53c0e476590cec8a2804122407b9d282fa1e99"
    ),
}
OFFICIAL_MANIFESTS = {
    "ready": (
        WORKSPACE_ROOT / ".codex-validation" / "p5e-services" / "ready.json"
    ),
    "owned": (
        WORKSPACE_ROOT
        / ".codex-validation"
        / "p5e-services"
        / "owned-processes.json"
    ),
}
MODEL_MODULES = (
    "app.modules.api_definitions.models",
    "app.modules.database_connections.models",
    "app.modules.datasets.models",
    "app.modules.environments.models",
    "app.modules.evidence.models",
    "app.modules.model_center.models",
    "app.modules.projects.models",
    "app.modules.prompt_center.models",
    "app.modules.requirement_reviews.models",
    "app.modules.requirements.models",
    "app.modules.resource_registry.models",
    "app.modules.runners.models",
    "app.modules.runs.models",
    "app.modules.scenarios.models",
    "app.modules.secrets.models",
    "app.modules.test_cases.models",
    "app.modules.web_cases.models",
    "app.modules.web_failure_analysis.models",
    "app.modules.web_healing.models",
    "app.modules.web_recording_ai.models",
    "app.modules.web_recordings.models",
)


class ValidationFailure(RuntimeError):
    """A secret-free validation stop with a stable code."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValidationFailure(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_snapshot() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relative, expected in EXPECTED_SOURCE_HASHES.items():
        actual = _sha256(WORKSPACE_ROOT / relative)
        result[relative] = {
            "expected": expected,
            "actual": actual,
            "matches": actual == expected,
        }
    return result


def _manifest_snapshot() -> dict[str, str | None]:
    return {
        name: _sha256(path) if path.is_file() else None
        for name, path in OFFICIAL_MANIFESTS.items()
    }


def _docker(*arguments: str) -> str:
    process = subprocess.run(
        ["docker", *arguments],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        operation = arguments[0].replace("-", "_") if arguments else "unknown"
        raise ValidationFailure(f"DOCKER_{operation.upper()}_FAILED")
    return process.stdout.strip()


def _docker_lines(*arguments: str) -> list[str]:
    return sorted(line.strip() for line in _docker(*arguments).splitlines() if line.strip())


def _docker_inventory() -> dict[str, Any]:
    containers = _docker_lines("ps", "--all", "--no-trunc", "--format", "{{.ID}}")
    volumes = _docker_lines("volume", "ls", "--quiet")
    return {
        "container_count": len(containers),
        "container_ids_sha256": _json_sha256(containers),
        "volume_count": len(volumes),
        "volume_names_sha256": _json_sha256(volumes),
    }


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    require(port != 3306, "RANDOM_PORT_MUST_NOT_BE_3306")
    return port


def _wait_healthy(container_name: str, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        health = _docker(
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            container_name,
        )
        if health == "healthy":
            return
        if health == "unhealthy":
            raise ValidationFailure("MYSQL_CONTAINER_UNHEALTHY")
        time.sleep(2)
    raise ValidationFailure("MYSQL_CONTAINER_HEALTH_TIMEOUT")


def _normalize_isolation(value: Any) -> str:
    return str(value).strip().upper().replace("_", "-").replace(" ", "-")


def _statement_operation(statement: str) -> str:
    normalized = " ".join(statement.strip().split()).upper()
    if normalized.startswith("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"):
        return "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
    if normalized.startswith("SET SESSION TRANSACTION ISOLATION LEVEL"):
        return normalized
    return normalized.split(None, 1)[0] if normalized else "EMPTY"


def _run_product_validation(
    database_url: Any,
    validation_stage: dict[str, str],
) -> dict[str, Any]:
    from sqlalchemy import (
        Column,
        Integer,
        MetaData,
        String,
        Table,
        create_engine,
        event,
        func,
        select,
        text,
        update,
    )
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import sessionmaker

    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)

    from app.core.exceptions import AppError
    from app.infrastructure.db.base import Base
    from app.modules.auth.schemas import CurrentUser
    from app.modules.environments.models import Environment
    from app.modules.projects.models import Project, ProjectMember
    from app.modules.reports import exports
    from app.modules.runners.models import Runner
    from app.modules.runs.models import CaseRun, StepRun, TestRun
    from app.modules.test_cases.models import TestCase, TestCaseVersion

    engine = create_engine(
        database_url,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        pool_size=8,
        max_overflow=0,
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    probe_metadata = MetaData()
    probe = Table(
        "p6_x1m_transaction_probe",
        probe_metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String(128), nullable=False),
    )
    statement_operations: dict[str, dict[int, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    snapshot_connection_ids: dict[str, list[int]] = defaultdict(list)
    active_phase = {"name": "schema"}

    def connection_id(connection: Any) -> int:
        return int(connection.connection.driver_connection.thread_id())

    def record_statement(
        connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        phase = active_phase["name"]
        identifier = connection_id(connection)
        operation = _statement_operation(statement)
        statement_operations[phase][identifier].append(operation)
        if operation == "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY":
            snapshot_connection_ids[phase].append(identifier)

    def pool_checked_out() -> int:
        checkedout = getattr(engine.pool, "checkedout", None)
        require(callable(checkedout), "POOL_CHECKEDOUT_UNAVAILABLE")
        return int(checkedout())

    try:
        validation_stage["name"] = "product_schema_create"
        active_phase["name"] = "schema"
        Base.metadata.create_all(engine)
        probe_metadata.create_all(engine)
        table_names = sorted(Base.metadata.tables)

        seeded_at = datetime(2026, 9, 10, 6, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        with factory.begin() as session:
            validation_stage["name"] = "product_seed_project"
            session.add(
                Project(
                    id=PROJECT_ID,
                    name="P6-X1M Isolated Project",
                    code="P6X1MYSQL",
                    owner_id="p6-x1m-owner",
                )
            )
            session.flush()
            validation_stage["name"] = "product_seed_references"
            session.add_all(
                [
                    ProjectMember(
                        project_id=PROJECT_ID,
                        user_id="p6-x1m-viewer",
                        role="VIEWER",
                    ),
                    Environment(
                        id=ENVIRONMENT_ID,
                        project_id=PROJECT_ID,
                        name="P6-X1M Isolated Environment",
                        code="P6X1M",
                        enabled=True,
                    ),
                    Runner(
                        id="p6-x1m-runner",
                        name="P6-X1M Isolated Runner",
                        hostname="isolated.invalid",
                        credential_digest="p6-x1m-synthetic-digest",
                        status="ACTIVE",
                        heartbeat_interval_seconds=30,
                    ),
                ]
            )
            session.flush()
            validation_stage["name"] = "product_seed_case"
            test_case = TestCase(
                id=CASE_ID,
                project_id=PROJECT_ID,
                code="P6-X1M-CASE",
                name="P6-X1M Snapshot Case",
                case_type="API",
                status="ACTIVE",
                source="MANUAL",
                current_version_id=None,
                created_by="p6-x1m-owner",
            )
            session.add(test_case)
            session.flush()
            validation_stage["name"] = "product_seed_case_version"
            session.add(
                TestCaseVersion(
                    id=CASE_VERSION_ID,
                    case_id=CASE_ID,
                    version_no=1,
                    content={"title": "P6-X1M isolated version"},
                    created_by="p6-x1m-owner",
                )
            )
            session.flush()
            test_case.current_version_id = CASE_VERSION_ID
            session.flush()
            validation_stage["name"] = "product_seed_run"
            session.add(
                TestRun(
                    id=RUN_ID_VALUE,
                    run_code="P6-X1M-RUN",
                    run_type="API_CASE",
                    project_id=PROJECT_ID,
                    environment_id=ENVIRONMENT_ID,
                    runner_id="p6-x1m-runner",
                    case_id=CASE_ID,
                    case_version_id=CASE_VERSION_ID,
                    status="SUCCESS",
                    trigger_type="MANUAL",
                    required_capabilities=[],
                    required_tags=[],
                    required_slot_type="API",
                    required_slot_count=1,
                    force_stopped=False,
                    started_at=seeded_at,
                    ended_at=seeded_at,
                    total=CASE_COUNT,
                    pass_count=CASE_COUNT,
                    fail_count=0,
                    review_count=0,
                    timeout_count=0,
                    created_by="p6-x1m-viewer",
                    created_at=seeded_at,
                    updated_at=seeded_at,
                )
            )
            session.flush()
            validation_stage["name"] = "product_seed_case_runs"
            for index in range(CASE_COUNT):
                case_run_id = FIRST_CASE_RUN_ID + index
                session.add(
                    CaseRun(
                        id=case_run_id,
                        run_id=RUN_ID_VALUE,
                        sequence_no=index + 1,
                        case_id=CASE_ID,
                        case_version_id=CASE_VERSION_ID,
                        status="SUCCESS",
                        duration=10 + index,
                        retry_count=0,
                        error_message=(
                            ORIGINAL_LATE_FIELD
                            if case_run_id == LAST_CASE_RUN_ID
                            else None
                        ),
                        started_at=seeded_at,
                        ended_at=seeded_at,
                        created_at=seeded_at,
                        updated_at=seeded_at,
                    )
                )
            session.flush()
            validation_stage["name"] = "product_seed_steps"
            for index in range(CASE_COUNT):
                case_run_id = FIRST_CASE_RUN_ID + index
                session.add(
                    StepRun(
                        id=962_001 + index,
                        case_run_id=case_run_id,
                        sequence_no=1,
                        node_id=f"node-{index + 1:03d}",
                        step_name=f"P6-X1M Step {index + 1:03d}",
                        step_type="API_REQUEST",
                        status="SUCCESS",
                        duration=10 + index,
                        retry_count=0,
                        started_at=seeded_at,
                        ended_at=seeded_at,
                        created_at=seeded_at,
                        updated_at=seeded_at,
                    )
                )
            session.flush()
            validation_stage["name"] = "product_seed_probe"
            session.execute(probe.insert().values(id=1, value="baseline"))

        validation_stage["name"] = "baseline_isolation"
        active_phase["name"] = "baseline_isolation"
        with engine.connect() as connection:
            default_isolation_before = connection.exec_driver_sql(
                "SELECT @@transaction_isolation"
            ).scalar_one()
        require(
            _normalize_isolation(default_isolation_before) == "READ-COMMITTED",
            "ENGINE_BASELINE_NOT_READ_COMMITTED",
        )

        event.listen(engine, "before_cursor_execute", record_statement)

        validation_stage["name"] = "read_only_probe"
        active_phase["name"] = "read_only_probe"
        read_only_probe: dict[str, Any] = {}
        with engine.connect() as connection:
            connection = exports._begin_consistent_read(connection)
            read_only_probe["connection_id"] = connection.exec_driver_sql(
                "SELECT CONNECTION_ID()"
            ).scalar_one()
            read_only_probe["transaction_isolation"] = connection.exec_driver_sql(
                "SELECT @@transaction_isolation"
            ).scalar_one()
            try:
                connection.execute(
                    probe.update().where(probe.c.id == 1).values(value="forbidden")
                )
            except DBAPIError as exc:
                arguments = getattr(exc.orig, "args", ())
                read_only_probe["write_blocked"] = True
                read_only_probe["write_error_code"] = (
                    int(arguments[0]) if arguments and isinstance(arguments[0], int) else None
                )
            else:
                read_only_probe["write_blocked"] = False
                read_only_probe["write_error_code"] = None
            finally:
                connection.rollback()
        require(
            _normalize_isolation(read_only_probe["transaction_isolation"])
            == "REPEATABLE-READ",
            "SNAPSHOT_ISOLATION_NOT_REPEATABLE_READ",
        )
        require(read_only_probe["write_blocked"] is True, "READ_ONLY_WRITE_NOT_BLOCKED")

        identity = CurrentUser(
            id="p6-x1m-viewer",
            username="p6-x1m-viewer",
            display_name="P6-X1M Viewer",
            roles=[],
        )

        original_fetch = exports.list_report_cases
        original_render = exports._render_html

        status_capture: dict[str, Any] = {}
        status_changed = False

        def status_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal status_changed
            if not status_changed:
                status_changed = True
                with factory.begin() as writer:
                    status_capture["writer_connection_id"] = writer.scalar(
                        text("SELECT CONNECTION_ID()")
                    )
                    writer.execute(
                        update(CaseRun)
                        .where(CaseRun.id == FIRST_CASE_RUN_ID)
                        .values(status="FAILED")
                    )
            return original_fetch(*args, **kwargs)

        def status_render(snapshot: Any) -> bytes:
            status_capture.update(
                {
                    "detail_success_count": (
                        snapshot.detail.summary.case_status_counts.success
                    ),
                    "export_success_count": sum(
                        item.status == "SUCCESS" for item in snapshot.cases
                    ),
                    "export_failed_count": sum(
                        item.status == "FAILED" for item in snapshot.cases
                    ),
                    "case_count": len(snapshot.cases),
                }
            )
            return original_render(snapshot)

        validation_stage["name"] = "status_export"
        active_phase["name"] = "status_export"
        caller = factory()
        caller_transaction = caller.begin()
        try:
            status_capture["caller_connection_id"] = caller.scalar(
                text("SELECT CONNECTION_ID()")
            )
            caller.execute(
                probe.update().where(probe.c.id == 1).values(value="caller-pending-1")
            )
            transaction_object = caller.get_transaction()
            status_capture["pool_checked_out_before"] = pool_checked_out()
            exports.list_report_cases = status_fetch
            exports._render_html = status_render  # type: ignore[assignment]
            status_file = exports.create_report_export(
                caller,
                identity,
                RUN_ID_VALUE,
                "html",
            )
            status_capture["pool_checked_out_after"] = pool_checked_out()
            status_capture["caller_transaction_same"] = (
                caller.get_transaction() is transaction_object
            )
            status_capture["caller_transaction_active"] = caller.in_transaction()
            caller.execute(
                probe.update().where(probe.c.id == 1).values(value="caller-pending-2")
            )
            status_capture["caller_continued"] = (
                caller.scalar(select(probe.c.value).where(probe.c.id == 1))
                == "caller-pending-2"
            )
            with factory() as observer:
                status_capture["uncommitted_not_visible"] = (
                    observer.scalar(select(probe.c.value).where(probe.c.id == 1))
                    == "baseline"
                )
                status_capture["database_status_after_concurrent_change"] = observer.scalar(
                    select(CaseRun.status).where(CaseRun.id == FIRST_CASE_RUN_ID)
                )
            status_capture["output_bytes"] = len(status_file.content)
            status_capture["output_sha256"] = hashlib.sha256(
                status_file.content
            ).hexdigest()
        finally:
            exports.list_report_cases = original_fetch
            exports._render_html = original_render  # type: ignore[assignment]
            if caller_transaction.is_active:
                caller_transaction.rollback()
            caller.close()
        with factory() as observer:
            status_capture["caller_rollback_preserved_baseline"] = (
                observer.scalar(select(probe.c.value).where(probe.c.id == 1))
                == "baseline"
            )
        status_snapshot_ids = snapshot_connection_ids["status_export"]
        require(len(set(status_snapshot_ids)) == 1, "STATUS_SNAPSHOT_CONNECTION_COUNT")
        status_capture["snapshot_connection_id"] = status_snapshot_ids[0]
        require(
            len(
                {
                    int(status_capture["caller_connection_id"]),
                    int(status_capture["snapshot_connection_id"]),
                    int(status_capture["writer_connection_id"]),
                }
            )
            == 3,
            "STATUS_CONNECTIONS_NOT_INDEPENDENT",
        )
        require(
            status_capture["case_count"] == CASE_COUNT
            and status_capture["detail_success_count"] == CASE_COUNT
            and status_capture["export_success_count"] == CASE_COUNT
            and status_capture["export_failed_count"] == 0
            and status_capture["database_status_after_concurrent_change"] == "FAILED",
            "STATUS_EXPORT_MIXED_SNAPSHOTS",
        )
        require(
            status_capture["pool_checked_out_before"]
            == status_capture["pool_checked_out_after"]
            == 1,
            "STATUS_EXPORT_CONNECTION_NOT_RELEASED",
        )
        require(
            all(
                status_capture[key] is True
                for key in (
                    "caller_transaction_same",
                    "caller_transaction_active",
                    "caller_continued",
                    "uncommitted_not_visible",
                    "caller_rollback_preserved_baseline",
                )
            ),
            "STATUS_EXPORT_TOUCHED_CALLER_TRANSACTION",
        )

        with factory.begin() as session:
            session.execute(
                update(CaseRun)
                .where(CaseRun.id == FIRST_CASE_RUN_ID)
                .values(status="SUCCESS")
            )

        cross_page_capture: dict[str, Any] = {}
        cross_page_changed = False

        def cross_page_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal cross_page_changed
            query = args[3]
            if query.page == 2 and not cross_page_changed:
                cross_page_changed = True
                with factory.begin() as writer:
                    cross_page_capture["writer_connection_id"] = writer.scalar(
                        text("SELECT CONNECTION_ID()")
                    )
                    writer.execute(
                        update(CaseRun)
                        .where(CaseRun.id == LAST_CASE_RUN_ID)
                        .values(error_message=CHANGED_LATE_FIELD)
                    )
            return original_fetch(*args, **kwargs)

        def cross_page_render(snapshot: Any) -> bytes:
            last_case = next(
                item for item in snapshot.cases if item.id == LAST_CASE_RUN_ID
            )
            cross_page_capture.update(
                {
                    "case_count": len(snapshot.cases),
                    "last_case_error_message": last_case.error_message,
                    "ordered_case_ids": [
                        snapshot.cases[0].id,
                        snapshot.cases[99].id,
                        snapshot.cases[100].id,
                        snapshot.cases[-1].id,
                    ],
                }
            )
            return original_render(snapshot)

        validation_stage["name"] = "cross_page_export"
        active_phase["name"] = "cross_page_export"
        exports.list_report_cases = cross_page_fetch
        exports._render_html = cross_page_render  # type: ignore[assignment]
        try:
            cross_page_capture["pool_checked_out_before"] = pool_checked_out()
            with factory() as source:
                cross_page_file = exports.create_report_export(
                    source,
                    identity,
                    RUN_ID_VALUE,
                    "html",
                )
            cross_page_capture["pool_checked_out_after"] = pool_checked_out()
        finally:
            exports.list_report_cases = original_fetch
            exports._render_html = original_render  # type: ignore[assignment]
        with factory() as observer:
            cross_page_capture["database_last_case_error_message"] = observer.scalar(
                select(CaseRun.error_message).where(CaseRun.id == LAST_CASE_RUN_ID)
            )
        cross_page_snapshot_ids = snapshot_connection_ids["cross_page_export"]
        require(
            len(set(cross_page_snapshot_ids)) == 1,
            "CROSS_PAGE_SNAPSHOT_CONNECTION_COUNT",
        )
        cross_page_capture["snapshot_connection_id"] = cross_page_snapshot_ids[0]
        cross_page_capture["output_bytes"] = len(cross_page_file.content)
        cross_page_capture["output_sha256"] = hashlib.sha256(
            cross_page_file.content
        ).hexdigest()
        cross_page_capture["output_kept_old_value"] = (
            ORIGINAL_LATE_FIELD.encode("utf-8") in cross_page_file.content
            and CHANGED_LATE_FIELD.encode("utf-8") not in cross_page_file.content
        )
        require(
            int(cross_page_capture["snapshot_connection_id"])
            != int(cross_page_capture["writer_connection_id"]),
            "CROSS_PAGE_CONNECTIONS_NOT_INDEPENDENT",
        )
        require(
            cross_page_capture["case_count"] == CASE_COUNT
            and cross_page_capture["last_case_error_message"] == ORIGINAL_LATE_FIELD
            and cross_page_capture["database_last_case_error_message"]
            == CHANGED_LATE_FIELD
            and cross_page_capture["ordered_case_ids"]
            == [FIRST_CASE_RUN_ID, FIRST_CASE_RUN_ID + 99, FIRST_CASE_RUN_ID + 100, LAST_CASE_RUN_ID]
            and cross_page_capture["output_kept_old_value"] is True,
            "CROSS_PAGE_EXPORT_MIXED_SNAPSHOTS",
        )
        require(
            cross_page_capture["pool_checked_out_before"]
            == cross_page_capture["pool_checked_out_after"]
            == 0,
            "CROSS_PAGE_CONNECTION_NOT_RELEASED",
        )

        validation_stage["name"] = "error_export"
        active_phase["name"] = "error_export"
        error_capture: dict[str, Any] = {}
        caller = factory()
        caller_transaction = caller.begin()
        original_limit = exports.MAX_EXPORT_BYTES
        try:
            error_capture["caller_connection_id"] = caller.scalar(
                text("SELECT CONNECTION_ID()")
            )
            caller.execute(
                probe.update().where(probe.c.id == 1).values(value="error-pending-1")
            )
            transaction_object = caller.get_transaction()
            error_capture["pool_checked_out_before"] = pool_checked_out()
            exports.MAX_EXPORT_BYTES = 1
            try:
                exports.create_report_export(
                    caller,
                    identity,
                    RUN_ID_VALUE,
                    "html",
                )
            except AppError as exc:
                error_capture["error_code"] = exc.code
                error_capture["error_status_code"] = exc.status_code
                error_capture["error_resource"] = (
                    exc.details.get("resource") if isinstance(exc.details, dict) else None
                )
            else:
                raise ValidationFailure("BYTE_LIMIT_EXPORT_DID_NOT_FAIL")
            error_capture["pool_checked_out_after"] = pool_checked_out()
            error_capture["caller_transaction_same"] = (
                caller.get_transaction() is transaction_object
            )
            error_capture["caller_transaction_active"] = caller.in_transaction()
            caller.execute(
                probe.update().where(probe.c.id == 1).values(value="error-pending-2")
            )
            error_capture["caller_continued"] = (
                caller.scalar(select(probe.c.value).where(probe.c.id == 1))
                == "error-pending-2"
            )
            with factory() as observer:
                error_capture["uncommitted_not_visible"] = (
                    observer.scalar(select(probe.c.value).where(probe.c.id == 1))
                    == "baseline"
                )
        finally:
            exports.MAX_EXPORT_BYTES = original_limit
            if caller_transaction.is_active:
                caller_transaction.rollback()
            caller.close()
        with factory() as observer:
            error_capture["caller_rollback_preserved_baseline"] = (
                observer.scalar(select(probe.c.value).where(probe.c.id == 1))
                == "baseline"
            )
        error_snapshot_ids = snapshot_connection_ids["error_export"]
        require(len(set(error_snapshot_ids)) == 1, "ERROR_SNAPSHOT_CONNECTION_COUNT")
        error_capture["snapshot_connection_id"] = error_snapshot_ids[0]
        require(
            int(error_capture["caller_connection_id"])
            != int(error_capture["snapshot_connection_id"]),
            "ERROR_CONNECTIONS_NOT_INDEPENDENT",
        )
        require(
            error_capture["error_code"] == exports.REPORT_EXPORT_LIMIT_ERROR_CODE
            and error_capture["error_status_code"] == 413
            and error_capture["error_resource"] == "utf8_bytes",
            "BYTE_LIMIT_ERROR_CONTRACT_MISMATCH",
        )
        require(
            error_capture["pool_checked_out_before"]
            == error_capture["pool_checked_out_after"]
            == 1,
            "ERROR_EXPORT_CONNECTION_NOT_RELEASED",
        )
        require(
            all(
                error_capture[key] is True
                for key in (
                    "caller_transaction_same",
                    "caller_transaction_active",
                    "caller_continued",
                    "uncommitted_not_visible",
                    "caller_rollback_preserved_baseline",
                )
            ),
            "ERROR_EXPORT_TOUCHED_CALLER_TRANSACTION",
        )

        validation_stage["name"] = "final_isolation"
        active_phase["name"] = "final_isolation"
        with engine.connect() as connection:
            default_isolation_after = connection.exec_driver_sql(
                "SELECT @@transaction_isolation"
            ).scalar_one()
            final_probe_value = connection.execute(
                select(probe.c.value).where(probe.c.id == 1)
            ).scalar_one()
            final_case_count = connection.scalar(
                select(func.count()).select_from(CaseRun)
            )
            final_step_count = connection.scalar(
                select(func.count()).select_from(StepRun)
            )
        require(
            _normalize_isolation(default_isolation_after) == "READ-COMMITTED",
            "POOL_ISOLATION_WAS_CONTAMINATED",
        )
        require(final_probe_value == "baseline", "CALLER_PROBE_WAS_COMMITTED")
        require(
            final_case_count == CASE_COUNT and final_step_count == CASE_COUNT,
            "SEEDED_GRAPH_COUNT_CHANGED",
        )
        require(pool_checked_out() == 0, "FINAL_POOL_CONNECTION_LEAK")

        statement_audit: dict[str, Any] = {}
        allowed_snapshot_operations = {
            "SELECT",
            "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
            "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
            "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        }
        for phase in (
            "status_export",
            "cross_page_export",
            "error_export",
        ):
            identifiers = sorted(set(snapshot_connection_ids[phase]))
            require(len(identifiers) == 1, f"{phase.upper()}_SNAPSHOT_ID_COUNT")
            identifier = identifiers[0]
            operations = statement_operations[phase][identifier]
            counts = Counter(operations)
            only_select_and_boundaries = all(
                operation in allowed_snapshot_operations for operation in operations
            )
            has_exact_start = (
                counts["START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"] == 1
            )
            has_selects = counts["SELECT"] > 0
            statement_audit[phase] = {
                "snapshot_connection_id": identifier,
                "operation_counts": dict(sorted(counts.items())),
                "only_select_and_transaction_boundaries": only_select_and_boundaries,
                "exact_consistent_read_only_start": has_exact_start,
                "has_product_selects": has_selects,
                "normalized_operations_sha256": _json_sha256(operations),
            }
            require(
                only_select_and_boundaries and has_exact_start and has_selects,
                f"{phase.upper()}_STATEMENT_AUDIT_FAILED",
            )

        validation_stage["name"] = "statement_audit"
        active_phase["name"] = "pool_release"
        event.remove(engine, "before_cursor_execute", record_statement)
        pool_final = pool_checked_out()
        require(pool_final == 0, "POOL_NOT_EMPTY_BEFORE_DISPOSE")

        return {
            "schema": {
                "creation": "Base.metadata.create_all",
                "model_table_count": len(table_names),
                "seeded_cases": CASE_COUNT,
                "seeded_steps": CASE_COUNT,
                "seeded_evidence": 0,
            },
            "database": {
                "engine_default_isolation_before": _normalize_isolation(
                    default_isolation_before
                ),
                "engine_default_isolation_after": _normalize_isolation(
                    default_isolation_after
                ),
                "final_probe_value": final_probe_value,
                "final_case_count": final_case_count,
                "final_step_count": final_step_count,
            },
            "read_only_probe": read_only_probe,
            "status_snapshot": status_capture,
            "cross_page_snapshot": cross_page_capture,
            "byte_limit_exception": error_capture,
            "statement_audit": statement_audit,
            "pool_checked_out_final": pool_final,
            "checks": {
                "real_repeatable_read_snapshot_started": True,
                "read_only_transaction_rejected_write": True,
                "status_export_used_one_old_snapshot": True,
                "cross_page_export_used_one_old_snapshot": True,
                "caller_uncommitted_work_preserved_after_success": True,
                "caller_uncommitted_work_preserved_after_exception": True,
                "independent_connection_released_after_success": True,
                "independent_connection_released_after_exception": True,
                "product_snapshot_business_statements_select_only": True,
                "pool_isolation_reset_to_read_committed": True,
                "product_source_path_used": True,
            },
        }
    finally:
        if event.contains(engine, "before_cursor_execute", record_statement):
            event.remove(engine, "before_cursor_execute", record_statement)
        engine.dispose()


def _safe_resource_label(container_name: str, run_id: str) -> bool:
    observed = _docker(
        "inspect",
        "--format",
        (
            '{{index .Config.Labels "com.openai.codex.package"}}|'
            '{{index .Config.Labels "com.openai.codex.run"}}'
        ),
        container_name,
    )
    return observed == f"{LABEL_PACKAGE_VALUE}|{run_id}"


def _safe_volume_label(volume_name: str, run_id: str) -> bool:
    observed = _docker(
        "volume",
        "inspect",
        "--format",
        (
            '{{index .Labels "com.openai.codex.package"}}|'
            '{{index .Labels "com.openai.codex.run"}}'
        ),
        volume_name,
    )
    return observed == f"{LABEL_PACKAGE_VALUE}|{run_id}"


def main() -> int:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = secrets.token_hex(6)
    container_name = f"p6-x1m-mysql-{run_id}"
    volume_name = f"p6-x1m-mysql-{run_id}-data"
    database_name = f"p6x1m_{run_id}"
    database_user = f"p6x1m_{run_id}"
    credential_file = EVIDENCE_ROOT / f"credentials-{run_id}.env"
    host_port = _free_loopback_port()
    container_created = False
    volume_created = False
    engine_result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    cleanup_errors: list[str] = []
    before_inventory: dict[str, Any] | None = None
    after_inventory: dict[str, Any] | None = None
    source_before = _source_snapshot()
    official_before = _manifest_snapshot()
    runtime: dict[str, Any] = {
        "image": IMAGE,
        "container_name": container_name,
        "volume_name": volume_name,
        "database_name": database_name,
        "host_binding": f"127.0.0.1:{host_port}",
        "container_port": 3306,
        "labels": {
            LABEL_PACKAGE: LABEL_PACKAGE_VALUE,
            LABEL_RUN: run_id,
        },
    }
    current_stage = "initialization"
    validation_stage = {"name": "product_validation"}

    try:
        require(
            all(item["matches"] for item in source_before.values()),
            "REVIEWED_SOURCE_HASH_MISMATCH",
        )
        require(
            len(run_id) == 12
            and container_name.startswith("p6-x1m-mysql-")
            and volume_name.endswith("-data"),
            "RESOURCE_NAME_GUARD_FAILED",
        )
        current_stage = "docker_inventory"
        runtime["docker_server_version"] = _docker(
            "version", "--format", "{{.Server.Version}}"
        )
        runtime["image_id"] = _docker(
            "image", "inspect", IMAGE, "--format", "{{.Id}}"
        )
        before_inventory = _docker_inventory()

        current_stage = "credential_file"
        database_password = secrets.token_hex(24)
        root_password = secrets.token_hex(32)
        credential_file.write_text(
            "\n".join(
                (
                    f"MYSQL_DATABASE={database_name}",
                    f"MYSQL_USER={database_user}",
                    f"MYSQL_PASSWORD={database_password}",
                    f"MYSQL_ROOT_PASSWORD={root_password}",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(credential_file, 0o600)

        current_stage = "volume_create"
        _docker(
            "volume",
            "create",
            "--label",
            f"{LABEL_PACKAGE}={LABEL_PACKAGE_VALUE}",
            "--label",
            f"{LABEL_RUN}={run_id}",
            volume_name,
        )
        volume_created = True

        current_stage = "container_create"
        _docker(
            "create",
            "--name",
            container_name,
            "--label",
            f"{LABEL_PACKAGE}={LABEL_PACKAGE_VALUE}",
            "--label",
            f"{LABEL_RUN}={run_id}",
            "--env-file",
            str(credential_file.resolve()),
            "--mount",
            f"source={volume_name},target=/var/lib/mysql",
            "--publish",
            f"127.0.0.1:{host_port}:3306",
            "--health-cmd",
            'mysqladmin ping -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent',
            "--health-interval",
            "2s",
            "--health-timeout",
            "5s",
            "--health-retries",
            "60",
            IMAGE,
            "--character-set-server=utf8mb4",
            "--collation-server=utf8mb4_0900_ai_ci",
        )
        container_created = True
        credential_file.unlink()

        require(
            _safe_resource_label(container_name, run_id),
            "CONTAINER_LABEL_MISMATCH",
        )
        require(_safe_volume_label(volume_name, run_id), "VOLUME_LABEL_MISMATCH")
        mount_lines = _docker_lines(
            "inspect",
            "--format",
            '{{range .Mounts}}{{printf "%s|%s|%s\\n" .Type .Name .Destination}}{{end}}',
            container_name,
        )
        require(
            mount_lines == [f"volume|{volume_name}|/var/lib/mysql"],
            "CONTAINER_MOUNT_SCOPE_MISMATCH",
        )
        runtime["mounts"] = mount_lines
        runtime["container_image_id"] = _docker(
            "inspect", "--format", "{{.Image}}", container_name
        )
        require(
            runtime["container_image_id"] == runtime["image_id"],
            "CONTAINER_IMAGE_MISMATCH",
        )

        current_stage = "container_start"
        _docker("start", container_name)
        _wait_healthy(container_name)
        runtime["health_before_validation"] = "healthy"
        runtime["container_pid"] = int(
            _docker("inspect", "--format", "{{.State.Pid}}", container_name)
        )
        runtime["published_port"] = _docker("port", container_name, "3306/tcp")
        require(
            runtime["published_port"] == f"127.0.0.1:{host_port}",
            "MYSQL_PORT_NOT_LOOPBACK_ONLY",
        )
        runtime["mysql_version"] = _docker("exec", container_name, "mysql", "--version")
        runtime["sqlalchemy_version"] = version("sqlalchemy")
        runtime["pymysql_version"] = version("pymysql")

        current_stage = "product_validation"
        from sqlalchemy import URL

        database_url = URL.create(
            "mysql+pymysql",
            username=database_user,
            password=database_password,
            host="127.0.0.1",
            port=host_port,
            database=database_name,
            query={"charset": "utf8mb4"},
        )
        os.environ["APP_DATABASE_URL"] = database_url.render_as_string(
            hide_password=False
        )
        os.environ["APP_RUNNER_DISCONNECT_SCAN_ENABLED"] = "false"
        sys.path.insert(0, str(BACKEND_ROOT))
        engine_result = _run_product_validation(database_url, validation_stage)
        require(
            all(engine_result["checks"].values()),
            "PRODUCT_VALIDATION_CHECK_FAILED",
        )
        current_stage = "post_source_confirmation"
        source_after_validation = _source_snapshot()
        require(
            source_after_validation == source_before,
            "PRODUCT_SOURCE_CHANGED_DURING_VALIDATION",
        )
    except Exception as exc:  # noqa: BLE001 - persist only safe type/stage/code
        failure = {
            "stage": (
                validation_stage["name"]
                if current_stage == "product_validation"
                else current_stage
            ),
            "error_type": type(exc).__name__,
            "error_code": str(exc) if isinstance(exc, ValidationFailure) else None,
        }
    finally:
        current_stage = "cleanup"
        if credential_file.is_file():
            try:
                credential_file.unlink()
            except OSError:
                cleanup_errors.append("CREDENTIAL_FILE_REMOVE_FAILED")

        if container_created:
            try:
                if not _safe_resource_label(container_name, run_id):
                    cleanup_errors.append("CONTAINER_LABEL_GUARD_FAILED")
                else:
                    _docker("rm", "--force", container_name)
            except Exception:  # noqa: BLE001 - safe cleanup code only
                cleanup_errors.append("CONTAINER_REMOVE_FAILED")

        if volume_created:
            try:
                if not _safe_volume_label(volume_name, run_id):
                    cleanup_errors.append("VOLUME_LABEL_GUARD_FAILED")
                else:
                    _docker("volume", "rm", volume_name)
            except Exception:  # noqa: BLE001 - safe cleanup code only
                cleanup_errors.append("VOLUME_REMOVE_FAILED")

        try:
            container_remaining = bool(
                _docker_lines(
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name=^/{container_name}$",
                )
            )
        except Exception:  # noqa: BLE001
            container_remaining = None
            cleanup_errors.append("CONTAINER_ABSENCE_CHECK_FAILED")
        try:
            volume_remaining = bool(
                _docker_lines(
                    "volume",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"name=^{volume_name}$",
                )
            )
        except Exception:  # noqa: BLE001
            volume_remaining = None
            cleanup_errors.append("VOLUME_ABSENCE_CHECK_FAILED")
        try:
            after_inventory = _docker_inventory()
        except Exception:  # noqa: BLE001
            cleanup_errors.append("FINAL_DOCKER_INVENTORY_FAILED")

    source_after = _source_snapshot()
    official_after = _manifest_snapshot()
    cleanup = {
        "container_absent": container_remaining is False,
        "volume_absent": volume_remaining is False,
        "credential_file_absent": not credential_file.exists(),
        "docker_inventory_unchanged": (
            before_inventory is not None
            and after_inventory is not None
            and before_inventory == after_inventory
        ),
        "official_manifests_unchanged": official_before == official_after,
        "cleanup_errors": cleanup_errors,
        "before_inventory": before_inventory,
        "after_inventory": after_inventory,
        "official_manifest_hashes_before": official_before,
        "official_manifest_hashes_after": official_after,
    }
    cleanup_ready = (
        all(
            cleanup[key] is True
            for key in (
                "container_absent",
                "volume_absent",
                "credential_file_absent",
                "docker_inventory_unchanged",
                "official_manifests_unchanged",
            )
        )
        and not cleanup_errors
    )
    source_unchanged = source_before == source_after and all(
        item["matches"] for item in source_after.values()
    )
    passed = failure is None and engine_result is not None and cleanup_ready and source_unchanged
    result = {
        "schema_version": 1,
        "package": PACKAGE,
        "status": "PASSED" if passed else "FAILED",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "source_hashes_before": source_before,
        "source_hashes_after": source_after,
        "source_unchanged": source_unchanged,
        "runtime": runtime,
        "validation": engine_result,
        "cleanup": cleanup,
        "failure": failure,
    }
    _write_json_atomic(RESULT_FILE, result)
    print(
        json.dumps(
            {
                "package": PACKAGE,
                "status": result["status"],
                "evidence": str(RESULT_FILE),
                "failure": failure,
                "cleanup_ready": cleanup_ready,
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
