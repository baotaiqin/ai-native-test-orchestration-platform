import copy
import json
import os
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime

os.environ["APP_RUNNER_DISCONNECT_SCAN_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project, ProjectMember
from app.modules.reports.schemas import (
    DATABASE_INTEGER_MAX,
    REPORT_DATE_MAX,
    REPORT_DATE_MIN,
    REPORT_PAGE_MAX,
)
from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)
from app.modules.runners.models import Runner
from app.modules.runs.models import (
    CaseRun,
    RunApiExecutionResult,
    RunScenarioExecutionResult,
    RunWebExecutionResult,
    StepRun,
)
from app.modules.runs.models import (
    TestRun as RunModel,
)
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.test_cases.models import TestCase as ApiTestCase
from app.modules.test_cases.models import TestCaseVersion as ApiCaseVersionModel
from app.modules.web_cases.models import WebCase, WebCaseVersion
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_healing.models import WebHealingProposal

CANARY = "synthetic_report_secret_8d2a"


@dataclass
class ReportClient:
    client: TestClient
    session_factory: sessionmaker[Session]


def _run(
    run_id: str,
    run_code: str,
    run_type: str,
    project_id: int,
    status: str,
    created_at: datetime,
    *,
    target_id: int,
    version_id: int,
    environment_id: int | None = 1,
    runner_id: str | None = "runner-1",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    total: int = 1,
    passed: int = 0,
    failed: int = 0,
    review: int = 0,
    timeout: int = 0,
) -> RunModel:
    targets = {
        "case_id": target_id if run_type == "API_CASE" else None,
        "case_version_id": version_id if run_type == "API_CASE" else None,
        "scenario_id": target_id if run_type == "SCENARIO" else None,
        "scenario_version_id": version_id if run_type == "SCENARIO" else None,
        "web_case_id": target_id if run_type == "WEB_CASE" else None,
        "web_case_version_id": version_id if run_type == "WEB_CASE" else None,
    }
    return RunModel(
        id=run_id,
        run_code=run_code,
        run_type=run_type,
        project_id=project_id,
        environment_id=environment_id,
        runner_id=runner_id,
        status=status,
        trigger_type="MANUAL",
        required_capabilities=[],
        required_tags=[],
        required_slot_type="WEB" if run_type == "WEB_CASE" else "API",
        required_slot_count=1,
        force_stopped=False,
        started_at=started_at,
        ended_at=ended_at,
        total=total,
        pass_count=passed,
        fail_count=failed,
        review_count=review,
        timeout_count=timeout,
        error_type=error_type,
        error_message=error_message,
        created_by="viewer",
        created_at=created_at,
        updated_at=ended_at or created_at,
        **targets,
    )


