from __future__ import annotations

import io
import json
import warnings
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace

import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import AuthenticationError, ProtocolError, RunnerError, TransportError
from runner.evidence import (
    EvidenceValidationError,
    audit_trace_bytes,
    sanitize_trace_file,
    validate_artifact,
    validate_manifest,
)
from runner.executors.web import WebExecutor, _collect_healing_context
from runner.isolation import IsolatedOutcome
from runner.models import (
    ClaimResult,
    ExecutionCancellationResult,
    HttpResponse,
    RunnerIdentity,
    WebEvidenceUploadResult,
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionCompleteResult,
    WebExecutionEvaluationResult,
    WebExecutionPlanResult,
    WebExecutionStartResult,
    WebLocatorCandidate,
    WebLocatorPlan,
    WebSessionPlan,
)
from runner.protocol import RunnerClient, _web_trace_payload
from runner.slots import SlotState
from runner.snapshot import DefaultSnapshotProbe, collect_snapshot
from tests.helpers import FakeProbe, FakeTransport

IDENTITY = RunnerIdentity("runner-001", "credential-value-123456789")


def _locator(*candidates: tuple[str, str]) -> WebLocatorPlan:
    return WebLocatorPlan(
        None,
        tuple(
            WebLocatorCandidate(strategy, value, index)
            for index, (strategy, value) in enumerate(candidates, 1)
        ),
    )


def _plan(
    *, assertion_expected: str = "Hello", session: WebSessionPlan | None = None
) -> WebExecutionPlanResult:
    return WebExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        8,
        "http://example.test/start",
        "CHROME",
        True,
        10_000,
        {"name": "Alice"},
        (
            WebExecutionActionPlan(
                "FILL", 30_000, "STOP", None, _locator(("css", "#name")), "{{name}}", None
            ),
            WebExecutionActionPlan(
                "CLICK",
                30_000,
                "STOP",
                None,
                _locator(("css", "#missing"), ("text", "Submit")),
                None,
                None,
            ),
        ),
        (
            WebExecutionAssertionPlan(
                "ASSERT_TEXT", 30_000, _locator(("text", "Hello")), assertion_expected
            ),
            WebExecutionAssertionPlan("ASSERT_URL", 30_000, None, "http://example.test/start"),
        ),
        session,
    )


class _FakeLocator:
    def __init__(self, page: _FakePage, value: str, *, missing: bool = False) -> None:
        self.page = page
        self.value = value
        self.missing = missing

    def wait_for(self, *, state: str, timeout: int) -> None:
        del state, timeout
        if self.missing:
            raise RuntimeError("locator missing")

    def fill(self, value: str, *, timeout: int) -> None:
        del timeout
        self.page.values[self.value] = value

    def click(self, *, timeout: int) -> None:
        del timeout
        self.page.clicked.append(self.value)

    def is_visible(self, *, timeout: int) -> bool:
        del timeout
        return not self.missing

    def inner_text(self, *, timeout: int) -> str:
        del timeout
        return "Hello"


class _FakePage:
    def __init__(self) -> None:
        self.url = ""
        self.values: dict[str, str] = {}
        self.clicked: list[str] = []

    def goto(self, url: str, *, timeout: int) -> None:
        del timeout
        self.url = url

    def locator(self, value: str) -> _FakeLocator:
        return _FakeLocator(self, value, missing=value == "#missing")

    def get_by_text(self, value: str) -> _FakeLocator:
        return _FakeLocator(self, f"text={value}")

    def wait_for_url(self, expected: str, *, timeout: int) -> None:
        del timeout
        if expected != self.url:
            raise RuntimeError("unexpected URL")

    def title(self) -> str:
        return "Example page"


class _FakeContext:
    def __init__(self) -> None:
        self.page = _FakePage()
        self.storage_state = None

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @property
    def chromium(self) -> _FakeContext:
        return self

    def launch(self, **kwargs: object) -> _FakeBrowser:
        assert kwargs["headless"] is True
        assert kwargs.get("channel") == "chrome" or kwargs.get("executable_path")
        return _FakeBrowser(self.page, self)


class _FakeBrowser:
    def __init__(self, page: _FakePage, owner: _FakeContext) -> None:
        self.page = page
        self.owner = owner

    def new_context(self, *, storage_state: object = None) -> _FakeBrowserContext:
        self.owner.storage_state = storage_state
        return _FakeBrowserContext(self.page)

    def close(self) -> None:
        return None


class _FakeBrowserContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        return None


