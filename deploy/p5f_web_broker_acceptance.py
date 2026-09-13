"""P5F-Q1 isolated RabbitMQ reconnect acceptance for one real Web Runner.

This is an acceptance harness, not a Backend integration test.  It starts a
loopback-only HTTP fixture that implements the strict Runner protocol in
memory, a loopback test site, and one uniquely named RabbitMQ container.  The
actual Pika broker adapter, RunnerWorker, RunnerTaskConsumer, RunnerClient,
spawn isolation, Playwright, and installed Chrome are used without mocks.

The harness never prints or persists the generated RabbitMQ credentials.  It
records ownership before starting Runner/browser work and removes only the
container whose exact name and ID it created.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit

if os.name == "nt":
    from ctypes import wintypes


WORKSPACE = Path(__file__).resolve().parents[1]
RUNNER_ROOT = WORKSPACE / "runner"
VALIDATION_ROOT = WORKSPACE / ".codex-validation" / "p5f-web-broker"
OWNERSHIP_PATH = VALIDATION_ROOT / "ownership.json"
RESULT_PATH = VALIDATION_ROOT / "result.json"
RABBIT_IMAGE = "rabbitmq:4.1-management-alpine"
MAX_HTTP_BODY_BYTES = 64 * 1024 * 1024

sys.path.insert(0, str(RUNNER_ROOT))

from runner.broker import PikaBroker, RabbitMqConfig
from runner.config import RunnerConfig
from runner.consumer import RunnerTaskConsumer
from runner.isolation import spawn_web_task_process
from runner.lifecycle import HeartbeatService
from runner.models import RunnerIdentity
from runner.protocol import RunnerClient
from runner.slots import SlotState
from runner.topology import (
    DEAD_QUEUE,
    PERSISTENT_DELIVERY_MODE,
    TASK_EXCHANGE,
    runner_queue_name,
    runner_routing_key,
)
from runner.transport import HttpxTransport
from runner.worker import (
    RabbitMqReconnectPolicy,
    RunnerWorker,
    WorkerResources,
)


class AcceptanceFailure(RuntimeError):
    """Expected, credential-free acceptance failure."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def wait_until(
    predicate: Any,
    *,
    timeout_seconds: float,
    label: str,
    interval_seconds: float = 0.1,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_seconds)
    raise AcceptanceFailure(f"TIMEOUT_{label}")


def process_inventory() -> dict[int, tuple[int, str]]:
    if os.name != "nt":
        raise AcceptanceFailure("WINDOWS_PROCESS_INVENTORY_REQUIRED")

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise AcceptanceFailure("PROCESS_SNAPSHOT_FAILED")
    inventory: dict[int, tuple[int, str]] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        if process_first(snapshot, ctypes.byref(entry)):
            while True:
                inventory[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile),
                )
                if not process_next(snapshot, ctypes.byref(entry)):
                    break
    finally:
        close_handle(snapshot)
    return inventory