@pytest.fixture
def report_client() -> Generator[ReportClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        Environment.__table__,
        Runner.__table__,
        ApiTestCase.__table__,
        ApiCaseVersionModel.__table__,
        Scenario.__table__,
        ScenarioVersion.__table__,
        WebCase.__table__,
        WebCaseVersion.__table__,
        RunModel.__table__,
        CaseRun.__table__,
        RunRequirementCapture.__table__,
        RunRequirementSource.__table__,
        StepRun.__table__,
        RunApiExecutionResult.__table__,
        RunScenarioExecutionResult.__table__,
        RunWebExecutionResult.__table__,
        EvidenceArtifact.__table__,
        WebFailureAnalysis.__table__,
        WebHealingProposal.__table__,
    )
    for table in tables:
        table.create(engine)

    old = {
        "title": "locked old API",
        "auth": {"token": CANARY},
        "request": {
            "method": "POST",
            "url": f"https://api.example.test/login?token={CANARY}",
            "headers": [{"name": "Authorization", "value": f"Bearer {CANARY}"}],
            "body": {
                "type": "JSON",
                "content": {"username": "demo", "password": CANARY},
            },
            "retry_policy": {"max_retries": 3},
        },
    }
    with factory() as session:
        session.add_all(
            [
                Project(id=1, name="Visible", code="VISIBLE", owner_id="owner"),
                Project(
                    id=2,
                    name="Archived",
                    code="ARCHIVED",
                    owner_id="owner",
                    status="ARCHIVED",
                    archived_at=datetime(2026, 9, 1),
                ),
                Project(id=3, name="Hidden", code="HIDDEN", owner_id="outsider"),
                ProjectMember(project_id=1, user_id="viewer", role="VIEWER"),
                ProjectMember(project_id=2, user_id="viewer", role="VIEWER"),
                Environment(id=1, project_id=1, name="Staging", code="STG", enabled=True),
                Runner(
                    id="runner-1",
                    name="Runner One",
                    hostname="runner.local",
                    credential_digest="digest-runner-1",
                    status="ACTIVE",
                    heartbeat_interval_seconds=30,
                ),
            ]
        )
        session.add_all(
            [
                ApiTestCase(
                    id=101,
                    project_id=1,
                    code="API-101",
                    name="API current name",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=102,
                    created_by="owner",
                ),
                ApiCaseVersionModel(
                    id=101,
                    case_id=101,
                    version_no=1,
                    content=old,
                    created_by="owner",
                ),
                ApiCaseVersionModel(
                    id=102,
                    case_id=101,
                    version_no=2,
                    content={"title": "current version"},
                    created_by="owner",
                ),
                ApiTestCase(
                    id=401,
                    project_id=2,
                    code="API-ARCHIVED",
                    name="Archived history",
                    case_type="API",
                    status="ARCHIVED",
                    source="MANUAL",
                    current_version_id=401,
                    created_by="owner",
                ),
                ApiCaseVersionModel(
                    id=401,
                    case_id=401,
                    version_no=1,
                    content={"title": "archived version"},
                    created_by="owner",
                ),
                Scenario(
                    id=301,
                    project_id=1,
                    code="SCN-301",
                    name="Scenario current",
                    status="APPROVED",
                    current_version_id=302,
                    created_by="owner",
                ),
                ScenarioVersion(
                    id=301,
                    scenario_id=301,
                    version_no=1,
                    dsl={"name": "locked scenario"},
                    created_by="owner",
                ),
                ScenarioVersion(
                    id=302,
                    scenario_id=301,
                    version_no=2,
                    dsl={"name": "current scenario"},
                    created_by="owner",
                ),
                WebCase(
                    id=201,
                    project_id=1,
                    code="WEB-201",
                    name="Web current",
                    status="APPROVED",
                    current_version_id=202,
                    created_by="owner",
                ),
                WebCaseVersion(
                    id=201,
                    web_case_id=201,
                    version_no=1,
                    content={"title": "locked web"},
                    status="APPROVED",
                    approved_by="owner",
                    approved_at=datetime(2026, 9, 1),
                    created_by="owner",
                ),
                WebCaseVersion(
                    id=202,
                    web_case_id=201,
                    version_no=2,
                    content={"title": "current web"},
                    status="APPROVED",
                    approved_by="owner",
                    approved_at=datetime(2026, 9, 2),
                    created_by="owner",
                ),
            ]
        )
        session.add_all(
            [
                _run(
                    "run-api",
                    "RUN-API",
                    "API_CASE",
                    1,
                    "SUCCESS",
                    datetime(2026, 9, 10, 1),
                    target_id=101,
                    version_id=101,
                    started_at=datetime(2026, 9, 10, 1),
                    ended_at=datetime(2026, 9, 10, 1, 0, 2),
                    total=7,
                    passed=6,
                    failed=1,
                ),
                _run(
                    "run-web",
                    "RUN-WEB",
                    "WEB_CASE",
                    1,
                    "FAILED",
                    datetime(2026, 9, 10, 2),
                    target_id=201,
                    version_id=201,
                    started_at=datetime(2026, 9, 10, 2),
                    ended_at=datetime(2026, 9, 10, 2, 0, 3),
                    error_type="WEB_EVIDENCE_ERROR",
                    error_message=f"password={CANARY}",
                    failed=1,
                ),
                _run(
                    "run-scenario",
                    "RUN-SCENARIO",
                    "SCENARIO",
                    1,
                    "TIMEOUT",
                    datetime(2026, 9, 9, 15, 59, 59),
                    target_id=301,
                    version_id=301,
                    timeout=1,
                ),
                _run(
                    "run-cancel",
                    "RUN-CANCEL",
                    "API_CASE",
                    1,
                    "CANCELLED",
                    datetime(2026, 9, 10, 3),
                    target_id=101,
                    version_id=101,
                ),
                _run(
                    "run-skip",
                    "RUN-SKIP",
                    "API_CASE",
                    1,
                    "SUCCESS",
                    datetime(2026, 9, 10, 4),
                    target_id=101,
                    version_id=101,
                ),
                _run(
                    "run-pending",
                    "RUN-PENDING",
                    "API_CASE",
                    1,
                    "RUNNING",
                    datetime(2026, 9, 10, 5),
                    target_id=101,
                    version_id=101,
                ),
                _run(
                    "run-archived",
                    "RUN-ARCHIVED",
                    "API_CASE",
                    2,
                    "SUCCESS",
                    datetime(2026, 9, 8),
                    target_id=401,
                    version_id=401,
                    environment_id=None,
                    runner_id=None,
                    passed=1,
                ),
                _run(
                    "run-hidden",
                    "RUN-HIDDEN",
                    "API_CASE",
                    3,
                    "SUCCESS",
                    datetime(2026, 9, 10),
                    target_id=999,
                    version_id=999,
                    environment_id=None,
                    runner_id=None,
                    passed=1,
                ),
            ]
        )
        session.flush()
        case_runs = [
            CaseRun(
                id=11,
                run_id="run-api",
                sequence_no=1,
                case_id=101,
                case_version_id=101,
                status="SUCCESS",
                duration=2000,
                retry_count=3,
            ),
            CaseRun(
                id=12,
                run_id="run-web",
                sequence_no=1,
                web_case_id=201,
                web_case_version_id=201,
                status="FAILED",
                duration=3000,
                retry_count=0,
                error_type="WEB_EVIDENCE_ERROR",
                error_message=f"cookie={CANARY}",
            ),
            CaseRun(
                id=13,
                run_id="run-scenario",
                sequence_no=1,
                scenario_id=301,
                scenario_version_id=301,
                status="TIMEOUT",
                duration=5000,
                retry_count=0,
            ),
            CaseRun(
                id=14,
                run_id="run-cancel",
                sequence_no=1,
                case_id=101,
                case_version_id=101,
                status="CANCELLED",
                duration=0,
                retry_count=0,
            ),
            CaseRun(
                id=15,
                run_id="run-skip",
                sequence_no=1,
                case_id=101,
                case_version_id=101,
                status="SKIPPED",
                duration=0,
                retry_count=0,
            ),
            CaseRun(
                id=16,
                run_id="run-pending",
                sequence_no=1,
                case_id=101,
                case_version_id=101,
                status="CREATED",
                duration=0,
                retry_count=0,
            ),
            CaseRun(
                id=17,
                run_id="run-archived",
                sequence_no=1,
                case_id=401,
                case_version_id=401,
                status="SUCCESS",
                duration=100,
                retry_count=0,
            ),
        ]
        session.add_all(case_runs)
        session.flush()
        session.add_all(
            [
                StepRun(
                    id=111,
                    case_run_id=11,
                    sequence_no=1,
                    node_id="api_request",
                    step_name="API request",
                    step_type="API_REQUEST",
                    status="SUCCESS",
                    duration=2000,
                    retry_count=3,
                ),
                StepRun(
                    id=121,
                    case_run_id=12,
                    sequence_no=1,
                    node_id="open_page",
                    step_name="Open page",
                    step_type="NAVIGATE",
                    status="SUCCESS",
                    duration=100,
                    retry_count=0,
                ),
                StepRun(
                    id=122,
                    case_run_id=12,
                    sequence_no=2,
                    node_id="submit",
                    step_name="Submit",
                    step_type="CLICK",
                    status="SUCCESS",
                    duration=100,
                    retry_count=0,
                ),
            ]
        )
        session.add_all(
            [
                RunApiExecutionResult(
                    run_id="run-api",
                    case_run_id=11,
                    message_id="message-api",
                    outcome="HTTP_RESPONSE",
                    status="SUCCESS",
                    retry_count=3,
                    response_summary={
                        "status_code": 200,
                        "headers": {"Authorization": f"Bearer {CANARY}"},
                    },
                    assertion_results=[
                        {"status": "PASS", "message": f"password={CANARY}"}
                    ],
                    action_traces=[
                        {
                            "sequence": 1,
                            "phase": "PRE",
                            "type": "SET_VARIABLE",
                            "name": "tenant",
                            "status": "SUCCESS",
                            "duration_ms": 2,
                            "error_type": None,
                        }
                    ],
                    completed_at=datetime(2026, 9, 10, 1, 0, 2),
                ),
                RunWebExecutionResult(
                    run_id="run-web",
                    case_run_id=12,
                    message_id="message-web",
                    outcome="SUCCESS",
                    status="SUCCESS",
                    traces=[
                        {
                            "node_id": "submit",
                            "status": "SUCCESS",
                            "note": f"token={CANARY}",
                        }
                    ],
                    completed_at=datetime(2026, 9, 10, 2, 0, 2),
                ),
                RunScenarioExecutionResult(
                    run_id="run-scenario",
                    case_run_id=13,
                    message_id="message-scenario",
                    outcome="TIMEOUT",
                    status="TIMEOUT",
                    traces=[{"node_id": "wait", "status": "TIMEOUT"}],
                    error_type="STEP_TIMEOUT",
                    error_message="scenario timeout",
                    completed_at=datetime(2026, 9, 9, 16),
                ),
            ]
        )
        for index in range(3):
            session.add(
                EvidenceArtifact(
                    id=f"artifact-{index}",
                    project_id=1,
                    run_id="run-web",
                    case_run_id=12,
                    step_run_id=121 if index == 0 else None,
                    artifact_type="RESPONSE",
                    file_name=f"response-{index}.json",
                    mime="application/json",
                    size=20 + index,
                    sha256=str(index) * 64,
                    minio_bucket=f"internal-{CANARY}",
                    minio_key=f"private/{CANARY}/{index}.json",
                    artifact_metadata={
                        "status_code": 200,
                        "headers": {
                            "Authorization": f"Bearer {CANARY}",
                            "X-Request-ID": f"request-{index}",
                        },
                        "body_size_bytes": 20,
                    },
                    created_at=datetime(2026, 9, 10, 2, 1, index),
                )
            )
        session.add(
            WebFailureAnalysis(
                id=1,
                project_id=1,
                run_id="run-web",
                case_run_id=12,
                prompt_version_id=7,
                output_schema_id=8,
                ai_call_id=9,
                status="COMPLETED",
                source_snapshot={"safe": True},
                source_snapshot_sha256="a" * 64,
                source_snapshot_size=10,
                structured_result={"summary": "diagnostic", "password": CANARY},
                actual_model="model-safe",
                fallback_used=False,
                repair_used=False,
                confidence=0.8,
                needs_human_review=True,
                created_by="viewer",
                created_at=datetime(2026, 9, 10, 2, 2),
            )
        )
        session.add(
            WebHealingProposal(
                id=1,
                project_id=1,
                run_id="run-web",
                case_run_id=12,
                web_case_id=201,
                web_case_version_id=201,
                node_id="submit",
                ai_call_id=10,
                status="DRAFT",
                draft_key="DRAFT",
                source_snapshot={"safe": True},
                source_snapshot_sha256="b" * 64,
                source_snapshot_size=10,
                structured_result={"locator": {"strategy": "css", "value": "#go"}},
                old_locator={"strategy": "css", "value": "#old"},
                proposed_locator={"strategy": "css", "value": "#go"},
                confidence=0.7,
                reason=f"authorization=Bearer {CANARY}",
                evidence_candidate_index=0,
                created_by="viewer",
                created_at=datetime(2026, 9, 10, 2, 3),
            )
        )
        session.commit()

    def database_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db_session] = database_override
    try:
        with TestClient(app) as client:
            yield ReportClient(client=client, session_factory=factory)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _identity(user_id: str, roles: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        display_name=user_id,
        roles=roles or [],
    )


