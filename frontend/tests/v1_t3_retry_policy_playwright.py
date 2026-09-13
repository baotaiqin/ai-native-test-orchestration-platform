"""Synthetic real-Chrome validation for V1-T3 retry-policy frontend gates.

The SPA is served from an isolated production build. Every API response is
intercepted in-process; no formal backend, database, AI, Runner, or real Run is
used.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Locator, Page, Route, async_playwright, expect

NOW = "2026-09-10T08:00:00Z"
RETRY_MESSAGE = "V1 API Step 自动重试最多一次；请将 max_retries 设为 0 或 1，并创建符合 V1 的新版本"


def case_content(max_retries: Any, title: str) -> dict[str, Any]:
    return {
        "title": title, "case_type": "API", "priority": "P1",
        "preconditions": ["tenant ready"],
        "steps": [{"order": 1, "action": "create", "expected": "201"}],
        "test_data": {"tenant": "alpha"}, "expected_result": "created",
        "tags": ["v1-t3"], "confidence": 0.91,
        "request": {
            "method": "POST", "url": "{{base_url}}/orders",
            "query_params": [{"name": "trace", "value": "{{trace}}", "enabled": True}],
            "headers": [{"name": "X-Test", "value": "safe", "enabled": True}],
            "cookies": [], "body": {"type": "JSON", "content": {"sku": "A"}},
            "auth": {"type": "NONE"}, "timeout_ms": 30000,
            "follow_redirects": True,
            "retry_policy": {
                "max_retries": max_retries, "backoff_ms": 700,
                "retry_on": ["TARGET_TIMEOUT", "HTTP_5XX"],
            },
        },
        "pre_actions": [{"type": "SET_VARIABLE", "name": "trace", "value": "t1", "enabled": True}],
        "post_actions": [{"type": "EXTRACT_RESPONSE", "name": "order_id", "source": "JSONPATH", "expression": "$.id", "required": True, "enabled": True}],
        "extractors": [{"name": "status", "source": "HEADER", "expression": "X-Status", "required": False, "enabled": True}],
        "data_source": {"dataset_id": 9, "dataset_version_id": 2, "prefix": "data", "column_mapping": {"sku": "request.sku"}},
        "assertions": [{"kind": "DETERMINISTIC", "type": "STATUS_CODE", "name": "created", "enabled": True, "source": "STATUS_CODE", "operator": "EQ", "expected": 201}],
        "cleanup": [{"cleanup_id": "order", "cleanup_type": "API", "policy": "ALWAYS", "enabled": True, "timeout_ms": 30000, "method": "DELETE", "url": "{{base_url}}/orders/{{order_id}}", "query_params": [], "headers": [], "cookies": [], "params": {}}],
        "vendor_extension": {"opaque": [1, {"keep": True}], "revision": "x7"},
    }


def version(case_id: int, version_no: int, max_retries: Any, title: str) -> dict[str, Any]:
    return {
        "id": case_id * 10 + version_no, "case_id": case_id,
        "version_no": version_no, "content": case_content(max_retries, title),
        "change_note": "historical fixture", "created_by": "mock", "created_at": NOW,
    }


def asset(case_id: int, max_retries: Any, title: str) -> dict[str, Any]:
    current = version(case_id, 3, max_retries, title)
    return {
        "id": case_id, "project_id": 1, "code": f"API-{case_id}", "name": title,
        "case_type": "API", "status": "ACTIVE", "source": "MANUAL",
        "current_version_id": current["id"], "created_by": "mock",
        "created_at": NOW, "updated_at": NOW, "current_version": current,
    }


def requirement(req_id: int, title: str) -> dict[str, Any]:
    current = {
        "id": req_id * 10, "requirement_id": req_id, "version_no": 1,
        "markdown_content": f"# {title}", "content_hash": "a" * 64,
        "source_type": "MANUAL", "source_filename": None,
        "change_summary": "fixture", "created_by": "mock", "created_at": NOW,
    }
    return {
        "id": req_id, "project_id": 1, "parent_id": None, "code": f"REQ-{req_id}",
        "title": title, "type": "FEATURE", "order_index": req_id,
        "status": "ACTIVE", "current_version_id": current["id"],
        "created_by": "mock", "created_at": NOW, "updated_at": NOW,
        "current_version": current, "children": [],
    }


def suggestion(suggestion_id: int, retries: Any, title: str) -> dict[str, Any]:
    return {
        "id": suggestion_id, "generation_id": 301, "sequence_no": suggestion_id - 500,
        "status": "DRAFT", "structured_result": case_content(retries, title),
        "human_result": None, "decision_note": None, "test_case_id": None,
        "reviewed_by": None, "reviewed_at": None,
    }


def run_item(run_id: str, status: str) -> dict[str, Any]:
    return {
        "id": run_id, "run_code": run_id.upper(), "run_type": "API_CASE",
        "project_id": 1, "environment_id": 21, "runner_id": "runner-1",
        "case_id": 11, "case_version_id": 113, "scenario_id": None,
        "scenario_version_id": None, "web_case_id": None, "web_case_version_id": None,
        "status": status, "trigger_type": "MANUAL", "required_capabilities": ["API"],
        "required_tags": [], "required_slot_type": "API", "required_slot_count": 1,
        "runtime_snapshot_id": None, "total_timeout_ms": None,
        "effective_total_timeout_ms": 300000, "force_stop_requested_at": None,
        "force_stopped": False, "started_at": NOW if status == "SUCCESS" else None,
        "ended_at": NOW if status == "SUCCESS" else None, "total": 1,
        "pass": 1 if status == "SUCCESS" else 0, "fail": 0, "review": 0,
        "timeout": 0, "error_type": None, "error_message": None,
        "created_by": "mock", "created_at": NOW, "updated_at": NOW,
    }


class State:
    def __init__(self) -> None:
        self.assets = [
            asset(11, 3, "Historical retry 3"), asset(12, 1, "V1 retry 1"),
            asset(13, "1", "Historical string retry"), asset(14, True, "Historical bool retry"),
            asset(15, -1, "Historical negative retry"),
        ]
        self.requirements = [requirement(101, "Checkout"), requirement(102, "Refund")]
        self.suggestions = [
            suggestion(501, 3, "Preserve full payload"), suggestion(502, 1, "Switch target"),
            suggestion(503, 0, "Requirement switch"), suggestion(504, -1, "Bulk invalid"),
            suggestion(505, 1, "Backend conflict"), suggestion(506, 0, "Bulk valid"),
            suggestion(507, 3, "Reject legacy draft"),
            suggestion(508, 1, "Unmount lifecycle"), suggestion(509, 1, "Late error lifecycle"),
            suggestion(510, 3, "Reject invalid steps"), suggestion(511, 3, "Reject invalid data"),
            suggestion(512, 1, "Requirement ABA"),
        ]
        self.test_case_writes: list[dict[str, Any]] = []
        self.patch_writes: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.bulk_writes: list[dict[str, Any]] = []
        self.validate_calls = 0
        self.create_run_calls = 0
        self.cancel_calls = 0
        self.unknown: list[str] = []
        self.delay_patch_id: int | None = None
        self.patch_error_ids: set[int] = set()
        self.patch_started = asyncio.Event()
        self.patch_release = asyncio.Event()


async def json_response(route: Route, body: Any, status: int = 200) -> None:
    await route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


def request_body(route: Route) -> dict[str, Any]:
    return json.loads(route.request.post_data or "{}")


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    project = {"id": 1, "name": "Synthetic V1-T3", "code": "V1T3", "description": None, "status": "ACTIVE", "owner_id": "mock", "created_at": NOW, "updated_at": NOW, "archived_at": None}
    if (method, path) == ("GET", "/api/v1/auth/me"):
        return await json_response(route, {"id": "mock", "username": "mock", "display_name": "Mock", "roles": ["TESTER"]})
    if (method, path) == ("GET", "/api/v1/projects"):
        return await json_response(route, {"items": [project], "total": 1})
    if (method, path) == ("GET", "/api/v1/test-cases"):
        return await json_response(route, state.assets)
    if method == "GET" and path.startswith("/api/v1/test-cases/") and path.endswith("/versions"):
        case_id = int(path.split("/")[4])
        found = next(item for item in state.assets if item["id"] == case_id)
        return await json_response(route, [found["current_version"]])
    if method == "GET" and path.startswith("/api/v1/test-cases/") and path.count("/") == 4:
        case_id = int(path.split("/")[4])
        return await json_response(route, next(item for item in state.assets if item["id"] == case_id))
    if (method, path) == ("POST", "/api/v1/test-cases"):
        body = request_body(route); state.test_case_writes.append({"path": path, "body": body})
        created = asset(90 + len(state.test_case_writes), body["content"]["request"]["retry_policy"]["max_retries"], body["content"]["title"])
        return await json_response(route, created)
    if method == "POST" and path.startswith("/api/v1/test-cases/") and path.endswith("/versions"):
        body = request_body(route); state.test_case_writes.append({"path": path, "body": body})
        if len([item for item in state.test_case_writes if item["path"] == path]) > 1:
            return await json_response(route, {"detail": {"code": "API_STEP_RETRY_LIMIT_EXCEEDED", "message": RETRY_MESSAGE}}, 409)
        case_id = int(path.split("/")[4])
        return await json_response(route, {"id": 999, "case_id": case_id, "version_no": 4, "content": body["content"], "change_note": body.get("change_note"), "created_by": "mock", "created_at": NOW})
    if (method, path) == ("GET", "/api/v1/datasets"):
        return await json_response(route, {"items": [], "total": 0})
    if (method, path) == ("GET", "/api/v1/prompt-center"):
        return await json_response(route, {"items": []})

    if (method, path) == ("GET", "/api/v1/requirements"):
        return await json_response(route, {"items": state.requirements})
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/versions"):
        req_id = int(path.split("/")[4]); req = next(item for item in state.requirements if item["id"] == req_id)
        return await json_response(route, [req["current_version"]])
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.count("/") == 4:
        req_id = int(path.split("/")[4]); return await json_response(route, next(item for item in state.requirements if item["id"] == req_id))
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/ai-reviews"):
        return await json_response(route, {"items": []})
    if method == "GET" and path.startswith("/api/v1/test-cases/requirements/") and path.endswith("/generations"):
        req_id = int(path.split("/")[5])
        items = [] if req_id == 102 else [{
            "id": 301, "project_id": 1, "requirement_id": 101,
            "requirement_version_id": 1010, "ai_call_id": 77,
            "additional_instructions": None, "raw_response": "synthetic",
            "structured_result": {"cases": [item["structured_result"] for item in state.suggestions]},
            "created_by": "mock", "created_at": NOW, "suggestions": state.suggestions,
        }]
        return await json_response(route, {"items": items})
    if method == "GET" and path.startswith("/api/v1/test-cases/requirements/") and path.endswith("/links"):
        return await json_response(route, [])
    if method == "PATCH" and path.startswith("/api/v1/test-cases/suggestions/"):
        suggestion_id = int(path.split("/")[5]); body = request_body(route)
        state.patch_writes.append({"id": suggestion_id, "body": body})
        if state.delay_patch_id == suggestion_id:
            state.patch_started.set(); await asyncio.wait_for(state.patch_release.wait(), 10)
            state.delay_patch_id = None; state.patch_release.clear(); state.patch_started.clear()
        if suggestion_id in state.patch_error_ids:
            return await json_response(route, {"detail": {"code": "API_STEP_RETRY_LIMIT_EXCEEDED", "message": RETRY_MESSAGE}}, 409)
        item = next(value for value in state.suggestions if value["id"] == suggestion_id)
        item["human_result"] = body["human_result"]; item["decision_note"] = body.get("decision_note")
        return await json_response(route, item)
    if method == "POST" and path.startswith("/api/v1/test-cases/suggestions/") and path.endswith("/decision"):
        suggestion_id = int(path.split("/")[5]); body = request_body(route)
        state.decisions.append({"id": suggestion_id, "body": body})
        if suggestion_id == 505:
            return await json_response(route, {"detail": {"code": "API_STEP_RETRY_LIMIT_EXCEEDED", "message": RETRY_MESSAGE}}, 409)
        item = next(value for value in state.suggestions if value["id"] == suggestion_id)
        item["status"] = "ACCEPTED" if body["action"] == "ACCEPT" else "REJECTED"
        return await json_response(route, item)
    if (method, path) == ("POST", "/api/v1/test-cases/suggestions/bulk-decision"):
        body = request_body(route); state.bulk_writes.append(body)
        for suggestion_id in body["suggestion_ids"]:
            item = next(value for value in state.suggestions if value["id"] == suggestion_id)
            item["status"] = "ACCEPTED" if body["action"] == "ACCEPT" else "REJECTED"
        return await json_response(route, [item for item in state.suggestions if item["id"] in body["suggestion_ids"]])

    if (method, path) == ("GET", "/api/v1/environments"):
        return await json_response(route, {"items": [{"id": 21, "project_id": 1, "name": "Synthetic", "code": "SYN", "base_url": None, "description": None, "is_default": True, "enabled": True, "created_at": NOW, "updated_at": NOW}]})
    if (method, path) == ("GET", "/api/v1/scenarios"):
        return await json_response(route, {"items": []})
    if (method, path) == ("GET", "/api/v1/web-cases"):
        return await json_response(route, {"items": [], "total": 0})
    if (method, path) == ("GET", "/api/v1/session-profiles"):
        return await json_response(route, [])
    if (method, path) == ("GET", "/api/v1/runners"):
        runner = {"id": "runner-1", "name": "Synthetic Runner", "hostname": "local", "ip_address": None, "os": "mock", "cpu": None, "ram": None, "disk": None, "python": None, "chrome": None, "playwright": None, "java": None, "jmeter": None, "status": "ACTIVE", "online": True, "online_status": "ONLINE", "redis_available": True, "heartbeat_interval_seconds": 10, "last_heartbeat_at": NOW, "revoked_at": None, "created_at": NOW, "updated_at": NOW, "tags": [], "capabilities": [{"name": "API", "status": "READY", "reason": None}], "slots": [{"type": "API", "total": 1, "available": 1}]}
        return await json_response(route, {"items": [runner], "total": 1})
    if (method, path) == ("GET", "/api/v1/runs"):
        items = [run_item("run-old-created", "CREATED"), run_item("run-old-success", "SUCCESS")]
        return await json_response(route, {"items": items, "total": 2, "page": 1, "page_size": 10})
    if (method, path) == ("POST", "/api/v1/runs/validate"):
        state.validate_calls += 1
        return await json_response(route, {"valid": True, "issues": [], "run_type": "API_CASE", "resolved_case_version_id": 123, "resolved_scenario_version_id": None, "resolved_web_case_version_id": None, "required_capabilities": ["API"]})
    if (method, path) == ("POST", "/api/v1/runs"):
        state.create_run_calls += 1
        return await json_response(route, {"detail": {"code": "RUN_VALIDATION_FAILED", "message": "运行校验未通过", "details": [{"code": "API_STEP_RETRY_LIMIT_EXCEEDED", "message": RETRY_MESSAGE, "field": "case_version.content.request.retry_policy.max_retries"}]}}, 409)
    if (method, path) == ("POST", "/api/v1/runs/run-old-created/cancel"):
        state.cancel_calls += 1
        detail = run_item("run-old-created", "CANCELLED"); detail.update({"case_runs": [], "web_traces": []})
        return await json_response(route, detail)
    if path.endswith("/events/stream"):
        return await json_response(route, {"message": "synthetic stream unavailable"}, 503)

    state.unknown.append(f"{method} {path}")
    await json_response(route, {"message": "unhandled synthetic endpoint"}, 404)


class QuietSpaHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        candidate = Path(self.directory or ".") / path.lstrip("/")
        if path != "/" and not candidate.exists() and "." not in Path(path).name:
            self.path = "/index.html"
        super().do_GET()


async def choose_select(select: Locator, option_name: str) -> None:
    await select.click()
    await select.page.get_by_role("option", name=option_name, exact=True).click()


async def wait_until(predicate: Any, timeout_seconds: float = 5) -> None:
    for _ in range(max(1, int(timeout_seconds * 20))):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("synthetic state condition did not become true")


def form_item(container: Page | Locator, label: str) -> Locator:
    return container.locator(".el-form-item").filter(has_text=label).first


async def open_editor_step(dialog: Locator, title: str) -> None:
    step = dialog.locator(".case-editor-nav-item").filter(has_text=title)
    await step.click()
    await expect(step).to_have_attribute("aria-current", "step")


async def validate_test_case_editor(page: Page, state: State, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await page.goto(f"{base_url}/test-cases")
    await expect(page.get_by_role("heading", name="测试用例", exact=True)).to_be_visible()
    create_button = page.get_by_role("button", name="新建用例", exact=True)
    initial_writes = len(state.test_case_writes)
    await create_button.click(); await page.locator(".el-dialog:visible").get_by_role("button", name="取消", exact=True).click()
    assert len(state.test_case_writes) == initial_writes
    checks.append("new-case open/cancel performs zero writes")

    for retry_value, title in [(0, "New retry zero"), (1, "New retry one")]:
        await create_button.click()
        dialog = page.locator(".el-dialog:visible")
        await form_item(dialog, "用例名称").locator("input").fill(title)
        await open_editor_step(dialog, "结果与说明")
        await form_item(dialog, "预期结果").locator("textarea").fill("ok")
        await open_editor_step(dialog, "请求配置")
        retry_select = form_item(dialog, "最大额外重试次数").locator(".el-select")
        await choose_select(retry_select, "0 · 不重试" if retry_value == 0 else "1 · 最多额外一次（总尝试最多两次）")
        if retry_value == 0:
            await open_editor_step(dialog, "数据与断言")
            await dialog.get_by_role("button", name="添加断言", exact=True).click()
            assertion = dialog.locator(".assertion-card").first
            await choose_select(assertion.locator(".el-select").first, "JSONPath Equal")
            await form_item(assertion, "名称").locator("input").fill("service_name")
            await form_item(assertion, "表达式").locator("input").fill("$.service")
            await form_item(assertion, "Expected（期望值）").locator("textarea").fill("v1-demo")
            await page.screenshot(path=str(out_dir / "test-case-step-editor-assertion.png"))
        await dialog.get_by_role("button", name="创建 V1", exact=True).click()
        await expect(dialog).not_to_be_visible()
    new_values = [item["body"]["content"]["request"]["retry_policy"]["max_retries"] for item in state.test_case_writes[:2]]
    assert new_values == [0, 1]
    assert state.test_case_writes[0]["body"]["content"]["assertions"][0]["expected"] == "v1-demo"
    checks.append("step navigation separates form sections and assertion expected updates while typing")
    checks.append("new API cases write exact numeric retry values 0 and 1")

    writes_before_historical_cancel = len(state.test_case_writes)
    await page.locator(".el-table__row").filter(has_text="Historical retry 3").click()
    historical_drawer = page.locator(".el-drawer:visible").filter(has_text="测试用例详情与版本")
    await historical_drawer.get_by_role("button", name="创建新版本").click()
    historical_dialog = page.locator(".el-dialog:visible")
    await open_editor_step(historical_dialog, "请求配置")
    await expect(historical_dialog.get_by_text("历史值 3", exact=False).first).to_be_visible()
    await historical_dialog.get_by_role("button", name="取消", exact=True).click()
    await historical_drawer.locator(".el-drawer__close-btn").click()
    await expect(page.locator(".el-drawer:visible")).to_have_count(0)
    assert len(state.test_case_writes) == writes_before_historical_cancel
    checks.append("historical retry 3 open/cancel performs zero writes")

    invalid_cases = [
        ("Historical retry 3", "3"), ("Historical string retry", "1"),
        ("Historical bool retry", "true"), ("Historical negative retry", "-1"),
    ]
    for title, expected_value in invalid_cases:
        await page.locator(".el-table__row").filter(has_text=title).click()
        drawer = page.locator(".el-drawer:visible").filter(has_text="测试用例详情与版本")
        await drawer.get_by_role("button", name="创建新版本").click()
        dialog = page.locator(".el-dialog:visible")
        await open_editor_step(dialog, "请求配置")
        await expect(dialog.get_by_text(f"历史值 {expected_value}", exact=False).first).to_be_visible()
        await expect(dialog.get_by_role("button", name="创建新版本", exact=True)).to_be_disabled()
        if title == "Historical retry 3":
            await dialog.get_by_text("V1 只允许 0 或 1", exact=False).scroll_into_view_if_needed()
            await page.screenshot(path=str(out_dir / "test-case-historical-retry-gate.png"))
            await choose_select(form_item(dialog, "最大额外重试次数").locator(".el-select"), "1 · 最多额外一次（总尝试最多两次）")
            await open_editor_step(dialog, "结果与说明")
            await form_item(dialog, "版本变更说明（必填）").locator("input").fill("显式修正为 V1")
            await dialog.get_by_role("button", name="创建新版本", exact=True).click()
            await expect(dialog).not_to_be_visible()
        else:
            await dialog.get_by_role("button", name="取消", exact=True).click()
        await page.locator(".el-drawer__close-btn:visible").click()
    assert state.assets[0]["current_version"]["content"]["request"]["retry_policy"]["max_retries"] == 3
    version_write = next(item for item in state.test_case_writes if item["path"] == "/api/v1/test-cases/11/versions")
    assert version_write["body"]["content"]["request"]["retry_policy"]["max_retries"] == 1
    checks.append("historical 3/string/bool/negative values remain readable and require explicit 0/1 correction")

    await page.locator(".el-table__row").filter(has_text="Historical retry 3").click()
    drawer = page.locator(".el-drawer:visible").filter(has_text="测试用例详情与版本")
    await drawer.get_by_role("button", name="创建新版本").click()
    dialog = page.locator(".el-dialog:visible")
    await open_editor_step(dialog, "请求配置")
    await choose_select(form_item(dialog, "最大额外重试次数").locator(".el-select"), "1 · 最多额外一次（总尝试最多两次）")
    await open_editor_step(dialog, "结果与说明")
    await form_item(dialog, "版本变更说明（必填）").locator("input").fill("server conflict")
    await dialog.get_by_role("button", name="创建新版本", exact=True).click()
    await expect(page.get_by_text(RETRY_MESSAGE, exact=True)).to_be_visible()
    checks.append("test-case save displays backend retry-limit 409 verbatim")
    await dialog.get_by_role("button", name="取消", exact=True).click(); await drawer.locator(".el-drawer__close-btn").click()


async def open_suggestion(page: Page, title: str) -> Locator:
    card = page.locator(".case-suggestion-card").filter(has_text=title)
    await card.locator("button").click(force=True)
    drawer = page.locator(".el-drawer:visible").filter(has_text="AI 用例建议审核")
    await expect(drawer).to_be_visible()
    return drawer


async def open_requirement_suggestions(page: Page, base_url: str) -> None:
    await page.goto(f"{base_url}/requirements")
    await expect(page.get_by_role("heading", name="需求管理")).to_be_visible()
    await page.get_by_role("tab", name="AI 用例").click()
    await expect(page.locator(".case-suggestion-card").first).to_be_visible()


async def validate_suggestions(page: Page, state: State, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await open_requirement_suggestions(page, base_url)
    await expect(page.locator(".case-suggestion-card").filter(has_text="Preserve full payload")).to_be_visible()

    original = copy.deepcopy(state.suggestions[0]["structured_result"])
    drawer = await open_suggestion(page, "Preserve full payload")
    await expect(drawer.get_by_text("历史值 3 · 不可接受", exact=True)).to_be_visible()
    await expect(drawer.get_by_role("button", name="Accept 并生成正式用例")).to_be_disabled()
    await choose_select(form_item(drawer, "最大额外重试次数").locator(".el-select"), "1 · 最多额外一次（总尝试最多两次）")
    state.delay_patch_id = 501
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await asyncio.wait_for(state.patch_started.wait(), 5)
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click(force=True)
    await page.locator(".case-suggestion-card").filter(has_text="Switch target").locator("button").evaluate("element => element.click()")
    await expect(page.locator(".el-drawer:visible").get_by_text("建议 #502", exact=False)).to_be_visible()
    state.patch_release.set()
    await expect(page.locator(".el-drawer:visible").filter(has_text="AI 用例建议审核").get_by_text("建议 #502", exact=False)).to_be_visible()
    await wait_until(lambda: len([item for item in state.decisions if item["id"] == 501]) == 1)
    assert len([item for item in state.patch_writes if item["id"] == 501]) == 1
    saved = next(item["body"]["human_result"] for item in state.patch_writes if item["id"] == 501)
    expected = copy.deepcopy(original); expected["request"]["retry_policy"]["max_retries"] = 1
    assert saved == expected, "full suggestion payload was not preserved exactly"
    checks.append("save-then-accept preserves full payload, locks original id, and suppresses duplicate clicks")
    await page.screenshot(path=str(out_dir / "suggestion-payload-and-id-lock.png"), full_page=True)
    await page.locator(".el-drawer__close-btn:visible").click()
    await expect(page.locator(".el-drawer:visible")).to_have_count(0)

    drawer = await open_suggestion(page, "Requirement switch")
    state.delay_patch_id = 503
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await asyncio.wait_for(state.patch_started.wait(), 5)
    await page.locator(".el-tree-node").filter(has_text="Refund").locator(".el-tree-node__content").evaluate("element => element.click()")
    await expect(page.get_by_role("heading", name="Refund")).to_be_visible()
    state.patch_release.set(); await page.wait_for_timeout(350)
    assert not any(item["id"] == 503 for item in state.decisions)
    checks.append("requirement switch after delayed save aborts accept decision")

    await page.locator(".el-tree-node").filter(has_text="Checkout").locator(".el-tree-node__content").click()
    await page.get_by_role("tab", name="AI 用例").click()
    await expect(page.locator(".case-suggestion-card").filter(has_text="Bulk invalid")).to_be_visible()
    for title in ("Bulk invalid", "Bulk valid"):
        await page.locator(".case-suggestion-card").filter(has_text=title).locator(".el-checkbox").click()
    await page.get_by_role("button", name="批量接受").click()
    await expect(page.get_by_text("未发送批量请求：建议 #504", exact=False)).to_be_visible()
    assert state.bulk_writes == []
    await page.locator(".case-suggestion-card").filter(has_text="Bulk invalid").locator(".el-checkbox").click()
    await page.get_by_role("button", name="批量接受").click()
    await wait_until(lambda: len(state.bulk_writes) == 1)
    assert state.bulk_writes[0]["suggestion_ids"] == [506]
    checks.append("bulk accept is atomic client-side and sends one request only when all selected drafts are valid")

    await page.locator(".case-suggestion-card").filter(has_text="Bulk invalid").locator(".el-checkbox").click()
    await page.get_by_role("button", name="批量拒绝").click()
    await wait_until(lambda: len(state.bulk_writes) == 2)
    assert state.bulk_writes[1]["action"] == "REJECT" and state.bulk_writes[1]["suggestion_ids"] == [504]
    checks.append("bulk reject remains available for a legacy retry draft")

    drawer = await open_suggestion(page, "Backend conflict")
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await expect(page.get_by_text(RETRY_MESSAGE, exact=True)).to_be_visible()
    checks.append("single suggestion accept displays backend retry-limit 409 verbatim")
    await drawer.locator(".el-drawer__close-btn").click(); await expect(page.locator(".el-drawer:visible")).to_have_count(0)

    drawer = await open_suggestion(page, "Reject legacy draft")
    await drawer.get_by_role("button", name="Reject", exact=True).click()
    await wait_until(lambda: any(item["id"] == 507 and item["body"]["action"] == "REJECT" for item in state.decisions))
    assert not any(item["id"] == 507 for item in state.patch_writes)
    checks.append("single reject remains available without saving or accepting a legacy retry draft")

    await open_requirement_suggestions(page, base_url)
    drawer = await open_suggestion(page, "Requirement ABA")
    state.delay_patch_id = 512
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await asyncio.wait_for(state.patch_started.wait(), 5)
    await page.locator(".el-tree-node").filter(has_text="Refund").locator(".el-tree-node__content").evaluate("element => element.click()")
    await expect(page.get_by_role("heading", name="Refund")).to_be_visible()
    await page.locator(".el-tree-node").filter(has_text="Checkout").locator(".el-tree-node__content").evaluate("element => element.click()")
    await expect(page.get_by_role("heading", name="Checkout")).to_be_visible()
    state.patch_release.set(); await page.wait_for_timeout(350)
    assert not any(item["id"] == 512 for item in state.decisions)
    assert await page.get_by_text("已生成正式 Test Case V1", exact=True).count() == 0
    checks.append("requirement A-to-B-to-A changes lifecycle generation and aborts late accept")

    await page.get_by_role("tab", name="AI 用例").click()
    drawer = await open_suggestion(page, "Unmount lifecycle")
    state.delay_patch_id = 508
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await asyncio.wait_for(state.patch_started.wait(), 5)
    await page.keyboard.press("Escape"); await expect(drawer).not_to_be_visible()
    await page.get_by_role("link", name="项目管理", exact=True).click()
    await page.wait_for_url("**/projects")
    state.patch_release.set(); await page.wait_for_timeout(350)
    assert not any(item["id"] == 508 for item in state.decisions)
    assert await page.get_by_text("已生成正式 Test Case V1", exact=True).count() == 0
    checks.append("component unmount invalidates late save without decision, refresh, or stale success message")

    await open_requirement_suggestions(page, base_url)
    drawer = await open_suggestion(page, "Late error lifecycle")
    state.delay_patch_id = 509; state.patch_error_ids.add(509)
    await drawer.get_by_role("button", name="Accept 并生成正式用例").click()
    await asyncio.wait_for(state.patch_started.wait(), 5)
    await page.keyboard.press("Escape"); await expect(drawer).not_to_be_visible()
    await page.get_by_role("link", name="项目管理", exact=True).click()
    await page.wait_for_url("**/projects")
    state.patch_release.set(); await page.wait_for_timeout(350)
    assert not any(item["id"] == 509 for item in state.decisions)
    assert await page.get_by_text(RETRY_MESSAGE, exact=True).count() == 0
    checks.append("late backend error after unmount stays silent and cannot continue accept")

    await open_requirement_suggestions(page, base_url)
    drawer = await open_suggestion(page, "Reject invalid steps")
    await form_item(drawer, "步骤 JSON").locator("textarea").fill("{invalid unsaved steps")
    await page.wait_for_timeout(350)
    await page.screenshot(path=str(out_dir / "suggestion-reject-invalid-json.png"))
    await drawer.get_by_role("button", name="Reject", exact=True).click()
    await wait_until(lambda: any(item["id"] == 510 and item["body"]["action"] == "REJECT" for item in state.decisions))
    assert not any(item["id"] == 510 for item in state.patch_writes)
    await expect(page.locator(".el-drawer:visible")).to_have_count(0)

    drawer = await open_suggestion(page, "Reject invalid data")
    await form_item(drawer, "测试数据 JSON").locator("textarea").fill("[invalid unsaved data")
    await drawer.get_by_role("button", name="Reject", exact=True).click()
    await wait_until(lambda: any(item["id"] == 511 and item["body"]["action"] == "REJECT" for item in state.decisions))
    assert not any(item["id"] == 511 for item in state.patch_writes)
    checks.append("reject ignores unsaved invalid steps and test-data JSON and sends no patch")


async def validate_runs(page: Page, state: State, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await page.goto(f"{base_url}/runs")
    await expect(page.get_by_role("heading", name="运行中心")).to_be_visible()
    await expect(page.get_by_text("当前 V3 的值为 3", exact=False)).to_be_visible()
    create_button = page.get_by_role("button", name="校验并创建")
    await expect(create_button).to_be_disabled()
    assert state.validate_calls == 0 and state.create_run_calls == 0
    await page.screenshot(path=str(out_dir / "run-center-historical-retry-gate.png"), full_page=True)
    checks.append("historical retry 3 current version blocks new API run before validate/create")

    case_select = page.locator(".resource-card").filter(has_text="API CASE").locator(".el-select")
    await choose_select(case_select, "V1 retry 1（API-12）")
    await expect(create_button).to_be_enabled()
    await create_button.click()
    await expect(page.get_by_text(RETRY_MESSAGE, exact=True)).to_be_visible()
    assert state.validate_calls == 1 and state.create_run_calls == 1
    checks.append("valid retry 1 reaches backend validate; nested RUN_VALIDATION_FAILED surfaces inner retry issue")

    await expect(page.get_by_text("RUN-OLD-SUCCESS", exact=True)).to_be_visible()
    created_row = page.locator(".el-table__row").filter(has_text="RUN-OLD-CREATED")
    await created_row.get_by_role("button", name="取消", exact=True).click()
    await page.get_by_role("button", name="确认取消", exact=True).click()
    await expect(page.get_by_text("Run 已取消", exact=True)).to_be_visible()
    assert state.cancel_calls == 1
    checks.append("historical completed run remains readable and historical created run remains cancellable")


async def run_browser(base_url: str, out_dir: Path, *, case_editor_only: bool = False) -> dict[str, Any]:
    state, checks, page_errors, console_errors = State(), [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1050})
        await context.add_init_script("""
          localStorage.setItem('auth_identity_epoch', 'synthetic-v1-t3-epoch');
          localStorage.setItem('access_token', 'synthetic-v1-t3-token');
          localStorage.setItem('current_user', JSON.stringify({id:'mock',username:'mock',display_name:'Mock',roles:['TESTER'],__identity_epoch:'synthetic-v1-t3-epoch'}));
        """)
        page = await context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        await page.route("**/api/v1/**", lambda route: handle_api(state, route))
        await validate_test_case_editor(page, state, base_url, out_dir, checks)
        if not case_editor_only:
            await validate_suggestions(page, state, base_url, out_dir, checks)
            await validate_runs(page, state, base_url, out_dir, checks)
        await context.close(); await browser.close()
    assert not state.unknown, f"unhandled API requests: {state.unknown}"
    assert not page_errors, f"page errors: {page_errors}"
    expected_http_errors = [message for message in console_errors if message.startswith("Failed to load resource: the server responded with a status of 409")]
    unexpected_console_errors = [message for message in console_errors if message not in expected_http_errors]
    assert not unexpected_console_errors, f"unexpected console errors: {unexpected_console_errors}"
    return {
        "mode": "synthetic-real-chrome", "formal_services_used": False,
        "checks": checks, "test_case_writes": len(state.test_case_writes),
        "suggestion_patch_writes": len(state.patch_writes),
        "suggestion_decisions": len(state.decisions), "bulk_writes": len(state.bulk_writes),
        "run_validate_calls": state.validate_calls, "run_create_calls": state.create_run_calls,
        "historical_run_cancel_calls": state.cancel_calls, "page_errors": 0,
        "unexpected_console_errors": 0,
        "screenshots": sorted(path.name for path in out_dir.glob("*.png")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--dist", type=Path, default=root / ".codex-validation" / "v1-t3" / "dist")
    parser.add_argument("--out", type=Path, default=root / ".codex-validation" / "v1-t3")
    parser.add_argument("--case-editor-only", action="store_true")
    args = parser.parse_args()
    out_dir = args.out.resolve(); out_dir.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietSpaHandler, directory=str(args.dist.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = asyncio.run(run_browser(
            f"http://127.0.0.1:{server.server_port}",
            out_dir,
            case_editor_only=args.case_editor_only,
        ))
        (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    main()