def process_details(pid: int) -> dict[str, Any]:
    inventory = process_inventory()
    parent_pid, name = inventory.get(pid, (0, "unknown"))
    details: dict[str, Any] = {
        "pid": pid,
        "parent_pid": parent_pid,
        "name": name,
        "creation_time": None,
        "executable_path": None,
    }
    if os.name != "nt" or pid not in inventory:
        return details
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    get_process_times.restype = wintypes.BOOL
    query_image = kernel32.QueryFullProcessImageNameW
    query_image.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    query_image.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(0x1000, False, pid)
    if not handle:
        return details
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if get_process_times(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            unix_seconds = (ticks - 116_444_736_000_000_000) / 10_000_000
            details["creation_time"] = (
                datetime.fromtimestamp(unix_seconds, UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if query_image(handle, 0, buffer, ctypes.byref(size)):
            details["executable_path"] = buffer.value
    finally:
        close_handle(handle)
    return details


def descendants(root_pid: int, inventory: dict[int, tuple[int, str]]) -> set[int]:
    result: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children = {pid for pid, (ppid, _name) in inventory.items() if ppid == parent}
        new_children = children - result
        result.update(new_children)
        pending.extend(new_children)
    return result


def chrome_pids(inventory: dict[int, tuple[int, str]]) -> set[int]:
    return {
        pid
        for pid, (_parent_pid, name) in inventory.items()
        if name.lower() in {"chrome.exe", "chromium.exe"}
    }


def web_temp_dirs() -> set[str]:
    temp_root = Path(tempfile.gettempdir())
    patterns = ("ai-test-runner-web-*", "playwright_chromiumdev_profile-*")
    return {
        str(path.resolve())
        for pattern in patterns
        for path in temp_root.glob(pattern)
        if path.is_dir()
    }


@dataclass
class BackendRun:
    run_id: str
    message_id: str
    case_run_id: int
    start_url: str
    claim_count: int = 0
    plan_count: int = 0
    start_count: int = 0
    cancellation_count: int = 0
    evidence_count: int = 0
    completion_count: int = 0
    terminal: bool = False
    completion_outcome: str | None = None
    completed: Event = field(default_factory=Event)
    duplicate_claim: Event = field(default_factory=Event)


class BackendState:
    def __init__(
        self,
        runner_id: str,
        credential: str,
        runs: dict[str, BackendRun],
    ) -> None:
        self.runner_id = runner_id
        self.credential = credential
        self.runs = runs
        self.heartbeat_count = 0
        self.evidence_types: dict[str, list[str]] = {run_id: [] for run_id in runs}
        self.lock = Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "heartbeat_count": self.heartbeat_count,
                "runs": {
                    run_id: {
                        "message_id": run.message_id,
                        "claim_count": run.claim_count,
                        "plan_count": run.plan_count,
                        "start_count": run.start_count,
                        "cancellation_count": run.cancellation_count,
                        "evidence_count": run.evidence_count,
                        "completion_count": run.completion_count,
                        "terminal": run.terminal,
                        "completion_outcome": run.completion_outcome,
                        "evidence_types": list(self.evidence_types[run_id]),
                    }
                    for run_id, run in self.runs.items()
                },
            }


class BackendServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: BackendState) -> None:
        super().__init__(("127.0.0.1", 0), BackendHandler)
        self.state = state


class BackendHandler(BaseHTTPRequestHandler):
    server: BackendServer

    def do_GET(self) -> None:
        if not self._authorized():
            self._send_json(403, {"detail": "forbidden"})
            return
        parsed = urlsplit(self.path)
        run, suffix = self._resolve_run(parsed.path)
        if run is None:
            self._send_json(404, {"detail": "not found"})
            return
        query = parse_qs(parsed.query)
        if query.get("message_id") != [run.message_id]:
            self._send_json(422, {"detail": "message mismatch"})
            return
        if suffix == "web-execution-plan":
            with self.server.state.lock:
                run.plan_count += 1
            self._send_json(200, self._plan(run))
            return
        if suffix == "execution-cancellation":
            if query.get("case_run_id") != [str(run.case_run_id)]:
                self._send_json(422, {"detail": "case mismatch"})
                return
            with self.server.state.lock:
                run.cancellation_count += 1
            self._send_json(
                200,
                {
                    "schema_version": 1,
                    "run_id": run.run_id,
                    "message_id": run.message_id,
                    "case_run_id": run.case_run_id,
                    "status": "RUNNING",
                    "cancellation_requested": False,
                    "force_stop_requested": False,
                },
            )
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json(403, {"detail": "forbidden"})
            return
        parsed = urlsplit(self.path)
        if parsed.path == (
            f"/api/v1/runners/{self.server.state.runner_id}/heartbeat"
        ):
            self._read_json()
            with self.server.state.lock:
                self.server.state.heartbeat_count += 1
            self._send_json(
                200,
                {
                    "runner_id": self.server.state.runner_id,
                    "status": "ACTIVE",
                    "heartbeat_interval_seconds": 5,
                    "server_time": utc_now(),
                },
            )
            return
        run, suffix = self._resolve_run(parsed.path)
        if run is None:
            self._send_json(404, {"detail": "not found"})
            return
        if suffix == "claim":
            self._claim(run)
            return
        if suffix == "web-execution-start":
            self._web_start(run)
            return
        if suffix == "web-evidence":
            self._web_evidence(run)
            return
        if suffix == "web-execution-complete":
            self._web_complete(run)
            return
        self._send_json(404, {"detail": "not found"})

    def _claim(self, run: BackendRun) -> None:
        body = self._read_json()
        if body != {"message_id": run.message_id}:
            self._send_json(422, {"detail": "claim mismatch"})
            return
        with self.server.state.lock:
            run.claim_count += 1
            if run.terminal:
                run.duplicate_claim.set()
                self._send_json(409, {"detail": "run already terminal"})
                return
            idempotent = run.claim_count > 1
        self._send_json(
            200,
            {
                "run_id": run.run_id,
                "message_id": run.message_id,
                "runner_id": self.server.state.runner_id,
                "status": "ASSIGNED",
                "claimed_at": utc_now(),
                "idempotent": idempotent,
            },
        )

    def _web_start(self, run: BackendRun) -> None:
        body = self._read_json()
        if body != {"message_id": run.message_id, "case_run_id": run.case_run_id}:
            self._send_json(422, {"detail": "start mismatch"})
            return
        with self.server.state.lock:
            run.start_count += 1
        self._send_json(
            200,
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "message_id": run.message_id,
                "runner_id": self.server.state.runner_id,
                "case_run_id": run.case_run_id,
                "status": "RUNNING",
                "started_at": utc_now(),
                "idempotent": False,
            },
        )

    def _web_evidence(self, run: BackendRun) -> None:
        content_type = self.headers.get("Content-Type", "")
        body = self._read_body()
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii", errors="strict")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        fields: dict[str, str] = {}
        file_bytes: bytes | None = None
        file_name: str | None = None
        mime: str | None = None
        if not message.is_multipart():
            self._send_json(422, {"detail": "multipart required"})
            return
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            if name == "file":
                file_bytes = payload
                file_name = part.get_filename()
                mime = part.get_content_type()
            elif isinstance(name, str):
                fields[name] = payload.decode("utf-8")
        required = {
            "message_id",
            "case_run_id",
            "artifact_type",
            "artifact_name",
            "sha256",
            "metadata_json",
        }
        if (
            set(fields) != required
            or fields.get("message_id") != run.message_id
            or fields.get("case_run_id") != str(run.case_run_id)
            or file_bytes is None
            or not file_name
            or not mime
            or hashlib.sha256(file_bytes).hexdigest() != fields.get("sha256")
        ):
            self._send_json(422, {"detail": "evidence mismatch"})
            return
        try:
            metadata = json.loads(fields["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            self._send_json(422, {"detail": "metadata mismatch"})
            return
        with self.server.state.lock:
            run.evidence_count += 1
            self.server.state.evidence_types[run.run_id].append(
                fields["artifact_type"]
            )
            evidence_number = run.evidence_count
        self._send_json(
            200,
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "message_id": run.message_id,
                "runner_id": self.server.state.runner_id,
                "case_run_id": run.case_run_id,
                "id": f"ev-{run.case_run_id}-{evidence_number}",
                "artifact_type": fields["artifact_type"],
                "artifact_name": fields["artifact_name"],
                "file_name": file_name,
                "mime": mime,
                "size": len(file_bytes),
                "sha256": fields["sha256"],
                "metadata": metadata,
                "created_at": utc_now(),
                "idempotent": False,
            },
        )

    def _web_complete(self, run: BackendRun) -> None:
        body = self._read_json()
        if (
            body.get("message_id") != run.message_id
            or body.get("case_run_id") != run.case_run_id
            or body.get("outcome") != "SUCCESS"
            or not isinstance(body.get("traces"), list)
            or body.get("error_type") is not None
            or body.get("error_message") is not None
        ):
            self._send_json(422, {"detail": "completion mismatch"})
            return
        with self.server.state.lock:
            run.completion_count += 1
            run.terminal = True
            run.completion_outcome = "SUCCESS"
            run.completed.set()
        self._send_json(
            200,
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "message_id": run.message_id,
                "runner_id": self.server.state.runner_id,
                "case_run_id": run.case_run_id,
                "outcome": "SUCCESS",
                "run_status": "SUCCESS",
                "case_run_status": "SUCCESS",
                "traces": body["traces"],
                "error_type": None,
                "error_message": None,
                "completed_at": utc_now(),
                "idempotent": False,
            },
        )

    def _plan(self, run: BackendRun) -> dict[str, Any]:
        acted_url = run.start_url.removesuffix("/start") + "/acted"
        locator = {
            "element_version_id": None,
            "candidates": [
                {"strategy": "css", "value": "#target", "priority": 1}
            ],
        }
        assertion_locator = {
            "element_version_id": None,
            "candidates": [
                {"strategy": "css", "value": "#result", "priority": 1}
            ],
        }
        return {
            "schema_version": 1,
            "run_id": run.run_id,
            "message_id": run.message_id,
            "runner_id": self.server.state.runner_id,
            "case_run_id": run.case_run_id,
            "web_case_id": 700 + run.case_run_id,
            "web_case_version_id": 800 + run.case_run_id,
            "start_url": run.start_url,
            "browser": "CHROME",
            "headless": True,
            "total_timeout_ms": 30_000,
            "initial_context": {},
            "actions": [
                {
                    "type": "CLICK",
                    "timeout_ms": 10_000,
                    "failure_policy": "STOP",
                    "url": None,
                    "locator": locator,
                    "value": None,
                    "key": None,
                }
            ],
            "assertions": [
                {
                    "type": "ASSERT_URL",
                    "timeout_ms": 5_000,
                    "locator": None,
                    "expected": acted_url,
                },
                {
                    "type": "ASSERT_TEXT",
                    "timeout_ms": 5_000,
                    "locator": assertion_locator,
                    "expected": "ACTION_OK",
                },
            ],
            "session": None,
        }

    def _resolve_run(self, path: str) -> tuple[BackendRun | None, str | None]:
        prefix = "/api/v1/runs/"
        if not path.startswith(prefix):
            return None, None
        parts = path[len(prefix) :].split("/", maxsplit=1)
        if len(parts) != 2:
            return None, None
        return self.server.state.runs.get(parts[0]), parts[1]

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == (
            f"Bearer {self.server.state.credential}"
        )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AcceptanceFailure("INVALID_CONTENT_LENGTH") from exc
        if not 0 <= length <= MAX_HTTP_BODY_BYTES:
            raise AcceptanceFailure("HTTP_BODY_TOO_LARGE")
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._read_body() or b"{}")
        except (json.JSONDecodeError, UnicodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        return None


class SiteState:
    def __init__(self, run_ids: tuple[str, ...], held_run_id: str) -> None:
        self.action_counts = {run_id: 0 for run_id in run_ids}
        self.start_events = {run_id: Event() for run_id in run_ids}
        self.release_events = {run_id: Event() for run_id in run_ids}
        self.held_run_id = held_run_id
        self.lock = Lock()
        for run_id in run_ids:
            if run_id != held_run_id:
                self.release_events[run_id].set()

    def release_all(self) -> None:
        for event in self.release_events.values():
            event.set()


class SiteServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: SiteState) -> None:
        super().__init__(("127.0.0.1", 0), SiteHandler)
        self.state = state


