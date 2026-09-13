from __future__ import annotations

import json
import multiprocessing
import secrets
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

import pytest

from runner.errors import ProtocolError
from runner.evidence import audit_trace_bytes, validate_manifest
from runner.executors.web import WebExecutor
from runner.isolation import IsolatedOutcome, spawn_web_task_process
from runner.models import (
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionPlanResult,
    WebLocatorCandidate,
    WebLocatorPlan,
)
from runner.protocol import _web_actions, _web_assertions
from runner.snapshot import DefaultSnapshotProbe
from tests.test_p5f_real_chrome_matrix import (
    _descendants,
    _playwright_profile_dirs,
    _windows_process_inventory,
)

NEW_LOCATOR_ACTIONS = (
    "DOUBLE_CLICK",
    "RIGHT_CLICK",
    "CLEAR",
    "HOVER",
    "CHECK",
    "UNCHECK",
    "RADIO",
    "ENTER",
    "TAB",
)
NEW_PAGE_ACTIONS = ("RELOAD", "BACK", "FORWARD", "WAIT_NETWORK_IDLE")


def _wire_locator() -> dict[str, object]:
    return {
        "element_version_id": 91,
        "candidates": [{"strategy": "css", "value": "#target", "priority": 1}],
    }


def _wire_action(action_type: str, *, locator: object = None) -> dict[str, object]:
    return {
        "type": action_type,
        "timeout_ms": 1_000,
        "failure_policy": "STOP",
        "url": None,
        "locator": locator,
        "value": None,
        "key": None,
    }


@pytest.mark.parametrize("action_type", NEW_LOCATOR_ACTIONS)
def test_v1_new_locator_action_protocol_accepts_exact_shape(action_type: str) -> None:
    parsed = _web_actions([_wire_action(action_type, locator=_wire_locator())])

    assert len(parsed) == 1
    assert parsed[0].type == action_type
    assert parsed[0].locator is not None
    assert parsed[0].locator.element_version_id == 91


@pytest.mark.parametrize("action_type", NEW_PAGE_ACTIONS)
def test_v1_new_page_action_protocol_accepts_exact_shape(action_type: str) -> None:
    parsed = _web_actions([_wire_action(action_type)])

    assert len(parsed) == 1
    assert parsed[0].type == action_type
    assert parsed[0].locator is None


@pytest.mark.parametrize("action_type", NEW_LOCATOR_ACTIONS)
def test_v1_new_locator_action_protocol_requires_locator(action_type: str) -> None:
    with pytest.raises(ProtocolError, match="缺少 locator"):
        _web_actions([_wire_action(action_type)])


@pytest.mark.parametrize("action_type", NEW_LOCATOR_ACTIONS)
@pytest.mark.parametrize(
    ("field", "value"),
    (("url", "http://example.test/override"), ("value", "override"), ("key", "Escape")),
)
def test_v1_new_locator_action_protocol_rejects_semantic_override_fields(
    action_type: str, field: str, value: str
) -> None:
    action = _wire_action(action_type, locator=_wire_locator())
    action[field] = value

    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions([action])


@pytest.mark.parametrize("action_type", NEW_PAGE_ACTIONS)
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("url", "http://example.test/override"),
        ("locator", _wire_locator()),
        ("value", "override"),
        ("key", "Escape"),
    ),
)
def test_v1_new_page_action_protocol_rejects_all_action_fields(
    action_type: str, field: str, value: object
) -> None:
    action = _wire_action(action_type)
    action[field] = value

    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions([action])


def test_v1_web_protocol_rejects_unknown_or_missing_action_fields() -> None:
    unknown = _wire_action("RUN_ARBITRARY_METHOD")
    with pytest.raises(ProtocolError, match="基本字段无效"):
        _web_actions([unknown])

    missing = _wire_action("RELOAD")
    missing.pop("failure_policy")
    with pytest.raises(ProtocolError, match="字段不符合协议"):
        _web_actions([missing])


