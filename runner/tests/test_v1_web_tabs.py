from __future__ import annotations

import json
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from runner.errors import ProtocolError
from runner.evidence import validate_manifest
from runner.executors.web import (
    MAX_MANAGED_WEB_TABS,
    WebExecutionError,
    WebExecutor,
    _ManagedTabs,
    _WebEvidenceCollector,
)
from runner.models import WebExecutionActionPlan, WebExecutionPlanResult
from runner.protocol import _web_actions, _web_assertions
from runner.snapshot import DefaultSnapshotProbe


def _wire_action(
    action_type: str,
    *,
    url: object = None,
    value: object = None,
    locator: object = None,
    key: object = None,
) -> dict[str, object]:
    return {
        "type": action_type,
        "timeout_ms": 2_000,
        "failure_policy": "STOP",
        "url": url,
        "locator": locator,
        "value": value,
        "key": key,
    }


@pytest.mark.parametrize(
    ("action_type", "url", "alias"),
    (
        ("NEW_TAB", "http://example.test/new", "tab_1"),
        ("SWITCH_TAB", None, "tab-1"),
        ("CLOSE_TAB", None, "main"),
    ),
)
def test_v1_w10_protocol_accepts_exact_seven_field_shape(
    action_type: str,
    url: str | None,
    alias: str,
) -> None:
    parsed = _web_actions([_wire_action(action_type, url=url, value=alias)])

    assert len(parsed) == 1
    assert parsed[0].type == action_type
    assert parsed[0].url == url
    assert parsed[0].value == alias
    assert parsed[0].locator is None
    assert parsed[0].key is None


@pytest.mark.parametrize(
    "alias",
    (None, "", "main", "1tab", "tab space", "标签", "a" * 65),
)
def test_v1_w10_new_tab_rejects_invalid_alias(alias: object) -> None:
    with pytest.raises(ProtocolError):
        _web_actions(
            [_wire_action("NEW_TAB", url="http://example.test/new", value=alias)]
        )


@pytest.mark.parametrize("action_type", ("SWITCH_TAB", "CLOSE_TAB"))
def test_v1_w10_switch_and_close_require_alias_only(action_type: str) -> None:
    with pytest.raises(ProtocolError, match="标签别名无效"):
        _web_actions([_wire_action(action_type)])
    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions(
            [_wire_action(action_type, url="http://example.test/extra", value="tab")]
        )
    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions([_wire_action(action_type, value="tab", key="Escape")])


def test_v1_w10_new_tab_requires_url_and_rejects_locator_or_key() -> None:
    with pytest.raises(ProtocolError, match="缺少 url"):
        _web_actions([_wire_action("NEW_TAB", value="tab")])
    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions(
            [
                _wire_action(
                    "NEW_TAB",
                    url="http://example.test/new",
                    value="tab",
                    locator={
                        "element_version_id": None,
                        "candidates": [
                            {"strategy": "css", "value": "#target", "priority": 1}
                        ],
                    },
                )
            ]
        )
    with pytest.raises(ProtocolError, match="不适用字段"):
        _web_actions(
            [
                _wire_action(
                    "NEW_TAB",
                    url="http://example.test/new",
                    value="tab",
                    key="Escape",
                )
            ]
        )


class _FakePage:
    def __init__(self, *, fail_goto: bool = False) -> None:
        self.closed = False
        self.fail_goto = fail_goto
        self.front_count = 0
        self.url = ""

    def goto(self, url: str, *, timeout: int) -> None:
        del timeout
        self.url = url
        if self.fail_goto:
            raise RuntimeError("controlled navigation failure")

    def bring_to_front(self) -> None:
        self.front_count += 1

    def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class _FakeContext:
    def __init__(self) -> None:
        self.created: list[_FakePage] = []
        self.fail_next_goto = False

    def new_page(self) -> _FakePage:
        page = _FakePage(fail_goto=self.fail_next_goto)
        self.fail_next_goto = False
        self.created.append(page)
        return page


def _action(action_type: str, *, url: str | None = None, value: str) -> WebExecutionActionPlan:
    return WebExecutionActionPlan(action_type, 2_000, "STOP", url, None, value, None)


def _perform_tab_action(
    executor: WebExecutor,
    tabs: _ManagedTabs,
    action: WebExecutionActionPlan,
) -> None:
    executor._perform_action(  # noqa: SLF001 - focused managed-tab state test
        tabs.current_page,
        action,
        {},
        time.monotonic() + 2,
        run_deadline=time.monotonic() + 5,
        managed_tabs=tabs,
    )