class _EvidencePage(_FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.handlers: dict[str, list[object]] = {}
        self.screenshot_kwargs: list[dict[str, object]] = []

    def on(self, event: str, callback: object) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def goto(self, url: str, *, timeout: int) -> None:
        super().goto(url, timeout=timeout)
        for callback in self.handlers.get("console", []):
            callback(
                SimpleNamespace(
                    type="error",
                    text="token=web-secret-value",
                    location={"url": "http://example.test/assets/app.js", "lineNumber": 3},
                )
            )
        for callback in self.handlers.get("requestfailed", []):
            callback(
                SimpleNamespace(
                    method="GET",
                    url="http://example.test/api?secret=web-secret-value",
                    failure="connection failed",
                )
            )

    def title(self) -> str:
        return "Safe page title"

    def screenshot(self, **kwargs: object) -> None:
        self.screenshot_kwargs.append(dict(kwargs))
        Path(str(kwargs["path"])).write_bytes(b"\x89PNG\r\n\x1a\nmini")


class _EvidenceTracing:
    def __init__(self) -> None:
        self.start_kwargs: dict[str, object] | None = None
        self.stop_called = False

    def start(self, **kwargs: object) -> None:
        self.start_kwargs = dict(kwargs)

    def stop(self, *, path: str) -> None:
        self.stop_called = True
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("trace.trace", b"safe trace event")


class _EvidenceBrowserContext:
    def __init__(self, page: _EvidencePage, tracing: _EvidenceTracing) -> None:
        self.page = page
        self.tracing = tracing
        self.viewport: object | None = None

    def new_page(self) -> _EvidencePage:
        return self.page

    def close(self) -> None:
        return None


class _EvidenceBrowser:
    def __init__(self, page: _EvidencePage, tracing: _EvidenceTracing) -> None:
        self.page = page
        self.tracing = tracing
        self.context: _EvidenceBrowserContext | None = None

    def new_context(self, **kwargs: object) -> _EvidenceBrowserContext:
        self.context = _EvidenceBrowserContext(self.page, self.tracing)
        self.context.viewport = kwargs.get("viewport")
        return self.context

    def close(self) -> None:
        return None


class _EvidencePlaywright:
    def __init__(self) -> None:
        self.page = _EvidencePage()
        self.tracing = _EvidenceTracing()
        self.browser = _EvidenceBrowser(self.page, self.tracing)
        self.chromium = self

    def __enter__(self) -> _EvidencePlaywright:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def launch(self, **kwargs: object) -> _EvidenceBrowser:
        assert kwargs["headless"] is True
        return self.browser


def test_web_executor_uses_typed_template_locator_fallback_and_safe_trace() -> None:
    context = _FakeContext()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(_plan())

    assert result.outcome == "SUCCESS"
    assert [item["node_id"] for item in result.traces] == [
        "action_1",
        "action_2",
        "assertion_1",
        "assertion_2",
    ]
    assert context.page.values == {"#name": "Alice"}
    assert context.page.clicked == ["text=Submit"]
    assert "Hello" not in repr(result)
    assert all(
        set(item)
        == {
            "node_id",
            "status",
            "duration_ms",
            "error_type",
            "error_message",
            "locator_attempts",
        }
        for item in result.traces
    )
    assert result.traces[1]["locator_attempts"] == [
        {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
        {"strategy": "text", "priority": 2, "status": "SUCCESS"},
    ]
    assert all("value" not in item for item in result.traces)


def test_web_executor_defers_ai_semantic_assertion_with_private_page_snapshot() -> None:
    context = _FakeContext()
    plan = replace(
        _plan(),
        assertions=(
            WebExecutionAssertionPlan(
                "ASSERT_AI_SEMANTIC",
                30_000,
                None,
                None,
                None,
                7,
                "页面表达问候语",
                0.8,
            ),
        ),
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(plan)

    assert result.outcome == "SUCCESS"
    assert result.traces[0]["node_id"] == "action_1"
    assert result.traces[-1]["node_id"] == "assertion_1"
    assert result.pending_ai[0]["node_id"] == "assertion_1"
    assert result.pending_ai[0]["response"]["text"] == "Hello"
    assert "Hello" not in repr(result)


def test_web_executor_records_action_failure_then_success_without_locator_values() -> None:
    class FailingClickLocator(_FakeLocator):
        def click(self, *, timeout: int) -> None:
            del timeout
            if self.value == "text=private-first":
                raise RuntimeError("private-action-error")
            super().click(timeout=0)

    class FailingClickPage(_FakePage):
        def get_by_text(self, value: str) -> FailingClickLocator:
            return FailingClickLocator(self, f"text={value}")

    context = _FakeContext()
    context.page = FailingClickPage()
    base = _plan()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(
        replace(
            base,
            actions=(
                WebExecutionActionPlan(
                    "CLICK",
                    30_000,
                    "STOP",
                    None,
                    _locator(("text", "private-first"), ("text", "second")),
                    None,
                    None,
                ),
            ),
            assertions=(),
        )
    )

    assert result.outcome == "SUCCESS"
    assert context.page.clicked == ["text=second"]
    assert result.traces[0]["locator_attempts"] == [
        {"strategy": "text", "priority": 1, "status": "ACTION_FAILED"},
        {"strategy": "text", "priority": 2, "status": "SUCCESS"},
    ]
    wire = json.dumps(result.to_wire(), ensure_ascii=False)
    assert "private-first" not in wire
    assert "private-action-error" not in wire


def test_web_executor_non_locator_nodes_emit_empty_attempts() -> None:
    context = _FakeContext()
    base = _plan()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(
        replace(
            base,
            actions=(
                WebExecutionActionPlan(
                    "GOTO", 30_000, "STOP", "http://example.test/next", None, None, None
                ),
            ),
            assertions=(
                WebExecutionAssertionPlan(
                    "ASSERT_URL", 30_000, None, "http://example.test/next"
                ),
            ),
        )
    )

    assert result.outcome == "SUCCESS"
    assert all(item["locator_attempts"] == [] for item in result.traces)


def test_web_executor_all_locator_misses_emit_bounded_healing_context() -> None:
    class HealingPage(_FakePage):
        def locator(self, value: str) -> _FakeLocator:
            return _FakeLocator(self, value, missing=value.startswith("#missing"))

        def evaluate(self, script: str) -> list[dict[str, str]]:
            assert "innerHTML" not in script
            assert "outerHTML" not in script
            assert "textContent" not in script
            return [
                {
                    "tag": "button",
                    "role": "button",
                    "id": "bad\nid",
                    "title": "tokenized title",
                },
                {"tag": "input", "name": "password", "type": "password"},
            ]

        def title(self) -> str:
            return "password\nfield"

    class HealingContext(_FakeContext):
        def __init__(self) -> None:
            self.page = HealingPage()
            self.storage_state = None

    context = HealingContext()
    base = _plan()
    locator_plan = WebLocatorPlan(
        17,
        (
            WebLocatorCandidate("css", "#missing-one", 1),
            WebLocatorCandidate("css", "#missing-two", 2),
        ),
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(
        replace(
            base,
            actions=(
                WebExecutionActionPlan(
                    "CLICK", 30_000, "STOP", None, locator_plan, None, None
                ),
            ),
            assertions=(),
            start_url="http://example.test/start?token=not-in-context",
        )
    )

    assert result.outcome == "FAILED"
    trace = result.traces[0]
    healing = trace["healing_context"]
    assert healing["schema_version"] == 1
    assert healing["trigger"] == "ALL_LOCATORS_FAILED"
    assert healing["element_version_id"] == 17
    assert healing["page_url"] == "http://example.test/start"
    assert len(healing["dom_candidates"]) == 1
    assert healing["page_title"] == ""
    assert "password" not in json.dumps(healing, ensure_ascii=False)
    assert "token" not in json.dumps(healing, ensure_ascii=False)
    assert "not-in-context" not in json.dumps(healing, ensure_ascii=False)
    assert all(item["status"] == "NOT_FOUND" for item in trace["locator_attempts"])
    assert _web_trace_payload([trace])[0]["healing_context"] == healing


def test_web_executor_locator_assertion_all_misses_emits_healing_context() -> None:
    class HealingAssertionPage(_FakePage):
        def locator(self, value: str) -> _FakeLocator:
            return _FakeLocator(self, value, missing=True)

        def evaluate(self, _script: str) -> list[dict[str, str]]:
            return [{"tag": "button", "role": "button", "id": "safe"}]

    context = _FakeContext()
    context.page = HealingAssertionPage()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(
        replace(
            _plan(),
            actions=(),
            assertions=(
                WebExecutionAssertionPlan(
                    "ASSERT_VISIBLE", 30_000, _locator(("css", "#missing")), None
                ),
            ),
        )
    )

    assert result.outcome == "FAILED"
    trace = result.traces[0]
    assert trace["error_type"] == "ASSERTION_FAILED"
    assert trace["healing_context"]["trigger"] == "ALL_LOCATORS_FAILED"
    assert all(item["status"] == "NOT_FOUND" for item in trace["locator_attempts"])


def test_healing_context_is_best_effort_and_bounded() -> None:
    class BrokenPage(_FakePage):
        def evaluate(self, _script: str) -> object:
            raise RuntimeError("page detail must stay private")

    page = BrokenPage()
    assert _collect_healing_context(page, _locator(("css", "#missing"))) is None

    class ManyPage(_FakePage):
        def evaluate(self, _script: str) -> list[dict[str, str]]:
            return [
                {"tag": "button", "role": "button", "id": f"button-{index}"}
                for index in range(100)
            ]

    context = _collect_healing_context(ManyPage(), _locator(("css", "#missing")))
    assert context is not None
    assert len(context["dom_candidates"]) == 40
    assert len(json.dumps(context, ensure_ascii=False).encode()) <= 64 * 1024

    class SpacedTitlePage(_FakePage):
        def evaluate(self, _script: str) -> list[dict[str, str]]:
            return [{"tag": "button", "role": "button", "id": "safe"}]

        def title(self) -> str:
            return "  Safe title  "

    spaced = _collect_healing_context(SpacedTitlePage(), _locator(("css", "#missing")))
    assert spaced is not None
    assert spaced["page_title"] == "Safe title"


def test_web_executor_action_failed_does_not_emit_healing_context() -> None:
    class FailingPage(_FakePage):
        def locator(self, value: str) -> _FakeLocator:
            locator = _FakeLocator(self, value)

            def fail_click(*, timeout: int) -> None:
                del timeout
                raise RuntimeError("action failure")

            locator.click = fail_click  # type: ignore[method-assign]
            return locator

    context = _FakeContext()
    context.page = FailingPage()
    base = _plan()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(
        replace(
            base,
            actions=(
                WebExecutionActionPlan(
                    "CLICK", 30_000, "STOP", None, _locator(("css", "#button")), None, None
                ),
            ),
            assertions=(),
        )
    )
    assert result.outcome == "FAILED"
    assert "healing_context" not in result.traces[0]


def test_web_executor_assertion_failure_does_not_include_actual_page_text() -> None:
    context = _FakeContext()
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(_plan(assertion_expected="secret-page-text"))

    assert result.outcome == "FAILED"
    assert result.error_type == "ASSERTION_FAILED"
    assert "secret-page-text" not in repr(result)
    assert all("Hello" not in repr(item) for item in result.traces)


def test_web_executor_loads_session_in_memory_without_repr_leak() -> None:
    context = _FakeContext()
    session = WebSessionPlan(
        9,
        {"cookies": [{"name": "session", "value": "session-secret-value"}]},
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(_plan(session=session))

    assert result.outcome == "SUCCESS"
    assert context.storage_state == session.storage_state
    assert "session-secret-value" not in repr(result)


def test_web_executor_node_timeout_is_safe_and_terminal() -> None:
    class TimeoutLocator(_FakeLocator):
        def wait_for(self, *, state: str, timeout: int) -> None:
            del state, timeout
            raise TimeoutError

    class TimeoutPage(_FakePage):
        def locator(self, value: str) -> TimeoutLocator:
            return TimeoutLocator(self, value)

    class TimeoutContext(_FakeContext):
        def __init__(self) -> None:
            self.page = TimeoutPage()
            self.storage_state = None

    context = TimeoutContext()
    base_plan = _plan()
    node_timeout_plan = replace(
        base_plan,
        actions=(replace(base_plan.actions[0], timeout_ms=100),),
        assertions=(),
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(node_timeout_plan)

    assert result.outcome == "TIMEOUT"
    assert result.error_type == "WEB_NODE_TIMEOUT"
    assert "TimeoutLocator" not in repr(result)


def test_web_executor_collects_masked_screenshot_summary_events_and_trace() -> None:
    playwright = _EvidencePlaywright()
    with TemporaryDirectory() as evidence_dir:
        result = WebExecutor(
            playwright_factory=lambda: playwright,
            chrome_executable="test-chrome",
            evidence_dir=evidence_dir,
        ).execute(_plan())

        assert result.outcome == "SUCCESS"
        artifact_types = {
            item.artifact_type
            for item in validate_manifest(evidence_dir, result.evidence.to_wire())
        }
        assert artifact_types == {
            "SCREENSHOT",
            "CONSOLE_ERROR",
            "NETWORK_ERROR",
            "WEB_SUMMARY",
            "PLAYWRIGHT_TRACE",
        }
        assert playwright.tracing.start_kwargs == {
            "screenshots": False,
            "snapshots": False,
            "sources": False,
        }
        assert len(playwright.page.screenshot_kwargs) == 1
        screenshot_kwargs = playwright.page.screenshot_kwargs[0]
        assert screenshot_kwargs["full_page"] is False
        assert screenshot_kwargs["mask"]
        summary = json.loads(
            (Path(evidence_dir) / "web-summary.json").read_text(encoding="utf-8")
        )
        assert summary["final_url"] == "http://example.test/start"
        console = json.loads(
            (Path(evidence_dir) / "console-error-1.json").read_text(encoding="utf-8")
        )
        network = json.loads(
            (Path(evidence_dir) / "network-error-1.json").read_text(encoding="utf-8")
        )
        assert "web-secret-value" not in json.dumps(console)
        assert network["url"] is None
        assert "web-secret-value" not in repr(result)


def test_web_executor_drops_screenshot_when_masked_call_is_unsupported() -> None:
    class NoMaskPage(_EvidencePage):
        def screenshot(self, **kwargs: object) -> None:
            self.screenshot_kwargs.append(dict(kwargs))
            raise TypeError("mask is unsupported")

    playwright = _EvidencePlaywright()
    page = NoMaskPage()
    playwright.page = page
    playwright.browser.page = page
    with TemporaryDirectory() as evidence_dir:
        result = WebExecutor(
            playwright_factory=lambda: playwright,
            chrome_executable="test-chrome",
            evidence_dir=evidence_dir,
        ).execute(_plan())

        assert result.outcome == "SUCCESS"
        assert all(item.artifact_type != "SCREENSHOT" for item in result.evidence.items)
        assert not list(Path(evidence_dir).glob("*.png"))


def test_web_executor_unexpected_page_error_still_stops_trace_and_writes_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright = _EvidencePlaywright()
    with TemporaryDirectory() as evidence_dir:
        executor = WebExecutor(
            playwright_factory=lambda: playwright,
            chrome_executable="test-chrome",
            evidence_dir=evidence_dir,
        )

        def fail_page(*_args: object, **_kwargs: object) -> WebExecutionResult:
            raise RuntimeError("unexpected page detail")

        from runner.executors.web import WebExecutionResult

        monkeypatch.setattr(executor, "_execute_page", fail_page)
        result = executor.execute(_plan())

        assert result.outcome == "FAILED"
        assert result.error_type == "WEB_EXECUTOR_ERROR"
        assert (Path(evidence_dir) / "web-summary.json").exists()
        assert playwright.tracing.stop_called is True
        assert any(item.artifact_type == "WEB_SUMMARY" for item in result.evidence.items)


def test_web_executor_unexpected_evidence_finish_is_failed_and_stops_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright = _EvidencePlaywright()
    with TemporaryDirectory() as evidence_dir:
        executor = WebExecutor(
            playwright_factory=lambda: playwright,
            chrome_executable="test-chrome",
            evidence_dir=evidence_dir,
        )

        def fail_finish(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("unexpected evidence detail")

        monkeypatch.setattr("runner.executors.web._WebEvidenceCollector.finish", fail_finish)
        result = executor.execute(_plan())

        assert result.outcome == "FAILED"
        assert result.error_type == "WEB_EXECUTOR_ERROR"
        assert (Path(evidence_dir) / "web-summary.json").exists()
        assert playwright.tracing.stop_called is True
        assert any(item.artifact_type == "WEB_SUMMARY" for item in result.evidence.items)


def test_trace_audit_drops_secret_query_and_rendered_input_values() -> None:
    def make_trace(content: bytes) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("trace.trace", content)
        return buffer.getvalue()

    assert audit_trace_bytes(make_trace(b"safe trace")) is True
    assert audit_trace_bytes(make_trace(b"token=hidden")) is False
    assert audit_trace_bytes(make_trace(b"http://example.test/path?x=1")) is False
    assert audit_trace_bytes(make_trace(b"fill value"), forbidden_values=("fill value",)) is False

    duplicate = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("trace.trace", b"first")
            archive.writestr("trace.trace", b"second")
    assert audit_trace_bytes(duplicate.getvalue()) is False
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "duplicate.zip"
        path.write_bytes(duplicate.getvalue())
        assert sanitize_trace_file(path) is False

    directory_entry = io.BytesIO()
    with zipfile.ZipFile(directory_entry, "w") as archive:
        archive.writestr("resources/", b"")
        archive.writestr("trace.trace", b"safe trace")
    assert audit_trace_bytes(directory_entry.getvalue()) is True
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "directory.zip"
        path.write_bytes(directory_entry.getvalue())
        assert sanitize_trace_file(path) is True

    css_error = io.BytesIO()
    with zipfile.ZipFile(css_error, "w") as archive:
        archive.writestr(
            "trace.trace",
            b'{"type":"after","error":{"message":"Unexpected token in selector",'
            b'"stack":"safe stack with token terminology"}}\n',
        )
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "css-error.zip"
        path.write_bytes(css_error.getvalue())
        assert sanitize_trace_file(path) is True
        cleaned = path.read_bytes()
        assert audit_trace_bytes(cleaned) is True
        assert b"Unexpected token" not in cleaned

    sensitive_key = io.BytesIO()
    with zipfile.ZipFile(sensitive_key, "w") as archive:
        archive.writestr("trace.trace", b'{"type":"event","token":"hidden"}\n')
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "sensitive-key.zip"
        path.write_bytes(sensitive_key.getvalue())
        assert sanitize_trace_file(path) is False

    sensitive_value = io.BytesIO()
    with zipfile.ZipFile(sensitive_value, "w") as archive:
        archive.writestr("trace.trace", b'{"type":"event","message":"token=hidden"}\n')
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "sensitive-value.zip"
        path.write_bytes(sensitive_value.getvalue())
        assert sanitize_trace_file(path) is False

    windows_resource = io.BytesIO()
    with zipfile.ZipFile(windows_resource, "w") as archive:
        archive.writestr("resources\\safe-resource", b"safe trace")
        archive.writestr("trace.trace", b"safe trace")
    assert audit_trace_bytes(windows_resource.getvalue()) is True
    with TemporaryDirectory() as evidence_dir:
        path = Path(evidence_dir) / "windows-resource.zip"
        path.write_bytes(windows_resource.getvalue())
        assert sanitize_trace_file(path) is True


def test_evidence_validation_rejects_path_traversal_bad_magic_and_nested_json() -> None:
    with TemporaryDirectory() as evidence_dir:
        root = Path(evidence_dir)
        (root / "bad.png").write_bytes(b"not-png")
        metadata = {"width": 1280, "height": 720, "title": "title", "page_url": None}
        with pytest.raises(EvidenceValidationError):
            validate_manifest(
                evidence_dir,
                [
                    {
                        "artifact_type": "SCREENSHOT",
                        "relative_path": "../bad.png",
                        "metadata": metadata,
                    }
                ],
            )
        with pytest.raises(EvidenceValidationError):
            validate_artifact(evidence_dir, "SCREENSHOT", "bad.png", metadata)
        (root / "data.json").write_text('{"nested": {"value": 1}}', encoding="utf-8")
        with pytest.raises(EvidenceValidationError):
            validate_artifact(evidence_dir, "WEB_SUMMARY", "data.json", {"status": "SUCCESS"})
        (root / "large.json").write_bytes(b'{"status":"' + b"a" * (2 * 1024 * 1024) + b'"}')
        with pytest.raises(EvidenceValidationError):
            validate_artifact(evidence_dir, "WEB_SUMMARY", "large.json", {"status": "SUCCESS"})
        with pytest.raises(EvidenceValidationError):
            validate_artifact(
                evidence_dir,
                "SCREENSHOT",
                "bad.png",
                {
                    "width": 1280,
                    "height": 720,
                    "title": "title",
                    "page_url": "http://example.test/path?secret=value",
                },
            )


def test_same_screenshot_content_keeps_final_and_failure_artifact_names_distinct() -> None:
    with TemporaryDirectory() as evidence_dir:
        root = Path(evidence_dir)
        data = b"\x89PNG\r\n\x1a\nidentical"
        (root / "screenshot-final.png").write_bytes(data)
        (root / "screenshot-failure-1.png").write_bytes(data)
        metadata = {"width": 1280, "height": 720, "title": "title", "page_url": None}

        final = validate_artifact(evidence_dir, "SCREENSHOT", "screenshot-final.png", metadata)
        failure = validate_artifact(
            evidence_dir, "SCREENSHOT", "screenshot-failure-1.png", metadata
        )

        assert final.artifact_name.startswith("screenshot-final-")
        assert failure.artifact_name.startswith("screenshot-failure-1-")
        assert final.artifact_name != failure.artifact_name


def test_web_executor_orders_locator_candidates_by_priority_then_original_order() -> None:
    context = _FakeContext()
    base = _plan()
    unordered = WebLocatorPlan(
        None,
        (
            WebLocatorCandidate("text", "high", 9),
            WebLocatorCandidate("text", "low", 2),
            WebLocatorCandidate("text", "low-tie", 2),
        ),
    )
    actions = (
        base.actions[0],
        replace(base.actions[1], locator=unordered),
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome"
    ).execute(replace(base, actions=actions))

    assert result.outcome == "SUCCESS"
    assert context.page.clicked == ["text=low"]


def test_web_executor_locator_fallback_shares_one_node_timeout_budget() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, milliseconds: int) -> None:
            self.value += milliseconds / 1000.0

    class BudgetLocator(_FakeLocator):
        def wait_for(self, *, state: str, timeout: int) -> None:
            del state
            self.page.clock.advance(min(timeout, 60))
            raise RuntimeError("candidate unavailable")

    class BudgetPage(_FakePage):
        def __init__(self, clock: Clock) -> None:
            super().__init__()
            self.clock = clock
            self.timeouts: list[int] = []

        def locator(self, value: str) -> BudgetLocator:
            self.timeouts.append(max(0, int((0.1 - self.clock()) * 1000)))
            return BudgetLocator(self, value)

    class BudgetContext(_FakeContext):
        def __init__(self, clock: Clock) -> None:
            self.page = BudgetPage(clock)
            self.storage_state = None

    clock = Clock()
    context = BudgetContext(clock)
    base = _plan()
    budget_plan = replace(
        base,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                100,
                "STOP",
                None,
                WebLocatorPlan(
                    None,
                    (
                        WebLocatorCandidate("css", "#one", 1),
                        WebLocatorCandidate("css", "#two", 2),
                        WebLocatorCandidate("css", "#three", 3),
                    ),
                ),
                None,
                None,
            ),
        ),
        assertions=(),
    )
    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome", clock=clock
    ).execute(budget_plan)

    assert result.outcome == "TIMEOUT"
    assert result.error_type == "WEB_NODE_TIMEOUT"
    assert context.page.timeouts == [100, 40]
    assert clock.value <= 0.1


def test_web_executor_assertion_candidates_timeout_when_budget_is_exhausted() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, milliseconds: int) -> None:
            self.value += milliseconds / 1000.0

    class AssertionBudgetLocator(_FakeLocator):
        def is_visible(self, *, timeout: int) -> bool:
            self.page.clock.advance(timeout)
            return False

    class AssertionBudgetPage(_FakePage):
        def __init__(self, clock: Clock) -> None:
            super().__init__()
            self.clock = clock

        def locator(self, value: str) -> AssertionBudgetLocator:
            return AssertionBudgetLocator(self, value)

    class AssertionBudgetContext(_FakeContext):
        def __init__(self, clock: Clock) -> None:
            self.page = AssertionBudgetPage(clock)
            self.storage_state = None

    clock = Clock()
    context = AssertionBudgetContext(clock)
    base = _plan()
    budget_plan = replace(
        base,
        actions=(),
        assertions=(
            WebExecutionAssertionPlan(
                "ASSERT_VISIBLE",
                100,
                WebLocatorPlan(
                    None,
                    (
                        WebLocatorCandidate("css", "#one", 1),
                        WebLocatorCandidate("css", "#two", 2),
                    ),
                ),
                None,
            ),
        ),
    )

    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome", clock=clock
    ).execute(budget_plan)

    assert result.outcome == "TIMEOUT"
    assert result.error_type == "WEB_NODE_TIMEOUT"


def test_web_executor_locator_action_uses_budget_remaining_after_wait() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, milliseconds: int) -> None:
            self.value += milliseconds / 1000.0

    class ReadyBudgetLocator(_FakeLocator):
        def wait_for(self, *, state: str, timeout: int) -> None:
            del state, timeout
            self.page.clock.advance(30)

        def click(self, *, timeout: int) -> None:
            self.page.action_timeouts.append(timeout)

    class ReadyBudgetPage(_FakePage):
        def __init__(self, clock: Clock) -> None:
            super().__init__()
            self.clock = clock
            self.action_timeouts: list[int] = []

        def locator(self, value: str) -> ReadyBudgetLocator:
            return ReadyBudgetLocator(self, value)

    class ReadyBudgetContext(_FakeContext):
        def __init__(self, clock: Clock) -> None:
            self.page = ReadyBudgetPage(clock)
            self.storage_state = None

    clock = Clock()
    context = ReadyBudgetContext(clock)
    base = _plan()
    budget_plan = replace(
        base,
        actions=(
            WebExecutionActionPlan(
                "CLICK",
                100,
                "STOP",
                None,
                WebLocatorPlan(
                    None, (WebLocatorCandidate("css", "#submit", 1),)
                ),
                None,
                None,
            ),
        ),
        assertions=(),
    )

    result = WebExecutor(
        playwright_factory=lambda: context, chrome_executable="test-chrome", clock=clock
    ).execute(budget_plan)

    assert result.outcome == "SUCCESS"
    assert context.page.action_timeouts == [70]