class SiteHandler(BaseHTTPRequestHandler):
    server: SiteServer

    def do_GET(self) -> None:
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[0] not in self.server.state.action_counts:
            self._send_html(404, "NOT_FOUND")
            return
        run_id, action = parts
        if action == "start":
            self.server.state.start_events[run_id].set()
            if not self.server.state.release_events[run_id].wait(timeout=25):
                self._send_html(504, "START_RELEASE_TIMEOUT")
                return
            self._send_html(
                200,
                '<a id="target" href="acted">Run action</a>'
                '<div id="status">READY</div>',
            )
            return
        if action == "acted":
            with self.server.state.lock:
                self.server.state.action_counts[run_id] += 1
            self._send_html(200, '<div id="result">ACTION_OK</div>')
            return
        self._send_html(404, "NOT_FOUND")

    def _send_html(self, status: int, content: str) -> None:
        encoded = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"</head><body>{content}</body></html>"
        ).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *_args: object) -> None:
        return None


@dataclass
class ObservationState:
    marker: str
    lock: Lock = field(default_factory=Lock)
    topology_count: int = 0
    connection_ids: list[int] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    acknowledgements: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    child_processes: list[dict[str, Any]] = field(default_factory=list)
    owned_browser_processes: dict[int, dict[str, Any]] = field(default_factory=dict)
    observed_temp_dirs: set[str] = field(default_factory=set)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "topology_count": self.topology_count,
                "connection_ids": list(self.connection_ids),
                "deliveries": [dict(item) for item in self.deliveries],
                "acknowledgements": [dict(item) for item in self.acknowledgements],
                "rejections": [dict(item) for item in self.rejections],
                "child_processes": [dict(item) for item in self.child_processes],
                "owned_browser_processes": [
                    dict(item)
                    for _pid, item in sorted(self.owned_browser_processes.items())
                ],
                "observed_temp_dirs": sorted(self.observed_temp_dirs),
            }


class ObservedChannel:
    def __init__(self, channel: Any, observations: ObservationState) -> None:
        self._channel = channel
        self._observations = observations

    def basic_get(self, *args: Any, **kwargs: Any) -> Any:
        delivery = self._channel.basic_get(*args, **kwargs)
        if isinstance(delivery, tuple) and len(delivery) == 3 and delivery[0] is not None:
            method, properties, _body = delivery
            record = {
                "message_id": getattr(properties, "message_id", None),
                "redelivered": bool(getattr(method, "redelivered", False)),
                "observed_at": utc_now(),
            }
            with self._observations.lock:
                self._observations.deliveries.append(record)
        return delivery

    def basic_ack(self, *args: Any, **kwargs: Any) -> Any:
        record = {"succeeded": False, "observed_at": utc_now()}
        try:
            result = self._channel.basic_ack(*args, **kwargs)
        except BaseException:
            with self._observations.lock:
                self._observations.acknowledgements.append(record)
            raise
        record["succeeded"] = True
        with self._observations.lock:
            self._observations.acknowledgements.append(record)
        return result

    def basic_reject(self, *args: Any, **kwargs: Any) -> Any:
        record = {
            "requeue": bool(kwargs.get("requeue", False)),
            "succeeded": False,
            "observed_at": utc_now(),
        }
        try:
            result = self._channel.basic_reject(*args, **kwargs)
        except BaseException:
            with self._observations.lock:
                self._observations.rejections.append(record)
            raise
        record["succeeded"] = True
        with self._observations.lock:
            self._observations.rejections.append(record)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._channel, name)


