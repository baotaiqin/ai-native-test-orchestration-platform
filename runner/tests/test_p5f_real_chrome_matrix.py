from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import secrets
import struct
import sys
import tempfile
import time
import zlib
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from runner.evidence import audit_trace_bytes, validate_manifest
from runner.executors.web import WebExecutor
from runner.isolation import IsolatedOutcome, spawn_web_task_process
from runner.models import (
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionPlanResult,
    WebLocatorCandidate,
    WebLocatorPlan,
    WebSessionPlan,
)
from runner.slots import SlotState
from runner.snapshot import DefaultSnapshotProbe

pytestmark = pytest.mark.skipif(
    not DefaultSnapshotProbe().web_ready(), reason="Playwright 或本机 Chrome 不可用"
)


class _MatrixServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _MatrixHandler)
        self.sensitive_page_value = secrets.token_urlsafe(24)
        self.slow_started = Event()


class _MatrixHandler(BaseHTTPRequestHandler):
    server: _MatrixServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/slow":
            self.server.slow_started.set()
            duration = float(query.get("duration", ["1"])[0])
            time.sleep(max(0.0, min(duration, 20.0)))
        delay_ms = max(0, min(int(query.get("delay_ms", ["0"])[0]), 10_000))
        primary_delay_ms = max(
            0, min(int(query.get("primary_delay_ms", ["0"])[0]), 10_000)
        )
        page_marker = self.server.sensitive_page_value
        body = f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>P5F Chrome Matrix</title></head>
  <body>
    <input id="sensitive-input" data-sensitive>
    <div id="server-sensitive" data-sensitive>{page_marker}</div>
    <button id="fallback-target">Continue</button>
    <div id="result">waiting</div>
    <div id="session-status">SESSION_MISSING</div>
    <script>
      if (document.cookie.includes('matrix_session=')) {{
        document.querySelector('#session-status').textContent = 'SESSION_REUSED';
      }}
      document.querySelector('#fallback-target').addEventListener('click', () => {{
        const value = document.querySelector('#sensitive-input').value;
        if (value) console.error('matrix-console=' + value);
        document.querySelector('#result').textContent = 'clicked';
      }});
      const addDelayedButton = (id) => {{
          const delayed = document.createElement('button');
          delayed.id = id;
          delayed.textContent = 'Delayed';
          delayed.addEventListener('click', () => {{
            window.delayedClicks = (window.delayedClicks || 0) + 1;
            document.querySelector('#result').textContent = 'clicked-' + window.delayedClicks;
          }});
          document.body.appendChild(delayed);
      }};
      if ({delay_ms} > 0)
        window.setTimeout(() => addDelayedButton('delayed-target'), {delay_ms});
      if ({primary_delay_ms} > 0)
        window.setTimeout(() => addDelayedButton('delayed-primary'), {primary_delay_ms});
    </script>
  </body>