def test_snapshot_web_ready_requires_explicit_probe_and_exposes_web_slot() -> None:
    class ReadyProbe(FakeProbe):
        def web_ready(self) -> bool:
            return True

    snapshot = collect_snapshot("runner-test", web_slots=1, probe=ReadyProbe())
    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert capabilities["WEB"] == {"name": "WEB", "status": "READY", "reason": None}
    assert {item["type"]: item for item in snapshot["slots"]}["WEB"] == {
        "type": "WEB",
        "total": 1,
        "available": 1,
    }


def _web_plan_body() -> dict[str, object]:
    locator = {
        "element_version_id": None,
        "candidates": [{"strategy": "css", "value": "#name", "priority": 1}],
    }
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "web_case_id": 7,
        "web_case_version_id": 8,
        "start_url": "http://example.test/start",
        "browser": "CHROME",
        "headless": True,
        "total_timeout_ms": 10_000,
        "initial_context": {"name": "Alice"},
        "actions": [
            {
                "type": "FILL",
                "timeout_ms": 30_000,
                "failure_policy": "STOP",
                "url": None,
                "locator": locator,
                "value": "{{name}}",
                "key": None,
            }
        ],
        "assertions": [],
        "session": None,
    }


def test_web_protocol_uses_exact_routes_and_strict_complete_shape() -> None:
    traces = [
        {
            "node_id": "action_1",
            "status": "SUCCESS",
            "duration_ms": 3,
            "error_type": None,
            "error_message": None,
            "locator_attempts": [
                {"strategy": "css", "priority": 1, "status": "SUCCESS"}
            ],
        }
    ]
    legacy_response_traces = [
        {
            "node_id": "action_1",
            "status": "SUCCESS",
            "duration_ms": 3,
            "error_type": None,
            "error_message": None,
        }
    ]
    complete_body = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "outcome": "SUCCESS",
        "run_status": "SUCCESS",
        "case_run_status": "SUCCESS",
        "traces": legacy_response_traces,
        "error_type": None,
        "error_message": None,
        "completed_at": "2026-09-01T10:00:00Z",
        "idempotent": False,
    }
    transport = FakeTransport(
        HttpResponse(200, _web_plan_body()),
        HttpResponse(
            200,
            {
                "schema_version": 1,
                "run_id": "run-001",
                "message_id": "message-001",
                "runner_id": "runner-001",
                "case_run_id": 42,
                "status": "RUNNING",
                "started_at": "2026-09-01T10:00:00+00:00",
                "idempotent": False,
            },
        ),
        HttpResponse(200, complete_body),
    )
    client = RunnerClient("http://backend.test", transport)

    plan = client.get_web_execution_plan(IDENTITY, "run-001", "message-001")
    started = client.web_execution_start(IDENTITY, "run-001", "message-001", plan.case_run_id)
    complete = client.web_execution_complete(
        IDENTITY,
        "run-001",
        "message-001",
        plan.case_run_id,
        outcome="SUCCESS",
        traces=traces,
    )

    assert plan.actions[0].value == "{{name}}"
    assert started.started_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert complete.outcome == "SUCCESS"
    assert complete.traces[0]["locator_attempts"] == []
    assert transport.calls[0]["url"].endswith(
        "/api/v1/runs/run-001/web-execution-plan?message_id=message-001"
    )
    assert transport.calls[1]["json"] == {"message_id": "message-001", "case_run_id": 42}
    assert transport.calls[2]["json"]["traces"] == traces
    assert IDENTITY.credential not in repr(complete)