class ObservedConsumer(RunnerTaskConsumer):
    def __init__(self, *args: Any, observations: ObservationState, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._observations = observations
        self._topology_observed = False

    def configure(self) -> Any:
        topology = super().configure()
        if not self._topology_observed:
            self._topology_observed = True
            with self._observations.lock:
                self._observations.topology_count += 1
        return topology


class ObservedTaskProcess:
    def __init__(self, process: Any, observations: ObservationState) -> None:
        self._process = process
        self._observations = observations
        self._record: dict[str, Any] | None = None

    def start(self) -> None:
        self._process.start()
        inner = getattr(self._process, "_process", None)
        pid = getattr(inner, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            raise AcceptanceFailure("WEB_CHILD_PID_MISSING")
        self._record = {
            **process_details(pid),
            "role": "web_spawn_child",
            "owned_by": self._observations.marker,
            "observed_at": utc_now(),
            "exitcode": None,
        }
        with self._observations.lock:
            self._observations.child_processes.append(self._record)

    def poll(self, timeout_seconds: float) -> bool:
        return self._process.poll(timeout_seconds)

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate(self) -> None:
        self._process.terminate()

    def request_cancel(self) -> None:
        self._process.request_cancel()

    def request_stop(self) -> None:
        self._process.request_stop()

    def join(self, timeout: float | None = None) -> None:
        self._process.join(timeout)

    @property
    def exitcode(self) -> int | None:
        return self._process.exitcode

    def outcome(self) -> Any:
        return self._process.outcome()

    def close(self) -> None:
        if self._record is not None:
            self._process.join(timeout=5)
            self._record["exitcode"] = self._process.exitcode
            self._record["closed_at"] = utc_now()
        self._process.close()


class DockerResource:
    def __init__(self, marker: str, ownership: dict[str, Any]) -> None:
        self.marker = marker
        self.name = f"p5f-q1-{marker}"
        self.container_id: str | None = None
        self.host_port: int | None = None
        self.username = f"u{secrets.token_hex(8)}"
        self.password = secrets.token_hex(24)
        self.ownership = ownership

    def create(self) -> None:
        self.ownership["container"] = {
            "name": self.name,
            "id": None,
            "image": RABBIT_IMAGE,
            "requested_port_binding": "127.0.0.1:ephemeral->5672/tcp",
            "host_port": None,
            "mounts": None,
            "created_at": None,
            "started_at": None,
            "removed": False,
        }
        write_json(OWNERSHIP_PATH, self.ownership)
        completed = self._docker(
            [
                "create",
                "--name",
                self.name,
                "-p",
                "127.0.0.1::5672",
                "--tmpfs",
                "/var/lib/rabbitmq:rw,size=268435456",
                "-e",
                f"RABBITMQ_DEFAULT_USER={self.username}",
                "-e",
                f"RABBITMQ_DEFAULT_PASS={self.password}",
                RABBIT_IMAGE,
            ],
            "DOCKER_CREATE_FAILED",
        )
        container_id = completed.stdout.strip()
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise AcceptanceFailure("DOCKER_CONTAINER_ID_INVALID")
        self.container_id = container_id
        inspection = self._inspect()
        self.ownership["container"].update(
            {
                "id": self.container_id,
                "mounts": self._safe_mounts(inspection.get("Mounts")),
                "created_at": inspection.get("Created"),
            }
        )
        write_json(OWNERSHIP_PATH, self.ownership)

    def start(self) -> None:
        if self.container_id is None:
            raise AcceptanceFailure("CONTAINER_NOT_CREATED")
        self._docker(["start", self.name], "DOCKER_START_FAILED")
        port_output = self._docker(
            ["port", self.name, "5672/tcp"], "DOCKER_PORT_FAILED"
        ).stdout.strip()
        try:
            self.host_port = int(port_output.rsplit(":", maxsplit=1)[1])
        except (IndexError, ValueError) as exc:
            raise AcceptanceFailure("DOCKER_PORT_INVALID") from exc
        inspection = self._inspect()
        if inspection.get("Id") != self.container_id:
            raise AcceptanceFailure("CONTAINER_IDENTITY_CHANGED")
        self.ownership["container"].update(
            {
                "host_port": self.host_port,
                "mounts": self._safe_mounts(inspection.get("Mounts")),
                "started_at": utc_now(),
            }
        )
        write_json(OWNERSHIP_PATH, self.ownership)

    def amqp_url(self) -> str:
        if self.host_port is None:
            raise AcceptanceFailure("CONTAINER_PORT_UNAVAILABLE")
        return (
            f"amqp://{self.username}:{self.password}@127.0.0.1:"
            f"{self.host_port}/%2F"
        )

    def connection_count(self) -> int:
        completed = self._docker(
            [
                "exec",
                self.name,
                "rabbitmqctl",
                "-s",
                "list_connections",
                "pid",
            ],
            "RABBIT_CONNECTION_LIST_FAILED",
        )
        informational_prefixes = ("listing connections", "timeout:")
        return len(
            [
                line
                for line in completed.stdout.splitlines()
                if line.strip()
                and line.strip().lower() != "pid"
                and not line.strip().lower().startswith(informational_prefixes)
            ]
        )

    def close_all_connections(self, reason: str) -> dict[str, Any]:
        before = self.connection_count()
        self._docker(
            [
                "exec",
                self.name,
                "rabbitmqctl",
                "close_all_connections",
                reason,
            ],
            "RABBIT_CLOSE_CONNECTIONS_FAILED",
        )
        return {"before": before, "command_succeeded": True, "issued_at": utc_now()}

    def queue_counts(self, amqp_url: str, queue_names: tuple[str, ...]) -> dict[str, int]:
        import pika

        connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        try:
            channel = connection.channel()
            return {
                queue: int(
                    channel.queue_declare(queue=queue, passive=True)
                    .method.message_count
                )
                for queue in queue_names
            }
        finally:
            connection.close()

    def remove(self) -> None:
        if self.container_id is None:
            return
        inspection = self._inspect(optional=True)
        if inspection is None:
            self.ownership["container"]["removed"] = True
            return
        if inspection.get("Id") != self.container_id:
            self.ownership["container"]["cleanup_error"] = "IDENTITY_MISMATCH"
            return
        completed = subprocess.run(
            ["docker", "rm", "-f", "-v", self.name],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if completed.returncode != 0:
            self.ownership["container"]["cleanup_error"] = "DOCKER_RM_FAILED"
            return
        self.ownership["container"]["removed"] = self._inspect(optional=True) is None

    def _inspect(self, *, optional: bool = False) -> dict[str, Any] | None:
        completed = subprocess.run(
            ["docker", "inspect", self.name],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if completed.returncode != 0:
            if optional:
                return None
            raise AcceptanceFailure("DOCKER_INSPECT_FAILED")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AcceptanceFailure("DOCKER_INSPECT_INVALID") from exc
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise AcceptanceFailure("DOCKER_INSPECT_SHAPE_INVALID")
        return payload[0]

    @staticmethod
    def _safe_mounts(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            {
                "type": item.get("Type"),
                "name": item.get("Name"),
                "destination": item.get("Destination"),
            }
            for item in value
            if isinstance(item, dict)
        ]

    @staticmethod
    def _docker(args: list[str], failure_code: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["docker", *args],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        if completed.returncode != 0:
            raise AcceptanceFailure(failure_code)
        return completed


class AcceptanceHarness:
    def __init__(self) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        self.marker = f"{stamp}-{secrets.token_hex(4)}"
        self.runner_id = f"p5fq1_{secrets.token_hex(6)}"
        self.identity = RunnerIdentity(self.runner_id, secrets.token_hex(24))
        self.run_ids = (
            f"p5fq1-idle-{secrets.token_hex(4)}",
            f"p5fq1-flight-{secrets.token_hex(4)}",
        )
        self.message_ids = (
            f"msg-idle-{secrets.token_hex(4)}",
            f"msg-flight-{secrets.token_hex(4)}",
        )
        self.observations = ObservationState(self.marker)
        self.ownership: dict[str, Any] = {
            "schema_version": 1,
            "package": "P5F-Q1/r1",
            "marker": self.marker,
            "recorded_at": utc_now(),
            "scope": "isolated_acceptance_only",
            "owned_paths": [
                str(VALIDATION_ROOT.resolve()),
                str(Path(__file__).resolve()),
            ],
            "harness_process": {
                **process_details(os.getpid()),
                "role": "acceptance_harness",
                "owned_by": self.marker,
            },
            "container": None,
            "processes": [],
            "cleanup": None,
        }
        self.docker = DockerResource(self.marker, self.ownership)
        self.site_state = SiteState(self.run_ids, self.run_ids[1])
        self.site_server = SiteServer(self.site_state)
        site_base = f"http://127.0.0.1:{self.site_server.server_port}"
        self.backend_runs = {
            run_id: BackendRun(
                run_id,
                message_id,
                index,
                f"{site_base}/{run_id}/start",
            )
            for index, (run_id, message_id) in enumerate(
                zip(self.run_ids, self.message_ids, strict=True), start=1
            )
        }
        self.backend_state = BackendState(
            self.runner_id, self.identity.credential, self.backend_runs
        )
        self.backend_server = BackendServer(self.backend_state)
        self.server_threads: list[Thread] = []
        self.stop_event = Event()
        self.worker_thread: Thread | None = None
        self.worker: RunnerWorker | None = None
        self.worker_result: Any | None = None
        self.worker_error_type: str | None = None
        self.slot_state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})
        self.disconnects: list[dict[str, Any]] = []
        self.rabbit_url: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.stage = "initialized"
        self.browser_baseline = chrome_pids(process_inventory())
        self.temp_baseline = web_temp_dirs()

    def run(self) -> dict[str, Any]:
        VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
        write_json(OWNERSHIP_PATH, self.ownership)
        self._start_servers()
        self.stage = "container_create"
        self.docker.create()
        self.stage = "container_start"
        self.docker.start()
        self.rabbit_url = self.docker.amqp_url()
        self.stage = "rabbit_ready"
        self._wait_rabbit_ready()
        self.stage = "worker_start"
        self._start_worker()
        wait_until(
            lambda: self.observations.snapshot()["topology_count"] >= 1,
            timeout_seconds=20,
            label="INITIAL_TOPOLOGY",
        )

        self.stage = "idle_disconnect"
        initial_connections = self.observations.snapshot()["connection_ids"]
        idle_disconnect = self.docker.close_all_connections(
            f"P5F_Q1_IDLE_{self.marker}"
        )
        wait_until(
            lambda: self.observations.snapshot()["topology_count"] >= 2,
            timeout_seconds=20,
            label="IDLE_RECONNECT",
        )
        after_idle = self.observations.snapshot()["connection_ids"]
        idle_disconnect.update(
            {
                "kind": "idle",
                "old_connection_replaced": (
                    len(initial_connections) == 1
                    and len(after_idle) >= 2
                    and initial_connections[0] != after_idle[1]
                ),
                "topology_redeclared": True,
            }
        )
        self.disconnects.append(idle_disconnect)

        self.stage = "idle_run_publish"
        self._publish(0)
        idle_run = self.backend_runs[self.run_ids[0]]
        wait_until(
            idle_run.completed.is_set,
            timeout_seconds=40,
            label="IDLE_RUN_COMPLETE",
        )
        wait_until(
            lambda: sum(
                1
                for item in self.observations.snapshot()["acknowledgements"]
                if item["succeeded"]
            )
            >= 1,
            timeout_seconds=10,
            label="IDLE_RUN_ACK",
        )

        self.stage = "inflight_run_publish"
        self._publish(1)
        flight_run = self.backend_runs[self.run_ids[1]]
        if not self.site_state.start_events[self.run_ids[1]].wait(timeout=30):
            raise AcceptanceFailure("TIMEOUT_INFLIGHT_SITE_START")
        self._observe_owned_browser()
        with self.observations.lock:
            self.observations.observed_temp_dirs.update(
                web_temp_dirs() - self.temp_baseline
            )

        self.stage = "inflight_disconnect"
        inflight_disconnect = self.docker.close_all_connections(
            f"P5F_Q1_INFLIGHT_{self.marker}"
        )
        time.sleep(0.5)
        inflight_disconnect.update(
            {
                "kind": "in_flight",
                "connection_count_after_close_probe": (
                    self.docker.connection_count()
                ),
                "site_request_was_held": True,
            }
        )
        self.disconnects.append(inflight_disconnect)
        self.site_state.release_events[self.run_ids[1]].set()

        wait_until(
            flight_run.completed.is_set,
            timeout_seconds=40,
            label="INFLIGHT_RUN_COMPLETE",
        )
        wait_until(
            flight_run.duplicate_claim.is_set,
            timeout_seconds=25,
            label="TERMINAL_REDELIVERY_CLAIM",
        )
        wait_until(
            lambda: self.observations.snapshot()["topology_count"] >= 3,
            timeout_seconds=20,
            label="INFLIGHT_RECONNECT",
        )
        wait_until(
            lambda: any(
                item["redelivered"]
                and item["message_id"] == self.message_ids[1]
                for item in self.observations.snapshot()["deliveries"]
            ),
            timeout_seconds=10,
            label="BROKER_REDELIVERY_FLAG",
        )
        wait_until(
            lambda: any(
                item["succeeded"] and not item["requeue"]
                for item in self.observations.snapshot()["rejections"]
            ),
            timeout_seconds=10,
            label="TERMINAL_REDELIVERY_REJECT",
        )

        self.stage = "worker_stop"
        self.stop_event.set()
        if self.worker_thread is None:
            raise AcceptanceFailure("WORKER_THREAD_MISSING")
        self.worker_thread.join(timeout=20)
        if self.worker_thread.is_alive():
            raise AcceptanceFailure("WORKER_STOP_TIMEOUT")
        if self.worker_error_type is not None:
            raise AcceptanceFailure("WORKER_THREAD_FAILED")
        if self.worker_result is None:
            raise AcceptanceFailure("WORKER_RESULT_MISSING")

        self.stage = "cleanup_verification"
        self._wait_process_cleanup()
        if self.rabbit_url is None:
            raise AcceptanceFailure("RABBIT_URL_MISSING")
        queue_counts = self.docker.queue_counts(
            self.rabbit_url,
            (runner_queue_name(self.runner_id), DEAD_QUEUE),
        )
        result = self._result(queue_counts)
        self.last_result = result
        self._validate_result(result)
        return result

    def cleanup(self) -> None:
        self.site_state.release_all()
        self.stop_event.set()
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=15)
        self.backend_server.shutdown()
        self.site_server.shutdown()
        self.backend_server.server_close()
        self.site_server.server_close()
        for thread in self.server_threads:
            thread.join(timeout=5)
        self.docker.remove()
        observations = self.observations.snapshot()
        inventory = process_inventory()
        owned_pids = {
            item["pid"] for item in observations["owned_browser_processes"]
        } | {item["pid"] for item in observations["child_processes"]}
        remaining_owned_pids = sorted(owned_pids & set(inventory))
        remaining_temp_dirs = sorted(
            path
            for path in observations["observed_temp_dirs"]
            if Path(path).exists()
        )
        self.ownership["processes"] = (
            observations["child_processes"]
            + observations["owned_browser_processes"]
        )
        self.ownership["cleanup"] = {
            "completed_at": utc_now(),
            "container_removed": bool(
                isinstance(self.ownership.get("container"), dict)
                and self.ownership["container"].get("removed")
            ),
            "remaining_owned_process_pids": remaining_owned_pids,
            "remaining_owned_temp_dirs": remaining_temp_dirs,
            "slot_available": self.slot_state.available(),
        }
        write_json(OWNERSHIP_PATH, self.ownership)

    def _start_servers(self) -> None:
        for name, server in (
            ("p5f-q1-site", self.site_server),
            ("p5f-q1-backend-fixture", self.backend_server),
        ):
            thread = Thread(target=server.serve_forever, name=name, daemon=True)
            thread.start()
            self.server_threads.append(thread)

    def _wait_rabbit_ready(self) -> None:
        if self.rabbit_url is None:
            raise AcceptanceFailure("RABBIT_URL_MISSING")

        def ready() -> bool:
            try:
                connection = PikaBroker(RabbitMqConfig(self.rabbit_url)).connect()
            except BaseException:  # noqa: BLE001 - bounded readiness probe
                return False
            try:
                return bool(getattr(connection, "is_open", False))
            finally:
                connection.close()

        wait_until(ready, timeout_seconds=40, label="RABBIT_READY", interval_seconds=0.5)

    def _client(self, transport: HttpxTransport) -> RunnerClient:
        return RunnerClient(
            f"http://127.0.0.1:{self.backend_server.server_port}",
            transport,
            connect_timeout_seconds=2,
            read_timeout_seconds=35,
            max_attempts=2,
            backoff_base_seconds=0.1,
            backoff_max_seconds=0.1,
        )

    def _start_worker(self) -> None:
        if self.rabbit_url is None:
            raise AcceptanceFailure("RABBIT_URL_MISSING")

        def target() -> None:
            heartbeat_transport = HttpxTransport()
            task_transport = HttpxTransport()
            broker = PikaBroker(RabbitMqConfig(self.rabbit_url or ""))
            try:
                connection = broker.connect()
                resources = WorkerResources(
                    executor=None,
                    heartbeat_transport=heartbeat_transport,
                    task_transport=task_transport,
                    connection=connection,
                )
                slots = {"API": 0, "WEB": 1, "PERFORMANCE": 0}
                task_client = self._client(task_transport)

                def web_factory(plan: Any, evidence_dir: Any = None) -> Any:
                    return ObservedTaskProcess(
                        spawn_web_task_process(plan, evidence_dir), self.observations
                    )

                def consumer_factory(rabbit_connection: Any) -> RunnerTaskConsumer:
                    with self.observations.lock:
                        self.observations.connection_ids.append(id(rabbit_connection))
                    channel = ObservedChannel(
                        rabbit_connection.channel(), self.observations
                    )
                    return ObservedConsumer(
                        channel,
                        task_client,
                        self.identity,
                        slots,
                        stop_event=self.stop_event,
                        slot_state=self.slot_state,
                        web_isolation_factory=web_factory,
                        force_stop_poll_seconds=0.1,
                        observations=self.observations,
                    )

                consumer = consumer_factory(connection)
                config = RunnerConfig(
                    backend_url=(
                        f"http://127.0.0.1:{self.backend_server.server_port}"
                    ),
                    name="P5F Q1 isolated acceptance",
                    api_slots=0,
                    web_slots=1,
                    performance_slots=0,
                    connect_timeout_seconds=2,
                    read_timeout_seconds=35,
                    max_attempts=2,
                    backoff_base_seconds=0.1,
                    backoff_max_seconds=0.1,
                )
                heartbeat = HeartbeatService(
                    self._client(heartbeat_transport),
                    config,
                    self.identity,
                    lambda: {
                        "name": config.name,
                        "tags": [],
                        "slots": {
                            "total": self.slot_state.totals(),
                            "available": self.slot_state.available(),
                        },
                    },
                    self.stop_event,
                    default_interval_seconds=5,
                )
                self.slot_state.set_on_change(heartbeat.request_immediate_heartbeat)
                self.worker = RunnerWorker(
                    consumer,
                    heartbeat,
                    self.stop_event,
                    resources,
                    poll_interval_seconds=0.05,
                    connection_factory=broker.connect,
                    consumer_factory=consumer_factory,
                    reconnect_policy=RabbitMqReconnectPolicy(
                        initial_delay_seconds=0.1,
                        max_delay_seconds=0.5,
                    ),
                )
                self.worker_result = self.worker.run()
            except BaseException as exc:  # noqa: BLE001 - thread terminal boundary
                self.worker_error_type = type(exc).__name__
                self.stop_event.set()

        self.worker_thread = Thread(target=target, name="p5f-q1-worker", daemon=True)
        self.worker_thread.start()

    def _publish(self, index: int) -> None:
        if self.rabbit_url is None:
            raise AcceptanceFailure("RABBIT_URL_MISSING")
        import pika

        run_id = self.run_ids[index]
        message_id = self.message_ids[index]
        body = json.dumps(
            {
                "schema_version": 1,
                "message_id": message_id,
                "run_id": run_id,
                "runner_id": self.runner_id,
                "run_type": "WEB_CASE",
                "required_slot_type": "WEB",
                "attempt": 1,
                "enqueued_at": utc_now(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        connection = pika.BlockingConnection(pika.URLParameters(self.rabbit_url))
        try:
            channel = connection.channel()
            channel.confirm_delivery()
            published = channel.basic_publish(
                exchange=TASK_EXCHANGE,
                routing_key=runner_routing_key(self.runner_id, "WEB"),
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=PERSISTENT_DELIVERY_MODE,
                    message_id=message_id,
                ),
                mandatory=True,
            )
            if published is False:
                raise AcceptanceFailure("PUBLISH_NOT_CONFIRMED")
        finally:
            connection.close()

    def _observe_owned_browser(self) -> None:
        def observe() -> bool:
            snapshot = self.observations.snapshot()
            if not snapshot["child_processes"]:
                return False
            root_pid = snapshot["child_processes"][-1]["pid"]
            inventory = process_inventory()
            owned = descendants(root_pid, inventory) & chrome_pids(inventory)
            if not owned:
                return False
            with self.observations.lock:
                for pid in sorted(owned):
                    self.observations.owned_browser_processes.setdefault(
                        pid,
                        {
                            **process_details(pid),
                            "role": "owned_chrome_descendant",
                            "owned_by": self.marker,
                            "spawn_root_pid": root_pid,
                            "observed_at": utc_now(),
                        },
                    )
            return True

        wait_until(observe, timeout_seconds=10, label="OWNED_CHROME_OBSERVATION")

    def _wait_process_cleanup(self) -> None:
        observations = self.observations.snapshot()
        owned_pids = {
            item["pid"] for item in observations["owned_browser_processes"]
        } | {item["pid"] for item in observations["child_processes"]}
        observed_dirs = set(observations["observed_temp_dirs"])
        wait_until(
            lambda: not (owned_pids & set(process_inventory())),
            timeout_seconds=15,
            label="OWNED_PROCESS_CLEANUP",
        )
        wait_until(
            lambda: not {path for path in observed_dirs if Path(path).exists()},
            timeout_seconds=15,
            label="OWNED_TEMP_CLEANUP",
        )

    def _result(self, queue_counts: dict[str, int]) -> dict[str, Any]:
        observations = self.observations.snapshot()
        backend = self.backend_state.snapshot()
        current_inventory = process_inventory()
        owned_pids = {
            item["pid"] for item in observations["owned_browser_processes"]
        } | {item["pid"] for item in observations["child_processes"]}
        current_chrome = chrome_pids(current_inventory)
        return {
            "schema_version": 1,
            "package": "P5F-Q1/r1",
            "status": "PASSED",
            "completed_at": utc_now(),
            "marker": self.marker,
            "boundaries": {
                "rabbitmq": "unique temporary Docker container",
                "backend": "controlled loopback HTTP fixture; no Backend DB",
                "site": "controlled random-port loopback HTTP site",
                "runner_path": (
                    "real PikaBroker + RunnerWorker + RunnerTaskConsumer + "
                    "RunnerClient + spawn + Playwright + Chrome"
                ),
                "mocks": [],
                "shared_services_touched": False,
                "formal_runner_registered": False,
            },
            "container": {
                "name": self.docker.name,
                "id": self.docker.container_id,
                "host_port": self.docker.host_port,
                "mounts": self.ownership["container"]["mounts"],
            },
            "disconnects": list(self.disconnects),
            "backend": backend,
            "site_action_counts": dict(self.site_state.action_counts),
            "broker_observations": {
                "deliveries": observations["deliveries"],
                "acknowledgements": observations["acknowledgements"],
                "rejections": observations["rejections"],
                "topology_count": observations["topology_count"],
                "connection_count": len(observations["connection_ids"]),
                "queue_counts_before_container_cleanup": queue_counts,
            },
            "worker": self.worker_result.to_mapping(),
            "cleanup_before_container_removal": {
                "slot_available": self.slot_state.available(),
                "spawn_children_observed": len(observations["child_processes"]),
                "owned_browser_processes_observed": len(
                    observations["owned_browser_processes"]
                ),
                "remaining_owned_process_pids": sorted(
                    owned_pids & set(current_inventory)
                ),
                "remaining_owned_browser_pids": sorted(
                    owned_pids & current_chrome
                ),
                "global_new_chrome_pids_vs_baseline": sorted(
                    current_chrome - self.browser_baseline
                ),
                "remaining_observed_temp_dirs": sorted(
                    path
                    for path in observations["observed_temp_dirs"]
                    if Path(path).exists()
                ),
            },
        }

    def _validate_result(self, result: dict[str, Any]) -> None:
        idle = result["backend"]["runs"][self.run_ids[0]]
        flight = result["backend"]["runs"][self.run_ids[1]]
        worker = result["worker"]
        broker = result["broker_observations"]
        cleanup = result["cleanup_before_container_removal"]
        queue_counts = broker["queue_counts_before_container_cleanup"]
        conditions = {
            "idle_action_once": result["site_action_counts"][self.run_ids[0]] == 1,
            "flight_action_once": result["site_action_counts"][self.run_ids[1]] == 1,
            "idle_claim_once": idle["claim_count"] == 1,
            "flight_claim_twice": flight["claim_count"] == 2,
            "idle_complete_once": idle["completion_count"] == 1,
            "flight_complete_once": flight["completion_count"] == 1,
            "no_duplicate_plan": flight["plan_count"] == 1,
            "no_duplicate_start": flight["start_count"] == 1,
            "both_success": (
                idle["completion_outcome"] == "SUCCESS"
                and flight["completion_outcome"] == "SUCCESS"
            ),
            "two_real_disconnects": len(result["disconnects"]) == 2,
            "idle_connection_replaced": result["disconnects"][0][
                "old_connection_replaced"
            ],
            "broker_close_commands_succeeded": all(
                item["command_succeeded"] for item in result["disconnects"]
            ),
            "worker_reconnected_twice": worker["rabbitmq_reconnect_count"] >= 2,
            "worker_stopped_cleanly": worker["stop_reason"] == "STOP_REQUESTED",
            "one_successful_ack": sum(
                1 for item in broker["acknowledgements"] if item["succeeded"]
            )
            == 1,
            "one_failed_uncertain_ack": sum(
                1 for item in broker["acknowledgements"] if not item["succeeded"]
            )
            == 1,
            "terminal_redelivery_rejected": any(
                item["succeeded"] and not item["requeue"]
                for item in broker["rejections"]
            ),
            "redelivery_flag_observed": any(
                item["message_id"] == self.message_ids[1] and item["redelivered"]
                for item in broker["deliveries"]
            ),
            "task_queue_empty": queue_counts[runner_queue_name(self.runner_id)] == 0,
            "terminal_delivery_dead_lettered": queue_counts[DEAD_QUEUE] == 1,
            "slot_released": cleanup["slot_available"]["WEB"] == 1,
            "two_spawn_children": cleanup["spawn_children_observed"] == 2,
            "real_chrome_observed": cleanup["owned_browser_processes_observed"] > 0,
            "owned_processes_gone": not cleanup["remaining_owned_process_pids"],
            "owned_browser_gone": not cleanup["remaining_owned_browser_pids"],
            "owned_temp_dirs_gone": not cleanup["remaining_observed_temp_dirs"],
            "no_docker_volume": not any(
                mount.get("type") == "volume" for mount in result["container"]["mounts"]
            ),
        }
        result["checks"] = conditions
        failed = sorted(name for name, passed in conditions.items() if not passed)
        if failed:
            result["status"] = "FAILED"
            result["failed_checks"] = failed
            write_json(RESULT_PATH, result)
            raise AcceptanceFailure("ACCEPTANCE_CHECK_FAILED")


def main() -> int:
    harness: AcceptanceHarness | None = None
    result: dict[str, Any] = {
        "schema_version": 1,
        "package": "P5F-Q1/r1",
        "status": "FAILED",
        "completed_at": utc_now(),
    }
    try:
        harness = AcceptanceHarness()
        result = harness.run()
    except BaseException as exc:  # noqa: BLE001 - redact the terminal boundary
        if harness is not None and harness.last_result is not None:
            result = harness.last_result
        result.update(
            {
                "status": "FAILED",
                "completed_at": utc_now(),
                "failure_stage": harness.stage if harness is not None else "startup",
                "failure_type": type(exc).__name__,
                "failure_code": (
                    str(exc) if isinstance(exc, AcceptanceFailure) else "UNEXPECTED"
                ),
            }
        )
    finally:
        if harness is not None:
            try:
                harness.cleanup()
            except BaseException as exc:  # noqa: BLE001 - redact cleanup boundary
                result.update(
                    {
                        "status": "FAILED",
                        "failure_stage": "cleanup",
                        "failure_type": type(exc).__name__,
                        "failure_code": "CLEANUP_FAILED",
                    }
                )
            else:
                result["container_removed"] = bool(
                    isinstance(harness.ownership.get("container"), dict)
                    and harness.ownership["container"].get("removed")
                )
                result["cleanup"] = harness.ownership.get("cleanup")
                cleanup = result.get("cleanup")
                cleanup_ok = (
                    result["container_removed"]
                    and isinstance(cleanup, dict)
                    and not cleanup.get("remaining_owned_process_pids")
                    and not cleanup.get("remaining_owned_temp_dirs")
                    and cleanup.get("slot_available", {}).get("WEB") == 1
                )
                if result["status"] == "PASSED" and not cleanup_ok:
                    result.update(
                        {
                            "status": "FAILED",
                            "failure_stage": "cleanup",
                            "failure_type": "AcceptanceFailure",
                            "failure_code": "CLEANUP_INCOMPLETE",
                        }
                    )
        write_json(RESULT_PATH, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "result_path": str(RESULT_PATH),
                "ownership_path": str(OWNERSHIP_PATH),
                "container_removed": result.get("container_removed", False),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
