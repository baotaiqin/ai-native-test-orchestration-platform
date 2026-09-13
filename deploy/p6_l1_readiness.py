"""Read-only pre/post verifier for the bounded P6-L1/r1 service load.

The formal database connection is guarded to SELECT/SHOW only.  The only HTTP
POST made in post mode is the existing development login, which the package
explicitly permits.  RabbitMQ is inspected with rabbitmqctl list_queues only;
no message is fetched, acknowledged, published, or redriven.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from sqlalchemy import func, select, text

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
DEPLOY_ROOT = WORKSPACE_ROOT / "deploy"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(DEPLOY_ROOT))

from app.infrastructure.db.session import SessionLocal
from app.infrastructure.redis.client import get_redis_client, get_runner_heartbeat_store
from app.modules.runners.models import Runner
from app.modules.runs.models import TestRun
from app.modules.web_recordings.models import WebRecording
from p5e_resume_failure_analysis import DEFAULT_READY_FILE, _connect_api
from p5f_archive_acceptance_assets import _database_snapshot, _protected_files

PACKAGE = "P6-L1/r1"
PROJECT_ID = 23
RUNNER_ID = "1282a04dfd894f85877384cb31af5c16"
HEALED_RUN_ID = "run_7852432fb7b241dabcad111664b9621b"
MIGRATION_HEAD = "20260909_0038"
VALIDATION_DIR = WORKSPACE_ROOT / ".codex-validation" / "p6-l1"
PREFLIGHT_FILE = VALIDATION_DIR / "preflight.json"
VERIFICATION_FILE = VALIDATION_DIR / "verification.json"
LOAD_FILE = VALIDATION_DIR / "load.json"
FREEZE_FILE = WORKSPACE_ROOT / ".codex-validation" / "p6-l1-controller-freeze.json"
ARCHIVE_VERIFICATION_FILE = (
    WORKSPACE_ROOT / ".codex-validation" / "p5f-archive" / "verification.json"
)
READY_FILE = WORKSPACE_ROOT / ".codex-validation" / "p5e-services" / "ready.json"
OWNED_FILE = (
    WORKSPACE_ROOT / ".codex-validation" / "p5e-services" / "owned-processes.json"
)
REDRIVE_FILE = (
    WORKSPACE_ROOT
    / ".codex-validation"
    / "p5e-services"
    / "redrive-run_15c59b0b27964dd8a6fec2fcca840f9c.json"
)
DEAD_QUEUE = "ai_test.tasks.dead.v1"
RUNNER_QUEUE = f"ai_test.runner.{RUNNER_ID}.v1"
ACTIVE_RUN_STATUSES = ("QUEUED", "ASSIGNED", "RUNNING", "CANCELLING")
ACTIVE_RECORDING_STATUSES = ("QUEUED", "RUNNING", "STOP_REQUESTED")

EXPECTED_CONTROL_FILE_HASHES = {
    ".codex-validation/p5e-live/result.json": (
        "2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176"
    ),
    ".codex-validation/p5e-live/attempts/V1P5E_20260909_F294AC1E.json": (
        "2a9995eb77ab3710431c800bbaf5a4edb2e4485afbb730b2e71dd94860bee176"
    ),
    ".codex-validation/p5e-live/attempts/V1P5E_20260909_39E35D46.json": (
        "6641011f5e02b7137191bd9ca5ab5a5049eb4099a702db4b1b8c9485da22e42b"
    ),
    ".codex-validation/p5e-live/ai-call-ledger.json": (
        "eb9cbf3fd6a43b5be90d74a658d01ae73126b6f3f82e6ca1444b21c1e19f8b43"
    ),
    ".codex-validation/p5e-services/redrive-run_15c59b0b27964dd8a6fec2fcca840f9c.json": (
        "fd53f47db14f32272e530ffcad3f7395ff60c89046d52bfb21fbb6189634125f"
    ),
    ".codex-validation/p5f-archive/preflight.json": (
        "00f57b1a56b9f096f5a7026cd7d5c01c790ad489cbeab2afa47535bb8c206eee"
    ),
    ".codex-validation/p5f-archive/operations.json": (
        "7f9d927838bdb9524e701adf77e914cd92a71edfc4333f4f61639ca04ccf79a7"
    ),
    ".codex-validation/p5f-archive/verification.json": (
        "58f35fc46abe70bdaaa10be738e0e9504420239ad3704a14f44e06d63d492597"
    ),
}

REQUIRED_OPENAPI_PATHS = {
    "/api/v1/reports",
    "/api/v1/reports/{run_id}",
    "/api/v1/reports/{run_id}/cases",
    "/api/v1/reports/{run_id}/steps",
    "/api/v1/reports/{run_id}/evidence",
    "/api/v1/reports/{run_id}/export",
    "/api/v1/dashboard",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=8) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError("http_json")
    return value


def _get_text(url: str) -> str:
    with urlopen(url, timeout=8) as response:
        return response.read().decode("utf-8")


def _run_safe(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return completed.stdout


def _queue_snapshot() -> dict[str, dict[str, int]]:
    output = _run_safe(
        [
            "docker",
            "exec",
            "ai-test-rabbitmq",
            "rabbitmqctl",
            "--quiet",
            "list_queues",
            "name",
            "messages",
            "messages_ready",
            "messages_unacknowledged",
        ]
    )
    rows: dict[str, dict[str, int]] = {}
    for line in output.splitlines():
        fields = line.strip().split("\t")
        if len(fields) != 4 or fields[0] == "name":
            continue
        if fields[0] in {DEAD_QUEUE, RUNNER_QUEUE}:
            rows[fields[0]] = {
                "messages": int(fields[1]),
                "messages_ready": int(fields[2]),
                "messages_unacknowledged": int(fields[3]),
            }
    return rows


def _container_snapshot() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    format_value = (
        "{{.Id}}|{{.State.Status}}|"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    for name in ("ai-test-rabbitmq", "ai-test-redis", "ai-test-minio"):
        raw = _run_safe(["docker", "inspect", "--format", format_value, name]).strip()
        container_id, status, health = raw.split("|", 2)
        result[name] = {"id": container_id, "status": status, "health": health}
    return result


def _source_snapshot() -> dict[str, dict[str, Any]]:
    freeze = _read_json(FREEZE_FILE)
    if freeze.get("package") != PACKAGE:
        raise RuntimeError("freeze_package")
    expected = freeze.get("source_hashes")
    if not isinstance(expected, dict) or len(expected) != 19:
        raise RuntimeError("freeze_hashes")
    result: dict[str, dict[str, Any]] = {}
    for relative, expected_hash in expected.items():
        actual = _sha256(WORKSPACE_ROOT / relative)
        result[relative] = {
            "expected": expected_hash,
            "actual": actual,
            "matches": actual == expected_hash,
        }
    return result


def _control_files_snapshot() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relative, expected in EXPECTED_CONTROL_FILE_HASHES.items():
        actual = _sha256(WORKSPACE_ROOT / relative)
        result[relative] = {
            "expected": expected,
            "actual": actual,
            "matches": actual == expected,
        }
    return result


def _http_snapshot(*, post: bool) -> tuple[dict[str, Any], dict[str, bool]]:
    checks: dict[str, bool] = {}
    backend_health = _get_json("http://127.0.0.1:8000/health")
    demo_health = _get_json("http://127.0.0.1:8765/health")
    demo_state = _get_json("http://127.0.0.1:8765/control/state")
    frontend = _get_text("http://127.0.0.1:5173")
    openapi = _get_json("http://127.0.0.1:8000/openapi.json")
    paths = openapi.get("paths")
    path_names = set(paths) if isinstance(paths, dict) else set()
    checks["backend_health"] = (
        backend_health.get("status") == "ok"
        and backend_health.get("service") == "backend"
    )
    checks["frontend_health"] = (
        "<title>AI 原生智能测试编排平台</title>" in frontend
    )
    checks["demo_health_and_changed_locator"] = (
        demo_health.get("status") == "ok"
        and demo_health.get("service") == "v1-demo"
        and demo_state.get("changed_locator") is True
    )
    api_reads: dict[str, Any] = {"performed": False}
    if post:
        checks["p6_openapi_paths_loaded"] = REQUIRED_OPENAPI_PATHS <= path_names
        api = _connect_api(DEFAULT_READY_FILE.resolve(), 20.0)
        reports = api.request(
            "GET",
            "/reports",
            params={"project_id": PROJECT_ID, "page": 1, "page_size": 5},
        )
        detail = api.request("GET", f"/reports/{HEALED_RUN_ID}")
        cases = api.request(
            "GET", f"/reports/{HEALED_RUN_ID}/cases", params={"page": 1, "page_size": 5}
        )
        steps = api.request(
            "GET", f"/reports/{HEALED_RUN_ID}/steps", params={"page": 1, "page_size": 5}
        )
        evidence = api.request(
            "GET",
            f"/reports/{HEALED_RUN_ID}/evidence",
            params={"page": 1, "page_size": 5},
        )
        dashboard = api.request("GET", "/dashboard", params={"project_id": PROJECT_ID})
        detail_summary = detail.get("summary")
        detail_run_id = (
            detail_summary.get("run_id") if isinstance(detail_summary, dict) else None
        )
        checks["report_and_dashboard_read_apis"] = all(
            isinstance(item, dict)
            for item in (reports, detail, cases, steps, evidence, dashboard)
        ) and detail_run_id == HEALED_RUN_ID and dashboard.get("read_only") is True
        api_reads = {
            "performed": True,
            "normal_login_posts": 1,
            "business_posts": 0,
            "report_list_total": reports.get("total"),
            "detail_run_id": detail_run_id,
            "case_total": cases.get("total"),
            "step_total": steps.get("total"),
            "evidence_total": evidence.get("total"),
            "dashboard_read_only": dashboard.get("read_only"),
        }
    return (
        {
            "backend": {"healthy": checks["backend_health"]},
            "frontend": {"healthy": checks["frontend_health"]},
            "demo": {
                "healthy": demo_health.get("status") == "ok"
                and demo_health.get("service") == "v1-demo",
                "changed_locator": demo_state.get("changed_locator"),
            },
            "openapi_path_count": len(path_names),
            "required_paths_present": sorted(REQUIRED_OPENAPI_PATHS & path_names),
            "read_api_checks": api_reads,
        },
        checks,
    )


def _database_and_runner_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    with SessionLocal() as session:
        migration_rows = session.execute(text("SELECT version_num FROM alembic_version")).all()
        migration_versions = sorted(str(row[0]) for row in migration_rows)
        mysql_identity_row = session.execute(
            text(
                "SELECT @@server_uuid AS server_uuid, @@hostname AS hostname, "
                "@@port AS port"
            )
        ).mappings().one()
        mysql_uptime_row = session.execute(
            text("SHOW GLOBAL STATUS LIKE 'Uptime'")
        ).one()
        in_flight_runs = int(
            session.scalar(
                select(func.count())
                .select_from(TestRun)
                .where(TestRun.status.in_(ACTIVE_RUN_STATUSES))
            )
            or 0
        )
        in_flight_recordings = int(
            session.scalar(
                select(func.count())
                .select_from(WebRecording)
                .where(WebRecording.status.in_(ACTIVE_RECORDING_STATUSES))
            )
            or 0
        )
        active_runners = list(
            session.scalars(select(Runner).where(Runner.status == "ACTIVE")).all()
        )
        runner = session.get(Runner, RUNNER_ID)
        if runner is None:
            raise LookupError("runner")
        capabilities = {
            item.capability: item.status for item in runner.capabilities
        }
        slots = {
            item.slot_type: {"total": item.total, "available": item.available}
            for item in runner.slots
        }
        last_heartbeat = runner.last_heartbeat_at
        if last_heartbeat is not None:
            if last_heartbeat.tzinfo is None:
                last_heartbeat = last_heartbeat.replace(tzinfo=UTC)
            else:
                last_heartbeat = last_heartbeat.astimezone(UTC)
        heartbeat = get_runner_heartbeat_store().get_heartbeat(RUNNER_ID)
        online = (
            isinstance(heartbeat, dict)
            and heartbeat.get("runner_id") == RUNNER_ID
            and heartbeat.get("status") == "ONLINE"
        )
        runner_snapshot = {
            "runner_id": runner.id,
            "status": runner.status,
            "active_runner_ids": sorted(item.id for item in active_runners),
            "online_status": "ONLINE" if online else "OFFLINE",
            "web_capability": capabilities.get("WEB"),
            "web_slots": slots.get("WEB"),
            "last_heartbeat_at": last_heartbeat.isoformat() if last_heartbeat else None,
        }
    return (
        {
            "migration_versions": migration_versions,
            "mysql_server": {
                "server_uuid": str(mysql_identity_row["server_uuid"]),
                "hostname": str(mysql_identity_row["hostname"]),
                "port": int(mysql_identity_row["port"]),
                "uptime_seconds": int(mysql_uptime_row[1]),
            },
            "in_flight_run_count": in_flight_runs,
            "in_flight_recording_count": in_flight_recordings,
        },
        runner_snapshot,
    )


def _ready_non_target_snapshot(ready: dict[str, Any], owned: list[Any]) -> dict[str, Any]:
    services = sorted(
        (
            item
            for item in ready.get("services", [])
            if isinstance(item, dict) and item.get("name") not in {"backend"}
        ),
        key=lambda item: str(item.get("name")),
    )
    non_target_owned = sorted(
        (
            item
            for item in owned
            if isinstance(item, dict)
            and item.get("purpose") not in {"backend", "runner-worker"}
        ),
        key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
    )
    ready_embedded = sorted(
        (
            item
            for item in ready.get("owned_processes", [])
            if isinstance(item, dict)
            and item.get("purpose") not in {"backend", "runner-worker"}
        ),
        key=lambda item: (str(item.get("purpose")), int(item.get("pid", 0))),
    )
    return {
        "services": services,
        "middleware": ready.get("middleware"),
        "database": ready.get("database"),
        "owned_non_target": non_target_owned,
        "ready_embedded_owned_non_target": ready_embedded,
    }


def main() -> int:
    args = _parse_args()
    output = args.output or (
        PREFLIGHT_FILE if args.phase == "preflight" else VERIFICATION_FILE
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "package": PACKAGE,
        "phase": args.phase,
        "mode": "READ_ONLY_SELECT_SHOW_AND_ALLOWED_LOGIN",
        "checked_at_utc": datetime.now(UTC).isoformat(),
    }
    checks: dict[str, bool] = {}
    try:
        sources = _source_snapshot()
        checks["controller_source_hashes_match"] = all(
            item["matches"] for item in sources.values()
        )
        result["source_hashes"] = sources

        control_files = _control_files_snapshot()
        checks["protected_control_files_unchanged"] = all(
            item["matches"] for item in control_files.values()
        )
        redrive = _read_json(REDRIVE_FILE)
        checks["redrive_journal_still_source_acked"] = (
            redrive.get("state") == "source_acked"
            and redrive.get("run_id")
            == "run_15c59b0b27964dd8a6fec2fcca840f9c"
        )
        result["control_files"] = control_files

        archive_baseline = _read_json(ARCHIVE_VERIFICATION_FILE)
        protected_files = _protected_files()
        protected_database = _database_snapshot(allow_archived=True)
        checks["archive_project_and_assets_unchanged"] = (
            protected_database["project"] == archive_baseline["project"]
            and protected_database["assets"] == archive_baseline["assets"]
            and protected_database["owned_case_version_references"]
            == archive_baseline["owned_case_version_references"]
            and protected_database["external_case_version_references"] == []
            and protected_database["extra_elements_on_owned_pages"] == 0
            and protected_database["owned_in_flight_run_ids"] == []
        )
        checks["four_runs_and_19_evidence_history_unchanged"] = (
            protected_database["history"] == archive_baseline["history"]
            and protected_database["history_sha256"]
            == archive_baseline["history_sha256_after"]
            and protected_database["history"]["runs"]["count"] == 4
            and protected_database["history"]["evidence"]["count"] == 19
            and protected_database["history"]["ai_calls"]["count"] == 9
            and protected_database["history"]["healing_proposals"]["count"] == 2
            and protected_database["history"]["failure_analyses"]["count"] == 1
        )
        checks["archive_protected_files_unchanged"] = (
            protected_files == archive_baseline["protected_files"]
        )
        result["protected"] = {
            "database": protected_database,
            "files": protected_files,
            "redrive_state": redrive.get("state"),
        }

        database, runner = _database_and_runner_snapshot()
        checks["migration_at_expected_head"] = database["migration_versions"] == [
            MIGRATION_HEAD
        ]
        checks["no_in_flight_runs"] = database["in_flight_run_count"] == 0
        checks["no_in_flight_recordings"] = (
            database["in_flight_recording_count"] == 0
        )
        checks["unique_runner_active_online_web_1_of_1"] = (
            runner["runner_id"] == RUNNER_ID
            and runner["status"] == "ACTIVE"
            and runner["active_runner_ids"] == [RUNNER_ID]
            and runner["online_status"] == "ONLINE"
            and runner["web_capability"] == "READY"
            and runner["web_slots"] == {"total": 1, "available": 1}
        )
        if args.phase == "post":
            if not args.confirmation_utc:
                raise ValueError("confirmation_utc")
            confirmation = datetime.fromisoformat(
                args.confirmation_utc.replace("Z", "+00:00")
            ).astimezone(UTC).replace(microsecond=0)
            heartbeat_at = datetime.fromisoformat(runner["last_heartbeat_at"]).astimezone(UTC)
            checks["runner_heartbeat_after_source_confirmation"] = (
                heartbeat_at >= confirmation
            )
        result["database"] = database
        result["runner"] = runner

        queues = _queue_snapshot()
        checks["runner_queue_empty"] = queues.get(RUNNER_QUEUE) == {
            "messages": 0,
            "messages_ready": 0,
            "messages_unacknowledged": 0,
        }
        checks["original_dlq_message_preserved"] = queues.get(DEAD_QUEUE) == {
            "messages": 1,
            "messages_ready": 1,
            "messages_unacknowledged": 0,
        }
        result["queues"] = queues

        containers = _container_snapshot()
        checks["middleware_containers_healthy"] = all(
            item["status"] == "running" and item["health"] in {"healthy", "none"}
            for item in containers.values()
        )
        result["containers"] = containers

        http, http_checks = _http_snapshot(post=args.phase == "post")
        checks.update(http_checks)
        result["http"] = http

        ready = _read_json(READY_FILE)
        owned = _read_json(OWNED_FILE)
        if not isinstance(ready, dict) or not isinstance(owned, list):
            raise TypeError("service_manifests")
        result["manifest_hashes"] = {
            "ready": _sha256(READY_FILE),
            "owned": _sha256(OWNED_FILE),
        }
        result["non_target_runtime_manifest"] = _ready_non_target_snapshot(ready, owned)

        if args.phase == "post":
            preflight = _read_json(PREFLIGHT_FILE)
            load = _read_json(LOAD_FILE)
            checks["preflight_was_ready"] = (
                preflight.get("package") == PACKAGE
                and preflight.get("phase") == "preflight"
                and preflight.get("ready") is True
            )
            checks["protected_database_equal_preflight"] = (
                result["protected"] == preflight.get("protected")
            )
            checks["queues_equal_preflight"] = queues == preflight.get("queues")
            checks["containers_equal_preflight"] = (
                containers == preflight.get("containers")
            )
            preflight_mysql = preflight.get("database", {}).get("mysql_server", {})
            current_mysql = database["mysql_server"]
            checks["mysql_server_not_restarted"] = (
                {
                    key: current_mysql.get(key)
                    for key in ("server_uuid", "hostname", "port")
                }
                == {
                    key: preflight_mysql.get(key)
                    for key in ("server_uuid", "hostname", "port")
                }
                and current_mysql["uptime_seconds"]
                >= int(preflight_mysql.get("uptime_seconds", 0))
            )
            checks["non_target_runtime_manifest_unchanged"] = (
                result["non_target_runtime_manifest"]
                == preflight.get("non_target_runtime_manifest")
            )
            history = load.get("history", {})
            ready_history = READY_FILE.parent / str(history.get("ready_file", ""))
            owned_history = OWNED_FILE.parent / str(history.get("owned_file", ""))
            checks["manifest_histories_preserved"] = (
                ready_history.is_file()
                and owned_history.is_file()
                and _sha256(ready_history) == history.get("ready_sha256")
                and _sha256(owned_history) == history.get("owned_sha256")
            )
            backend = next(
                (
                    item
                    for item in ready.get("services", [])
                    if isinstance(item, dict) and item.get("name") == "backend"
                ),
                None,
            )
            backend_hashes = {
                key: value["actual"]
                for key, value in sources.items()
                if key.startswith("backend/")
            }
            runner_hashes = {
                key: value["actual"]
                for key, value in sources.items()
                if key.startswith("runner/")
            }
            checks["backend_manifest_loaded_p6_l1"] = (
                isinstance(backend, dict)
                and backend.get("ownership") == "p5e-owned"
                and backend.get("reload_package") == PACKAGE
                and backend.get("loaded_source_hashes") == backend_hashes
                and bool(backend.get("process_tree"))
            )
            ready_runner = ready.get("runner")
            checks["worker_manifest_loaded_p6_l1"] = (
                isinstance(ready_runner, dict)
                and ready_runner.get("runner_id") == RUNNER_ID
                and ready_runner.get("ownership") == "p5e-owned"
                and ready_runner.get("reload_package") == PACKAGE
                and ready_runner.get("loaded_source_hashes") == runner_hashes
                and ready_runner.get("logical_worker_count") == 1
                and ready_runner.get("process_count") == 2
                and ready_runner.get("web_slots") == {"total": 1, "available": 1}
            )
            checks["ready_embedded_owned_matches_ledger"] = (
                ready.get("owned_processes") == owned
            )
            confirmation = datetime.fromisoformat(
                args.confirmation_utc.replace("Z", "+00:00")
            ).astimezone(UTC)
            target_starts = [
                datetime.fromisoformat(str(item["started_at"]).replace("Z", "+00:00")).astimezone(UTC)
                for item in owned
                if isinstance(item, dict)
                and item.get("purpose") in {"backend", "runner-worker"}
            ]
            expected_target_count = len(
                load.get("new_backend", {}).get("process_tree", [])
            ) + len(load.get("new_worker", {}).get("process_tree", []))
            checks["target_processes_started_after_confirmation"] = (
                expected_target_count >= 4
                and len(target_starts) == expected_target_count
                and all(value > confirmation for value in target_starts)
            )
            safety = load.get("safety", {})
            checks["load_scope_and_operations_bounded"] = (
                safety.get("restarted_only_backend_and_worker") is True
                and safety.get("business_posts") == 0
                and safety.get("ai_requests") == 0
                and safety.get("message_get_publish_ack_redrive_operations") == 0
                and safety.get("demo_state_changes") == 0
                and safety.get("container_restarts") == 0
            )
            checks["loader_internal_protection_checks_passed"] = all(
                bool(value) for value in load.get("protection_checks", {}).values()
            )
            result["load"] = load

        result["checks"] = checks
        result["ready"] = all(checks.values())
    except Exception as exc:  # noqa: BLE001 - output is redacted by construction
        result["checks"] = checks
        result["ready"] = False
        result["error_type"] = type(exc).__name__
    finally:
        try:
            get_redis_client().close()
        except Exception:  # noqa: BLE001 - close warnings do not alter verification
            pass

    _write_json(output, result)
    print(
        json.dumps(
            {
                "package": PACKAGE,
                "phase": args.phase,
                "ready": result["ready"],
                "checks": len(result.get("checks", {})),
                "failed_checks": sorted(
                    key for key, value in result.get("checks", {}).items() if not value
                ),
                "error_type": result.get("error_type"),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