def _as(report_client: ReportClient, identity: CurrentUser) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: identity
    return report_client.client


def _privacy_snapshot(factory: sessionmaker[Session]) -> dict[str, object]:
    with factory() as session:
        api_result = session.scalar(select(RunApiExecutionResult))
        artifact = session.get(EvidenceArtifact, "artifact-0")
        analysis = session.get(WebFailureAnalysis, 1)
        healing = session.get(WebHealingProposal, 1)
        assert api_result and artifact and analysis and healing
        return {
            "response": copy.deepcopy(api_result.response_summary),
            "assertions": copy.deepcopy(api_result.assertion_results),
            "artifact_metadata": copy.deepcopy(artifact.artifact_metadata),
            "bucket": artifact.minio_bucket,
            "key": artifact.minio_key,
            "analysis": copy.deepcopy(analysis.structured_result),
            "healing_reason": healing.reason,
        }


def test_report_list_filters_permissions_and_archived_history(
    report_client: ReportClient,
) -> None:
    client = _as(report_client, _identity("viewer"))
    listed = client.get("/api/v1/reports", params={"project_id": 1, "page_size": 2})
    assert listed.status_code == 200
    assert listed.json()["total"] == 6
    assert listed.json()["has_more"] is True
    assert [item["run_id"] for item in listed.json()["items"]] == [
        "run-pending",
        "run-skip",
    ]

    filtered = client.get(
        "/api/v1/reports",
        params={"project_id": 1, "run_type": "WEB_CASE"},
    )
    assert filtered.status_code == 200
    assert [item["run_id"] for item in filtered.json()["items"]] == ["run-web"]

    local_day = client.get(
        "/api/v1/reports",
        params={
            "project_id": 1,
            "created_from": "2026-09-10",
            "created_to": "2026-09-10",
        },
    )
    assert local_day.status_code == 200
    assert "run-scenario" not in {
        item["run_id"] for item in local_day.json()["items"]
    }

    archived = client.get("/api/v1/reports", params={"project_id": 2})
    assert archived.status_code == 200
    assert archived.json()["items"][0]["run_id"] == "run-archived"
    assert client.get("/api/v1/reports/run-archived").status_code == 200

    assert client.get("/api/v1/reports/run-hidden").status_code == 404
    assert client.get("/api/v1/reports", params={"project_id": 3}).status_code == 404
    assert client.get("/api/v1/reports/run-missing").status_code == 404


