"""Run the bounded P5-E live acceptance against the formal local stack.

This script intentionally talks only to the public HTTP API and the local demo.
It never reads Runner credentials, model keys, cookies, or StorageState, and it
never prints response bodies.  A fresh successful execution makes exactly
three AI business calls; the explicitly bounded Call 16 recovery makes four
cumulative calls and never resets the global ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

WORKSPACE = Path(__file__).resolve().parents[1]
BACKEND_DIR = WORKSPACE / "backend"
DEFAULT_READY_FILE = WORKSPACE / ".codex-validation" / "p5e-services" / "ready.json"
DEFAULT_RESULT_FILE = WORKSPACE / ".codex-validation" / "p5e-live" / "result.json"
DEFAULT_ATTEMPT_DIR = WORKSPACE / ".codex-validation" / "p5e-live" / "attempts"
DEFAULT_AI_LEDGER_FILE = (
    WORKSPACE / ".codex-validation" / "p5e-live" / "ai-call-ledger.json"
)
API_PREFIX = "/api/v1"
AI_CALL_LIMIT = 6
TERMINAL_RUN_STATUSES = {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}
KNOWN_CALL_16_ACCEPTANCE_ID = "V1P5E_20260909_F294AC1E"
KNOWN_CALL_16_BASELINE_RUN_ID = "run_786380f61a9647cd8976d376f90bc6e8"
KNOWN_CALL_16_FAILED_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
KNOWN_CALL_16_ID = 16
KNOWN_CALL_16_PROJECT_ID = 23
KNOWN_CALL_16_CASE_ID = 6
KNOWN_CALL_16_CASE_VERSION_ID = 6
KNOWN_CALL_16_ELEMENT_ID = 2
KNOWN_CALL_16_ELEMENT_VERSION_ID = 2
KNOWN_CALL_16_CASE_RUN_ID = 28
SENSITIVE_PUBLIC_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "raw_response",
    "refresh_token",
    "source_snapshot",
    "storage_state",
    "token",
}


class AcceptanceFailure(RuntimeError):
    """A safe, user-displayable acceptance failure without response content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ApiFailure(AcceptanceFailure):
    def __init__(self, status: int | None, method: str, path: str) -> None:
        safe_path = urlsplit(path).path
        status_part = str(status) if status is not None else "NETWORK"
        super().__init__(f"HTTP_{status_part}_{method}_{safe_path}")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AcceptanceFailure(code)


def _load_json(
    path: Path, *, invalid_code: str = "READINESS_MANIFEST_INVALID"
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure(invalid_code) from exc
    require(isinstance(payload, dict), invalid_code)
    return payload


def _local_url(value: object, *, port: int, label: str) -> str:
    require(isinstance(value, str), f"{label}_URL_MISSING")
    parsed = urlsplit(value)
    require(parsed.scheme == "http", f"{label}_URL_NOT_HTTP")
    require(parsed.hostname in {"127.0.0.1", "localhost"}, f"{label}_URL_NOT_LOCAL")
    require(parsed.port == port, f"{label}_PORT_MISMATCH")
    require(not parsed.username and not parsed.password, f"{label}_URL_HAS_CREDENTIALS")
    require(not parsed.query and not parsed.fragment, f"{label}_URL_HAS_EXTRA_DATA")
    return value.rstrip("/")


def _public_keys_are_safe(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_PUBLIC_KEYS:
                return False
            if not _public_keys_are_safe(child):
                return False
    elif isinstance(value, list):
        return all(_public_keys_are_safe(item) for item in value)
    return True


class JsonClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._authorization: str | None = None

    def set_bearer(self, token: str) -> None:
        require(bool(token), "LOGIN_TOKEN_MISSING")
        self._authorization = f"Bearer {token}"

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        authenticated: bool = True,
        expected: tuple[int, ...] = (200,),
        response_type: type = dict,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean_params = {
                key: value for key, value in params.items() if value is not None
            }
            url = f"{url}?{urlencode(clean_params)}"
        headers = {"Accept": "application/json"}
        if authenticated:
            require(self._authorization is not None, "AUTHORIZATION_NOT_INITIALIZED")
            headers["Authorization"] = self._authorization
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                status = response.status
                raw = response.read(10_000_001)
        except HTTPError as exc:
            try:
                exc.read(1_000_001)
            finally:
                exc.close()
            raise ApiFailure(exc.code, method, path) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiFailure(None, method, path) from exc
        if status not in expected:
            raise ApiFailure(status, method, path)
        require(len(raw) <= 10_000_000, "HTTP_JSON_RESPONSE_TOO_LARGE")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AcceptanceFailure("HTTP_JSON_RESPONSE_INVALID") from exc
        require(isinstance(parsed, response_type), "HTTP_JSON_RESPONSE_TYPE_INVALID")
        return parsed

    def get_bytes(self, path: str, *, timeout: float | None = None) -> bytes:
        require(self._authorization is not None, "AUTHORIZATION_NOT_INITIALIZED")
        request = Request(
            f"{self.base_url}{path}",
            headers={"Authorization": self._authorization},
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                require(response.status == 200, "EVIDENCE_DOWNLOAD_STATUS_INVALID")
                raw = response.read(20_000_001)
        except HTTPError as exc:
            try:
                exc.read(1_000_001)
            finally:
                exc.close()
            raise ApiFailure(exc.code, "GET", path) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiFailure(None, "GET", path) from exc
        require(len(raw) <= 20_000_000, "EVIDENCE_DOWNLOAD_TOO_LARGE")
        return raw


@dataclass
class SafeResult:
    acceptance_id: str
    status: str = "RUNNING"
    stage: str = "initialize"
    ids: dict[str, Any] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"ai_business_calls": 0})
    links: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "acceptance_id": self.acceptance_id,
            "status": self.status,
            "stage": self.stage,
            "ids": self.ids,
            "statuses": self.statuses,
            "counts": self.counts,
            "links": self.links,
        }


