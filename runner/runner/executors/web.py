"""Bounded Playwright Web_CASE executor.

The browser is intentionally created by the isolation child.  This module keeps
the execution result to safe trace metadata; page content, cookies, storage
state and request values are never part of a trace or exception message.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from runner.errors import ExecutionError, ProtocolError, TargetTimeoutError
from runner.evidence import (
    EvidenceManifestItem,
    WebEvidenceManifest,
    canonical_json,
    safe_url,
    sanitize_trace_file,
)
from runner.models import (
    WebExecutionActionPlan,
    WebExecutionAssertionPlan,
    WebExecutionPlanResult,
    WebLocatorCandidate,
    WebSessionConditionPlan,
)
from runner.redaction import redact_text

MAX_WEB_TRACE_COUNT = 300
MAX_WEB_ERROR_LENGTH = 500
MAX_HEALING_CONTEXT_BYTES = 64 * 1024
MAX_HEALING_CANDIDATES = 40
MAX_HEALING_VALUE_LENGTH = 200
MAX_OBSERVED_ASSERTION_VALUES = 100
MAX_OBSERVED_ASSERTION_VALUE_LENGTH = 10_000
WEB_VIEWPORT = {"width": 1280, "height": 720}
WEB_LOCATOR_PROBE_TIMEOUT_MS = 250
WEB_TIMEOUT_CLASSIFICATION_TOLERANCE_SECONDS = 0.01
MAX_MANAGED_WEB_TABS = 16
_TEMPLATE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_WEB_TAB_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SENSITIVE_NAME = re.compile(
    r"(?i)(authorization|cookie|token|password|passwd|secret|credential|api[_-]?key)"
)
_SENSITIVE_HEALING_TEXT = re.compile(
    r"(?i)(authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)"
)
_OBSERVED_ASSERTION_OVERFLOW = "\x00[ASSERTION_VALUE_OVERFLOW]\x00"


@dataclass(frozen=True, repr=False)
class WebExecutionResult:
    outcome: str
    traces: list[dict[str, Any]]
    error_type: str | None = None
    error_message: str | None = None
    evidence: WebEvidenceManifest = WebEvidenceManifest()
    refreshed_session: Mapping[str, Any] | None = None
    session_recovery: Mapping[str, Any] | None = None
    pending_ai: tuple[Mapping[str, Any], ...] = ()

    def __repr__(self) -> str:
        return (
            "WebExecutionResult("
            f"outcome={self.outcome!r}, traces={len(self.traces)}, "
            f"error_type={self.error_type!r}, "
            f"error_message={'[REDACTED]' if self.error_message else None!r}, "
            f"evidence={self.evidence!r}, refreshed_session={self.refreshed_session is not None}, "
            f"session_recovery={self.session_recovery is not None}, "
            f"pending_ai={len(self.pending_ai)})"
        )

    def to_wire(self) -> dict[str, Any]:
        payload = {
            "outcome": self.outcome,
            "traces": [dict(item) for item in self.traces],
            "error_type": self.error_type,
            "error_message": self.error_message,
            "evidence": self.evidence.to_wire(),
        }
        if self.refreshed_session is not None:
            payload["refreshed_session"] = dict(self.refreshed_session)
        if self.session_recovery is not None:
            payload["session_recovery"] = dict(self.session_recovery)
        if self.pending_ai:
            payload["pending_ai"] = [dict(item) for item in self.pending_ai]
        return payload


class WebExecutionError(ExecutionError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        timeout: bool = False,
        locator_attempts: list[dict[str, Any]] | None = None,
        healing_context: Mapping[str, Any] | None = None,
    ) -> None:
        self.timeout = timeout
        self.locator_attempts = [dict(item) for item in (locator_attempts or [])[:20]]
        self.healing_context = (
            {str(key): value for key, value in healing_context.items()}
            if isinstance(healing_context, Mapping)
            else None
        )
        super().__init__(message[:MAX_WEB_ERROR_LENGTH], error_type=error_type[:100])


class _ManagedTabs:
    """Track only pages explicitly created by this execution plan."""

    def __init__(
        self,
        main_page: Any,
        *,
        browser_context: Any | None = None,
        on_new_page: Callable[[Any], None] | None = None,
    ) -> None:
        self._browser_context = browser_context or getattr(main_page, "context", None)
        self._on_new_page = on_new_page
        self._pages: dict[str, Any] = {"main": main_page}
        self._current_alias: str | None = "main"
        self._activation_order = ["main"]

    @property
    def current_alias(self) -> str:
        self._prune_closed()
        if self._current_alias is None:
            raise WebExecutionError("WEB_TAB_NOT_FOUND", "Web 标签页不存在或已关闭")
        return self._current_alias

    @property
    def current_page(self) -> Any:
        return self.target(self.current_alias)

    @property
    def alive_count(self) -> int:
        self._prune_closed()
        return len(self._pages)

    @property
    def aliases(self) -> tuple[str, ...]:
        self._prune_closed()
        return tuple(alias for alias in self._activation_order if alias in self._pages)

    @property
    def pages(self) -> tuple[Any, ...]:
        self._prune_closed()
        return tuple(self._pages[alias] for alias in self.aliases)

    def evidence_page(self, fallback: Any) -> Any:
        try:
            return self.current_page
        except WebExecutionError:
            return fallback

    def create(self, alias: str) -> Any:
        self._prune_closed()
        if not _WEB_TAB_ALIAS.fullmatch(alias) or alias == "main":
            raise WebExecutionError("WEB_TAB_INVALID", "Web 标签别名无效")
        if alias in self._pages:
            raise WebExecutionError("WEB_TAB_EXISTS", "Web 标签别名已存在")
        if len(self._pages) >= MAX_MANAGED_WEB_TABS:
            raise WebExecutionError("WEB_TAB_LIMIT", "Web 受管标签页数量超限")
        new_page = getattr(self._browser_context, "new_page", None)
        if not callable(new_page):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Chrome 浏览器上下文不可用")
        try:
            page = new_page()
        except Exception as exc:  # noqa: BLE001 - browser details stay private
            raise WebExecutionError("WEB_ACTION_FAILED", "Web 节点执行失败") from exc
        self._pages[alias] = page
        self.activate(alias)
        if self._on_new_page is not None:
            try:
                self._on_new_page(page)
            except Exception:  # noqa: BLE001 - evidence binding is best effort
                pass
        return page

    def target(self, alias: str) -> Any:
        self._prune_closed()
        page = self._pages.get(alias)
        if page is None:
            raise WebExecutionError("WEB_TAB_NOT_FOUND", "Web 标签页不存在或已关闭")
        return page

    def activate(self, alias: str) -> None:
        if alias not in self._pages:
            raise WebExecutionError("WEB_TAB_NOT_FOUND", "Web 标签页不存在或已关闭")
        self._current_alias = alias
        if alias in self._activation_order:
            self._activation_order.remove(alias)
        self._activation_order.append(alias)

    def require_closable(self, alias: str) -> Any:
        page = self.target(alias)
        if len(self._pages) <= 1:
            raise WebExecutionError("WEB_LAST_TAB", "Web 不能关闭最后一个标签页")
        return page

    def forget(self, alias: str) -> None:
        was_current = alias == self._current_alias
        self._pages.pop(alias, None)
        if alias in self._activation_order:
            self._activation_order.remove(alias)
        if was_current:
            self._current_alias = None
            self._select_fallback()

    @staticmethod
    def page_is_closed(page: Any) -> bool:
        is_closed = getattr(page, "is_closed", None)
        if not callable(is_closed):
            return False
        try:
            return bool(is_closed())
        except Exception:  # noqa: BLE001 - keep state until a browser operation proves failure
            return False

    def _prune_closed(self) -> None:
        for alias, page in tuple(self._pages.items()):
            if self.page_is_closed(page):
                self._pages.pop(alias, None)
                if alias in self._activation_order:
                    self._activation_order.remove(alias)
        if self._current_alias not in self._pages:
            self._current_alias = None
            self._select_fallback()

    def _select_fallback(self) -> None:
        if "main" in self._pages:
            self._current_alias = "main"
            return
        for alias in reversed(self._activation_order):
            if alias in self._pages:
                self._current_alias = alias
                return


class WebExecutor:
    """Execute one fixed Web plan with deterministic action/locator handling."""

    def __init__(
        self,
        *,
        playwright_factory: Callable[[], Any] | None = None,
        chrome_executable: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        evidence_dir: str | Path | None = None,
    ) -> None:
        self._playwright_factory = playwright_factory or _default_playwright_factory
        self._chrome_executable = chrome_executable
        self._clock = clock
        self._evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        self._download_names: list[str] = []
        self._request_urls: list[str] = []
        self._pending_ai: list[dict[str, Any]] = []

    def execute(
        self,
        plan: WebExecutionPlanResult,
        *,
        stop_event: Any | None = None,
        deadline: float | None = None,
    ) -> WebExecutionResult:
        if not isinstance(plan, WebExecutionPlanResult):
            return self._failed("WEB_PLAN_INVALID", "Web 执行计划无效")
        self._download_names = []
        self._request_urls = []
        self._pending_ai = []
        effective_deadline = deadline
        if effective_deadline is None:
            effective_deadline = self._clock() + plan.total_timeout_ms / 1000.0
        if self._cancelled(stop_event):
            return self._cancelled_result()
        try:
            with self._playwright_factory() as playwright:
                browser = self._launch_browser(playwright, plan)
                try:
                    context = self._new_context(browser, plan)
                    try:
                        page = context.new_page()
                        observed_assertion_values: list[str] = []
                        evidence = _WebEvidenceCollector(
                            self._evidence_dir,
                            plan,
                            clock=self._clock,
                            observed_assertion_values=observed_assertion_values,
                        )
                        evidence.start(context, page)
                        self._bind_request_tracking(page)

                        def bind_new_page(new_page: Any) -> None:
                            evidence._bind_page(new_page)
                            self._bind_request_tracking(new_page)

                        managed_tabs = _ManagedTabs(
                            page,
                            browser_context=context,
                            on_new_page=bind_new_page,
                        )
                        try:
                            result = self._execute_with_session_recovery(
                                context,
                                page,
                                plan,
                                stop_event=stop_event,
                                deadline=effective_deadline,
                                observed_assertion_values=observed_assertion_values,
                                managed_tabs=managed_tabs,
                            )
                            result = replace(result, pending_ai=tuple(self._pending_ai))
                        except Exception:  # noqa: BLE001 - safe browser execution boundary
                            result = WebExecutionResult(
                                "FAILED", [], "WEB_EXECUTOR_ERROR", "Web 执行失败"
                            )
                        try:
                            manifest = evidence.finish(
                                context,
                                managed_tabs.evidence_page(page),
                                result,
                                page_count=managed_tabs.alive_count,
                            )
                        except Exception:  # noqa: BLE001 - evidence never overrides execution
                            result = WebExecutionResult(
                                "FAILED", [], "WEB_EXECUTOR_ERROR", "Web 执行失败"
                            )
                            try:
                                manifest = evidence.abort(
                                    context,
                                    managed_tabs.evidence_page(page),
                                    result,
                                    page_count=managed_tabs.alive_count,
                                )
                            except Exception:  # noqa: BLE001 - evidence remains fail-closed
                                manifest = WebEvidenceManifest()
                        return replace(result, evidence=manifest)
                    finally:
                        _safe_close(context)
                finally:
                    _safe_close(browser)
        except WebExecutionError as exc:
            return self._failed(exc.error_type or "WEB_EXECUTION_ERROR", str(exc), exc.timeout)
        except TargetTimeoutError:
            return self._failed("TARGET_TIMEOUT", "Web 目标执行超时", timeout=True)
        except Exception:  # noqa: BLE001 - browser boundary must not expose details
            return self._failed("WEB_EXECUTOR_ERROR", "Web 执行失败")

    def close(self) -> None:
        """Compatibility hook; browser resources belong to one execute call."""

    def _launch_browser(self, playwright: Any, plan: WebExecutionPlanResult) -> Any:
        chromium = getattr(playwright, "chromium", None)
        launch = getattr(chromium, "launch", None)
        if not callable(launch):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Chrome 浏览器不可用")
        config = plan.browser_config
        kwargs: dict[str, Any] = {
            "headless": plan.headless,
            "args": [f"--window-size={config.window_width},{config.window_height}"],
        }
        runtime_context = _web_execution_context(plan)
        if config.proxy is not None:
            proxy: dict[str, str] = {"server": config.proxy.server}
            if config.proxy.username is not None and config.proxy.password is not None:
                username = _resolve_template(config.proxy.username, runtime_context)
                password = _resolve_template(config.proxy.password, runtime_context)
                if not isinstance(username, str) or not isinstance(password, str):
                    raise ProtocolError("Web Proxy 认证信息无效")
                proxy["username"] = username
                proxy["password"] = password
            kwargs["proxy"] = proxy
        if config.download_path is not None:
            if self._evidence_dir is None:
                raise WebExecutionError("WEB_DOWNLOAD_PATH_UNAVAILABLE", "Web 下载目录不可用")
            root = self._evidence_dir.resolve()
            target = (root / config.download_path).resolve()
            try:
                target.relative_to(root)
                target.mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as exc:
                raise WebExecutionError(
                    "WEB_DOWNLOAD_PATH_UNAVAILABLE", "Web 下载目录不可用"
                ) from exc
            kwargs["downloads_path"] = str(target)
        executable = self._chrome_executable or find_system_chrome()
        if executable:
            kwargs["executable_path"] = executable
        else:
            kwargs["channel"] = "chrome"
        try:
            return launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - do not expose browser details
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Chrome 浏览器启动失败") from exc

    @staticmethod
    def _new_context(browser: Any, plan: WebExecutionPlanResult) -> Any:
        new_context = getattr(browser, "new_context", None)
        if not callable(new_context):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Chrome 浏览器上下文不可用")
        config = plan.browser_config
        kwargs: dict[str, Any] = {
            "viewport": {
                "width": config.window_width,
                "height": config.window_height,
            }
        }
        if config.language is not None:
            kwargs["locale"] = config.language
        if config.user_agent is not None:
            kwargs["user_agent"] = config.user_agent
        if plan.session is not None and plan.session.storage_state is not None:
            kwargs["storage_state"] = dict(plan.session.storage_state)
        try:
            return new_context(**kwargs)
        except TypeError:
            # Small fake contexts may expose only storage_state.
            storage_state = kwargs.get("storage_state")
            return new_context(storage_state=storage_state)

    def _execute_with_session_recovery(
        self,
        browser_context: Any,
        page: Any,
        plan: WebExecutionPlanResult,
        *,
        stop_event: Any | None,
        deadline: float,
        observed_assertion_values: list[str],
        managed_tabs: _ManagedTabs,
    ) -> WebExecutionResult:
        session = plan.session
        recovery = session.recovery if session is not None else None
        if session is None or recovery is None:
            return self._execute_page(
                page,
                plan,
                stop_event=stop_event,
                deadline=deadline,
                observed_assertion_values=observed_assertion_values,
                managed_tabs=managed_tabs,
            )
        started = self._clock()
        reason = "PROFILE_EXPIRED" if recovery.force_refresh else "NOT_EXPIRED"
        try:
            needed = recovery.force_refresh
            if not needed:
                context = _web_execution_context(plan)
                start_url = _resolve_template(plan.start_url, context)
                _validate_browser_url(start_url, "start_url")
                self._check_deadline(stop_event, deadline)
                self._invoke_page(
                    managed_tabs.current_page,
                    "goto",
                    start_url,
                    timeout=self._remaining_timeout(deadline),
                )
                needed = self._session_condition_matches(
                    managed_tabs.current_page,
                    recovery.expiry_condition,
                    context,
                    deadline,
                )
                if needed:
                    reason = "EXPIRY_CONDITION"
            if not needed:
                result = self._execute_page(
                    managed_tabs.current_page,
                    plan,
                    stop_event=stop_event,
                    deadline=deadline,
                    observed_assertion_values=observed_assertion_values,
                    managed_tabs=managed_tabs,
                )
                return replace(
                    result,
                    session_recovery={
                        "status": "NOT_NEEDED",
                        "reason": reason,
                        "login_web_case_version_id": recovery.login_web_case_version_id,
                        "duration_ms": _elapsed_ms(self._clock(), started),
                        "error_type": None,
                        "persistence_status": None,
                    },
                )
            login_plan = replace(
                plan,
                web_case_id=recovery.login_web_case_id,
                web_case_version_id=recovery.login_web_case_version_id,
                start_url=recovery.start_url,
                actions=recovery.actions,
                assertions=recovery.assertions,
                session=None,
            )
            login_result = self._execute_page(
                managed_tabs.current_page,
                login_plan,
                stop_event=stop_event,
                deadline=deadline,
                observed_assertion_values=observed_assertion_values,
                managed_tabs=managed_tabs,
            )
            if login_result.outcome == "CANCELLED":
                return login_result
            if login_result.outcome != "SUCCESS" or not self._session_condition_matches(
                managed_tabs.current_page,
                recovery.success_condition,
                _web_execution_context(plan),
                deadline,
            ):
                raise WebExecutionError("WEB_SESSION_RECOVERY_FAILED", "Web 登录态恢复失败")
            state_method = getattr(browser_context, "storage_state", None)
            if not callable(state_method):
                raise WebExecutionError("WEB_SESSION_RECOVERY_FAILED", "Web 登录态恢复失败")
            refreshed_state = state_method()
            if not isinstance(refreshed_state, Mapping) or not refreshed_state:
                raise WebExecutionError("WEB_SESSION_RECOVERY_FAILED", "Web 登录态恢复失败")
            result = self._execute_page(
                managed_tabs.current_page,
                plan,
                stop_event=stop_event,
                deadline=deadline,
                observed_assertion_values=observed_assertion_values,
                managed_tabs=managed_tabs,
            )
            return replace(
                result,
                refreshed_session={
                    "profile_id": session.profile_id,
                    "expected_revision": session.revision,
                    "expected_fingerprint": session.storage_state_fingerprint,
                    "storage_state": dict(refreshed_state),
                },
                session_recovery={
                    "status": "SUCCESS",
                    "reason": reason,
                    "login_web_case_version_id": recovery.login_web_case_version_id,
                    "duration_ms": _elapsed_ms(self._clock(), started),
                    "error_type": None,
                    "persistence_status": None,
                },
            )
        except WebExecutionError as exc:
            if exc.error_type == "CANCEL_REQUESTED":
                return self._cancelled_result()
            traces: list[dict[str, Any]] = []
            self._skip_trace_ids(traces, plan)
            return WebExecutionResult(
                "TIMEOUT" if exc.timeout else "FAILED",
                traces,
                exc.error_type or "WEB_SESSION_RECOVERY_FAILED",
                str(exc),
                session_recovery={
                    "status": "FAILED",
                    "reason": reason,
                    "login_web_case_version_id": recovery.login_web_case_version_id,
                    "duration_ms": _elapsed_ms(self._clock(), started),
                    "error_type": exc.error_type or "WEB_SESSION_RECOVERY_FAILED",
                    "persistence_status": None,
                },
            )

    def _session_condition_matches(
        self,
        page: Any,
        condition: WebSessionConditionPlan,
        context: Mapping[str, Any],
        deadline: float,
    ) -> bool:
        self._check_deadline(None, deadline)
        if condition.type in {"URL_EQUALS", "URL_CONTAINS"}:
            expected = _resolve_template(condition.value, context)
            actual = _page_url(page)
            if not isinstance(expected, str):
                raise WebExecutionError("WEB_SESSION_RECOVERY_FAILED", "Web 登录态恢复失败")
            return actual == expected if condition.type == "URL_EQUALS" else expected in actual
        if condition.locator is None:
            raise WebExecutionError("WEB_SESSION_RECOVERY_FAILED", "Web 登录态恢复失败")
        visible = False
        for candidate in _ordered_candidates(condition.locator.candidates)[:20]:
            try:
                locator = self._make_locator(page, candidate, context)
                visible = (
                    self._invoke_locator(
                        locator,
                        "is_visible",
                        timeout=min(
                            WEB_LOCATOR_PROBE_TIMEOUT_MS, self._remaining_timeout(deadline)
                        ),
                    )
                    is True
                )
            except Exception:  # noqa: BLE001 - a missing candidate is a false probe
                visible = False
            if visible:
                break
        return visible if condition.type == "LOCATOR_VISIBLE" else not visible

    def _execute_page(
        self,
        page: Any,
        plan: WebExecutionPlanResult,
        *,
        stop_event: Any | None,
        deadline: float,
        observed_assertion_values: list[str] | None = None,
        managed_tabs: _ManagedTabs | None = None,
    ) -> WebExecutionResult:
        context = _web_execution_context(plan)
        tabs = managed_tabs or _ManagedTabs(page)
        if observed_assertion_values is None:
            observed_assertion_values = []
        planned_sensitive_values = _web_sensitive_values(plan)
        traces: list[dict[str, Any]] = []
        failed = False
        try:
            start_url = _resolve_template(plan.start_url, context)
            _validate_browser_url(start_url, "start_url")
            self._check_deadline(stop_event, deadline)
            self._invoke_page(
                tabs.current_page,
                "goto",
                start_url,
                timeout=self._remaining_timeout(deadline),
            )
        except WebExecutionError as exc:
            if exc.error_type == "CANCEL_REQUESTED":
                return self._cancelled_result()
            exc = self._total_timeout_if_expired(
                exc, run_deadline=deadline, run_budget_limited=True
            )
            status = "TIMEOUT" if exc.timeout else "FAILED"
            self._record_trace(traces, "action_1", status, 0, exc)
            self._skip_trace_ids(traces, plan)
            return self._result_for_status(status, traces, exc)
        except Exception as exc:  # noqa: BLE001 - safe browser boundary
            error = self._browser_error(exc)
            error = self._total_timeout_if_expired(
                error, run_deadline=deadline, run_budget_limited=True
            )
            self._record_trace(
                traces, "action_1", "TIMEOUT" if error.timeout else "FAILED", 0, error
            )
            self._skip_trace_ids(traces, plan, skip_from=2)
            return self._result_for_status("TIMEOUT" if error.timeout else "FAILED", traces, error)

        for index, action in enumerate(plan.actions, start=1):
            if any(item["node_id"] == f"action_{index}" for item in traces):
                continue
            if self._cancelled(stop_event):
                return self._cancelled_result()
            started = self._clock()
            run_budget_limited = deadline <= started + action.timeout_ms / 1000.0
            locator_attempts: list[dict[str, Any]] = []
            try:
                self._check_deadline(stop_event, deadline)
                node_deadline = min(deadline, started + action.timeout_ms / 1000.0)
                locator_attempts = self._perform_action(
                    tabs.current_page,
                    action,
                    context,
                    node_deadline,
                    run_deadline=deadline,
                    planned_sensitive_values=planned_sensitive_values,
                    observed_assertion_values=observed_assertion_values,
                    managed_tabs=tabs,
                )
                duration = _elapsed_ms(self._clock(), started)
                if duration > action.timeout_ms:
                    raise WebExecutionError(
                        "WEB_NODE_TIMEOUT",
                        "Web 节点执行超时",
                        timeout=True,
                        locator_attempts=locator_attempts,
                    )
                self._record_trace(
                    traces,
                    f"action_{index}",
                    "SUCCESS",
                    duration,
                    None,
                    locator_attempts=locator_attempts,
                )
            except WebExecutionError as exc:
                duration = _elapsed_ms(self._clock(), started)
                locator_attempts = _error_locator_attempts(exc, locator_attempts)
                if exc.error_type == "CANCEL_REQUESTED":
                    return self._cancelled_result()
                exc = self._total_timeout_if_expired(
                    exc,
                    run_deadline=deadline,
                    run_budget_limited=run_budget_limited,
                    locator_attempts=locator_attempts,
                )
                if (
                    duration > action.timeout_ms
                    and not exc.timeout
                    and not _is_locator_healing_failure(exc)
                ):
                    exc = WebExecutionError(
                        "WEB_NODE_TIMEOUT",
                        "Web 节点执行超时",
                        timeout=True,
                        locator_attempts=locator_attempts,
                    )
                status = "TIMEOUT" if exc.timeout else "FAILED"
                self._record_trace(
                    traces,
                    f"action_{index}",
                    status,
                    duration,
                    exc,
                    locator_attempts=locator_attempts,
                )
                failed = True
                if action.failure_policy == "STOP" or exc.timeout:
                    self._skip_trace_ids(traces, plan, skip_from=index + 1)
                    return self._result_for_status(status, traces, exc)
            except Exception as exc:  # noqa: BLE001 - browser boundary
                error = self._browser_error(exc)
                error = self._total_timeout_if_expired(
                    error,
                    run_deadline=deadline,
                    run_budget_limited=run_budget_limited,
                    locator_attempts=locator_attempts,
                )
                duration = _elapsed_ms(self._clock(), started)
                if duration > action.timeout_ms and not error.timeout:
                    error = WebExecutionError("WEB_NODE_TIMEOUT", "Web 节点执行超时", timeout=True)
                status = "TIMEOUT" if error.timeout else "FAILED"
                self._record_trace(
                    traces,
                    f"action_{index}",
                    status,
                    duration,
                    error,
                    locator_attempts=locator_attempts,
                )
                failed = True
                if action.failure_policy == "STOP" or error.timeout:
                    self._skip_trace_ids(traces, plan, skip_from=index + 1)
                    return self._result_for_status(status, traces, error)

        for index, assertion in enumerate(plan.assertions, start=1):
            if self._cancelled(stop_event):
                return self._cancelled_result()
            started = self._clock()
            run_budget_limited = deadline <= started + assertion.timeout_ms / 1000.0
            locator_attempts = []
            try:
                self._check_deadline(stop_event, deadline)
                node_deadline = min(deadline, started + assertion.timeout_ms / 1000.0)
                if assertion.type == "ASSERT_AI_SEMANTIC":
                    self._pending_ai.append(
                        {
                            "node_id": f"assertion_{index}",
                            "response": self._semantic_snapshot(
                                tabs.current_page,
                                planned_sensitive_values,
                                timeout=self._remaining_timeout(node_deadline),
                            ),
                        }
                    )
                else:
                    locator_attempts = self._perform_assertion(
                        tabs.current_page,
                        assertion,
                        context,
                        node_deadline,
                        run_deadline=deadline,
                        observed_assertion_values=observed_assertion_values,
                        planned_sensitive_values=planned_sensitive_values,
                    )
                duration = _elapsed_ms(self._clock(), started)
                if duration > assertion.timeout_ms:
                    raise WebExecutionError(
                        "WEB_NODE_TIMEOUT",
                        "Web 节点执行超时",
                        timeout=True,
                        locator_attempts=locator_attempts,
                    )
                self._record_trace(
                    traces,
                    f"assertion_{index}",
                    "SUCCESS",
                    duration,
                    None,
                    locator_attempts=locator_attempts,
                )
            except WebExecutionError as exc:
                duration = _elapsed_ms(self._clock(), started)
                locator_attempts = _error_locator_attempts(exc, locator_attempts)
                if exc.error_type == "CANCEL_REQUESTED":
                    return self._cancelled_result()
                exc = self._total_timeout_if_expired(
                    exc,
                    run_deadline=deadline,
                    run_budget_limited=run_budget_limited,
                    locator_attempts=locator_attempts,
                )
                if (
                    duration > assertion.timeout_ms
                    and not exc.timeout
                    and not _is_locator_healing_failure(exc)
                ):
                    exc = WebExecutionError(
                        "WEB_NODE_TIMEOUT",
                        "Web 节点执行超时",
                        timeout=True,
                        locator_attempts=locator_attempts,
                    )
                status = "TIMEOUT" if exc.timeout else "FAILED"
                self._record_trace(
                    traces,
                    f"assertion_{index}",
                    status,
                    duration,
                    exc,
                    locator_attempts=locator_attempts,
                )
                self._skip_trace_ids(traces, plan, assertion_from=index + 1)
                return self._result_for_status(status, traces, exc)
            except Exception as exc:  # noqa: BLE001 - browser boundary
                error = self._browser_error(exc)
                error = self._total_timeout_if_expired(
                    error,
                    run_deadline=deadline,
                    run_budget_limited=run_budget_limited,
                    locator_attempts=locator_attempts,
                )
                duration = _elapsed_ms(self._clock(), started)
                if duration > assertion.timeout_ms and not error.timeout:
                    error = WebExecutionError("WEB_NODE_TIMEOUT", "Web 节点执行超时", timeout=True)
                status = "TIMEOUT" if error.timeout else "FAILED"
                self._record_trace(
                    traces,
                    f"assertion_{index}",
                    status,
                    duration,
                    error,
                    locator_attempts=locator_attempts,
                )
                self._skip_trace_ids(traces, plan, assertion_from=index + 1)
                return self._result_for_status(status, traces, error)
        if failed:
            return WebExecutionResult("FAILED", traces, "WEB_ACTION_FAILED", "Web 动作执行失败")
        return WebExecutionResult("SUCCESS", traces)

    def _perform_action(
        self,
        page: Any,
        action: WebExecutionActionPlan,
        context: Mapping[str, Any],
        deadline: float,
        *,
        run_deadline: float,
        planned_sensitive_values: tuple[str, ...] = (),
        observed_assertion_values: list[str] | None = None,
        managed_tabs: _ManagedTabs | None = None,
    ) -> list[dict[str, Any]]:
        timeout = self._remaining_timeout(deadline, action.timeout_ms)
        tabs = managed_tabs or _ManagedTabs(page)
        healing_sensitive_values = (
            *planned_sensitive_values,
            *(observed_assertion_values or ()),
        )
        if action.type in {"NEW_TAB", "SWITCH_TAB", "CLOSE_TAB"}:
            alias = action.value
            if not isinstance(alias, str) or not _WEB_TAB_ALIAS.fullmatch(alias):
                raise WebExecutionError("WEB_TAB_INVALID", "Web 标签别名无效")
            if action.type == "NEW_TAB":
                new_page = tabs.create(alias)
                url = _resolve_template(action.url, context)
                _validate_browser_url(url, "action.url")
                self._invoke_page(
                    new_page,
                    "goto",
                    url,
                    timeout=self._remaining_timeout(deadline),
                )
                return []
            target = tabs.target(alias)
            if action.type == "SWITCH_TAB":
                self._invoke_page(target, "bring_to_front", timeout=timeout)
                tabs.activate(alias)
                return []
            target = tabs.require_closable(alias)
            was_current = alias == tabs.current_alias
            try:
                self._invoke_page(target, "close", timeout=timeout)
            except Exception:
                if tabs.page_is_closed(target):
                    tabs.forget(alias)
                raise
            tabs.forget(alias)
            if was_current:
                current_page = tabs.current_page
                self._invoke_page(
                    current_page,
                    "bring_to_front",
                    timeout=self._remaining_timeout(deadline),
                )
            return []
        if action.type == "GOTO":
            url = _resolve_template(action.url, context)
            _validate_browser_url(url, "action.url")
            self._invoke_page(page, "goto", url, timeout=timeout)
            return []
        if action.type == "RELOAD":
            self._invoke_page(page, "reload", timeout=timeout)
            return []
        if action.type == "BACK":
            self._invoke_page(page, "go_back", timeout=timeout)
            return []
        if action.type == "FORWARD":
            self._invoke_page(page, "go_forward", timeout=timeout)
            return []
        if action.type == "FILL":
            value = _resolve_template(action.value, context)
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "fill", value, timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "CLICK":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "click", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "DOUBLE_CLICK":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "dblclick", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "RIGHT_CLICK":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "click", timeout=operation_timeout, button="right"
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "CLEAR":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "clear", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "HOVER":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "hover", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "DRAG_DROP":
            target_selector = _resolve_template(action.value, context)
            if not isinstance(target_selector, str) or not target_selector.strip():
                raise ProtocolError("DRAG_DROP 目标 Locator 无效")
            target = page.locator(target_selector)
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator,
                    "drag_to",
                    target,
                    timeout=operation_timeout,
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "UPLOAD":
            if not isinstance(action.value, str) or not isinstance(action.key, str):
                raise ProtocolError("UPLOAD Action 配置无效")
            try:
                upload_bytes = base64.b64decode(action.value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProtocolError("UPLOAD 文件内容无效") from exc
            if not upload_bytes or len(upload_bytes) > 1024 * 1024:
                raise ProtocolError("UPLOAD 文件大小无效")
            mime = mimetypes.guess_type(action.key)[0] or "application/octet-stream"
            payload = {"name": action.key, "mimeType": mime, "buffer": upload_bytes}
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator,
                    "set_input_files",
                    payload,
                    timeout=operation_timeout,
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "DOWNLOAD":
            expected_name = _resolve_template(action.value, context)
            if not isinstance(expected_name, str):
                raise ProtocolError("DOWNLOAD 文件名无效")
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._capture_download(
                    page,
                    locator,
                    expected_name,
                    operation_timeout,
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "SELECT":
            value = _resolve_template(action.value, context)
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "select_option", value, timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "PRESS":
            key = _resolve_template(action.key, context)
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "press", key, timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "CHECK":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "check", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "UNCHECK":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "uncheck", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "RADIO":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._select_radio(
                    locator, operation_timeout, deadline
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "ENTER":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "press", "Enter", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "TAB":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "press", "Tab", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "WAIT_ELEMENT":
            return self._perform_locator_action(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._invoke_locator(
                    locator, "wait_for", state="visible", timeout=operation_timeout
                ),
                run_deadline=run_deadline,
                sensitive_values=healing_sensitive_values,
            )
        elif action.type == "WAIT_URL":
            expected = _resolve_template(action.url, context)
            self._invoke_page(page, "wait_for_url", expected, timeout=timeout)
            return []
        elif action.type == "WAIT_NETWORK_IDLE":
            self._invoke_page(page, "wait_for_load_state", "networkidle", timeout=timeout)
            return []
        elif action.type == "WAIT_TIME":
            duration_ms = int(_resolve_template(action.value, context))
            if not 1 <= duration_ms <= 600_000:
                raise ProtocolError("WAIT_TIME 时长无效")
            if duration_ms > timeout:
                raise WebExecutionError("WEB_NODE_TIMEOUT", "Web 节点执行超时", timeout=True)
            self._invoke_page(page, "wait_for_timeout", duration_ms, timeout=timeout)
            return []
        elif action.type == "WAIT_TEXT":
            expected = _resolve_template(action.value, context)
            if not isinstance(expected, str) or not expected:
                raise ProtocolError("WAIT_TEXT 文本无效")
            return self._assert_locator_candidates(
                page,
                action.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_value_matches(
                    locator,
                    "inner_text",
                    expected,
                    operation_timeout,
                    observed_assertion_values,
                    exact=False,
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        elif action.type == "COOKIE":
            name = _resolve_template(action.key, context)
            value = _resolve_template(action.value, context)
            current_url = getattr(page, "url", None)
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(value, str)
                or not isinstance(current_url, str)
            ):
                raise ProtocolError("COOKIE Action 配置无效")
            _validate_browser_url(current_url, "page.url")
            self._invoke_browser_context(
                page,
                "add_cookies",
                [{"name": name, "value": value, "url": current_url}],
            )
            return []
        elif action.type in {"LOCAL_STORAGE", "SESSION_STORAGE"}:
            storage_name = "localStorage" if action.type == "LOCAL_STORAGE" else "sessionStorage"
            key = _resolve_template(action.key, context)
            value = _resolve_template(action.value, context)
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ProtocolError("Web Storage Action 配置无效")
            self._invoke_page(
                page,
                "evaluate",
                "([storageName, key, value]) => window[storageName].setItem(key, value)",
                [storage_name, key, value],
                timeout=timeout,
            )
            return []
        elif action.type == "JS_EVAL":
            script = _resolve_template(action.value, context)
            if not isinstance(script, str) or not script:
                raise ProtocolError("JS_EVAL 脚本无效")
            self._invoke_page(page, "evaluate", script, timeout=timeout)
            return []
        else:
            raise ProtocolError("Web action 类型不受支持")

    @staticmethod
    def _invoke_browser_context(page: Any, method_name: str, *args: Any) -> Any:
        browser_context = getattr(page, "context", None)
        method = getattr(browser_context, method_name, None)
        if not callable(method):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Web 浏览器上下文操作不可用")
        return method(*args)

    def _capture_download(
        self,
        page: Any,
        locator: Any,
        expected_name: str,
        timeout: int,
    ) -> None:
        expect_download = getattr(page, "expect_download", None)
        if not callable(expect_download):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Web 下载监听不可用")
        with expect_download(timeout=timeout) as download_info:
            self._invoke_locator(locator, "click", timeout=timeout)
        download = download_info.value
        failure_method = getattr(download, "failure", None)
        if callable(failure_method) and failure_method():
            raise WebExecutionError("WEB_DOWNLOAD_FAILED", "Web 文件下载失败")
        suggested_name = getattr(download, "suggested_filename", None)
        if not isinstance(suggested_name, str) or not suggested_name:
            raise WebExecutionError("WEB_DOWNLOAD_FAILED", "Web 文件下载失败")
        if expected_name and suggested_name != expected_name:
            raise WebExecutionError("WEB_DOWNLOAD_MISMATCH", "Web 下载文件名不匹配")
        self._download_names.append(suggested_name)
        delete_method = getattr(download, "delete", None)
        if callable(delete_method):
            delete_method()

    def _select_radio(self, locator: Any, timeout: int, deadline: float) -> None:
        is_radio = self._invoke_locator(
            locator,
            "evaluate",
            "(element) => element instanceof HTMLInputElement && element.type === 'radio'",
            timeout=timeout,
        )
        if is_radio is not True:
            raise WebExecutionError("WEB_ACTION_FAILED", "Web 节点执行失败")
        self._invoke_locator(locator, "check", timeout=self._remaining_timeout(deadline))

    def _perform_assertion(
        self,
        page: Any,
        assertion: WebExecutionAssertionPlan,
        context: Mapping[str, Any],
        deadline: float,
        *,
        run_deadline: float,
        observed_assertion_values: list[str] | None = None,
        planned_sensitive_values: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        timeout = self._remaining_timeout(deadline, assertion.timeout_ms)
        if assertion.type == "ASSERT_URL":
            expected = _resolve_template(assertion.expected, context)
            self._invoke_page(page, "wait_for_url", expected, timeout=timeout)
            return []
        if assertion.type == "ASSERT_VISIBLE":
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: (
                    self._invoke_locator(locator, "is_visible", timeout=operation_timeout) is True
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_HIDDEN":
            return self._assert_hidden_candidates(
                page,
                assertion.locator,
                context,
                deadline,
            )
        if assertion.type == "ASSERT_CLICKABLE":
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_is_clickable(
                    locator, operation_timeout
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
                retry_predicate_timeouts=True,
                predicate_probe_timeout_ms=WEB_LOCATOR_PROBE_TIMEOUT_MS,
            )
        if assertion.type == "ASSERT_EXISTS":
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: (
                    self._invoke_locator(locator, "count", timeout=operation_timeout) > 0
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_ENABLED":
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: (
                    self._invoke_locator(locator, "is_enabled", timeout=operation_timeout) is True
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_TEXT":
            expected = _resolve_template(assertion.expected, context)
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_value_matches(
                    locator,
                    "inner_text",
                    expected,
                    operation_timeout,
                    observed_assertion_values,
                    exact=False,
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_TEXT_EQUAL":
            expected = _resolve_template(assertion.expected, context)
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_value_matches(
                    locator,
                    "inner_text",
                    expected,
                    operation_timeout,
                    observed_assertion_values,
                    exact=True,
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_INPUT_VALUE":
            expected = _resolve_template(assertion.expected, context)
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_value_matches(
                    locator,
                    "input_value",
                    expected,
                    operation_timeout,
                    observed_assertion_values,
                    exact=True,
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_TITLE":
            expected = _resolve_template(assertion.expected, context)
            actual = self._invoke_page(page, "title", timeout=timeout)
            _remember_assertion_value(observed_assertion_values, actual)
            if not isinstance(expected, str) or actual != expected:
                raise WebExecutionError("ASSERTION_FAILED", "Web 断言未通过")
            return []
        if assertion.type == "ASSERT_ATTRIBUTE":
            expected = _resolve_template(assertion.expected, context)
            key = _resolve_template(assertion.key, context)
            if not isinstance(expected, str) or not isinstance(key, str) or not key:
                raise ProtocolError("ASSERT_ATTRIBUTE 配置无效")
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: self._locator_attribute_matches(
                    locator,
                    key,
                    expected,
                    operation_timeout,
                    observed_assertion_values,
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_ELEMENT_COUNT":
            expected = _resolve_template(assertion.expected, context)
            if not isinstance(expected, str) or not expected.isdigit():
                raise ProtocolError("ASSERT_ELEMENT_COUNT 配置无效")
            expected_count = int(expected)
            return self._assert_locator_candidates(
                page,
                assertion.locator,
                context,
                deadline,
                lambda locator, operation_timeout: (
                    self._invoke_locator(locator, "count", timeout=operation_timeout)
                    == expected_count
                ),
                run_deadline=run_deadline,
                planned_sensitive_values=planned_sensitive_values,
                observed_assertion_values=observed_assertion_values,
            )
        if assertion.type == "ASSERT_DOWNLOAD_SUCCESS":
            expected = _resolve_template(assertion.expected, context)
            if not isinstance(expected, str):
                raise ProtocolError("ASSERT_DOWNLOAD_SUCCESS 配置无效")
            matched = next(
                (name for name in self._download_names if not expected or name == expected),
                None,
            )
            if matched is None:
                raise WebExecutionError("ASSERTION_FAILED", "Web 断言未通过")
            _remember_assertion_value(observed_assertion_values, matched)
            return []
        if assertion.type == "ASSERT_NETWORK_REQUEST":
            expected = _resolve_template(assertion.expected, context)
            if not isinstance(expected, str) or not expected:
                raise ProtocolError("ASSERT_NETWORK_REQUEST 配置无效")
            while True:
                matched = next((url for url in self._request_urls if expected in url), None)
                if matched is not None:
                    _remember_assertion_value(observed_assertion_values, matched)
                    return []
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise WebExecutionError("WEB_NODE_TIMEOUT", "Web 节点执行超时", timeout=True)
                time.sleep(min(0.05, remaining))
        if assertion.type == "ASSERT_SCREENSHOT_VISUAL_COMPARE":
            if not isinstance(assertion.expected, str):
                raise ProtocolError("Screenshot baseline 无效")
            try:
                baseline = base64.b64decode(assertion.expected, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProtocolError("Screenshot baseline 无效") from exc
            if not baseline.startswith(b"\x89PNG\r\n\x1a\n") or len(baseline) > 1024 * 1024:
                raise ProtocolError("Screenshot baseline 无效")
            actual = self._invoke_page(page, "screenshot", type="png", timeout=timeout)
            if (
                not isinstance(actual, bytes)
                or len(actual) > 5 * 1024 * 1024
                or not hmac.compare_digest(
                    hashlib.sha256(actual).digest(),
                    hashlib.sha256(baseline).digest(),
                )
            ):
                raise WebExecutionError("ASSERTION_FAILED", "Web 视觉断言未通过")
            return []
        raise ProtocolError("Web assertion 类型不受支持")

    def _semantic_snapshot(
        self,
        page: Any,
        sensitive_values: tuple[str, ...],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        try:
            page_url = safe_url(_page_url(page))
            title = redact_text(
                self._invoke_page(page, "title", timeout=timeout),
                sensitive_values,
                limit=200,
            )
            body = self._invoke_page(page, "locator", "body", timeout=timeout)
            visible_text = redact_text(
                self._invoke_locator(body, "inner_text", timeout=timeout),
                sensitive_values,
                limit=64_000,
            )
        except WebExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - page content stays private
            raise WebExecutionError("WEB_AI_SNAPSHOT_FAILED", "Web AI 页面快照采集失败") from exc
        return {
            "status_code": 200,
            "json_body": {"page_url": page_url, "page_title": title},
            "text": visible_text,
            "headers": {},
            "cookies": {},
            "elapsed_ms": 0,
            "response_time_ms": 0,
        }

    def _bind_request_tracking(self, page: Any) -> None:
        on = getattr(page, "on", None)
        if not callable(on):
            return

        def record(request: Any) -> None:
            if len(self._request_urls) >= 1000:
                return
            url = getattr(request, "url", None)
            if isinstance(url, str) and 1 <= len(url) <= 4096:
                self._request_urls.append(url)

        on("request", record)

    def _locator_value_matches(
        self,
        locator: Any,
        method_name: str,
        expected: object,
        timeout: int,
        observed_assertion_values: list[str] | None,
        *,
        exact: bool,
    ) -> bool:
        actual = self._invoke_locator(locator, method_name, timeout=timeout)
        _remember_assertion_value(observed_assertion_values, actual)
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        return actual == expected if exact else expected in actual

    def _locator_attribute_matches(
        self,
        locator: Any,
        key: str,
        expected: str,
        timeout: int,
        observed_assertion_values: list[str] | None,
    ) -> bool:
        actual = self._invoke_locator(locator, "get_attribute", key, timeout=timeout)
        _remember_assertion_value(observed_assertion_values, actual)
        return isinstance(actual, str) and actual == expected

    def _locator_is_clickable(self, locator: Any, timeout: int) -> bool:
        self._invoke_locator(locator, "click", timeout=timeout, trial=True)
        return True

    def _assert_hidden_candidates(
        self,
        page: Any,
        locator_plan: Any,
        context: Mapping[str, Any],
        deadline: float,
    ) -> list[dict[str, Any]]:
        if locator_plan is None or not locator_plan.candidates:
            raise ProtocolError("Web locator 缺失")
        candidates = _ordered_candidates(locator_plan.candidates)[:20]
        last_complete_attempts: list[dict[str, Any]] = []
        while True:
            statuses: dict[int, str] = {}
            visible_match = False
            invalid_candidate = False
            for index, candidate in enumerate(candidates):
                remaining_ms = int((deadline - self._clock()) * 1000)
                if remaining_ms <= 0:
                    break
                try:
                    locator = self._make_locator(page, candidate, context)
                    count = self._invoke_locator(
                        locator,
                        "count",
                        timeout=min(remaining_ms, WEB_LOCATOR_PROBE_TIMEOUT_MS),
                    )
                    if type(count) is not int or count < 0:
                        raise WebExecutionError("ASSERTION_FAILED", "Web 断言未通过")
                    if count == 0:
                        statuses[index] = "NOT_FOUND"
                        continue
                    visible_filter = getattr(locator, "filter", None)
                    if not callable(visible_filter):
                        raise WebExecutionError("ASSERTION_FAILED", "Web 断言未通过")
                    visible_locator = visible_filter(visible=True)
                    visible_count = self._invoke_locator(
                        visible_locator,
                        "count",
                        timeout=min(remaining_ms, WEB_LOCATOR_PROBE_TIMEOUT_MS),
                    )
                    if type(visible_count) is not int or visible_count < 0:
                        raise WebExecutionError("ASSERTION_FAILED", "Web 断言未通过")
                except Exception:  # noqa: BLE001 - candidate details stay private
                    statuses[index] = "ACTION_FAILED"
                    invalid_candidate = True
                    continue
                if visible_count > 0:
                    statuses[index] = "ACTION_FAILED"
                    visible_match = True
                else:
                    statuses[index] = "SUCCESS"
            attempts = _locator_attempts(candidates, statuses)
            if len(statuses) == len(candidates):
                last_complete_attempts = attempts
            if invalid_candidate:
                raise WebExecutionError(
                    "ASSERTION_FAILED",
                    "Web 断言未通过",
                    locator_attempts=attempts,
                )
            if len(statuses) == len(candidates) and not visible_match:
                return attempts
            remaining_seconds = deadline - self._clock()
            if remaining_seconds <= 0:
                raise WebExecutionError(
                    "WEB_NODE_TIMEOUT",
                    "Web 节点执行超时",
                    timeout=True,
                    locator_attempts=last_complete_attempts or attempts,
                )
            time.sleep(min(0.05, remaining_seconds))

    def _assert_locator_candidates(
        self,
        page: Any,
        locator_plan: Any,
        context: Mapping[str, Any],
        deadline: float,
        predicate: Callable[[Any, int], bool],
        *,
        run_deadline: float,
        planned_sensitive_values: tuple[str, ...] = (),
        observed_assertion_values: list[str] | None = None,
        retry_predicate_timeouts: bool = False,
        predicate_probe_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if locator_plan is None or not locator_plan.candidates:
            raise ProtocolError("Web locator 缺失")
        candidates = _ordered_candidates(locator_plan.candidates)[:20]
        statuses: dict[int, str] = {}
        exhausted: set[int] = set()
        timed_out = False
        while len(exhausted) < len(candidates):
            retryable_probe = False
            for index, candidate in enumerate(candidates):
                if index in exhausted:
                    continue
                remaining_ms = int((deadline - self._clock()) * 1000)
                if remaining_ms <= 0:
                    timed_out = True
                    break
                try:
                    locator = self._make_locator(page, candidate, context)
                    ready = self._locator_ready(
                        locator, min(remaining_ms, WEB_LOCATOR_PROBE_TIMEOUT_MS)
                    )
                except Exception as exc:  # noqa: BLE001 - candidate details stay private
                    if not (retry_predicate_timeouts and statuses.get(index) == "ACTION_FAILED"):
                        statuses[index] = "NOT_FOUND"
                    if _looks_like_timeout(exc):
                        retryable_probe = True
                    else:
                        exhausted.add(index)
                    continue
                if not ready:
                    statuses[index] = "NOT_FOUND"
                    exhausted.add(index)
                    continue
                try:
                    predicate_timeout = self._remaining_timeout(deadline)
                    if predicate_probe_timeout_ms is not None:
                        predicate_timeout = min(predicate_timeout, predicate_probe_timeout_ms)
                    passed = predicate(locator, predicate_timeout)
                except Exception as exc:  # noqa: BLE001 - candidate details stay private
                    predicate_timed_out = _looks_like_timeout(exc)
                    statuses[index] = "ACTION_FAILED"
                    if predicate_timed_out and retry_predicate_timeouts:
                        retryable_probe = True
                        continue
                    timed_out = timed_out or predicate_timed_out
                    exhausted.add(index)
                    continue
                if passed:
                    statuses[index] = "SUCCESS"
                    return _locator_attempts(candidates, statuses)
                statuses[index] = "ACTION_FAILED"
                exhausted.add(index)
            if timed_out or not retryable_probe:
                break
        attempts = _locator_attempts(candidates, statuses)
        timed_out = timed_out or self._clock() >= deadline
        all_not_found = (
            len(statuses) == len(candidates)
            and bool(attempts)
            and all(item["status"] == "NOT_FOUND" for item in attempts)
        )
        run_timed_out = self._clock() >= run_deadline
        healing_failure = (
            all_not_found
            and not run_timed_out
            and (not timed_out or _healing_element_version_id(locator_plan) is not None)
        )
        effective_timeout = timed_out and not healing_failure
        raise WebExecutionError(
            "WEB_NODE_TIMEOUT" if effective_timeout else "ASSERTION_FAILED",
            "Web 节点执行超时" if effective_timeout else "Web 断言未通过",
            timeout=effective_timeout,
            locator_attempts=attempts,
            healing_context=(
                _collect_healing_context(
                    page,
                    locator_plan,
                    sensitive_values=(
                        *planned_sensitive_values,
                        *(observed_assertion_values or ()),
                    ),
                )
                if healing_failure
                else None
            ),
        )

    def _locator_with_fallback(
        self,
        page: Any,
        locator_plan: Any,
        context: Mapping[str, Any],
        timeout: int,
    ) -> Any:
        if locator_plan is None or not locator_plan.candidates:
            raise ProtocolError("Web locator 缺失")
        candidates = _ordered_candidates(locator_plan.candidates)[:20]
        exhausted: set[int] = set()
        deadline = self._clock() + timeout / 1000.0
        while len(exhausted) < len(candidates):
            retryable_probe = False
            for index, candidate in enumerate(candidates):
                if index in exhausted:
                    continue
                remaining_ms = int((deadline - self._clock()) * 1000)
                if remaining_ms <= 0:
                    break
                try:
                    locator = self._make_locator(page, candidate, context)
                    ready = self._locator_ready(
                        locator, min(remaining_ms, WEB_LOCATOR_PROBE_TIMEOUT_MS)
                    )
                except Exception as exc:  # noqa: BLE001 - candidate details stay private
                    if _looks_like_timeout(exc):
                        retryable_probe = True
                    else:
                        exhausted.add(index)
                    continue
                if ready:
                    return locator
                exhausted.add(index)
            if self._clock() >= deadline or not retryable_probe:
                break
        timed_out = self._clock() >= deadline
        raise WebExecutionError(
            "WEB_NODE_TIMEOUT" if timed_out else "WEB_LOCATOR_NOT_FOUND",
            "Web Locator 未找到",
            timeout=timed_out,
        )

    def _perform_locator_action(
        self,
        page: Any,
        locator_plan: Any,
        context: Mapping[str, Any],
        deadline: float,
        action: Callable[[Any, int], Any],
        *,
        run_deadline: float,
        sensitive_values: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        if locator_plan is None or not locator_plan.candidates:
            raise ProtocolError("Web locator 缺失")
        candidates = _ordered_candidates(locator_plan.candidates)[:20]
        statuses: dict[int, str] = {}
        exhausted: set[int] = set()
        timed_out = False
        attempted = False
        while len(exhausted) < len(candidates):
            retryable_probe = False
            for index, candidate in enumerate(candidates):
                if index in exhausted:
                    continue
                remaining_ms = int((deadline - self._clock()) * 1000)
                if remaining_ms <= 0:
                    timed_out = True
                    break
                try:
                    locator = self._make_locator(page, candidate, context)
                    ready = self._locator_ready(
                        locator, min(remaining_ms, WEB_LOCATOR_PROBE_TIMEOUT_MS)
                    )
                except Exception as exc:  # noqa: BLE001 - candidate details stay private
                    statuses[index] = "NOT_FOUND"
                    if _looks_like_timeout(exc):
                        retryable_probe = True
                    else:
                        exhausted.add(index)
                    continue
                if not ready:
                    statuses[index] = "NOT_FOUND"
                    exhausted.add(index)
                    continue
                attempted = True
                try:
                    operation_timeout = self._remaining_timeout(deadline)
                    action(locator, operation_timeout)
                except Exception as exc:  # noqa: BLE001 - candidate details stay private
                    timed_out = timed_out or _looks_like_timeout(exc)
                    statuses[index] = "ACTION_FAILED"
                    exhausted.add(index)
                    continue
                statuses[index] = "SUCCESS"
                return _locator_attempts(candidates, statuses)
            if timed_out or not retryable_probe:
                break
        attempts = _locator_attempts(candidates, statuses)
        timed_out = timed_out or self._clock() >= deadline
        all_not_found = (
            len(statuses) == len(candidates)
            and bool(attempts)
            and all(item["status"] == "NOT_FOUND" for item in attempts)
        )
        run_timed_out = self._clock() >= run_deadline
        healing_failure = (
            not attempted
            and all_not_found
            and not run_timed_out
            and (not timed_out or _healing_element_version_id(locator_plan) is not None)
        )
        if timed_out and not healing_failure:
            raise WebExecutionError(
                "WEB_NODE_TIMEOUT",
                "Web 节点执行超时",
                timeout=True,
                locator_attempts=attempts,
            )
        raise WebExecutionError(
            "WEB_ACTION_FAILED" if attempted else "WEB_LOCATOR_NOT_FOUND",
            "Web 节点执行失败" if attempted else "Web Locator 未找到",
            locator_attempts=attempts,
            healing_context=(
                _collect_healing_context(
                    page,
                    locator_plan,
                    sensitive_values=sensitive_values,
                )
                if healing_failure
                else None
            ),
        )

    @staticmethod
    def _make_locator(page: Any, candidate: WebLocatorCandidate, context: Mapping[str, Any]) -> Any:
        value = _resolve_template(candidate.value, context)
        if candidate.strategy == "css":
            return page.locator(value)
        if candidate.strategy == "xpath":
            return page.locator(f"xpath={value}")
        if candidate.strategy == "text":
            return page.get_by_text(value)
        if candidate.strategy == "role":
            return page.get_by_role(value)
        if candidate.strategy == "label":
            return page.get_by_label(value)
        if candidate.strategy == "placeholder":
            return page.get_by_placeholder(value)
        if candidate.strategy == "test_id":
            return page.get_by_test_id(value)
        raise ProtocolError("Web locator strategy 不受支持")

    @staticmethod
    def _locator_ready(locator: Any, timeout: int) -> bool:
        wait_for = getattr(locator, "wait_for", None)
        if callable(wait_for):
            wait_for(state="attached", timeout=timeout)
            return True
        count = getattr(locator, "count", None)
        if callable(count):
            return bool(count())
        return True

    @staticmethod
    def _invoke_locator(
        locator: Any, method_name: str, *args: Any, timeout: int, **kwargs: Any
    ) -> Any:
        method = getattr(locator, method_name, None)
        if not callable(method):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Web Locator 操作不可用")
        try:
            return method(*args, timeout=timeout, **kwargs)
        except TypeError:
            return method(*args, **kwargs)

    @staticmethod
    def _invoke_page(page: Any, method_name: str, *args: Any, timeout: int, **kwargs: Any) -> Any:
        method = getattr(page, method_name, None)
        if not callable(method):
            raise WebExecutionError("WEB_BROWSER_UNAVAILABLE", "Web 页面操作不可用")
        try:
            return method(*args, timeout=timeout, **kwargs)
        except TypeError:
            return method(*args, **kwargs)

    def _check_deadline(self, stop_event: Any | None, deadline: float) -> None:
        if WebExecutor._cancelled(stop_event):
            raise WebExecutionError("CANCEL_REQUESTED", "Runner 检测到取消请求")
        if self._clock() >= deadline:
            raise WebExecutionError("WEB_TOTAL_TIMEOUT", "Web Run 总超时", timeout=True)

    def _remaining_timeout(self, deadline: float, node_timeout_ms: int | None = None) -> int:
        remaining_ms = int((deadline - self._clock()) * 1000)
        if remaining_ms <= 0:
            raise WebExecutionError("WEB_NODE_TIMEOUT", "Web 节点执行超时", timeout=True)
        return min(600_000, remaining_ms, node_timeout_ms or 600_000)

    def _total_timeout_if_expired(
        self,
        error: WebExecutionError,
        *,
        run_deadline: float,
        run_budget_limited: bool,
        locator_attempts: list[dict[str, Any]] | None = None,
    ) -> WebExecutionError:
        if (
            error.timeout
            and run_budget_limited
            and self._clock() >= run_deadline - WEB_TIMEOUT_CLASSIFICATION_TOLERANCE_SECONDS
        ):
            return WebExecutionError(
                "WEB_TOTAL_TIMEOUT",
                "Web Run 总超时",
                timeout=True,
                locator_attempts=locator_attempts,
            )
        return error

    @staticmethod
    def _cancelled(stop_event: Any | None) -> bool:
        is_set = getattr(stop_event, "is_set", None)
        return bool(is_set()) if callable(is_set) else False

    @staticmethod
    def _record_trace(
        traces: list[dict[str, Any]],
        node_id: str,
        status: str,
        duration_ms: int,
        error: WebExecutionError | None,
        *,
        locator_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        if any(item["node_id"] == node_id for item in traces):
            return
        trace = {
            "node_id": node_id,
            "status": status,
            "duration_ms": max(0, min(86_400_000, duration_ms)),
            "error_type": error.error_type if error else None,
            "error_message": str(error) if error else None,
            "locator_attempts": _safe_locator_attempts(
                locator_attempts if locator_attempts is not None else _error_locator_attempts(error)
            ),
        }
        healing_context = _error_healing_context(error)
        if healing_context is not None:
            trace["healing_context"] = healing_context
        traces.append(trace)

    @staticmethod
    def _skip_trace_ids(
        traces: list[dict[str, Any]],
        plan: WebExecutionPlanResult,
        *,
        skip_from: int = 1,
        assertion_from: int = 1,
    ) -> None:
        existing = {item["node_id"] for item in traces}
        for index in range(skip_from, len(plan.actions) + 1):
            node_id = f"action_{index}"
            if node_id not in existing:
                traces.append(_trace(node_id, "SKIPPED"))
        for index in range(assertion_from, len(plan.assertions) + 1):
            node_id = f"assertion_{index}"
            if node_id not in existing:
                traces.append(_trace(node_id, "SKIPPED"))

    @staticmethod
    def _result_for_status(
        status: str, traces: list[dict[str, Any]], error: WebExecutionError
    ) -> WebExecutionResult:
        return WebExecutionResult(
            "TIMEOUT" if status == "TIMEOUT" else "FAILED",
            traces[:MAX_WEB_TRACE_COUNT],
            error.error_type or "WEB_EXECUTION_ERROR",
            str(error),
        )

    @staticmethod
    def _failed(error_type: str, message: str, timeout: bool = False) -> WebExecutionResult:
        return WebExecutionResult(
            "TIMEOUT" if timeout else "FAILED", [], error_type[:100], message[:MAX_WEB_ERROR_LENGTH]
        )

    @staticmethod
    def _cancelled_result() -> WebExecutionResult:
        return WebExecutionResult("CANCELLED", [], "CANCEL_REQUESTED", "Runner 检测到取消请求")

    @staticmethod
    def _browser_error(exc: BaseException) -> WebExecutionError:
        return WebExecutionError(
            "WEB_NODE_TIMEOUT" if _looks_like_timeout(exc) else "WEB_ACTION_FAILED",
            "Web 节点执行超时" if _looks_like_timeout(exc) else "Web 节点执行失败",
            timeout=_looks_like_timeout(exc),
        )


class _WebEvidenceCollector:
    """Collect bounded evidence while the child still owns the browser page."""

    def __init__(
        self,
        root: Path | None,
        plan: WebExecutionPlanResult,
        *,
        clock: Callable[[], float],
        observed_assertion_values: list[str] | None = None,
    ) -> None:
        self._root = root
        self._plan = plan
        self._clock = clock
        self._started = clock()
        self._tracing: Any | None = None
        self._trace_started = False
        self._console_errors: list[dict[str, Any]] = []
        self._network_errors: list[dict[str, Any]] = []
        self._observed_assertion_values = observed_assertion_values
        self._forbidden_values = _web_sensitive_values(plan)
        self._drop_trace_for_observed_overflow = False

    def start(self, context: Any, page: Any) -> None:
        if self._root is None:
            return
        try:
            root = self._root.resolve(strict=True)
            if not root.is_dir():
                return
            self._root = root
        except (OSError, RuntimeError):
            self._root = None
            return
        tracing = getattr(context, "tracing", None)
        start = getattr(tracing, "start", None)
        if callable(start):
            try:
                start(screenshots=False, snapshots=False, sources=False)
                self._tracing = tracing
                self._trace_started = True
            except Exception:  # noqa: BLE001 - trace is optional and fail-closed
                self._tracing = None
        self._bind_page(page)

    def finish(
        self,
        context: Any,
        page: Any,
        result: WebExecutionResult,
        *,
        page_count: int = 1,
    ) -> WebEvidenceManifest:
        if self._root is None:
            return WebEvidenceManifest()
        items: list[EvidenceManifestItem] = []
        self._refresh_forbidden_values()
        try:
            title = (
                "[REDACTED]"
                if self._drop_trace_for_observed_overflow
                else _safe_page_title(page, self._forbidden_values)
            )
        except Exception:  # noqa: BLE001 - summary values are always safe
            title = ""
        try:
            final_url = safe_url(_page_url(page))
        except Exception:  # noqa: BLE001 - summary values are always safe
            final_url = None
        screenshot_metadata = {
            "width": self._plan.browser_config.window_width,
            "height": self._plan.browser_config.window_height,
            "title": title,
            "page_url": final_url,
        }
        try:
            if not self._drop_trace_for_observed_overflow:
                screenshot = self._capture_screenshot(
                    page, "screenshot-final.png", screenshot_metadata
                )
                if screenshot is not None:
                    items.append(screenshot)
                if result.outcome in {"FAILED", "TIMEOUT"}:
                    failure = self._capture_screenshot(
                        page, "screenshot-failure-1.png", screenshot_metadata
                    )
                    if failure is not None:
                        items.append(failure)
            if not self._drop_trace_for_observed_overflow:
                for index, metadata in enumerate(self._console_errors[:20], start=1):
                    metadata = {
                        **metadata,
                        "message": redact_text(
                            metadata.get("message", ""),
                            self._forbidden_values,
                            limit=256,
                        ),
                    }
                    item = self._write_json_item(
                        "CONSOLE_ERROR", f"console-error-{index}.json", metadata
                    )
                    if item is not None:
                        items.append(item)
                for index, metadata in enumerate(self._network_errors[:20], start=1):
                    metadata = {
                        **metadata,
                        "message": redact_text(
                            metadata.get("message", ""),
                            self._forbidden_values,
                            limit=256,
                        ),
                    }
                    item = self._write_json_item(
                        "NETWORK_ERROR", f"network-error-{index}.json", metadata
                    )
                    if item is not None:
                        items.append(item)
        except Exception:  # noqa: BLE001 - evidence collection is fail-closed
            pass
        summary = {
            "title": title,
            "final_url": final_url,
            "page_count": max(1, min(MAX_MANAGED_WEB_TABS, page_count)),
            "duration_ms": min(86_400_000, _elapsed_ms(self._clock(), self._started)),
            "error_count": len(self._console_errors) + len(self._network_errors),
            "status": result.outcome,
        }
        try:
            summary_item = self._write_json_item("WEB_SUMMARY", "web-summary.json", summary)
            if summary_item is not None:
                items.append(summary_item)
        except Exception:  # noqa: BLE001 - evidence collection is fail-closed
            pass
        try:
            trace_item = self._stop_trace(context, result)
        except Exception:  # noqa: BLE001 - unsafe trace is omitted
            trace_item = None
        if trace_item is not None:
            items.append(trace_item)
        return WebEvidenceManifest(tuple(items))

    def abort(
        self,
        context: Any,
        page: Any,
        result: WebExecutionResult,
        *,
        page_count: int = 1,
    ) -> WebEvidenceManifest:
        """Close evidence collection after an unexpected finish failure.

        The browser is still owned by the caller at this point.  Only bounded,
        metadata-only artifacts are attempted; a failure in either operation is
        omitted and never escapes into the execution result.
        """

        if self._root is None:
            return WebEvidenceManifest()
        self._refresh_forbidden_values()
        try:
            title = (
                "[REDACTED]"
                if self._drop_trace_for_observed_overflow
                else _safe_page_title(page, self._forbidden_values)
            )
        except Exception:  # noqa: BLE001 - summary values are always safe
            title = ""
        try:
            final_url = safe_url(_page_url(page))
        except Exception:  # noqa: BLE001 - summary values are always safe
            final_url = None
        metadata = {
            "title": title,
            "final_url": final_url,
            "page_count": max(1, min(MAX_MANAGED_WEB_TABS, page_count)),
            "duration_ms": min(86_400_000, _elapsed_ms(self._clock(), self._started)),
            "error_count": len(self._console_errors) + len(self._network_errors),
            "status": result.outcome,
        }
        items: list[EvidenceManifestItem] = []
        try:
            summary = self._write_json_item("WEB_SUMMARY", "web-summary.json", metadata)
        except Exception:  # noqa: BLE001 - evidence remains fail-closed
            summary = None
        if summary is not None:
            items.append(summary)
        try:
            trace = self._stop_trace(context, result)
        except Exception:  # noqa: BLE001 - unsafe trace is omitted
            trace = None
        if trace is not None:
            items.append(trace)
        return WebEvidenceManifest(tuple(items))

    def _refresh_forbidden_values(self) -> None:
        observed = self._observed_assertion_values or ()
        self._drop_trace_for_observed_overflow = _OBSERVED_ASSERTION_OVERFLOW in observed
        self._forbidden_values = tuple(
            dict.fromkeys(
                (
                    *self._forbidden_values,
                    *(value for value in observed if value != _OBSERVED_ASSERTION_OVERFLOW),
                )
            )
        )

    def _bind_page(self, page: Any) -> None:
        if self._root is None:
            return
        on = getattr(page, "on", None)
        if not callable(on):
            return
        try:
            on("console", self._on_console)
            on("requestfailed", self._on_request_failed)
        except Exception:  # noqa: BLE001 - event collection is best effort
            return

    def _on_console(self, message: Any) -> None:
        if len(self._console_errors) >= 20:
            return
        message_type = _object_value(message, "type")
        if callable(message_type):
            message_type = message_type()
        if str(message_type).lower() != "error":
            return
        location = _object_value(message, "location")
        if callable(location):
            location = location()
        if not isinstance(location, Mapping):
            location = {}
        source = safe_url(location.get("url"))
        self._console_errors.append(
            {
                "level": "ERROR",
                "message": redact_text(
                    _bounded_evidence_text(
                        _object_value(message, "text") or "", self._forbidden_values
                    ),
                    self._forbidden_values,
                    limit=MAX_OBSERVED_ASSERTION_VALUE_LENGTH,
                ),
                "source": source,
                "line": _safe_int(location.get("lineNumber")),
                "column": _safe_int(location.get("columnNumber")),
                "count": 1,
                "timestamp": _utc_timestamp(),
            }
        )

    def _on_request_failed(self, request: Any) -> None:
        if len(self._network_errors) >= 20:
            return
        failure = _object_value(request, "failure")
        if callable(failure):
            failure = failure()
        self._network_errors.append(
            {
                "method": _safe_text(_object_value(request, "method"), 32),
                "status": 0,
                "category": "REQUEST_FAILED",
                "url": safe_url(_object_value(request, "url")),
                "message": _bounded_evidence_text(failure, self._forbidden_values),
                "duration_ms": 0,
                "timestamp": _utc_timestamp(),
            }
        )

    def _capture_screenshot(
        self,
        page: Any,
        filename: str,
        metadata: Mapping[str, Any],
    ) -> EvidenceManifestItem | None:
        path = self._path(filename)
        if path is None:
            return None
        screenshot = getattr(page, "screenshot", None)
        if not callable(screenshot):
            return None
        try:
            mask_locator = page.locator(
                "input, textarea, select, [contenteditable], [data-sensitive]"
            )
            screenshot(
                path=str(path),
                full_page=False,
                mask=[mask_locator],
                animations="disabled",
            )
        except Exception:  # noqa: BLE001 - screenshot is best effort
            return None
        return self._manifest_item("SCREENSHOT", path, metadata)

    def _write_json_item(
        self,
        artifact_type: str,
        filename: str,
        metadata: Mapping[str, Any],
    ) -> EvidenceManifestItem | None:
        path = self._path(filename)
        if path is None:
            return None
        try:
            path.write_bytes(canonical_json(metadata))
        except (OSError, ProtocolError):
            return None
        return self._manifest_item(artifact_type, path, metadata)

    def _stop_trace(self, context: Any, result: WebExecutionResult) -> EvidenceManifestItem | None:
        if not self._trace_started or self._tracing is None:
            return None
        path = self._path("playwright-trace.zip")
        if path is None:
            return None
        stop = getattr(self._tracing, "stop", None)
        if not callable(stop):
            return None
        try:
            stop(path=str(path))
        except Exception:  # noqa: BLE001 - unsafe/unavailable trace is dropped
            return None
        if self._drop_trace_for_observed_overflow:
            _safe_unlink(path)
            return None
        if not sanitize_trace_file(path, forbidden_values=self._forbidden_values):
            _safe_unlink(path)
            return None
        metadata = {
            "browser": self._plan.browser,
            "page_count": 1,
            "duration_ms": min(86_400_000, _elapsed_ms(self._clock(), self._started)),
        }
        return self._manifest_item("PLAYWRIGHT_TRACE", path, metadata)

    def _path(self, filename: str) -> Path | None:
        if self._root is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", filename):
            return None
        path = self._root / filename
        try:
            path.resolve().relative_to(self._root)
        except ValueError:
            return None
        return path

    @staticmethod
    def _manifest_item(
        artifact_type: str, path: Path, metadata: Mapping[str, Any]
    ) -> EvidenceManifestItem | None:
        try:
            relative_path = path.name
            return EvidenceManifestItem(artifact_type, relative_path, dict(metadata))
        except (AttributeError, TypeError):
            return None


def _default_playwright_factory() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WebExecutionError("WEB_PLAYWRIGHT_UNAVAILABLE", "Playwright 不可用") from exc
    return sync_playwright()


def find_system_chrome() -> str | None:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    import os
    from pathlib import Path

    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(str(Path(root) / "Google/Chrome/Application/chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _resolve_template(value: object, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}", value)
        if match:
            return _context_value(context, match.group(1))

        def replace(item: re.Match[str]) -> str:
            resolved = _context_value(context, item.group(1))
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
            return str(resolved)

        return _TEMPLATE.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _resolve_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_template(item, context) for item in value]
    return value


def _context_value(context: Mapping[str, Any], name: str) -> Any:
    current: Any = context
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ProtocolError("Web Runtime Context 缺少模板变量")
        current = current[part]
    return current


def _validate_browser_url(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ProtocolError(f"Web {field_name} 无效")
    if "\r" in value or "\n" in value:
        raise ProtocolError(f"Web {field_name} 不能包含换行符")
    parsed = urlsplit(value)
    if parsed.scheme.lower() == "data":
        return
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ProtocolError(f"Web {field_name} 必须使用 http、https 或受限 data URL")
    if parsed.username is not None or parsed.password is not None:
        raise ProtocolError(f"Web {field_name} 不允许携带用户名或密码")


def _elapsed_ms(now: float, started: float) -> int:
    return max(0, int((now - started) * 1000))


def _trace(node_id: str, status: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": status,
        "duration_ms": 0,
        "error_type": None,
        "error_message": None,
        "locator_attempts": [],
    }


def _locator_attempt(candidate: WebLocatorCandidate, status: str) -> dict[str, Any]:
    return {
        "strategy": candidate.strategy,
        "priority": candidate.priority,
        "status": status,
    }


def _locator_attempts(
    candidates: tuple[WebLocatorCandidate, ...], statuses: Mapping[int, str]
) -> list[dict[str, Any]]:
    return [
        _locator_attempt(candidate, statuses[index])
        for index, candidate in enumerate(candidates)
        if index in statuses
    ]


def _safe_locator_attempts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, Mapping):
            continue
        strategy = item.get("strategy")
        priority = item.get("priority")
        status = item.get("status")
        if (
            not isinstance(strategy, str)
            or strategy not in {"css", "xpath", "text", "role", "label", "placeholder", "test_id"}
            or type(priority) is not int
            or not 1 <= priority <= 20
            or not isinstance(status, str)
            or status not in {"NOT_FOUND", "ACTION_FAILED", "SUCCESS"}
        ):
            continue
        result.append({"strategy": strategy, "priority": priority, "status": status})
    return result


def _error_locator_attempts(
    error: WebExecutionError | None,
    fallback: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if error is not None and error.locator_attempts:
        return error.locator_attempts
    return fallback or []


def _error_healing_context(error: WebExecutionError | None) -> dict[str, Any] | None:
    if error is None or not isinstance(error.healing_context, Mapping):
        return None
    return _normalize_healing_context(error.healing_context)


def _collect_healing_context(
    page: Any,
    locator_plan: Any,
    *,
    sensitive_values: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Collect only bounded, allow-listed descriptors after all locators miss."""

    try:
        overflowed = _OBSERVED_ASSERTION_OVERFLOW in sensitive_values
        safe_sensitive_values = tuple(
            value for value in sensitive_values if value != _OBSERVED_ASSERTION_OVERFLOW
        )
        raw_candidates = [] if overflowed else page.evaluate(_HEALING_CONTEXT_SCRIPT)
        candidates = (
            []
            if overflowed
            else _normalize_healing_candidates(
                raw_candidates, sensitive_values=safe_sensitive_values
            )
        )
        context = {
            "schema_version": 1,
            "trigger": "ALL_LOCATORS_FAILED",
            "element_version_id": _healing_element_version_id(locator_plan),
            "page_url": _healing_page_url(page),
            "page_title": ("" if overflowed else _safe_healing_title(page, safe_sensitive_values)),
            "dom_candidates": candidates,
        }
        return _normalize_healing_context(context)
    except Exception:  # noqa: BLE001 - healing is best effort and never changes outcome
        return None