def test_report_details_preserve_locked_versions_statuses_and_safe_audit(
    report_client: ReportClient,
) -> None:
    client = _as(report_client, _identity("viewer"))
    before = _privacy_snapshot(report_client.session_factory)

    api = client.get("/api/v1/reports/run-api")
    assert api.status_code == 200
    api_body = api.json()
    assert api_body["summary"]["target"]["version_id"] == 101
    assert api_body["summary"]["target"]["version_no"] == 1
    assert api_body["summary"]["target"]["current_version_id"] == 102
    assert api_body["summary"]["target"]["is_current_version"] is False
    assert api_body["summary"]["target"]["asset_metadata_basis"] == "CURRENT"
    assert api_body["summary"]["target"]["version_basis"] == "LOCKED_BY_RUN"
    assert api_body["summary"]["recorded_counts"] == {
        "total": 7,
        "passed": 6,
        "failed": 1,
        "review": 0,
        "timeout": 0,
        "source": "RUN_RECORD",
    }
    api_case = api_body["cases"]["items"][0]
    assert api_case["retry_count"] == 3
    assert api_case["execution_result"]["retry_count"] == 3
    assert api_body["steps"]["items"][0]["retry_count"] == 3
    actual_request = api_case["execution_result"]["actual_request"]
    assert actual_request["availability"] == "RECORDED"
    assert actual_request["value"]["method"] == "POST"
    assert actual_request["value"]["body"]["content"]["username"] == "demo"
    assert actual_request["value"]["body"]["content"]["password"] == "<redacted>"
    assert actual_request["value"]["headers"][0]["value"] == "<redacted>"
    assert CANARY not in actual_request["value"]["url"]
    assert api_case["execution_result"]["response"]["availability"] == "RECORDED"
    assert api_case["execution_result"]["traces"] == {
        "availability": "RECORDED",
        "value": [
            {
                "sequence": 1,
                "phase": "PRE",
                "type": "SET_VARIABLE",
                "name": "tenant",
                "status": "SUCCESS",
                "duration_ms": 2,
                "error_type": None,
            }
        ],
        "note": "执行时保存的 API 动作级执行轨迹（仅元数据）",
    }
    assert CANARY not in api.text

    web = client.get("/api/v1/reports/run-web")
    assert web.status_code == 200
    web_body = web.json()
    assert web_body["summary"]["status"] == "FAILED"
    assert web_body["summary"]["error_type"] == "WEB_EVIDENCE_ERROR"
    assert web_body["cases"]["items"][0]["status"] == "FAILED"
    assert web_body["cases"]["items"][0]["execution_result"]["status"] == "SUCCESS"
    assert {item["status"] for item in web_body["steps"]["items"]} == {"SUCCESS"}
    assert web_body["evidence"]["total"] == 3
    assert "minio" not in web.text.lower()
    assert CANARY not in web.text
    assert web_body["related_ai"]["failure_analyses"]["latest"]["ai_call_id"] == 9
    assert web_body["related_ai"]["healing_proposals"]["latest"]["ai_call_id"] == 10

    scenario = client.get("/api/v1/reports/run-scenario").json()
    assert scenario["summary"]["target"]["kind"] == "SCENARIO"
    assert scenario["cases"]["items"][0]["execution_result"]["status"] == "TIMEOUT"
    cancelled = client.get("/api/v1/reports/run-cancel").json()
    assert cancelled["cases"]["items"][0]["status"] == "CANCELLED"
    assert cancelled["cases"]["items"][0]["execution_result"]["availability"] == (
        "NOT_RECORDED"
    )
    skipped = client.get("/api/v1/reports/run-skip").json()["summary"]
    assert skipped["case_status_counts"]["skipped"] == 1
    assert skipped["case_success_rate"]["denominator"] == 0
    assert skipped["case_success_rate"]["value"] is None
    pending = client.get("/api/v1/reports/run-pending").json()["summary"]
    assert pending["case_status_counts"]["unfinished"] == 1

    after = _privacy_snapshot(report_client.session_factory)
    assert after == before
    assert CANARY in json.dumps(after, ensure_ascii=False)