def test_web_protocol_rejects_unknown_plan_fields_without_secret_echo() -> None:
    body = _web_plan_body()
    body["unknown"] = "credential-value-123456789"
    transport = FakeTransport(HttpResponse(200, body))
    client = RunnerClient("http://backend.test", transport)

    with pytest.raises(ProtocolError) as exc:
        client.get_web_execution_plan(IDENTITY, "run-001", "message-001")
    assert IDENTITY.credential not in str(exc.value)


def test_web_evidence_multipart_shape_and_strict_response() -> None:
    class MultipartTransport(FakeTransport):
        def __init__(self, *responses: HttpResponse | Exception) -> None:
            super().__init__(*responses)
            self.multipart: dict[str, object] | None = None
            self.multipart_calls: list[dict[str, object]] = []

        def post_multipart(self, url: str, **kwargs: object) -> HttpResponse:
            self.multipart = {"url": url, **kwargs}
            self.multipart_calls.append(self.multipart)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            assert isinstance(response, HttpResponse)
            return response

    with TemporaryDirectory() as evidence_dir:
        root = Path(evidence_dir)
        relative_path = "final.png"
        (root / relative_path).write_bytes(b"\x89PNG\r\n\x1a\ncontent")
        metadata = {
            "width": 1280,
            "height": 720,
            "title": "title",
            "page_url": "http://example.test/result",
        }
        artifact = validate_artifact(evidence_dir, "SCREENSHOT", relative_path, metadata)
        repeat_artifact = validate_artifact(evidence_dir, "SCREENSHOT", relative_path, metadata)
        assert repeat_artifact.artifact_name == artifact.artifact_name
        response_body = {
            "schema_version": 1,
            "run_id": "run-001",
            "message_id": "message-001",
            "runner_id": "runner-001",
            "case_run_id": 42,
            "id": "evidence-17",
            "artifact_type": "SCREENSHOT",
            "artifact_name": artifact.artifact_name,
            "file_name": artifact.artifact_name + ".png",
            "mime": "image/png",
            "size": artifact.size,
            "sha256": artifact.sha256,
            "metadata": metadata,
            "created_at": "2026-09-01T10:00:00Z",
            "idempotent": False,
        }
        transport = MultipartTransport(HttpResponse(200, response_body))
        client = RunnerClient("http://backend.test", transport, max_attempts=1)

        result = client.upload_web_evidence(
            IDENTITY, "run-001", "message-001", 42, artifact
        )

        assert result.evidence_id == "evidence-17"
        assert result.sha256 == artifact.sha256
        assert transport.multipart is not None
        assert transport.multipart["url"] == (
            "http://backend.test/api/v1/runs/run-001/web-evidence"
        )
        assert transport.multipart["file_name"] == artifact.artifact_name + ".png"
        fields = transport.multipart["fields"]
        assert isinstance(fields, dict)
        assert fields["message_id"] == "message-001"
        assert fields["case_run_id"] == "42"
        assert fields["artifact_name"] == artifact.artifact_name
        assert fields["sha256"] == artifact.sha256
        assert fields["metadata_json"] == json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        assert IDENTITY.credential not in repr(result)

        invalid_response = dict(response_body)
        invalid_response["unexpected"] = "ignored"
        invalid_transport = MultipartTransport(HttpResponse(200, invalid_response))
        invalid_client = RunnerClient("http://backend.test", invalid_transport, max_attempts=1)
        with pytest.raises(ProtocolError):
            invalid_client.upload_web_evidence(
                IDENTITY, "run-001", "message-001", 42, artifact
            )
        invalid_sha_response = dict(response_body)
        invalid_sha_response["sha256"] = "0" * 64
        sha_transport = MultipartTransport(HttpResponse(200, invalid_sha_response))
        sha_client = RunnerClient("http://backend.test", sha_transport, max_attempts=1)
        with pytest.raises(ProtocolError):
            sha_client.upload_web_evidence(IDENTITY, "run-001", "message-001", 42, artifact)
        invalid_file_response = dict(response_body)
        invalid_file_response["file_name"] = "other-artifact.png"
        file_transport = MultipartTransport(HttpResponse(200, invalid_file_response))
        file_client = RunnerClient("http://backend.test", file_transport, max_attempts=1)
        with pytest.raises(ProtocolError):
            file_client.upload_web_evidence(
                IDENTITY, "run-001", "message-001", 42, artifact
            )

        sleeps: list[float] = []
        retry_transport = MultipartTransport(
            TransportError("temporary", transient=True),
            HttpResponse(503, {"code": "TEMPORARY"}),
            HttpResponse(200, response_body),
        )
        retry_client = RunnerClient(
            "http://backend.test",
            retry_transport,
            max_attempts=3,
            backoff_base_seconds=0.1,
            backoff_max_seconds=1.0,
            sleeper=sleeps.append,
        )
        retried = retry_client.upload_web_evidence(
            IDENTITY, "run-001", "message-001", 42, artifact
        )
        assert retried.evidence_id == "evidence-17"
        assert len(retry_transport.multipart_calls) == 3
        assert sleeps == [0.1, 0.2]

        exhausted_transport = MultipartTransport(
            ConnectionError("lost-1"),
            ConnectionError("lost-2"),
            ConnectionError("lost-3"),
        )
        exhausted_client = RunnerClient(
            "http://backend.test",
            exhausted_transport,
            max_attempts=3,
            backoff_base_seconds=0,
        )
        with pytest.raises(TransportError, match="重试失败"):
            exhausted_client.upload_web_evidence(
                IDENTITY, "run-001", "message-001", 42, artifact
            )
        assert len(exhausted_transport.multipart_calls) == 3


