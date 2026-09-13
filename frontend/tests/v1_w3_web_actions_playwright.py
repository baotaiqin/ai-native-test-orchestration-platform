"""Synthetic real-Chrome acceptance for V1-W3 expanded WebCase actions.

The production SPA build is served on a random loopback port and all API calls
are intercepted. No formal service, database, Runner, AI provider, or Run is used.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route, async_playwright, expect


NOW = "2026-09-10T09:00:00Z"
NEW_PAGE_ACTIONS = ["RELOAD", "BACK", "FORWARD", "WAIT_NETWORK_IDLE"]
NEW_ELEMENT_ACTIONS = [
    "DOUBLE_CLICK", "RIGHT_CLICK", "CLEAR", "HOVER", "CHECK",
    "UNCHECK", "RADIO", "ENTER", "TAB",
]
NEW_ACTIONS = NEW_PAGE_ACTIONS + NEW_ELEMENT_ACTIONS
ALL_ACTIONS = [
    "GOTO", "RELOAD", "BACK", "FORWARD", "FILL", "CLICK", "DOUBLE_CLICK",
    "RIGHT_CLICK", "CLEAR", "HOVER", "SELECT", "CHECK", "UNCHECK", "RADIO",
    "PRESS", "ENTER", "TAB", "WAIT_ELEMENT", "WAIT_URL", "WAIT_NETWORK_IDLE",
]


def direct_locator(value: str) -> dict[str, Any]:
    return {"strategy": "css", "value": value, "element_version_id": None}


ASSERTIONS = [
    {"type": "ASSERT_VISIBLE", "locator": direct_locator("#visible"), "timeout_ms": 100},
    {"type": "ASSERT_TEXT", "locator": {"strategy": None, "value": None, "element_version_id": 501}, "expected": "完成", "timeout_ms": 600000},
    {"type": "ASSERT_URL", "expected": "https://synthetic.invalid/done", "timeout_ms": 30000},
]


LEGACY_ACTIONS = [
    {"type": "GOTO", "url": "https://synthetic.invalid/start", "timeout_ms": 101, "failure_policy": "STOP"},
    {"type": "FILL", "locator": direct_locator("#name"), "value": "plain-value", "timeout_ms": 102, "failure_policy": "CONTINUE"},
    {"type": "CLICK", "locator": {"strategy": None, "value": None, "element_version_id": 501}, "timeout_ms": 103, "failure_policy": "STOP"},
    {"type": "SELECT", "locator": direct_locator("#choice"), "value": "one", "timeout_ms": 104, "failure_policy": "CONTINUE"},
    {"type": "PRESS", "locator": direct_locator("#press"), "key": "Escape", "timeout_ms": 105, "failure_policy": "STOP"},
    {"type": "WAIT_ELEMENT", "locator": direct_locator("#ready"), "timeout_ms": 106, "failure_policy": "CONTINUE"},
    {"type": "WAIT_URL", "url": "https://synthetic.invalid/done", "timeout_ms": 107, "failure_policy": "STOP"},
]


def content(actions: list[dict[str, Any]], session_profile_id: int | None = 21) -> dict[str, Any]:
    return {
        "start_url": "https://synthetic.invalid/start",
        "natural_language_steps": [f"步骤 {index + 1}" for index in range(len(actions))],
        "actions": copy.deepcopy(actions),
        "assertions": copy.deepcopy(ASSERTIONS),
        "session_profile_id": session_profile_id,
        "browser": "CHROME",
        "headless": True,
        "total_timeout_ms": 900000,
        "parameters": {"suite": "v1-w3"},
    }


def case_item(case_id: int, name: str, status: str = "DRAFT", current_version_id: int | None = None) -> dict[str, Any]:
    return {
        "id": case_id, "project_id": 1, "code": f"WEB-{case_id}", "name": name,
        "status": status, "current_version_id": current_version_id,
        "created_by": "synthetic", "created_at": NOW, "updated_at": NOW,
    }


def case_version(case_id: int, version_id: int, version_no: int, payload: dict[str, Any], status: str = "DRAFT") -> dict[str, Any]:
    return {
        "id": version_id, "web_case_id": case_id, "version_no": version_no,
        "content": copy.deepcopy(payload), "change_note": f"V{version_no}", "status": status,
        "approved_by": "synthetic" if status == "APPROVED" else None,
        "approved_at": NOW if status == "APPROVED" else None,
        "created_by": "synthetic", "created_at": NOW,
    }


def action_payloads(action_types: list[str]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for index, action_type in enumerate(action_types):
        base: dict[str, Any] = {
            "type": action_type,
            "timeout_ms": 100 if index == 0 else 600000 if index == 1 else 1000 + index,
            "failure_policy": "STOP" if index % 2 == 0 else "CONTINUE",
        }
        if action_type in NEW_ELEMENT_ACTIONS:
            base["locator"] = (
                {"strategy": None, "value": None, "element_version_id": 501}
                if action_type == "DOUBLE_CLICK"
                else direct_locator(f"#target-{action_type.lower()}")
            )
        actions.append(base)
    return actions


def new_action_payloads() -> list[dict[str, Any]]:
    return action_payloads(NEW_ACTIONS)


def recording_item(recording_id: str) -> dict[str, Any]:
    return {
        "id": recording_id, "project_id": 1, "runner_id": "runner-synthetic",
        "environment_id": None, "session_profile_id": 21, "saved_session_profile_id": 22,
        "start_url": "https://synthetic.invalid/start", "browser": "CHROME",
        "status": "COMPLETED", "dispatch_status": "PUBLISHED", "message_id": f"msg-{recording_id}",
        "event_count": 0, "save_session": False, "confirmed_web_case_id": None,
        "confirmed_web_case_version_id": None, "created_by": "synthetic",
        "created_at": NOW, "updated_at": NOW,
    }


def recording_detail(recording_id: str) -> dict[str, Any]:
    return {
        **recording_item(recording_id), "events": [], "dom_context_available": False,
        "stop_requested_at": None, "cancel_requested_at": None, "started_at": NOW,
        "completed_at": NOW, "error_type": None, "error_message": None,
        "confirmed_by": None, "confirmed_at": None,
    }


def suggestion(suggestion_id: int, recording_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1, "id": suggestion_id, "recording_id": recording_id,
        "project_id": 1, "status": "DRAFT", "ai_call_id": 10 + suggestion_id,
        "actual_model": "synthetic-model", "prompt_version_id": 31,
        "output_schema_id": 41, "fallback_used": False, "repair_used": False,
        "source_snapshot_sha256": "a" * 64, "source_snapshot_size": 128,
        "additional_instructions": None,
        "structured_result": {
            "suggested_name": f"AI candidate {suggestion_id}", "summary": "合成建议",
            "steps": [{"source_event_sequence": 1, "natural_language_step": "打开页面", "action": "GOTO", "locator": None, "element_name": None, "reason": None}],
            "assertions": [], "warnings": [],
        },
        "canonical_suggested_content": copy.deepcopy(candidate), "human_content": None,
        "decision_note": None, "confirmed_web_case_id": None,
        "confirmed_web_case_version_id": None, "created_by": "synthetic",
        "reviewed_by": None, "created_at": NOW, "reviewed_at": None, "idempotent": False,
    }


class Gate:
    def __init__(self) -> None:
        self.claimed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()


class State:
    def __init__(self) -> None:
        legacy = content(LEGACY_ACTIONS)
        self.cases = [
            case_item(1, "Legacy actions", current_version_id=101),
            case_item(3, "Archived history", "ARCHIVED", 301),
        ]
        self.versions = {
            1: [case_version(1, 101, 1, legacy)],
            3: [case_version(3, 301, 1, legacy, "APPROVED")],
        }
        self.recordings = {
            key: recording_detail(key)
            for key in ["rec-unedited-0001", "rec-edited-0002", "rec-reject-0003", "rec-stale-0004"]
        }
        expanded = content(new_action_payloads(), session_profile_id=999)
        self.suggestions = {
            "rec-unedited-0001": suggestion(11, "rec-unedited-0001", expanded),
            "rec-edited-0002": suggestion(12, "rec-edited-0002", expanded),
            "rec-reject-0003": suggestion(13, "rec-reject-0003", expanded),
            "rec-stale-0004": suggestion(14, "rec-stale-0004", expanded),
        }
        self.requests: list[dict[str, Any]] = []
        self.case_creates: list[dict[str, Any]] = []
        self.version_creates: list[dict[str, Any]] = []
        self.approvals: list[int] = []
        self.ai_confirms: list[dict[str, Any]] = []
        self.ai_rejects: list[dict[str, Any]] = []
        self.unknown: list[str] = []
        self.fail_next_case_create = False
        self.version_gate: Gate | None = None
        self.confirm_gate: Gate | None = None


async def fulfill(route: Route, payload: Any, status: int = 200) -> None:
    await route.fulfill(status=status, content_type="application/json; charset=utf-8", body=json.dumps(payload, ensure_ascii=False))


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    query = parse_qs(parsed.query)
    state.requests.append({"method": method, "path": path, "query": query, "authorization": request.headers.get("authorization")})

    if (method, path) == ("GET", "/api/v1/projects"):
        projects = [
            {"id": 1, "name": "W3 合成项目", "code": "W3", "description": None, "status": "ACTIVE", "owner_id": "synthetic", "created_at": NOW, "updated_at": NOW, "archived_at": None},
        ]
        return await fulfill(route, {"items": projects, "total": len(projects)})
    if (method, path) == ("GET", "/api/v1/web-cases"):
        return await fulfill(route, {"items": state.cases, "total": len(state.cases)})
    if method == "GET" and re.fullmatch(r"/api/v1/web-cases/\d+", path):
        case_id = int(path.rsplit("/", 1)[1])
        item = next(item for item in state.cases if item["id"] == case_id)
        current = next((version for version in state.versions[case_id] if version["id"] == item["current_version_id"]), None)
        return await fulfill(route, {**item, "current_version": current})
    if method == "GET" and re.fullmatch(r"/api/v1/web-cases/\d+/versions", path):
        case_id = int(path.split("/")[4])
        return await fulfill(route, state.versions[case_id])
    if (method, path) == ("POST", "/api/v1/web-cases"):
        body = request.post_data_json
        state.case_creates.append(copy.deepcopy(body))
        if state.fail_next_case_create:
            state.fail_next_case_create = False
            return await fulfill(route, {"message": "合成 Web Case 保存失败"}, 500)
        case_id = 2
        version = case_version(case_id, 201, 1, body["content"])
        item = case_item(case_id, body["name"], current_version_id=version["id"])
        state.cases.insert(1, item)
        state.versions[case_id] = [version]
        return await fulfill(route, {**item, "current_version": version}, 201)
    if method == "POST" and re.fullmatch(r"/api/v1/web-cases/\d+/versions", path):
        case_id = int(path.split("/")[4])
        body = request.post_data_json
        state.version_creates.append({"case_id": case_id, "body": copy.deepcopy(body)})
        if state.version_gate and not state.version_gate.claimed:
            state.version_gate.claimed = True
            state.version_gate.started.set()
            await asyncio.wait_for(state.version_gate.release.wait(), 20)
        version_no = len(state.versions[case_id]) + 1
        version = case_version(case_id, case_id * 100 + version_no, version_no, body["content"])
        version["change_note"] = body["change_note"]
        state.versions[case_id].insert(0, version)
        item = next(item for item in state.cases if item["id"] == case_id)
        item["current_version_id"] = version["id"]
        item["status"] = "DRAFT"
        return await fulfill(route, version, 201)
    if method == "POST" and re.fullmatch(r"/api/v1/web-cases/\d+/approve", path):
        case_id = int(path.split("/")[4])
        state.approvals.append(case_id)
        item = next(item for item in state.cases if item["id"] == case_id)
        item["status"] = "APPROVED"
        for version in state.versions[case_id]:
            if version["id"] == item["current_version_id"]:
                version["status"] = "APPROVED"
        return await fulfill(route, item)
    if (method, path) == ("GET", "/api/v1/web-pages"):
        return await fulfill(route, [{"id": 10, "project_id": 1, "code": "PAGE", "name": "合成页面", "url_pattern": None, "description": None, "status": "ACTIVE", "created_by": "synthetic", "created_at": NOW, "updated_at": NOW}])
    if (method, path) == ("GET", "/api/v1/web-elements"):
        return await fulfill(route, [{"id": 50, "project_id": 1, "page_id": 10, "name": "提交按钮", "description": None, "element_type": "BUTTON", "status": "ACTIVE", "current_version_id": 501, "created_by": "synthetic", "created_at": NOW, "updated_at": NOW}])
    if (method, path) == ("GET", "/api/v1/session-profiles"):
        profiles = [
            {"id": identifier, "project_id": 1, "environment_id": None, "name": f"session_{identifier}", "status": "ACTIVE", "metadata": {}, "expires_at": None, "storage_state_fingerprint": str(identifier) * 32, "created_by": "synthetic", "created_at": NOW, "updated_at": NOW}
            for identifier in [21, 22]
        ]
        return await fulfill(route, profiles)
    if (method, path) == ("GET", "/api/v1/environments"):
        return await fulfill(route, {"items": []})
    if (method, path) == ("GET", "/api/v1/runners"):
        return await fulfill(route, {"items": [], "total": 0})
    if (method, path) == ("GET", "/api/v1/web-recordings"):
        items = [recording_item(recording_id) for recording_id in state.recordings]
        return await fulfill(route, {"items": items, "total": len(items), "page": 1, "page_size": 20})
    if method == "GET" and re.fullmatch(r"/api/v1/web-recordings/[^/]+", path):
        recording_id = path.rsplit("/", 1)[1]
        return await fulfill(route, state.recordings[recording_id])
    if method == "GET" and path.endswith("/ai-suggestions"):
        recording_id = path.split("/")[4]
        item = state.suggestions[recording_id]
        return await fulfill(route, {"items": [item], "total": 1})
    if method == "POST" and re.fullmatch(r"/api/v1/web-recordings/[^/]+/confirm", path):
        recording_id = path.split("/")[4]
        body = request.post_data_json
        state.ai_confirms.append({"recording_id": recording_id, "body": copy.deepcopy(body), "authorization": request.headers.get("authorization")})
        if state.confirm_gate and not state.confirm_gate.claimed:
            state.confirm_gate.claimed = True
            state.confirm_gate.started.set()
            await asyncio.wait_for(state.confirm_gate.release.wait(), 20)
        state.suggestions[recording_id]["status"] = "ACCEPTED"
        state.recordings[recording_id]["confirmed_web_case_id"] = 90
        state.recordings[recording_id]["confirmed_web_case_version_id"] = 901
        return await fulfill(route, {"schema_version": 1, "recording_id": recording_id, "web_case_id": 90, "web_case_version_id": 901, "status": "DRAFT", "idempotent": False})
    if method == "POST" and path.endswith("/reject"):
        parts = path.split("/")
        recording_id, suggestion_id = parts[4], int(parts[6])
        body = request.post_data_json
        state.ai_rejects.append({"recording_id": recording_id, "suggestion_id": suggestion_id, "body": copy.deepcopy(body)})
        state.suggestions[recording_id]["status"] = "REJECTED"
        return await fulfill(route, state.suggestions[recording_id])
    if (method, path) == ("GET", "/api/v1/prompt-center"):
        return await fulfill(route, {"items": []})
    if (method, path) == ("GET", "/api/v1/model-center/bindings/project"):
        return await fulfill(route, {"items": []})

    state.unknown.append(f"{method} {path}")
    await fulfill(route, {"message": "unexpected synthetic route"}, 500)


class SpaHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == 404:
            self.path = "/index.html"
            return self.do_GET()
        super().send_error(code, message, explain)


async def choose(page: Page, test_id: str, label: str) -> None:
    select = page.get_by_test_id(test_id)
    await select.click()
    controlled_id = await select.evaluate("""node =>
      node.getAttribute('aria-controls')
        || node.querySelector('[aria-controls]')?.getAttribute('aria-controls')
    """)
    options_root = page.locator(f"#{controlled_id}") if controlled_id else page.locator(".el-select-dropdown__item:visible").last.locator("..")
    option = options_root.get_by_role("option", name=label, exact=True)
    await expect(option.last).to_be_visible()
    await option.last.click()


async def field_input(page: Page, test_id: str):
    target = page.get_by_test_id(test_id)
    return target if await target.evaluate("node => node.tagName === 'INPUT'") else target.locator("input")


async def fill_number(page: Page, test_id: str, value: int) -> None:
    target = await field_input(page, test_id)
    await target.fill(str(value))
    await target.press("Enter")


async def switch_manual_action(page: Page, index: int, action_type: str) -> None:
    await choose(page, f"manual-action-type-{index}", action_type)


async def open_recording(page: Page, recording_id: str) -> None:
    row = page.locator(".recording-list-card .el-table__row").filter(has_text=recording_id[:12])
    await expect(row).to_be_visible()
    await row.get_by_role("button", name="详情").click()
    await expect(page.get_by_role("heading", name=recording_id)).to_be_visible()
    await expect(page.get_by_text("人工编辑 AI 候选 WebCaseContent")).to_be_visible()


async def close_recording(page: Page) -> None:
    await page.locator(".el-drawer__close-btn").click()
    await expect(page.locator(".el-drawer")).to_be_hidden()


async def validate(base_url: str, evidence: Path) -> dict[str, Any]:
    state = State()
    page_errors: list[str] = []
    console_errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context_browser = await browser.new_context(viewport={"width": 1800, "height": 1200}, device_scale_factor=1)
        await context_browser.add_init_script("""
          localStorage.setItem('access_token', 'w3-token-a');
          localStorage.setItem('current_user', JSON.stringify({id:'w3-user-a',username:'w3-a',display_name:'W3 A',roles:['ADMIN']}));
        """)
        page = await context_browser.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        await page.route("**/api/v1/**", lambda route: handle_api(state, route))

        await page.goto(f"{base_url}/web-assets?project_id=1")
        await expect(page.get_by_role("heading", name="Legacy actions")).to_be_visible()
        await expect(page.locator("[data-testid^='manual-action-row-']")).to_have_count(7)

        # Saving the legacy seven actions and three assertions must preserve every field.
        await (await field_input(page, "web-case-change-note")).fill("legacy preserve")
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("Web Case 新版本已保存", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("save-web-case")).to_be_enabled()
        assert state.version_creates[-1]["body"]["content"] == content(LEGACY_ACTIONS)
        assert len(state.version_creates[-1]["body"]["content"]["assertions"]) == 3

        # A version switch invalidates an in-flight save's follow-up refresh.
        state.version_gate = Gate()
        await (await field_input(page, "web-case-change-note")).fill("stale version save")
        before_stale_save_loads = sum(1 for item in state.requests if item["path"] == "/api/v1/web-cases")
        await page.get_by_test_id("save-web-case").click()
        await asyncio.wait_for(state.version_gate.started.wait(), 10)
        version_one_row = page.locator(".version-history .el-table__row").filter(has_text="V1")
        await version_one_row.get_by_role("button", name="查看").click()
        state.version_gate.release.set()
        await page.wait_for_timeout(400)
        after_stale_save_loads = sum(1 for item in state.requests if item["path"] == "/api/v1/web-cases")
        assert after_stale_save_loads == before_stale_save_loads
        assert len(state.version_creates) == 2
        state.version_gate = None

        # Construct all 13 new actions in the manual editor. Deliberately pass
        # through old field-bearing types first to prove switching removes them.
        await page.get_by_test_id("new-web-case").click()
        await (await field_input(page, "web-case-name")).fill("Expanded manual actions")
        await (await field_input(page, "web-case-start-url")).fill("https://synthetic.invalid/start")
        await (await field_input(page, "manual-action-url-0")).fill("https://stale.invalid/remove-me")
        await switch_manual_action(page, 0, "RELOAD")

        await page.get_by_test_id("add-manual-action").click()
        await switch_manual_action(page, 1, "PRESS")
        await (await field_input(page, "manual-action-key-1")).fill("Shift+X")
        await switch_manual_action(page, 1, "ENTER")

        await page.get_by_test_id("add-manual-action").click()
        await switch_manual_action(page, 2, "FILL")
        await (await field_input(page, "manual-action-value-2")).fill("remove-me")
        await (await field_input(page, "manual-action-locator-value-2")).fill("#remove-me")
        await switch_manual_action(page, 2, "WAIT_NETWORK_IDLE")

        remaining = ["BACK", "FORWARD", "DOUBLE_CLICK", "RIGHT_CLICK", "CLEAR", "HOVER", "CHECK", "UNCHECK", "RADIO", "TAB"]
        for index, action_type in enumerate(remaining, start=3):
            await page.get_by_test_id("add-manual-action").click()
            await switch_manual_action(page, index, action_type)

        final_types = ["RELOAD", "ENTER", "WAIT_NETWORK_IDLE"] + remaining
        before_missing_locator_save = len(state.case_creates)
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("请为每个目标配置完整的 Locator", exact=False)).to_be_visible()
        assert len(state.case_creates) == before_missing_locator_save
        for index, action_type in enumerate(final_types):
            await fill_number(page, f"manual-action-timeout-{index}", 100 if index == 0 else 600000 if index == 1 else 1000 + index)
            if index % 2:
                await choose(page, f"manual-action-policy-{index}", "继续")
            if action_type in NEW_ELEMENT_ACTIONS:
                if action_type == "DOUBLE_CLICK":
                    await choose(page, f"manual-action-locator-mode-{index}", "引用 Element")
                    await choose(page, f"manual-action-element-version-{index}", "提交按钮 · V#501")
                else:
                    await (await field_input(page, f"manual-action-locator-value-{index}")).fill(f"#target-{action_type.lower()}")

        action_type_select = page.get_by_test_id("manual-action-type-0")
        await action_type_select.click()
        action_type_popup_id = await action_type_select.evaluate("""node =>
          node.getAttribute('aria-controls')
            || node.querySelector('[aria-controls]')?.getAttribute('aria-controls')
        """)
        options = [item.strip() for item in await page.locator(f"#{action_type_popup_id}").get_by_role("option").all_text_contents()]
        assert set(options) == set(ALL_ACTIONS), (options, ALL_ACTIONS)
        await page.keyboard.press("Escape")
        state.fail_next_case_create = True
        before_failed_create = len(state.case_creates)
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("合成 Web Case 保存失败", exact=False)).to_be_visible()
        await page.wait_for_timeout(500)
        assert len(state.case_creates) == before_failed_create + 1
        await expect(page.locator("[data-testid^='manual-action-row-']")).to_have_count(13)
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("Web Case V1 已创建", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("save-web-case")).to_be_enabled()
        expected_manual = action_payloads(final_types)
        assert state.case_creates[-1]["content"]["actions"] == expected_manual
        for action in state.case_creates[-1]["content"]["actions"]:
            expected_keys = {"type", "timeout_ms", "failure_policy"}
            if action["type"] in NEW_ELEMENT_ACTIONS:
                expected_keys.add("locator")
            assert set(action) == expected_keys, action
        await expect(page.locator("[data-testid^='manual-action-row-']")).to_have_count(13)
        for index, action_type in enumerate(final_types):
            await expect(page.get_by_test_id(f"manual-action-type-{index}")).to_contain_text(action_type)
        await page.screenshot(path=evidence / "01-manual-13-actions.png", full_page=True)

        # Approval remains a separate explicit action and archived history cannot save.
        assert not state.approvals
        await page.get_by_role("button", name="批准执行").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await expect(page.get_by_text("Web Case 已批准", exact=False)).to_be_visible()
        assert state.approvals == [2]
        await page.locator("button.asset-list-item").filter(has_text="Archived history").click()
        await expect(page.get_by_test_id("save-web-case")).to_be_disabled()

        # AI unedited acceptance omits content and therefore uses canonical server content.
        await page.get_by_role("tab", name="录制工作台").click()
        await open_recording(page, "rec-unedited-0001")
        await page.get_by_test_id("open-ai-accept").click()
        await page.get_by_test_id("confirm-ai-accept").click()
        await expect(page.get_by_text("AI 建议已接受", exact=False)).to_be_visible()
        unedited = next(item for item in state.ai_confirms if item["recording_id"] == "rec-unedited-0001")
        assert "content" not in unedited["body"]
        await close_recording(page)

        # Edited acceptance submits all 13 actions, the three existing assertions,
        # and the recording's exact saved Session Profile reference.
        await open_recording(page, "rec-edited-0002")
        await expect(page.locator("[data-testid^='ai-action-row-']")).to_have_count(13)
        await fill_number(page, "ai-action-timeout-0", 101)
        await expect(page.get_by_text("已编辑：接受时会携带 content")).to_be_visible()
        await page.screenshot(path=evidence / "02-ai-edited-13-actions.png", full_page=True)
        await page.get_by_test_id("open-ai-accept").click()
        await page.get_by_test_id("confirm-ai-accept").click()
        await expect(page.get_by_text("AI 建议已接受", exact=False)).to_be_visible()
        edited = next(item for item in state.ai_confirms if item["recording_id"] == "rec-edited-0002")
        expected_ai_actions = new_action_payloads()
        expected_ai_actions[0]["timeout_ms"] = 101
        assert edited["body"]["content"]["actions"] == expected_ai_actions
        assert edited["body"]["content"]["assertions"] == ASSERTIONS
        assert edited["body"]["content"]["session_profile_id"] == 22
        referenced = next(action for action in edited["body"]["content"]["actions"] if action["type"] == "DOUBLE_CLICK")
        assert referenced["locator"] == {"strategy": None, "value": None, "element_version_id": 501}
        await close_recording(page)

        # Invalid edited content must not block an explicit rejection.
        before_reject_confirms = len(state.ai_confirms)
        await open_recording(page, "rec-reject-0003")
        await (await field_input(page, "ai-content-start-url")).fill("")
        await page.get_by_test_id("open-ai-reject").click()
        await page.get_by_test_id("confirm-ai-reject").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await expect(page.get_by_text("AI 建议已拒绝", exact=False)).to_be_visible()
        assert len(state.ai_rejects) == 1
        assert len(state.ai_confirms) == before_reject_confirms
        await close_recording(page)

        # A cross-tab identity change invalidates a held AI acceptance. The already
        # dispatched request may finish server-side, but it cannot refresh or toast
        # into the new identity and is never retried.
        await open_recording(page, "rec-stale-0004")
        state.confirm_gate = Gate()
        await page.get_by_test_id("open-ai-accept").click()
        before_detail_gets = sum(1 for item in state.requests if item["path"] == "/api/v1/web-recordings/rec-stale-0004")
        await page.get_by_test_id("confirm-ai-accept").click()
        await asyncio.wait_for(state.confirm_gate.started.wait(), 10)
        await page.evaluate("""
          localStorage.setItem('access_token', 'w3-token-b');
          localStorage.setItem('current_user', JSON.stringify({id:'w3-user-b',username:'w3-b',display_name:'W3 B',roles:['ADMIN']}));
          window.dispatchEvent(new StorageEvent('storage', {key:'access_token', oldValue:'w3-token-a', newValue:'w3-token-b', storageArea:localStorage}));
        """)
        state.confirm_gate.release.set()
        await page.wait_for_timeout(500)
        after_detail_gets = sum(1 for item in state.requests if item["path"] == "/api/v1/web-recordings/rec-stale-0004")
        assert after_detail_gets == before_detail_gets
        assert len([item for item in state.ai_confirms if item["recording_id"] == "rec-stale-0004"]) == 1
        await expect(page.get_by_text("AI 建议已接受", exact=False)).to_have_count(0)

        expected_negative_console = [message for message in console_errors if "status of 500" in message]
        unexpected_console = [message for message in console_errors if message not in expected_negative_console]
        assert len(expected_negative_console) == 1, console_errors
        assert not state.unknown, state.unknown
        assert not page_errors, page_errors
        assert not unexpected_console, unexpected_console
        await browser.close()

    return {
        "status": "passed",
        "mode": "synthetic-real-chrome",
        "formal_services_used": False,
        "new_actions": NEW_ACTIONS,
        "manual_create_attempt_count": len(state.case_creates),
        "version_create_count": len(state.version_creates),
        "approval_count": len(state.approvals),
        "ai_confirm_count": len(state.ai_confirms),
        "ai_reject_count": len(state.ai_rejects),
        "request_count": len(state.requests),
        "page_errors": len(page_errors),
        "unexpected_console_errors": len(unexpected_console),
        "expected_negative_http_console_events": len(expected_negative_console),
        "checks": [
            "all 13 new actions construct, save and reload with exact WebCase asset payloads",
            "page actions have no business fields and locator actions use WebLocator only",
            "GOTO FILL and PRESS fields are removed when switching action semantics",
            "timeout boundaries and STOP CONTINUE policies persist",
            "missing locators block before request and failed save keeps edits without retry",
            "legacy seven actions and three assertions preserve their configuration",
            "ElementVersion references and Session Profile identity stay exact",
            "approval remains explicit and archived history cannot save",
            "unedited AI accept omits content while edited accept sends full valid content",
            "invalid AI edits do not block reject and no accept is dispatched",
            "identity-invalidated late AI accept triggers no refresh toast or retry",
            "version switch invalidates a late manual save follow-up refresh",
        ],
        "screenshots": ["01-manual-13-actions.png", "02-ai-edited-13-actions.png"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    dist = Path(args.dist).resolve()
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SpaHandler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(validate(f"http://127.0.0.1:{server.server_port}", evidence))
        (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