def test_v1_w10_managed_tab_state_rejects_invalid_transitions_and_keeps_failed_new_page() -> None:
    main = _FakePage()
    context = _FakeContext()
    tabs = _ManagedTabs(main, browser_context=context)
    executor = WebExecutor()

    _perform_tab_action(
        executor,
        tabs,
        _action("NEW_TAB", url="http://example.test/one", value="one"),
    )
    assert tabs.current_alias == "one"

    with pytest.raises(WebExecutionError) as duplicate:
        _perform_tab_action(
            executor,
            tabs,
            _action("NEW_TAB", url="http://example.test/duplicate", value="one"),
        )
    assert duplicate.value.error_type == "WEB_TAB_EXISTS"

    context.fail_next_goto = True
    with pytest.raises(RuntimeError, match="controlled navigation failure"):
        _perform_tab_action(
            executor,
            tabs,
            _action("NEW_TAB", url="http://example.test/broken", value="broken"),
        )
    assert tabs.current_alias == "broken"
    assert tabs.target("broken").url == "http://example.test/broken"

    with pytest.raises(WebExecutionError) as unknown:
        _perform_tab_action(executor, tabs, _action("SWITCH_TAB", value="unknown"))
    assert unknown.value.error_type == "WEB_TAB_NOT_FOUND"

    _perform_tab_action(executor, tabs, _action("SWITCH_TAB", value="one"))
    _perform_tab_action(executor, tabs, _action("CLOSE_TAB", value="broken"))
    assert tabs.current_alias == "one"
    with pytest.raises(WebExecutionError) as closed:
        _perform_tab_action(executor, tabs, _action("SWITCH_TAB", value="broken"))
    assert closed.value.error_type == "WEB_TAB_NOT_FOUND"

    _perform_tab_action(
        executor,
        tabs,
        _action("NEW_TAB", url="http://example.test/two", value="two"),
    )
    _perform_tab_action(executor, tabs, _action("CLOSE_TAB", value="main"))
    assert tabs.current_alias == "two"
    _perform_tab_action(executor, tabs, _action("SWITCH_TAB", value="one"))
    _perform_tab_action(executor, tabs, _action("CLOSE_TAB", value="one"))
    assert tabs.current_alias == "two"

    with pytest.raises(WebExecutionError) as last:
        _perform_tab_action(executor, tabs, _action("CLOSE_TAB", value="two"))
    assert last.value.error_type == "WEB_LAST_TAB"

    limit_tabs = _ManagedTabs(_FakePage(), browser_context=_FakeContext())
    for index in range(1, MAX_MANAGED_WEB_TABS):
        limit_tabs.create(f"tab-{index}")
    assert limit_tabs.alive_count == MAX_MANAGED_WEB_TABS
    with pytest.raises(WebExecutionError) as limit:
        limit_tabs.create("overflow")
    assert limit.value.error_type == "WEB_TAB_LIMIT"


class _TabServer(ThreadingHTTPServer):
    daemon_threads = True


class _TabHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        alias = urlsplit(self.path).path.strip("/") or "main"
        body = f"""<!doctype html><html><head><title>W10 {alias}</title></head><body>
<input id="field" value="">
<button id="mark" type="button"
  onclick="document.querySelector('#result').textContent='{alias}-clicked'">mark</button>
<button id="popup" type="button" onclick="window.open('/popup', '_blank')">popup</button>
<div id="result">{alias}-initial</div>
</body></html>""".encode()
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


