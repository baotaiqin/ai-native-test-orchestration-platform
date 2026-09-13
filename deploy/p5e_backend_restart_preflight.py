"""Read-only database gate before restarting the P5-E-owned backend."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.infrastructure.db.session import SessionLocal
from app.modules.runs.models import TestRun
from app.modules.web_recordings.models import WebRecording


def _count(model: type, statuses: tuple[str, ...]) -> int:
    with SessionLocal() as session:
        value = session.scalar(
            select(func.count()).select_from(model).where(model.status.in_(statuses))
        )
        return int(value or 0)


def main() -> int:
    try:
        executing_runs = _count(TestRun, ("ASSIGNED", "RUNNING", "CANCELLING"))
        queued_runs = _count(TestRun, ("QUEUED",))
        active_recordings = _count(WebRecording, ("RUNNING", "STOP_REQUESTED"))
        ready = executing_runs == 0 and active_recordings == 0
        result = {
            "ok": True,
            "ready": ready,
            "executing_run_count": executing_runs,
            "queued_run_count": queued_runs,
            "active_web_recording_count": active_recordings,
        }
    except Exception as exc:  # noqa: BLE001 - never serialize database details
        result = {"ok": False, "ready": False, "error_type": type(exc).__name__}
        ready = False
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