def test_v1_existing_web_actions_and_assertions_remain_compatible() -> None:
    locator = _wire_locator()
    actions = [
        {**_wire_action("GOTO"), "url": "http://example.test/next"},
        {**_wire_action("FILL", locator=locator), "value": "value"},
        _wire_action("CLICK", locator=locator),
        {**_wire_action("SELECT", locator=locator), "value": "option"},
        {**_wire_action("PRESS", locator=locator), "key": "Escape"},
        _wire_action("WAIT_ELEMENT", locator=locator),
        {**_wire_action("WAIT_URL"), "url": "http://example.test/next"},
    ]
    assertions = [
        {"type": "ASSERT_VISIBLE", "timeout_ms": 1_000, "locator": locator, "expected": None},
        {"type": "ASSERT_TEXT", "timeout_ms": 1_000, "locator": locator, "expected": "ok"},
        {
            "type": "ASSERT_URL",
            "timeout_ms": 1_000,
            "locator": None,
            "expected": "http://example.test/next",
        },
    ]

    assert [item.type for item in _web_actions(actions)] == [
        "GOTO",
        "FILL",
        "CLICK",
        "SELECT",
        "PRESS",
        "WAIT_ELEMENT",
        "WAIT_URL",
    ]
    assert [item.type for item in _web_assertions(assertions)] == [
        "ASSERT_VISIBLE",
        "ASSERT_TEXT",
        "ASSERT_URL",
    ]


class _V1WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _V1WebHandler)
        self.counts: dict[str, int] = {}
        self.count_lock = Lock()
        self.slow_reload_started = Event()
        self.release_slow_reload = Event()
        self.hang_started = Event()
        self.release_hang = Event()
        self.secret = secrets.token_urlsafe(24)

    def increment(self, path: str) -> int:
        with self.count_lock:
            count = self.counts.get(path, 0) + 1
            self.counts[path] = count
            return count


class _V1WebHandler(BaseHTTPRequestHandler):
    server: _V1WebServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        count = self.server.increment(path)
        if path == "/hang":
            self.server.hang_started.set()
            self.server.release_hang.wait(timeout=15)
            self._send(b"", content_type="application/octet-stream")
            return
        if path == "/slow-reload" and count >= 2:
            self.server.slow_reload_started.set()
            self.server.release_slow_reload.wait(timeout=15)
        if path == "/actions":
            self._send(self._actions_page())
            return
        if path == "/busy":
            self._send(
                b"<!doctype html><title>busy</title><div id='busy'>busy</div>"
                b"<script>fetch('/hang').catch(() => {});</script>"
            )
            return
        label = path.removeprefix("/") or "root"
        self._send(f"<!doctype html><title>{label}</title><div>{label}</div>".encode())

    def _actions_page(self) -> bytes:
        secret = self.server.secret
        return f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>V1 Web actions</title></head>
  <body>
    <button id="double">double</button>
    <button id="right">right</button>
    <input id="clear" value="initial">
    <button id="hover">hover</button>
    <input id="check" type="checkbox">
    <input id="uncheck" type="checkbox" checked>
    <input id="radio" type="radio" name="choice">
    <input id="not-radio" type="checkbox" data-sensitive value="{secret}">
    <input id="enter">
    <button id="tab-start">tab start</button><button id="tab-next">tab next</button>
    <input id="sensitive" data-sensitive value="{secret}">
    <div id="result">ready</div>
    <script>
      const tokens = new Map();
      const mark = (key, value = key) => {{
        tokens.set(key, value);
        document.querySelector('#result').textContent = [...tokens.values()].join('|');
      }};
      document.querySelector('#double').addEventListener('dblclick', () => mark('double'));
      document.querySelector('#right').addEventListener('contextmenu', event => {{
        event.preventDefault(); mark('right');
      }});
      document.querySelector('#clear').addEventListener('input', event => {{
        if (event.target.value === '') mark('clear');
      }});
      document.querySelector('#hover').addEventListener('mouseenter', () => mark('hover'));
      let checkChanges = 0;
      document.querySelector('#check').addEventListener('change', event => {{
        checkChanges += 1; mark('check', `check:${{event.target.checked}}:${{checkChanges}}`);
      }});
      let uncheckChanges = 0;
      document.querySelector('#uncheck').addEventListener('change', event => {{
        uncheckChanges += 1;
        mark('uncheck', `uncheck:${{event.target.checked}}:${{uncheckChanges}}`);
      }});
      let radioChanges = 0;
      document.querySelector('#radio').addEventListener('change', event => {{
        radioChanges += 1; mark('radio', `radio:${{event.target.checked}}:${{radioChanges}}`);
      }});
      document.querySelector('#enter').addEventListener('keydown', event => {{
        if (event.key === 'Enter') mark('enter');
      }});
      document.querySelector('#tab-next').addEventListener('focus', () => mark('tab'));
    </script>
  </body>
