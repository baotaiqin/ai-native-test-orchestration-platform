"""Read-only readiness verification for the existing local Runner identity.

The script deliberately emits only non-sensitive control-plane facts. It loads the
DPAPI-protected identity to obtain the Runner ID, but never serializes credentials
or configuration URLs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT / "runner"))
sys.path.insert(0, str(WORKSPACE_ROOT / "backend"))

from app.infrastructure.db.session import SessionLocal
from app.infrastructure.redis.client import (
    get_redis_client,
    get_runner_heartbeat_store,
)
from app.modules.runners.models import Runner
from app.modules.runs.models import TestRun
from runner.config import default_state_dir
from runner.state import RunnerStateStore


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum-heartbeat-utc", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        minimum_heartbeat = datetime.fromisoformat(args.minimum_heartbeat_utc)
        # MySQL persists Runner heartbeats at DATETIME(0) precision. Compare at
        # the same precision so a valid initial heartbeat is not rejected only
        # because the Windows process start timestamp includes sub-seconds.
        minimum_heartbeat = _utc(minimum_heartbeat).replace(microsecond=0)
        identity = RunnerStateStore(default_state_dir()).load_identity()

        with SessionLocal() as session:
            runner = session.get(Runner, identity.runner_id)
            if runner is None:
                raise LookupError("runner")

            slots = {
                slot.slot_type: {
                    "total": slot.total,
                    "available": slot.available,
                }
                for slot in runner.slots
            }
            capabilities = {
                capability.capability: capability.status
                for capability in runner.capabilities
            }
            last_heartbeat = (
                _utc(runner.last_heartbeat_at)
                if runner.last_heartbeat_at is not None
                else None
            )

            redis_store = get_runner_heartbeat_store()
            heartbeat = redis_store.get_heartbeat(runner.id)
            redis_online = (
                isinstance(heartbeat, dict)
                and heartbeat.get("runner_id") == runner.id
                and heartbeat.get("status") == "ONLINE"
            )
            web_slot = slots.get("WEB", {"total": 0, "available": 0})
            active_run_count = session.scalar(
                select(func.count())
                .select_from(TestRun)
                .where(
                    TestRun.runner_id == runner.id,
                    TestRun.status.in_(
                        ("QUEUED", "ASSIGNED", "RUNNING", "CANCELLING")
                    ),
                )
            )
            active_run_count = int(active_run_count or 0)
            ready = all(
                (
                    runner.status == "ACTIVE",
                    redis_online,
                    last_heartbeat is not None,
                    last_heartbeat is not None
                    and last_heartbeat >= minimum_heartbeat,
                    capabilities.get("WEB") == "READY",
                    web_slot["total"] == 1,
                    0 <= web_slot["available"] <= web_slot["total"],
                    active_run_count == 0,
                )
            )
            result = {
                "ready": ready,
                "runner_id": runner.id,
                "status": runner.status,
                "online_status": "ONLINE" if redis_online else "OFFLINE",
                "redis_available": True,
                "last_heartbeat_at": (
                    last_heartbeat.isoformat() if last_heartbeat is not None else None
                ),
                "web_capability": capabilities.get("WEB"),
                "web_slots": web_slot,
                "active_run_count": active_run_count,
            }
    except Exception as exc:  # noqa: BLE001 - failures stay redacted by construction
        result = {"ready": False, "error_type": type(exc).__name__}
        ready = False
    finally:
        try:
            get_redis_client().close()
        except Exception as exc:  # noqa: BLE001 - close errors contain no readiness facts
            result["redis_close_warning"] = type(exc).__name__

    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
