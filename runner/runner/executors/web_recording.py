"""Bounded, headed Chrome recorder for the Web Recording control plane.

The recorder deliberately captures descriptors rather than input values or DOM.
It is normally run in the existing spawn child, so the parent can terminate a
stuck browser without sharing a Playwright object with the RabbitMQ thread.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from runner.errors import ExecutionError
from runner.executors.web import (
    WEB_VIEWPORT,
    _default_playwright_factory,
    find_system_chrome,
)
from runner.models import WebRecordingExecutionPlanResult

MAX_EVENTS = 1000
MAX_EVENT_STRING = 2048
MAX_RECORDING_SECONDS = 24 * 60 * 60
_SENSITIVE = re.compile(
    r"(?i)(authorization|cookie|token|password|passwd|secret|credential|api[_-]?key)"
)
_SAFE_KEYS = {
    "Enter",
    "Tab",
    "Escape",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
}
_STRATEGIES = ("test_id", "id", "name", "aria_label", "placeholder", "role", "text")


@dataclass(frozen=True, repr=False)
class WebRecordingExecutionResult:
    outcome: str
    events: list[dict[str, Any]]
    error_type: str | None = None
    error_message: str | None = None
    storage_state: Mapping[str, Any] | None = None

    def __repr__(self) -> str:
        return (
            "WebRecordingExecutionResult("
            f"outcome={self.outcome!r}, events={len(self.events)}, "
            f"error_type={self.error_type!r}, "
            f"error_message={'[REDACTED]' if self.error_message else None!r}, "
            f"storage_state={self.storage_state is not None})"
        )

    def to_wire(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "outcome": self.outcome,
            "events": [dict(event) for event in self.events],
            "error_type": self.error_type,
            "error_message": self.error_message,
        }
        if self.storage_state is not None:
            result["storage_state"] = dict(self.storage_state)
        return result


class WebRecordingExecutor:
    def __init__(
        self,
        *,
        playwright_factory: Callable[[], Any] | None = None,
        chrome_executable: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_recording_seconds: float = MAX_RECORDING_SECONDS,
    ) -> None:
        self._playwright_factory = playwright_factory or _default_playwright_factory
        self._chrome_executable = chrome_executable
        self._clock = clock
        self._sleeper = sleeper
        self._max_recording_seconds = max(1.0, min(max_recording_seconds, MAX_RECORDING_SECONDS))
        self._events: list[dict[str, Any]] = []
        self._started_at = 0.0

    def execute(
        self,
        plan: WebRecordingExecutionPlanResult,
        *,
        cancel_event: Any | None = None,
        stop_event: Any | None = None,
    ) -> WebRecordingExecutionResult:
        if not isinstance(plan, WebRecordingExecutionPlanResult):
            return self._failed("WEB_RECORDING_PLAN_INVALID", "Web 录制计划无效")
        if _is_set(cancel_event):
            return self._cancelled()
        if _is_set(stop_event):
            return self._completed()
        self._events = []
        self._started_at = self._clock()
        browser: Any | None = None
        context: Any | None = None
        try:
            with self._playwright_factory() as playwright:
                browser = self._launch_browser(playwright)
                try:
                    context = self._new_context(browser, plan)
                    page = context.new_page()
                    self._install_hooks(context, page)
                    try:
                        page.goto(plan.start_url)
                    except Exception:
                        return self._failed("TARGET_NETWORK_ERROR", "录制起始页面打开失败")
                    self._append_navigate(page, plan.start_url)
                    while True:
                        if _is_set(cancel_event):
                            return self._cancelled()
                        if _is_set(stop_event):
                            return self._completed_with_session(context, plan)
                        if self._page_closed(page, browser):
                            return self._completed_with_session(context, plan)
                        if self._clock() - self._started_at >= self._max_recording_seconds:
                            return self._failed("RECORDING_TIMEOUT", "录制等待超时")
                        self._sleeper(0.1)
                finally:
                    _safe_close(context)
            return self._completed()
        except ExecutionError as exc:
            return self._failed(exc.error_type or "WEB_RECORDING_ERROR", "Web 录制失败")
        except BaseException:  # noqa: BLE001 - browser boundary must be opaque
            return self._failed("WEB_RECORDING_ERROR", "Web 录制失败")
        finally:
            _safe_close(browser)

    def _launch_browser(self, playwright: Any) -> Any:
        chromium = getattr(playwright, "chromium", None)
        launch = getattr(chromium, "launch", None)
        if not callable(launch):
            raise ExecutionError("Chrome 浏览器不可用", error_type="WEB_BROWSER_UNAVAILABLE")
        kwargs: dict[str, Any] = {"headless": False}
        executable = self._chrome_executable or find_system_chrome()
        if executable:
            kwargs["executable_path"] = executable
        else:
            kwargs["channel"] = "chrome"
        try:
            return launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - do not expose browser details
            raise ExecutionError(
                "Chrome 浏览器启动失败", error_type="WEB_BROWSER_UNAVAILABLE"
            ) from exc

    @staticmethod
    def _new_context(browser: Any, plan: WebRecordingExecutionPlanResult) -> Any:
        new_context = getattr(browser, "new_context", None)
        if not callable(new_context):
            raise ExecutionError("Chrome 浏览器上下文不可用", error_type="WEB_BROWSER_UNAVAILABLE")
        kwargs: dict[str, Any] = {"viewport": dict(WEB_VIEWPORT)}
        if plan.session is not None:
            kwargs["storage_state"] = dict(plan.session.storage_state)
        try:
            return new_context(**kwargs)
        except TypeError:
            kwargs.pop("viewport", None)
            return new_context(**kwargs)

    def _install_hooks(self, context: Any, page: Any) -> None:
        expose = getattr(context, "expose_binding", None)
        if callable(expose):
            try:
                expose("__ai_test_record", self._binding_callback)
            except Exception:
                pass
        add_init_script = getattr(context, "add_init_script", None)
        if callable(add_init_script):
            try:
                add_init_script(script=_RECORDING_SCRIPT)
            except TypeError:
                try:
                    add_init_script(_RECORDING_SCRIPT)
                except Exception:
                    pass
            except Exception:
                pass
        on_page = getattr(context, "on", None)
        if callable(on_page):
            try:
                on_page("page", self._attach_page)
            except Exception:
                pass
        self._attach_page(page)

    def _attach_page(self, page: Any) -> None:
        on = getattr(page, "on", None)
        if callable(on):
            try:
                on("framenavigated", lambda *_args: self._append_navigate(page, _page_url(page)))
            except Exception:
                pass

    def _binding_callback(self, _source: Any, payload: object) -> None:
        if isinstance(payload, Mapping):
            self.record_browser_event(payload)

    def record_browser_event(self, payload: Mapping[str, Any]) -> None:
        """Convert a browser descriptor into one safe, protocol-shaped event."""

        event_type = payload.get("event_type")
        page_url = _safe_event_url(payload.get("page_url"))
        title = _safe_title(payload.get("title"))
        locators = _build_locator_candidates(payload)
        if event_type in {"CLICK", "FILL", "SELECT", "PRESS"} and not locators:
            return
        if event_type == "CLICK":
            self._append("CLICK", page_url, title, locator_candidates=locators)
        elif event_type == "FILL":
            sensitive = (
                payload.get("sensitive") is True
                or _SENSITIVE.search(str(payload.get("name", ""))) is not None
            )
            value = "REDACTED" if sensitive else _placeholder(payload)
            self._append("FILL", page_url, title, locator_candidates=locators, value=value)
        elif event_type == "SELECT":
            selected = payload.get("value")
            safe_value = (
                "REDACTED"
                if not isinstance(selected, str)
                or len(selected) > 128
                or _SENSITIVE.search(str(payload.get("name", "")))
                or _SENSITIVE.search(selected)
                else selected
            )
            self._append("SELECT", page_url, title, locator_candidates=locators, value=safe_value)
        elif event_type == "PRESS" and payload.get("key") in _SAFE_KEYS:
            self._append("PRESS", page_url, title, locator_candidates=locators, key=payload["key"])

    def _append_navigate(self, page: Any, url: object) -> None:
        safe_url = _safe_event_url(url)
        if safe_url is None:
            return
        if (
            self._events
            and self._events[-1]["event_type"] == "NAVIGATE"
            and self._events[-1]["target_url"] == safe_url
        ):
            return
        self._append(
            "NAVIGATE",
            _safe_event_url(_page_url(page)),
            _safe_title(_page_title(page)),
            target_url=safe_url,
        )

    def _append(
        self,
        event_type: str,
        page_url: str | None,
        title: str | None,
        *,
        target_url: str | None = None,
        locator_candidates: list[dict[str, Any]] | None = None,
        value: str | None = None,
        key: str | None = None,
    ) -> None:
        if len(self._events) >= MAX_EVENTS:
            return
        elapsed = max(0, min(86_400_000, int((self._clock() - self._started_at) * 1000)))
        if (
            event_type == "FILL"
            and self._events
            and self._events[-1]["event_type"] == "FILL"
            and self._events[-1]["page_url"] == page_url
            and self._events[-1]["locator_candidates"] == list(locator_candidates or [])
        ):
            return
        self._events.append(
            {
                "event_type": event_type,
                "sequence": len(self._events) + 1,
                "relative_time_ms": elapsed,
                "page_url": page_url,
                "title": title,
                "target_url": target_url,
                "locator_candidates": list(locator_candidates or []),
                "value": value,
                "key": key,
            }
        )

    def _completed(self) -> WebRecordingExecutionResult:
        return WebRecordingExecutionResult("COMPLETED", list(self._events))

    def _completed_with_session(
        self, context: Any, plan: WebRecordingExecutionPlanResult
    ) -> WebRecordingExecutionResult:
        if not plan.save_session:
            return self._completed()
        try:
            state = context.storage_state()
            if not isinstance(state, Mapping):
                return self._failed("STORAGE_STATE_INVALID", "录制 Session 保存失败")
            encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 1_000_000:
                return self._failed("STORAGE_STATE_TOO_LARGE", "录制 Session 保存失败")
            return WebRecordingExecutionResult(
                "COMPLETED", list(self._events), storage_state=dict(state)
            )
        except Exception:
            return self._failed("STORAGE_STATE_ERROR", "录制 Session 保存失败")

    def _cancelled(self) -> WebRecordingExecutionResult:
        return WebRecordingExecutionResult("CANCELLED", [], "CANCEL_REQUESTED", "录制已取消")

    @staticmethod
    def _failed(error_type: str, message: str) -> WebRecordingExecutionResult:
        return WebRecordingExecutionResult("FAILED", [], error_type[:100], message[:1000])

    @staticmethod
    def _page_closed(page: Any, browser: Any) -> bool:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed):
            try:
                if is_closed():
                    return True
            except Exception:
                return True
        connected = getattr(browser, "is_connected", None)
        if callable(connected):
            try:
                return not connected()
            except Exception:
                return True
        return False


def _build_locator_candidates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    values = {
        "test_id": payload.get("test_id"),
        "id": payload.get("id"),
        "name": payload.get("name"),
        "aria_label": payload.get("aria_label"),
        "placeholder": payload.get("placeholder"),
        "role": payload.get("role"),
        "text": payload.get("text"),
    }
    for strategy in _STRATEGIES:
        value = values.get(strategy)
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_EVENT_STRING:
            continue
        if "\r" in value or "\n" in value or _SENSITIVE.search(value) or value in seen:
            continue
        seen.add(value)
        wire_strategy = (
            "test_id" if strategy == "test_id" else strategy.replace("aria_label", "label")
        )
        if strategy == "id":
            wire_value = f'[id="{_css_attribute_value(value)}"]'
            wire_strategy = "css"
        elif strategy == "name":
            wire_value = f'[name="{_css_attribute_value(value)}"]'
            wire_strategy = "css"
        else:
            wire_value = value
        candidates.append(
            {
                "strategy": wire_strategy,
                "value": wire_value[:2000],
                "priority": len(candidates) + 1,
            }
        )
        if len(candidates) == 10:
            break
    return candidates


def _css_attribute_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _placeholder(payload: Mapping[str, Any]) -> str:
    raw = (
        payload.get("name")
        or payload.get("id")
        or payload.get("aria_label")
        or payload.get("placeholder")
    )
    if not isinstance(raw, str) or not raw or _SENSITIVE.search(raw):
        return "{{recording.field_1}}"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_.-") or "field_1"
    if not name[0].isalpha():
        name = f"field_{name}"
    return "{{recording." + name[:100] + "}}"


def _safe_event_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return None
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE.search(name):
            return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_title(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 512 or "\n" in value or "\r" in value:
        return None
    return "<redacted>" if _SENSITIVE.search(value) else value


def _page_url(page: Any) -> object:
    value = getattr(page, "url", None)
    return value() if callable(value) else value


def _page_title(page: Any) -> object:
    title = getattr(page, "title", None)
    if not callable(title):
        return None
    try:
        return title()
    except Exception:
        return None


def _is_set(event: Any) -> bool:
    return bool(event is not None and getattr(event, "is_set", lambda: False)())


def _safe_close(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


_RECORDING_SCRIPT = r"""
(() => {
  const send = (event_type, el, extra = {}) => {
    if (!window.__ai_test_record || !el) return;
    const attr = (name) => (el.getAttribute && el.getAttribute(name)) || null;
    const text = (el.innerText || el.textContent || '').slice(0, 200);
    window.__ai_test_record({event_type, page_url: location.href, title: document.title,
      id: attr('id'), name: attr('name'), test_id: attr('data-testid'),
      aria_label: attr('aria-label'), placeholder: attr('placeholder'),
      role: attr('role'), text, sensitive: (el.type || '').toLowerCase() === 'password', ...extra});
  };
  document.addEventListener('click', e => send('CLICK', e.target), true);
  document.addEventListener('input', e => {
    const el = e.target;
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.type || '').toLowerCase();
    if (tag === 'textarea' || (tag === 'input' &&
        !['checkbox','radio','file','hidden','button','submit','reset'].includes(type)) ||
        el.isContentEditable) {
      send('FILL', el);
    }
  }, true);
  document.addEventListener('change', e => {
    if ((e.target.tagName || '').toLowerCase() === 'select') {
      send('SELECT', e.target, {value: e.target.value});
    }
  }, true);
  document.addEventListener('keydown', e => send('PRESS', e.target, {key: e.key}), true);
})();
"""
