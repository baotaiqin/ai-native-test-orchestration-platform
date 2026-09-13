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
from runner.evidence import audit_trace_bytes, validate_manifest
from runner.executors.web import WebExecutor, _WebEvidenceCollector
from runner.isolation import IsolatedOutcome, spawn_web_task_process
from runner.models import WebExecutionAssertionPlan, WebExecutionPlanResult
from runner.protocol import _web_assertions
from runner.snapshot import DefaultSnapshotProbe
from tests.test_p5f_real_chrome_matrix import (
    _descendants,
    _playwright_profile_dirs,
    _windows_process_inventory,
)

NEW_LOCATOR_ASSERTIONS = (
    "ASSERT_EXISTS",
    "ASSERT_ENABLED",
    "ASSERT_TEXT_EQUAL",
    "ASSERT_INPUT_VALUE",
)


def _wire_locator(*selectors: str, locked: bool = True) -> dict[str, object]:
    return {
        "element_version_id": 201 if locked else None,
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


@pytest.mark.parametrize(
    ("assertion_type", "locator", "expected"),
    (
        ("ASSERT_EXISTS", _wire_locator("#target"), None),
        ("ASSERT_ENABLED", _wire_locator("#target"), None),
        ("ASSERT_TEXT_EQUAL", _wire_locator("#target"), ""),
        ("ASSERT_INPUT_VALUE", _wire_locator("#target"), ""),
        ("ASSERT_TITLE", None, ""),
    ),
)
def test_v1_new_assertion_protocol_accepts_exact_four_field_shape(
    assertion_type: str, locator: object, expected: object
) -> None:
    parsed = _web_assertions(
        [_wire_assertion(assertion_type, locator=locator, expected=expected)]
    )

    assert len(parsed) == 1
    assert parsed[0].type == assertion_type
    assert parsed[0].expected == expected


@pytest.mark.parametrize("assertion_type", NEW_LOCATOR_ASSERTIONS)
def test_v1_new_locator_assertion_protocol_requires_locator(assertion_type: str) -> None:
    expected = None if assertion_type in {"ASSERT_EXISTS", "ASSERT_ENABLED"} else "value"
    with pytest.raises(ProtocolError, match="缺少 locator"):
        _web_assertions([_wire_assertion(assertion_type, expected=expected)])


@pytest.mark.parametrize("assertion_type", ("ASSERT_TEXT_EQUAL", "ASSERT_INPUT_VALUE"))
def test_v1_new_value_assertion_protocol_requires_expected(assertion_type: str) -> None:
    with pytest.raises(ProtocolError, match="缺少 expected"):
        _web_assertions([_wire_assertion(assertion_type, locator=_wire_locator("#target"))])


@pytest.mark.parametrize("assertion_type", ("ASSERT_EXISTS", "ASSERT_ENABLED"))
def test_v1_new_state_assertion_protocol_rejects_expected(assertion_type: str) -> None:
    with pytest.raises(ProtocolError, match="不适用 expected"):
        _web_assertions(
            [
                _wire_assertion(
                    assertion_type, locator=_wire_locator("#target"), expected="override"
                )
            ]
        )


def test_v1_title_protocol_requires_expected_and_rejects_locator() -> None:
    with pytest.raises(ProtocolError, match="缺少 expected"):
        _web_assertions([_wire_assertion("ASSERT_TITLE")])
    with pytest.raises(ProtocolError, match="不适用 locator"):
        _web_assertions(
            [
                _wire_assertion(
                    "ASSERT_TITLE", locator=_wire_locator("title"), expected="title"
                )
            ]
        )


def test_v1_assertion_protocol_rejects_bool_unknown_and_extra_fields() -> None:
    with pytest.raises(ProtocolError, match="基本字段无效"):
        _web_assertions(
            [
                _wire_assertion(
                    "ASSERT_EXISTS", locator=_wire_locator("#target"), timeout_ms=True
                )
            ]
        )
    with pytest.raises(ProtocolError, match="基本字段无效"):
        _web_assertions([_wire_assertion("ASSERT_UNSUPPORTED", locator=_wire_locator("#target"))])
    extra = _wire_assertion("ASSERT_EXISTS", locator=_wire_locator("#target"))
    extra["attribute"] = "disabled"
    with pytest.raises(ProtocolError, match="字段不符合协议"):
        _web_assertions([extra])


def test_v1_existing_assertion_payloads_remain_compatible() -> None:
    locator = _wire_locator("#target")
    parsed = _web_assertions(
        [
            _wire_assertion("ASSERT_VISIBLE", locator=locator, expected="legacy-ignored"),
            _wire_assertion("ASSERT_TEXT", locator=locator, expected="contains"),
            _wire_assertion("ASSERT_URL", locator=locator, expected="http://example.test/"),
        ]
    )

    assert [item.type for item in parsed] == ["ASSERT_VISIBLE", "ASSERT_TEXT", "ASSERT_URL"]
    assert parsed[0].expected == "legacy-ignored"
    assert parsed[2].locator is not None


class _AssertionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _AssertionHandler)
        self.delayed_page_served = Event()
        self.secret_title = f"private-title-{secrets.token_urlsafe(18)}" + " title-segment" * 30
        self.secret_input = f"private-input-{secrets.token_urlsafe(18)}" + " input-segment" * 30
        self.secret_text = f"private-text-{secrets.token_urlsafe(18)}" + " text-segment" * 30
        self.long_title_marker = f"late-title-{secrets.token_urlsafe(18)}"
        self.long_title = self.long_title_marker + " paragraph" * 80
        self.oversize_title_marker = f"oversize-title-{secrets.token_urlsafe(18)}"
        self.oversize_title = self.oversize_title_marker + "x" * 10_100
        self.planned_secret = f"planned-healing-{secrets.token_urlsafe(18)}" + " value" * 50
        self.action_title_secret = f"action-title-{secrets.token_urlsafe(18)}"
        self.action_fill_secret = f"action-fill-{secrets.token_urlsafe(18)}"
        self.action_select_secret = f"action-select-{secrets.token_urlsafe(18)}"