def _normalize_healing_context(value: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        if set(value) != {
            "schema_version",
            "trigger",
            "element_version_id",
            "page_url",
            "page_title",
            "dom_candidates",
        }:
            return None
        if value.get("schema_version") != 1 or value.get("trigger") != "ALL_LOCATORS_FAILED":
            return None
        element_version_id = value.get("element_version_id")
        if element_version_id is not None and (
            isinstance(element_version_id, bool)
            or not isinstance(element_version_id, int)
            or element_version_id <= 0
        ):
            return None
        page_url = value.get("page_url")
        if page_url is not None and (
            not isinstance(page_url, str)
            or len(page_url) > 2048
            or not _is_clean_healing_text(page_url)
        ):
            return None
        page_title = value.get("page_title")
        if not isinstance(page_title, str) or len(page_title) > MAX_HEALING_VALUE_LENGTH:
            return None
        if "\r" in page_title or "\n" in page_title or _SENSITIVE_HEALING_TEXT.search(page_title):
            page_title = ""
        else:
            page_title = page_title.strip()
        candidates = _normalize_healing_candidates(value.get("dom_candidates"))
        normalized = {
            "schema_version": 1,
            "trigger": "ALL_LOCATORS_FAILED",
            "element_version_id": element_version_id,
            "page_url": page_url,
            "page_title": page_title,
            "dom_candidates": candidates,
        }
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_HEALING_CONTEXT_BYTES:
            while candidates and len(encoded.encode("utf-8")) > MAX_HEALING_CONTEXT_BYTES:
                candidates.pop()
                normalized["dom_candidates"] = candidates
                encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_HEALING_CONTEXT_BYTES:
            return None
        return normalized
    except (TypeError, ValueError, OverflowError):
        return None


def _normalize_healing_candidates(
    value: object, *, sensitive_values: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    allowed = (
        "tag",
        "role",
        "id",
        "name",
        "aria-label",
        "placeholder",
        "data-testid",
        "type",
        "title",
    )
    locating = {"role", "id", "name", "aria-label", "placeholder", "data-testid", "title"}
    result: list[dict[str, str]] = []
    for item in value[:MAX_HEALING_CANDIDATES]:
        if not isinstance(item, Mapping):
            continue
        candidate: dict[str, str] = {}
        for key in allowed:
            raw = item.get(key)
            if not isinstance(raw, str):
                continue
            if "\r" in raw or "\n" in raw:
                continue
            if any(secret and secret in raw for secret in sensitive_values):
                continue
            normalized = raw.strip()
            if not normalized or len(normalized) > MAX_HEALING_VALUE_LENGTH:
                continue
            if _SENSITIVE_HEALING_TEXT.search(normalized):
                continue
            candidate[key] = normalized
        if candidate.get("tag"):
            candidate["tag"] = candidate["tag"].lower()
        if any(key in candidate for key in locating):
            result.append({key: candidate[key] for key in allowed if key in candidate})
    return result


def _healing_element_version_id(locator_plan: Any) -> int | None:
    value = getattr(locator_plan, "element_version_id", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _healing_page_url(page: Any) -> str | None:
    value = _page_url(page)
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        stripped = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return safe_url(stripped)
    except (TypeError, ValueError):
        return None


def _safe_healing_title(page: Any, sensitive_values: tuple[str, ...] = ()) -> str:
    title = _safe_page_title(page, sensitive_values)[:MAX_HEALING_VALUE_LENGTH]
    if "\r" in title or "\n" in title or _SENSITIVE_HEALING_TEXT.search(title):
        return ""
    return title.strip()


def _is_clean_healing_text(value: str) -> bool:
    return "\r" not in value and "\n" not in value


_HEALING_CONTEXT_SCRIPT = r"""
(() => {
  const elements = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,[role],[tabindex]'
  ));
  return elements.slice(0, 160).map((el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (!rect.width || !rect.height || style.visibility === 'hidden' || style.display === 'none') {
      return null;
    }
    const attr = (name) => {
      const value = el.getAttribute(name) || '';
      return value.length <= 10000 ? value : '';
    };
    return {
      tag: (el.tagName || '').toLowerCase(),
      role: attr('role'),
      id: attr('id'),
      name: attr('name'),
      'aria-label': attr('aria-label'),
      placeholder: attr('placeholder'),
      'data-testid': attr('data-testid'),
      type: attr('type'),
      title: attr('title')
    };
  }).filter(Boolean).slice(0, 40);
})()
"""


def _ordered_candidates(
    candidates: tuple[WebLocatorCandidate, ...],
) -> tuple[WebLocatorCandidate, ...]:
    return tuple(
        candidate
        for _index, candidate in sorted(
            enumerate(candidates), key=lambda item: (item[1].priority, item[0])
        )
    )


def _looks_like_timeout(exc: BaseException) -> bool:
    return bool(getattr(exc, "timeout", False)) or "timeout" in type(exc).__name__.lower()


def _is_locator_healing_failure(exc: WebExecutionError) -> bool:
    return (
        exc.error_type in {"WEB_LOCATOR_NOT_FOUND", "ASSERTION_FAILED"}
        and exc.healing_context is not None
        and bool(exc.locator_attempts)
        and all(item.get("status") == "NOT_FOUND" for item in exc.locator_attempts)
    )


def _safe_close(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _web_sensitive_values(plan: WebExecutionPlanResult) -> tuple[str, ...]:
    values: list[str] = [item.value for item in plan.secrets if item.value]
    context = _web_execution_context(plan)
    actions = list(plan.actions)
    assertions = list(plan.assertions)
    if plan.session is not None and plan.session.recovery is not None:
        actions.extend(plan.session.recovery.actions)
        assertions.extend(plan.session.recovery.assertions)
    for action in actions:
        if action.type not in {"FILL", "SELECT"}:
            continue
        try:
            value = _resolve_template(action.value, context)
        except Exception:  # noqa: BLE001 - execution will report the safe plan error
            continue
        if isinstance(value, str) and value:
            values.append(value)
    for assertion in assertions:
        if assertion.expected is None:
            continue
        try:
            value = _resolve_template(assertion.expected, context)
        except Exception:  # noqa: BLE001 - execution will report the safe plan error
            continue
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(dict.fromkeys(values))


def _web_execution_context(plan: WebExecutionPlanResult) -> dict[str, Any]:
    context = dict(plan.initial_context)
    if plan.secrets:
        context["secret"] = {item.name: item.value for item in plan.secrets}
    return context


def _remember_assertion_value(values: list[str] | None, value: object) -> None:
    if values is None or not isinstance(value, str) or not value:
        return
    if value in values:
        return
    if (
        len(value) > MAX_OBSERVED_ASSERTION_VALUE_LENGTH
        or len(values) >= MAX_OBSERVED_ASSERTION_VALUES
    ):
        if _OBSERVED_ASSERTION_OVERFLOW not in values:
            values.append(_OBSERVED_ASSERTION_OVERFLOW)
        return
    values.append(value)


def _bounded_evidence_text(value: object, secrets: tuple[str, ...]) -> str:
    text = str(value if value is not None else "")
    if len(text) > MAX_OBSERVED_ASSERTION_VALUE_LENGTH:
        return "[REDACTED]"
    return redact_text(text, secrets, limit=MAX_OBSERVED_ASSERTION_VALUE_LENGTH)


def _object_value(value: object, attribute: str) -> object:
    try:
        return getattr(value, attribute, None)
    except Exception:  # noqa: BLE001 - third-party object boundary
        return None


def _safe_text(value: object, limit: int) -> str:
    return redact_text(value if value is not None else "", limit=limit)


def _safe_int(value: object) -> int:
    return value if type(value) is int and 0 <= value <= 1_000_000 else 0


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _page_url(page: Any) -> object:
    return _object_value(page, "url")


def _safe_page_title(page: Any, secrets: tuple[str, ...] = ()) -> str:
    title = _object_value(page, "title")
    if callable(title):
        try:
            title = title()
        except Exception:  # noqa: BLE001 - browser boundary
            title = ""
    text = str(title if title is not None else "")
    if len(text) > MAX_OBSERVED_ASSERTION_VALUE_LENGTH:
        return "[REDACTED]"
    return redact_text(text, secrets, limit=256)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
