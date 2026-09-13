from __future__ import annotations

from dataclasses import replace
from typing import Any

from runner.executors.web import WebExecutor
from runner.models import (
    WebExecutionActionPlan,
    WebExecutionPlanResult,
    WebExecutionSecret,
    WebLocatorCandidate,
    WebLocatorPlan,
    WebSessionConditionPlan,
    WebSessionPlan,
    WebSessionRecoveryPlan,
)


class _Locator:
    def __init__(self, page: _Page, selector: str) -> None:
        self.page = page
        self.selector = selector

    def wait_for(self, **_kwargs: Any) -> None:
        return None

    def fill(self, value: str, **_kwargs: Any) -> None:
        self.page.password = value

    def click(self, **_kwargs: Any) -> None:
        if self.selector == "#login" and self.page.password == "managed-password":
            self.page.context.authenticated = True
            self.page.url = "https://target.test/home"
        elif self.selector == "#target" and not self.page.context.authenticated:
            raise RuntimeError("not authenticated")

    def is_visible(self, **_kwargs: Any) -> bool:
        return self.selector != "#target" or self.page.context.authenticated


class _Page:
    def __init__(self, context: _Context) -> None:
        self.context = context
        self.url = "about:blank"
        self.password = ""

    def goto(self, url: str, **_kwargs: Any) -> None:
        if url.endswith("/target") and not self.context.authenticated:
            self.url = "https://target.test/login"
        else:
            self.url = url

    def locator(self, selector: str) -> _Locator:
        return _Locator(self, selector)

    def close(self) -> None:
        return None


class _Context:
    def __init__(self, storage_state: object = None) -> None:
        self.authenticated = bool(
            isinstance(storage_state, dict) and storage_state.get("cookies")
        )
        self.page = _Page(self)

    def new_page(self) -> _Page:
        return self.page

    def storage_state(self) -> dict[str, object]:
        return {
            "cookies": [
                {
                    "name": "sid",
                    "value": "refreshed-cookie",
                    "domain": "target.test",
                    "path": "/",
                }
            ]
        }

    def close(self) -> None:
        return None


class _Browser:
    def new_context(self, **kwargs: Any) -> _Context:
        return _Context(kwargs.get("storage_state"))

    def close(self) -> None:
        return None


class _Playwright:
    chromium: _Playwright

    def __init__(self) -> None:
        self.chromium = self

    def __enter__(self) -> _Playwright:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def launch(self, **_kwargs: Any) -> _Browser:
        return _Browser()


def _locator(selector: str) -> WebLocatorPlan:
    return WebLocatorPlan(None, (WebLocatorCandidate("css", selector, 1),))


def test_expired_session_runs_fixed_login_once_and_returns_private_refresh() -> None:
    login_actions = (
        WebExecutionActionPlan(
            "FILL", 1_000, "STOP", None, _locator("#password"), "{{secret.PASSWORD}}", None
        ),
        WebExecutionActionPlan(
            "CLICK", 1_000, "STOP", None, _locator("#login"), None, None
        ),
    )
    recovery = WebSessionRecoveryPlan(
        31,
        32,
        True,
        WebSessionConditionPlan("URL_CONTAINS", "/login", None),
        WebSessionConditionPlan("URL_EQUALS", "https://target.test/home", None),
        "https://target.test/login",
        login_actions,
        (),
    )
    plan = WebExecutionPlanResult(
        1,
        "run-session",
        "message-session",
        "runner-session",
        10,
        11,
        12,
        "https://target.test/target",
        "CHROME",
        True,
        30_000,
        {},
        (
            WebExecutionActionPlan(
                "CLICK", 1_000, "STOP", None, _locator("#target"), None, None
            ),
        ),
        (),
        WebSessionPlan(21, None, 7, "a" * 64, recovery),
        (WebExecutionSecret("PASSWORD", "managed-password"),),
    )

    result = WebExecutor(playwright_factory=_Playwright).execute(plan)

    assert result.outcome == "SUCCESS"
    assert [item["node_id"] for item in result.traces] == ["action_1"]
    assert result.session_recovery == {
        "status": "SUCCESS",
        "reason": "PROFILE_EXPIRED",
        "login_web_case_version_id": 32,
        "duration_ms": result.session_recovery["duration_ms"],
        "error_type": None,
        "persistence_status": None,
    }
    assert result.refreshed_session is not None
    assert result.refreshed_session["expected_revision"] == 7
    assert result.refreshed_session["storage_state"]["cookies"][0]["value"] == "refreshed-cookie"
    assert "managed-password" not in repr(plan)
    assert "managed-password" not in repr(result)

    runtime_plan = replace(
        plan,
        session=replace(
            plan.session,
            storage_state={"cookies": []},
            recovery=replace(recovery, force_refresh=False),
        ),
    )
    runtime_result = WebExecutor(playwright_factory=_Playwright).execute(runtime_plan)
    assert runtime_result.outcome == "SUCCESS"
    assert runtime_result.session_recovery is not None
    assert runtime_result.session_recovery["reason"] == "EXPIRY_CONDITION"