class _AssertionHandler(BaseHTTPRequestHandler):
    server: _AssertionServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        if path == "/private":
            body = self._private_page()
        elif path == "/private-input":
            body = self._private_input_page()
        elif path == "/delayed":
            self.server.delayed_page_served.set()
            body = self._delayed_page()
        elif path == "/long-title":
            body = f"<!doctype html><title>{self.server.long_title}</title>safe".encode()
        elif path == "/oversize-title":
            body = f"<!doctype html><title>{self.server.oversize_title}</title>safe".encode()
        elif path == "/healing":
            body = self._healing_page()
        elif path == "/planned-healing":
            body = self._planned_healing_page()
        elif path == "/action-healing":
            body = self._action_healing_page()
        elif path == "/empty-title":
            body = b"<!doctype html><div id='empty-title'>empty title</div>"
        else:
            body = self._assertion_page()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    @staticmethod
    def _assertion_page() -> bytes:
        return b"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>V1 Exact Title</title></head>
  <body>
    <div id="hidden" style="display:none">Hidden attached value</div>
    <button id="enabled">enabled</button>
    <button id="disabled" disabled>disabled</button>
    <div id="aria-disabled" role="button" aria-disabled="true" tabindex="0">aria disabled</div>
    <div id="text">Hello World</div>
    <input id="empty-input" value="">
    <input id="filled-input" value="Input Value">
    <div id="not-input">Input Value</div>
    <script>
      window.assertionSideEffects = {clicks: 0, inputs: 0, changes: 0};
      for (const element of document.querySelectorAll('button,[role=button]')) {
        element.addEventListener('click', () => window.assertionSideEffects.clicks++);
      }
      for (const element of document.querySelectorAll('input')) {
        element.addEventListener('input', () => window.assertionSideEffects.inputs++);
        element.addEventListener('change', () => window.assertionSideEffects.changes++);
      }
    </script>
  </body>
</html>"""

    def _private_page(self) -> bytes:
        return f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>{self.server.secret_title}</title></head>
  <body>
    <input id="private-input" data-sensitive value="{self.server.secret_input}">
    <div id="private-text" data-sensitive>{self.server.secret_text}</div>
    <script>
      console.error('private=' + {self.server.secret_input!r} + ':'
        + {self.server.secret_title!r} + ':' + {self.server.secret_text!r});
    </script>
  </body>
</html>""".encode()

    def _private_input_page(self) -> bytes:
        return f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Safe title</title></head>
  <body>
    <input id="private-input" data-sensitive value="{self.server.secret_input}">
    <script>console.error('private=' + {self.server.secret_input!r});</script>
  </body>