class _Channel:
    def __init__(self) -> None:
        self.acks: list[object] = []
        self.rejects: list[object] = []
        self.nacks: list[object] = []
        self.stop_calls = 0

    def basic_ack(self, **kwargs: object) -> None:
        self.acks.append(kwargs["delivery_tag"])

    def basic_reject(self, **kwargs: object) -> None:
        self.rejects.append(kwargs["delivery_tag"])

    def basic_nack(self, **kwargs: object) -> None:
        self.nacks.append(kwargs["delivery_tag"])

    def stop_consuming(self) -> None:
        self.stop_calls += 1


class _WebClient:
    def __init__(
        self,
        plan: WebExecutionPlanResult,
        *,
        cancel_after: int | None = None,
        upload_error: Exception | None = None,
        complete_error: Exception | None = None,
    ) -> None:
        self.plan = plan
        self.cancel_after = cancel_after
        self.upload_error = upload_error
        self.complete_error = complete_error
        self.control_calls = 0
        self.calls: list[str] = []
        self.uploaded: list[object] = []
        self.completed_outcomes: list[str] = []
        self.completed_payloads: list[dict[str, object]] = []
        self.evaluation_result: WebExecutionEvaluationResult | None = None
        self.evaluated_items: list[dict[str, object]] = []

    def claim(self, identity, run_id, message_id):
        del identity, run_id, message_id
        self.calls.append("claim")
        return ClaimResult(
            "run-001", "message-001", "runner-001", "ASSIGNED", datetime.now(UTC), False
        )

    def get_web_execution_plan(self, identity, run_id, message_id, case_run_id=None):
        del identity, run_id, message_id
        self.calls.append("plan")
        assert case_run_id is None
        return self.plan

    def web_execution_start(self, identity, run_id, message_id, case_run_id):
        del identity, run_id, message_id
        self.calls.append("start")
        return WebExecutionStartResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            case_run_id,
            "RUNNING",
            datetime.now(UTC),
            False,
        )

    def get_execution_cancellation(self, identity, run_id, message_id, case_run_id):
        del identity, run_id, message_id
        self.calls.append("cancel")
        self.control_calls += 1
        requested = self.cancel_after is not None and self.control_calls >= self.cancel_after
        return ExecutionCancellationResult(
            1, "run-001", "message-001", case_run_id, "RUNNING", requested, False
        )

    def upload_web_evidence(
        self, identity, run_id, message_id, case_run_id, artifact
    ) -> WebEvidenceUploadResult:
        del identity, run_id, message_id, case_run_id
        self.calls.append("upload")
        self.uploaded.append(artifact)
        if self.upload_error is not None:
            raise self.upload_error
        return WebEvidenceUploadResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            42,
            "evidence-9",
            artifact.artifact_type,
            artifact.artifact_name,
            "server-file.png",
            artifact.mime,
            artifact.size,
            artifact.sha256,
            dict(artifact.metadata),
            datetime.now(UTC),
            False,
        )

    def web_execution_complete(self, identity, run_id, message_id, case_run_id, **kwargs):
        del identity
        self.calls.append("complete")
        outcome = kwargs["outcome"]
        self.completed_outcomes.append(outcome)
        self.completed_payloads.append(dict(kwargs))
        if self.complete_error is not None:
            raise self.complete_error
        case_run_status = (
            "REVIEW" if kwargs.get("error_type") == "ASSERTION_REVIEW" else outcome
        )
        return WebExecutionCompleteResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            outcome,
            outcome,
            case_run_status,
            kwargs["traces"],
            kwargs.get("error_type"),
            kwargs.get("error_message"),
            datetime.now(UTC),
            False,
        )

    def web_execution_evaluate(
        self, identity, run_id, message_id, case_run_id, items
    ) -> WebExecutionEvaluationResult:
        del identity
        self.calls.append("evaluate")
        self.evaluated_items = list(items)
        if self.evaluation_result is not None:
            return self.evaluation_result
        return WebExecutionEvaluationResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            "PASS",
            (),
            tuple(
                {"node_id": item["node_id"], "status": "PASS"}
                for item in self.evaluated_items
            ),
            "signed-web-evaluation",
        )


