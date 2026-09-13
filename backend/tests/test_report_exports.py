import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker
from test_reports import (
    CANARY,
    ReportClient,
    _as,
    _identity,
    _privacy_snapshot,
    _run,
)
from test_reports import report_client as _shared_report_client  # noqa: F401

from app.core.exceptions import AppError
from app.main import app
from app.modules.environments.models import Environment
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project
from app.modules.reports import exports
from app.modules.runs.models import CaseRun, StepRun
from app.modules.runs.models import TestRun as RunModel
from app.modules.test_cases.models import TestCase as ApiCaseModel
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_healing.models import WebHealingProposal


@pytest.fixture(name="report_export_client")
def imported_report_client(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_report_client")


def _export(client: TestClient, run_id: str, export_format: str = "html") -> Any:
    encoded = quote(run_id, safe="")
    return client.get(
        f"/api/v1/reports/{encoded}/export",
        params={"format": export_format},
    )


def _body(response: Any) -> str:
    return response.content.decode("utf-8", errors="strict")


def _assert_download_headers(response: Any, extension: str) -> None:
    assert response.headers["content-type"] == {
        "md": "text/markdown; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }[extension]
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(
        rf'attachment; filename="ai-test-report-[A-Za-z0-9._-]+\.{extension}"',
        disposition,
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def _seed_full_pages(report_client: ReportClient, *, total: int = 105) -> None:
    with report_client.session_factory() as session:
        for sequence in range(total, 1, -1):
            session.add(
                CaseRun(
                    id=1_000 + sequence,
                    run_id="run-api",
                    sequence_no=sequence,
                    case_id=101,
                    case_version_id=101,
                    status="SUCCESS",
                    duration=sequence,
                    retry_count=0,
                )
            )
            session.add(
                StepRun(
                    id=2_000 + sequence,
                    case_run_id=11,
                    sequence_no=sequence,
                    node_id=f"node-{sequence:03}",
                    step_name=f"Step {sequence:03}",
                    step_type="API_REQUEST",
                    status="SUCCESS",
                    duration=sequence,
                    retry_count=0,
                )
            )
        base_time = datetime(2026, 9, 10, 4)
        for sequence in range(total, 0, -1):
            session.add(
                EvidenceArtifact(
                    id=f"export-artifact-{sequence:03}",
                    project_id=1,
                    run_id="run-api",
                    case_run_id=11,
                    step_run_id=111,
                    artifact_type="RESPONSE",
                    file_name=f"response-{sequence:03}.json",
                    mime="application/json",
                    size=sequence,
                    sha256=f"{sequence:064x}"[-64:],
                    minio_bucket="private-bucket",
                    minio_key=f"private/{sequence}.json",
                    artifact_metadata={"sequence": sequence},
                    created_at=base_time + timedelta(microseconds=sequence),
                )
            )
        session.commit()


def _copy_report_database(
    report_client: ReportClient,
    database_path: Path,
) -> tuple[Any, Any]:
    source = report_client.session_factory.kw["bind"].raw_connection()
    destination = sqlite3.connect(database_path)
    try:
        source.driver_connection.backup(destination)
    finally:
        destination.close()
        source.close()
    engine = create_engine(f"sqlite:///{database_path}")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    return engine, factory


def test_markdown_and_html_exports_are_complete_safe_read_only_attachments(
    report_export_client: ReportClient,
) -> None:
    client = _as(report_export_client, _identity("viewer"))
    before = _privacy_snapshot(report_export_client.session_factory)

    markdown = _export(client, "run-web", "markdown")
    assert markdown.status_code == 200, markdown.text
    _assert_download_headers(markdown, "md")
    assert "Content-Security-Policy" not in markdown.headers
    markdown_body = _body(markdown)
    assert "Run 摘要与引用" in markdown_body
    assert "Cases（完整，共 1 条）" in markdown_body
    assert "Steps（完整，共 2 条）" in markdown_body
    assert "Evidence（完整，共 3 条）" in markdown_body
    assert "Web Failure Analyses" in markdown_body
    assert "Web Healing Proposals" in markdown_body

    html_response = _export(client, "run-web", "html")
    assert html_response.status_code == 200, html_response.text
    _assert_download_headers(html_response, "html")
    assert html_response.headers["content-security-policy"] == exports.HTML_CSP
    html_body = _body(html_response)
    assert html_body.startswith("<!doctype html>")
    assert 'data-record-kind="case"' in html_body
    assert html_body.count('data-record-kind="step"') == 2
    assert html_body.count('data-record-kind="evidence"') == 3
    assert "WEB_EVIDENCE_ERROR" in html_body
    assert "NOT_RECORDED" in html_body
    assert "raw_ai_content_exposed" not in html_body.lower()
    assert "Raw AI Content Exposed" in html_body
    for response in (markdown, html_response):
        body = _body(response)
        assert CANARY not in body
        assert "minio" not in body.lower()
        assert "private-bucket" not in body
        assert "storage_state" not in body.lower()

    assert _privacy_snapshot(report_export_client.session_factory) == before


def test_export_permissions_archived_history_format_and_filename_are_safe(
    report_export_client: ReportClient,
) -> None:
    client = _as(report_export_client, _identity("viewer"))
    assert _export(client, "run-hidden").status_code == 404
    assert _export(client, "run-missing").status_code == 404
    archived = _export(client, "run-archived", "markdown")
    assert archived.status_code == 200, archived.text
    assert "ARCHIVED" in archived.text
    assert client.get("/api/v1/reports/run-api/export").status_code == 422
    assert _export(client, "run-api", "pdf").status_code == 422

    malicious_run_id = 'run-evil";<script onload=boom>'
    with report_export_client.session_factory() as session:
        session.add(
            _run(
                malicious_run_id,
                "RUN-EVIL",
                "API_CASE",
                1,
                "SUCCESS",
                datetime(2026, 9, 10, 6),
                target_id=101,
                version_id=101,
                passed=1,
            )
        )
        session.add(
            CaseRun(
                id=999,
                run_id=malicious_run_id,
                sequence_no=1,
                case_id=101,
                case_version_id=101,
                status="SUCCESS",
            )
        )
        session.commit()
    safe = _export(client, malicious_run_id)
    assert safe.status_code == 200, safe.text
    disposition = safe.headers["content-disposition"]
    assert "<" not in disposition and ">" not in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2

    admin = _as(report_export_client, _identity("admin", ["ADMIN"]))
    assert _export(admin, "run-hidden").status_code == 200


def test_export_preserves_three_run_types_versions_statuses_and_empty_denominator(
    report_export_client: ReportClient,
) -> None:
    client = _as(report_export_client, _identity("viewer"))

    api = _body(_export(client, "run-api"))
    assert "API_CASE" in api
    assert "LOCKED_BY_RUN" in api
    assert "current_version_id&quot;:102" in api
    assert "is_current_version&quot;:false" in api
    assert "Retry Count</th><td><pre>3" in api
    assert "请求配置快照" in api and "NOT_RECORDED" in api

    web = _body(_export(client, "run-web"))
    assert "Run Status</th><td><pre>FAILED" in web
    assert "Result Status</th><td><pre>SUCCESS" in web
    assert "WEB_EVIDENCE_ERROR" in web

    scenario = _body(_export(client, "run-scenario"))
    assert "SCENARIO" in scenario and "TIMEOUT" in scenario
    cancelled = _body(_export(client, "run-cancel"))
    assert "CANCELLED" in cancelled and "NOT_RECORDED" in cancelled
    skipped = _body(_export(client, "run-skip"))
    assert "denominator&quot;:0" in skipped
    assert "value&quot;:null" in skipped
    assert "SKIPPED" in skipped
    pending = _body(_export(client, "run-pending"))
    assert "当前 Run 尚未终态" in pending
    assert "UNAVAILABLE" in pending


def test_export_crosses_first_page_with_stable_order_and_non_n_plus_one_queries(
    report_export_client: ReportClient,
) -> None:
    client = _as(report_export_client, _identity("viewer"))
    engine = report_export_client.session_factory.kw["bind"]

    def query_count(run_id: str) -> tuple[int, Any]:
        statements: list[str] = []

        def record_statement(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            response = _export(client, run_id)
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
        assert all(
            item.lstrip().upper().startswith(("SELECT", "BEGIN"))
            for item in statements
        )
        return len(statements), response

    base_queries, base = query_count("run-api")
    assert base.status_code == 200, base.text
    _seed_full_pages(report_export_client)
    expanded_queries, expanded = query_count("run-api")
    assert expanded.status_code == 200, expanded.text
    expanded_body = _body(expanded)
    assert expanded_body.count('data-record-kind="case"') == 105
    assert expanded_body.count('data-record-kind="step"') == 105
    assert expanded_body.count('data-record-kind="evidence"') == 105
    assert expanded_body.index("Case 2 / ID 1002") < expanded_body.index(
        "Case 105 / ID 1105"
    )
    assert expanded_body.index("node-002") < expanded_body.index("node-105")
    assert expanded_body.index("export-artifact-001") < expanded_body.index(
        "export-artifact-105"
    )
    assert expanded_queries <= base_queries + 40
    assert expanded_queries < 105


def test_export_keeps_one_wal_snapshot_during_concurrent_status_change(
    report_export_client: ReportClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _copy_report_database(
        report_export_client,
        tmp_path / "status-snapshot.sqlite",
    )
    original_fetch = exports.list_report_cases
    original_render = exports._render_html
    captured: list[Any] = []
    changed = False

    def concurrent_fetch(*args: Any, **kwargs: Any) -> Any:
        nonlocal changed
        if not changed:
            changed = True
            with factory() as writer:
                writer.execute(
                    update(CaseRun).where(CaseRun.id == 11).values(status="FAILED")
                )
                writer.commit()
        return original_fetch(*args, **kwargs)

    def capture(snapshot: Any) -> bytes:
        captured.append(snapshot)
        return original_render(snapshot)

    monkeypatch.setattr(exports, "list_report_cases", concurrent_fetch)
    monkeypatch.setattr(exports, "_render_html", capture)
    try:
        with factory() as reader, reader.begin():
            reader.scalar(select(RunModel.id).limit(1))
            caller_transaction = reader.get_transaction()
            exports.create_report_export(
                reader,
                _identity("viewer"),
                "run-api",
                "html",
            )
            assert reader.get_transaction() is caller_transaction
        snapshot = captured[0]
        successful_cases = sum(case.status == "SUCCESS" for case in snapshot.cases)
        assert snapshot.detail.summary.case_status_counts.success == successful_cases == 1
    finally:
        engine.dispose()


def test_export_keeps_old_cross_page_field_in_one_wal_snapshot(
    report_export_client: ReportClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_full_pages(report_export_client)
    engine, factory = _copy_report_database(
        report_export_client,
        tmp_path / "cross-page-snapshot.sqlite",
    )
    original_fetch = exports.list_report_cases
    original_render = exports._render_html
    captured: list[Any] = []
    changed = False

    def concurrent_fetch(*args: Any, **kwargs: Any) -> Any:
        nonlocal changed
        filters = args[3]
        if filters.page == 2 and not changed:
            changed = True
            with factory() as writer:
                writer.execute(
                    update(CaseRun)
                    .where(CaseRun.id == 1_105)
                    .values(error_message="CHANGED_AFTER_FIRST_PAGE")
                )
                writer.commit()
        return original_fetch(*args, **kwargs)

    def capture(snapshot: Any) -> bytes:
        captured.append(snapshot)
        return original_render(snapshot)

    monkeypatch.setattr(exports, "list_report_cases", concurrent_fetch)
    monkeypatch.setattr(exports, "_render_html", capture)
    try:
        with factory() as reader:
            exports.create_report_export(
                reader,
                _identity("viewer"),
                "run-api",
                "html",
            )
        last_case = next(case for case in captured[0].cases if case.id == 1_105)
        assert last_case.error_message is None
    finally:
        engine.dispose()


def test_mysql_snapshot_transaction_is_repeatable_read_and_read_only() -> None:
    class FakeDialect:
        name = "mysql"

    class FakeConnection:
        dialect = FakeDialect()

        def __init__(self) -> None:
            self.execution_option_calls: list[dict[str, str]] = []
            self.statements: list[str] = []

        def execution_options(self, **options: str) -> Any:
            self.execution_option_calls.append(options)
            return self

        def exec_driver_sql(self, statement: str) -> None:
            self.statements.append(statement)

    connection = FakeConnection()
    assert exports._begin_consistent_read(connection) is connection  # type: ignore[arg-type]
    assert connection.execution_option_calls == [
        {"isolation_level": "REPEATABLE READ"}
    ]
    assert connection.statements == [
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
    ]


def test_single_connection_memory_database_refuses_without_touching_caller_transaction(
    report_export_client: ReportClient,
) -> None:
    with report_export_client.session_factory() as reader, reader.begin():
        reader.scalar(select(RunModel.id).limit(1))
        caller_transaction = reader.get_transaction()
        with pytest.raises(AppError) as rejected:
            exports.create_report_export(
                reader,
                _identity("viewer"),
                "run-api",
                "html",
            )
        assert rejected.value.code == exports.REPORT_EXPORT_SNAPSHOT_ERROR_CODE
        assert reader.get_transaction() is caller_transaction
        assert reader.in_transaction()


def test_export_count_and_utf8_output_limits_fail_before_a_partial_attachment(
    report_export_client: ReportClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert exports.MAX_EXPORT_CASES == 2_000
    assert exports.MAX_EXPORT_STEPS == 10_000
    assert exports.MAX_EXPORT_EVIDENCE == 5_000
    assert exports.MAX_EXPORT_REQUIREMENT_SOURCES == 2_000
    assert exports.MAX_EXPORT_BYTES == 16 * 1024 * 1024
    for resource, limit in (
        ("cases", exports.MAX_EXPORT_CASES),
        ("steps", exports.MAX_EXPORT_STEPS),
        ("evidence", exports.MAX_EXPORT_EVIDENCE),
    ):
        exports._enforce_count_limit(resource, limit, limit)
        with pytest.raises(AppError) as over_count:
            exports._enforce_count_limit(resource, limit + 1, limit)
        assert over_count.value.code == exports.REPORT_EXPORT_LIMIT_ERROR_CODE
        assert over_count.value.details == {
            "resource": resource,
            "limit": limit,
            "observed": limit + 1,
        }

    client = _as(report_export_client, _identity("viewer"))
    monkeypatch.setattr(exports, "MAX_EXPORT_CASES", 0)
    monkeypatch.setattr(
        exports,
        "list_report_cases",
        lambda *args, **kwargs: pytest.fail("full Case paging must not start after pre-count"),
    )
    rejected = _export(client, "run-api")
    assert rejected.status_code == 413, rejected.text
    assert rejected.json()["code"] == exports.REPORT_EXPORT_LIMIT_ERROR_CODE
    assert rejected.json()["details"] == {
        "resource": "cases",
        "limit": 0,
        "observed": 1,
    }
    assert "content-disposition" not in rejected.headers

    monkeypatch.setattr(exports, "MAX_EXPORT_CASES", 2_000)
    monkeypatch.undo()
    monkeypatch.setattr(exports, "MAX_EXPORT_BYTES", 1)
    rejected = _export(client, "run-api", "markdown")
    assert rejected.status_code == 413, rejected.text
    assert rejected.json()["details"]["resource"] == "utf8_bytes"
    assert "content-disposition" not in rejected.headers

    writer = exports._Utf8Writer(maximum_bytes=4)
    writer.append("你a")
    assert writer.build() == "你a".encode()
    with pytest.raises(AppError) as over_bytes:
        writer.append("b")
    assert over_bytes.value.details == {
        "resource": "utf8_bytes",
        "limit": 4,
        "observed": 5,
    }
    with pytest.raises(AppError) as invalid_utf8:
        exports._Utf8Writer().append("\ud800")
    assert invalid_utf8.value.code == exports.REPORT_EXPORT_ENCODING_ERROR_CODE

    inconsistent_page = type(
        "ChangedPage",
        (),
        {"total": 2, "page": 1, "items": []},
    )()
    with pytest.raises(AppError) as changed:
        exports._collect_all(1, lambda page: inconsistent_page)
    assert changed.value.code == exports.REPORT_EXPORT_SNAPSHOT_ERROR_CODE


def test_malicious_dynamic_content_is_inert_in_html_and_markdown(
    report_export_client: ReportClient,
) -> None:
    probe_url = os.environ.get("P6_X1_PROBE_URL", "https://attacker.invalid/probe")
    malicious = (
        "P6_X1_BROWSER_MARKER </style><script>document.title='P6_X1_EXECUTED'</script>"
        f'<img src="{probe_url}" onerror="document.title=\'P6_X1_EXECUTED\'"> '
        f"![image]({probe_url}) [click]({probe_url}) ``` | end"
    )
    with report_export_client.session_factory() as session:
        project = session.get(Project, 1)
        environment = session.get(Environment, 1)
        api_case = session.get(ApiCaseModel, 101)
        run = session.get(RunModel, "run-web")
        case = session.get(CaseRun, 12)
        step = session.get(StepRun, 121)
        artifact = session.get(EvidenceArtifact, "artifact-0")
        analysis = session.get(WebFailureAnalysis, 1)
        healing = session.get(WebHealingProposal, 1)
        assert all(
            item is not None
            for item in (
                project,
                environment,
                api_case,
                run,
                case,
                step,
                artifact,
                analysis,
                healing,
            )
        )
        project.name = malicious
        environment.name = malicious
        api_case.name = malicious
        run.error_message = malicious
        case.error_message = malicious
        step.node_id = malicious
        step.step_name = malicious
        step.error_message = malicious
        artifact.file_name = malicious
        artifact.artifact_metadata = {"diagnostic": malicious}
        analysis.structured_result = {"summary": malicious}
        healing.reason = malicious
        session.commit()

    client = _as(report_export_client, _identity("viewer"))
    before = _privacy_snapshot(report_export_client.session_factory)
    html_response = _export(client, "run-web", "html")
    assert html_response.status_code == 200, html_response.text
    html_body = _body(html_response)
    assert "P6_X1_BROWSER_MARKER" in html_body
    assert "&lt;script&gt;" in html_body
    assert not re.search(r"<(?:script|img|iframe|form)\b", html_body, re.I)
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html_body, re.I)
    assert f'src="{probe_url}"' not in html_body
    assert f'href="{probe_url}"' not in html_body
    assert '<meta http-equiv="Content-Security-Policy"' in html_body

    markdown = _export(client, "run-web", "markdown")
    assert markdown.status_code == 200, markdown.text
    markdown_body = _body(markdown)
    assert "P6\\_X1\\_BROWSER\\_MARKER" in markdown_body
    assert "<script>" not in markdown_body
    assert "![image]" not in markdown_body
    assert "[click](" not in markdown_body
    assert "```" not in markdown_body
    assert probe_url not in markdown_body
    assert _privacy_snapshot(report_export_client.session_factory) == before

    artifact_path = os.environ.get("P6_X1_BROWSER_ARTIFACT")
    if artifact_path:
        output = Path(artifact_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(html_response.content)
        print(f"BROWSER_ARTIFACT={output.resolve()}")


def test_export_openapi_declares_required_closed_format_enum() -> None:
    specification = app.openapi()
    operation = specification["paths"]["/api/v1/reports/{run_id}/export"]["get"]
    format_parameter = next(
        item for item in operation["parameters"] if item["name"] == "format"
    )
    assert format_parameter["required"] is True
    assert format_parameter["schema"]["enum"] == ["markdown", "html"]
