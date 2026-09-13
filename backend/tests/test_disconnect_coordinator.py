"""工作包 A：Runner 断线自动收口专项测试。

所有时间与 sleep 均可注入或受控，测试不真实等待 heartbeat TTL。
"""

import threading
import time
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.exceptions import AuthorizationError
from app.infrastructure.redis.client import RedisStoreUnavailableError
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project
from app.modules.runners.models import Runner, RunnerCapability, RunnerSlot, RunnerTag
from app.modules.runners.schemas import OnlineStatus, RunnerStatus
from app.modules.runs import disconnect_coordinator as coordinator_module
from app.modules.runs import service as runs_service
from app.modules.runs.disconnect_coordinator import (
    RunnerDisconnectCoordinator,
    build_disconnect_coordinator,
)
from app.modules.runs.enums import RunNodeStatus, RunStatus, RunType
from app.modules.runs.models import CaseRun, StepRun, TestRun
from app.modules.runs.service import reconcile_disconnected_runner_runs
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.web_design.models import WebExploration

TTL_SECONDS = 90.0
NOW = datetime(2026, 8, 29, 12, 0, 0)


class FakeHeartbeatStore:
    def __init__(self) -> None:
        self.online: set[str] = set()
        self.unavailable = False

    def get_heartbeat(self, runner_id: str) -> dict[str, Any] | None:
        if self.unavailable:
            raise RedisStoreUnavailableError()
        return {"runner_id": runner_id, "status": "ONLINE"} if runner_id in self.online else None


class FakeEventStream:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, dict[str, Any]]] = []

    def append_event(self, project_id: int, run_id: str, event: dict[str, Any]) -> str:
        self.events.append((project_id, run_id, event))
        return f"{len(self.events)}-0"


def _make_run(
    session: Session,
    *,
    run_id: str,
    runner_id: str,
    project_id: int,
    case_id: int,
    case_version_id: int,
    status: RunStatus,
    node_status: RunNodeStatus = RunNodeStatus.RUNNING,
    total_timeout_ms: int | None = 900_000,
) -> None:
    run = TestRun(
        id=run_id,
        run_code=f"CODE-{run_id[-8:]}",
        run_type=RunType.API_CASE.value,
        project_id=project_id,
        runner_id=runner_id,
        case_id=case_id,
        case_version_id=case_version_id,
        status=status.value,
        trigger_type="MANUAL",
        required_capabilities=["API"],
        required_tags=[],
        required_slot_type="API",
        required_slot_count=1,
        total_timeout_ms=total_timeout_ms,
        total=1,
        created_by="test",
    )
    session.add(run)
    session.flush()
    case_run = CaseRun(
        run_id=run_id,
        sequence_no=1,
        case_id=case_id,
        case_version_id=case_version_id,
        status=node_status.value,
    )
    session.add(case_run)
    session.flush()
    session.add(
        StepRun(
            case_run_id=case_run.id,
            sequence_no=1,
            node_id=f"node-{run_id}",
            step_name="Request Step",
            step_type="HTTP_REQUEST",
            status=node_status.value,
        )
    )