def test_report_subresource_pagination_and_query_rejection(
    report_client: ReportClient,
) -> None:
    client = _as(report_client, _identity("viewer"))
    steps = client.get(
        "/api/v1/reports/run-web/steps", params={"page_size": 1}
    )
    assert steps.status_code == 200
    assert steps.json()["total"] == 2
    assert steps.json()["has_more"] is True
    assert steps.json()["next_page"] == 2
    next_steps = client.get(steps.json()["continuation_path"])
    assert next_steps.status_code == 200
    assert next_steps.json()["items"][0]["sequence_no"] == 2

    evidence = client.get(
        "/api/v1/reports/run-web/evidence", params={"page_size": 1}
    )
    assert evidence.status_code == 200
    assert evidence.json()["total"] == 3
    assert evidence.json()["has_more"] is True
    assert evidence.json()["items"][0]["download_path"].startswith(
        "/api/v1/evidence/"
    )

    invalid_queries = (
        {"project_id": 1, "page_size": 101},
        {"project_id": 1, "status": "UNKNOWN"},
        {"project_id": 1, "created_from": "2026-09-11", "created_to": "2026-09-10"},
        {"project_id": 1, "unexpected": "value"},
    )
    for params in invalid_queries:
        assert client.get("/api/v1/reports", params=params).status_code == 422
    assert client.get(
        "/api/v1/reports/run-web/steps", params={"page": 0}
    ).status_code == 422

    _as(report_client, _identity("admin", ["ADMIN"]))
    assert report_client.client.get("/api/v1/reports/run-hidden").status_code == 200


