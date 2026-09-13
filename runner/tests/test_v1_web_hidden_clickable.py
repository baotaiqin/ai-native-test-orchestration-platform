from __future__ import annotations

import json
import multiprocessing
import secrets
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from runner.errors import ProtocolError
from runner.evidence import validate_manifest
from runner.executors.web import WebExecutor
from runner.isolation import IsolatedOutcome, spawn_web_task_process
from runner.models import WebExecutionAssertionPlan, WebExecutionPlanResult
from runner.protocol import _web_assertions
from runner.snapshot import DefaultSnapshotProbe
from tests.test_p5f_real_chrome_matrix import (
    _descendants,
    _playwright_profile_dirs,
    _windows_process_inventory,
)

NEW_RULE_ASSERTIONS = ("ASSERT_HIDDEN", "ASSERT_CLICKABLE")


def _wire_locator(*selectors: str, locked: bool = True) -> dict[str, object]:
    return {
        "element_version_id": 701 if locked else None,
        "candidates": [
            {"strategy": "css", "value": selector, "priority": index}
            for index, selector in enumerate(selectors, start=1)
        ],
    }


def _wire_assertion(
    assertion_type: str,
    *,
    locator: object = None,
    expected: object = None,
    timeout_ms: object = 1_000,
) -> dict[str, object]:
    return {
        "type": assertion_type,
        "timeout_ms": timeout_ms,
        "locator": locator,
        "expected": expected,
    }


@pytest.mark.parametrize("assertion_type", NEW_RULE_ASSERTIONS)
def test_v1_w7_protocol_accepts_exact_shape(assertion_type: str) -> None:
    parsed = _web_assertions(
        [_wire_assertion(assertion_type, locator=_wire_locator("#target"))]
    )

    assert len(parsed) == 1
    assert parsed[0].type == assertion_type
    assert parsed[0].locator is not None
    assert parsed[0].expected is None


@pytest.mark.parametrize("assertion_type", NEW_RULE_ASSERTIONS)
def test_v1_w7_protocol_requires_locator_and_rejects_expected(
    assertion_type: str,
) -> None:
    with pytest.raises(ProtocolError, match="缺少 locator"):
        _web_assertions([_wire_assertion(assertion_type)])
    with pytest.raises(ProtocolError, match="不适用 expected"):
        _web_assertions(
            [
                _wire_assertion(
                    assertion_type,
                    locator=_wire_locator("#target"),
                    expected="value",
                )
            ]
        )


def test_v1_w7_protocol_rejects_bool_extra_field_and_unknown_type() -> None:
    with pytest.raises(ProtocolError, match="基本字段无效"):
        _web_assertions(
            [
                _wire_assertion(
                    "ASSERT_HIDDEN",
                    locator=_wire_locator("#target"),
                    timeout_ms=True,
                )
            ]
        )
    extra = _wire_assertion("ASSERT_CLICKABLE", locator=_wire_locator("#target"))
    extra["force"] = True
    with pytest.raises(ProtocolError, match="字段不符合协议"):
        _web_assertions([extra])
    with pytest.raises(ProtocolError, match="基本字段无效"):
        _web_assertions(
            [_wire_assertion("ASSERT_UNKNOWN", locator=_wire_locator("#target"))]
        )


class _W7Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _W7Handler)
        self.delayed_page_served = Event()
        self.private_marker = f"w7-private-{secrets.token_urlsafe(18)}"