</html>""".encode()

    def _send(self, body: bytes, *, content_type: str = "text/html; charset=utf-8") -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def v1_web_server() -> Iterator[tuple[_V1WebServer, str]]:
    server = _V1WebServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.release_slow_reload.set()
        server.release_hang.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _locator(selector: str, *, fallback: bool = False) -> WebLocatorPlan:
    candidates = []
    if fallback:
        candidates.append(WebLocatorCandidate("css", "#intentionally-missing", 1))
    candidates.append(WebLocatorCandidate("css", selector, len(candidates) + 1))
    return WebLocatorPlan(91, tuple(candidates))


def _action(
    action_type: str,
    selector: str | None = None,
    *,
    timeout_ms: int = 4_000,
    failure_policy: str = "STOP",
    fallback: bool = False,
) -> WebExecutionActionPlan:
    return WebExecutionActionPlan(
        action_type,
        timeout_ms,
        failure_policy,
        None,
        _locator(selector, fallback=fallback) if selector is not None else None,
        None,
        None,
    )


def _assert_text(value: str) -> WebExecutionAssertionPlan:
    return WebExecutionAssertionPlan("ASSERT_TEXT", 3_000, _locator("#result"), value)


def _plan(
    start_url: str,
    *,
    actions: tuple[WebExecutionActionPlan, ...],
    assertions: tuple[WebExecutionAssertionPlan, ...] = (),
    total_timeout_ms: int = 30_000,
) -> WebExecutionPlanResult:
    return WebExecutionPlanResult(
        1,
        "run-v1-w1",
        "message-v1-w1",
        "runner-v1-w1",
        1101,
        1102,
        1103,
        start_url,
        "CHROME",
        True,
        total_timeout_ms,
        {},
        actions,
        assertions,
        None,
    )


def _goto(url: str) -> WebExecutionActionPlan:
    return WebExecutionActionPlan("GOTO", 4_000, "STOP", url, None, None, None)


REAL_CHROME_UNAVAILABLE = not DefaultSnapshotProbe().web_ready()


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_reload_back_and_forward_effects(
    v1_web_server: tuple[_V1WebServer, str],
) -> None:
    server, base_url = v1_web_server
    reload_url = f"{base_url}/reload"
    reload_result = WebExecutor().execute(
        _plan(reload_url, actions=(_action("RELOAD"),))
    )
    assert reload_result.outcome == "SUCCESS"
    assert server.counts["/reload"] == 2

    back_result = WebExecutor().execute(
        _plan(
            f"{base_url}/a",
            actions=(_goto(f"{base_url}/b"), _action("BACK")),
            assertions=(WebExecutionAssertionPlan("ASSERT_URL", 3_000, None, f"{base_url}/a"),),
        )
    )
    assert back_result.outcome == "SUCCESS"

    forward_result = WebExecutor().execute(
        _plan(
            f"{base_url}/a",
            actions=(_goto(f"{base_url}/b"), _action("BACK"), _action("FORWARD")),
            assertions=(WebExecutionAssertionPlan("ASSERT_URL", 3_000, None, f"{base_url}/b"),),
        )
    )
    assert forward_result.outcome == "SUCCESS"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_element_actions_are_effective_idempotent_and_private(
    v1_web_server: tuple[_V1WebServer, str],
) -> None:
    server, base_url = v1_web_server
    actions = (
        _action("DOUBLE_CLICK", "#double", fallback=True),
        _action("RIGHT_CLICK", "#right"),
        _action("CLEAR", "#clear"),
        _action("HOVER", "#hover"),
        _action("CHECK", "#check"),
        _action("CHECK", "#check"),
        _action("UNCHECK", "#uncheck"),
        _action("UNCHECK", "#uncheck"),
        _action("RADIO", "#radio"),
        _action("RADIO", "#radio"),
        _action("ENTER", "#enter"),
        _action("TAB", "#tab-start"),
        _action("WAIT_NETWORK_IDLE"),
    )
    expected = (
        "double",
        "right",
        "clear",
        "hover",
        "check:true:1",
        "uncheck:false:1",
        "radio:true:1",
        "enter",
        "tab",
    )
    plan = _plan(
        f"{base_url}/actions",
        actions=actions,
        assertions=tuple(_assert_text(value) for value in expected),
    )

    with TemporaryDirectory(prefix="v1-w1-evidence-") as evidence_dir:
        result = WebExecutor(evidence_dir=evidence_dir).execute(plan)
        artifacts = validate_manifest(evidence_dir, result.evidence.to_wire())
        forbidden = (server.secret,)
        assert result.outcome == "SUCCESS"
        assert result.traces[0]["locator_attempts"] == [
            {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
            {"strategy": "css", "priority": 2, "status": "SUCCESS"},
        ]
        assert server.secret not in json.dumps(result.to_wire(), ensure_ascii=False)
        assert all(server.secret.encode() not in item.path.read_bytes() for item in artifacts)
        trace = next(item for item in artifacts if item.artifact_type == "PLAYWRIGHT_TRACE")
        assert audit_trace_bytes(trace.path.read_bytes(), forbidden_values=forbidden)


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_radio_rejects_non_radio_and_continue_runs_later_action(
    v1_web_server: tuple[_V1WebServer, str],
) -> None:
    server, base_url = v1_web_server
    plan = _plan(
        f"{base_url}/actions",
        actions=(
            _action("RADIO", "#not-radio", failure_policy="CONTINUE"),
            _action("HOVER", "#hover"),
        ),
        assertions=(_assert_text("hover"),),
    )

    result = WebExecutor().execute(plan)

    assert result.outcome == "FAILED"
    assert [item["status"] for item in result.traces] == ["FAILED", "SUCCESS", "SUCCESS"]
    assert result.traces[0]["error_type"] == "WEB_ACTION_FAILED"
    assert server.secret not in json.dumps(result.to_wire(), ensure_ascii=False)


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_network_idle_honors_node_and_total_budgets(
    v1_web_server: tuple[_V1WebServer, str],
) -> None:
    server, base_url = v1_web_server
    node_started = time.monotonic()
    node_result = WebExecutor().execute(
        _plan(f"{base_url}/busy", actions=(_action("WAIT_NETWORK_IDLE", timeout_ms=600),))
    )
    assert server.hang_started.wait(timeout=5)
    assert node_result.outcome == "TIMEOUT"
    assert node_result.error_type == "WEB_NODE_TIMEOUT"
    assert time.monotonic() - node_started < 5

    total_started = time.monotonic()
    total_result = WebExecutor().execute(
        _plan(
            f"{base_url}/busy",
            actions=(_action("WAIT_NETWORK_IDLE", timeout_ms=4_000),),
            total_timeout_ms=900,
        )
    )
    assert total_result.outcome == "TIMEOUT"
    assert total_result.error_type == "WEB_TOTAL_TIMEOUT"
    assert time.monotonic() - total_started < 5


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_isolation_cancel_cleans_owned_reload_process_without_duplicate_action(
    v1_web_server: tuple[_V1WebServer, str], tmp_path: Path
) -> None:
    server, base_url = v1_web_server
    plan = _plan(
        f"{base_url}/slow-reload",
        actions=(_action("RELOAD", timeout_ms=10_000), _action("WAIT_NETWORK_IDLE")),
        total_timeout_ms=20_000,
    )
    profiles_before = _playwright_profile_dirs()
    process = spawn_web_task_process(plan, tmp_path / "isolation-evidence")
    descendant_pids: set[int] = set()
    profile_dirs: set[Path] = set()
    try:
        process.start()
        assert server.slow_reload_started.wait(timeout=20)
        root = process._process  # noqa: SLF001 - test-only ownership evidence
        assert root is not None and isinstance(root.pid, int)
        inventory = _windows_process_inventory()
        descendant_pids = _descendants(root.pid, inventory)
        assert "chrome.exe" in {inventory[pid][1].lower() for pid in descendant_pids}
        profile_dirs = _playwright_profile_dirs() - profiles_before
        assert profile_dirs

        process.request_cancel()
        server.release_slow_reload.set()
        outcome = _wait_for_process(process)

        assert outcome.kind == "WEB_RESULT"
        assert outcome.result is not None
        assert outcome.result["outcome"] == "CANCELLED"
        assert outcome.result["error_type"] == "CANCEL_REQUESTED"
        assert server.counts["/slow-reload"] == 2
        assert process.exitcode == 0
    finally:
        server.release_slow_reload.set()
        process.close()

    cleanup_deadline = time.monotonic() + 10
    remaining_pids = set(descendant_pids)
    remaining_profiles = set(profile_dirs)
    while time.monotonic() < cleanup_deadline:
        remaining_pids = descendant_pids & set(_windows_process_inventory())
        remaining_profiles = {path for path in profile_dirs if path.exists()}
        if not remaining_pids and not remaining_profiles:
            break
        time.sleep(0.2)
    assert not remaining_pids
    assert not remaining_profiles
    assert process not in multiprocessing.active_children()


def _wait_for_process(process: object, timeout_seconds: float = 30.0) -> IsolatedOutcome:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll(0.1):  # type: ignore[attr-defined]
            break
    else:
        pytest.fail("isolated V1 Web process did not finish within the bounded wait")
    process.join(timeout=5.0)  # type: ignore[attr-defined]
    assert not process.is_alive()  # type: ignore[attr-defined]
    outcome = process.outcome()  # type: ignore[attr-defined]
    assert isinstance(outcome, IsolatedOutcome)
    return outcome
