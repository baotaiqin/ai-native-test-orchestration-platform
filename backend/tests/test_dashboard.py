import copy
import os
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

os.environ["APP_RUNNER_DISCONNECT_SCAN_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.infrastructure.redis.client import (
    RedisStoreUnavailableError,
    get_runner_heartbeat_store,
)
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project, ProjectMember
from app.modules.reports.schemas import DATABASE_INTEGER_MAX
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirements.models import Requirement
from app.modules.runners.models import Runner
from app.modules.runs.models import CaseRun, StepRun
from app.modules.runs.models import TestRun as RunModel
from app.modules.scenarios.models import Scenario
from app.modules.test_cases.models import AiCaseGeneration, AiCaseSuggestion
from app.modules.test_cases.models import TestCase as ApiTestCase
from app.modules.web_cases.models import WebCase, WebCaseVersion
from app.modules.web_healing.models import WebHealingProposal
from app.modules.web_recording_ai.models import WebRecordingAiSuggestion
from app.modules.web_recordings.models import WebRecording

NOW = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


class FakeHeartbeatStore:
    def get_heartbeat(self, runner_id: str) -> dict | None:
        return {"status": "ONLINE"} if runner_id == "runner-online" else None


class UnavailableHeartbeatStore:
    def get_heartbeat(self, runner_id: str) -> dict | None:
        raise RedisStoreUnavailableError(runner_id)


@dataclass
class DashboardClient:
    client: TestClient
    session_factory: sessionmaker[Session]


