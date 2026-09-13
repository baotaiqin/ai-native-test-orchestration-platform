from __future__ import annotations

import base64
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest

from runner.errors import ProtocolError
from runner.executors.web import WebExecutor
from runner.models import (
    HttpResponse,
    RunnerIdentity,
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionBrowserConfig,
    WebExecutionProxyConfig,
    WebLocatorCandidate,
    WebLocatorPlan,
)
from runner.protocol import RunnerClient, _web_actions, _web_assertions, _web_browser_config
from tests.helpers import FakeTransport


def _wire_locator(selector: str = "#source") -> dict[str, object]:
    return {
        "element_version_id": None,
        "candidates": [{"strategy": "css", "value": selector, "priority": 1}],
    }


def _wire(action_type: str, **fields: object) -> dict[str, object]:
    return {
        "type": action_type,
        "timeout_ms": 1000,
        "failure_policy": "STOP",
        "url": None,
        "locator": None,
        "value": None,
        "key": None,
        **fields,
    }


def test_extended_action_protocol_accepts_only_bounded_shapes() -> None:
    parsed = _web_actions(
        [
            _wire("DRAG_DROP", locator=_wire_locator(), value="#target"),
            _wire(
                "UPLOAD",
                locator=_wire_locator(),
                key="sample.txt",
                value=base64.b64encode(b"managed upload").decode(),
            ),
            _wire("DOWNLOAD", locator=_wire_locator(), value="report.csv"),
            _wire("WAIT_TIME", value="250"),
            _wire("WAIT_TEXT", locator=_wire_locator(), value="ready"),
            _wire("COOKIE", key="sid", value="opaque"),
            _wire("LOCAL_STORAGE", key="theme", value="dark"),
            _wire("SESSION_STORAGE", key="step", value="one"),
            _wire("JS_EVAL", value="() => document.title"),
        ]
    )

    assert [action.type for action in parsed] == [
        "DRAG_DROP",
        "UPLOAD",
        "DOWNLOAD",
        "WAIT_TIME",
        "WAIT_TEXT",
        "COOKIE",
        "LOCAL_STORAGE",
        "SESSION_STORAGE",
        "JS_EVAL",
    ]


def test_extended_assertion_protocol_accepts_strict_shapes() -> None:
    locator = _wire_locator()
    baseline = base64.b64encode(b"\x89PNG\r\n\x1a\nbaseline").decode()
    parsed = _web_assertions(
        [
            {
                "type": "ASSERT_ATTRIBUTE",
                "timeout_ms": 1000,
                "locator": locator,
                "expected": "ready",
                "key": "data-status",
            },
            {
                "type": "ASSERT_ELEMENT_COUNT",
                "timeout_ms": 1000,
                "locator": locator,
                "expected": "2",
                "key": None,
            },
            {
                "type": "ASSERT_DOWNLOAD_SUCCESS",
                "timeout_ms": 1000,
                "locator": None,
                "expected": "report.csv",
                "key": None,
            },
            {
                "type": "ASSERT_NETWORK_REQUEST",
                "timeout_ms": 1000,
                "locator": None,
                "expected": "/api/orders",
                "key": None,
            },
            {
                "type": "ASSERT_SCREENSHOT_VISUAL_COMPARE",
                "timeout_ms": 1000,
                "locator": None,
                "expected": baseline,
                "key": None,
            },
            {
                "type": "ASSERT_AI_SEMANTIC",
                "timeout_ms": 1000,
                "locator": None,
                "expected": None,
                "key": None,
                "prompt_id": 7,
                "criteria": "页面说明订单已经创建",
                "confidence_threshold": 0.85,
            },
        ]
    )

    assert [assertion.type for assertion in parsed] == [
        "ASSERT_ATTRIBUTE",
        "ASSERT_ELEMENT_COUNT",
        "ASSERT_DOWNLOAD_SUCCESS",
        "ASSERT_NETWORK_REQUEST",
        "ASSERT_SCREENSHOT_VISUAL_COMPARE",
        "ASSERT_AI_SEMANTIC",
    ]


def test_browser_config_protocol_accepts_only_bounded_safe_shape() -> None:
    config = _web_browser_config(
        {
            "window_width": 1440,
            "window_height": 900,
            "language": "zh-CN",
            "user_agent": "V1-Test-Agent",
            "proxy": {
                "server": "http://proxy.example.test:8080",
                "username": "{{secret.PROXY_USER}}",
                "password": "{{secret.PROXY_PASSWORD}}",
            },
            "download_path": "downloads/reports",
        }
    )

    assert config.window_width == 1440
    assert config.proxy is not None
    assert config.proxy.password == "{{secret.PROXY_PASSWORD}}"

    with pytest.raises(ProtocolError, match="download_path"):
        _web_browser_config(
            {
                "window_width": 1440,
                "window_height": 900,
                "language": None,
                "user_agent": None,
                "proxy": None,
                "download_path": "../outside",
            }
        )


