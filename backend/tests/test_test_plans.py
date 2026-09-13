from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.db.base import Base
from app.infrastructure.rabbitmq.client import TaskPublishResult
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.projects.models import Project, ProjectMember
from app.modules.runners.models import Runner, RunnerCapability, RunnerSlot
from app.modules.runners.security import digest_secret
from app.modules.runs.models import TestRun as RunModel
from app.modules.scenarios.models import Scenario as ScenarioModel
from app.modules.scenarios.models import ScenarioVersion as ScenarioVersionModel
from app.modules.test_cases.models import TestCase as CaseModel
from app.modules.test_cases.models import TestCaseVersion as CaseVersionModel
from app.modules.test_plans.schemas import TestPlanCreate as PlanCreate
from app.modules.test_plans.schemas import TestPlanUpdate as PlanUpdate
from app.modules.test_plans.service import (
    cancel_test_plan_run,
    create_test_plan,
    get_test_plan_run,
    retry_test_plan_run_dispatch,
    set_test_plan_status,
    start_test_plan_run,
    update_test_plan,
    validate_test_plan,
)
from app.modules.web_cases.models import WebCase as WebCaseModel
from app.modules.web_cases.models import WebCaseVersion as WebCaseVersionModel
from tests.auth_helpers import install_test_auth, uninstall_test_auth


class FakeHeartbeatStore:
    def __init__(self, runner_id: str) -> None:
        self.runner_id = runner_id

    def get_heartbeat(self, runner_id: str) -> dict[str, str] | None:
        if runner_id == self.runner_id:
            return {"runner_id": runner_id, "status": "ONLINE"}
        return None


class FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.published = True

    def publish(self, *, routing_key: str, payload: dict[str, Any]) -> TaskPublishResult:
        self.calls.append((routing_key, payload))
        return TaskPublishResult(
            published=self.published,
            code=None if self.published else "BROKER_UNAVAILABLE",
        )


class FakeEventStream:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, dict[str, Any]]] = []

    def append_event(self, project_id: int, run_id: str, event: dict[str, Any]) -> str:
        self.events.append((project_id, run_id, event))
        return f"{len(self.events)}-0"