def _run(
    run_id: str,
    project_id: int,
    status: str,
    created_at: datetime,
) -> RunModel:
    return RunModel(
        id=run_id,
        run_code=run_id.upper(),
        run_type="API_CASE",
        project_id=project_id,
        case_id=1,
        case_version_id=1,
        status=status,
        trigger_type="MANUAL",
        required_capabilities=[],
        required_tags=[],
        required_slot_type="API",
        required_slot_count=1,
        force_stopped=False,
        total=1,
        pass_count=1 if status == "SUCCESS" else 0,
        fail_count=1 if status == "FAILED" else 0,
        review_count=0,
        timeout_count=1 if status == "TIMEOUT" else 0,
        created_by="viewer",
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.fixture
def dashboard_client() -> Generator[DashboardClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        Runner.__table__,
        ApiTestCase.__table__,
        Scenario.__table__,
        WebCase.__table__,
        WebCaseVersion.__table__,
        RunModel.__table__,
        CaseRun.__table__,
        StepRun.__table__,
        EvidenceArtifact.__table__,
        Requirement.__table__,
        RequirementReview.__table__,
        AiCaseGeneration.__table__,
        AiCaseSuggestion.__table__,
        WebRecording.__table__,
        WebRecordingAiSuggestion.__table__,
        WebHealingProposal.__table__,
    )
    for table in tables:
        table.create(engine)

    with factory() as session:
        session.add_all(
            [
                Project(id=1, name="One", code="ONE", owner_id="owner"),
                Project(id=2, name="Two", code="TWO", owner_id="owner"),
                Project(id=3, name="Hidden", code="HIDDEN-DASH", owner_id="other"),
                Project(
                    id=4,
                    name="Archived",
                    code="ARCHIVED-DASH",
                    owner_id="owner",
                    status="ARCHIVED",
                    archived_at=datetime(2026, 9, 1),
                ),
                Project(id=5, name="Empty", code="EMPTY", owner_id="owner"),
                ProjectMember(project_id=1, user_id="viewer", role="VIEWER"),
                ProjectMember(project_id=2, user_id="viewer", role="VIEWER"),
                ProjectMember(project_id=4, user_id="viewer", role="VIEWER"),
                ProjectMember(project_id=5, user_id="viewer", role="VIEWER"),
            ]
        )
        session.add_all(
            [
                Runner(
                    id="runner-online",
                    name="Online",
                    hostname="online.local",
                    credential_digest="digest-online",
                    status="ACTIVE",
                    heartbeat_interval_seconds=30,
                ),
                Runner(
                    id="runner-offline",
                    name="Offline",
                    hostname="offline.local",
                    credential_digest="digest-offline",
                    status="ACTIVE",
                    heartbeat_interval_seconds=30,
                ),
                Runner(
                    id="runner-revoked",
                    name="Revoked",
                    hostname="revoked.local",
                    credential_digest="digest-revoked",
                    status="REVOKED",
                    heartbeat_interval_seconds=30,
                ),
            ]
        )
        session.add_all(
            [
                ApiTestCase(
                    id=1,
                    project_id=1,
                    code="API-1",
                    name="API One",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=1,
                    created_by="owner",
                ),
                ApiTestCase(
                    id=2,
                    project_id=1,
                    code="API-OLD",
                    name="Archived API",
                    case_type="API",
                    status="ARCHIVED",
                    source="MANUAL",
                    current_version_id=2,
                    created_by="owner",
                ),
                ApiTestCase(
                    id=3,
                    project_id=2,
                    code="API-2",
                    name="API Two",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=3,
                    created_by="owner",
                ),
                ApiTestCase(
                    id=4,
                    project_id=3,
                    code="API-HIDDEN",
                    name="Hidden API",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=4,
                    created_by="other",
                ),
                Scenario(
                    id=1,
                    project_id=1,
                    code="SCN-1",
                    name="Scenario One",
                    status="APPROVED",
                    current_version_id=1,
                    created_by="owner",
                ),
                Scenario(
                    id=2,
                    project_id=1,
                    code="SCN-OLD",
                    name="Archived Scenario",
                    status="ARCHIVED",
                    current_version_id=2,
                    created_by="owner",
                ),
                Scenario(
                    id=3,
                    project_id=2,
                    code="SCN-2",
                    name="Scenario Two",
                    status="DRAFT",
                    current_version_id=3,
                    created_by="owner",
                ),
            ]
        )
        session.add_all(
            [
                WebCase(
                    id=1,
                    project_id=1,
                    code="WEB-1",
                    name="Web One",
                    status="APPROVED",
                    current_version_id=1,
                    created_by="owner",
                ),
                WebCase(
                    id=2,
                    project_id=1,
                    code="WEB-OLD",
                    name="Archived Web",
                    status="ARCHIVED",
                    current_version_id=2,
                    created_by="owner",
                ),
                WebCase(
                    id=3,
                    project_id=2,
                    code="WEB-2",
                    name="Web Two",
                    status="DRAFT",
                    current_version_id=3,
                    created_by="owner",
                ),
                WebCase(
                    id=4,
                    project_id=3,
                    code="WEB-HIDDEN",
                    name="Hidden Web",
                    status="DRAFT",
                    current_version_id=4,
                    created_by="other",
                ),
            ]
        )
        for version_id in range(1, 5):
            session.add(
                WebCaseVersion(
                    id=version_id,
                    web_case_id=version_id,
                    version_no=1,
                    content={"title": f"web-{version_id}"},
                    status="DRAFT",
                    created_by="owner",
                )
            )
        session.add_all(
            [
                _run("before-day", 1, "SUCCESS", datetime(2026, 9, 9, 15, 59, 59)),
                _run("today-success", 1, "SUCCESS", datetime(2026, 9, 9, 16)),
                _run("today-failed", 1, "FAILED", datetime(2026, 9, 10, 15, 59, 59)),
                _run("today-timeout", 1, "TIMEOUT", datetime(2026, 9, 10, 1)),
                _run("today-cancelled", 1, "CANCELLED", datetime(2026, 9, 10, 2)),
                _run("today-running", 1, "RUNNING", datetime(2026, 9, 10, 3)),
                _run("after-day", 1, "SUCCESS", datetime(2026, 9, 10, 16)),
                _run("project-two-success", 2, "SUCCESS", datetime(2026, 9, 10, 4)),
                _run("hidden-success", 3, "SUCCESS", datetime(2026, 9, 10, 5)),
            ]
        )
        session.flush()
        session.add(
            CaseRun(
                id=100,
                run_id="today-success",
                sequence_no=1,
                case_id=1,
                case_version_id=1,
                status="SUCCESS",
                duration=10,
                retry_count=0,
            )
        )
        session.flush()
        for index in range(3):
            session.add(
                StepRun(
                    id=100 + index,
                    case_run_id=100,
                    sequence_no=index + 1,
                    node_id=f"node-{index}",
                    step_name=f"Step {index}",
                    step_type="HTTP",
                    status="SUCCESS",
                    duration=1,
                    retry_count=0,
                )
            )
            session.add(
                EvidenceArtifact(
                    id=f"dashboard-artifact-{index}",
                    project_id=1,
                    run_id="today-success",
                    case_run_id=100,
                    artifact_type="RESPONSE",
                    file_name=f"response-{index}.json",
                    mime="application/json",
                    size=1,
                    sha256=str(index) * 64,
                    minio_bucket="internal",
                    minio_key=f"private/{index}",
                )
            )
        session.add_all(
            [
                Requirement(
                    id=1,
                    project_id=1,
                    code="REQ-1",
                    title="Active",
                    status="ACTIVE",
                    current_version_id=1,
                    created_by="owner",
                ),
                Requirement(
                    id=2,
                    project_id=1,
                    code="REQ-OLD",
                    title="Archived",
                    status="ARCHIVED",
                    current_version_id=2,
                    created_by="owner",
                ),
                Requirement(
                    id=3,
                    project_id=3,
                    code="REQ-HIDDEN",
                    title="Hidden",
                    status="ACTIVE",
                    current_version_id=3,
                    created_by="other",
                ),
            ]
        )
        session.add_all(
            [
                RequirementReview(
                    id=1,
                    project_id=1,
                    requirement_id=1,
                    requirement_version_id=1,
                    ai_call_id=1,
                    status="DRAFT",
                    context_snapshot={},
                    raw_response="safe",
                    structured_result={},
                    created_by="owner",
                ),
                RequirementReview(
                    id=2,
                    project_id=1,
                    requirement_id=2,
                    requirement_version_id=2,
                    ai_call_id=2,
                    status="DRAFT",
                    context_snapshot={},
                    raw_response="safe",
                    structured_result={},
                    created_by="owner",
                ),
                RequirementReview(
                    id=3,
                    project_id=3,
                    requirement_id=3,
                    requirement_version_id=3,
                    ai_call_id=3,
                    status="DRAFT",
                    context_snapshot={},
                    raw_response="safe",
                    structured_result={},
                    created_by="other",
                ),
            ]
        )
        session.add_all(
            [
                AiCaseGeneration(
                    id=1,
                    project_id=1,
                    requirement_id=1,
                    requirement_version_id=1,
                    ai_call_id=11,
                    raw_response="safe",
                    structured_result={},
                    created_by="owner",
                ),
                AiCaseGeneration(
                    id=2,
                    project_id=1,
                    requirement_id=2,
                    requirement_version_id=2,
                    ai_call_id=12,
                    raw_response="safe",
                    structured_result={},
                    created_by="owner",
                ),
                AiCaseSuggestion(
                    id=1,
                    generation_id=1,
                    sequence_no=1,
                    status="DRAFT",
                    structured_result={"title": "review me"},
                ),
                AiCaseSuggestion(
                    id=2,
                    generation_id=2,
                    sequence_no=1,
                    status="DRAFT",
                    structured_result={"title": "not actionable"},
                ),
            ]
        )
        session.add_all(
            [
                WebRecording(
                    id="recording-ready",
                    project_id=1,
                    runner_id="runner-online",
                    start_url="https://example.test",
                    browser="CHROME",
                    status="COMPLETED",
                    dispatch_status="PUBLISHED",
                    attempt_count=1,
                    save_session=False,
                    events=[],
                    event_count=0,
                    dom_context_size=0,
                    created_by="owner",
                ),
                WebRecording(
                    id="recording-confirmed",
                    project_id=1,
                    runner_id="runner-online",
                    start_url="https://example.test",
                    browser="CHROME",
                    status="COMPLETED",
                    dispatch_status="PUBLISHED",
                    attempt_count=1,
                    save_session=False,
                    events=[],
                    event_count=0,
                    dom_context_size=0,
                    confirmed_web_case_id=1,
                    confirmed_web_case_version_id=1,
                    created_by="owner",
                ),
                WebRecordingAiSuggestion(
                    id=1,
                    project_id=1,
                    recording_id="recording-ready",
                    ai_call_id=21,
                    status="DRAFT",
                    draft_key="DRAFT",
                    source_snapshot={},
                    source_snapshot_sha256="a" * 64,
                    source_snapshot_size=0,
                    structured_result={"title": "ready"},
                    canonical_suggested_content={"title": "ready"},
                    created_by="owner",
                ),
                WebRecordingAiSuggestion(
                    id=2,
                    project_id=1,
                    recording_id="recording-ready",
                    ai_call_id=None,
                    status="DRAFT",
                    draft_key=None,
                    source_snapshot={},
                    source_snapshot_sha256="b" * 64,
                    source_snapshot_size=0,
                    structured_result={},
                    canonical_suggested_content={},
                    created_by="owner",
                ),
                WebRecordingAiSuggestion(
                    id=3,
                    project_id=1,
                    recording_id="recording-confirmed",
                    ai_call_id=23,
                    status="DRAFT",
                    draft_key="DRAFT",
                    source_snapshot={},
                    source_snapshot_sha256="c" * 64,
                    source_snapshot_size=0,
                    structured_result={"title": "confirmed"},
                    canonical_suggested_content={"title": "confirmed"},
                    created_by="owner",
                ),
            ]
        )
        session.add_all(
            [
                WebHealingProposal(
                    id=1,
                    project_id=1,
                    run_id="today-success",
                    case_run_id=100,
                    web_case_id=1,
                    web_case_version_id=1,
                    node_id="node-1",
                    ai_call_id=31,
                    status="DRAFT",
                    draft_key="DRAFT",
                    source_snapshot={},
                    source_snapshot_sha256="d" * 64,
                    source_snapshot_size=0,
                    structured_result={"locator": {"strategy": "css", "value": "#go"}},
                    old_locator={"strategy": "css", "value": "#old"},
                    proposed_locator={"strategy": "css", "value": "#go"},
                    confidence=Decimal("0.8"),
                    reason="safe reason",
                    evidence_candidate_index=0,
                    created_by="owner",
                ),
                WebHealingProposal(
                    id=2,
                    project_id=1,
                    run_id="today-success",
                    case_run_id=100,
                    web_case_id=1,
                    web_case_version_id=1,
                    node_id="node-2",
                    ai_call_id=None,
                    status="DRAFT",
                    draft_key="DRAFT",
                    source_snapshot={},
                    source_snapshot_sha256="e" * 64,
                    source_snapshot_size=0,
                    structured_result={},
                    old_locator={},
                    proposed_locator=None,
                    confidence=Decimal("0"),
                    reason="待生成",
                    evidence_candidate_index=0,
                    created_by="owner",
                ),
                WebHealingProposal(
                    id=3,
                    project_id=1,
                    run_id="today-success",
                    case_run_id=100,
                    web_case_id=2,
                    web_case_version_id=2,
                    node_id="node-3",
                    ai_call_id=33,
                    status="DRAFT",
                    draft_key="DRAFT",
                    source_snapshot={},
                    source_snapshot_sha256="f" * 64,
                    source_snapshot_size=0,
                    structured_result={"locator": {"strategy": "css", "value": "#go"}},
                    old_locator={},
                    proposed_locator={"strategy": "css", "value": "#go"},
                    confidence=Decimal("0.8"),
                    reason="archived parent",
                    evidence_candidate_index=0,
                    created_by="owner",
                ),
            ]
        )
        session.commit()

    def database_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_runner_heartbeat_store] = FakeHeartbeatStore
    try:
        with TestClient(app) as client:
            yield DashboardClient(client=client, session_factory=factory)
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