@pytest.fixture(scope="module")
def tab_server() -> Iterator[str]:
    server = _TabServer(("127.0.0.1", 0), _TabHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


REAL_CHROME_UNAVAILABLE = not DefaultSnapshotProbe().web_ready()


def _wire_tab_flow(base_url: str) -> list[dict[str, object]]:
    def locator(selector: str) -> dict[str, object]:
        return {
            "element_version_id": None,
            "candidates": [{"strategy": "css", "value": selector, "priority": 1}],
        }

    return [
        _wire_action("NEW_TAB", url=f"{base_url}/alpha", value="alpha"),
        _wire_action("FILL", locator=locator("#field"), value="alpha-filled"),
        _wire_action("CLICK", locator=locator("#popup")),
        _wire_action("WAIT_URL", url=f"{base_url}/alpha"),
        _wire_action("NEW_TAB", url=f"{base_url}/beta", value="beta"),
        _wire_action("CLICK", locator=locator("#mark")),
        _wire_action("SWITCH_TAB", value="alpha"),
        _wire_action("CLICK", locator=locator("#mark")),
        _wire_action("CLOSE_TAB", value="beta"),
        _wire_action("WAIT_URL", url=f"{base_url}/alpha"),
        _wire_action("NEW_TAB", url=f"{base_url}/gamma", value="gamma"),
        _wire_action("CLOSE_TAB", value="gamma"),
        _wire_action("WAIT_URL", url=f"{base_url}/main"),
        _wire_action("SWITCH_TAB", value="alpha"),
        _wire_action("CLOSE_TAB", value="main"),
        _wire_action("WAIT_URL", url=f"{base_url}/alpha"),
    ]


def _plan(base_url: str) -> WebExecutionPlanResult:
    locator = {
        "element_version_id": None,
        "candidates": [{"strategy": "css", "value": "#field", "priority": 1}],
    }
    result_locator = {
        "element_version_id": None,
        "candidates": [{"strategy": "css", "value": "#result", "priority": 1}],
    }
    assertions = _web_assertions(
        [
            {
                "type": "ASSERT_INPUT_VALUE",
                "timeout_ms": 2_000,
                "locator": locator,
                "expected": "alpha-filled",
            },
            {
                "type": "ASSERT_TEXT_EQUAL",
                "timeout_ms": 2_000,
                "locator": result_locator,
                "expected": "alpha-clicked",
            },
            {
                "type": "ASSERT_URL",
                "timeout_ms": 2_000,
                "locator": None,
                "expected": f"{base_url}/alpha",
            },
        ]
    )
    return WebExecutionPlanResult(
        schema_version=1,
        run_id="run-v1-w10",
        message_id="message-v1-w10",
        runner_id="runner-v1-w10",
        case_run_id=2101,
        web_case_id=2102,
        web_case_version_id=2103,
        start_url=f"{base_url}/main",
        browser="CHROME",
        headless=True,
        total_timeout_ms=20_000,
        initial_context={},
        actions=tuple(_web_actions(_wire_tab_flow(base_url))),
        assertions=tuple(assertions),
        session=None,
    )


@pytest.mark.skipif(REAL_CHROME_UNAVAILABLE, reason="Playwright 或本机 Chrome 不可用")
def test_v1_w10_real_chrome_managed_tab_flow_and_cleanup(
    tab_server: str,
    tmp_path: Path,
) -> None:
    plan = _plan(tab_server)
    evidence_dir = tmp_path / "w10-evidence"
    evidence_dir.mkdir()
    all_pages: tuple[object, ...] = ()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        try:
            main_page = context.new_page()
            observed: list[str] = []
            collector = _WebEvidenceCollector(
                evidence_dir,
                plan,
                clock=time.monotonic,
                observed_assertion_values=observed,
            )
            collector.start(context, main_page)
            tabs = _ManagedTabs(
                main_page,
                browser_context=context,
                on_new_page=collector._bind_page,  # noqa: SLF001 - same execution binding
            )
            result = WebExecutor()._execute_page(  # noqa: SLF001 - bounded Chrome acceptance
                main_page,
                plan,
                stop_event=None,
                deadline=time.monotonic() + plan.total_timeout_ms / 1000.0,
                observed_assertion_values=observed,
                managed_tabs=tabs,
            )

            assert result.outcome == "SUCCESS"
            assert all(trace["status"] == "SUCCESS" for trace in result.traces)
            assert tabs.current_alias == "alpha"
            assert tabs.aliases == ("alpha",)
            assert tabs.alive_count == 1
            assert tabs.current_page.url == f"{tab_server}/alpha"
            assert len(context.pages) == 2
            assert tabs.current_page in context.pages

            manifest = collector.finish(
                context,
                tabs.current_page,
                result,
                page_count=tabs.alive_count,
            )
            artifacts = validate_manifest(evidence_dir, manifest.to_wire())
            summary = next(item for item in artifacts if item.artifact_type == "WEB_SUMMARY")
            summary_data = json.loads(summary.path.read_text(encoding="utf-8"))
            assert summary_data["final_url"] == f"{tab_server}/alpha"
            assert summary_data["title"] == "W10 alpha"
            assert summary_data["page_count"] == 1
            all_pages = tuple(context.pages)
        finally:
            context.close()
            browser.close()

    assert len(all_pages) == 2
    assert all(page.is_closed() for page in all_pages)