def test_browser_config_reaches_chrome_launch_and_context() -> None:
    launch_kwargs: dict[str, object] = {}
    context_kwargs: dict[str, object] = {}

    class Browser:
        def new_context(self, **kwargs: object) -> object:
            context_kwargs.update(kwargs)
            return object()

    browser = Browser()

    def launch(**kwargs: object) -> Browser:
        launch_kwargs.update(kwargs)
        return browser

    config = WebExecutionBrowserConfig(
        1440,
        900,
        "zh-CN",
        "V1-Test-Agent",
        WebExecutionProxyConfig("http://proxy.example.test:8080"),
        "downloads/reports",
    )
    plan = SimpleNamespace(
        headless=True,
        browser_config=config,
        initial_context={},
        secrets=(),
        session=None,
    )
    with TemporaryDirectory() as evidence_dir:
        executor = WebExecutor(evidence_dir=evidence_dir, chrome_executable="test-chrome")
        assert executor._launch_browser(  # noqa: SLF001
            SimpleNamespace(chromium=SimpleNamespace(launch=launch)), plan
        ) is browser
        executor._new_context(browser, plan)  # noqa: SLF001

    assert launch_kwargs["headless"] is True
    assert launch_kwargs["args"] == ["--window-size=1440,900"]
    assert launch_kwargs["proxy"] == {"server": "http://proxy.example.test:8080"}
    assert Path(str(launch_kwargs["downloads_path"])).parts[-2:] == ("downloads", "reports")
    assert context_kwargs == {
        "viewport": {"width": 1440, "height": 900},
        "locale": "zh-CN",
        "user_agent": "V1-Test-Agent",
    }


class _FakeContext:
    def __init__(self) -> None:
        self.cookies: list[dict[str, str]] = []

    def add_cookies(self, cookies: list[dict[str, str]]) -> None:
        self.cookies.extend(cookies)


class _FakeLocator:
    def __init__(self, selector: str, calls: list[tuple[Any, ...]]) -> None:
        self.selector = selector
        self.calls = calls

    def wait_for(self, **_kwargs: object) -> None:
        return None

    def drag_to(self, target: _FakeLocator, **_kwargs: object) -> None:
        self.calls.append(("drag_to", self.selector, target.selector))

    def inner_text(self, **_kwargs: object) -> str:
        self.calls.append(("inner_text", self.selector))
        return "status: ready"

    def get_attribute(self, key: str, **_kwargs: object) -> str | None:
        self.calls.append(("get_attribute", self.selector, key))
        return "ready" if key == "data-status" else None

    def count(self, **_kwargs: object) -> int:
        self.calls.append(("count", self.selector))
        return 2

    def set_input_files(self, payload: dict[str, object], **_kwargs: object) -> None:
        self.calls.append(("set_input_files", self.selector, payload))

    def click(self, **_kwargs: object) -> None:
        self.calls.append(("click", self.selector))


class _FakeDownload:
    suggested_filename = "report.csv"

    def failure(self) -> None:
        return None

    def delete(self) -> None:
        return None


class _FakeDownloadInfo:
    value = _FakeDownload()

    def __enter__(self) -> _FakeDownloadInfo:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakePage:
    url = "https://example.test/current"

    def __init__(self) -> None:
        self.context = _FakeContext()
        self.calls: list[tuple[Any, ...]] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector, self.calls)

    def wait_for_timeout(self, duration_ms: int) -> None:
        self.calls.append(("wait_for_timeout", duration_ms))

    def evaluate(self, script: str, arg: object = None) -> object:
        self.calls.append(("evaluate", script, arg))
        return None

    def expect_download(self, **_kwargs: object) -> _FakeDownloadInfo:
        return _FakeDownloadInfo()

    def screenshot(self, **kwargs: object) -> bytes:
        self.calls.append(("screenshot", kwargs.get("type")))
        return b"\x89PNG\r\n\x1a\nbaseline"

    def title(self) -> str:
        return "Order created"


def _action(
    action_type: str,
    *,
    locator: bool = False,
    value: str | None = None,
    key: str | None = None,
) -> WebExecutionActionPlan:
    plan = (
        WebLocatorPlan(None, (WebLocatorCandidate("css", "#source", 1),))
        if locator
        else None
    )
    return WebExecutionActionPlan(action_type, 1000, "STOP", None, plan, value, key)


