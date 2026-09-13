"""Safe Run event payloads and best-effort Redis publication."""

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.core.time import to_utc_isoformat, utc_now_aware
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisStoreUnavailableError,
)
from app.modules.runs.enums import TERMINAL_RUN_STATUSES, RunStatus

logger = get_logger(__name__)


def _event_type(to_status: RunStatus, from_status: RunStatus | None) -> str:
    if from_status is None and to_status == RunStatus.CREATED:
        return "RUN_CREATED"
    if to_status == RunStatus.QUEUED:
        return "RUN_DISPATCHED"
    if to_status == RunStatus.ASSIGNED:
        return "RUN_CLAIMED"
    if to_status == RunStatus.RUNNING:
        return "RUN_STARTED"
    if to_status == RunStatus.CANCELLING:
        return "RUN_CANCELLING"
    if to_status == RunStatus.CANCELLED:
        return "RUN_CANCELLED"
    if to_status in TERMINAL_RUN_STATUSES:
        return "RUN_COMPLETED"
    return "RUN_STATUS_CHANGED"


def build_run_event(
    *,
    project_id: int,
    run_id: str,
    from_status: RunStatus | None,
    to_status: RunStatus,
    case_run_id: int | None = None,
    step_run_id: int | None = None,
    occurred_at: datetime | None = None,
    total: int | None = None,
    pass_count: int | None = None,
    fail_count: int | None = None,
    review_count: int | None = None,
    timeout_count: int | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Build the intentionally small, non-sensitive stream event contract."""

    event: dict[str, Any] = {
        "schema_version": 1,
        "event_type": event_type or _event_type(to_status, from_status),
        "project_id": project_id,
        "run_id": run_id,
        "case_run_id": case_run_id,
        "step_run_id": step_run_id,
        "from_status": from_status.value if from_status is not None else None,
        "to_status": to_status.value,
        "occurred_at": to_utc_isoformat(occurred_at or utc_now_aware()),
    }
    counts = {
        "total": total,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "review_count": review_count,
        "timeout_count": timeout_count,
    }
    event.update({name: value for name, value in counts.items() if value is not None})
    return event


def publish_run_event_best_effort(
    stream: RedisRunEventStream,
    *,
    project_id: int,
    run_id: str,
    from_status: RunStatus | None,
    to_status: RunStatus,
    case_run_id: int | None = None,
    step_run_id: int | None = None,
    occurred_at: datetime | None = None,
    total: int | None = None,
    pass_count: int | None = None,
    fail_count: int | None = None,
    review_count: int | None = None,
    timeout_count: int | None = None,
    event_type: str | None = None,
) -> None:
    """Publish after the SQL commit without making Redis authoritative."""

    if from_status == to_status:
        return
    event = build_run_event(
        project_id=project_id,
        run_id=run_id,
        from_status=from_status,
        to_status=to_status,
        case_run_id=case_run_id,
        step_run_id=step_run_id,
        occurred_at=occurred_at,
        total=total,
        pass_count=pass_count,
        fail_count=fail_count,
        review_count=review_count,
        timeout_count=timeout_count,
        event_type=event_type,
    )
    try:
        stream.append_event(project_id, run_id, event)
    except RedisStoreUnavailableError:
        logger.warning(
            "run_event_stream_unavailable",
            extra={
                "event": "RUN_EVENT_STREAM_UNAVAILABLE",
                "project_id": project_id,
                "run_id": run_id,
                "event_type": event["event_type"],
                "to_status": event["to_status"],
            },
        )