class _FinishedProcess:
    def __init__(self, outcome: IsolatedOutcome | None = None) -> None:
        self.outcome_value = outcome or IsolatedOutcome(
            "WEB_RESULT",
            {
                "outcome": "SUCCESS",
                "traces": [
                    {
                        "node_id": "action_1",
                        "status": "SUCCESS",
                        "duration_ms": 1,
                        "error_type": None,
                        "error_message": None,
                    },
                    {
                        "node_id": "assertion_1",
                        "status": "SUCCESS",
                        "duration_ms": 1,
                        "error_type": None,
                        "error_message": None,
                    },
                ],
                "error_type": None,
                "error_message": None,
            },
        )
        self.closed = False

    def start(self) -> None:
        return None

    def poll(self, timeout_seconds: float) -> bool:
        del timeout_seconds
        return True

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        return None

    def request_cancel(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        del timeout

    @property
    def exitcode(self) -> int:
        return 0

    def outcome(self) -> IsolatedOutcome:
        return self.outcome_value

    def close(self) -> None:
        self.closed = True


def _successful_web_outcome(*, evidence: list[dict[str, object]] | None = None) -> IsolatedOutcome:
    result: dict[str, object] = {
        "outcome": "SUCCESS",
        "traces": [
            {
                "node_id": "action_1",
                "status": "SUCCESS",
                "duration_ms": 1,
                "error_type": None,
                "error_message": None,
            },
            {
                "node_id": "action_2",
                "status": "SUCCESS",
                "duration_ms": 1,
                "error_type": None,
                "error_message": None,
            },
            {
                "node_id": "assertion_1",
                "status": "SUCCESS",
                "duration_ms": 1,
                "error_type": None,
                "error_message": None,
            },
            {
                "node_id": "assertion_2",
                "status": "SUCCESS",
                "duration_ms": 1,
                "error_type": None,
                "error_message": None,
            },
        ],
        "error_type": None,
        "error_message": None,
    }
    if evidence is not None:
        result["evidence"] = evidence
    return IsolatedOutcome("WEB_RESULT", result)


def _web_delivery_body() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "WEB_CASE",
            "required_slot_type": "WEB",
            "attempt": 1,
            "enqueued_at": "2026-09-01T10:00:00Z",
        }
    ).encode()


def test_web_consumer_claims_executes_completes_before_ack() -> None:
    body = {
        "schema_version": 1,
        "message_id": "message-001",
        "run_id": "run-001",
        "runner_id": "runner-001",
        "run_type": "WEB_CASE",
        "required_slot_type": "WEB",
        "attempt": 1,
        "enqueued_at": "2026-09-01T10:00:00Z",
    }
    import json

    channel = _Channel()
    client = _WebClient(_plan())
    process = _FinishedProcess()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=lambda _plan: process,
    )

    outcome = consumer.process_execution_delivery(10, json.dumps(body).encode())

    assert outcome is ConsumeOutcome.ACKED
    assert client.calls == ["claim", "plan", "start", "cancel", "cancel", "upload", "complete"]
    assert channel.acks == [10]
    assert channel.rejects == []
    assert process.closed is True


def test_web_consumer_evaluates_pending_ai_before_trusted_completion() -> None:
    channel = _Channel()
    client = _WebClient(_plan())
    client.evaluation_result = WebExecutionEvaluationResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "REVIEW",
        ({"name": "assertion_1", "status": "REVIEW"},),
        ({"node_id": "assertion_1", "status": "REVIEW"},),
        "signed-web-evaluation",
    )
    process = _FinishedProcess(
        IsolatedOutcome(
            "WEB_RESULT",
            {
                "outcome": "SUCCESS",
                "traces": [
                    {
                        "node_id": "assertion_1",
                        "status": "SUCCESS",
                        "duration_ms": 1,
                        "error_type": None,
                        "error_message": None,
                    }
                ],
                "error_type": None,
                "error_message": None,
                "pending_ai": [
                    {
                        "node_id": "assertion_1",
                        "response": {
                            "status_code": 200,
                            "json_body": {"page_title": "Created"},
                            "text": "Order created",
                        },
                    }
                ],
            },
        )
    )
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=lambda _plan: process,
    )

    outcome = consumer.process_execution_delivery(101, _web_delivery_body())

    assert outcome is ConsumeOutcome.ACKED
    assert client.calls[-2:] == ["evaluate", "complete"]
    assert client.evaluated_items[0]["node_id"] == "assertion_1"
    payload = client.completed_payloads[0]
    assert payload["outcome"] == "FAILED"
    assert payload["evaluation_token"] == "signed-web-evaluation"
    assert payload["traces"][0]["status"] == "REVIEW"
    assert payload["error_type"] == "ASSERTION_REVIEW"
    assert channel.acks == [101]


