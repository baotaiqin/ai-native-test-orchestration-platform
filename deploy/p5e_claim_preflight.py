"""Read-only verification of the backend preconditions for a Runner claim."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT / "runner"))
sys.path.insert(0, str(WORKSPACE_ROOT / "backend"))

from app.infrastructure.db.session import SessionLocal
from app.modules.runners.models import Runner
from app.modules.runners.security import matches_digest
from app.modules.runs.models import RunDispatchOutbox, TestRun
from app.modules.runs.service import _validate_dispatch_payload
from runner.config import default_state_dir
from runner.state import RunnerStateStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        identity = RunnerStateStore(default_state_dir()).load_identity()
        with SessionLocal() as session:
            run = session.get(TestRun, args.run_id)
            outbox = session.scalar(
                select(RunDispatchOutbox).where(RunDispatchOutbox.run_id == args.run_id)
            )
            runner = session.get(Runner, identity.runner_id)
            if run is None or outbox is None or runner is None:
                raise LookupError("control-plane record")

            payload = _validate_dispatch_payload(run, outbox)
            checks = {
                "runner_id_matches": run.runner_id == identity.runner_id,
                "credential_digest_matches": matches_digest(
                    identity.credential,
                    runner.credential_digest,
                ),
                "run_status_queued": run.status == "QUEUED",
                "outbox_status_published": outbox.status == "PUBLISHED",
                "outbox_unclaimed": outbox.claimed_runner_id is None
                and outbox.claimed_at is None,
                "dispatch_payload_valid": payload is not None,
                "message_id_matches_payload": payload is not None
                and payload.message_id == outbox.message_id,
            }
            ready = all(checks.values())
            result = {
                "ok": True,
                "run_id": run.id,
                "run_type": run.run_type,
                "required_slot_type": run.required_slot_type,
                "run_status": run.status,
                "outbox_status": outbox.status,
                "attempt_count": outbox.attempt_count,
                "claim_preconditions_ready": ready,
                "checks": checks,
            }
    except Exception as exc:  # noqa: BLE001 - never serialize the exception message
        result = {"ok": False, "error_type": type(exc).__name__}
        ready = False

    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