</html>""".encode()

    def _healing_page(self) -> bytes:
        return f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>{self.server.secret_title}</title></head>
  <body>
    <input id="observed-input" value="{self.server.secret_input}">
    <div id="observed-text">{self.server.secret_text}</div>
    <button id="safe-healing" data-testid="safe-candidate"
      aria-label="{self.server.secret_title}" title="{self.server.secret_input}">safe</button>
  </body>
</html>""".encode()

    def _planned_healing_page(self) -> bytes:
        return f"""<!doctype html><title>Safe planned title</title>
<button id="safe-planned" data-testid="safe-planned-candidate"
  aria-label="{self.server.planned_secret}"
  title="{self.server.oversize_title}">safe</button>""".encode()

    def _action_healing_page(self) -> bytes:
        return f"""<!doctype html><title>{self.server.action_title_secret}</title>
<button id="safe-action" data-testid="safe-action-candidate"
  aria-label="{self.server.action_title_secret}"
  title="{self.server.action_fill_secret}">safe</button>
<select id="safe-select" data-testid="safe-select-candidate"
  aria-label="{self.server.action_select_secret}"><option>safe</option></select>""".encode()

    @staticmethod
    def _delayed_page() -> bytes:
        return b"""<!doctype html><title>Delayed assertion</title>
<div id="ready">ready</div>
<script>
  window.setTimeout(() => {
    const node = document.createElement('div');
    node.id = 'late';
    node.style.display = 'none';
    document.body.appendChild(node);
  }, 900);
</script>"""

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture(scope="module")
def assertion_server() -> Iterator[tuple[_AssertionServer, str]]:
    server = _AssertionServer()
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
def real_page(
    assertion_server: tuple[_AssertionServer, str],
) -> Iterator[tuple[object, str, _AssertionServer]]:
    if REAL_CHROME_UNAVAILABLE:
        pytest.skip("Playwright 或本机 Chrome 不可用")
    server, base_url = assertion_server
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
    selector: str | None = None,
    expected: str | None = None,
    *,
    timeout_ms: int = 1_500,
    fallback: bool = False,
    locked: bool = True,
) -> WebExecutionAssertionPlan:
    locator = None
    if selector is not None:
        selectors = ("#intentionally-missing", selector) if fallback else (selector,)
        locator = _wire_locator(*selectors, locked=locked)
    return _web_assertions(
        [
            _wire_assertion(
                assertion_type,
                locator=locator,
                expected=expected,
                timeout_ms=timeout_ms,
            )
        ]
    )[0]


def _plan(
    start_url: str,
    assertions: tuple[WebExecutionAssertionPlan, ...],
    *,
    initial_context: dict[str, object] | None = None,
    total_timeout_ms: int = 10_000,
) -> WebExecutionPlanResult:
    return WebExecutionPlanResult(
        schema_version=1,
        run_id="run-v1-w4",
        message_id="message-v1-w4",
        runner_id="runner-v1-w4",
        case_run_id=1401,
        web_case_id=1402,
        web_case_version_id=1403,
        start_url=start_url,
        browser="CHROME",
        headless=True,
        total_timeout_ms=total_timeout_ms,
        initial_context=initial_context or {},
        actions=(),
        assertions=assertions,
        session=None,
    )