@pytest.fixture
def disconnect_context() -> Generator[
    tuple[sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]],
    None,
    None,
]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        Environment.__table__,
        Runner.__table__,
        RunnerTag.__table__,
        RunnerCapability.__table__,
        RunnerSlot.__table__,
        TestCase.__table__,
        TestCaseVersion.__table__,
        Scenario.__table__,
        ScenarioVersion.__table__,
        TestRun.__table__,
        CaseRun.__table__,
        StepRun.__table__,
        EvidenceArtifact.__table__,
    )
    for table in tables:
        table.create(engine)
    with session_factory() as session:
        project = Project(
            name="Disconnect Project",
            code="DISCONNECT_PROJECT",
            owner_id="dev-admin",
            status="ACTIVE",
        )
        session.add(project)
        session.flush()
        environment = Environment(
            project_id=project.id, name="Test", code="TEST", enabled=True, is_default=True
        )
        runner = Runner(
            id="runner-a",
            name="Runner A",
            hostname="RUNNER-A",
            credential_digest="digest-runner-a",
            status="ACTIVE",
            heartbeat_interval_seconds=30,
        )
        runner_b = Runner(
            id="runner-b",
            name="Runner B",
            hostname="RUNNER-B",
            credential_digest="digest-runner-b",
            status="ACTIVE",
            heartbeat_interval_seconds=30,
        )
        case = TestCase(
            project_id=project.id,
            code="TC-DC-001",
            name="Disconnect Case",
            case_type="API",
            status="ACTIVE",
            source="MANUAL",
            created_by="dev-admin",
        )
        session.add_all([environment, runner, runner_b, case])
        session.flush()
        case_version = TestCaseVersion(
            case_id=case.id,
            version_no=1,
            content={
                "title": "Disconnect Case",
                "case_type": "API",
                "request": {"method": "GET", "url": "https://example.test/health"},
            },
            created_by="dev-admin",
        )
        session.add(case_version)
        session.flush()
        case.current_version_id = case_version.id
        session.commit()
        ids = {
            "project_id": project.id,
            "case_id": case.id,
            "case_version_id": case_version.id,
        }
    store = FakeHeartbeatStore()
    events = FakeEventStream()
    try:
        yield session_factory, store, events, ids
    finally:
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _coordinator(
    session_factory: sessionmaker[Session],
    store: FakeHeartbeatStore,
    events: FakeEventStream,
) -> RunnerDisconnectCoordinator:
    return RunnerDisconnectCoordinator(
        enabled=True,
        session_factory=session_factory,
        heartbeat_store=store,
        event_stream=events,
        interval_seconds=0.05,
        heartbeat_ttl_seconds=TTL_SECONDS,
        now_provider=lambda: NOW,
    )


def _expire_runner(session: Session, runner_id: str, offset_seconds: float) -> None:
    runner = session.get(Runner, runner_id)
    runner.last_heartbeat_at = NOW - timedelta(seconds=offset_seconds)


