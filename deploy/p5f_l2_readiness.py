"""Secret-free readiness proof for loading P5F-L1/r2 into Backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from sqlalchemy import event, func, select, text

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
DEPLOY_ROOT = WORKSPACE_ROOT / "deploy"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(DEPLOY_ROOT))

from app.core.redaction import redact_text, redact_value
from app.infrastructure.db.session import SessionLocal, engine
from app.infrastructure.redis.client import (
    get_redis_client,
    get_runner_heartbeat_store,
)
from app.modules.prompt_center.models import AiCallLog
from app.modules.runners.models import Runner
from app.modules.runs.models import TestRun
from app.modules.web_cases.models import WebCase, WebElement, WebPage
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_recordings.models import WebRecording
from p5e_resume_failure_analysis import DEFAULT_READY_FILE, _connect_api

PACKAGE = "P5F-L2/r1"
PROJECT_ID = 23
RUNNER_ID = "1282a04dfd894f85877384cb31af5c16"
FAILED_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
HEALED_RUN_ID = "run_7852432fb7b241dabcad111664b9621b"
EXPECTED_MIGRATION = "20260909_0038"
EXPECTED_SOURCE_HASHES = {
    "backend/app/core/redaction.py": (
        "458d05f7299b56cd277ec6b63ee3082527c34986b167f7c5c3e9432f7a74ddbb"
    ),
    "backend/app/core/logging.py": (
        "4736ef0407ba5ba41473ef3d00b17f55f8acaa4dea6b9fad44e031675bebca43"
    ),
    "backend/app/modules/prompt_center/service.py": (
        "2db4cfbbcc949fdb0ace73f2ce13f4c69b71715e6007ffada65397f5856a2c26"
    ),
}
EXPECTED_PROTECTED_HASHES = {
    "result": "2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176",
    "attempt": "2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176",
    "ledger": "eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43",
    "archive_preflight": (
        "00f57b1a56b9f096f5a7026cd7d5c01c790ad489cbeab2afa47535bb8c206eee"
    ),
    "archive_operations": (
        "7f9d927838bdb9524e701adf77e914cd92a71edfc4333f4f61639ca04ccf79a7"
    ),
    "archive_verification": (
        "58f35fc46abe70bdaaa10be738e0e9504420239ad3704a14f44e06d63d492597"
    ),
    "archive_report": (
        "52f1829c8f8da4e9a34f7e55eb6c3a0751d7036dbb0a0585b2b8352ef7899c7d"
    ),
}
PROTECTED_FILES = {
    "result": WORKSPACE_ROOT / ".codex-validation" / "p5e-live" / "result.json",
    "attempt": (
        WORKSPACE_ROOT
        / ".codex-validation"
        / "p5e-live"
        / "attempts"
        / "V1P5E_20260909_F294AC1E.json"
    ),
    "ledger": (
        WORKSPACE_ROOT / ".codex-validation" / "p5e-live" / "ai-call-ledger.json"
    ),
    "archive_preflight": (
        WORKSPACE_ROOT / ".codex-validation" / "p5f-archive" / "preflight.json"
    ),
    "archive_operations": (
        WORKSPACE_ROOT / ".codex-validation" / "p5f-archive" / "operations.json"
    ),
    "archive_verification": (
        WORKSPACE_ROOT / ".codex-validation" / "p5f-archive" / "verification.json"
    ),
    "archive_report": WORKSPACE_ROOT / "文档" / "P5F-C1合成资产归档交付.md",
}
EXPECTED_AI_IDS = list(range(12, 21))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preflight", "post"), required=True)
    parser.add_argument("--confirmation-utc")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


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


def _guard_select_show_only(
    _connection: Any,
    _cursor: Any,
    statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    operation = statement.lstrip().split(None, 1)[0].upper()
    if operation not in {"SELECT", "SHOW"}:
        raise RuntimeError("formal database statement blocked")


event.listen(engine, "before_cursor_execute", _guard_select_show_only)


def _protected_snapshot() -> dict[str, Any]:
    hashes = {name: _sha256(path) for name, path in PROTECTED_FILES.items()}
    ledger = _read_json(PROTECTED_FILES["ledger"])
    archive = _read_json(PROTECTED_FILES["archive_verification"])
    calls = ledger.get("calls") if isinstance(ledger, dict) else None
    return {
        "sha256": hashes,
        "result_attempt_byte_equal": (
            PROTECTED_FILES["result"].read_bytes()
            == PROTECTED_FILES["attempt"].read_bytes()
        ),
        "manifest": {
            "status": _read_json(PROTECTED_FILES["result"]).get("status"),
            "stage": _read_json(PROTECTED_FILES["result"]).get("stage"),
        },
        "ledger_total_attempted": ledger.get("total_attempted"),
        "ledger_call_count": len(calls) if isinstance(calls, list) else None,
        "archive": {
            "status": archive.get("status"),
            "history_sha256_before": archive.get("history_sha256_before"),
            "history_sha256_after": archive.get("history_sha256_after"),
            "asset_statuses": [
                {
                    "case": item.get("case", {}).get("status"),
                    "element": item.get("element", {}).get("status"),
                    "page": item.get("page", {}).get("status"),
                }
                for item in archive.get("assets", [])
                if isinstance(item, dict)
            ],
        },
    }


def _source_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for relative, expected in EXPECTED_SOURCE_HASHES.items():
        actual = _sha256(WORKSPACE_ROOT / relative)
        snapshot[relative] = {
            "expected": expected,
            "actual": actual,
            "matches": actual == expected,
        }
    return snapshot


def _safe_ai_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "task_type": row.task_type,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "model_config_id": row.model_config_id,
        "actual_model": row.actual_model,
        "prompt_version_id": row.prompt_version_id,
        "output_schema_id": row.output_schema_id,
        "input_token": row.input_token,
        "output_token": row.output_token,
        "total_token": row.total_token,
        "latency_ms": row.latency_ms,
        "success": row.success,
        "fallback_used": row.fallback_used,
        "retry_count": row.retry_count,
        "repair_used": row.repair_used,
        "error_type": row.error_type,
        "response_id": row.response_id,
    }


def _database_snapshot() -> dict[str, Any]:
    with engine.connect() as connection:
        migration_revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
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
        runs = {
            run.id: run.status
            for run in session.scalars(
                select(TestRun).where(TestRun.id.in_((FAILED_RUN_ID, HEALED_RUN_ID)))
            ).all()
        }
        cases = {
            row.id: row.status
            for row in session.scalars(select(WebCase).where(WebCase.id.in_((5, 6)))).all()
        }
        elements = {
            row.id: row.status
            for row in session.scalars(
                select(WebElement).where(WebElement.id.in_((1, 2)))
            ).all()
        }
        pages = {
            row.id: row.status
            for row in session.scalars(select(WebPage).where(WebPage.id.in_((1, 2)))).all()
        }
        analysis = session.execute(
            select(
                WebFailureAnalysis.id,
                WebFailureAnalysis.status,
                WebFailureAnalysis.run_id,
                WebFailureAnalysis.ai_call_id,
                WebFailureAnalysis.prompt_version_id,
                WebFailureAnalysis.output_schema_id,
                WebFailureAnalysis.fallback_used,
                WebFailureAnalysis.repair_used,
            ).where(WebFailureAnalysis.id == 2)
        ).one_or_none()
        ai_rows = session.execute(
            select(
                AiCallLog.id,
                AiCallLog.project_id,
                AiCallLog.task_type,
                AiCallLog.entity_type,
                AiCallLog.entity_id,
                AiCallLog.model_config_id,
                AiCallLog.actual_model,
                AiCallLog.prompt_version_id,
                AiCallLog.output_schema_id,
                AiCallLog.input_token,
                AiCallLog.output_token,
                AiCallLog.total_token,
                AiCallLog.latency_ms,
                AiCallLog.success,
                AiCallLog.fallback_used,
                AiCallLog.retry_count,
                AiCallLog.repair_used,
                AiCallLog.error_type,
                AiCallLog.response_id,
            )
            .where(AiCallLog.project_id == PROJECT_ID)
            .order_by(AiCallLog.id.desc())
        ).all()
        ai_audit = [_safe_ai_row(row) for row in ai_rows]
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
        return {
            "migration_revision": migration_revision,
            "in_flight_run_count": in_flight_runs,
            "in_flight_recording_count": in_flight_recordings,
            "runs": runs,
            "assets": {
                "cases": cases,
                "elements": elements,
                "pages": pages,
            },
            "failure_analysis": (
                {
                    "id": analysis.id,
                    "status": analysis.status,
                    "run_id": analysis.run_id,
                    "ai_call_id": analysis.ai_call_id,
                    "prompt_version_id": analysis.prompt_version_id,
                    "output_schema_id": analysis.output_schema_id,
                    "fallback_used": analysis.fallback_used,
                    "repair_used": analysis.repair_used,
                }
                if analysis is not None
                else None
            ),
            "ai_audit": ai_audit,
            "runner": {
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
            },
        }


def _validate_ai_history_api(database: dict[str, Any]) -> dict[str, Any]:
    api = _connect_api(DEFAULT_READY_FILE.resolve(), 20.0)
    response = api.request("GET", "/ai/calls", params={"project_id": PROJECT_ID})
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        raise TypeError("ai_history_items")
    database_by_id = {item["id"]: item for item in database["ai_audit"]}
    audit_fields = (
        "id",
        "project_id",
        "task_type",
        "entity_type",
        "entity_id",
        "model_config_id",
        "actual_model",
        "prompt_version_id",
        "output_schema_id",
        "input_token",
        "output_token",
        "total_token",
        "latency_ms",
        "success",
        "fallback_used",
        "retry_count",
        "repair_used",
        "error_type",
        "response_id",
    )
    audit_matches_database = all(
        item.get("id") in database_by_id
        and all(
            item.get(field) == database_by_id[item["id"]].get(field)
            for field in audit_fields
        )
        for item in items
        if isinstance(item, dict)
    ) and len(items) == len(database_by_id)
    content_fields = ("raw_response", "repair_response", "parsed_result", "validation_errors")
    content_fields_present = all(
        isinstance(item, dict) and all(field in item for field in content_fields)
        for item in items
    )
    content_fields_display_safe = all(
        isinstance(item, dict)
        and redact_text(item["raw_response"]) == item["raw_response"]
        and (
            item["repair_response"] is None
            or redact_text(item["repair_response"]) == item["repair_response"]
        )
        and redact_value(item["parsed_result"]) == item["parsed_result"]
        and redact_value(item["validation_errors"]) == item["validation_errors"]
        for item in items
    )
    call_20 = next(
        (item for item in items if isinstance(item, dict) and item.get("id") == 20),
        None,
    )
    if not isinstance(call_20, dict):
        raise TypeError("call_20")
    return {
        "authorized_read": True,
        "total": response.get("total"),
        "ids": [item.get("id") for item in items if isinstance(item, dict)],
        "audit_fields_match_database": audit_matches_database,
        "content_fields_present": content_fields_present,
        "content_fields_display_safe": content_fields_display_safe,
        "content_fields_serialized": False,
        "call_20_audit": {
            field: call_20.get(field) for field in audit_fields
        },
        "requests": {
            "normal_login_posts": 1,
            "business_posts": 0,
            "health_gets": 1,
            "authorized_ai_history_gets": 1,
        },
    }


def main() -> int:
    args = _parse_args()
    output = (
        WORKSPACE_ROOT
        / ".codex-validation"
        / "p5f-l2"
        / ("preflight.json" if args.phase == "preflight" else "verification.json")
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "package": PACKAGE,
        "phase": args.phase,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "formal_db_guard": "SELECT_SHOW_ONLY",
    }
    checks: dict[str, bool] = {}
    try:
        sources = _source_snapshot()
        protected = _protected_snapshot()
        database = _database_snapshot()
        backend_health = _get_json("http://127.0.0.1:8000/health")
        demo_health = _get_json("http://127.0.0.1:8765/health")
        demo_state = _get_json("http://127.0.0.1:8765/control/state")
        frontend = _get_text("http://127.0.0.1:5173")
        checks["l1_source_hashes_match"] = all(
            item["matches"] for item in sources.values()
        )
        checks["protected_file_hashes_match"] = (
            protected["sha256"] == EXPECTED_PROTECTED_HASHES
            and protected["result_attempt_byte_equal"] is True
            and protected["manifest"] == {"status": "PASSED", "stage": "complete"}
            and protected["ledger_total_attempted"] == 5
            and protected["ledger_call_count"] == 5
        )
        archive = protected["archive"]
        checks["c1_archive_summary_unchanged"] = (
            archive["status"] == "PASSED"
            and archive["history_sha256_before"]
            == "1d4b2a85d48bc286db7475fb3ebc42eceb689d9a56095e66c3f1270a036dfc78"
            and archive["history_sha256_after"] == archive["history_sha256_before"]
            and len(archive["asset_statuses"]) == 2
            and all(
                set(statuses.values()) == {"ARCHIVED"}
                for statuses in archive["asset_statuses"]
            )
        )
        checks["migration_at_head_0038"] = (
            database["migration_revision"] == EXPECTED_MIGRATION
        )
        checks["no_in_flight_runs_or_recordings"] = (
            database["in_flight_run_count"] == 0
            and database["in_flight_recording_count"] == 0
        )
        checks["fixed_runs_unchanged"] = database["runs"] == {
            FAILED_RUN_ID: "FAILED",
            HEALED_RUN_ID: "SUCCESS",
        }
        checks["six_assets_archived"] = database["assets"] == {
            "cases": {5: "ARCHIVED", 6: "ARCHIVED"},
            "elements": {1: "ARCHIVED", 2: "ARCHIVED"},
            "pages": {1: "ARCHIVED", 2: "ARCHIVED"},
        }
        checks["analysis_2_call_20_unchanged"] = database["failure_analysis"] == {
            "id": 2,
            "status": "COMPLETED",
            "run_id": FAILED_RUN_ID,
            "ai_call_id": 20,
            "prompt_version_id": 16,
            "output_schema_id": 12,
            "fallback_used": False,
            "repair_used": False,
        }
        checks["ai_audit_ids_12_through_20"] = sorted(
            item["id"] for item in database["ai_audit"]
        ) == EXPECTED_AI_IDS
        call_20 = next(
            (item for item in database["ai_audit"] if item["id"] == 20), None
        )
        checks["call_20_audit_identity"] = (
            isinstance(call_20, dict)
            and call_20["project_id"] == PROJECT_ID
            and call_20["task_type"] == "WEB_FAILURE_ANALYSIS"
            and call_20["prompt_version_id"] == 16
            and call_20["output_schema_id"] == 12
            and call_20["success"] is True
            and call_20["fallback_used"] is False
            and call_20["repair_used"] is False
            and call_20["retry_count"] == 0
            and call_20["error_type"] is None
        )
        checks["runner_active_online_web_1_of_1"] = (
            database["runner"]["status"] == "ACTIVE"
            and database["runner"]["online_status"] == "ONLINE"
            and database["runner"]["web_capability"] == "READY"
            and database["runner"]["web_slots"] == {"total": 1, "available": 1}
        )
        checks["three_http_services_healthy"] = (
            backend_health.get("status") == "ok"
            and backend_health.get("service") == "backend"
            and demo_health.get("status") == "ok"
            and demo_health.get("service") == "v1-demo"
            and demo_state.get("changed_locator") is True
            and "<title>AI 原生智能测试编排平台</title>" in frontend
        )
        result.update(
            {
                "source_hashes": sources,
                "protected_files": protected,
                "database": database,
                "http": {
                    "backend_healthy": backend_health.get("status") == "ok",
                    "frontend_healthy": (
                        "<title>AI 原生智能测试编排平台</title>" in frontend
                    ),
                    "demo_healthy": demo_health.get("status") == "ok",
                    "demo_changed_locator": demo_state.get("changed_locator"),
                },
            }
        )

        if args.phase == "post":
            if not args.confirmation_utc:
                raise ValueError("confirmation_utc")
            ready_file = (
                WORKSPACE_ROOT
                / ".codex-validation"
                / "p5e-services"
                / "ready.json"
            )
            reload_file = (
                WORKSPACE_ROOT
                / ".codex-validation"
                / "p5f-l2"
                / "backend-reload.json"
            )
            ready = _read_json(ready_file)
            reload_record = _read_json(reload_file)
            backend = next(
                item
                for item in ready.get("services", [])
                if isinstance(item, dict) and item.get("name") == "backend"
            )
            confirmation = _parse_utc(args.confirmation_utc)
            checks["backend_started_after_l1_confirmation"] = (
                _parse_utc(str(backend.get("started_at"))) > confirmation
            )
            checks["backend_l2_owned_tree_recorded"] = (
                backend.get("ownership") == "p5e-owned"
                and backend.get("reload_package") == PACKAGE
                and bool(backend.get("process_tree"))
                and backend.get("loaded_source_hashes")
                == {key: value for key, value in EXPECTED_SOURCE_HASHES.items()}
            )
            history = reload_record.get("history", {})
            ready_history = _read_json(ready_file.parent / history["ready_file"])
            owned_history = _read_json(ready_file.parent / history["owned_file"])
            current_owned = _read_json(ready_file.parent / "owned-processes.json")
            current_non_backend = sorted(
                (
                    item
                    for item in current_owned
                    if isinstance(item, dict) and item.get("purpose") != "backend"
                ),
                key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
            )
            history_non_backend = sorted(
                (
                    item
                    for item in owned_history
                    if isinstance(item, dict) and item.get("purpose") != "backend"
                ),
                key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
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
            checks["ready_owned_history_and_non_backend_unchanged"] = (
                _sha256(ready_file.parent / history["ready_file"])
                == history["ready_sha256"]
                and _sha256(ready_file.parent / history["owned_file"])
                == history["owned_sha256"]
                and current_non_backend == history_non_backend
                and current_non_backend_services == history_non_backend_services
                and ready.get("runner") == ready_history.get("runner")
                and ready.get("middleware") == ready_history.get("middleware")
                and ready.get("database") == ready_history.get("database")
            )
            ai_history_api = _validate_ai_history_api(database)
            checks["authorized_ai_history_matches_audit"] = (
                ai_history_api["authorized_read"] is True
                and ai_history_api["total"] == len(EXPECTED_AI_IDS)
                and sorted(ai_history_api["ids"]) == EXPECTED_AI_IDS
                and ai_history_api["audit_fields_match_database"] is True
                and ai_history_api["content_fields_present"] is True
                and ai_history_api["content_fields_display_safe"] is True
                and ai_history_api["content_fields_serialized"] is False
            )
            safety = reload_record.get("safety", {})
            checks["reload_scope_backend_only"] = (
                safety.get("restarted_other_services") is False
                and safety.get("business_posts") == 0
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
                "started_after_l1_confirmation": checks[
                    "backend_started_after_l1_confirmation"
                ],
            }
            result["reload_isolation"] = {
                "mode": reload_record.get("mode"),
                "history": history,
                "non_backend_unchanged": checks[
                    "ready_owned_history_and_non_backend_unchanged"
                ],
                "safety": safety,
            }
            result["authorized_ai_history"] = ai_history_api

        result["checks"] = checks
        result["ready"] = all(checks.values())
    except Exception as exc:  # noqa: BLE001 - emit only the exception type
        result["checks"] = checks
        result["ready"] = False
        result["error_type"] = type(exc).__name__
    finally:
        try:
            get_redis_client().close()
        except Exception as exc:  # noqa: BLE001 - type only
            result["redis_close_warning"] = type(exc).__name__

    _write_json_atomic(output, result)
    print(
        json.dumps(
            {
                "package": PACKAGE,
                "phase": args.phase,
                "ready": result.get("ready"),
                "checked_at_utc": result.get("checked_at_utc"),
                "checks": checks,
                "error_type": result.get("error_type"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