class _W7Handler(BaseHTTPRequestHandler):
    server: _W7Server

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        if path == "/delayed-clickable":
            self.server.delayed_page_served.set()
            body = self._delayed_clickable_page()
        elif path == "/sensitive":
            body = self._sensitive_page()
        else:
            body = self._rule_page()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    @staticmethod
    def _rule_page() -> bytes:
        return b"""<!doctype html>
<html><head><meta charset="utf-8"><title>W7 rules</title>
<style>
  #spacer { height: 900px; }
  #covered-wrap { position: relative; width: 180px; height: 50px; }
  #covered, #cover { position: absolute; inset: 0; }
  #cover { z-index: 2; background: rgba(0, 0, 0, .2); }
</style></head><body>
<div id="hidden" style="display:none">hidden</div>
<button id="visible" type="button">visible</button>
<button id="delayed-hide" type="button">delayed hide</button>
<button id="disabled" type="button" disabled>disabled</button>
<button id="aria-disabled" type="button" aria-disabled="true">aria disabled</button>
<div id="covered-wrap"><button id="covered" type="button">covered</button>
  <div id="cover">cover</div></div>
<button id="hidden-clickable" type="button" style="display:none">hidden clickable</button>
<div id="spacer"></div>
<form id="form"><button id="clickable" type="submit">clickable</button></form>
<script>
  window.w7Effects = {clicks: 0, inputs: 0, submits: 0, navigations: 0};
  document.addEventListener('click', () => window.w7Effects.clicks++);
  document.addEventListener('input', () => window.w7Effects.inputs++);
  document.querySelector('#form').addEventListener('submit', event => {
    window.w7Effects.submits++;
    event.preventDefault();
  });
  window.addEventListener('hashchange', () => window.w7Effects.navigations++);
  window.setTimeout(() => document.querySelector('#delayed-hide').style.display='none', 250);
  window.setTimeout(() => {
    const button = document.createElement('button');
    button.id = 'late-clickable';
    button.type = 'button';
    button.textContent = 'late';
    document.body.appendChild(button);
  }, 350);
</script></body></html>"""

    @staticmethod
    def _delayed_clickable_page() -> bytes:
        return b"""<!doctype html><title>W7 cancel</title><body><script>
window.setTimeout(() => {
  const button = document.createElement('button');
  button.id = 'late-cancel';
  button.type = 'button';
  document.body.appendChild(button);
}, 900);
</script></body>"""

    def _sensitive_page(self) -> bytes:
        return f"""<!doctype html><title>{self.server.private_marker}</title>
<button id="safe-w7" data-testid="safe-w7-candidate"
  aria-label="{self.server.private_marker}">safe</button>""".encode()

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture(scope="module")
def w7_server() -> Iterator[tuple[_W7Server, str]]:
    server = _W7Server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


REAL_CHROME_UNAVAILABLE = not DefaultSnapshotProbe().web_ready()


@pytest.fixture(scope="module")
def real_page(w7_server: tuple[_W7Server, str]) -> Iterator[tuple[object, str, _W7Server]]:
    if REAL_CHROME_UNAVAILABLE:
        pytest.skip("Playwright 或本机 Chrome 不可用")
    server, base_url = w7_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context()
            try:
                yield context.new_page(), base_url, server
            finally:
                context.close()
        finally:
            browser.close()


def _assertion(
    assertion_type: str,
    *selectors: str,
    timeout_ms: int = 1_000,
    locked: bool = True,
) -> WebExecutionAssertionPlan:
    return _web_assertions(
        [
            _wire_assertion(
                assertion_type,
                locator=_wire_locator(*selectors, locked=locked),
                timeout_ms=timeout_ms,
            )
        ]
    )[0]


def _plan(
    start_url: str,
    assertions: tuple[WebExecutionAssertionPlan, ...],
    *,
    total_timeout_ms: int = 10_000,
    initial_context: dict[str, object] | None = None,
) -> WebExecutionPlanResult:
    return WebExecutionPlanResult(
        schema_version=1,
        run_id="run-v1-w7",
        message_id="message-v1-w7",
        runner_id="runner-v1-w7",
        case_run_id=1701,
        web_case_id=1702,
        web_case_version_id=1703,
        start_url=start_url,
        browser="CHROME",
        headless=True,
        total_timeout_ms=total_timeout_ms,
        initial_context=initial_context or {},
        actions=(),
        assertions=assertions,
        session=None,
    )


def _run_page(page: object, plan: WebExecutionPlanResult):
    return WebExecutor()._execute_page(  # noqa: SLF001 - real-browser acceptance
        page,
        plan,
        stop_event=None,
        deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
    )


