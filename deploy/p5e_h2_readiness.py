"""Create a bounded, secret-free readiness snapshot for P5E-H2/r1.

This verifier is read-only with respect to application services and the formal
database. It records only identifiers, counts, statuses, hashes, and public local
HTTP fingerprints; credentials and configuration URLs are never serialized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from sqlalchemy import func, select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.db.session import SessionLocal
from app.infrastructure.redis.client import (
    get_redis_client,
    get_runner_heartbeat_store,
)
from app.modules.prompt_center.models import AiCallLog
from app.modules.runners.models import Runner
from app.modules.runs.models import TestRun
from app.modules.web_healing.models import WebHealingProposal
from app.modules.web_recordings.models import WebRecording

EXPECTED_RUNNER_ID = "1282a04dfd894f85877384cb31af5c16"
FAILED_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
CALL_16_ID = 16
CALL_16_PROJECT_ID = 23
EXPECTED_MIGRATION = "20260909_0038"
EXPECTED_HASHES = {
    "backend/app/modules/web_healing/service.py": (
        "b17fa1d82f818f325850558e2621c280c57f4468c69348ce247bc5c12a0b183f"
    ),
    "backend/app/modules/web_healing/schemas.py": (
        "1c8a4d5b1a29fad37ea81a5861c363495247c0d50fc5db5d26187c01af069a11"
    ),
    "backend/tests/test_runs.py": (
        "06884bc572729657e541da1cacb90a14787256cab706bc6bf70f95c1412da8b6"
    ),
    "backend/tests/test_p5e_acceptance_resume.py": (
        "1b3c606a612105cd0837340014ca5079b6aa93aa2da2b2abe621d9add75ce591"
    ),
    "deploy/p5e_live_acceptance.py": (
        "4ec9bce6946cad14691ec4ae3fec10a769f07b5e64cf411800faac26719ec216"
    ),
    "runner/runner/isolation.py": (
        "8dd2184ed0b72f9196ab94e78ecd707e79954ee69879f84211ca81e3b7d336a2"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-confirmation-utc", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-services"
            / "h2-verification.json"
        ),
    )
    return parser.parse_args()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_utc(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise TypeError("http_json")
    return payload


def _get_text(url: str) -> str:
    with urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _service_by_name(ready: dict[str, Any], name: str) -> dict[str, Any]:
    service = next(
        (
            item
            for item in ready.get("services", [])
            if isinstance(item, dict) and item.get("name") == name
        ),
        None,
    )
    if not isinstance(service, dict):
        raise TypeError(f"service_{name}")
    return service


def _safe_call_16(call: AiCallLog | None) -> dict[str, Any]:
    if call is None:
        return {"exists": False}
    validation_errors = call.validation_errors
    return {
        "exists": True,
        "id": call.id,
        "project_id": call.project_id,
        "task_type": call.task_type,
        "success": call.success,
        "fallback_used": call.fallback_used,
        "retry_count": call.retry_count,
        "repair_used": call.repair_used,
        "error_type": call.error_type,
        "validation_error_count": (
            len(validation_errors) if isinstance(validation_errors, list) else None
        ),
    }


def main() -> int:
    args = _parse_args()
    result: dict[str, Any] = {
        "schema_version": 1,
        "package": "P5E-H2/r1",
        "generated_at": datetime.now(UTC).isoformat(),
        "start_confirmation_utc": args.start_confirmation_utc,
    }
    checks: dict[str, bool] = {}
    try:
        confirmation = _parse_utc(args.start_confirmation_utc)
        ready_path = (
            WORKSPACE_ROOT / ".codex-validation" / "p5e-services" / "ready.json"
        )
        ready = json.loads(ready_path.read_text(encoding="utf-8-sig"))
        if not isinstance(ready, dict):
            raise TypeError("ready_manifest")

        hashes: dict[str, dict[str, Any]] = {}
        for relative_path, expected in EXPECTED_HASHES.items():
            actual = _sha256(WORKSPACE_ROOT / relative_path)
            hashes[relative_path] = {
                "expected": expected,
                "actual": actual,
                "matches": actual == expected,
            }
        checks["all_source_hashes_match"] = all(
            item["matches"] for item in hashes.values()
        )
        result["source_hashes"] = hashes

        backend = _service_by_name(ready, "backend")
        demo = _service_by_name(ready, "demo")
        frontend = _service_by_name(ready, "frontend")
        runner_ready = ready.get("runner")
        if not isinstance(runner_ready, dict):
            raise TypeError("runner_ready")

        service_starts_after_confirmation = {
            name: _parse_utc(str(service["started_at"])) > confirmation
            for name, service in (
                ("backend", backend),
                ("demo", demo),
                ("frontend", frontend),
            )
        }
        runner_starts_after_confirmation = (
            _parse_utc(str(runner_ready["started_at"])) > confirmation
        )
        checks["backend_loaded_after_hash_confirmation"] = (
            service_starts_after_confirmation["backend"]
        )
        checks["runner_loaded_after_hash_confirmation"] = (
            runner_starts_after_confirmation
        )

        backend_health = _get_json("http://127.0.0.1:8000/health")
        demo_health = _get_json("http://127.0.0.1:8765/health")
        frontend_text = _get_text("http://127.0.0.1:5173")
        demo_state = _get_json("http://127.0.0.1:8765/control/state")
        openapi = _get_json("http://127.0.0.1:8000/openapi.json")
        candidate_schema = (
            openapi.get("components", {})
            .get("schemas", {})
            .get("WebHealingProposalResponse", {})
            .get("properties", {})
            .get("candidate_locators", {})
        )
        max_items = candidate_schema.get("maxItems")
        checks["backend_fingerprint"] = (
            backend_health.get("status") == "ok"
            and backend_health.get("service") == "backend"
        )
        checks["demo_fingerprint"] = (
            demo_health.get("status") == "ok"
            and demo_health.get("service") == "v1-demo"
        )
        checks["frontend_fingerprint"] = (
            "<title>AI 原生智能测试编排平台</title>" in frontend_text
        )
        checks["openapi_candidate_locators_max_items_360"] = max_items == 360
        checks["demo_changed_locator_false"] = (
            demo_state.get("changed_locator") is False
        )
        result["http"] = {
            "backend": {"healthy": checks["backend_fingerprint"], "pid": backend["pid"]},
            "frontend": {
                "healthy": checks["frontend_fingerprint"],
                "pid": frontend["pid"],
            },
            "demo": {
                "healthy": checks["demo_fingerprint"],
                "pid": demo["pid"],
                "changed_locator": demo_state.get("changed_locator"),
            },
            "openapi": {
                "schema": "WebHealingProposalResponse",
                "field": "candidate_locators",
                "maxItems": max_items,
            },
        }
        result["process_evidence"] = {
            "services": {
                service["name"]: {
                    "ownership": service.get("ownership"),
                    "listener_pid": service.get("pid"),
                    "listener_started_at": service.get("started_at"),
                    "process_tree": service.get("process_tree", []),
                    "started_after_hash_confirmation": service_starts_after_confirmation[
                        service["name"]
                    ],
                }
                for service in (backend, demo, frontend)
            },
            "runner": {
                "ownership": runner_ready.get("ownership"),
                "logical_worker_count": runner_ready.get("logical_worker_count"),
                "process_count": runner_ready.get("process_count"),
                "process_tree": runner_ready.get("process_tree", []),
                "started_after_hash_confirmation": runner_starts_after_confirmation,
            },
        }

        migration = subprocess.run(
            [str(WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "alembic", "current"],
            cwd=BACKEND_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        revision_match = re.search(r"([0-9_]+)\s+\(head\)", migration)
        migration_revision = revision_match.group(1) if revision_match else None
        checks["migration_at_expected_head"] = migration_revision == EXPECTED_MIGRATION

        with SessionLocal() as session:
            runner = session.get(Runner, EXPECTED_RUNNER_ID)
            if runner is None:
                raise LookupError("runner")
            heartbeat = get_runner_heartbeat_store().get_heartbeat(runner.id)
            web_capability = next(
                (
                    item.status
                    for item in runner.capabilities
                    if item.capability == "WEB"
                ),
                None,
            )
            web_slot = next(
                (item for item in runner.slots if item.slot_type == "WEB"), None
            )
            redis_online = (
                isinstance(heartbeat, dict)
                and heartbeat.get("runner_id") == runner.id
                and heartbeat.get("status") == "ONLINE"
            )
            active_run_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(TestRun)
                    .where(TestRun.status.in_(("ASSIGNED", "RUNNING", "CANCELLING")))
                )
                or 0
            )
            queued_run_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(TestRun)
                    .where(TestRun.status == "QUEUED")
                )
                or 0
            )
            active_recording_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WebRecording)
                    .where(WebRecording.status.in_(("RUNNING", "STOP_REQUESTED")))
                )
                or 0
            )
            failed_run = session.get(TestRun, FAILED_RUN_ID)
            call_16 = session.get(AiCallLog, CALL_16_ID)
            project_locator_call_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AiCallLog)
                    .where(
                        AiCallLog.project_id == CALL_16_PROJECT_ID,
                        AiCallLog.task_type == "LOCATOR_HEALING",
                    )
                )
                or 0
            )
            call_16_match_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AiCallLog)
                    .where(
                        AiCallLog.id == CALL_16_ID,
                        AiCallLog.project_id == CALL_16_PROJECT_ID,
                        AiCallLog.task_type == "LOCATOR_HEALING",
                    )
                )
                or 0
            )
            failed_run_proposal_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WebHealingProposal)
                    .where(WebHealingProposal.run_id == FAILED_RUN_ID)
                )
                or 0
            )
            runner_snapshot = {
                "runner_id": runner.id,
                "status": runner.status,
                "online_status": "ONLINE" if redis_online else "OFFLINE",
                "web_capability": web_capability,
                "web_slots": (
                    {"total": web_slot.total, "available": web_slot.available}
                    if web_slot is not None
                    else None
                ),
                "last_heartbeat_at": (
                    _utc(runner.last_heartbeat_at).isoformat()
                    if runner.last_heartbeat_at is not None
                    else None
                ),
            }
            failed_run_snapshot = {
                "id": FAILED_RUN_ID,
                "status": failed_run.status if failed_run is not None else None,
                "error_type": failed_run.error_type if failed_run is not None else None,
                "terminal": (
                    failed_run is not None
                    and failed_run.status
                    in {"SUCCESS", "FAILED", "CANCELLED", "TIMEOUT"}
                ),
                "proposal_count": failed_run_proposal_count,
            }
            call_16_snapshot = _safe_call_16(call_16)

        checks["runner_identity"] = runner_snapshot["runner_id"] == EXPECTED_RUNNER_ID
        checks["runner_active_online"] = (
            runner_snapshot["status"] == "ACTIVE"
            and runner_snapshot["online_status"] == "ONLINE"
        )
        checks["runner_web_ready_1_of_1"] = (
            runner_snapshot["web_capability"] == "READY"
            and runner_snapshot["web_slots"] == {"total": 1, "available": 1}
        )
        checks["one_logical_worker"] = (
            runner_ready.get("logical_worker_count") == 1
        )
        checks["no_executing_runs"] = active_run_count == 0
        checks["no_queued_runs"] = queued_run_count == 0
        checks["no_active_recordings"] = active_recording_count == 0
        checks["failed_run_terminal_failed"] = (
            failed_run_snapshot["status"] == "FAILED"
            and failed_run_snapshot["terminal"] is True
        )
        checks["failed_run_has_no_proposal"] = failed_run_proposal_count == 0
        checks["call_16_intact"] = call_16_snapshot == {
            "exists": True,
            "id": 16,
            "project_id": 23,
            "task_type": "LOCATOR_HEALING",
            "success": True,
            "fallback_used": False,
            "retry_count": 0,
            "repair_used": False,
            "error_type": None,
            "validation_error_count": 0,
        }
        checks["call_16_is_unique_match"] = call_16_match_count == 1
        result["database"] = {
            "engine": "mysql",
            "migration_revision": migration_revision,
            "at_expected_head": checks["migration_at_expected_head"],
            "executing_run_count": active_run_count,
            "queued_run_count": queued_run_count,
            "active_recording_count": active_recording_count,
            "failed_run": failed_run_snapshot,
            "call_16": call_16_snapshot,
            "call_16_match_count": call_16_match_count,
            "project_locator_call_count": project_locator_call_count,
        }
        result["runner"] = runner_snapshot

        ledger_path = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "ai-call-ledger.json"
        )
        ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
        ledger_calls = ledger.get("calls") if isinstance(ledger, dict) else None
        ledger_call_count = len(ledger_calls) if isinstance(ledger_calls, list) else None
        ledger_total = ledger.get("total_attempted") if isinstance(ledger, dict) else None
        checks["acceptance_ai_ledger_is_one"] = (
            ledger_total == 1 and ledger_call_count == 1
        )
        result["acceptance_ai_ledger"] = {
            "total_attempted": ledger_total,
            "call_count": ledger_call_count,
        }

        checks["owned_processes_are_recorded"] = all(
            service.get("ownership") == "p5e-owned"
            and bool(service.get("process_tree"))
            for service in (backend, demo, frontend)
        ) and (
            runner_ready.get("ownership") == "p5e-owned"
            and bool(runner_ready.get("process_tree"))
        )
        middleware = ready.get("middleware", {})
        checks["middleware_healthy"] = all(
            isinstance(middleware.get(name), dict)
            and middleware[name].get("healthy") is True
            for name in ("rabbitmq", "redis", "minio")
        )
        result["middleware"] = middleware
        result["checks"] = checks
        result["ready"] = all(checks.values())
    except Exception as exc:  # noqa: BLE001 - failure output is redacted by design
        result["ready"] = False
        result["error_type"] = type(exc).__name__
        result["checks"] = checks
    finally:
        try:
            get_redis_client().close()
        except Exception as exc:  # noqa: BLE001 - type only, no connection details
            result["redis_close_warning"] = type(exc).__name__

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
