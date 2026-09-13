import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import utc_now_naive
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.schedules.models import ScheduleTrigger, TestSchedule
from app.modules.schedules.service import claim_due_schedule, dispatch_claimed_trigger

logger = get_logger(__name__)


@dataclass
class ScheduleScanRound:
    started_at: datetime
    due_schedule_ids: list[int] = field(default_factory=list)
    claimed_trigger_ids: list[str] = field(default_factory=list)
    dispatched_trigger_ids: list[str] = field(default_factory=list)
    failed_trigger_ids: list[str] = field(default_factory=list)


class TestScheduleCoordinator:
    def __init__(
        self,
        *,
        enabled: bool = True,
        session_factory: Callable[[], Session] | None = None,
        heartbeat_store: RedisRunnerHeartbeatStore | None = None,
        publisher: TaskPublisher | None = None,
        event_stream: RedisRunEventStream | None = None,
        interval_seconds: float | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = enabled
        self.session_factory = session_factory or SessionLocal
        self.heartbeat_store = heartbeat_store or get_runner_heartbeat_store()
        self.publisher = publisher or get_task_publisher()
        self.event_stream = event_stream or get_run_event_stream()
        self.interval_seconds = interval_seconds or settings.schedule_scan_interval_seconds
        self._now_provider = now_provider or utc_now_naive
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self.run_forever,
            args=(self._stop_event,),
            name="test-plan-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "schedule_coordinator_started",
            extra={
                "event": "SCHEDULE_COORDINATOR_STARTED",
                "interval_seconds": self.interval_seconds,
            },
        )

    def stop(self, timeout_seconds: float = 15.0) -> None:
        thread = self._thread
        if thread is None or self._stop_event is None:
            return
        self._stop_event.set()
        thread.join(timeout_seconds)
        if thread.is_alive():
            logger.warning(
                "schedule_coordinator_stop_timeout",
                extra={"event": "SCHEDULE_COORDINATOR_STOP_TIMEOUT"},
            )
        self._thread = None

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001 - one round must not stop future schedules
                logger.warning(
                    "schedule_scan_round_failed",
                    extra={"event": "SCHEDULE_SCAN_ROUND_FAILED", "error_type": type(exc).__name__},
                )
            stop_event.wait(self.interval_seconds)

    def scan_once(self) -> ScheduleScanRound:
        now = self._now_provider()
        result = ScheduleScanRound(started_at=now)
        with self.session_factory() as session:
            result.due_schedule_ids = list(
                session.scalars(
                    select(TestSchedule.id)
                    .where(
                        TestSchedule.status == "ACTIVE",
                        TestSchedule.enabled.is_(True),
                        TestSchedule.next_run_at.is_not(None),
                        TestSchedule.next_run_at <= now,
                    )
                    .order_by(TestSchedule.next_run_at.asc(), TestSchedule.id.asc())
                    .limit(100)
                ).all()
            )
        for schedule_id in result.due_schedule_ids:
            with self.session_factory() as session:
                trigger_id = claim_due_schedule(session, schedule_id, now=now)
            if trigger_id is None:
                continue
            result.claimed_trigger_ids.append(trigger_id)
            try:
                with self.session_factory() as session:
                    response = dispatch_claimed_trigger(
                        session,
                        trigger_id,
                        self.heartbeat_store,
                        self.publisher,
                        self.event_stream,
                    )
                if response.status == "DISPATCHED":
                    result.dispatched_trigger_ids.append(trigger_id)
                else:
                    result.failed_trigger_ids.append(trigger_id)
            except Exception as exc:  # noqa: BLE001 - persist a safe failure and continue
                result.failed_trigger_ids.append(trigger_id)
                with self.session_factory() as session:
                    trigger = session.get(ScheduleTrigger, trigger_id)
                    schedule = session.get(TestSchedule, trigger.schedule_id) if trigger else None
                    if trigger is not None and trigger.status == "CLAIMED":
                        trigger.status = "FAILED"
                        trigger.error_code = "SCHEDULER_INTERNAL_ERROR"
                        trigger.error_message = "调度触发失败，请检查服务日志后重试"
                        trigger.completed_at = self._now_provider()
                        if schedule is not None:
                            schedule.last_trigger_status = trigger.status
                            schedule.last_error_code = trigger.error_code
                            schedule.last_error_message = trigger.error_message
                        session.commit()
                logger.warning(
                    "schedule_trigger_failed",
                    extra={
                        "event": "SCHEDULE_TRIGGER_FAILED",
                        "trigger_id": trigger_id,
                        "error_type": type(exc).__name__,
                    },
                )
        return result


def build_schedule_coordinator() -> TestScheduleCoordinator:
    settings = get_settings()
    return TestScheduleCoordinator(
        enabled=settings.schedule_scan_enabled,
        interval_seconds=settings.schedule_scan_interval_seconds,
    )
