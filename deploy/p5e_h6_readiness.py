"""Read-only, secret-free readiness verification for P5E-H6/r1."""

from __future__ import annotations

import argparse
import hashlib
import json
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
from app.modules.web_cases.models import WebCaseVersion, WebElementVersion
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_healing.models import WebHealingProposal
from app.modules.web_recordings.models import WebRecording

PACKAGE = "P5E-H6/r1"
RUNNER_ID = "1282a04dfd894f85877384cb31af5c16"
FAILED_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
HEALED_RUN_ID = "run_7852432fb7b241dabcad111664b9621b"
EXPECTED_RESULT_HASH = (
    "aa3a4a9f396d2df7e2beb6cae100da91de22160b6aecb251df2d4ce96ed999f8"
)
EXPECTED_LEDGER_HASH = (
    "b6e20d30a358293a58435f856230345de91e0edda2eccfe44dfd40ea6675ef65"
)
EXPECTED_SOURCE_HASHES = {
    "backend/app/modules/ai_gateway/service.py": (
        "4479107a11a8ba4b45198e3b845ac57b68f0cb587cc02ff8b46594415c835729"
    ),
    "backend/app/modules/web_failure_analysis/service.py": (
        "729f707e8a0d9efa3b8aab7e0802013a80c55e2d6bb67eed9ced4cd807833453"
    ),
    "deploy/p5e_resume_failure_analysis.py": (
        "9b387663be3b888e85e4cd7fdb1c446043c3545e5dd1407ba7808f5b6706187a"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("preflight", "post"), required=True)
    parser.add_argument("--confirmation-utc")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError("http_json")
    return value


def _get_text(url: str) -> str:
    with urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_utc(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def main() -> int:
    args = _parse_args()
    output = args.output or (
        WORKSPACE_ROOT
        / ".codex-validation"
        / "p5e-services"
        / ("h6-preflight.json" if args.phase == "preflight" else "h6-verification.json")
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "package": PACKAGE,
        "phase": args.phase,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "mode": "READ_ONLY",
    }
    checks: dict[str, bool] = {}
    try:
        hashes: dict[str, dict[str, Any]] = {}
        for relative, expected in EXPECTED_SOURCE_HASHES.items():
            actual = _sha256(WORKSPACE_ROOT / relative)
            hashes[relative] = {
                "expected": expected,
                "actual": actual,
                "matches": actual == expected,
            }
        checks["h5_source_hashes_match"] = all(
            item["matches"] for item in hashes.values()
        )
        result["source_hashes"] = hashes

        result_file = WORKSPACE_ROOT / ".codex-validation" / "p5e-live" / "result.json"
        attempt_file = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "attempts"
            / "V1P5E_20260909_F294AC1E.json"
        )
        ledger_file = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "ai-call-ledger.json"
        )
        receipt_file = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "call-19-failure-analysis-recovery.json"
        )
        checkpoint_result = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "checkpoints"
            / "V1P5E_20260909_F294AC1E-after-call-19.json"
        )
        checkpoint_ledger = (
            WORKSPACE_ROOT
            / ".codex-validation"
            / "p5e-live"
            / "checkpoints"
            / "V1P5E_20260909_F294AC1E-ledger-before-call-20.json"
        )
        manifest = json.loads(result_file.read_text(encoding="utf-8-sig"))
        attempt = json.loads(attempt_file.read_text(encoding="utf-8-sig"))
        ledger = json.loads(ledger_file.read_text(encoding="utf-8-sig"))
        result_hash = _sha256(result_file)
        attempt_hash = _sha256(attempt_file)
        ledger_hash = _sha256(ledger_file)
        ledger_calls = ledger.get("calls") if isinstance(ledger, dict) else None
        checks["manifest_hash_unchanged"] = (
            result_hash == EXPECTED_RESULT_HASH
            and attempt_hash == EXPECTED_RESULT_HASH
            and result_file.read_bytes() == attempt_file.read_bytes()
        )
        checks["ledger_hash_and_count_unchanged"] = (
            ledger_hash == EXPECTED_LEDGER_HASH
            and ledger.get("total_attempted") == 4
            and isinstance(ledger_calls, list)
            and len(ledger_calls) == 4
        )
        checks["manifest_h3_checkpoint"] = (
            manifest.get("status") == "FAILED"
            and manifest.get("stage") == "generate_failure_analysis"
            and attempt == manifest
        )
        checks["no_failure_analysis_recovery_artifacts"] = not any(
            path.exists() for path in (receipt_file, checkpoint_result, checkpoint_ledger)
        )
        result["formal_checkpoint"] = {
            "acceptance_id": "V1P5E_20260909_F294AC1E",
            "result_sha256": result_hash,
            "attempt_sha256": attempt_hash,
            "result_attempt_byte_equal": result_file.read_bytes()
            == attempt_file.read_bytes(),
            "ledger_sha256": ledger_hash,
            "ledger_total_attempted": ledger.get("total_attempted"),
            "ledger_call_count": len(ledger_calls) if isinstance(ledger_calls, list) else None,
            "status": manifest.get("status"),
            "stage": manifest.get("stage"),
            "recovery_receipt_exists": receipt_file.exists(),
            "recovery_checkpoint_exists": checkpoint_result.exists()
            or checkpoint_ledger.exists(),
        }

        backend_health = _get_json("http://127.0.0.1:8000/health")
        demo_health = _get_json("http://127.0.0.1:8765/health")
        demo_state = _get_json("http://127.0.0.1:8765/control/state")
        frontend = _get_text("http://127.0.0.1:5173")
        checks["backend_health"] = (
            backend_health.get("status") == "ok"
            and backend_health.get("service") == "backend"
        )
        checks["frontend_health"] = (
            "<title>AI 原生智能测试编排平台</title>" in frontend
        )
        checks["demo_health_and_state_true"] = (
            demo_health.get("status") == "ok"
            and demo_health.get("service") == "v1-demo"
            and demo_state.get("changed_locator") is True
        )
        result["http"] = {
            "backend": {"healthy": checks["backend_health"]},
            "frontend": {"healthy": checks["frontend_health"]},
            "demo": {
                "healthy": demo_health.get("status") == "ok"
                and demo_health.get("service") == "v1-demo",
                "changed_locator": demo_state.get("changed_locator"),
            },
        }

        with SessionLocal() as session:
            in_flight_runs = int(
                session.scalar(
                    select(func.count())
                    .select_from(TestRun)
                    .where(
                        TestRun.status.in_(
                            ("QUEUED", "ASSIGNED", "RUNNING", "CANCELLING")
                        )
                    )
                )
                or 0
            )
            in_flight_recordings = int(
                session.scalar(
                    select(func.count())
                    .select_from(WebRecording)
                    .where(
                        WebRecording.status.in_(
                            ("QUEUED", "RUNNING", "STOP_REQUESTED")
                        )
                    )
                )
                or 0
            )
            failed_run = session.get(TestRun, FAILED_RUN_ID)
            healed_run = session.get(TestRun, HEALED_RUN_ID)
            rejected = session.get(WebHealingProposal, 2)
            accepted = session.get(WebHealingProposal, 3)
            healed_element = session.get(WebElementVersion, 3)
            healed_case = session.get(WebCaseVersion, 7)
            call_19 = session.get(AiCallLog, 19)
            failure_analysis_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WebFailureAnalysis)
                    .where(WebFailureAnalysis.run_id == FAILED_RUN_ID)
                )
                or 0
            )
            runner = session.get(Runner, RUNNER_ID)
            if runner is None:
                raise LookupError("runner")
            heartbeat = get_runner_heartbeat_store().get_heartbeat(RUNNER_ID)
            redis_online = (
                isinstance(heartbeat, dict)
                and heartbeat.get("runner_id") == RUNNER_ID
                and heartbeat.get("status") == "ONLINE"
            )
            web_capability = next(
                (
                    capability.status
                    for capability in runner.capabilities
                    if capability.capability == "WEB"
                ),
                None,
            )
            web_slot = next(
                (slot for slot in runner.slots if slot.slot_type == "WEB"), None
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
            database = {
                "in_flight_run_count": in_flight_runs,
                "in_flight_recording_count": in_flight_recordings,
                "failed_run": {
                    "id": FAILED_RUN_ID,
                    "status": failed_run.status if failed_run is not None else None,
                },
                "healed_run": {
                    "id": HEALED_RUN_ID,
                    "status": healed_run.status if healed_run is not None else None,
                },
                "proposals": {
                    "2": {
                        "status": rejected.status if rejected is not None else None,
                    },
                    "3": {
                        "status": accepted.status if accepted is not None else None,
                        "created_element_version_id": (
                            accepted.created_element_version_id
                            if accepted is not None
                            else None
                        ),
                        "created_web_case_version_id": (
                            accepted.created_web_case_version_id
                            if accepted is not None
                            else None
                        ),
                    },
                },
                "healed_element_version": {
                    "id": healed_element.id if healed_element is not None else None,
                    "version_no": (
                        healed_element.version_no if healed_element is not None else None
                    ),
                },
                "healed_web_case_version": {
                    "id": healed_case.id if healed_case is not None else None,
                    "status": healed_case.status if healed_case is not None else None,
                },
                "failure_analysis_count": failure_analysis_count,
                "call_19": {
                    "exists": call_19 is not None,
                    "success": call_19.success if call_19 is not None else None,
                    "repair_used": call_19.repair_used if call_19 is not None else None,
                    "entity_sha256": call_19.entity_id if call_19 is not None else None,
                },
            }

        checks["no_in_flight_runs"] = in_flight_runs == 0
        checks["no_in_flight_recordings"] = in_flight_recordings == 0
        checks["fixed_runs_unchanged"] = (
            database["failed_run"]["status"] == "FAILED"
            and database["healed_run"]["status"] == "SUCCESS"
        )
        checks["proposal_and_versions_unchanged"] = (
            database["proposals"]["2"]["status"] == "REJECTED"
            and database["proposals"]["3"]["status"] == "ACCEPTED"
            and database["proposals"]["3"]["created_element_version_id"] == 3
            and database["proposals"]["3"]["created_web_case_version_id"] == 7
            and database["healed_element_version"]["id"] == 3
            and database["healed_web_case_version"]["id"] == 7
            and database["healed_web_case_version"]["status"] == "APPROVED"
        )
        checks["failure_analysis_still_absent"] = failure_analysis_count == 0
        checks["call_19_unchanged"] = database["call_19"] == {
            "exists": True,
            "success": True,
            "repair_used": False,
            "entity_sha256": (
                "a7a7815b0548190b4768febb53c5e2139c35e158cb83e465037b5930f177f486"
            ),
        }
        checks["runner_active_online_web_1_of_1"] = (
            runner_snapshot["status"] == "ACTIVE"
            and runner_snapshot["online_status"] == "ONLINE"
            and runner_snapshot["web_capability"] == "READY"
            and runner_snapshot["web_slots"] == {"total": 1, "available": 1}
        )
        result["database"] = database
        result["runner"] = runner_snapshot

        if args.phase == "post":
            if not args.confirmation_utc:
                raise ValueError("confirmation_utc")
            ready_file = (
                WORKSPACE_ROOT
                / ".codex-validation"
                / "p5e-services"
                / "ready.json"
            )
            ready = json.loads(ready_file.read_text(encoding="utf-8-sig"))
            owned_file = (
                WORKSPACE_ROOT
                / ".codex-validation"
                / "p5e-services"
                / "owned-processes.json"
            )
            reload_file = (
                WORKSPACE_ROOT
                / ".codex-validation"
                / "p5e-services"
                / "h6-backend-reload.json"
            )
            reload_record = json.loads(reload_file.read_text(encoding="utf-8-sig"))
            history = reload_record.get("history", {})
            ready_history_file = ready_file.parent / history.get("ready_file", "")
            owned_history_file = ready_file.parent / history.get("owned_file", "")
            ready_history = json.loads(
                ready_history_file.read_text(encoding="utf-8-sig")
            )
            owned_history = json.loads(
                owned_history_file.read_text(encoding="utf-8-sig")
            )
            current_owned = json.loads(owned_file.read_text(encoding="utf-8-sig"))
            backend = next(
                item
                for item in ready.get("services", [])
                if isinstance(item, dict) and item.get("name") == "backend"
            )
            confirmation = _parse_utc(args.confirmation_utc)
            checks["backend_started_after_h5_confirmation"] = (
                _parse_utc(str(backend.get("started_at"))) > confirmation
            )
            checks["backend_h6_owned_tree_recorded"] = (
                backend.get("ownership") == "p5e-owned"
                and backend.get("reload_package") == PACKAGE
                and bool(backend.get("process_tree"))
            )
            current_non_backend_services = sorted(
                (
                    item
                    for item in ready.get("services", [])
                    if isinstance(item, dict) and item.get("name") != "backend"
                ),
                key=lambda item: str(item.get("name")),
            )
            history_non_backend_services = sorted(
                (
                    item
                    for item in ready_history.get("services", [])
                    if isinstance(item, dict) and item.get("name") != "backend"
                ),
                key=lambda item: str(item.get("name")),
            )
            current_non_backend_owned = sorted(
                (
                    item
                    for item in current_owned
                    if isinstance(item, dict) and item.get("purpose") != "backend"
                ),
                key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
            )
            history_non_backend_owned = sorted(
                (
                    item
                    for item in owned_history
                    if isinstance(item, dict) and item.get("purpose") != "backend"
                ),
                key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
            )
            checks["h2_history_copies_match_original_hashes"] = (
                _sha256(ready_history_file) == history.get("ready_sha256")
                and _sha256(owned_history_file) == history.get("owned_sha256")
            )
            checks["non_backend_ready_state_unchanged"] = (
                current_non_backend_services == history_non_backend_services
                and ready.get("runner") == ready_history.get("runner")
                and ready.get("middleware") == ready_history.get("middleware")
                and ready.get("database") == ready_history.get("database")
            )
            checks["non_backend_owned_state_unchanged"] = (
                current_non_backend_owned == history_non_backend_owned
            )
            safety = reload_record.get("safety", {})
            checks["reload_scope_was_backend_only"] = (
                safety.get("restarted_other_services") is False
                and safety.get("business_requests") == 0
                and safety.get("ai_requests") == 0
                and safety.get("message_operations") == 0
                and safety.get("demo_state_changes") == 0
            )
            result["backend_runtime"] = {
                "listener_pid": backend.get("pid"),
                "started_at": backend.get("started_at"),
                "ownership": backend.get("ownership"),
                "working_directory": backend.get("working_directory"),
                "command_identity": backend.get("command_identity"),
                "process_tree": backend.get("process_tree"),
                "started_after_h5_confirmation": checks[
                    "backend_started_after_h5_confirmation"
                ],
            }
            result["reload_isolation"] = {
                "ready_history_file": ready_history_file.name,
                "ready_history_sha256": _sha256(ready_history_file),
                "owned_history_file": owned_history_file.name,
                "owned_history_sha256": _sha256(owned_history_file),
                "non_backend_ready_state_unchanged": checks[
                    "non_backend_ready_state_unchanged"
                ],
                "non_backend_owned_state_unchanged": checks[
                    "non_backend_owned_state_unchanged"
                ],
                "safety": safety,
            }

        result["checks"] = checks
        result["ready"] = all(checks.values())
    except Exception as exc:  # noqa: BLE001 - type-only failure is secret-free
        result["checks"] = checks
        result["ready"] = False
        result["error_type"] = type(exc).__name__
    finally:
        try:
            get_redis_client().close()
        except Exception as exc:  # noqa: BLE001 - type only
            result["redis_close_warning"] = type(exc).__name__

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