@pytest.fixture
def plan_context() -> Generator[tuple[sessionmaker[Session], dict[str, Any]], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    install_test_auth(engine)
    Base.metadata.create_all(engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        project = Project(
            name="Plan Project",
            code="PLAN_PROJECT",
            owner_id="dev-admin",
            status="ACTIVE",
        )
        project.members.append(ProjectMember(user_id="dev-admin", role="PROJECT_OWNER"))
        session.add(project)
        session.flush()
        environment = Environment(
            project_id=project.id,
            name="Test",
            code="TEST",
            enabled=True,
            is_default=True,
        )
        runner = Runner(
            id="runner-plan-test",
            name="Plan Runner",
            hostname="PLAN-RUNNER",
            credential_digest=digest_secret("runner-plan-credential"),
            status="ACTIVE",
            heartbeat_interval_seconds=30,
            chrome_version="130",
        )
        runner.capabilities.append(RunnerCapability(capability="API", status="READY"))
        runner.capabilities.append(RunnerCapability(capability="WEB", status="READY"))
        runner.slots.append(RunnerSlot(slot_type="API", total=4, available=4))
        runner.slots.append(RunnerSlot(slot_type="WEB", total=2, available=2))
        case = CaseModel(
            project_id=project.id,
            code="TC-PLAN-001",
            name="Plan API Case",
            case_type="API",
            status="ACTIVE",
            source="MANUAL",
            created_by="dev-admin",
        )
        session.add_all([environment, runner, case])
        session.flush()
        version = CaseVersionModel(
            case_id=case.id,
            version_no=1,
            content={
                "title": "Plan API Case",
                "case_type": "API",
                "priority": "P1",
                "steps": [{"order": 1, "action": "GET health", "expected": "200"}],
                "expected_result": "healthy",
                "request": {
                    "method": "GET",
                    "url": "https://example.test/{{tenant_id}}/health",
                },
            },
            created_by="dev-admin",
        )
        session.add(version)
        session.flush()
        case.current_version_id = version.id
        scenario = ScenarioModel(
            project_id=project.id,
            code="SC-PLAN-001",
            name="Plan Scenario",
            status="APPROVED",
            created_by="dev-admin",
        )
        web_case = WebCaseModel(
            project_id=project.id,
            code="WEB-PLAN-001",
            name="Plan Web Case",
            status="APPROVED",
            created_by="dev-admin",
        )
        session.add_all([scenario, web_case])
        session.flush()
        scenario_version = ScenarioVersionModel(
            scenario_id=scenario.id,
            version_no=1,
            dsl={
                "version": "1.0",
                "settings": {"initial_variables": ["tenant_id"]},
                "nodes": [
                    {"id": "start", "type": "START", "name": "Start"},
                    {"id": "wait", "type": "WAIT", "name": "Wait", "config": {"duration_ms": 1}},
                    {"id": "end", "type": "END", "name": "End"},
                ],
            },
            created_by="dev-admin",
        )
        web_version = WebCaseVersionModel(
            web_case_id=web_case.id,
            version_no=1,
            content={
                "start_url": "https://example.test/{{tenant_id}}",
                "actions": [
                    {"type": "GOTO", "url": "https://example.test/{{tenant_id}}"}
                ],
                "assertions": [],
            },
            status="APPROVED",
            approved_by="dev-admin",
            approved_at=environment.created_at,
            created_by="dev-admin",
        )
        session.add_all([scenario_version, web_version])
        session.flush()
        scenario.current_version_id = scenario_version.id
        web_case.current_version_id = web_version.id
        session.commit()
        ids = {
            "project_id": project.id,
            "environment_id": environment.id,
            "runner_id": runner.id,
            "case_id": case.id,
            "case_version_id": version.id,
            "scenario_id": scenario.id,
            "scenario_version_id": scenario_version.id,
            "web_case_id": web_case.id,
            "web_case_version_id": web_version.id,
        }
    try:
        yield factory, ids
    finally:
        uninstall_test_auth(engine)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user() -> CurrentUser:
    return CurrentUser(
        id="dev-admin", username="admin", display_name="开发管理员", roles=["ADMIN"]
    )


def _payload(ids: dict[str, Any]) -> PlanCreate:
    return PlanCreate(
        project_id=ids["project_id"],
        name="Regression smoke",
        environment_id=ids["environment_id"],
        runner_id=ids["runner_id"],
        runtime_variables={"tenant_id": "tenant-a"},
        items=[{"target_type": "API_CASE", "case_id": ids["case_id"]}],
    )


def test_plan_pins_version_validates_and_starts_child_run(plan_context) -> None:
    factory, ids = plan_context
    store = FakeHeartbeatStore(ids["runner_id"])
    publisher = FakePublisher()
    event_stream = FakeEventStream()
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        assert plan.items[0].case_version_id == ids["case_version_id"]
        validation = validate_test_plan(session, _user(), plan.id, store)  # type: ignore[arg-type]
        assert validation.valid is True

        started = start_test_plan_run(
            session,
            _user(),
            plan.id,
            store,  # type: ignore[arg-type]
            publisher,
            event_stream,  # type: ignore[arg-type]
        )

        assert started.plan_run.status == "QUEUED"
        assert started.plan_run.total == 1
        assert len(publisher.calls) == 1
        child = session.scalar(select(RunModel))
        assert child is not None
        assert child.runtime_variables == {"tenant_id": "tenant-a"}
        assert child.case_version_id == ids["case_version_id"]

        child.status = "SUCCESS"
        session.commit()
        completed = get_test_plan_run(session, _user(), started.plan_run.id)
        assert completed.status == "SUCCESS"
        assert completed.passed == 1
        assert completed.ended_at is not None


def test_plan_runtime_variables_reject_sensitive_and_reserved_names() -> None:
    common = {
        "project_id": 1,
        "name": "Regression smoke",
        "environment_id": 1,
        "runner_id": "runner-1",
        "items": [{"target_type": "API_CASE", "case_id": 1}],
    }
    with pytest.raises(ValueError, match="敏感变量"):
        PlanCreate(**common, runtime_variables={"access_token": "plain"})
    with pytest.raises(ValueError, match="保留变量"):
        PlanCreate(**common, runtime_variables={"run_id": "spoofed"})


def test_mixed_plan_runtime_variable_validates_all_target_types(plan_context) -> None:
    factory, ids = plan_context
    store = FakeHeartbeatStore(ids["runner_id"])
    with factory() as session:
        payload = PlanCreate(
            **_payload(ids).model_dump(exclude={"items"}),
            items=[
                {"target_type": "API_CASE", "case_id": ids["case_id"]},
                {"target_type": "SCENARIO", "scenario_id": ids["scenario_id"]},
                {"target_type": "WEB_CASE", "web_case_id": ids["web_case_id"]},
            ],
        )
        plan = create_test_plan(session, _user(), payload)
        validation = validate_test_plan(session, _user(), plan.id, store)  # type: ignore[arg-type]

        assert validation.valid is True
        assert [item.target_type.value for item in validation.items] == [
            "API_CASE",
            "SCENARIO",
            "WEB_CASE",
        ]


def test_plan_update_replaces_items_and_archive_restore(plan_context) -> None:
    factory, ids = plan_context
    with factory() as session:
        created = create_test_plan(session, _user(), _payload(ids))
        payload = PlanUpdate(
            **_payload(ids).model_dump(exclude={"items", "name"}),
            name="Updated regression",
            items=[
                {"target_type": "SCENARIO", "scenario_id": ids["scenario_id"]},
                {"target_type": "API_CASE", "case_id": ids["case_id"]},
            ],
        )
        updated = update_test_plan(session, _user(), created.id, payload)
        assert updated.name == "Updated regression"
        assert [item.target_type.value for item in updated.items] == ["SCENARIO", "API_CASE"]
        assert set_test_plan_status(session, _user(), created.id, "ARCHIVED").status == "ARCHIVED"
        assert set_test_plan_status(session, _user(), created.id, "ACTIVE").status == "ACTIVE"


def test_plan_retry_dispatch_and_aggregate_cancel(plan_context) -> None:
    factory, ids = plan_context
    store = FakeHeartbeatStore(ids["runner_id"])
    publisher = FakePublisher()
    publisher.published = False
    event_stream = FakeEventStream()
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        started = start_test_plan_run(
            session,
            _user(),
            plan.id,
            store,  # type: ignore[arg-type]
            publisher,
            event_stream,  # type: ignore[arg-type]
        )
        assert started.plan_run.items[0].error_code == "DISPATCH_FAILED"

        publisher.published = True
        retried = retry_test_plan_run_dispatch(
            session,
            _user(),
            started.plan_run.id,
            store,  # type: ignore[arg-type]
            publisher,
            event_stream,  # type: ignore[arg-type]
        )
        assert retried.items[0].error_code is None
        assert len(publisher.calls) == 2

        cancelled = cancel_test_plan_run(
            session,
            _user(),
            started.plan_run.id,
            event_stream,  # type: ignore[arg-type]
        )
        assert cancelled.status == "CANCELLED"
        assert cancelled.items[0].status == "CANCELLED"