</html>""".encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def matrix_server() -> tuple[_MatrixServer, str]:
    server = _MatrixServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _locator(*candidates: tuple[str, str, int]) -> WebLocatorPlan:
    return WebLocatorPlan(
        None,
        tuple(
            WebLocatorCandidate(strategy, value, priority)
            for strategy, value, priority in candidates
        ),
    )


def _plan(
    base_url: str,
    *,
    headless: bool = True,
    total_timeout_ms: int = 15_000,
    actions: tuple[WebExecutionActionPlan, ...] = (),
    assertions: tuple[WebExecutionAssertionPlan, ...] = (),
    initial_context: dict[str, object] | None = None,
    session: WebSessionPlan | None = None,
) -> WebExecutionPlanResult:
    return WebExecutionPlanResult(
        1,
        "run-p5f-matrix",
        "message-p5f-matrix",
        "runner-p5f-matrix",
        501,
        601,
        701,
        base_url,
        "CHROME",
        headless,
        total_timeout_ms,
        initial_context or {},
        actions,
        assertions,
        session,
    )


def _fallback_actions() -> tuple[WebExecutionActionPlan, ...]:
    return (
        WebExecutionActionPlan(
            "CLICK",
            4_000,
            "STOP",
            None,
            _locator(
                ("css", "#intentionally-missing", 1),
                ("css", "#fallback-target", 2),
            ),
            None,
            None,
        ),
    )


def _clicked_assertion() -> tuple[WebExecutionAssertionPlan, ...]:
    return (
        WebExecutionAssertionPlan(
            "ASSERT_TEXT",
            3_000,
            _locator(("css", "#result", 1)),
            "clicked",
        ),
    )


def _single_delayed_click_assertion() -> tuple[WebExecutionAssertionPlan, ...]:
    return (
        WebExecutionAssertionPlan(
            "ASSERT_TEXT",
            3_000,
            _locator(("css", "#result", 1)),
            "clicked-1",
        ),
    )


def _wait_for_process(process: object, timeout_seconds: float = 30.0) -> IsolatedOutcome:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll(0.1):  # type: ignore[attr-defined]
            break
    else:
        pytest.fail("isolated Web process did not finish within the bounded wait")
    process.join(timeout=5.0)  # type: ignore[attr-defined]
    assert not process.is_alive()  # type: ignore[attr-defined]
    outcome = process.outcome()  # type: ignore[attr-defined]
    assert isinstance(outcome, IsolatedOutcome)
    return outcome


def _png_color_pixel_count(path: Path, expected_rgb: tuple[int, int, int]) -> int:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = bit_depth = color_type = 0
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, _interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    assert bit_depth == 8 and color_type in {2, 6}
    bytes_per_pixel = 3 if color_type == 2 else 4
    stride = width * bytes_per_pixel
    raw = zlib.decompress(bytes(compressed))
    assert len(raw) == height * (stride + 1)
    previous = bytearray(stride)
    count = 0
    cursor = 0
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        decoded = bytearray(stride)
        for index, value in enumerate(encoded):
            left = decoded[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                base = left + above - upper_left
                left_delta = abs(base - left)
                above_delta = abs(base - above)
                upper_left_delta = abs(base - upper_left)
                predictor = (
                    left
                    if left_delta <= above_delta and left_delta <= upper_left_delta
                    else above
                    if above_delta <= upper_left_delta
                    else upper_left
                )
            else:
                pytest.fail(f"unsupported PNG filter type: {filter_type}")
            decoded[index] = (value + predictor) & 0xFF
        count += sum(
            tuple(decoded[index : index + 3]) == expected_rgb
            for index in range(0, stride, bytes_per_pixel)
        )
        previous = decoded
    return count


def _windows_process_inventory() -> dict[int, tuple[int, str]]:
    if os.name != "nt":
        pytest.skip("physical descendant verification is Windows-specific")

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
    assert snapshot != wintypes.HANDLE(-1).value
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


def _descendants(root_pid: int, inventory: dict[int, tuple[int, str]]) -> set[int]:
    result: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children = {pid for pid, (ppid, _name) in inventory.items() if ppid == parent}
        new_children = children - result
        result.update(new_children)
        pending.extend(new_children)
    return result


def _playwright_profile_dirs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("playwright_chromiumdev_profile-*"))


def _validate_with_backend_web_trace(trace: dict[str, object]) -> object:
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    original_path = list(sys.path)
    try:
        sys.path.insert(0, str(backend_root))
        from app.modules.runs.schemas import WebExecutionTrace

        return WebExecutionTrace.model_validate(trace)
    finally:
        sys.path[:] = original_path


@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
def test_real_chrome_modes_use_deterministic_locator_fallback(
    matrix_server: tuple[_MatrixServer, str], headless: bool
) -> None:
    _server, base_url = matrix_server
    plan = _plan(
        base_url,
        headless=headless,
        actions=_fallback_actions(),
        assertions=_clicked_assertion(),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "SUCCESS"
    assert result.traces[0]["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
        {"strategy": "css", "priority": 2, "status": "SUCCESS"},
    ]


def test_real_chrome_single_locator_can_appear_within_the_shared_node_budget(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    _server, base_url = matrix_server
    plan = _plan(
        f"{base_url}?delay_ms=1500",
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                4_000,
                "STOP",
                None,
                _locator(("css", "#delayed-target", 1)),
                None,
                None,
            ),
        ),
        assertions=_single_delayed_click_assertion(),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "SUCCESS"
    assert result.traces[0]["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "SUCCESS"}
    ]


def test_real_chrome_repolls_delayed_fallback_within_the_shared_node_budget(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    _server, base_url = matrix_server
    plan = _plan(
        f"{base_url}?delay_ms=2500&primary_delay_ms=3500",
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                4_000,
                "STOP",
                None,
                _locator(
                    ("css", "#delayed-primary", 1),
                    ("css", "#delayed-target", 2),
                ),
                None,
                None,
            ),
        ),
        assertions=_single_delayed_click_assertion(),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "SUCCESS"
    assert result.traces[0]["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
        {"strategy": "css", "priority": 2, "status": "SUCCESS"},
    ]


def test_real_chrome_all_missing_locators_keep_not_found_healing_context(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    server, base_url = matrix_server
    plan = _plan(
        base_url,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                1_000,
                "STOP",
                None,
                WebLocatorPlan(
                    811,
                    (
                        WebLocatorCandidate("css", "#missing-one", 1),
                        WebLocatorCandidate("css", "#missing-two", 2),
                    ),
                ),
                None,
                None,
            ),
        ),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "FAILED"
    trace = result.traces[0]
    assert trace["status"] == "FAILED"
    assert trace["error_type"] == "WEB_LOCATOR_NOT_FOUND"
    assert trace["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
        {"strategy": "css", "priority": 2, "status": "NOT_FOUND"},
    ]
    assert trace["healing_context"]["trigger"] == "ALL_LOCATORS_FAILED"
    assert trace["healing_context"]["element_version_id"] == 811
    assert server.sensitive_page_value not in json.dumps(trace["healing_context"])
    validated = _validate_with_backend_web_trace(trace)
    assert validated.error_type == "WEB_LOCATOR_NOT_FOUND"


def test_real_chrome_partial_locator_probe_timeout_does_not_claim_all_not_found(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    _server, base_url = matrix_server
    candidates = tuple(
        WebLocatorCandidate("css", f"#missing-{index}", index)
        for index in range(1, 11)
    )
    plan = _plan(
        base_url,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                1_000,
                "STOP",
                None,
                WebLocatorPlan(812, candidates),
                None,
                None,
            ),
        ),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "TIMEOUT"
    trace = result.traces[0]
    assert trace["status"] == "TIMEOUT"
    assert trace["error_type"] == "WEB_NODE_TIMEOUT"
    assert 0 < len(trace["locator_attempts"]) < len(candidates)
    assert all(item["status"] == "NOT_FOUND" for item in trace["locator_attempts"])
    assert "healing_context" not in trace
    validated = _validate_with_backend_web_trace(trace)
    assert validated.healing_context is None


def test_real_chrome_reuses_valid_synthetic_session_storage_state(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    _server, base_url = matrix_server
    cookie_value = secrets.token_urlsafe(24)
    session = WebSessionPlan(
        901,
        {
            "cookies": [
                {
                    "name": "matrix_session",
                    "value": cookie_value,
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        },
    )
    plan = _plan(
        base_url,
        session=session,
        assertions=(
            WebExecutionAssertionPlan(
                "ASSERT_TEXT",
                3_000,
                _locator(("css", "#session-status", 1)),
                "SESSION_REUSED",
            ),
        ),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "SUCCESS"
    assert result.traces[0]["status"] == "SUCCESS"


def test_real_chrome_evidence_redacts_synthetic_sensitive_markers(
    matrix_server: tuple[_MatrixServer, str], tmp_path: Path
) -> None:
    server, base_url = matrix_server
    input_marker = secrets.token_urlsafe(24)
    plan = _plan(
        base_url,
        initial_context={"sensitive_value": input_marker},
        actions=(
            WebExecutionActionPlan(
                "FILL",
                3_000,
                "STOP",
                None,
                _locator(("css", "#sensitive-input", 1)),
                "{{sensitive_value}}",
                None,
            ),
            WebExecutionActionPlan(
                "CLICK",
                3_000,
                "STOP",
                None,
                _locator(("css", "#fallback-target", 1)),
                None,
                None,
            ),
        ),
        assertions=_clicked_assertion(),
    )
    evidence_root = tmp_path / "privacy-evidence"
    evidence_root.mkdir()

    result = WebExecutor(evidence_dir=evidence_root).execute(plan)

    assert result.outcome == "SUCCESS"
    artifacts = validate_manifest(evidence_root, result.evidence.to_wire())
    by_type = {artifact.artifact_type: artifact for artifact in artifacts}
    assert {"SCREENSHOT", "PLAYWRIGHT_TRACE", "CONSOLE_ERROR", "WEB_SUMMARY"} <= set(
        by_type
    )
    forbidden_values = (input_marker, server.sensitive_page_value)
    forbidden_bytes = tuple(value.encode() for value in forbidden_values)
    leaked_categories = {
        artifact.artifact_type
        for artifact in artifacts
        if any(value in artifact.path.read_bytes() for value in forbidden_bytes)
    }
    assert not leaked_categories
    console = json.loads(by_type["CONSOLE_ERROR"].path.read_text(encoding="utf-8"))
    assert console["message"] == "matrix-console=[REDACTED]"
    assert _png_color_pixel_count(by_type["SCREENSHOT"].path, (255, 0, 255)) >= 1_000
    trace_bytes = by_type["PLAYWRIGHT_TRACE"].path.read_bytes()
    assert audit_trace_bytes(trace_bytes, forbidden_values=forbidden_values)


def test_real_spawn_holds_one_web_slot_and_releases_process_and_temp_dir(
    matrix_server: tuple[_MatrixServer, str], tmp_path: Path
) -> None:
    _server, base_url = matrix_server
    plan = _plan(
        base_url,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                3_000,
                "STOP",
                None,
                _locator(("css", "#fallback-target", 1)),
                None,
                None,
            ),
        ),
        assertions=_clicked_assertion(),
    )
    slots = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})
    lease = slots.try_acquire("WEB")
    assert lease is not None
    assert slots.available()["WEB"] == 0
    assert slots.try_acquire("WEB") is None
    temp_path: Path | None = None
    process = None
    try:
        with TemporaryDirectory(prefix="p5f-real-spawn-", dir=tmp_path) as evidence_dir:
            temp_path = Path(evidence_dir)
            process = spawn_web_task_process(plan, evidence_dir)
            process.start()
            outcome = _wait_for_process(process)
            assert outcome.kind == "WEB_RESULT"
            assert outcome.result is not None
            assert outcome.result["outcome"] == "SUCCESS"
            assert process.exitcode == 0
            validate_manifest(evidence_dir, outcome.result["evidence"])
    finally:
        if process is not None:
            process.close()
        lease.release()
    assert temp_path is not None and not temp_path.exists()
    assert slots.available()["WEB"] == 1


def test_real_spawn_cancel_reaches_browser_execution(
    matrix_server: tuple[_MatrixServer, str],
) -> None:
    server, base_url = matrix_server
    slow_url = f"{base_url}slow?duration=1.2"
    plan = _plan(
        slow_url,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                3_000,
                "STOP",
                None,
                _locator(("css", "#fallback-target", 1)),
                None,
                None,
            ),
        ),
    )
    process = spawn_web_task_process(plan)
    try:
        process.start()
        assert server.slow_started.wait(timeout=20)
        process.request_cancel()
        outcome = _wait_for_process(process)
        assert outcome.kind == "WEB_RESULT"
        assert outcome.result is not None
        assert outcome.result["outcome"] == "CANCELLED"
        assert outcome.result["error_type"] == "CANCEL_REQUESTED"
        assert process.exitcode == 0
    finally:
        process.close()


def test_real_spawn_force_stop_physically_terminates_child(
    matrix_server: tuple[_MatrixServer, str], tmp_path: Path
) -> None:
    server, base_url = matrix_server
    plan = _plan(
        f"{base_url}slow?duration=15",
        total_timeout_ms=30_000,
        actions=_fallback_actions(),
    )
    temp_path: Path | None = None
    process = None
    descendant_pids: set[int] = set()
    profile_dirs: set[Path] = set()
    profiles_before = _playwright_profile_dirs()
    try:
        with TemporaryDirectory(prefix="p5f-force-stop-", dir=tmp_path) as evidence_dir:
            temp_path = Path(evidence_dir)
            process = spawn_web_task_process(plan, evidence_dir)
            process.start()
            assert server.slow_started.wait(timeout=20)
            assert process.is_alive()
            root_process = process._process  # noqa: SLF001 - test-only process-tree evidence
            assert root_process is not None and isinstance(root_process.pid, int)
            inventory = _windows_process_inventory()
            descendant_pids = _descendants(root_process.pid, inventory)
            assert descendant_pids
            descendant_names = {inventory[pid][1].lower() for pid in descendant_pids}
            assert "chrome.exe" in descendant_names
            profile_dirs = _playwright_profile_dirs() - profiles_before
            assert profile_dirs
            assert all(path.exists() for path in profile_dirs)
            process.terminate()
            process.join(timeout=5)
            assert not process.is_alive()
            assert process.exitcode not in {None, 0}
            assert process.outcome() is None
    finally:
        if process is not None:
            process.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        inventory = _windows_process_inventory()
        remaining = descendant_pids & set(inventory)
        remaining_profiles = {path for path in profile_dirs if path.exists()}
        if not remaining and not remaining_profiles:
            break
        time.sleep(0.2)
    assert not remaining
    assert not remaining_profiles
    assert not multiprocessing.active_children()
    assert temp_path is not None and not temp_path.exists()


def test_real_spawn_total_timeout_returns_bounded_terminal_result(
    matrix_server: tuple[_MatrixServer, str], tmp_path: Path
) -> None:
    _server, base_url = matrix_server
    plan = _plan(
        f"{base_url}slow?duration=3",
        total_timeout_ms=1_000,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                3_000,
                "STOP",
                None,
                _locator(("css", "#fallback-target", 1)),
                None,
                None,
            ),
        ),
    )
    temp_path: Path | None = None
    process = None
    started = time.monotonic()
    try:
        with TemporaryDirectory(prefix="p5f-total-timeout-", dir=tmp_path) as evidence_dir:
            temp_path = Path(evidence_dir)
            process = spawn_web_task_process(plan, evidence_dir)
            process.start()
            outcome = _wait_for_process(process, timeout_seconds=15)
            assert outcome.kind == "WEB_RESULT"
            assert outcome.result is not None
            assert outcome.result["outcome"] == "TIMEOUT"
            assert outcome.result["error_type"] in {"WEB_NODE_TIMEOUT", "WEB_TOTAL_TIMEOUT"}
            assert time.monotonic() - started < 10
            validate_manifest(evidence_dir, outcome.result["evidence"])
    finally:
        if process is not None:
            process.close()
    assert temp_path is not None and not temp_path.exists()