def test_report_extreme_inputs_reject_before_database_and_adjacent_bounds_work(
    report_client: ReportClient,
) -> None:
    client = _as(report_client, _identity("viewer"))
    endpoint = "/api/v1/reports"

    rejected_list_queries = (
        {"project_id": 1, "created_from": "0001-01-01"},
        {"project_id": 1, "created_from": "1000-01-01"},
        {"project_id": 1, "created_from": "9999-12-31"},
        {"project_id": 1, "created_to": "1000-01-01"},
        {"project_id": 1, "created_to": "9999-12-31"},
        {"project_id": 1, "page": str(REPORT_PAGE_MAX + 1)},
        {"project_id": 1, "page": str(2**80)},
        {"project_id": str(DATABASE_INTEGER_MAX + 1)},
        {"project_id": 1, "environment_id": str(DATABASE_INTEGER_MAX + 1)},
    )
    for params in rejected_list_queries:
        assert client.get(endpoint, params=params).status_code == 422

    for boundary_date in (REPORT_DATE_MIN.isoformat(), REPORT_DATE_MAX.isoformat()):
        response = client.get(
            endpoint,
            params={
                "project_id": 1,
                "created_from": boundary_date,
                "created_to": boundary_date,
            },
        )
        assert response.status_code == 200

    last_page = client.get(
        endpoint,
        params={"project_id": 1, "page": REPORT_PAGE_MAX, "page_size": 100},
    )
    assert last_page.status_code == 200
    assert last_page.json()["items"] == []
    assert client.get(
        endpoint, params={"project_id": DATABASE_INTEGER_MAX}
    ).status_code == 404
    assert client.get(
        endpoint,
        params={"project_id": 1, "environment_id": DATABASE_INTEGER_MAX},
    ).status_code == 200

    for resource in ("cases", "steps", "evidence"):
        resource_path = f"/api/v1/reports/run-web/{resource}"
        assert client.get(
            resource_path, params={"page": REPORT_PAGE_MAX, "page_size": 100}
        ).status_code == 200
        assert client.get(
            resource_path, params={"page": str(REPORT_PAGE_MAX + 1)}
        ).status_code == 422
        assert client.get(
            resource_path, params={"page": str(2**80)}
        ).status_code == 422

    steps_path = "/api/v1/reports/run-web/steps"
    assert client.get(
        steps_path, params={"case_run_id": DATABASE_INTEGER_MAX}
    ).status_code == 200
    assert client.get(
        steps_path, params={"case_run_id": str(DATABASE_INTEGER_MAX + 1)}
    ).status_code == 422
    evidence_path = "/api/v1/reports/run-web/evidence"
    assert client.get(
        evidence_path,
        params={
            "case_run_id": DATABASE_INTEGER_MAX,
            "step_run_id": DATABASE_INTEGER_MAX,
        },
    ).status_code == 200
    for field in ("case_run_id", "step_run_id"):
        assert client.get(
            evidence_path, params={field: str(DATABASE_INTEGER_MAX + 1)}
        ).status_code == 422


def test_report_openapi_publishes_input_and_metadata_basis_constraints() -> None:
    specification = app.openapi()
    parameters = {
        item["name"]: item["schema"]
        for item in specification["paths"]["/api/v1/reports"]["get"]["parameters"]
    }
    assert parameters["project_id"]["maximum"] == DATABASE_INTEGER_MAX
    assert parameters["environment_id"]["anyOf"][0]["maximum"] == (
        DATABASE_INTEGER_MAX
    )
    assert parameters["page"]["maximum"] == REPORT_PAGE_MAX
    assert parameters["page_size"]["maximum"] == 100
    for field in ("created_from", "created_to"):
        assert parameters[field]["x-minimum"] == REPORT_DATE_MIN.isoformat()
        assert parameters[field]["x-maximum"] == REPORT_DATE_MAX.isoformat()

    target = specification["components"]["schemas"]["ReportTargetReference"]
    metadata_basis = target["properties"]["asset_metadata_basis"]
    assert metadata_basis["const"] == "CURRENT"
    assert metadata_basis["default"] == "CURRENT"
