"""Periodic background coordination for disconnected and overdue Runs.

Disconnect failure still requires Redis to report the heartbeat key missing
while available plus an expired MySQL heartbeat. Execution-deadline closure is
independent of heartbeat state and uses persisted ``started_at`` plus the
effective Run timeout and a bounded reporting grace. Both paths recheck under a
Run row lock so normal completion is never overwritten.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import utc_now_naive
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.runners.models import Runner
from app.modules.runners.schemas import RunnerStatus
from app.modules.runs.enums import RunStatus
from app.modules.runs.models import TestRun
from app.modules.runs.service import (
    reconcile_disconnected_runner_runs_core,
    reconcile_overdue_run_core,
)
from app.modules.web_design.models import WebExploration

logger = get_logger(__name__)

_DISCONNECT_RUN_STATUSES = (RunStatus.ASSIGNED.value, RunStatus.RUNNING.value)
_OVERDUE_RUN_STATUSES = (RunStatus.RUNNING.value, RunStatus.CANCELLING.value)
_STALE_EXPLORATION_STATUSES = ("QUEUED", "RUNNING", "STOP_REQUESTED")


@dataclass
class RunnerDisconnectScanRound:
    """Observable summary of one scan round, used by logging and tests."""

    started_at: datetime
    candidate_runner_ids: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    reconciled_run_count: int = 0
    overdue_candidate_run_ids: list[str] = field(default_factory=list)
    overdue_run_count: int = 0
    overdue_run_ids: list[str] = field(default_factory=list)
    stale_exploration_candidate_ids: list[str] = field(default_factory=list)
    stale_exploration_count: int = 0
    stale_exploration_ids: list[str] = field(default_factory=list)


class RunnerDisconnectCoordinator:
    """Runs disconnect and execution-deadline scans on one controllable thread.

    ``now_provider`` and ``interval_seconds`` are injectable so tests never wait
    for the real heartbeat TTL; the loop wait itself is the interruptible
    ``threading.Event.wait`` so shutdown does not sleep out the interval.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        session_factory: Callable[[], Session] | None = None,
        heartbeat_store: RedisRunnerHeartbeatStore | None = None,
        event_stream: RedisRunEventStream | None = None,
        interval_seconds: float | None = None,
        heartbeat_ttl_seconds: float | None = None,
        execution_timeout_grace_seconds: float | None = None,
        web_exploration_stale_timeout_seconds: float | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = enabled
        self.session_factory = session_factory or SessionLocal
        self.heartbeat_store = heartbeat_store or get_runner_heartbeat_store()
        self.event_stream = event_stream or get_run_event_stream()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.runner_disconnect_scan_interval_seconds
        )
        self.heartbeat_ttl_seconds = (
            heartbeat_ttl_seconds
            if heartbeat_ttl_seconds is not None
            else settings.runner_heartbeat_ttl_seconds
        )
        self.execution_timeout_grace_seconds = (
            execution_timeout_grace_seconds
            if execution_timeout_grace_seconds is not None
            else settings.runner_execution_timeout_grace_seconds
        )
        self.web_exploration_stale_timeout_seconds = web_exploration_stale_timeout_seconds
        if not 0 <= self.execution_timeout_grace_seconds <= 3600:
            raise ValueError("execution_timeout_grace_seconds 必须位于 0 到 3600 之间")
        if (
            self.web_exploration_stale_timeout_seconds is not None
            and not 60 <= self.web_exploration_stale_timeout_seconds <= 86_400
        ):
            raise ValueError("web_exploration_stale_timeout_seconds 必须位于 60 到 86400 之间")
        self._now_provider = now_provider or utc_now_naive
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    def _now(self) -> datetime:
        return self._now_provider()

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self.run_forever,
            args=(self._stop_event,),
            name="runner-disconnect-reconcile",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "runner_disconnect_coordinator_started",
            extra={
                "event": "RUNNER_DISCONNECT_COORDINATOR_STARTED",
                "interval_seconds": self.interval_seconds,
                "heartbeat_ttl_seconds": self.heartbeat_ttl_seconds,
                "execution_timeout_grace_seconds": self.execution_timeout_grace_seconds,
                "web_exploration_stale_timeout_seconds": (
                    self.web_exploration_stale_timeout_seconds
                ),
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
                "runner_disconnect_coordinator_stop_timeout",
                extra={"event": "RUNNER_DISCONNECT_COORDINATOR_STOP_TIMEOUT"},
            )
        self._thread = None
        logger.info(
            "runner_disconnect_coordinator_stopped",
            extra={"event": "RUNNER_DISCONNECT_COORDINATOR_STOPPED"},
        )

    def run_forever(self, stop_event: threading.Event) -> None:
        """Scan until the stop event is set; one bad round must never end the loop."""

        while not stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001 - a failed round is logged, not fatal
                logger.warning(
                    "runner_disconnect_scan_round_failed",
                    extra={
                        "event": "RUNNER_DISCONNECT_SCAN_ROUND_FAILED",
                        "error_type": type(exc).__name__,
                    },
                )
            stop_event.wait(self.interval_seconds)

    def scan_once(self) -> RunnerDisconnectScanRound:
        """Run one full scan round; per-runner failures are recorded and skipped."""

        round_result = RunnerDisconnectScanRound(started_at=self._now())
        if self.web_exploration_stale_timeout_seconds is not None:
            try:
                round_result.stale_exploration_candidate_ids = (
                    self._candidate_stale_exploration_ids()
                )
            except Exception as exc:  # noqa: BLE001 - next round retries candidate query
                round_result.errors.append(("", type(exc).__name__))
                logger.warning(
                    "stale_web_exploration_candidate_query_failed",
                    extra={
                        "event": "STALE_WEB_EXPLORATION_CANDIDATE_QUERY_FAILED",
                        "error_type": type(exc).__name__,
                    },
                )
        for exploration_id in round_result.stale_exploration_candidate_ids:
            try:
                reconciled = self._reconcile_stale_exploration(exploration_id)
            except Exception as exc:  # noqa: BLE001 - one task must not abort the round
                round_result.errors.append((exploration_id, type(exc).__name__))
                logger.warning(
                    "stale_web_exploration_reconcile_failed",
                    extra={
                        "event": "STALE_WEB_EXPLORATION_RECONCILE_FAILED",
                        "exploration_id": exploration_id,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                if reconciled:
                    round_result.stale_exploration_count += 1
                    round_result.stale_exploration_ids.append(exploration_id)
        try:
            round_result.overdue_candidate_run_ids = self._candidate_overdue_run_ids()
        except Exception as exc:  # noqa: BLE001 - next round retries the candidate query
            round_result.errors.append(("", type(exc).__name__))
            logger.warning(
                "overdue_run_candidate_query_failed",
                extra={
                    "event": "OVERDUE_RUN_CANDIDATE_QUERY_FAILED",
                    "error_type": type(exc).__name__,
                },
            )
        for run_id in round_result.overdue_candidate_run_ids:
            try:
                reconciled = self._reconcile_overdue_run(run_id)
            except Exception as exc:  # noqa: BLE001 - one Run must not abort the round
                round_result.errors.append((run_id, type(exc).__name__))
                logger.warning(
                    "overdue_run_reconcile_failed",
                    extra={
                        "event": "OVERDUE_RUN_RECONCILE_FAILED",
                        "run_id": run_id,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                if reconciled:
                    round_result.overdue_run_count += 1
                    round_result.overdue_run_ids.append(run_id)

        try:
            round_result.candidate_runner_ids = self._candidate_runner_ids()
        except Exception as exc:  # noqa: BLE001 - next round retries the candidate query
            round_result.errors.append(("", type(exc).__name__))
            logger.warning(
                "runner_disconnect_candidate_query_failed",
                extra={
                    "event": "RUNNER_DISCONNECT_CANDIDATE_QUERY_FAILED",
                    "error_type": type(exc).__name__,
                },
            )
        for runner_id in round_result.candidate_runner_ids:
            try:
                round_result.reconciled_run_count += self._reconcile_runner(runner_id)
            except Exception as exc:  # noqa: BLE001 - one Runner must not abort the round
                round_result.errors.append((runner_id, type(exc).__name__))
                logger.warning(
                    "runner_disconnect_reconcile_failed",
                    extra={
                        "event": "RUNNER_DISCONNECT_RECONCILE_FAILED",
                        "runner_id": runner_id,
                        "error_type": type(exc).__name__,
                    },
                )
        if (
            round_result.candidate_runner_ids
            or round_result.overdue_candidate_run_ids
            or round_result.stale_exploration_candidate_ids
            or round_result.errors
        ):
            logger.info(
                "runner_disconnect_scan_round",
                extra={
                    "event": "RUNNER_DISCONNECT_SCAN_ROUND",
                    "candidate_runner_ids": round_result.candidate_runner_ids,
                    "reconciled_run_count": round_result.reconciled_run_count,
                    "overdue_candidate_run_ids": round_result.overdue_candidate_run_ids,
                    "overdue_run_count": round_result.overdue_run_count,
                    "stale_exploration_candidate_ids": (
                        round_result.stale_exploration_candidate_ids
                    ),
                    "stale_exploration_count": round_result.stale_exploration_count,
                    "error_count": len(round_result.errors),
                },
            )
        return round_result

    def _candidate_stale_exploration_ids(self) -> list[str]:
        assert self.web_exploration_stale_timeout_seconds is not None
        cutoff = self._now() - timedelta(
            seconds=self.web_exploration_stale_timeout_seconds
        )
        with self.session_factory() as session:
            statement = (
                select(WebExploration.id)
                .where(
                    WebExploration.status.in_(_STALE_EXPLORATION_STATUSES),
                    WebExploration.updated_at < cutoff,
                )
                .order_by(WebExploration.updated_at.asc(), WebExploration.id.asc())
            )
            return list(session.scalars(statement).all())

    def _reconcile_stale_exploration(self, exploration_id: str) -> bool:
        assert self.web_exploration_stale_timeout_seconds is not None
        now = self._now()
        cutoff = now - timedelta(seconds=self.web_exploration_stale_timeout_seconds)
        with self.session_factory() as session:
            try:
                exploration = session.scalar(
                    select(WebExploration)
                    .where(WebExploration.id == exploration_id)
                    .with_for_update()
                )
                if (
                    exploration is None
                    or exploration.status not in _STALE_EXPLORATION_STATUSES
                    or exploration.updated_at >= cutoff
                ):
                    return False
                exploration.status = "FAILED"
                exploration.completed_at = now
                exploration.error_type = "WEB_EXPLORATION_STALE"
                exploration.error_message = (
                    "Web 探索长时间未收到 Runner 进度或完成回执，已自动结束"
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
        return True

    def _candidate_overdue_run_ids(self) -> list[str]:
        now = self._now()
        with self.session_factory() as session:
            grace_microseconds = int(self.execution_timeout_grace_seconds * 1_000_000)
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "mysql":
                deadline = func.timestampadd(
                    literal_column("MICROSECOND"),
                    TestRun.total_timeout_ms * 1_000 + grace_microseconds,
                    TestRun.started_at,
                )
                deadline_filter = deadline < now
            elif dialect_name == "sqlite":
                deadline = func.julianday(TestRun.started_at) + (
                    (
                        TestRun.total_timeout_ms / 1_000.0
                        + self.execution_timeout_grace_seconds
                    )
                    / 86_400.0
                )
                deadline_filter = deadline < func.julianday(now)
            else:
                # The database-independent prefilter is deliberately
                # conservative; the exact deadline is checked below and again
                # after the Run row is locked by the reconciliation core.
                deadline_filter = TestRun.started_at < now - timedelta(
                    seconds=self.execution_timeout_grace_seconds + 1
                )
            statement = (
                select(TestRun.id, TestRun.started_at, TestRun.total_timeout_ms)
                .where(
                    TestRun.status.in_(_OVERDUE_RUN_STATUSES),
                    TestRun.started_at.is_not(None),
                    TestRun.total_timeout_ms.is_not(None),
                    deadline_filter,
                )
                .order_by(TestRun.started_at.asc(), TestRun.id.asc())
            )
            rows = session.execute(statement).all()
            return [
                run_id
                for run_id, started_at, total_timeout_ms in rows
                if started_at is not None
                and total_timeout_ms is not None
                and now
                > started_at
                + timedelta(
                    milliseconds=total_timeout_ms,
                    seconds=self.execution_timeout_grace_seconds,
                )
            ]

    def _reconcile_overdue_run(self, run_id: str) -> bool:
        with self.session_factory() as session:
            try:
                status = reconcile_overdue_run_core(
                    session,
                    run_id,
                    self.event_stream,
                    grace_seconds=self.execution_timeout_grace_seconds,
                    now=self._now(),
                )
            except Exception:
                session.rollback()
                raise
            return status is not None

    def _candidate_runner_ids(self) -> list[str]:
        with self.session_factory() as session:
            statement = (
                select(Runner.id)
                .where(
                    Runner.status == RunnerStatus.ACTIVE.value,
                    Runner.id.in_(
                        select(TestRun.runner_id).where(
                            TestRun.runner_id.is_not(None),
                            TestRun.status.in_(_DISCONNECT_RUN_STATUSES),
                        )
                    ),
                )
                .order_by(Runner.id)
            )
            return list(session.scalars(statement).all())

    def _reconcile_runner(self, runner_id: str) -> int:
        """Reconcile one Runner in its own short-lived session with a fresh deadline."""

        with self.session_factory() as session:
            try:
                result = reconcile_disconnected_runner_runs_core(
                    session,
                    runner_id,
                    self.heartbeat_store,
                    self.event_stream,
                    require_heartbeat_expiry=True,
                    heartbeat_ttl_seconds=self.heartbeat_ttl_seconds,
                    now=self._now(),
                )
            except Exception:
                session.rollback()
                raise
            return result.reconciled_run_count


def build_disconnect_coordinator() -> RunnerDisconnectCoordinator:
    """Factory used by the FastAPI lifespan; overridable in tests via dependency_overrides."""

    settings = get_settings()
    return RunnerDisconnectCoordinator(
        enabled=settings.runner_disconnect_scan_enabled,
        interval_seconds=settings.runner_disconnect_scan_interval_seconds,
        heartbeat_ttl_seconds=settings.runner_heartbeat_ttl_seconds,
        execution_timeout_grace_seconds=settings.runner_execution_timeout_grace_seconds,
        web_exploration_stale_timeout_seconds=(
            settings.web_exploration_stale_timeout_seconds
        ),
    )