def _run_page(
    page: object,
    plan: WebExecutionPlanResult,
    *,
    stop_event: object | None = None,
) -> object:
    return WebExecutor()._execute_page(  # noqa: SLF001 - real-browser executor acceptance
        page,
        plan,
        stop_event=stop_event,
        deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
    )


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_new_assertions_succeed_without_interaction_side_effects(
    real_page: tuple[object, str, _AssertionServer],
) -> None:
    page, base_url, _server = real_page
    assertions = (
        _assertion("ASSERT_EXISTS", "#hidden", fallback=True),
        _assertion("ASSERT_ENABLED", "#enabled"),
        _assertion("ASSERT_TEXT_EQUAL", "#text", "{{exact_text}}"),
        _assertion("ASSERT_INPUT_VALUE", "#empty-input", ""),
        _assertion("ASSERT_INPUT_VALUE", "#filled-input", "{{input_value}}"),
        _assertion("ASSERT_TITLE", expected="{{title}}"),
        _assertion("ASSERT_TEXT", "#text", "Hello"),
    )
    plan = _plan(
        f"{base_url}/assertions",
        assertions,
        initial_context={
            "exact_text": "Hello World",
            "input_value": "Input Value",
            "title": "V1 Exact Title",
        },
    )

    result = _run_page(page, plan)

    assert result.outcome == "SUCCESS", (
        result.error_type,
        [(item["node_id"], item["status"], item["error_type"]) for item in result.traces],
    )
    assert page.locator("#hidden").is_visible() is False
    assert result.traces[0]["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
        {"strategy": "css", "priority": 2, "status": "SUCCESS"},
    ]
    assert page.evaluate("window.assertionSideEffects") == {
        "clicks": 0,
        "inputs": 0,
        "changes": 0,
    }


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
@pytest.mark.parametrize(
    ("assertion_type", "selector", "expected"),
    (
        ("ASSERT_EXISTS", "#completely-missing", None),
        ("ASSERT_ENABLED", "#disabled", None),
        ("ASSERT_ENABLED", "#aria-disabled", None),
        ("ASSERT_TEXT_EQUAL", "#text", "Hello"),
        ("ASSERT_TEXT_EQUAL", "#text", "hello world"),
        ("ASSERT_INPUT_VALUE", "#filled-input", "Input"),
        ("ASSERT_INPUT_VALUE", "#not-input", "Input Value"),
        ("ASSERT_TITLE", None, "V1 Exact"),
    ),
)
def test_v1_real_chrome_new_assertions_fail_safely_without_mutation(
    real_page: tuple[object, str, _AssertionServer],
    assertion_type: str,
    selector: str | None,
    expected: str | None,
) -> None:
    page, base_url, _server = real_page
    plan = _plan(
        f"{base_url}/assertions",
        (_assertion(assertion_type, selector, expected, timeout_ms=350),),
    )

    result = _run_page(page, plan)

    assert result.outcome == "FAILED"
    assert result.error_type == "ASSERTION_FAILED"
    assert result.traces[0]["node_id"] == "assertion_1"
    assert result.traces[0]["error_message"] == "Web 断言未通过"
    assert page.evaluate("window.assertionSideEffects") == {
        "clicks": 0,
        "inputs": 0,
        "changes": 0,
    }
    assert "Hello World" not in json.dumps(result.to_wire(), ensure_ascii=False)
    assert "Input Value" not in json.dumps(result.to_wire(), ensure_ascii=False)


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_title_accepts_empty_exact_value(
    real_page: tuple[object, str, _AssertionServer],
) -> None:
    page, base_url, _server = real_page
    result = _run_page(
        page,
        _plan(f"{base_url}/empty-title", (_assertion("ASSERT_TITLE", expected=""),)),
    )

    assert result.outcome == "SUCCESS"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_assertion_values_are_absent_from_result_and_evidence(
    assertion_server: tuple[_AssertionServer, str], tmp_path: Path
) -> None:
    server, base_url = assertion_server
    secrets_under_test = (server.secret_title, server.secret_input, server.secret_text)
    plan = _plan(
        f"{base_url}/private",
        (
            _assertion("ASSERT_TITLE", expected="{{title}}"),
            _assertion("ASSERT_INPUT_VALUE", "#private-input", "{{input}}"),
            _assertion("ASSERT_TEXT_EQUAL", "#private-text", "{{text}}"),
        ),
        initial_context={
            "title": server.secret_title,
            "input": server.secret_input,
            "text": server.secret_text,
        },
    )
    evidence_dir = tmp_path / "private-evidence"
    evidence_dir.mkdir()
    process = spawn_web_task_process(plan, evidence_dir)
    try:
        process.start()
        outcome = _wait_for_process(process)
    finally:
        process.close()

    assert outcome.kind == "WEB_RESULT"
    assert outcome.result is not None
    assert outcome.result["outcome"] == "SUCCESS"
    wire = json.dumps(outcome.result, ensure_ascii=False)
    assert not any(value in wire for value in secrets_under_test)
    artifacts = validate_manifest(evidence_dir, outcome.result["evidence"])
    assert {"SCREENSHOT", "WEB_SUMMARY", "PLAYWRIGHT_TRACE", "CONSOLE_ERROR"} <= {
        item.artifact_type for item in artifacts
    }
    assert not any(
        value.encode() in item.path.read_bytes()
        for value in secrets_under_test
        for item in artifacts
    )
    trace = next(item for item in artifacts if item.artifact_type == "PLAYWRIGHT_TRACE")
    assert audit_trace_bytes(trace.path.read_bytes(), forbidden_values=secrets_under_test)
    summary = next(item for item in artifacts if item.artifact_type == "WEB_SUMMARY")
    assert json.loads(summary.path.read_text(encoding="utf-8"))["title"] == "[REDACTED]"

    mismatch_plan = _plan(
        f"{base_url}/private-input",
        (_assertion("ASSERT_INPUT_VALUE", "#private-input", "different-value"),),
    )
    mismatch_evidence_dir = tmp_path / "mismatch-private-evidence"
    mismatch_evidence_dir.mkdir()
    mismatch_process = spawn_web_task_process(mismatch_plan, mismatch_evidence_dir)
    try:
        mismatch_process.start()
        mismatch_outcome = _wait_for_process(mismatch_process)
    finally:
        mismatch_process.close()

    assert mismatch_outcome.kind == "WEB_RESULT"
    assert mismatch_outcome.result is not None
    assert mismatch_outcome.result["outcome"] == "FAILED"
    mismatch_wire = json.dumps(mismatch_outcome.result, ensure_ascii=False)
    assert server.secret_input not in mismatch_wire
    mismatch_artifacts = validate_manifest(
        mismatch_evidence_dir, mismatch_outcome.result["evidence"]
    )
    assert all(
        server.secret_input.encode() not in item.path.read_bytes()
        for item in mismatch_artifacts
    )
    mismatch_trace = next(
        item for item in mismatch_artifacts if item.artifact_type == "PLAYWRIGHT_TRACE"
    )
    assert audit_trace_bytes(
        mismatch_trace.path.read_bytes(), forbidden_values=(server.secret_input,)
    )


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_abort_redacts_late_observed_title_before_truncation(
    real_page: tuple[object, str, _AssertionServer], tmp_path: Path
) -> None:
    page, base_url, server = real_page
    plan = _plan(
        f"{base_url}/long-title",
        (_assertion("ASSERT_TITLE", expected="different title"),),
    )
    observed: list[str] = []
    evidence_dir = tmp_path / "abort-evidence"
    evidence_dir.mkdir()
    collector = _WebEvidenceCollector(
        evidence_dir,
        plan,
        clock=time.monotonic,
        observed_assertion_values=observed,
    )
    collector.start(page.context, page)

    result = WebExecutor()._execute_page(  # noqa: SLF001 - abnormal-finish acceptance
        page,
        plan,
        stop_event=None,
        deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
        observed_assertion_values=observed,
    )
    manifest = collector.abort(page.context, page, result)

    assert result.outcome == "FAILED"
    assert server.long_title in observed
    artifacts = validate_manifest(evidence_dir, manifest.to_wire())
    wire = json.dumps(manifest.to_wire(), ensure_ascii=False)
    assert server.long_title_marker not in wire
    assert all(
        server.long_title_marker.encode() not in item.path.read_bytes() for item in artifacts
    )
    summary = next(item for item in artifacts if item.artifact_type == "WEB_SUMMARY")
    assert json.loads(summary.path.read_text(encoding="utf-8"))["title"] == "[REDACTED]"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_oversize_observed_title_uses_safe_omission_and_drops_trace(
    real_page: tuple[object, str, _AssertionServer], tmp_path: Path
) -> None:
    page, base_url, server = real_page
    plan = _plan(
        f"{base_url}/oversize-title",
        (_assertion("ASSERT_TITLE", expected="different title"),),
    )
    observed: list[str] = []
    evidence_dir = tmp_path / "oversize-evidence"
    evidence_dir.mkdir()
    collector = _WebEvidenceCollector(
        evidence_dir,
        plan,
        clock=time.monotonic,
        observed_assertion_values=observed,
    )
    collector.start(page.context, page)

    result = WebExecutor()._execute_page(  # noqa: SLF001 - bounded-overflow acceptance
        page,
        plan,
        stop_event=None,
        deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
        observed_assertion_values=observed,
    )
    manifest = collector.finish(page.context, page, result)

    assert result.outcome == "FAILED"
    artifacts = validate_manifest(evidence_dir, manifest.to_wire())
    assert {
        "PLAYWRIGHT_TRACE",
        "SCREENSHOT",
        "CONSOLE_ERROR",
        "NETWORK_ERROR",
    }.isdisjoint(
        item.artifact_type for item in artifacts
    )
    wire = json.dumps(manifest.to_wire(), ensure_ascii=False)
    assert server.oversize_title_marker not in wire
    assert all(
        server.oversize_title_marker.encode() not in item.path.read_bytes()
        for item in artifacts
    )
    summary = next(item for item in artifacts if item.artifact_type == "WEB_SUMMARY")
    assert json.loads(summary.path.read_text(encoding="utf-8"))["title"] == "[REDACTED]"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_healing_context_redacts_cross_node_values_and_keeps_safe_clues(
    real_page: tuple[object, str, _AssertionServer],
) -> None:
    page, base_url, server = real_page
    observed_plan = _plan(
        f"{base_url}/healing",
        (
            _assertion("ASSERT_TITLE", expected="{{title}}"),
            _assertion("ASSERT_INPUT_VALUE", "#observed-input", "{{input}}"),
            _assertion("ASSERT_TEXT_EQUAL", "#observed-text", "{{text}}"),
            _assertion("ASSERT_EXISTS", "#missing-observed"),
        ),
        initial_context={
            "title": server.secret_title,
            "input": server.secret_input,
            "text": server.secret_text,
        },
    )
    observed: list[str] = []

    observed_result = WebExecutor()._execute_page(  # noqa: SLF001 - result privacy acceptance
        page,
        observed_plan,
        stop_event=None,
        deadline=time.monotonic() + observed_plan.total_timeout_ms / 1000.0,
        observed_assertion_values=observed,
    )

    assert observed_result.outcome == "FAILED"
    assert [item["status"] for item in observed_result.traces] == [
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "FAILED",
    ]
    observed_wire = json.dumps(observed_result.to_wire(), ensure_ascii=False)
    assert not any(
        value in observed_wire
        for value in (server.secret_title, server.secret_input, server.secret_text)
    )
    healing = observed_result.traces[-1]["healing_context"]
    assert healing["element_version_id"] == 201
    assert healing["page_title"] == "[REDACTED]"
    assert any(
        candidate.get("id") == "safe-healing"
        and candidate.get("data-testid") == "safe-candidate"
        for candidate in healing["dom_candidates"]
    )

    planned_plan = _plan(
        f"{base_url}/planned-healing",
        (
            _assertion("ASSERT_EXISTS", "#missing-planned"),
            _assertion("ASSERT_TEXT_EQUAL", "#safe-planned", "{{planned}}"),
        ),
        initial_context={"planned": server.planned_secret},
    )
    planned_result = WebExecutor()._execute_page(  # noqa: SLF001 - planned-value acceptance
        page,
        planned_plan,
        stop_event=None,
        deadline=time.monotonic() + planned_plan.total_timeout_ms / 1000.0,
        observed_assertion_values=[],
    )

    assert planned_result.outcome == "FAILED"
    planned_wire = json.dumps(planned_result.to_wire(), ensure_ascii=False)
    assert server.planned_secret not in planned_wire
    assert server.oversize_title_marker not in planned_wire
    planned_healing = planned_result.traces[0]["healing_context"]
    safe_candidate = next(
        candidate
        for candidate in planned_healing["dom_candidates"]
        if candidate.get("id") == "safe-planned"
    )
    assert safe_candidate["data-testid"] == "safe-planned-candidate"
    assert "aria-label" not in safe_candidate
    assert "title" not in safe_candidate


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_action_healing_uses_all_planned_values_and_preserves_semantics(
    real_page: tuple[object, str, _AssertionServer],
) -> None:
    from dataclasses import replace

    from runner.protocol import _web_actions

    page, base_url, server = real_page
    action_wires = [
        {
            "type": action_type,
            "timeout_ms": 500,
            "failure_policy": failure_policy,
            "url": None,
            "locator": _wire_locator(selector),
            "value": value,
            "key": None,
        }
        for action_type, selector, failure_policy, value in (
            ("FILL", "#missing-fill", "CONTINUE", "{{fill_secret}}"),
            ("SELECT", "#missing-select", "CONTINUE", "{{select_secret}}"),
            ("CLICK", "#missing-click", "STOP", None),
        )
    ]
    plan = replace(
        _plan(
            f"{base_url}/action-healing",
            (_assertion("ASSERT_TITLE", expected="{{title_secret}}"),),
            initial_context={
                "title_secret": server.action_title_secret,
                "fill_secret": server.action_fill_secret,
                "select_secret": server.action_select_secret,
            },
        ),
        actions=tuple(_web_actions(action_wires)),
    )

    result = WebExecutor()._execute_page(  # noqa: SLF001 - action privacy acceptance
        page,
        plan,
        stop_event=None,
        deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
    )

    assert result.outcome == "FAILED"
    assert [trace["node_id"] for trace in result.traces] == [
        "action_1",
        "action_2",
        "action_3",
        "assertion_1",
    ]
    assert [trace["status"] for trace in result.traces] == [
        "FAILED",
        "FAILED",
        "FAILED",
        "SKIPPED",
    ]
    action_traces = result.traces[:3]
    assert all(trace["error_type"] == "WEB_LOCATOR_NOT_FOUND" for trace in action_traces)
    assert all(
        trace["locator_attempts"]
        == [{"strategy": "css", "priority": 1, "status": "NOT_FOUND"}]
        for trace in action_traces
    )
    assert all(trace["healing_context"]["element_version_id"] == 201
               for trace in action_traces)
    assert all(trace["healing_context"]["page_title"] == "[REDACTED]"
               for trace in action_traces)

    wire = json.dumps(result.to_wire(), ensure_ascii=False)
    leak_flags = {
        "planned_title": server.action_title_secret in wire,
        "planned_fill": server.action_fill_secret in wire,
        "planned_select": server.action_select_secret in wire,
    }
    assert not any(leak_flags.values()), f"Planned action value leak flags: {leak_flags}"
    safe_candidates = [
        candidate
        for candidate in action_traces[0]["healing_context"]["dom_candidates"]
        if candidate.get("id") in {"safe-action", "safe-select"}
    ]
    assert {candidate["id"] for candidate in safe_candidates} == {
        "safe-action",
        "safe-select",
    }
    sensitive_field_flags = {
        candidate["id"]: any(field in candidate for field in ("aria-label", "title"))
        for candidate in safe_candidates
    }
    assert not any(sensitive_field_flags.values()), (
        f"Sensitive candidate field flags: {sensitive_field_flags}"
    )
    assert {candidate.get("data-testid") for candidate in safe_candidates} == {
        "safe-action-candidate",
        "safe-select-candidate",
    }


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_chrome_assertions_classify_node_and_total_timeout(
    real_page: tuple[object, str, _AssertionServer],
) -> None:
    page, base_url, _server = real_page
    node_result = _run_page(
        page,
        _plan(
            f"{base_url}/assertions",
            (
                _assertion(
                    "ASSERT_EXISTS",
                    "#missing-node",
                    timeout_ms=300,
                    locked=False,
                ),
            ),
            total_timeout_ms=4_000,
        ),
    )
    assert node_result.outcome == "TIMEOUT"
    assert node_result.error_type == "WEB_NODE_TIMEOUT"

    total_result = _run_page(
        page,
        _plan(
            f"{base_url}/assertions",
            (
                _assertion(
                    "ASSERT_EXISTS",
                    "#missing-total",
                    timeout_ms=3_000,
                    locked=False,
                ),
            ),
            total_timeout_ms=800,
        ),
    )
    assert total_result.outcome == "TIMEOUT"
    assert total_result.error_type == "WEB_TOTAL_TIMEOUT"
    assert total_result.traces[0]["node_id"] == "assertion_1"


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_real_isolation_cancel_converges_and_cleans_owned_browser(
    assertion_server: tuple[_AssertionServer, str], tmp_path: Path
) -> None:
    server, base_url = assertion_server
    server.delayed_page_served.clear()
    plan = _plan(
        f"{base_url}/delayed",
        (
            _assertion("ASSERT_EXISTS", "#late", timeout_ms=4_000, locked=False),
            _assertion("ASSERT_TITLE", expected="Delayed assertion"),
        ),
        total_timeout_ms=10_000,
    )
    profiles_before = _playwright_profile_dirs()
    descendant_pids: set[int] = set()
    profile_dirs: set[Path] = set()
    process = None
    evidence_path: Path | None = None
    try:
        with TemporaryDirectory(prefix="v1-w4-cancel-", dir=tmp_path) as evidence_dir:
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
        pytest.fail("isolated V1 assertion process did not finish within the bounded wait")
    process.join(timeout=5.0)  # type: ignore[attr-defined]
    assert not process.is_alive()  # type: ignore[attr-defined]
    outcome = process.outcome()  # type: ignore[attr-defined]
    assert isinstance(outcome, IsolatedOutcome)
    return outcome