def _as(client: DashboardClient, identity: CurrentUser) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: identity
    return client.client


def _pending_snapshot(factory: sessionmaker[Session]) -> dict[str, list]:
    with factory() as session:
        return {
            "reviews": copy.deepcopy(
                list(session.execute(select(RequirementReview.id, RequirementReview.status)))
            ),
            "case_suggestions": copy.deepcopy(
                list(session.execute(select(AiCaseSuggestion.id, AiCaseSuggestion.status)))
            ),
            "recording_suggestions": copy.deepcopy(
                list(
                    session.execute(
                        select(WebRecordingAiSuggestion.id, WebRecordingAiSuggestion.status)
                    )
                )
            ),
            "healing": copy.deepcopy(
                list(session.execute(select(WebHealingProposal.id, WebHealingProposal.status)))
            ),
        }


def test_dashboard_visible_projects_counts_pending_and_shanghai_boundary(
    dashboard_client: DashboardClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.dashboard import service

    monkeypatch.setattr(service, "utc_now_aware", lambda: NOW)
    client = _as(dashboard_client, _identity("viewer"))
    before = _pending_snapshot(dashboard_client.session_factory)

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["active_project_count"] == 3
    assert body["date_range"]["timezone"] == "Asia/Shanghai"
    assert body["date_range"]["start_utc"] == "2026-09-09T16:00:00Z"
    assert body["date_range"]["end_utc_exclusive"] == "2026-09-10T16:00:00Z"
    assert body["cases"] == {
        "api_cases": 2,
        "web_cases": 2,
        "case_total": 4,
        "scenarios": 2,
        "definition": body["cases"]["definition"],
    }
    assert body["today_runs"]["total"] == 6
    assert body["today_runs"]["success"] == 2
    assert body["today_runs"]["failed"] == 1
    assert body["today_runs"]["timeout"] == 1
    assert body["today_runs"]["cancelled"] == 1
    assert body["today_runs"]["unfinished"] == 1
    assert body["today_runs"]["success_rate"]["numerator"] == 2
    assert body["today_runs"]["success_rate"]["denominator"] == 4
    assert body["today_runs"]["success_rate"]["value"] == 0.5
    assert body["pending_reviews"]["requirement_reviews"] == 1
    assert body["pending_reviews"]["ai_case_suggestions"] == 1
    assert body["pending_reviews"]["web_recording_ai_suggestions"] == 1
    assert body["pending_reviews"]["web_healing_proposals"] == 1
    assert body["pending_reviews"]["total"] == 4
    assert body["runners"] == {
        "visibility": "ADMIN_ONLY",
        "available": None,
        "status": "HIDDEN",
        "registered_total": None,
        "active_total": None,
        "online": None,
        "offline": None,
        "unknown": None,
    }
    assert "hidden-success" not in {item["run_id"] for item in body["recent_runs"]}
    assert "before-day" in {item["run_id"] for item in body["recent_runs"]}
    assert _pending_snapshot(dashboard_client.session_factory) == before


def test_dashboard_project_scope_empty_denominator_and_permission_rejection(
    dashboard_client: DashboardClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.dashboard import service

    monkeypatch.setattr(service, "utc_now_aware", lambda: NOW)
    client = _as(dashboard_client, _identity("viewer"))

    project = client.get("/api/v1/dashboard", params={"project_id": 1})
    assert project.status_code == 200
    assert project.json()["active_project_count"] == 1
    assert project.json()["today_runs"]["total"] == 5
    assert project.json()["today_runs"]["success_rate"]["value"] == pytest.approx(1 / 3)

    empty = client.get("/api/v1/dashboard", params={"project_id": 5})
    assert empty.status_code == 200
    assert empty.json()["today_runs"]["total"] == 0
    assert empty.json()["today_runs"]["success_rate"]["denominator"] == 0
    assert empty.json()["today_runs"]["success_rate"]["value"] is None
    assert empty.json()["recent_runs"] == []

    assert client.get("/api/v1/dashboard", params={"project_id": 3}).status_code == 404
    assert client.get("/api/v1/dashboard", params={"project_id": 4}).status_code == 404
    assert client.get("/api/v1/dashboard", params={"unexpected": 1}).status_code == 422
    assert client.get("/api/v1/dashboard", params={"project_id": 0}).status_code == 422
    assert client.get(
        "/api/v1/dashboard", params={"project_id": DATABASE_INTEGER_MAX}
    ).status_code == 404
    for value in (DATABASE_INTEGER_MAX + 1, 2**80):
        assert client.get(
            "/api/v1/dashboard", params={"project_id": str(value)}
        ).status_code == 422


def test_admin_runner_overview_known_and_redis_unavailable_unknown(
    dashboard_client: DashboardClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.dashboard import service

    monkeypatch.setattr(service, "utc_now_aware", lambda: NOW)
    client = _as(dashboard_client, _identity("admin", ["ADMIN"]))

    known = client.get("/api/v1/dashboard")
    assert known.status_code == 200
    assert known.json()["active_project_count"] == 4
    assert known.json()["today_runs"]["total"] == 7
    assert known.json()["runners"] == {
        "visibility": "VISIBLE",
        "available": True,
        "status": "KNOWN",
        "registered_total": 3,
        "active_total": 2,
        "online": 1,
        "offline": 1,
        "unknown": 0,
    }

    app.dependency_overrides[get_runner_heartbeat_store] = UnavailableHeartbeatStore
    unavailable = client.get("/api/v1/dashboard")
    assert unavailable.status_code == 200
    assert unavailable.json()["runners"] == {
        "visibility": "VISIBLE",
        "available": False,
        "status": "UNKNOWN",
        "registered_total": 3,
        "active_total": 2,
        "online": None,
        "offline": None,
        "unknown": 2,
    }