def test_scan_reconciles_when_redis_missing_and_mysql_expired(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _expire_runner(session, "runner-a", TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-assigned",
            runner_id="runner-a",
            status=RunStatus.ASSIGNED,
            node_status=RunNodeStatus.ASSIGNED,
            **ids,
        )
        _make_run(
            session,
            run_id="run-running",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.RUNNING,
            **ids,
        )
        session.commit()

    round_result = _coordinator(session_factory, store, events).scan_once()
    assert round_result.candidate_runner_ids == ["runner-a"]
    assert round_result.errors == []
    assert round_result.reconciled_run_count == 2

    with session_factory() as session:
        for run_id in ("run-assigned", "run-running"):
            run = session.get(TestRun, run_id)
            assert run is not None
            assert run.status == RunStatus.FAILED.value
            assert run.error_type == "RUNNER_DISCONNECTED"
            assert run.error_message == "Runner 心跳超时，执行已异常终止"
            assert run.ended_at is not None
            assert run.fail_count == 1
            case_run = session.scalar(select(CaseRun).where(CaseRun.run_id == run_id))
            assert case_run is not None and case_run.status == RunNodeStatus.FAILED.value
            step_run = session.scalar(
                select(StepRun).where(StepRun.case_run_id == case_run.id)
            )
            assert step_run is not None and step_run.status == RunNodeStatus.FAILED.value

    failed_events = [event for _, _, event in events.events if event["to_status"] == "FAILED"]
    assert len(failed_events) == 2

    second = _coordinator(session_factory, store, events).scan_once()
    assert second.candidate_runner_ids == []
    assert second.reconciled_run_count == 0
    assert len([event for _, _, event in events.events if event["to_status"] == "FAILED"]) == 2


@pytest.mark.parametrize(
    ("redis_online", "redis_unavailable", "heartbeat_offset_seconds"),
    [
        (True, False, TTL_SECONDS + 5),
        (False, True, TTL_SECONDS + 5),
        (False, False, TTL_SECONDS - 10),
        (False, False, None),
    ],
    ids=[
        "redis_online",
        "redis_unavailable",
        "mysql_heartbeat_still_fresh",
        "mysql_heartbeat_null",
    ],
)
def test_scan_skips_when_protection_gates_block(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    redis_online: bool,
    redis_unavailable: bool,
    heartbeat_offset_seconds: float | None,
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        if heartbeat_offset_seconds is not None:
            _expire_runner(session, "runner-a", heartbeat_offset_seconds)
        _make_run(
            session,
            run_id="run-pending",
            runner_id="runner-a",
            status=RunStatus.ASSIGNED,
            node_status=RunNodeStatus.ASSIGNED,
            **ids,
        )
        session.commit()

    store.unavailable = redis_unavailable
    if redis_online:
        store.online.add("runner-a")

    round_result = _coordinator(session_factory, store, events).scan_once()
    assert round_result.reconciled_run_count == 0
    assert round_result.errors == []
    with session_factory() as session:
        run = session.get(TestRun, "run-pending")
        assert run is not None and run.status == RunStatus.ASSIGNED.value


def test_scan_excludes_terminal_runs_and_revoked_runners(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _expire_runner(session, "runner-a", TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-success",
            runner_id="runner-a",
            status=RunStatus.SUCCESS,
            node_status=RunNodeStatus.SUCCESS,
            **ids,
        )
        runner_b = session.get(Runner, "runner-b")
        runner_b.status = RunnerStatus.REVOKED.value
        runner_b.last_heartbeat_at = NOW - timedelta(seconds=TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-revoked",
            runner_id="runner-b",
            status=RunStatus.ASSIGNED,
            node_status=RunNodeStatus.ASSIGNED,
            **ids,
        )
        session.commit()

    round_result = _coordinator(session_factory, store, events).scan_once()
    assert round_result.candidate_runner_ids == []
    assert round_result.reconciled_run_count == 0
    with session_factory() as session:
        assert session.get(TestRun, "run-success").status == RunStatus.SUCCESS.value
        assert session.get(TestRun, "run-revoked").status == RunStatus.ASSIGNED.value


def test_scan_isolates_per_runner_failures(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        for runner_id in ("runner-a", "runner-b"):
            _expire_runner(session, runner_id, TTL_SECONDS + 5)
            _make_run(
                session,
                run_id=f"run-{runner_id}",
                runner_id=runner_id,
                status=RunStatus.RUNNING,
                node_status=RunNodeStatus.RUNNING,
                **ids,
            )
        session.commit()

    real_core = coordinator_module.reconcile_disconnected_runner_runs_core

    def flaky_core(
        session: Session, runner_id: str, store: Any, event_stream: Any, **kwargs: Any
    ) -> Any:
        if runner_id == "runner-a":
            raise RuntimeError("runner-a 单点故障")
        return real_core(session, runner_id, store, event_stream, **kwargs)

    monkeypatch.setattr(coordinator_module, "reconcile_disconnected_runner_runs_core", flaky_core)
    round_result = _coordinator(session_factory, store, events).scan_once()
    assert ("runner-a", "RuntimeError") in round_result.errors
    assert round_result.reconciled_run_count == 1
    with session_factory() as session:
        assert session.get(TestRun, "run-runner-a").status == RunStatus.RUNNING.value
        assert session.get(TestRun, "run-runner-b").status == RunStatus.FAILED.value


def test_scan_rechecks_redis_online_state_after_lock(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _expire_runner(session, "runner-a", TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-recheck",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.RUNNING,
            **ids,
        )
        session.commit()

    calls = {"count": 0}

    def recovering_online_state(runner: Runner, store: Any) -> tuple[OnlineStatus, bool, bool]:
        calls["count"] += 1
        if calls["count"] == 1:
            return OnlineStatus.OFFLINE, False, True
        return OnlineStatus.ONLINE, True, True

    monkeypatch.setattr(runs_service, "get_runner_online_state", recovering_online_state)
    round_result = _coordinator(session_factory, store, events).scan_once()
    assert calls["count"] == 2
    assert round_result.reconciled_run_count == 0
    with session_factory() as session:
        assert session.get(TestRun, "run-recheck").status == RunStatus.RUNNING.value


def test_scan_rechecks_heartbeat_expiry_after_lock(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _expire_runner(session, "runner-a", TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-freshened",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.RUNNING,
            **ids,
        )
        session.commit()

    calls = {"count": 0}

    def freshening_expiry(runner: Runner, now: datetime, ttl_seconds: float) -> bool:
        calls["count"] += 1
        return calls["count"] == 1

    monkeypatch.setattr(runs_service, "_heartbeat_expired", freshening_expiry)
    round_result = _coordinator(session_factory, store, events).scan_once()
    assert calls["count"] == 2
    assert round_result.reconciled_run_count == 0
    with session_factory() as session:
        assert session.get(TestRun, "run-freshened").status == RunStatus.RUNNING.value


@pytest.mark.parametrize("heartbeat_mode", ["online", "offline", "unknown"])
def test_scan_reconciles_overdue_runs_independent_of_heartbeat_and_preserves_evidence(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    heartbeat_mode: str,
) -> None:
    session_factory, store, events, ids = disconnect_context
    expired_at = NOW - timedelta(minutes=16, seconds=1)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run-overdue-running",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.RUNNING,
            **ids,
        )
        _make_run(
            session,
            run_id="run-overdue-cancelling",
            runner_id="runner-a",
            status=RunStatus.CANCELLING,
            node_status=RunNodeStatus.CANCELLING,
            **ids,
        )
        _make_run(
            session,
            run_id="run-overdue-force-stop",
            runner_id="runner-a",
            status=RunStatus.CANCELLING,
            node_status=RunNodeStatus.CANCELLING,
            **ids,
        )
        for run_id in (
            "run-overdue-running",
            "run-overdue-cancelling",
            "run-overdue-force-stop",
        ):
            run = session.get(TestRun, run_id)
            assert run is not None
            run.started_at = expired_at
        force_run = session.get(TestRun, "run-overdue-force-stop")
        assert force_run is not None
        force_run.force_stop_requested_at = NOW - timedelta(minutes=1)

        running_case = session.scalar(
            select(CaseRun).where(CaseRun.run_id == "run-overdue-running")
        )
        assert running_case is not None
        running_step = session.scalar(
            select(StepRun).where(StepRun.case_run_id == running_case.id)
        )
        assert running_step is not None
        running_step.status = RunNodeStatus.SUCCESS.value
        running_step.ended_at = expired_at + timedelta(seconds=1)
        running_step.duration = 1000
        session.add(
            EvidenceArtifact(
                id="artifact-overdue-running",
                project_id=ids["project_id"],
                run_id="run-overdue-running",
                case_run_id=running_case.id,
                artifact_type="RESPONSE",
                file_name="response.json",
                mime="application/json",
                size=2,
                sha256="0" * 64,
                minio_bucket="test-bucket",
                minio_key="safe/response.json",
                artifact_metadata={"status_code": 200},
            )
        )
        session.commit()

    if heartbeat_mode == "online":
        store.online.add("runner-a")
    elif heartbeat_mode == "unknown":
        store.unavailable = True

    round_result = _coordinator(session_factory, store, events).scan_once()
    assert round_result.overdue_run_count == 3
    assert set(round_result.overdue_run_ids) == {
        "run-overdue-running",
        "run-overdue-cancelling",
        "run-overdue-force-stop",
    }

    with session_factory() as session:
        running = session.get(TestRun, "run-overdue-running")
        running_case = session.scalar(
            select(CaseRun).where(CaseRun.run_id == "run-overdue-running")
        )
        running_step = session.scalar(
            select(StepRun).where(StepRun.case_run_id == running_case.id)
        ) if running_case is not None else None
        assert running is not None and running.status == RunStatus.TIMEOUT.value
        assert running.error_type == "TOTAL_TIMEOUT"
        assert "上报宽限" in (running.error_message or "")
        assert running.ended_at is not None
        assert running.timeout_count == 1
        assert running_case is not None and running_case.status == RunNodeStatus.TIMEOUT.value
        assert running_step is not None and running_step.status == RunNodeStatus.SUCCESS.value
        assert session.get(EvidenceArtifact, "artifact-overdue-running") is not None

        cancelling = session.get(TestRun, "run-overdue-cancelling")
        cancelling_case = session.scalar(
            select(CaseRun).where(CaseRun.run_id == "run-overdue-cancelling")
        )
        assert cancelling is not None and cancelling.status == RunStatus.CANCELLED.value
        assert cancelling.error_type == "CANCEL_REQUESTED"
        assert "Cleanup" in (cancelling.error_message or "")
        assert cancelling.force_stopped is False
        assert cancelling_case is not None
        assert cancelling_case.status == RunNodeStatus.CANCELLED.value

        force_stopped = session.get(TestRun, "run-overdue-force-stop")
        assert force_stopped is not None
        assert force_stopped.status == RunStatus.CANCELLED.value
        assert force_stopped.error_type == "FORCE_STOP_REQUESTED"
        assert "远端执行" in (force_stopped.error_message or "")
        assert "Cleanup" in (force_stopped.error_message or "")
        assert force_stopped.force_stopped is False

    terminal_events = {
        run_id: event["to_status"]
        for _, run_id, event in events.events
        if run_id.startswith("run-overdue")
    }
    assert terminal_events == {
        "run-overdue-running": "TIMEOUT",
        "run-overdue-cancelling": "CANCELLED",
        "run-overdue-force-stop": "CANCELLED",
    }


def test_scan_skips_overdue_boundary_without_reliable_expired_deadline(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _make_run(
            session,
            run_id="run-deadline-boundary",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            **ids,
        )
        _make_run(
            session,
            run_id="run-no-start",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            **ids,
        )
        _make_run(
            session,
            run_id="run-legacy-no-budget",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            total_timeout_ms=None,
            **ids,
        )
        boundary = session.get(TestRun, "run-deadline-boundary")
        assert boundary is not None
        boundary.started_at = NOW - timedelta(minutes=16)
        legacy = session.get(TestRun, "run-legacy-no-budget")
        assert legacy is not None
        legacy.started_at = NOW - timedelta(days=30)
        session.commit()

    result = _coordinator(session_factory, store, events).scan_once()
    assert result.overdue_candidate_run_ids == []
    assert result.overdue_run_count == 0
    with session_factory() as session:
        assert session.get(TestRun, "run-deadline-boundary").status == RunStatus.RUNNING.value
        assert session.get(TestRun, "run-no-start").status == RunStatus.RUNNING.value
        assert session.get(TestRun, "run-legacy-no-budget").status == RunStatus.RUNNING.value


def test_overdue_scan_rechecks_after_normal_completion_wins(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _make_run(
            session,
            run_id="run-completion-wins",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            **ids,
        )
        run = session.get(TestRun, "run-completion-wins")
        assert run is not None
        run.started_at = NOW - timedelta(minutes=16, seconds=1)
        session.commit()

    real_core = coordinator_module.reconcile_overdue_run_core
    called = False

    def completing_core(session: Session, run_id: str, event_stream: Any, **kwargs: Any) -> Any:
        nonlocal called
        if not called:
            called = True
            with session_factory() as completion_session:
                run = completion_session.get(TestRun, run_id)
                case_run = completion_session.scalar(
                    select(CaseRun).where(CaseRun.run_id == run_id)
                )
                step_run = completion_session.scalar(
                    select(StepRun).where(StepRun.case_run_id == case_run.id)
                ) if case_run is not None else None
                assert run is not None and case_run is not None and step_run is not None
                step_run.status = RunNodeStatus.SUCCESS.value
                step_run.ended_at = NOW
                case_run.status = RunNodeStatus.SUCCESS.value
                case_run.ended_at = NOW
                run.status = RunStatus.SUCCESS.value
                run.ended_at = NOW
                completion_session.commit()
        return real_core(session, run_id, event_stream, **kwargs)

    monkeypatch.setattr(coordinator_module, "reconcile_overdue_run_core", completing_core)
    result = _coordinator(session_factory, store, events).scan_once()
    assert called is True
    assert result.overdue_run_count == 0
    with session_factory() as session:
        run = session.get(TestRun, "run-completion-wins")
        assert run is not None and run.status == RunStatus.SUCCESS.value
        assert run.error_type is None
    assert not any(run_id == "run-completion-wins" for _, run_id, _ in events.events)


def test_overdue_scan_preserves_already_terminal_children(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _make_run(
            session,
            run_id="run-children-finished",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.SUCCESS,
            **ids,
        )
        run = session.get(TestRun, "run-children-finished")
        assert run is not None
        run.started_at = NOW - timedelta(minutes=16, seconds=1)
        session.commit()

    result = _coordinator(session_factory, store, events).scan_once()
    assert result.overdue_run_count == 1
    with session_factory() as session:
        run = session.get(TestRun, "run-children-finished")
        case_run = session.scalar(
            select(CaseRun).where(CaseRun.run_id == "run-children-finished")
        )
        step_run = session.scalar(
            select(StepRun).where(StepRun.case_run_id == case_run.id)
        ) if case_run is not None else None
        assert run is not None and run.status == RunStatus.TIMEOUT.value
        assert run.pass_count == 1
        assert run.timeout_count == 0
        assert case_run is not None and case_run.status == RunNodeStatus.SUCCESS.value
        assert step_run is not None and step_run.status == RunNodeStatus.SUCCESS.value


def test_run_forever_survives_failed_round(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    with session_factory() as session:
        _expire_runner(session, "runner-a", TTL_SECONDS + 5)
        _make_run(
            session,
            run_id="run-loop",
            runner_id="runner-a",
            status=RunStatus.RUNNING,
            node_status=RunNodeStatus.RUNNING,
            **ids,
        )
        session.commit()

    coordinator = _coordinator(session_factory, store, events)
    calls = {"count": 0}
    original_scan = coordinator.scan_once

    def flaky_scan() -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("单轮故障")
        return original_scan()

    coordinator.scan_once = flaky_scan  # type: ignore[method-assign]
    stop_event = threading.Event()
    thread = threading.Thread(target=coordinator.run_forever, args=(stop_event,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while calls["count"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert calls["count"] >= 2
    with session_factory() as session:
        assert session.get(TestRun, "run-loop").status == RunStatus.FAILED.value


def test_scan_records_candidate_query_failure(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    _, store, events, _ = disconnect_context

    def broken_factory() -> Session:
        raise RuntimeError("数据库不可用")

    coordinator = RunnerDisconnectCoordinator(
        enabled=True,
        session_factory=broken_factory,
        heartbeat_store=store,
        event_stream=events,
        interval_seconds=0.05,
        heartbeat_ttl_seconds=TTL_SECONDS,
        now_provider=lambda: NOW,
    )
    round_result = coordinator.scan_once()
    assert round_result.candidate_runner_ids == []
    assert ("", "RuntimeError") in round_result.errors


def test_manual_reconcile_keeps_redis_only_contract(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, ids = disconnect_context
    admin = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])
    with session_factory() as session:
        # MySQL 心跳仍新鲜：手动接口保持原契约（只看 Redis 明确 OFFLINE）
        _expire_runner(session, "runner-a", 10)
        _make_run(
            session,
            run_id="run-manual",
            runner_id="runner-a",
            status=RunStatus.ASSIGNED,
            node_status=RunNodeStatus.ASSIGNED,
            **ids,
        )
        session.commit()

    with session_factory() as session:
        result = reconcile_disconnected_runner_runs(
            session, admin, "runner-a", store, events
        )
    assert result.reconciled_run_count == 1
    assert result.reconciled_run_ids == ["run-manual"]

    viewer = CurrentUser(id="viewer", username="viewer", display_name="Viewer", roles=["VIEWER"])
    with session_factory() as session, pytest.raises(AuthorizationError):
        reconcile_disconnected_runner_runs(session, viewer, "runner-a", store, events)


def test_disabled_coordinator_never_starts(
    disconnect_context: tuple[
        sessionmaker[Session], FakeHeartbeatStore, FakeEventStream, dict[str, Any]
    ],
) -> None:
    session_factory, store, events, _ = disconnect_context
    coordinator = RunnerDisconnectCoordinator(
        enabled=False,
        session_factory=session_factory,
        heartbeat_store=store,
        event_stream=events,
        interval_seconds=0.05,
        heartbeat_ttl_seconds=TTL_SECONDS,
        now_provider=lambda: NOW,
    )
    coordinator.start()
    coordinator.stop()
    assert coordinator._thread is None


def test_stale_web_exploration_is_terminalized_without_overwriting_fresh_work() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    WebExploration.__table__.create(engine)
    try:
        with session_factory() as session:
            for exploration_id, updated_at in (
                ("stale-exploration", NOW - timedelta(minutes=16)),
                ("fresh-exploration", NOW - timedelta(minutes=2)),
            ):
                session.add(
                    WebExploration(
                        id=exploration_id,
                        project_id=1,
                        plan_item_id=1,
                        runner_id="runner-a",
                        use_login_credentials=False,
                        headless=False,
                        decision_prompt_id=1,
                        start_url="https://example.test",
                        allowed_origins=["https://example.test"],
                        max_steps=20,
                        current_step=0,
                        status="RUNNING",
                        dispatch_status="PUBLISHED",
                        attempt_count=1,
                        observations=[],
                        action_trace=[],
                        decision_ai_call_ids=[],
                        created_by="dev-admin",
                        created_at=updated_at,
                        updated_at=updated_at,
                    )
                )
            session.commit()
        coordinator = RunnerDisconnectCoordinator(
            enabled=True,
            session_factory=session_factory,
            heartbeat_store=FakeHeartbeatStore(),
            event_stream=FakeEventStream(),
            interval_seconds=30,
            heartbeat_ttl_seconds=TTL_SECONDS,
            web_exploration_stale_timeout_seconds=900,
            now_provider=lambda: NOW,
        )

        assert coordinator._candidate_stale_exploration_ids() == ["stale-exploration"]
        assert coordinator._reconcile_stale_exploration("stale-exploration") is True

        with session_factory() as session:
            stale = session.get(WebExploration, "stale-exploration")
            fresh = session.get(WebExploration, "fresh-exploration")
            assert stale is not None and stale.status == "FAILED"
            assert stale.error_type == "WEB_EXPLORATION_STALE"
            assert stale.completed_at == NOW
            assert fresh is not None and fresh.status == "RUNNING"
    finally:
        WebExploration.__table__.drop(engine)
        engine.dispose()


class RecordingCoordinator(RunnerDisconnectCoordinator):
    def __init__(self) -> None:
        super().__init__(enabled=False)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout_seconds: float = 15.0) -> None:
        self.stopped = True


def test_lifespan_starts_and_stops_coordinator() -> None:
    recording = RecordingCoordinator()
    app.dependency_overrides[build_disconnect_coordinator] = lambda: recording
    try:
        with TestClient(app):
            assert recording.started is True
            assert recording.stopped is False
        assert recording.stopped is True
    finally:
        app.dependency_overrides.pop(build_disconnect_coordinator, None)