class LiveAcceptance:
    def __init__(
        self,
        *,
        ready_file: Path,
        result_file: Path,
        ordinary_timeout: float,
        ai_timeout: float,
        run_timeout: float,
        poll_interval: float,
        allow_new_attempt: bool,
        resume_attempt: bool,
        resume_after_call_16: bool,
    ) -> None:
        self.ready_file = ready_file
        self.result_file = result_file
        self.ordinary_timeout = ordinary_timeout
        self.ai_timeout = ai_timeout
        self.run_timeout = run_timeout
        self.poll_interval = poll_interval
        self.allow_new_attempt = allow_new_attempt
        self.resume_after_call_16 = resume_after_call_16
        self.resume_attempt = resume_attempt or resume_after_call_16
        self.ai_ledger_file = DEFAULT_AI_LEDGER_FILE
        self.ai_calls = 0
        if self.resume_attempt:
            self.safe = self._load_resume_state()
            self.acceptance_id = self.safe.acceptance_id
            self.ai_calls = self.safe.counts["ai_business_calls"]
        else:
            suffix = secrets.token_hex(4).upper()
            date = datetime.now(UTC).strftime("%Y%m%d")
            self.acceptance_id = f"V1P5E_{date}_{suffix}"
            self.safe = SafeResult(self.acceptance_id)
        self.attempt_file = DEFAULT_ATTEMPT_DIR / f"{self.acceptance_id}.json"
        self.api: JsonClient | None = None
        self.demo: JsonClient | None = None
        self.project_id = 0
        self.environment_id = 0
        self.runner_id = ""
        self.healing_prompt: dict[str, Any] = {}
        self.analysis_prompt: dict[str, Any] = {}
        self.model_bindings_by_task: dict[str, dict[str, Any]] = {}

    def _load_resume_state(self) -> SafeResult:
        require(self.result_file.is_file(), "RESUME_RESULT_MISSING")
        pointer = _load_json(self.result_file, invalid_code="RESUME_RESULT_INVALID")
        acceptance_id = pointer.get("acceptance_id")
        require(
            isinstance(acceptance_id, str)
            and re.fullmatch(r"V1P5E_\d{8}_[A-F0-9]{8}", acceptance_id) is not None,
            "RESUME_ACCEPTANCE_ID_INVALID",
        )
        attempt_file = DEFAULT_ATTEMPT_DIR / f"{acceptance_id}.json"
        require(attempt_file.is_file(), "RESUME_ATTEMPT_MANIFEST_MISSING")
        manifest = _load_json(
            attempt_file, invalid_code="RESUME_ATTEMPT_MANIFEST_INVALID"
        )
        require(manifest == pointer, "RESUME_POINTER_MANIFEST_MISMATCH")
        require(manifest.get("schema_version") == 1, "RESUME_SCHEMA_MISMATCH")
        require(manifest.get("status") == "FAILED", "RESUME_STATUS_NOT_FAILED")
        require(
            manifest.get("stage")
            in {
                "run_failed_old_locator",
                "preflight",
                "verify_resume_assets",
                "resume_failed_old_locator",
                "verify_real_locator_failure",
                "generate_and_reject_healing",
            },
            "RESUME_STAGE_NOT_SUPPORTED",
        )
        ids = manifest.get("ids")
        statuses = manifest.get("statuses")
        counts = manifest.get("counts")
        links = manifest.get("links")
        require(isinstance(ids, dict), "RESUME_IDS_INVALID")
        require(isinstance(statuses, dict), "RESUME_STATUSES_INVALID")
        require(isinstance(counts, dict), "RESUME_COUNTS_INVALID")
        require(isinstance(links, dict), "RESUME_LINKS_INVALID")
        require(_public_keys_are_safe(manifest), "RESUME_MANIFEST_UNSAFE")
        require(
            isinstance(statuses.get("error_code"), str)
            and bool(statuses["error_code"]),
            "RESUME_FAILURE_CODE_INVALID",
        )
        require(statuses.get("baseline_run") == "SUCCESS", "RESUME_BASELINE_NOT_SUCCESS")
        require(statuses.get("demo_locator") == "CHANGED", "RESUME_DEMO_NOT_CHANGED")
        expected_ai_calls = 1 if self.resume_after_call_16 else 0
        require(
            counts.get("ai_business_calls") == expected_ai_calls,
            "RESUME_AI_CALL_COUNT_UNEXPECTED",
        )
        if self.resume_after_call_16:
            require(
                acceptance_id == KNOWN_CALL_16_ACCEPTANCE_ID,
                "CALL_16_RECOVERY_ATTEMPT_MISMATCH",
            )
            require(
                manifest.get("stage") == "generate_and_reject_healing",
                "CALL_16_RECOVERY_STAGE_MISMATCH",
            )
            require(
                statuses.get("error_code")
                == "HTTP_409_POST_/runs/run_15c59b0b27964dd8a6fec2fcca840f9c/"
                "web-healing-proposals",
                "CALL_16_RECOVERY_ERROR_MISMATCH",
            )
            require(
                "healing_proposal_ids" not in ids
                and "healed_web_case_version_id" not in ids,
                "CALL_16_RECOVERY_ALREADY_MATERIALIZED",
            )
        run_ids = ids.get("run_ids")
        require(isinstance(run_ids, dict), "RESUME_RUN_IDS_INVALID")
        for label in ("baseline", "failed_old_locator"):
            require(
                isinstance(run_ids.get(label), str)
                and re.fullmatch(r"run_[a-f0-9]{32}", run_ids[label]) is not None,
                f"RESUME_{label.upper()}_RUN_ID_INVALID",
            )
        if self.resume_after_call_16:
            require(
                run_ids.get("baseline") == KNOWN_CALL_16_BASELINE_RUN_ID
                and run_ids.get("failed_old_locator") == KNOWN_CALL_16_FAILED_RUN_ID,
                "CALL_16_RECOVERY_RUN_MISMATCH",
            )
            require(
                ids.get("project_id") == KNOWN_CALL_16_PROJECT_ID
                and ids.get("web_case_id") == KNOWN_CALL_16_CASE_ID
                and ids.get("source_web_case_version_id")
                == KNOWN_CALL_16_CASE_VERSION_ID
                and ids.get("web_element_id") == KNOWN_CALL_16_ELEMENT_ID
                and ids.get("source_element_version_id")
                == KNOWN_CALL_16_ELEMENT_VERSION_ID
                and ids.get("failed_case_run_id") == KNOWN_CALL_16_CASE_RUN_ID,
                "CALL_16_RECOVERY_ASSET_MISMATCH",
            )
        for key in (
            "project_id",
            "environment_id",
            "web_element_id",
            "source_element_version_id",
            "web_case_id",
            "source_web_case_version_id",
        ):
            require(type(ids.get(key)) is int, f"RESUME_{key.upper()}_INVALID")
        require(
            isinstance(ids.get("runner_id"), str) and bool(ids["runner_id"]),
            "RESUME_RUNNER_ID_INVALID",
        )
        statuses = dict(statuses)
        statuses.pop("error_code", None)
        return SafeResult(
            acceptance_id=acceptance_id,
            status="RUNNING",
            stage="resume_initialize",
            ids=ids,
            statuses=statuses,
            counts=counts,
            links=links,
        )

    def stage(self, name: str) -> None:
        self.safe.stage = name
        print(f"[{name}] running", flush=True)

    def write_result(self) -> None:
        payload = self.safe.as_json()
        payload["links"]["attempt_manifest"] = f"attempts/{self.acceptance_id}.json"
        require(_public_keys_are_safe(payload), "RESULT_CONTAINS_FORBIDDEN_KEY")
        self.attempt_file.parent.mkdir(parents=True, exist_ok=True)
        self.attempt_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        pointer = {
            "schema_version": 1,
            "acceptance_id": self.acceptance_id,
            "status": self.safe.status,
            "stage": self.safe.stage,
            "ids": self.safe.ids,
            "statuses": self.safe.statuses,
            "counts": self.safe.counts,
            "links": payload["links"],
        }
        self.result_file.write_text(
            json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _api(self) -> JsonClient:
        require(self.api is not None, "API_CLIENT_NOT_INITIALIZED")
        return self.api

    def _demo(self) -> JsonClient:
        require(self.demo is not None, "DEMO_CLIENT_NOT_INITIALIZED")
        return self.demo

    def _count_ai_call(self) -> None:
        ledger = self._load_or_rebuild_ai_ledger()
        total = ledger["total_attempted"]
        require(total < AI_CALL_LIMIT, "AI_BUSINESS_CALL_LIMIT_EXCEEDED")
        self.ai_calls += 1
        total += 1
        ledger["total_attempted"] = total
        ledger["calls"].append(
            {
                "acceptance_id": self.acceptance_id,
                "attempt_ordinal": self.ai_calls,
                "stage": self.safe.stage,
                "status": "ATTEMPTED",
            }
        )
        self._write_ai_ledger(ledger)
        self.safe.counts["ai_business_calls"] = self.ai_calls
        self.safe.counts["ai_business_calls_cumulative"] = total

    def _load_or_rebuild_ai_ledger(self) -> dict[str, Any]:
        if self.ai_ledger_file.is_file():
            try:
                ledger = json.loads(self.ai_ledger_file.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AcceptanceFailure("AI_CALL_LEDGER_INVALID") from exc
            require(isinstance(ledger, dict), "AI_CALL_LEDGER_INVALID")
            require(ledger.get("schema_version") == 1, "AI_CALL_LEDGER_SCHEMA_MISMATCH")
            require(
                isinstance(ledger.get("total_attempted"), int),
                "AI_CALL_LEDGER_TOTAL_INVALID",
            )
            require(
                isinstance(ledger.get("calls"), list), "AI_CALL_LEDGER_CALLS_INVALID"
            )
            require(
                ledger["total_attempted"] == len(ledger["calls"]),
                "AI_CALL_LEDGER_COUNT_MISMATCH",
            )
            require(
                0 <= ledger["total_attempted"] <= AI_CALL_LIMIT,
                "AI_CALL_LEDGER_LIMIT_INVALID",
            )
            return ledger

        calls: list[dict[str, Any]] = []
        if DEFAULT_ATTEMPT_DIR.is_dir():
            for path in sorted(DEFAULT_ATTEMPT_DIR.glob("V1P5E_*.json")):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise AcceptanceFailure("ATTEMPT_MANIFEST_INVALID") from exc
                count = manifest.get("counts", {}).get("ai_business_calls", 0)
                acceptance_id = manifest.get("acceptance_id")
                require(
                    isinstance(count, int) and count >= 0, "ATTEMPT_AI_COUNT_INVALID"
                )
                require(isinstance(acceptance_id, str), "ATTEMPT_ID_INVALID")
                for ordinal in range(1, count + 1):
                    calls.append(
                        {
                            "acceptance_id": acceptance_id,
                            "attempt_ordinal": ordinal,
                            "stage": "RECOVERED_FROM_MANIFEST",
                            "status": "ATTEMPTED",
                        }
                    )
        require(len(calls) <= AI_CALL_LIMIT, "AI_BUSINESS_CALL_LIMIT_EXCEEDED")
        return {"schema_version": 1, "total_attempted": len(calls), "calls": calls}

    def _write_ai_ledger(self, ledger: dict[str, Any]) -> None:
        require(_public_keys_are_safe(ledger), "AI_CALL_LEDGER_UNSAFE")
        self.ai_ledger_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ai_ledger_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.ai_ledger_file)

    def prepare(self) -> None:
        self.stage("preflight")
        if self.resume_attempt:
            require(self.result_file.is_file(), "RESUME_RESULT_MISSING")
            require(self.attempt_file.is_file(), "RESUME_ATTEMPT_MANIFEST_MISSING")
        else:
            require(
                self.allow_new_attempt or not self.result_file.exists(),
                "NEW_ATTEMPT_CONFIRMATION_REQUIRED",
            )
            require(not self.attempt_file.exists(), "ATTEMPT_ID_COLLISION")
        require(self.ready_file.is_file(), "READINESS_MANIFEST_MISSING")
        ready = _load_json(self.ready_file)
        require(ready.get("schema_version") == 1, "READINESS_SCHEMA_MISMATCH")

        database = ready.get("database")
        require(isinstance(database, dict), "READINESS_DATABASE_MISSING")
        require(database.get("engine") == "mysql", "READINESS_DATABASE_NOT_MYSQL")
        require(database.get("at_head") is True, "READINESS_MIGRATION_NOT_AT_HEAD")

        middleware = ready.get("middleware")
        require(isinstance(middleware, dict), "READINESS_MIDDLEWARE_MISSING")
        for name in ("rabbitmq", "redis", "minio"):
            service = middleware.get(name)
            require(isinstance(service, dict), f"READINESS_{name.upper()}_MISSING")
            require(
                service.get("healthy") is True, f"READINESS_{name.upper()}_UNHEALTHY"
            )
            self.safe.statuses[name] = "HEALTHY"

        services = ready.get("services")
        require(isinstance(services, list), "READINESS_SERVICES_MISSING")
        service_map = {
            item.get("name"): item
            for item in services
            if isinstance(item, dict) and item.get("name")
        }
        backend = service_map.get("backend")
        demo = service_map.get("demo")
        require(isinstance(backend, dict), "READINESS_BACKEND_MISSING")
        require(isinstance(demo, dict), "READINESS_DEMO_MISSING")
        require(backend.get("healthy") is True, "READINESS_BACKEND_UNHEALTHY")
        require(demo.get("healthy") is True, "READINESS_DEMO_UNHEALTHY")
        backend_url = _local_url(backend.get("address"), port=8000, label="BACKEND")
        demo_url = _local_url(demo.get("address"), port=8765, label="DEMO")

        runner = ready.get("runner")
        require(isinstance(runner, dict), "READINESS_RUNNER_MISSING")
        require(runner.get("healthy") is True, "READINESS_RUNNER_UNHEALTHY")
        require(runner.get("status") == "ACTIVE", "READINESS_RUNNER_NOT_ACTIVE")
        require(
            runner.get("online_status") == "ONLINE",
            "READINESS_RUNNER_STATUS_NOT_ONLINE",
        )
        require(
            runner.get("logical_worker_count") == 1,
            "READINESS_RUNNER_COUNT_NOT_ONE",
        )
        require(
            runner.get("web_capability") == "READY",
            "READINESS_RUNNER_WEB_CAPABILITY_NOT_READY",
        )
        ready_slots = runner.get("web_slots")
        require(isinstance(ready_slots, dict), "READINESS_WEB_SLOTS_INVALID")
        require(
            ready_slots.get("total") == 1 and ready_slots.get("available") == 1,
            "READINESS_WEB_SLOT_COUNT_NOT_ONE",
        )
        runner_id = runner.get("runner_id")
        require(
            isinstance(runner_id, str) and bool(runner_id),
            "READINESS_RUNNER_ID_MISSING",
        )
        self.runner_id = runner_id

        self.api = JsonClient(f"{backend_url}{API_PREFIX}", self.ordinary_timeout)
        self.demo = JsonClient(demo_url, self.ordinary_timeout)
        backend_health = JsonClient(backend_url, self.ordinary_timeout).request(
            "GET", "/health", authenticated=False
        )
        demo_health = self._demo().request("GET", "/health", authenticated=False)
        require(backend_health.get("status") == "ok", "BACKEND_HEALTH_FAILED")
        require(demo_health.get("status") == "ok", "DEMO_HEALTH_FAILED")
        self.safe.statuses.update({"backend": "HEALTHY", "demo": "HEALTHY"})

        if str(BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(BACKEND_DIR))
        from app.core.config import Settings

        settings = Settings(_env_file=str(BACKEND_DIR / ".env"))
        login = self._api().request(
            "POST",
            "/auth/login",
            payload={
                "username": settings.dev_admin_username,
                "password": settings.dev_admin_password,
            },
            authenticated=False,
        )
        token = login.get("access_token")
        require(isinstance(token, str), "LOGIN_TOKEN_MISSING")
        self._api().set_bearer(token)
        del login, token, settings

        expected_resume_ids = (
            json.loads(json.dumps(self.safe.ids)) if self.resume_attempt else None
        )
        self._select_runtime_context()
        if self.resume_attempt:
            require(
                isinstance(expected_resume_ids, dict),
                "RESUME_EXPECTED_IDS_INVALID",
            )
            self._assert_resume_runtime_context(expected_resume_ids)
            demo_state = self._demo().request(
                "GET", "/control/state", authenticated=False
            )
            require(
                demo_state.get("changed_locator") is True,
                "RESUME_DEMO_LOCATOR_NOT_CHANGED",
            )
            self._validate_resume_ledger()
            if self.resume_after_call_16:
                self._validate_known_failed_healing_call()
        else:
            self._demo().request(
                "POST",
                "/control/locator",
                payload={"changed": False},
                authenticated=False,
            )

    def _assert_resume_runtime_context(self, expected_ids: dict[str, Any]) -> None:
        for key in (
            "project_id",
            "model_binding_ids",
            "environment_id",
            "runner_id",
            "prompt_ids",
            "prompt_version_ids",
            "output_schema_ids",
        ):
            require(
                self.safe.ids.get(key) == expected_ids.get(key),
                f"RESUME_{key.upper()}_DRIFT",
            )

    def _validate_resume_ledger(self) -> None:
        ledger = self._load_or_rebuild_ai_ledger()
        manifest_cumulative = self.safe.counts.get(
            "ai_business_calls_cumulative",
            self.safe.counts.get("ai_business_calls"),
        )
        require(
            manifest_cumulative == ledger["total_attempted"],
            "RESUME_AI_LEDGER_TOTAL_DIVERGED",
        )
        attempt_calls = [
            item
            for item in ledger["calls"]
            if isinstance(item, dict)
            and item.get("acceptance_id") == self.acceptance_id
        ]
        expected_attempt_calls = 1 if self.resume_after_call_16 else 0
        require(
            len(attempt_calls) == expected_attempt_calls,
            "RESUME_AI_LEDGER_DIVERGED",
        )
        if self.resume_after_call_16:
            require(
                attempt_calls[0].get("attempt_ordinal") == 1
                and attempt_calls[0].get("stage") == "generate_and_reject_healing"
                and attempt_calls[0].get("status") == "ATTEMPTED",
                "CALL_16_RECOVERY_LEDGER_MISMATCH",
            )

    def _validate_known_failed_healing_call(self) -> None:
        require(
            self.safe.ids.get("failed_case_run_id") == KNOWN_CALL_16_CASE_RUN_ID,
            "CALL_16_CASE_RUN_MISMATCH",
        )
        calls = self._api().request(
            "GET",
            "/ai/calls",
            params={"project_id": self.project_id, "task_type": "LOCATOR_HEALING"},
        )
        items = calls.get("items")
        require(isinstance(items, list), "CALL_16_LIST_INVALID")
        call = next(
            (
                item
                for item in items
                if isinstance(item, dict) and item.get("id") == KNOWN_CALL_16_ID
            ),
            None,
        )
        require(isinstance(call, dict), "CALL_16_NOT_FOUND")
        binding = self.model_bindings_by_task.get("LOCATOR_HEALING")
        require(isinstance(binding, dict), "CALL_16_MODEL_BINDING_MISSING")
        require(
            binding.get("id") in self.safe.ids["model_binding_ids"],
            "CALL_16_MODEL_BINDING_MISMATCH",
        )
        models = self._api().request("GET", "/model-center").get("items")
        require(isinstance(models, list), "CALL_16_MODEL_LIST_INVALID")
        expected_model = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and item.get("id") == binding.get("primary_model_id")
            ),
            None,
        )
        require(isinstance(expected_model, dict), "CALL_16_MODEL_NOT_FOUND")
        require(
            call.get("project_id") == self.project_id
            and call.get("task_type") == "LOCATOR_HEALING"
            and call.get("success") is True
            and call.get("retry_count") == 0
            and call.get("repair_used") is False
            and call.get("fallback_used") is False
            and call.get("error_type") is None
            and call.get("validation_errors") == []
            and call.get("entity_type") == "WEB_HEALING_PROPOSAL"
            and call.get("model_config_id") == binding.get("primary_model_id")
            and call.get("actual_model") == expected_model.get("model_name")
            and call.get("prompt_version_id")
            == self.safe.ids["prompt_version_ids"]["locator_healing"]
            and call.get("output_schema_id")
            == self.safe.ids["output_schema_ids"]["locator_healing"],
            "CALL_16_METADATA_MISMATCH",
        )
        parsed = call.get("parsed_result")
        require(isinstance(parsed, dict), "CALL_16_PARSED_RESULT_INVALID")
        require(
            parsed.get("evidence_candidate_index") == 2
            and parsed.get("locator")
            == {
                "strategy": "css",
                "value": "[data-testid='signin-confirm']",
            },
            "CALL_16_KNOWN_RESULT_MISMATCH",
        )
        require(
            call.get("entity_id") == self._known_call_16_legacy_source_digest(),
            "CALL_16_SOURCE_ENTITY_MISMATCH",
        )
        run_id = KNOWN_CALL_16_FAILED_RUN_ID
        proposals = self._api().request(
            "GET",
            f"/runs/{run_id}/web-healing-proposals",
            params={"case_run_id": self.safe.ids["failed_case_run_id"]},
        )
        require(
            proposals.get("total") == 0 and proposals.get("items") == [],
            "CALL_16_PROPOSAL_ALREADY_EXISTS",
        )

    def _known_call_16_legacy_source_digest(self) -> str:
        failed = self._api().request("GET", f"/runs/{KNOWN_CALL_16_FAILED_RUN_ID}")
        require(
            failed.get("id") == KNOWN_CALL_16_FAILED_RUN_ID
            and failed.get("project_id") == KNOWN_CALL_16_PROJECT_ID
            and failed.get("environment_id") == self.safe.ids["environment_id"]
            and failed.get("runner_id") == self.safe.ids["runner_id"]
            and failed.get("run_type") == "WEB_CASE"
            and failed.get("status") == "FAILED"
            and failed.get("web_case_id") == KNOWN_CALL_16_CASE_ID
            and failed.get("web_case_version_id") == KNOWN_CALL_16_CASE_VERSION_ID,
            "CALL_16_FIXED_RUN_MISMATCH",
        )
        case_runs = failed.get("case_runs")
        require(isinstance(case_runs, list), "CALL_16_CASE_RUNS_INVALID")
        case_run = next(
            (
                item
                for item in case_runs
                if isinstance(item, dict)
                and item.get("id") == KNOWN_CALL_16_CASE_RUN_ID
            ),
            None,
        )
        require(
            isinstance(case_run, dict)
            and case_run.get("status") == "FAILED"
            and case_run.get("web_case_version_id")
            == KNOWN_CALL_16_CASE_VERSION_ID,
            "CALL_16_CASE_RUN_MISMATCH",
        )
        traces = failed.get("web_traces")
        require(isinstance(traces, list), "CALL_16_TRACES_INVALID")
        trace = next(
            (
                item
                for item in traces
                if isinstance(item, dict) and item.get("node_id") == "action_1"
            ),
            None,
        )
        require(
            isinstance(trace, dict)
            and trace.get("status") == "FAILED"
            and trace.get("error_type") == "WEB_LOCATOR_NOT_FOUND",
            "CALL_16_TRACE_MISMATCH",
        )
        context = trace.get("healing_context")
        require(isinstance(context, dict), "CALL_16_HEALING_CONTEXT_INVALID")
        candidates = context.get("dom_candidates")
        require(
            isinstance(candidates, list)
            and len(candidates) > 2
            and isinstance(candidates[2], dict)
            and (
                candidates[2].get("data-testid")
                or candidates[2].get("data_testid")
            )
            == "signin-confirm",
            "CALL_16_CANDIDATE_SOURCE_MISMATCH",
        )

        case_versions = self._api().request(
            "GET",
            f"/web-cases/{KNOWN_CALL_16_CASE_ID}/versions",
            response_type=list,
        )
        source_case_version = next(
            (
                item
                for item in case_versions
                if isinstance(item, dict)
                and item.get("id") == KNOWN_CALL_16_CASE_VERSION_ID
            ),
            None,
        )
        require(
            isinstance(source_case_version, dict)
            and source_case_version.get("web_case_id") == KNOWN_CALL_16_CASE_ID
            and source_case_version.get("status") == "APPROVED",
            "CALL_16_CASE_VERSION_MISMATCH",
        )
        content = source_case_version.get("content")
        actions = content.get("actions") if isinstance(content, dict) else None
        require(
            isinstance(actions, list)
            and bool(actions)
            and isinstance(actions[0], dict),
            "CALL_16_SOURCE_ACTION_MISSING",
        )
        node = actions[0]
        locator = node.get("locator")
        require(
            node.get("type") == "CLICK"
            and isinstance(locator, dict)
            and locator.get("element_version_id")
            == KNOWN_CALL_16_ELEMENT_VERSION_ID,
            "CALL_16_SOURCE_LOCATOR_MISMATCH",
        )

        element_versions = self._api().request(
            "GET",
            f"/web-elements/{KNOWN_CALL_16_ELEMENT_ID}/versions",
            response_type=list,
        )
        source_element_version = next(
            (
                item
                for item in element_versions
                if isinstance(item, dict)
                and item.get("id") == KNOWN_CALL_16_ELEMENT_VERSION_ID
            ),
            None,
        )
        require(
            isinstance(source_element_version, dict)
            and source_element_version.get("element_id") == KNOWN_CALL_16_ELEMENT_ID,
            "CALL_16_ELEMENT_VERSION_MISMATCH",
        )
        raw_locators = source_element_version.get("locators")
        require(
            isinstance(raw_locators, list) and bool(raw_locators),
            "CALL_16_LOCATORS_INVALID",
        )
        old_locators: list[dict[str, Any]] = []
        for item in sorted(
            raw_locators,
            key=lambda value: (value.get("priority", 0), value.get("id", 0)),
        ):
            require(isinstance(item, dict), "CALL_16_LOCATOR_INVALID")
            old_locators.append(
                {
                    key: item[key]
                    for key in ("strategy", "value", "priority", "source")
                    if item.get(key) is not None
                }
            )

        audited_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            require(isinstance(candidate, dict), "CALL_16_CANDIDATE_INVALID")
            audited: dict[str, Any] = {}
            for output_key, input_keys in (
                ("tag", ("tag",)),
                ("role", ("role",)),
                ("id", ("id",)),
                ("name", ("name",)),
                ("aria-label", ("aria-label", "aria_label")),
                ("placeholder", ("placeholder",)),
                ("data-testid", ("data-testid", "data_testid")),
                ("type", ("type",)),
                ("title", ("title",)),
            ):
                value = next(
                    (
                        candidate[key]
                        for key in input_keys
                        if candidate.get(key) is not None
                    ),
                    None,
                )
                if value is not None:
                    audited[output_key] = value
            require("tag" in audited, "CALL_16_CANDIDATE_TAG_MISSING")
            audited_candidates.append(audited)

        snapshot = {
            "schema_version": 1,
            "run_id": KNOWN_CALL_16_FAILED_RUN_ID,
            "project_id": KNOWN_CALL_16_PROJECT_ID,
            "case_run_id": KNOWN_CALL_16_CASE_RUN_ID,
            "web_case_id": KNOWN_CALL_16_CASE_ID,
            "web_case_version_id": KNOWN_CALL_16_CASE_VERSION_ID,
            "node_id": "action_1",
            "node_type": node["type"],
            "old_locator": {
                "element_version_id": KNOWN_CALL_16_ELEMENT_VERSION_ID,
                "locators": old_locators,
            },
            "healing_context": {
                "schema_version": context.get("schema_version"),
                "trigger": context.get("trigger"),
                "element_version_id": context.get("element_version_id"),
                "page_url": context.get("page_url"),
                "page_title": context.get("page_title"),
                "dom_candidates": audited_candidates,
            },
        }
        healing_context = snapshot["healing_context"]
        require(
            healing_context["schema_version"] == 1
            and healing_context["trigger"] == "ALL_LOCATORS_FAILED"
            and healing_context["element_version_id"]
            == KNOWN_CALL_16_ELEMENT_VERSION_ID,
            "CALL_16_HEALING_CONTEXT_MISMATCH",
        )
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _select_runtime_context(self) -> None:
        projects = self._api().request("GET", "/projects").get("items")
        require(isinstance(projects, list), "PROJECT_LIST_INVALID")
        selected: tuple[dict[str, Any], dict[str, Any]] | None = None
        expected_project_id = (
            self.safe.ids.get("project_id") if self.resume_attempt else None
        )
        for project in projects:
            if not isinstance(project, dict) or not isinstance(project.get("id"), int):
                continue
            if expected_project_id is not None and project["id"] != expected_project_id:
                continue
            if project.get("status") not in (None, "ACTIVE"):
                continue
            bindings = self._api().request(
                "GET",
                "/model-center/bindings/project",
                params={"project_id": project["id"]},
            )
            items = bindings.get("items")
            if not isinstance(items, list):
                continue
            task_types = {
                item.get("task_type") for item in items if isinstance(item, dict)
            }
            if {"LOCATOR_HEALING", "WEB_FAILURE_ANALYSIS"}.issubset(task_types):
                selected = (project, bindings)
                break
        require(selected is not None, "PROJECT_WITH_REQUIRED_MODEL_BINDINGS_NOT_FOUND")
        project, bindings = selected
        self.project_id = project["id"]
        self.safe.ids["project_id"] = self.project_id
        binding_items = bindings["items"]
        self.model_bindings_by_task = {
            item["task_type"]: item
            for item in binding_items
            if isinstance(item, dict)
            and item.get("task_type")
            in {"LOCATOR_HEALING", "WEB_FAILURE_ANALYSIS"}
        }
        self.safe.ids["model_binding_ids"] = sorted(
            item["id"]
            for item in binding_items
            if item.get("task_type") in {"LOCATOR_HEALING", "WEB_FAILURE_ANALYSIS"}
        )

        environments = (
            self._api()
            .request("GET", "/environments", params={"project_id": self.project_id})
            .get("items")
        )
        require(isinstance(environments, list), "ENVIRONMENT_LIST_INVALID")
        enabled = [
            item
            for item in environments
            if isinstance(item, dict) and item.get("enabled")
        ]
        require(bool(enabled), "ENABLED_ENVIRONMENT_NOT_FOUND")
        if self.resume_attempt:
            expected_environment_id = self.safe.ids.get("environment_id")
            environment = next(
                (
                    item
                    for item in enabled
                    if item.get("id") == expected_environment_id
                ),
                None,
            )
            require(
                isinstance(environment, dict),
                "RESUME_ENVIRONMENT_NOT_ENABLED",
            )
        else:
            environment = next(
                (item for item in enabled if item.get("is_default")), enabled[0]
            )
        self.environment_id = environment["id"]
        self.safe.ids["environment_id"] = self.environment_id

        runners = self._api().request("GET", "/runners").get("items")
        require(isinstance(runners, list), "RUNNER_LIST_INVALID")
        runner = next(
            (
                item
                for item in runners
                if isinstance(item, dict) and item.get("id") == self.runner_id
            ),
            None,
        )
        require(isinstance(runner, dict), "READY_RUNNER_NOT_REGISTERED")
        require(runner.get("status") == "ACTIVE", "READY_RUNNER_NOT_ACTIVE")
        require(runner.get("online") is True, "READY_RUNNER_NOT_ONLINE")
        require(
            runner.get("online_status") == "ONLINE", "READY_RUNNER_STATUS_NOT_ONLINE"
        )
        capabilities = runner.get("capabilities")
        slots = runner.get("slots")
        require(isinstance(capabilities, list), "RUNNER_CAPABILITIES_INVALID")
        require(isinstance(slots, list), "RUNNER_SLOTS_INVALID")
        require(
            any(
                isinstance(item, dict)
                and item.get("name") == "WEB"
                and item.get("status") == "READY"
                for item in capabilities
            ),
            "RUNNER_WEB_CAPABILITY_NOT_READY",
        )
        require(
            any(
                isinstance(item, dict)
                and item.get("type") == "WEB"
                and item.get("total") == 1
                and item.get("available", 0) >= 1
                for item in slots
            ),
            "RUNNER_WEB_SLOT_NOT_AVAILABLE",
        )
        self.safe.ids["runner_id"] = self.runner_id
        self.safe.statuses["runner"] = "ONLINE"

        expected_prompt_ids = self.safe.ids.get("prompt_ids", {})
        require(isinstance(expected_prompt_ids, dict), "PROMPT_IDS_INVALID")
        self.healing_prompt = self._select_prompt(
            "LOCATOR_HEALING",
            expected_id=(
                expected_prompt_ids.get("locator_healing")
                if self.resume_attempt
                else None
            ),
        )
        self.analysis_prompt = self._select_prompt(
            "WEB_FAILURE_ANALYSIS",
            expected_id=(
                expected_prompt_ids.get("web_failure_analysis")
                if self.resume_attempt
                else None
            ),
        )
        self.safe.ids["prompt_ids"] = {
            "locator_healing": self.healing_prompt["id"],
            "web_failure_analysis": self.analysis_prompt["id"],
        }
        self.safe.ids["prompt_version_ids"] = {
            "locator_healing": self.healing_prompt["current_version_id"],
            "web_failure_analysis": self.analysis_prompt["current_version_id"],
        }
        self.safe.ids["output_schema_ids"] = {
            "locator_healing": self.healing_prompt["current_version"][
                "output_schema_id"
            ],
            "web_failure_analysis": self.analysis_prompt["current_version"][
                "output_schema_id"
            ],
        }

    def _select_prompt(
        self, task_type: str, *, expected_id: int | None = None
    ) -> dict[str, Any]:
        prompts = (
            self._api()
            .request("GET", "/prompt-center", params={"task_type": task_type})
            .get("items")
        )
        require(isinstance(prompts, list), f"{task_type}_PROMPT_LIST_INVALID")
        prompt = next(
            (
                item
                for item in prompts
                if isinstance(item, dict)
                and (expected_id is None or item.get("id") == expected_id)
                and item.get("enabled") is True
                and isinstance(item.get("current_version_id"), int)
                and isinstance(item.get("current_version"), dict)
                and isinstance(item["current_version"].get("output_schema_id"), int)
            ),
            None,
        )
        require(isinstance(prompt, dict), f"{task_type}_ENABLED_PROMPT_NOT_FOUND")
        return prompt

    def create_assets(self) -> tuple[int, int, int]:
        self.stage("create_test_assets")
        page = self._api().request(
            "POST",
            "/web-pages",
            payload={
                "project_id": self.project_id,
                "code": f"{self.acceptance_id}_LOGIN",
                "name": f"{self.acceptance_id} Login Page",
                "url_pattern": "http://127.0.0.1:8765/login",
                "description": "P5-E live acceptance synthetic asset",
            },
            expected=(201,),
        )
        page_id = page["id"]
        self.safe.ids["web_page_id"] = page_id

        element = self._api().request(
            "POST",
            "/web-elements",
            payload={
                "project_id": self.project_id,
                "page_id": page_id,
                "name": f"{self.acceptance_id} Login Button",
                "description": "P5-E live acceptance synthetic asset",
                "element_type": "BUTTON",
            },
            expected=(201,),
        )
        element_id = element["id"]
        self.safe.ids["web_element_id"] = element_id
        element_version = self._api().request(
            "POST",
            f"/web-elements/{element_id}/versions",
            payload={
                "description": "Original demo locator",
                "element_type": "BUTTON",
                "locators": [
                    {
                        "strategy": "css",
                        "value": "#login-btn",
                        "priority": 1,
                        "source": "MANUAL",
                    }
                ],
            },
            expected=(201,),
        )
        source_element_version_id = element_version["id"]
        self.safe.ids["source_element_version_id"] = source_element_version_id

        case = self._api().request(
            "POST",
            "/web-cases",
            payload={
                "project_id": self.project_id,
                "name": f"{self.acceptance_id} Empty Login Click",
                "change_note": "Create P5-E live acceptance case",
                "content": {
                    "start_url": "http://127.0.0.1:8765/login",
                    "actions": [
                        {
                            "type": "CLICK",
                            "locator": {
                                "element_version_id": source_element_version_id
                            },
                            "timeout_ms": 5000,
                            "failure_policy": "STOP",
                        }
                    ],
                    "natural_language_steps": ["Click the empty local demo login form"],
                    "assertions": [],
                    "browser": "CHROME",
                    "headless": True,
                    "total_timeout_ms": 60000,
                    "parameters": {},
                },
            },
            expected=(201,),
        )
        case_id = case["id"]
        source_case_version_id = case["current_version_id"]
        self.safe.ids["web_case_id"] = case_id
        self.safe.ids["source_web_case_version_id"] = source_case_version_id
        approved = self._api().request("POST", f"/web-cases/{case_id}/approve")
        require(approved.get("status") == "APPROVED", "SOURCE_WEB_CASE_APPROVAL_FAILED")
        require(
            approved.get("current_version_id") == source_case_version_id,
            "SOURCE_WEB_CASE_VERSION_CHANGED_ON_APPROVAL",
        )
        self.safe.statuses["source_web_case"] = "APPROVED"
        self.safe.links.update(
            {
                "web_case_api": f"{API_PREFIX}/web-cases/{case_id}",
                "web_element_versions_api": f"{API_PREFIX}/web-elements/{element_id}/versions",
            }
        )
        return case_id, source_case_version_id, source_element_version_id

    def _run_payload(self, case_id: int, case_version_id: int) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "runner_id": self.runner_id,
            "run_type": "WEB_CASE",
            "web_case_id": case_id,
            "web_case_version_id": case_version_id,
            "trigger_type": "MANUAL",
            "required_capabilities": ["WEB"],
            "required_tags": [],
            "required_slot_type": "WEB",
            "required_slot_count": 1,
            "total_timeout_ms": 120000,
        }

    def wait_for_runner_slot(self, label: str) -> None:
        deadline = time.monotonic() + min(self.run_timeout, 60.0)
        while time.monotonic() < deadline:
            runners = self._api().request("GET", "/runners").get("items")
            require(isinstance(runners, list), "RUNNER_LIST_INVALID_DURING_SLOT_WAIT")
            runner = next(
                (
                    item
                    for item in runners
                    if isinstance(item, dict) and item.get("id") == self.runner_id
                ),
                None,
            )
            require(isinstance(runner, dict), "RUNNER_MISSING_DURING_SLOT_WAIT")
            capabilities = runner.get("capabilities")
            slots = runner.get("slots")
            require(
                isinstance(capabilities, list),
                "RUNNER_CAPABILITIES_INVALID_DURING_WAIT",
            )
            require(isinstance(slots, list), "RUNNER_SLOTS_INVALID_DURING_WAIT")
            web_ready = any(
                isinstance(item, dict)
                and item.get("name") == "WEB"
                and item.get("status") == "READY"
                for item in capabilities
            )
            web_slot = next(
                (
                    item
                    for item in slots
                    if isinstance(item, dict) and item.get("type") == "WEB"
                ),
                None,
            )
            if (
                runner.get("status") == "ACTIVE"
                and runner.get("online") is True
                and runner.get("online_status") == "ONLINE"
                and web_ready
                and isinstance(web_slot, dict)
                and web_slot.get("total") == 1
                and web_slot.get("available") == 1
            ):
                self.safe.statuses[f"{label}_runner_slot"] = "READY"
                return
            time.sleep(self.poll_interval)
        raise AcceptanceFailure(f"{label.upper()}_RUNNER_SLOT_WAIT_TIMEOUT")

    def execute_run(
        self,
        label: str,
        case_id: int,
        case_version_id: int,
        expected_status: str,
    ) -> dict[str, Any]:
        self.stage(f"run_{label}")
        self.wait_for_runner_slot(label)
        payload = self._run_payload(case_id, case_version_id)
        validation = self._api().request("POST", "/runs/validate", payload=payload)
        if validation.get("valid") is not True:
            issues = validation.get("issues")
            codes = []
            if isinstance(issues, list):
                codes = [
                    str(item.get("code"))
                    for item in issues
                    if isinstance(item, dict)
                    and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(item.get("code")))
                ]
            self.safe.statuses[f"{label}_validation"] = (
                f"FAILED:{','.join(codes)}" if codes else "FAILED:UNKNOWN"
            )
        require(
            validation.get("valid") is True, f"{label.upper()}_RUN_VALIDATION_FAILED"
        )
        require(
            not validation.get("issues"), f"{label.upper()}_RUN_VALIDATION_HAS_ISSUES"
        )
        require(
            validation.get("resolved_web_case_version_id") == case_version_id,
            f"{label.upper()}_RUN_RESOLVED_VERSION_MISMATCH",
        )
        created = self._api().request("POST", "/runs", payload=payload, expected=(201,))
        run_id = created["id"]
        run_ids = self.safe.ids.setdefault("run_ids", {})
        run_ids[label] = run_id
        self.safe.links[f"{label}_run_api"] = f"{API_PREFIX}/runs/{run_id}"
        dispatched = self._api().request("POST", f"/runs/{run_id}/dispatch")
        require(
            dispatched.get("run_id") == run_id, f"{label.upper()}_DISPATCH_ID_MISMATCH"
        )
        require(
            dispatched.get("outbox_status") in {"PUBLISHED", "PENDING"},
            f"{label.upper()}_DISPATCH_NOT_PUBLISHED",
        )
        deadline = time.monotonic() + self.run_timeout
        detail = created
        while time.monotonic() < deadline:
            detail = self._api().request("GET", f"/runs/{run_id}")
            if detail.get("status") in TERMINAL_RUN_STATUSES:
                break
            time.sleep(self.poll_interval)
        require(
            detail.get("status") in TERMINAL_RUN_STATUSES,
            f"{label.upper()}_RUN_POLL_TIMEOUT",
        )
        require(
            detail.get("status") == expected_status,
            f"{label.upper()}_RUN_STATUS_UNEXPECTED",
        )
        require(
            detail.get("web_case_version_id") == case_version_id,
            f"{label.upper()}_VERSION_DRIFT",
        )
        self.safe.statuses[f"{label}_run"] = expected_status
        return detail

    def assert_failure_context(
        self, failed: dict[str, Any], source_element_version_id: int
    ) -> tuple[int, dict[str, Any]]:
        self.stage("verify_real_locator_failure")
        case_runs = failed.get("case_runs")
        traces = failed.get("web_traces")
        require(
            isinstance(case_runs, list) and len(case_runs) == 1,
            "FAILED_CASE_RUN_INVALID",
        )
        require(isinstance(traces, list), "FAILED_WEB_TRACES_INVALID")
        case_run = case_runs[0]
        require(case_run.get("status") == "FAILED", "FAILED_CASE_RUN_STATUS_INVALID")
        step_runs = case_run.get("step_runs")
        require(isinstance(step_runs, list), "FAILED_STEP_RUNS_INVALID")
        step = next(
            (item for item in step_runs if item.get("node_id") == "action_1"), None
        )
        require(isinstance(step, dict), "FAILED_ACTION_STEP_MISSING")
        require(step.get("status") == "FAILED", "FAILED_ACTION_STEP_STATUS_INVALID")
        require(
            step.get("error_type") == "WEB_LOCATOR_NOT_FOUND",
            "LOCATOR_ERROR_TYPE_MISMATCH",
        )
        trace = next(
            (item for item in traces if item.get("node_id") == "action_1"), None
        )
        require(isinstance(trace, dict), "FAILED_ACTION_TRACE_MISSING")
        require(trace.get("status") == "FAILED", "FAILED_ACTION_TRACE_STATUS_INVALID")
        require(
            trace.get("error_type") == "WEB_LOCATOR_NOT_FOUND",
            "TRACE_ERROR_TYPE_MISMATCH",
        )
        attempts = trace.get("locator_attempts")
        require(
            isinstance(attempts, list) and bool(attempts), "LOCATOR_ATTEMPTS_MISSING"
        )
        require(
            all(
                isinstance(item, dict) and item.get("status") == "NOT_FOUND"
                for item in attempts
            ),
            "LOCATOR_ATTEMPTS_NOT_ALL_NOT_FOUND",
        )
        context = trace.get("healing_context")
        require(isinstance(context, dict), "HEALING_CONTEXT_MISSING")
        require(
            context.get("trigger") == "ALL_LOCATORS_FAILED", "HEALING_TRIGGER_INVALID"
        )
        require(
            context.get("element_version_id") == source_element_version_id,
            "HEALING_ELEMENT_VERSION_MISMATCH",
        )
        candidates = context.get("dom_candidates")
        require(
            isinstance(candidates, list) and bool(candidates),
            "HEALING_CANDIDATES_MISSING",
        )
        require(
            any(
                item.get("id") == "signin-confirm"
                or item.get("data-testid") == "signin-confirm"
                for item in candidates
                if isinstance(item, dict)
            ),
            "CHANGED_LOGIN_CANDIDATE_MISSING",
        )
        page_url = context.get("page_url")
        require(isinstance(page_url, str), "HEALING_PAGE_URL_MISSING")
        parsed = urlsplit(page_url)
        require(
            parsed.hostname in {"127.0.0.1", "localhost"}, "HEALING_PAGE_URL_NOT_LOCAL"
        )
        require(
            parsed.port == 8765 and parsed.path == "/login", "HEALING_PAGE_URL_INVALID"
        )
        require(
            not parsed.query and not parsed.fragment, "HEALING_PAGE_URL_NOT_REDACTED"
        )
        require(_public_keys_are_safe(trace), "HEALING_TRACE_EXPOSES_FORBIDDEN_KEY")
        return case_run["id"], trace

    def evidence_for(self, label: str, run_id: str) -> list[dict[str, Any]]:
        evidence = self._api().request(
            "GET",
            "/evidence",
            params={"project_id": self.project_id, "run_id": run_id, "page_size": 100},
        )
        items = evidence.get("items")
        require(
            isinstance(items, list) and bool(items), f"{label.upper()}_EVIDENCE_MISSING"
        )
        types = {item.get("artifact_type") for item in items if isinstance(item, dict)}
        require("WEB_SUMMARY" in types, f"{label.upper()}_WEB_SUMMARY_MISSING")
        require("SCREENSHOT" in types, f"{label.upper()}_SCREENSHOT_MISSING")
        for item in items:
            require(
                item.get("run_id") == run_id, f"{label.upper()}_EVIDENCE_RUN_MISMATCH"
            )
            require(
                item.get("project_id") == self.project_id,
                f"{label.upper()}_EVIDENCE_PROJECT_MISMATCH",
            )
            digest = item.get("sha256")
            require(
                isinstance(digest, str) and len(digest) == 64,
                f"{label.upper()}_EVIDENCE_SHA_INVALID",
            )
        downloadable = next(
            item for item in items if item.get("artifact_type") == "WEB_SUMMARY"
        )
        content = self._api().get_bytes(f"/evidence/{downloadable['id']}/download")
        require(
            len(content) == downloadable.get("size"),
            f"{label.upper()}_EVIDENCE_SIZE_MISMATCH",
        )
        require(
            hashlib.sha256(content).hexdigest() == downloadable.get("sha256"),
            f"{label.upper()}_EVIDENCE_DIGEST_MISMATCH",
        )
        evidence_ids = self.safe.ids.setdefault("evidence_ids", {})
        evidence_ids[label] = [item["id"] for item in items]
        self.safe.counts[f"{label}_evidence"] = len(items)
        return items

    def _assert_ai_traceability(
        self, body: dict[str, Any], prompt: dict[str, Any], label: str
    ) -> None:
        require(isinstance(body.get("ai_call_id"), int), f"{label}_AI_CALL_ID_MISSING")
        require(
            isinstance(body.get("actual_model"), str), f"{label}_ACTUAL_MODEL_MISSING"
        )
        require(bool(body.get("actual_model")), f"{label}_ACTUAL_MODEL_EMPTY")
        require(
            body.get("prompt_version_id") == prompt["current_version_id"],
            f"{label}_PROMPT_VERSION_MISMATCH",
        )
        require(
            body.get("output_schema_id")
            == prompt["current_version"]["output_schema_id"],
            f"{label}_OUTPUT_SCHEMA_MISMATCH",
        )
        require(
            isinstance(body.get("source_snapshot_sha256"), str)
            and len(body["source_snapshot_sha256"]) == 64,
            f"{label}_SOURCE_DIGEST_INVALID",
        )
        require(body.get("source_snapshot_size", 0) > 0, f"{label}_SOURCE_EMPTY")
        require(_public_keys_are_safe(body), f"{label}_PUBLIC_RESPONSE_UNSAFE")

    def healing_flow(
        self,
        *,
        failed_run: dict[str, Any],
        case_run_id: int,
        case_id: int,
        source_case_version_id: int,
        source_element_version_id: int,
    ) -> tuple[int, int]:
        run_id = failed_run["id"]
        create_payload = {
            "case_run_id": case_run_id,
            "node_id": "action_1",
            "prompt_id": self.healing_prompt["id"],
        }

        self.stage("generate_and_reject_healing")
        before_case = self._api().request("GET", f"/web-cases/{case_id}")
        before_versions = self._api().request(
            "GET", f"/web-cases/{case_id}/versions", response_type=list
        )
        self._count_ai_call()
        first = self._api().request(
            "POST",
            f"/runs/{run_id}/web-healing-proposals",
            payload=create_payload,
            timeout=self.ai_timeout,
        )
        self._assert_ai_traceability(first, self.healing_prompt, "FIRST_HEALING")
        require(first.get("status") == "DRAFT", "FIRST_HEALING_NOT_DRAFT")
        require(first.get("web_case_id") == case_id, "FIRST_HEALING_CASE_MISMATCH")
        require(
            first.get("web_case_version_id") == source_case_version_id,
            "FIRST_HEALING_VERSION_MISMATCH",
        )
        require(
            first.get("old_locator", {}).get("element_version_id")
            == source_element_version_id,
            "FIRST_HEALING_OLD_LOCATOR_MISMATCH",
        )
        proposed = first.get("proposed_locator")
        candidate_locators = first.get("candidate_locators")
        require(isinstance(proposed, dict), "FIRST_HEALING_PROPOSAL_MISSING")
        require(
            isinstance(candidate_locators, list) and bool(candidate_locators),
            "HEALING_WHITELIST_MISSING",
        )
        require(
            any(item.get("locator") == proposed for item in candidate_locators),
            "FIRST_HEALING_PROPOSAL_NOT_WHITELISTED",
        )
        rejected = self._api().request(
            "POST",
            f"/runs/{run_id}/web-healing-proposals/{first['id']}/reject",
            payload={"decision_note": "V1 P5-E synthetic rejection check"},
        )
        require(rejected.get("status") == "REJECTED", "FIRST_HEALING_REJECT_FAILED")
        after_reject_case = self._api().request("GET", f"/web-cases/{case_id}")
        after_reject_versions = self._api().request(
            "GET", f"/web-cases/{case_id}/versions", response_type=list
        )
        require(
            before_case.get("status") == after_reject_case.get("status") == "APPROVED",
            "REJECT_CHANGED_CASE_STATUS",
        )
        require(
            before_case.get("current_version_id")
            == after_reject_case.get("current_version_id")
            == source_case_version_id,
            "REJECT_CHANGED_CURRENT_VERSION",
        )
        require(
            [item["id"] for item in before_versions]
            == [item["id"] for item in after_reject_versions],
            "REJECT_CREATED_CASE_VERSION",
        )

        self.stage("generate_and_accept_healing")
        runs_before = self._api().request(
            "GET", "/runs", params={"project_id": self.project_id, "page_size": 100}
        )
        self._count_ai_call()
        second = self._api().request(
            "POST",
            f"/runs/{run_id}/web-healing-proposals",
            payload=create_payload,
            timeout=self.ai_timeout,
        )
        self._assert_ai_traceability(second, self.healing_prompt, "SECOND_HEALING")
        require(second.get("id") != first.get("id"), "SECOND_HEALING_NOT_NEW")
        require(second.get("status") == "DRAFT", "SECOND_HEALING_NOT_DRAFT")
        second_proposed = second.get("proposed_locator")
        second_candidates = second.get("candidate_locators")
        require(isinstance(second_proposed, dict), "SECOND_HEALING_PROPOSAL_MISSING")
        require(
            isinstance(second_candidates, list) and bool(second_candidates),
            "SECOND_WHITELIST_MISSING",
        )
        require(
            any(item.get("locator") == second_proposed for item in second_candidates),
            "SECOND_HEALING_PROPOSAL_NOT_WHITELISTED",
        )
        accepted = self._api().request(
            "POST",
            f"/runs/{run_id}/web-healing-proposals/{second['id']}/accept",
            payload={"decision_note": "V1 P5-E synthetic acceptance check"},
        )
        require(accepted.get("status") == "ACCEPTED", "SECOND_HEALING_ACCEPT_FAILED")
        healed_element_version_id = accepted.get("created_element_version_id")
        healed_case_version_id = accepted.get("created_web_case_version_id")
        require(
            isinstance(healed_element_version_id, int),
            "HEALED_ELEMENT_VERSION_NOT_CREATED",
        )
        require(
            isinstance(healed_case_version_id, int), "HEALED_CASE_VERSION_NOT_CREATED"
        )
        self.safe.ids["healing_proposal_ids"] = {
            "rejected": first["id"],
            "accepted": second["id"],
        }
        self.safe.ids["healed_element_version_id"] = healed_element_version_id
        self.safe.ids["healed_web_case_version_id"] = healed_case_version_id
        self.safe.ids["healing_ai_call_ids"] = [
            first["ai_call_id"],
            second["ai_call_id"],
        ]

        draft_case = self._api().request("GET", f"/web-cases/{case_id}")
        versions = self._api().request(
            "GET", f"/web-cases/{case_id}/versions", response_type=list
        )
        healed_version = next(
            (item for item in versions if item.get("id") == healed_case_version_id),
            None,
        )
        source_version = next(
            (item for item in versions if item.get("id") == source_case_version_id),
            None,
        )
        require(draft_case.get("status") == "DRAFT", "ACCEPT_DID_NOT_LEAVE_CASE_DRAFT")
        require(
            draft_case.get("current_version_id") == healed_case_version_id,
            "ACCEPT_CURRENT_VERSION_MISMATCH",
        )
        require(isinstance(healed_version, dict), "HEALED_CASE_VERSION_MISSING")
        require(
            healed_version.get("status") == "DRAFT", "HEALED_CASE_VERSION_NOT_DRAFT"
        )
        require(
            healed_version.get("approved_by") is None,
            "HEALED_CASE_VERSION_AUTO_APPROVED",
        )
        require(
            healed_version.get("approved_at") is None,
            "HEALED_CASE_VERSION_HAS_APPROVAL_TIME",
        )
        require(isinstance(source_version, dict), "SOURCE_CASE_VERSION_MISSING")
        require(
            source_version.get("status") == "APPROVED", "SOURCE_CASE_VERSION_MUTATED"
        )
        runs_after = self._api().request(
            "GET", "/runs", params={"project_id": self.project_id, "page_size": 100}
        )
        require(
            runs_before.get("total") == runs_after.get("total"),
            "HEALING_ACCEPT_AUTO_CREATED_RUN",
        )

        approved = self._api().request("POST", f"/web-cases/{case_id}/approve")
        require(approved.get("status") == "APPROVED", "HEALED_CASE_APPROVAL_FAILED")
        require(
            approved.get("current_version_id") == healed_case_version_id,
            "HEALED_CASE_APPROVAL_VERSION_MISMATCH",
        )
        versions_after_approval = self._api().request(
            "GET", f"/web-cases/{case_id}/versions", response_type=list
        )
        approved_healed = next(
            (
                item
                for item in versions_after_approval
                if item.get("id") == healed_case_version_id
            ),
            None,
        )
        require(
            isinstance(approved_healed, dict)
            and approved_healed.get("status") == "APPROVED",
            "HEALED_CASE_VERSION_APPROVAL_FAILED",
        )
        element_versions = self._api().request(
            "GET",
            f"/web-elements/{self.safe.ids['web_element_id']}/versions",
            response_type=list,
        )
        require(
            any(
                item.get("id") == healed_element_version_id for item in element_versions
            ),
            "HEALED_ELEMENT_VERSION_MISSING",
        )
        self.safe.statuses.update(
            {
                "rejected_healing_proposal": "REJECTED",
                "accepted_healing_proposal": "ACCEPTED",
                "healed_web_case": "APPROVED",
            }
        )
        return healed_case_version_id, healed_element_version_id

    def failure_analysis(
        self,
        *,
        failed_run_id: str,
        failed_case_run_id: int,
        failed_evidence: list[dict[str, Any]],
    ) -> None:
        self.stage("generate_failure_analysis")
        self._count_ai_call()
        analysis = self._api().request(
            "POST",
            f"/runs/{failed_run_id}/web-failure-analyses",
            payload={
                "case_run_id": failed_case_run_id,
                "prompt_id": self.analysis_prompt["id"],
            },
            timeout=self.ai_timeout,
        )
        self._assert_ai_traceability(analysis, self.analysis_prompt, "FAILURE_ANALYSIS")
        require(analysis.get("status") == "COMPLETED", "FAILURE_ANALYSIS_NOT_COMPLETED")
        require(
            analysis.get("run_id") == failed_run_id, "FAILURE_ANALYSIS_RUN_MISMATCH"
        )
        require(
            analysis.get("case_run_id") == failed_case_run_id,
            "FAILURE_ANALYSIS_CASE_RUN_MISMATCH",
        )
        structured = analysis.get("structured_result")
        require(isinstance(structured, dict), "FAILURE_ANALYSIS_RESULT_MISSING")
        require(
            structured.get("failure_category") in {"LOCATOR_NOT_FOUND", "PAGE_CHANGED"},
            "FAILURE_ANALYSIS_CATEGORY_UNEXPECTED",
        )
        evidence_node_ids = structured.get("evidence_node_ids")
        require(
            isinstance(evidence_node_ids, list) and "action_1" in evidence_node_ids,
            "FAILURE_ANALYSIS_NODE_REFERENCE_MISSING",
        )
        require(bool(failed_evidence), "FAILURE_ANALYSIS_EVIDENCE_NOT_AVAILABLE")
        history = self._api().request(
            "GET",
            f"/runs/{failed_run_id}/web-failure-analyses",
            params={"case_run_id": failed_case_run_id},
        )
        items = history.get("items")
        require(isinstance(items, list), "FAILURE_ANALYSIS_HISTORY_INVALID")
        require(
            any(item.get("id") == analysis.get("id") for item in items),
            "FAILURE_ANALYSIS_HISTORY_MISSING",
        )
        self.safe.ids["failure_analysis_id"] = analysis["id"]
        self.safe.ids["failure_analysis_ai_call_id"] = analysis["ai_call_id"]
        self.safe.statuses["failure_analysis"] = "COMPLETED"
        self.safe.counts["failure_analysis_history"] = history.get("total", 0)
        self.safe.links["failure_analysis_api"] = (
            f"{API_PREFIX}/runs/{failed_run_id}/web-failure-analyses"
        )

    def _poll_existing_failed_run(
        self, *, case_id: int, source_case_version_id: int
    ) -> dict[str, Any]:
        self.stage("resume_failed_old_locator")
        run_ids = self.safe.ids.get("run_ids")
        require(isinstance(run_ids, dict), "RESUME_RUN_IDS_INVALID")
        run_id = run_ids.get("failed_old_locator")
        require(isinstance(run_id, str), "RESUME_FAILED_RUN_ID_INVALID")
        deadline = time.monotonic() + self.run_timeout
        detail: dict[str, Any] = {}
        while time.monotonic() < deadline:
            detail = self._api().request("GET", f"/runs/{run_id}")
            if detail.get("status") in TERMINAL_RUN_STATUSES:
                break
            time.sleep(self.poll_interval)
        require(
            detail.get("status") in TERMINAL_RUN_STATUSES,
            "RESUME_FAILED_OLD_LOCATOR_RUN_POLL_TIMEOUT",
        )
        require(
            detail.get("status") == "FAILED",
            "RESUME_FAILED_OLD_LOCATOR_RUN_STATUS_UNEXPECTED",
        )
        require(
            detail.get("web_case_id") == case_id,
            "RESUME_FAILED_RUN_CASE_MISMATCH",
        )
        require(
            detail.get("web_case_version_id") == source_case_version_id,
            "RESUME_FAILED_RUN_VERSION_DRIFT",
        )
        self.safe.statuses["failed_old_locator_run"] = "FAILED"
        return detail

    def _resume_from_failed_run(self) -> None:
        self.stage("verify_resume_assets")
        case_id = self.safe.ids["web_case_id"]
        source_case_version_id = self.safe.ids["source_web_case_version_id"]
        source_element_version_id = self.safe.ids["source_element_version_id"]
        case = self._api().request("GET", f"/web-cases/{case_id}")
        require(case.get("project_id") == self.project_id, "RESUME_CASE_PROJECT_MISMATCH")
        require(case.get("status") == "APPROVED", "RESUME_SOURCE_CASE_NOT_APPROVED")
        require(
            case.get("current_version_id") == source_case_version_id,
            "RESUME_SOURCE_CASE_VERSION_DRIFT",
        )
        element_versions = self._api().request(
            "GET",
            f"/web-elements/{self.safe.ids['web_element_id']}/versions",
            response_type=list,
        )
        require(
            any(
                isinstance(item, dict)
                and item.get("id") == source_element_version_id
                for item in element_versions
            ),
            "RESUME_SOURCE_ELEMENT_VERSION_MISSING",
        )

        run_ids = self.safe.ids["run_ids"]
        baseline = self._api().request("GET", f"/runs/{run_ids['baseline']}")
        require(baseline.get("status") == "SUCCESS", "RESUME_BASELINE_NOT_SUCCESS")
        require(
            baseline.get("web_case_id") == case_id
            and baseline.get("web_case_version_id") == source_case_version_id,
            "RESUME_BASELINE_BINDING_MISMATCH",
        )
        self.evidence_for("baseline", baseline["id"])

        failed = self._poll_existing_failed_run(
            case_id=case_id,
            source_case_version_id=source_case_version_id,
        )
        failed_case_run_id, _ = self.assert_failure_context(
            failed, source_element_version_id
        )
        self.safe.ids["failed_case_run_id"] = failed_case_run_id
        failed_evidence = self.evidence_for("failed_old_locator", failed["id"])

        healed_case_version_id, _ = self.healing_flow(
            failed_run=failed,
            case_run_id=failed_case_run_id,
            case_id=case_id,
            source_case_version_id=source_case_version_id,
            source_element_version_id=source_element_version_id,
        )
        healed = self.execute_run(
            "healed", case_id, healed_case_version_id, expected_status="SUCCESS"
        )
        self.evidence_for("healed", healed["id"])
        self.failure_analysis(
            failed_run_id=failed["id"],
            failed_case_run_id=failed_case_run_id,
            failed_evidence=failed_evidence,
        )
        self._complete_acceptance()

    def _complete_acceptance(self) -> None:
        expected_ai_calls = 4 if self.resume_after_call_16 else 3
        require(
            self.ai_calls == expected_ai_calls,
            "AI_BUSINESS_CALL_COUNT_UNEXPECTED",
        )
        self.safe.counts["synthetic_web_assets"] = 5
        self.safe.counts["synthetic_runs"] = 3
        self.safe.counts["healing_proposals"] = 2
        self.safe.counts["failure_analyses"] = 1
        self.safe.status = "PASSED"
        self.stage("complete")
        self.safe.status = "PASSED"
        self.write_result()
        print(f"[complete] PASSED {self.acceptance_id}", flush=True)

    def run(self) -> None:
        self.prepare()
        if self.resume_attempt:
            self._resume_from_failed_run()
            return
        case_id, source_case_version_id, source_element_version_id = (
            self.create_assets()
        )

        baseline = self.execute_run(
            "baseline", case_id, source_case_version_id, expected_status="SUCCESS"
        )
        self.evidence_for("baseline", baseline["id"])

        self.stage("change_demo_locator")
        changed = self._demo().request(
            "POST", "/control/locator", payload={"changed": True}, authenticated=False
        )
        require(changed.get("changed") is True, "DEMO_LOCATOR_CHANGE_FAILED")
        self.safe.statuses["demo_locator"] = "CHANGED"

        failed = self.execute_run(
            "failed_old_locator",
            case_id,
            source_case_version_id,
            expected_status="FAILED",
        )
        failed_case_run_id, _ = self.assert_failure_context(
            failed, source_element_version_id
        )
        self.safe.ids["failed_case_run_id"] = failed_case_run_id
        failed_evidence = self.evidence_for("failed_old_locator", failed["id"])

        healed_case_version_id, _ = self.healing_flow(
            failed_run=failed,
            case_run_id=failed_case_run_id,
            case_id=case_id,
            source_case_version_id=source_case_version_id,
            source_element_version_id=source_element_version_id,
        )

        healed = self.execute_run(
            "healed", case_id, healed_case_version_id, expected_status="SUCCESS"
        )
        self.evidence_for("healed", healed["id"])

        self.failure_analysis(
            failed_run_id=failed["id"],
            failed_case_run_id=failed_case_run_id,
            failed_evidence=failed_evidence,
        )
        self._complete_acceptance()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready-file", type=Path, default=DEFAULT_READY_FILE)
    parser.add_argument("--result-file", type=Path, default=DEFAULT_RESULT_FILE)
    parser.add_argument("--ordinary-timeout", type=float, default=20.0)
    parser.add_argument("--ai-timeout", type=float, default=150.0)
    parser.add_argument("--run-timeout", type=float, default=150.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    attempt_mode = parser.add_mutually_exclusive_group()
    attempt_mode.add_argument(
        "--new-attempt",
        action="store_true",
        help="Explicitly preserve the prior manifest and start a new bounded attempt.",
    )
    attempt_mode.add_argument(
        "--resume",
        action="store_true",
        help="Resume the current failed pre-AI attempt without creating assets or a Run.",
    )
    attempt_mode.add_argument(
        "--resume-after-call-16",
        action="store_true",
        help="Continue only the audited Call 16 whitelist rejection with a 4-call budget.",
    )
    args = parser.parse_args()
    require(1 <= args.ordinary_timeout <= 60, "ORDINARY_TIMEOUT_OUT_OF_RANGE")
    require(30 <= args.ai_timeout <= 300, "AI_TIMEOUT_OUT_OF_RANGE")
    require(30 <= args.run_timeout <= 300, "RUN_TIMEOUT_OUT_OF_RANGE")
    require(0.25 <= args.poll_interval <= 10, "POLL_INTERVAL_OUT_OF_RANGE")
    args.ready_file = args.ready_file.resolve()
    args.result_file = args.result_file.resolve()
    require(args.ready_file == DEFAULT_READY_FILE.resolve(), "READY_FILE_SCOPE_INVALID")
    require(
        args.result_file == DEFAULT_RESULT_FILE.resolve(), "RESULT_FILE_SCOPE_INVALID"
    )
    return args


def main() -> int:
    try:
        args = parse_args()
        acceptance = LiveAcceptance(
            ready_file=args.ready_file,
            result_file=args.result_file,
            ordinary_timeout=args.ordinary_timeout,
            ai_timeout=args.ai_timeout,
            run_timeout=args.run_timeout,
            poll_interval=args.poll_interval,
            allow_new_attempt=args.new_attempt,
            resume_attempt=args.resume,
            resume_after_call_16=args.resume_after_call_16,
        )
    except AcceptanceFailure as exc:
        print(f"[arguments] FAILED {exc.code}", file=sys.stderr, flush=True)
        return 2
    try:
        acceptance.run()
    except AcceptanceFailure as exc:
        acceptance.safe.status = "FAILED"
        acceptance.safe.statuses["error_code"] = exc.code
        if exc.code != "NEW_ATTEMPT_CONFIRMATION_REQUIRED":
            acceptance.write_result()
        print(
            f"[{acceptance.safe.stage}] FAILED {exc.code}", file=sys.stderr, flush=True
        )
        return 1
    except Exception:  # noqa: BLE001 - fail closed without exposing exception details
        acceptance.safe.status = "FAILED"
        acceptance.safe.statuses["error_code"] = "UNEXPECTED_ERROR"
        acceptance.write_result()
        print(
            f"[{acceptance.safe.stage}] FAILED UNEXPECTED_ERROR",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