def test_extended_actions_execute_without_exposing_values_in_trace() -> None:
    executor = WebExecutor()
    page = _FakePage()
    deadline = time.monotonic() + 5
    actions = [
        _action("DRAG_DROP", locator=True, value="#target"),
        _action(
            "UPLOAD",
            locator=True,
            key="sample.txt",
            value=base64.b64encode(b"managed upload").decode(),
        ),
        _action("DOWNLOAD", locator=True, value="report.csv"),
        _action("WAIT_TIME", value="25"),
        _action("WAIT_TEXT", locator=True, value="ready"),
        _action("COOKIE", key="sid", value="opaque-cookie"),
        _action("LOCAL_STORAGE", key="theme", value="dark"),
        _action("SESSION_STORAGE", key="step", value="one"),
        _action("JS_EVAL", value="() => document.title"),
    ]

    attempts = [
        executor._perform_action(  # noqa: SLF001 - focused executor contract test
            page,
            action,
            {},
            deadline,
            run_deadline=deadline,
        )
        for action in actions
    ]

    assert attempts[0] == [{"strategy": "css", "priority": 1, "status": "SUCCESS"}]
    assert attempts[4] == [{"strategy": "css", "priority": 1, "status": "SUCCESS"}]
    assert ("drag_to", "#source", "#target") in page.calls
    assert ("wait_for_timeout", 25) in page.calls
    upload_call = next(call for call in page.calls if call[0] == "set_input_files")
    assert upload_call[2]["name"] == "sample.txt"
    assert upload_call[2]["buffer"] == b"managed upload"
    assert ("click", "#source") in page.calls
    assert executor._download_names == ["report.csv"]  # noqa: SLF001
    assert page.context.cookies == [
        {"name": "sid", "value": "opaque-cookie", "url": page.url}
    ]
    assert "opaque-cookie" not in repr(attempts)


def test_extended_assertions_use_private_runtime_observations() -> None:
    executor = WebExecutor()
    page = _FakePage()
    deadline = time.monotonic() + 2
    locator = WebLocatorPlan(None, (WebLocatorCandidate("css", "#source", 1),))
    executor._download_names = ["report.csv"]  # noqa: SLF001
    executor._request_urls = ["https://example.test/api/orders?token=private"]  # noqa: SLF001
    assertions = [
        WebExecutionAssertionPlan(
            "ASSERT_ATTRIBUTE", 1000, locator, "ready", "data-status"
        ),
        WebExecutionAssertionPlan("ASSERT_ELEMENT_COUNT", 1000, locator, "2"),
        WebExecutionAssertionPlan("ASSERT_DOWNLOAD_SUCCESS", 1000, None, "report.csv"),
        WebExecutionAssertionPlan("ASSERT_NETWORK_REQUEST", 1000, None, "/api/orders"),
        WebExecutionAssertionPlan(
            "ASSERT_SCREENSHOT_VISUAL_COMPARE",
            1000,
            None,
            base64.b64encode(b"\x89PNG\r\n\x1a\nbaseline").decode(),
        ),
    ]
    observed: list[str] = []

    attempts = [
        executor._perform_assertion(  # noqa: SLF001 - focused executor contract test
            page,
            assertion,
            {},
            deadline,
            run_deadline=deadline,
            observed_assertion_values=observed,
        )
        for assertion in assertions
    ]

    assert attempts[:2] == [
        [{"strategy": "css", "priority": 1, "status": "SUCCESS"}],
        [{"strategy": "css", "priority": 1, "status": "SUCCESS"}],
    ]
    assert attempts[2:] == [[], [], []]
    assert ("screenshot", "png") in page.calls
    assert "ready" in observed
    assert "report.csv" in observed
    assert any("token=private" in value for value in observed)


def test_web_ai_snapshot_is_bounded_and_redacts_managed_values() -> None:
    executor = WebExecutor()
    page = _FakePage()
    snapshot = executor._semantic_snapshot(  # noqa: SLF001
        page,
        ("ready",),
        timeout=1000,
    )

    assert snapshot["status_code"] == 200
    assert snapshot["json_body"]["page_title"] == "Order created"
    assert snapshot["text"] == "status: [REDACTED]"
    assert snapshot["headers"] == {}
    assert snapshot["cookies"] == {}


def test_web_ai_evaluation_protocol_is_identity_bound() -> None:
    response = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "assertion_status": "REVIEW",
        "assertion_results": [
            {
                "sequence": 1,
                "name": "assertion_1",
                "type": "AI_SEMANTIC",
                "status": "REVIEW",
                "expected": None,
                "actual": None,
                "message": "review",
                "duration_ms": 2,
                "confidence": 0.5,
                "reason": "insufficient",
                "ai_call_id": 91,
                "actual_model": "model",
                "fallback_used": False,
                "repair_used": False,
            }
        ],
        "node_results": [{"node_id": "assertion_1", "status": "REVIEW"}],
        "evaluation_token": "signed-web-evaluation",
    }
    transport = FakeTransport(HttpResponse(200, response))
    client = RunnerClient("https://backend.example.test", transport)
    result = client.web_execution_evaluate(
        RunnerIdentity("runner-001", "runner-credential"),
        "run-001",
        "message-001",
        42,
        [
            {
                "node_id": "assertion_1",
                "response": {
                    "status_code": 200,
                    "json_body": {"page_title": "Order created"},
                    "text": "Order 42 created",
                },
            }
        ],
    )

    assert result.assertion_status == "REVIEW"
    assert result.node_results == ({"node_id": "assertion_1", "status": "REVIEW"},)
    assert "signed-web-evaluation" not in repr(result)
    assert transport.calls[0]["url"].endswith("/web-execution-evaluate")
