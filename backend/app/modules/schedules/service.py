from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.logging import get_logger
from app.core.time import utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublisher
from app.infrastructure.redis.client import RedisRunEventStream, RedisRunnerHeartbeatStore
from app.modules.auth.models import User
from app.modules.auth.schemas import CurrentUser, UserStatus
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.schedules.calendar import next_occurrence
from app.modules.schedules.models import ScheduleTrigger, TestSchedule
from app.modules.schedules.schemas import (
    ScheduleCreate,
    ScheduleListResponse,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    ScheduleResponse,
    ScheduleTriggerListResponse,
    ScheduleTriggerResponse,
    ScheduleUpdate,
)
from app.modules.test_plans.models import TestPlan, TestPlanRun
from app.modules.test_plans.service import start_test_plan_run

logger = get_logger(__name__)


def _load_schedule(
    session: Session, user: CurrentUser, schedule_id: int, *, for_update: bool = False
) -> TestSchedule:
    statement = select(TestSchedule).where(TestSchedule.id == schedule_id)
    if for_update:
        statement = statement.with_for_update()
    schedule = session.scalar(statement)
    if schedule is None:
        raise ResourceNotFoundError("定时任务不存在")
    get_project(session, user, schedule.project_id)
    return schedule


def _validate_target(
    session: Session, user: CurrentUser, project_id: int, plan_id: int
) -> TestPlan:
    project = get_project(session, user, project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能保存定时任务")
    plan = session.get(TestPlan, plan_id)
    if plan is None or plan.project_id != project_id or plan.status != "ACTIVE":
        raise ResourceConflictError("定时任务必须绑定本项目已启用的 Test Plan")
    return plan


def _calculate_next(schedule: TestSchedule, after: datetime) -> datetime:
    return next_occurrence(
        schedule_type=schedule.schedule_type,
        timezone=schedule.timezone,
        after=after,
        daily_time=schedule.daily_time,
        weekdays=schedule.weekdays,
        cron_expression=schedule.cron_expression,
    )


def _schedule_response(session: Session, schedule: TestSchedule) -> ScheduleResponse:
    plan = session.get(TestPlan, schedule.plan_id)
    return ScheduleResponse(
        id=schedule.id,
        project_id=schedule.project_id,
        plan_id=schedule.plan_id,
        plan_name=plan.name if plan is not None else "已删除 Test Plan",
        name=schedule.name,
        schedule_type=schedule.schedule_type,
        timezone=schedule.timezone,
        daily_time=schedule.daily_time,
        weekdays=schedule.weekdays,
        cron_expression=schedule.cron_expression,
        enabled=schedule.enabled,
        status=schedule.status,
        next_run_at=schedule.next_run_at,
        last_scheduled_at=schedule.last_scheduled_at,
        last_trigger_status=schedule.last_trigger_status,
        last_error_code=schedule.last_error_code,
        last_error_message=schedule.last_error_message,
        created_by=schedule.created_by,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _apply_payload(schedule: TestSchedule, payload: ScheduleCreate | ScheduleUpdate) -> None:
    schedule.project_id = payload.project_id
    schedule.plan_id = payload.plan_id
    schedule.name = payload.name
    schedule.schedule_type = payload.schedule_type
    schedule.timezone = payload.timezone
    schedule.daily_time = payload.daily_time
    schedule.weekdays = payload.weekdays
    schedule.cron_expression = payload.cron_expression
    schedule.enabled = payload.enabled
    schedule.next_run_at = _calculate_next(schedule, utc_now_naive()) if payload.enabled else None
    schedule.last_error_code = None
    schedule.last_error_message = None


def create_schedule(
    session: Session, user: CurrentUser, payload: ScheduleCreate
) -> ScheduleResponse:
    _validate_target(session, user, payload.project_id, payload.plan_id)
    schedule = TestSchedule(
        project_id=payload.project_id,
        plan_id=payload.plan_id,
        name=payload.name,
        schedule_type=payload.schedule_type,
        timezone=payload.timezone,
        daily_time=payload.daily_time,
        weekdays=payload.weekdays,
        cron_expression=payload.cron_expression,
        enabled=payload.enabled,
        status="ACTIVE",
        created_by=user.id,
    )
    schedule.next_run_at = _calculate_next(schedule, utc_now_naive()) if schedule.enabled else None
    session.add(schedule)
    session.commit()
    return _schedule_response(session, schedule)


def update_schedule(
    session: Session, user: CurrentUser, schedule_id: int, payload: ScheduleUpdate
) -> ScheduleResponse:
    schedule = _load_schedule(session, user, schedule_id, for_update=True)
    if schedule.project_id != payload.project_id:
        raise ResourceConflictError("定时任务不能跨项目移动")
    if schedule.status != "ACTIVE":
        raise ResourceConflictError("已归档定时任务不能编辑")
    _validate_target(session, user, payload.project_id, payload.plan_id)
    _apply_payload(schedule, payload)
    session.commit()
    return _schedule_response(session, schedule)


def list_schedules(session: Session, user: CurrentUser, project_id: int) -> ScheduleListResponse:
    get_project(session, user, project_id)
    schedules = list(
        session.scalars(
            select(TestSchedule)
            .where(TestSchedule.project_id == project_id)
            .order_by(TestSchedule.created_at.desc(), TestSchedule.id.desc())
        ).all()
    )
    return ScheduleListResponse(
        items=[_schedule_response(session, schedule) for schedule in schedules],
        total=len(schedules),
    )


def get_schedule(session: Session, user: CurrentUser, schedule_id: int) -> ScheduleResponse:
    return _schedule_response(session, _load_schedule(session, user, schedule_id))


def set_schedule_status(
    session: Session, user: CurrentUser, schedule_id: int, status: str
) -> ScheduleResponse:
    schedule = _load_schedule(session, user, schedule_id, for_update=True)
    project = get_project(session, user, schedule.project_id)
    ensure_project_writable(session, project, user)
    schedule.status = status
    if status == "ARCHIVED":
        schedule.enabled = False
        schedule.next_run_at = None
    else:
        schedule.next_run_at = (
            _calculate_next(schedule, utc_now_naive()) if schedule.enabled else None
        )
    session.commit()
    return _schedule_response(session, schedule)


def preview_schedule(
    payload: SchedulePreviewRequest, *, after: datetime | None = None
) -> SchedulePreviewResponse:
    cursor = after or utc_now_naive()
    occurrences: list[datetime] = []
    for _ in range(payload.count):
        cursor = next_occurrence(
            schedule_type=payload.schedule_type,
            timezone=payload.timezone,
            after=cursor,
            daily_time=payload.daily_time,
            weekdays=payload.weekdays,
            cron_expression=payload.cron_expression,
        )
        occurrences.append(cursor)
    return SchedulePreviewResponse(occurrences=occurrences)


def _trigger_response(session: Session, trigger: ScheduleTrigger) -> ScheduleTriggerResponse:
    schedule = session.get(TestSchedule, trigger.schedule_id)
    plan_run = session.get(TestPlanRun, trigger.plan_run_id) if trigger.plan_run_id else None
    return ScheduleTriggerResponse(
        id=trigger.id,
        schedule_id=trigger.schedule_id,
        schedule_name=schedule.name if schedule is not None else "已删除定时任务",
        project_id=trigger.project_id,
        scheduled_for_at=trigger.scheduled_for_at,
        status=trigger.status,
        plan_run_id=trigger.plan_run_id,
        plan_run_status=plan_run.status if plan_run is not None else None,
        error_code=trigger.error_code,
        error_message=trigger.error_message,
        created_at=trigger.created_at,
        completed_at=trigger.completed_at,
    )


def list_schedule_triggers(
    session: Session, user: CurrentUser, project_id: int
) -> ScheduleTriggerListResponse:
    get_project(session, user, project_id)
    triggers = list(
        session.scalars(
            select(ScheduleTrigger)
            .where(ScheduleTrigger.project_id == project_id)
            .order_by(ScheduleTrigger.created_at.desc(), ScheduleTrigger.id.desc())
            .limit(100)
        ).all()
    )
    return ScheduleTriggerListResponse(
        items=[_trigger_response(session, trigger) for trigger in triggers],
        total=len(triggers),
    )


def claim_due_schedule(
    session: Session, schedule_id: int, *, now: datetime | None = None
) -> str | None:
    current = now or utc_now_naive()
    schedule = session.scalar(
        select(TestSchedule).where(TestSchedule.id == schedule_id).with_for_update()
    )
    if (
        schedule is None
        or schedule.status != "ACTIVE"
        or not schedule.enabled
        or schedule.next_run_at is None
        or schedule.next_run_at > current
    ):
        return None
    scheduled_for = schedule.next_run_at
    trigger = ScheduleTrigger(
        id=f"schedule_{uuid4().hex}",
        schedule_id=schedule.id,
        project_id=schedule.project_id,
        scheduled_for_at=scheduled_for,
        status="CLAIMED",
    )
    schedule.last_scheduled_at = scheduled_for
    schedule.last_trigger_status = "CLAIMED"
    schedule.last_error_code = None
    schedule.last_error_message = None
    schedule.next_run_at = _calculate_next(schedule, max(current, scheduled_for))
    session.add(trigger)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return trigger.id


def _current_user_for_schedule(session: Session, schedule: TestSchedule) -> CurrentUser:
    user = session.get(User, schedule.created_by)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise ResourceConflictError("定时任务创建人不存在或已停用")
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=[user.platform_role],
    )


def dispatch_claimed_trigger(
    session: Session,
    trigger_id: str,
    heartbeat_store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> ScheduleTriggerResponse:
    trigger = session.scalar(
        select(ScheduleTrigger).where(ScheduleTrigger.id == trigger_id).with_for_update()
    )
    if trigger is None:
        raise ResourceNotFoundError("调度触发记录不存在")
    schedule = session.get(TestSchedule, trigger.schedule_id)
    if schedule is None:
        trigger.status = "SKIPPED"
        trigger.error_code = "SCHEDULE_NOT_FOUND"
        trigger.error_message = "定时任务已不存在"
        trigger.completed_at = utc_now_naive()
        session.commit()
        return _trigger_response(session, trigger)
    if trigger.status != "CLAIMED":
        return _trigger_response(session, trigger)
    try:
        if schedule.status != "ACTIVE" or not schedule.enabled:
            trigger.status = "SKIPPED"
            trigger.error_code = "SCHEDULE_DISABLED"
            trigger.error_message = "定时任务已停用或归档"
        else:
            actor = _current_user_for_schedule(session, schedule)
            result = start_test_plan_run(
                session,
                actor,
                schedule.plan_id,
                heartbeat_store,
                publisher,
                event_stream,
                trigger_type="SCHEDULE",
                schedule_id=schedule.id,
            )
            trigger = session.get(ScheduleTrigger, trigger_id)
            schedule = session.get(TestSchedule, schedule.id)
            assert trigger is not None and schedule is not None
            trigger.status = "DISPATCHED"
            trigger.plan_run_id = result.plan_run.id
            trigger.error_code = None
            trigger.error_message = None
        trigger.completed_at = utc_now_naive()
    except AppError as exc:
        session.rollback()
        trigger = session.get(ScheduleTrigger, trigger_id)
        schedule = session.get(TestSchedule, trigger.schedule_id) if trigger else None
        if trigger is None or schedule is None:
            raise
        trigger.status = "FAILED"
        trigger.error_code = exc.code
        trigger.error_message = exc.message
        trigger.completed_at = utc_now_naive()
    schedule.last_trigger_status = trigger.status
    schedule.last_error_code = trigger.error_code
    schedule.last_error_message = trigger.error_message
    session.commit()
    return _trigger_response(session, trigger)


def retry_schedule_trigger(
    session: Session,
    user: CurrentUser,
    trigger_id: str,
    heartbeat_store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> ScheduleTriggerResponse:
    trigger = session.get(ScheduleTrigger, trigger_id)
    if trigger is None:
        raise ResourceNotFoundError("调度触发记录不存在")
    project = get_project(session, user, trigger.project_id)
    ensure_project_writable(session, project, user)
    if trigger.status != "FAILED" or trigger.plan_run_id is not None:
        raise ResourceConflictError("只有尚未创建计划 Run 的失败触发可以重试")
    trigger.status = "CLAIMED"
    trigger.error_code = None
    trigger.error_message = None
    trigger.completed_at = None
    session.commit()
    return dispatch_claimed_trigger(session, trigger.id, heartbeat_store, publisher, event_stream)