def test_web_consumer_uploads_evidence_before_complete_and_cleans_tempdir() -> None:
    body = {
        "schema_version": 1,
        "message_id": "message-001",
        "run_id": "run-001",
        "runner_id": "runner-001",
        "run_type": "WEB_CASE",
        "required_slot_type": "WEB",
        "attempt": 1,
        "enqueued_at": "2026-09-01T10:00:00Z",
    }
    channel = _Channel()
    client = _WebClient(_plan())
    process_roots: list[str] = []

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        del plan
        process_roots.append(evidence_dir)
        relative_path = "screenshot-final.png"
        (Path(evidence_dir) / relative_path).write_bytes(b"\x89PNG\r\n\x1a\nmini")
        artifact = {
            "artifact_type": "SCREENSHOT",
            "relative_path": relative_path,
            "metadata": {
                "width": 1280,
                "height": 720,
                "title": "title",
                "page_url": "http://example.test/start",
            },
        }
        return _FinishedProcess(
            IsolatedOutcome(
                "WEB_RESULT",
                {
                    "outcome": "SUCCESS",
                    "traces": [
                        {
                            "node_id": "action_1",
                            "status": "SUCCESS",
                            "duration_ms": 1,
                            "error_type": None,
                            "error_message": None,
                        }
                    ],
                    "error_type": None,
                    "error_message": None,
                    "evidence": [artifact],
                },
            )
        )

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=factory,
    )

    import json

    outcome = consumer.process_execution_delivery(11, json.dumps(body).encode())

    assert outcome is ConsumeOutcome.ACKED
    assert client.calls == [
        "claim",
        "plan",
        "start",
        "cancel",
        "cancel",
        "upload",
        "upload",
        "complete",
    ]
    assert channel.acks == [11]
    assert process_roots and not Path(process_roots[0]).exists()


def test_web_consumer_cancel_after_child_result_uploads_before_cancel_complete() -> None:
    body = {
        "schema_version": 1,
        "message_id": "message-001",
        "run_id": "run-001",
        "runner_id": "runner-001",
        "run_type": "WEB_CASE",
        "required_slot_type": "WEB",
        "attempt": 1,
        "enqueued_at": "2026-09-01T10:00:00Z",
    }
    channel = _Channel()
    client = _WebClient(_plan(), cancel_after=2)
    process_roots: list[str] = []

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        del plan
        process_roots.append(evidence_dir)
        metadata = {
            "title": "safe",
            "final_url": "http://example.test/start",
            "page_count": 1,
            "duration_ms": 1,
            "error_count": 0,
            "status": "CANCELLED",
        }
        (Path(evidence_dir) / "web-summary.json").write_bytes(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        )
        return _FinishedProcess(
            IsolatedOutcome(
                "WEB_RESULT",
                {
                    "outcome": "CANCELLED",
                    "traces": [],
                    "error_type": "CANCEL_REQUESTED",
                    "error_message": "Runner 检测到取消请求",
                    "evidence": [
                        {
                            "artifact_type": "WEB_SUMMARY",
                            "relative_path": "web-summary.json",
                            "metadata": metadata,
                        }
                    ],
                },
            )
        )

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=factory,
    )

    outcome = consumer.process_execution_delivery(13, json.dumps(body).encode())

    assert outcome is ConsumeOutcome.ACKED
    assert client.calls == ["claim", "plan", "start", "cancel", "cancel", "upload", "complete"]
    assert client.completed_outcomes == ["CANCELLED"]
    assert channel.acks == [13]
    assert channel.rejects == []
    assert process_roots and not Path(process_roots[0]).exists()


@pytest.mark.parametrize(
    ("process_kind", "expected_outcome"),
    [
        ("TOTAL_TIMEOUT", "TIMEOUT"),
        ("FORCE_STOPPED", "CANCELLED"),
        ("CANCEL_UNRESPONSIVE", "CANCELLED"),
        ("EXECUTION_ERROR", "FAILED"),
    ],
)
def test_web_consumer_uploads_terminal_summary_before_complete(
    process_kind: str, expected_outcome: str
) -> None:
    body = {
        "schema_version": 1,
        "message_id": "message-001",
        "run_id": "run-001",
        "runner_id": "runner-001",
        "run_type": "WEB_CASE",
        "required_slot_type": "WEB",
        "attempt": 1,
        "enqueued_at": "2026-09-01T10:00:00Z",
    }
    channel = _Channel()
    client = _WebClient(_plan())
    process = _FinishedProcess(IsolatedOutcome(process_kind))
    process_roots: list[str] = []

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        del plan
        process_roots.append(evidence_dir)
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=factory,
    )

    outcome = consumer.process_execution_delivery(12, json.dumps(body).encode())

    assert outcome is ConsumeOutcome.ACKED
    assert client.completed_outcomes == [expected_outcome]
    assert len(client.uploaded) == 1
    assert client.uploaded[0].metadata["status"] == expected_outcome
    assert client.calls[-2:] == ["upload", "complete"]
    assert channel.acks == [12]
    assert process_roots and not Path(process_roots[0]).exists()


@pytest.mark.parametrize(
    "upload_error",
    [
        ProtocolError("permanent response mismatch"),
        RunnerError("permanent local failure"),
        TransportError("bounded retries exhausted"),
    ],
)
def test_web_evidence_upload_failure_completes_failed_with_real_traces(
    upload_error: Exception,
) -> None:
    channel = _Channel()
    client = _WebClient(_plan(), upload_error=upload_error)
    process = _FinishedProcess(_successful_web_outcome())
    process_roots: list[str] = []
    state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        del plan
        process_roots.append(evidence_dir)
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        slot_state=state,
        web_isolation_factory=factory,
    )

    outcome = consumer.process_execution_delivery(20, _web_delivery_body())

    assert outcome is ConsumeOutcome.ACKED
    assert client.completed_outcomes == ["FAILED"]
    payload = client.completed_payloads[0]
    assert payload["error_type"] == "WEB_EVIDENCE_ERROR"
    assert payload["error_message"] == "Web 执行证据处理失败"
    assert [trace["status"] for trace in payload["traces"]] == [
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
        "SUCCESS",
    ]
    assert channel.acks == [20]
    assert channel.nacks == []
    assert channel.rejects == []
    assert process.closed is True
    assert state.available()["WEB"] == 1
    assert process_roots and not Path(process_roots[0]).exists()


def test_web_evidence_validation_failure_completes_failed_without_fabricating_step() -> None:
    channel = _Channel()
    client = _WebClient(_plan())
    process = _FinishedProcess(
        _successful_web_outcome(
            evidence=[
                {
                    "artifact_type": "SCREENSHOT",
                    "relative_path": "missing.png",
                    "metadata": {
                        "width": 1280,
                        "height": 720,
                        "title": "safe",
                        "page_url": "http://example.test/start",
                    },
                }
            ]
        )
    )
    process_roots: list[str] = []
    state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        del plan
        process_roots.append(evidence_dir)
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        slot_state=state,
        web_isolation_factory=factory,
    )

    outcome = consumer.process_execution_delivery(21, _web_delivery_body())

    assert outcome is ConsumeOutcome.ACKED
    payload = client.completed_payloads[0]
    assert payload["outcome"] == "FAILED"
    assert payload["error_type"] == "WEB_EVIDENCE_ERROR"
    assert all(trace["status"] == "SUCCESS" for trace in payload["traces"])
    assert channel.acks == [21]
    assert state.available()["WEB"] == 1
    assert process_roots and not Path(process_roots[0]).exists()


