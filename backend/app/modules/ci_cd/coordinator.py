import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import WEBHOOK_DELIVERIES
from app.core.time import utc_now_naive
from app.infrastructure.db.session import SessionLocal
from app.modules.ci_cd.models import WebhookDelivery
from app.modules.ci_cd.service import (
    HttpxWebhookSender,
    WebhookSender,
    dispatch_webhook_delivery,
)
from app.modules.test_plans.models import TestPlanRun
from app.modules.test_plans.service import refresh_test_plan_run_state

logger = get_logger(__name__)


@dataclass
class WebhookScanRound:
    started_at: datetime
    refreshed_plan_run_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    succeeded_ids: list[str] = field(default_factory=list)
    pending_retry_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)


class WebhookDeliveryCoordinator:
    def __init__(
        self,
        *,
        enabled: bool = True,
        session_factory: Callable[[], Session] | None = None,
        sender: WebhookSender | None = None,
        interval_seconds: float | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = enabled
        self.session_factory = session_factory or SessionLocal
        self.sender = sender or HttpxWebhookSender()
        self.interval_seconds = interval_seconds or settings.webhook_scan_interval_seconds
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
            name="webhook-delivery-outbox",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "webhook_coordinator_started",
            extra={
                "event": "WEBHOOK_COORDINATOR_STARTED",
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
                "webhook_coordinator_stop_timeout",
                extra={"event": "WEBHOOK_COORDINATOR_STOP_TIMEOUT"},
            )
        self._thread = None

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001 - a failed round must not stop retries
                logger.warning(
                    "webhook_scan_round_failed",
                    extra={
                        "event": "WEBHOOK_SCAN_ROUND_FAILED",
                        "error_type": type(exc).__name__,
                    },
                )
            stop_event.wait(self.interval_seconds)

    def scan_once(self) -> WebhookScanRound:
        now = self._now_provider()
        result = WebhookScanRound(started_at=now)
        with self.session_factory() as session:
            plan_run_ids = list(
                session.scalars(
                    select(TestPlanRun.id)
                    .where(TestPlanRun.status.in_(("CREATED", "QUEUED", "RUNNING")))
                    .order_by(TestPlanRun.updated_at.asc(), TestPlanRun.id.asc())
                    .limit(100)
                ).all()
            )
            for plan_run_id in plan_run_ids:
                try:
                    refresh_test_plan_run_state(session, plan_run_id)
                    result.refreshed_plan_run_ids.append(plan_run_id)
                except Exception as exc:  # noqa: BLE001 - one run must not block the outbox
                    logger.warning(
                        "webhook_plan_run_refresh_failed",
                        extra={
                            "event": "WEBHOOK_PLAN_RUN_REFRESH_FAILED",
                            "plan_run_id": plan_run_id,
                            "error_type": type(exc).__name__,
                        },
                    )
            due_at = self._now_provider()
            result.candidate_ids = list(
                session.scalars(
                    select(WebhookDelivery.id)
                    .where(
                        (
                            (
                                WebhookDelivery.status.in_(("PENDING", "RETRY"))
                                & (WebhookDelivery.next_attempt_at <= due_at)
                            )
                            | (
                                (WebhookDelivery.status == "SENDING")
                                & (
                                    WebhookDelivery.last_attempt_at
                                    <= due_at - timedelta(seconds=90)
                                )
                            )
                        ),
                    )
                    .order_by(WebhookDelivery.next_attempt_at.asc(), WebhookDelivery.id.asc())
                    .limit(100)
                ).all()
            )
        for delivery_id in result.candidate_ids:
            try:
                with self.session_factory() as session:
                    response = dispatch_webhook_delivery(session, delivery_id, self.sender)
            except Exception as exc:  # noqa: BLE001 - keep the outbox alive
                result.failed_ids.append(delivery_id)
                logger.warning(
                    "webhook_delivery_unhandled_failure",
                    extra={
                        "event": "WEBHOOK_DELIVERY_UNHANDLED_FAILURE",
                        "delivery_id": delivery_id,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            if response.status == "SUCCEEDED":
                result.succeeded_ids.append(delivery_id)
            elif response.status == "RETRY":
                result.pending_retry_ids.append(delivery_id)
            elif response.status == "FAILED":
                result.failed_ids.append(delivery_id)
            WEBHOOK_DELIVERIES.labels(status=response.status.lower()).inc()
        return result


def build_webhook_coordinator() -> WebhookDeliveryCoordinator:
    settings = get_settings()
    return WebhookDeliveryCoordinator(
        enabled=settings.webhook_scan_enabled,
        interval_seconds=settings.webhook_scan_interval_seconds,
    )
