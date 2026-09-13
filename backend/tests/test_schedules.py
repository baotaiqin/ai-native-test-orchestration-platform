from datetime import datetime

import pytest
from sqlalchemy import select

from app.modules.runs.models import TestRun as RunModel
from app.modules.schedules.calendar import ScheduleExpressionError, next_occurrence, parse_cron
from app.modules.schedules.coordinator import TestScheduleCoordinator as ScheduleCoordinator
from app.modules.schedules.models import ScheduleTrigger
from app.modules.schedules.models import TestSchedule as ScheduleModel
from app.modules.schedules.schemas import ScheduleCreate, ScheduleUpdate
from app.modules.schedules.service import (
    create_schedule,
    retry_schedule_trigger,
    set_schedule_status,
    update_schedule,
)
from app.modules.test_plans.models import TestPlanRun as PlanRunModel
from app.modules.test_plans.service import create_test_plan
from tests.test_test_plans import (  # noqa: F401
    FakeEventStream,
    FakeHeartbeatStore,
    FakePublisher,
    _payload,
    _user,
    plan_context,
)


def _schedule_payload(ids: dict[str, object], plan_id: int) -> ScheduleCreate:
    return ScheduleCreate(
        project_id=ids["project_id"],
        plan_id=plan_id,
        name="工作日回归",
        schedule_type="WEEKLY",
        timezone="Asia/Shanghai",
        daily_time="09:30",
        weekdays=[1, 2, 3, 4, 5],
        enabled=True,
    )


def test_calendar_supports_daily_weekly_and_cron() -> None:
    assert next_occurrence(
        schedule_type="DAILY",
        timezone="Asia/Shanghai",
        daily_time="09:00",
        after=datetime(2026, 9, 13, 0, 30),
    ) == datetime(2026, 9, 13, 1, 0)
    assert next_occurrence(
        schedule_type="WEEKLY",
        timezone="UTC",
        daily_time="10:15",
        weekdays=[1],
        after=datetime(2026, 9, 13, 12, 0),
    ) == datetime(2026, 9, 14, 10, 15)
    assert next_occurrence(
        schedule_type="CRON",
        timezone="UTC",
        cron_expression="*/15 9 * * 1-5",
        after=datetime(2026, 9, 14, 9, 14),
    ) == datetime(2026, 9, 14, 9, 15)
    assert 0 in parse_cron("0 0 * * 7").weekdays
    with pytest.raises(ScheduleExpressionError, match="5 段"):
        parse_cron("0 9 * *")


def test_schedule_crud_recomputes_next_and_archive_stops_trigger(
    plan_context,  # noqa: F811
) -> None:
    factory, ids = plan_context
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        created = create_schedule(session, _user(), _schedule_payload(ids, plan.id))
        assert created.status == "ACTIVE"
        assert created.next_run_at is not None
        assert created.weekdays == [1, 2, 3, 4, 5]

        updated_payload = ScheduleUpdate(
            project_id=ids["project_id"],
            plan_id=plan.id,
            name="每日冒烟",
            schedule_type="DAILY",
            timezone="UTC",
            daily_time="08:00",
            enabled=False,
        )
        updated = update_schedule(session, _user(), created.id, updated_payload)
        assert updated.schedule_type == "DAILY"
        assert updated.next_run_at is None
        archived = set_schedule_status(session, _user(), created.id, "ARCHIVED")
        assert archived.status == "ARCHIVED"
        assert archived.enabled is False


def test_coordinator_claims_once_and_creates_scheduled_plan_run(
    plan_context,  # noqa: F811
) -> None:
    factory, ids = plan_context
    now = datetime(2026, 9, 14, 1, 1)
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        schedule = create_schedule(session, _user(), _schedule_payload(ids, plan.id))
        schedule_model = session.get(ScheduleModel, schedule.id)
        assert schedule_model is not None
        schedule_model.next_run_at = datetime(2026, 9, 14, 1, 0)
        session.commit()

    publisher = FakePublisher()
    coordinator = ScheduleCoordinator(
        session_factory=factory,
        heartbeat_store=FakeHeartbeatStore(str(ids["runner_id"])),
        publisher=publisher,
        event_stream=FakeEventStream(),
        now_provider=lambda: now,
        interval_seconds=5,
    )
    first = coordinator.scan_once()
    second = coordinator.scan_once()
    assert len(first.claimed_trigger_ids) == 1
    assert first.dispatched_trigger_ids == first.claimed_trigger_ids
    assert second.claimed_trigger_ids == []
    with factory() as session:
        triggers = list(session.scalars(select(ScheduleTrigger)).all())
        assert len(triggers) == 1
        assert triggers[0].status == "DISPATCHED"
        plan_run = session.get(PlanRunModel, triggers[0].plan_run_id)
        assert plan_run is not None
        assert plan_run.trigger_type == "SCHEDULE"
        assert plan_run.schedule_id == schedule.id
        child = session.scalar(select(RunModel))
        assert child is not None and child.trigger_type == "SYSTEM"
        assert len(publisher.calls) == 1


def test_failed_due_trigger_is_visible_and_retryable(
    plan_context,  # noqa: F811
) -> None:
    factory, ids = plan_context
    now = datetime(2026, 9, 14, 1, 1)
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        schedule = create_schedule(session, _user(), _schedule_payload(ids, plan.id))
        schedule_model = session.get(ScheduleModel, schedule.id)
        assert schedule_model is not None
        schedule_model.next_run_at = datetime(2026, 9, 14, 1, 0)
        session.commit()

    coordinator = ScheduleCoordinator(
        session_factory=factory,
        heartbeat_store=FakeHeartbeatStore("another-runner"),
        publisher=FakePublisher(),
        event_stream=FakeEventStream(),
        now_provider=lambda: now,
        interval_seconds=5,
    )
    result = coordinator.scan_once()
    assert len(result.failed_trigger_ids) == 1
    with factory() as session:
        trigger = session.get(ScheduleTrigger, result.failed_trigger_ids[0])
        assert trigger is not None
        assert trigger.status == "FAILED"
        assert trigger.plan_run_id is None
        retried = retry_schedule_trigger(
            session,
            _user(),
            trigger.id,
            FakeHeartbeatStore(str(ids["runner_id"])),
            FakePublisher(),
            FakeEventStream(),
        )
        assert retried.status == "DISPATCHED"
        assert retried.plan_run_id is not None