def test_web_evidence_failure_with_incomplete_trace_does_not_invent_completion() -> None:
    channel = _Channel()
    client = _WebClient(_plan())
    process = _FinishedProcess(
        IsolatedOutcome(
            "WEB_RESULT",
            {
                "outcome": "SUCCESS",
                "traces": [
                    {
                        "node_id": "action_1",
                        "status": "SUCCESS",
                        "duration_ms": 1,
                        "error_type": None,
                        "error_message": None,
                    }
                ],
                "error_type": None,
                "error_message": None,
                "evidence": [
                    {
                        "artifact_type": "SCREENSHOT",
                        "relative_path": "missing.png",
                        "metadata": {},
                    }
                ],
            },
        )
    )
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=lambda _plan: process,
    )

    outcome = consumer.process_execution_delivery(22, _web_delivery_body())

    assert outcome is ConsumeOutcome.REJECTED
    assert client.completed_payloads == []
    assert channel.rejects == [22]
    assert channel.acks == []


@pytest.mark.parametrize("process_kind", ["TOTAL_TIMEOUT", "EXECUTION_ERROR"])
def test_web_evidence_failure_without_real_child_trace_requeues_without_reexecution(
    process_kind: str,
) -> None:
    class RedeliveryClient(_WebClient):
        def __init__(self) -> None:
            super().__init__(
                _plan(), upload_error=TransportError("bounded retries exhausted")
            )
            self.claim_attempts = 0

        def claim(self, identity, run_id, message_id):
            self.claim_attempts += 1
            if self.claim_attempts > 1:
                del identity, run_id, message_id
                self.calls.append("claim")
                raise ProtocolError("HTTP 409 RUN_STATE_CONFLICT")
            return super().claim(identity, run_id, message_id)

    channel = _Channel()
    client = RedeliveryClient()
    process = _FinishedProcess(IsolatedOutcome(process_kind))
    process_roots: list[str] = []
    factory_calls = 0
    state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        nonlocal factory_calls
        del plan
        factory_calls += 1
        process_roots.append(evidence_dir)
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        slot_state=state,
        web_isolation_factory=factory,
    )

    first = consumer.process_execution_delivery(27, _web_delivery_body())
    assert first is ConsumeOutcome.REQUEUED
    assert client.completed_payloads == []
    assert channel.nacks == [27]
    assert process.closed is True
    assert state.available()["WEB"] == 1
    assert process_roots and not Path(process_roots[0]).exists()

    redelivered = consumer.process_execution_delivery(28, _web_delivery_body())
    assert redelivered is ConsumeOutcome.REJECTED
    assert channel.rejects == [28]
    assert factory_calls == 1
    assert client.completed_payloads == []
    assert state.available()["WEB"] == 1


@pytest.mark.parametrize(
    ("upload_error", "complete_error", "completion_attempted"),
    [
        (AuthenticationError("revoked"), None, False),
        (ProtocolError("bad evidence response"), AuthenticationError("revoked"), True),
    ],
)
def test_web_evidence_authentication_failure_stops_consuming(
    upload_error: Exception,
    complete_error: Exception | None,
    completion_attempted: bool,
) -> None:
    channel = _Channel()
    client = _WebClient(
        _plan(), upload_error=upload_error, complete_error=complete_error
    )
    process = _FinishedProcess(_successful_web_outcome())
    state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        slot_state=state,
        web_isolation_factory=lambda _plan: process,
    )

    outcome = consumer.process_execution_delivery(23, _web_delivery_body())

    assert outcome is ConsumeOutcome.STOPPED_AUTH
    assert bool(client.completed_payloads) is completion_attempted
    assert channel.rejects == [23]
    assert channel.acks == []
    assert channel.nacks == []
    assert channel.stop_calls == 1
    assert state.available()["WEB"] == 1
    assert process.closed is True


def test_web_evidence_failure_keeps_pending_cancel_as_terminal_priority() -> None:
    channel = _Channel()
    client = _WebClient(
        _plan(),
        cancel_after=2,
        upload_error=ProtocolError("bad evidence response"),
    )
    process = _FinishedProcess(_successful_web_outcome())
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        web_isolation_factory=lambda _plan: process,
    )

    outcome = consumer.process_execution_delivery(24, _web_delivery_body())

    assert outcome is ConsumeOutcome.ACKED
    assert client.completed_outcomes == ["CANCELLED"]
    assert client.completed_payloads[0]["error_type"] == "CANCEL_REQUESTED"
    assert channel.acks == [24]


def test_web_evidence_completion_response_loss_requeues_without_browser_restart() -> None:
    class RedeliveryClient(_WebClient):
        def __init__(self) -> None:
            super().__init__(
                _plan(),
                upload_error=TransportError("bounded retries exhausted"),
                complete_error=TransportError("completion response lost"),
            )
            self.claim_attempts = 0

        def claim(self, identity, run_id, message_id):
            self.claim_attempts += 1
            if self.claim_attempts > 1:
                del identity, run_id, message_id
                self.calls.append("claim")
                raise ProtocolError("HTTP 409 RUN_STATE_CONFLICT")
            return super().claim(identity, run_id, message_id)

    channel = _Channel()
    client = RedeliveryClient()
    process = _FinishedProcess(_successful_web_outcome())
    process_roots: list[str] = []
    factory_calls = 0
    state = SlotState({"API": 0, "WEB": 1, "PERFORMANCE": 0})

    def factory(plan: WebExecutionPlanResult, evidence_dir: str) -> _FinishedProcess:
        nonlocal factory_calls
        del plan
        factory_calls += 1
        process_roots.append(evidence_dir)
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        slot_state=state,
        web_isolation_factory=factory,
    )

    first = consumer.process_execution_delivery(25, _web_delivery_body())
    assert first is ConsumeOutcome.REQUEUED
    assert channel.nacks == [25]
    assert state.available()["WEB"] == 1
    assert process_roots and not Path(process_roots[0]).exists()

    redelivered = consumer.process_execution_delivery(26, _web_delivery_body())
    assert redelivered is ConsumeOutcome.REJECTED
    assert channel.rejects == [26]
    assert factory_calls == 1
    assert client.completed_outcomes == ["FAILED"]
    assert state.available()["WEB"] == 1


@pytest.mark.skipif(
    not DefaultSnapshotProbe().web_ready(), reason="Playwright 或本机 Chrome 不可用"
)
def test_web_executor_real_chrome_local_http_smoke() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            body = (
                b"<input id='name'><button id='submit' onclick=\"document.body.innerHTML += "
                b"'<div id=\\'result\\'>Hello Alice</div>'\">Submit</button>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/"
        plan = WebExecutionPlanResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            42,
            7,
            8,
            base_url,
            "CHROME",
            True,
            30_000,
            {"name": "Alice"},
            (
                WebExecutionActionPlan(
                    "FILL", 10_000, "STOP", None, _locator(("css", "#name")), "{{name}}", None
                ),
                WebExecutionActionPlan(
                    "CLICK", 10_000, "STOP", None, _locator(("css", "#submit")), None, None
                ),
            ),
            (
                WebExecutionAssertionPlan(
                    "ASSERT_VISIBLE", 10_000, _locator(("css", "#result")), None
                ),
                WebExecutionAssertionPlan(
                    "ASSERT_TEXT", 10_000, _locator(("css", "#result")), "Hello Alice"
                ),
                WebExecutionAssertionPlan("ASSERT_URL", 10_000, None, base_url),
            ),
            None,
        )
        with TemporaryDirectory() as evidence_dir:
            result = WebExecutor(evidence_dir=evidence_dir).execute(plan)
            assert result.outcome == "SUCCESS"
            assert [item["node_id"] for item in result.traces] == [
                "action_1",
                "action_2",
                "assertion_1",
                "assertion_2",
                "assertion_3",
            ]
            artifact_types = {
                item.artifact_type
                for item in validate_manifest(evidence_dir, result.evidence.to_wire())
            }
            assert {"SCREENSHOT", "WEB_SUMMARY", "PLAYWRIGHT_TRACE"} <= artifact_types
    finally:
        server.shutdown()
        server.server_close()