def _assert_no_business_side_effects(page: object, start_url: str) -> None:
    assert page.evaluate("window.w7Effects") == {
        "clicks": 0,
        "inputs": 0,
        "submits": 0,
        "navigations": 0,
    }
    assert page.url == start_url


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w7_hidden_checks_all_candidates_and_waits_for_hidden(
    real_page: tuple[object, str, _W7Server],
) -> None:
    page, base_url, _server = real_page
    start_url = f"{base_url}/rules"

    missing = _run_page(page, _plan(start_url, (_assertion("ASSERT_HIDDEN", "#missing"),)))
    assert missing.outcome == "SUCCESS"
    assert missing.traces[0]["locator_attempts"][0]["status"] == "NOT_FOUND"
    assert "healing_context" not in missing.traces[0]

    hidden = _run_page(page, _plan(start_url, (_assertion("ASSERT_HIDDEN", "#hidden"),)))
    assert hidden.outcome == "SUCCESS"
    assert hidden.traces[0]["locator_attempts"][0]["status"] == "SUCCESS"

    delayed = _run_page(
        page,
        _plan(start_url, (_assertion("ASSERT_HIDDEN", "#delayed-hide", timeout_ms=1_000),)),
    )
    assert delayed.outcome == "SUCCESS"

    missing_and_hidden = _run_page(
        page,
        _plan(start_url, (_assertion("ASSERT_HIDDEN", "#missing", "#hidden"),)),
    )
    assert missing_and_hidden.outcome == "SUCCESS"
    assert [item["status"] for item in missing_and_hidden.traces[0]["locator_attempts"]] == [
        "NOT_FOUND",
        "SUCCESS",
    ]

    missing_and_visible = _run_page(
        page,
        _plan(
            start_url,
            (_assertion("ASSERT_HIDDEN", "#missing", "#visible", timeout_ms=300),),
        ),
    )
    assert missing_and_visible.outcome == "TIMEOUT"
    assert missing_and_visible.error_type == "WEB_NODE_TIMEOUT"
    assert [item["status"] for item in missing_and_visible.traces[0]["locator_attempts"]] == [
        "NOT_FOUND",
        "ACTION_FAILED",
    ]

    invalid = _run_page(page, _plan(start_url, (_assertion("ASSERT_HIDDEN", "["),)))
    assert invalid.outcome == "FAILED"
    assert invalid.error_type == "ASSERTION_FAILED"
    assert invalid.traces[0]["locator_attempts"][0]["status"] == "ACTION_FAILED"
    _assert_no_business_side_effects(page, start_url)


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w7_clickable_uses_trial_actionability_without_business_side_effects(
    real_page: tuple[object, str, _W7Server],
) -> None:
    page, base_url, _server = real_page
    start_url = f"{base_url}/rules"

    clickable = _run_page(
        page,
        _plan(start_url, (_assertion("ASSERT_CLICKABLE", "#clickable"),)),
    )
    assert clickable.outcome == "SUCCESS"
    _assert_no_business_side_effects(page, start_url)

    fallback = _run_page(
        page,
        _plan(
            start_url,
            (_assertion("ASSERT_CLICKABLE", "#missing", "#clickable"),),
        ),
    )
    assert fallback.outcome == "SUCCESS"
    assert [item["status"] for item in fallback.traces[0]["locator_attempts"]] == [
        "NOT_FOUND",
        "SUCCESS",
    ]
    _assert_no_business_side_effects(page, start_url)

    late = _run_page(
        page,
        _plan(
            start_url,
            (_assertion("ASSERT_CLICKABLE", "#late-clickable", timeout_ms=1_500),),
        ),
    )
    assert late.outcome == "SUCCESS"
    _assert_no_business_side_effects(page, start_url)

    for selector in ("#disabled", "#aria-disabled", "#covered", "#hidden-clickable"):
        rejected = _run_page(
            page,
            _plan(
                start_url,
                (_assertion("ASSERT_CLICKABLE", selector, timeout_ms=500),),
            ),
        )
        assert rejected.outcome == "TIMEOUT"
        assert rejected.error_type == "WEB_NODE_TIMEOUT"
        assert rejected.traces[0]["locator_attempts"][0]["status"] == "ACTION_FAILED"
        _assert_no_business_side_effects(page, start_url)


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w7_node_and_total_budgets_remain_classified(
    real_page: tuple[object, str, _W7Server],
) -> None:
    page, base_url, _server = real_page
    start_url = f"{base_url}/rules"
    node = _run_page(
        page,
        _plan(
            start_url,
            (_assertion("ASSERT_CLICKABLE", "#never", timeout_ms=300, locked=False),),
            total_timeout_ms=4_000,
        ),
    )
    assert node.outcome == "TIMEOUT"
    assert node.error_type == "WEB_NODE_TIMEOUT"

    total = _run_page(
        page,
        _plan(
            start_url,
            (_assertion("ASSERT_HIDDEN", "#visible", timeout_ms=3_000),),
            total_timeout_ms=700,
        ),
    )
    assert total.outcome == "TIMEOUT"
    assert total.error_type == "WEB_TOTAL_TIMEOUT"
    assert total.traces[0]["node_id"] == "assertion_1"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w7_sensitive_values_stay_out_of_result_evidence_and_healing(
    w7_server: tuple[_W7Server, str], tmp_path: Path
) -> None:
    server, base_url = w7_server
    title_assertion = _web_assertions(
        [
            _wire_assertion(
                "ASSERT_TITLE",
                locator=None,
                expected="{{private}}",
            )
        ]
    )[0]
    plan = _plan(
        f"{base_url}/sensitive",
        (title_assertion, _assertion("ASSERT_CLICKABLE", "#missing", timeout_ms=500)),
        initial_context={"private": server.private_marker},
    )
    evidence_dir = tmp_path / "w7-private-evidence"
    evidence_dir.mkdir()
    process = spawn_web_task_process(plan, evidence_dir)
    try:
        process.start()
        outcome = _wait_for_process(process)
    finally:
        process.close()

    assert outcome.kind == "WEB_RESULT"
    assert outcome.result is not None
    assert outcome.result["outcome"] == "FAILED"
    assert outcome.result["error_type"] == "ASSERTION_FAILED"
    healing = outcome.result["traces"][1]["healing_context"]
    assert healing["element_version_id"] == 701
    assert healing["page_title"] == "[REDACTED]"
    safe_candidate = next(
        candidate
        for candidate in healing["dom_candidates"]
        if candidate.get("id") == "safe-w7"
    )
    assert safe_candidate.get("data-testid") == "safe-w7-candidate"
    assert "aria-label" not in safe_candidate

    artifacts = validate_manifest(evidence_dir, outcome.result["evidence"])
    result_wire = json.dumps(outcome.result, ensure_ascii=False)
    leak_flags = {
        "result": server.private_marker in result_wire,
        "artifacts": any(
            server.private_marker.encode() in artifact.path.read_bytes()
            for artifact in artifacts
        ),
    }
    assert not any(leak_flags.values()), f"W7 private marker leak flags: {leak_flags}"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w7_cancel_converges_and_cleans_owned_browser(
    w7_server: tuple[_W7Server, str], tmp_path: Path
) -> None:
    server, base_url = w7_server
    server.delayed_page_served.clear()
    plan = _plan(
        f"{base_url}/delayed-clickable",
        (
            _assertion("ASSERT_CLICKABLE", "#late-cancel", timeout_ms=4_000, locked=False),
            _assertion("ASSERT_HIDDEN", "#after-cancel"),
        ),
    )
    profiles_before = _playwright_profile_dirs()
    descendant_pids: set[int] = set()
    profile_dirs: set[Path] = set()
    process = None
    evidence_path: Path | None = None
    try:
        with TemporaryDirectory(prefix="v1-w7-cancel-", dir=tmp_path) as evidence_dir:
            evidence_path = Path(evidence_dir)
            process = spawn_web_task_process(plan, evidence_dir)
            process.start()
            assert server.delayed_page_served.wait(timeout=20)
            time.sleep(0.15)
            root = process._process  # noqa: SLF001 - test-only ownership evidence
            assert root is not None and isinstance(root.pid, int)
            inventory = _windows_process_inventory()
            descendant_pids = _descendants(root.pid, inventory)
            assert "chrome.exe" in {inventory[pid][1].lower() for pid in descendant_pids}
            profile_dirs = _playwright_profile_dirs() - profiles_before
            assert profile_dirs

            process.request_cancel()
            outcome = _wait_for_process(process)

            assert outcome.kind == "WEB_RESULT"
            assert outcome.result is not None
            assert outcome.result["outcome"] == "CANCELLED"
            assert outcome.result["error_type"] == "CANCEL_REQUESTED"
            assert process.exitcode == 0
    finally:
        if process is not None:
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
    assert evidence_path is not None and not evidence_path.exists()


def _wait_for_process(process: object, timeout_seconds: float = 30.0) -> IsolatedOutcome:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll(0.1):  # type: ignore[attr-defined]
            break
    else:
        pytest.fail("isolated V1 W7 process did not finish within the bounded wait")
    process.join(timeout=5.0)  # type: ignore[attr-defined]
    assert not process.is_alive()  # type: ignore[attr-defined]
    outcome = process.outcome()  # type: ignore[attr-defined]
    assert isinstance(outcome, IsolatedOutcome)
    return outcome
