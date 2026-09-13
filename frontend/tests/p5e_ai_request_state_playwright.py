"""Isolated Playwright/Chrome checks for the P5-E AI request state boundary.

All API responses are synthetic. The script never reaches a real backend, invokes
a model, or calls an Accept/Approve/Dispatch endpoint.
"""

from __future__ import annotations

import asyncio
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route, async_playwright, expect


NOW = "2026-09-09T01:00:00Z"


def make_run(run_id: str, code: str, case_run_id: int) -> dict[str, Any]:
    step = {
        "id": case_run_id * 10, "case_run_id": case_run_id, "sequence_no": 1,
        "node_id": "click-submit", "step_name": "点击提交", "step_type": "CLICK",
        "status": "FAILED", "started_at": NOW, "ended_at": NOW, "duration": 120,
        "error_type": "LOCATOR_NOT_FOUND", "error_message": "元素未找到", "retry_count": 0,
        "created_at": NOW, "updated_at": NOW,
    }
    case_run = {
        "id": case_run_id, "run_id": run_id, "sequence_no": 1,
        "case_id": None, "case_version_id": None, "scenario_id": None,
        "scenario_version_id": None, "web_case_id": 101, "web_case_version_id": 201,
        "status": "FAILED", "duration": 120, "retry_count": 0,
        "started_at": NOW, "ended_at": NOW, "error_type": "LOCATOR_NOT_FOUND",
        "error_message": "元素未找到", "created_at": NOW, "updated_at": NOW,
        "step_runs": [step],
    }
    return {
        "id": run_id, "run_code": code, "run_type": "WEB_CASE", "project_id": 1,
        "environment_id": 11, "runner_id": "runner-mock", "case_id": None,
        "case_version_id": None, "scenario_id": None, "scenario_version_id": None,
        "web_case_id": 101, "web_case_version_id": 201, "status": "FAILED",
        "trigger_type": "MANUAL", "required_capabilities": ["WEB"], "required_tags": [],
        "required_slot_type": "WEB", "required_slot_count": 1,
        "runtime_snapshot_id": "snapshot-mock", "total_timeout_ms": 90_000,
        "effective_total_timeout_ms": 90_000, "force_stop_requested_at": None,
        "force_stopped": False, "started_at": NOW, "ended_at": NOW,
        "total": 1, "pass": 0, "fail": 1, "review": 0, "timeout": 0,
        "error_type": "LOCATOR_NOT_FOUND", "error_message": "Web 用例执行失败",
        "created_by": "mock-user", "created_at": NOW, "updated_at": NOW,
        "case_runs": [case_run],
        "web_traces": [{
            "node_id": "click-submit", "status": "FAILED", "duration_ms": 120,
            "error_type": "LOCATOR_NOT_FOUND", "error_message": "元素未找到",
            "locator_attempts": [{"strategy": "css", "priority": 1, "status": "NOT_FOUND"}],
            "healing_context": {
                "schema_version": 1, "trigger": "ALL_LOCATORS_FAILED",
                "element_version_id": None, "page_url": None, "page_title": "Mock page",
                "dom_candidates": [{"tag": "button", "role": "button", "name": "提交"}],
            },
        }],
    }


RUNS = {
    "run-1": make_run("run-1", "RUN-MOCK-1", 1001),
    "run-2": make_run("run-2", "RUN-MOCK-2", 1002),
}


def summary(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key not in {"case_runs", "web_traces"}}


def failure_item(run_id: str, item_id: int, marker: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "id": item_id, "project_id": 1, "run_id": run_id,
        "case_run_id": RUNS[run_id]["case_runs"][0]["id"], "status": "COMPLETED",
        "ai_call_id": item_id + 10_000, "actual_model": "mock-model",
        "prompt_version_id": 601, "output_schema_id": 501,
        "fallback_used": False, "repair_used": False,
        "source_snapshot_sha256": "a" * 64, "source_snapshot_size": 128,
        "structured_result": {
            "failure_category": "LOCATOR_NOT_FOUND", "severity": "MEDIUM",
            "summary": marker, "root_cause": "页面结构已变化",
            "recommendations": ["人工复核 Locator"], "evidence_node_ids": ["click-submit"],
            "confidence": 0.9, "needs_human_review": True,
        },
        "created_by": "mock-user", "created_at": NOW,
    }


def healing_item(run_id: str, item_id: int, marker: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "id": item_id, "project_id": 1, "run_id": run_id,
        "case_run_id": RUNS[run_id]["case_runs"][0]["id"], "web_case_id": 101,
        "web_case_version_id": 201, "node_id": "click-submit", "status": "DRAFT",
        "ai_call_id": item_id + 10_000, "actual_model": "mock-model",
        "prompt_version_id": 602, "output_schema_id": 502,
        "fallback_used": False, "repair_used": False,
        "source_snapshot_sha256": "b" * 64, "source_snapshot_size": 128,
        "old_locator": {"strategy": "css", "value": "#submit"},
        "proposed_locator": {"strategy": "role", "value": marker}, "human_locator": None,
        "candidate_locators": [{
            "candidate_index": 0, "locator": {"strategy": "role", "value": marker},
        }],
        "confidence": 0.9, "reason": "结构变化", "evidence_candidate_index": 0,
        "decision_note": None, "created_element_version_id": None,
        "created_web_case_version_id": None, "created_by": "mock-user",
        "reviewed_by": None, "created_at": NOW, "reviewed_at": None, "idempotent": False,
    }


class State:
    def __init__(self) -> None:
        self.failure_mode = "success"
        self.healing_mode = "success"
        self.failure_history_status = 200
        self.healing_history_status = 200
        self.failure_count = 0
        self.healing_count = 0
        self.failure_history = {run_id: [] for run_id in RUNS}
        self.healing_history = {run_id: [] for run_id in RUNS}
        self.failure_started = asyncio.Event()
        self.failure_release = asyncio.Event()
        self.healing_started = asyncio.Event()
        self.healing_release = asyncio.Event()
        self.unknown: list[str] = []


def prompt(task_type: str) -> dict[str, Any]:
    healing = task_type == "LOCATOR_HEALING"
    prompt_id, schema_id = (702, 502) if healing else (701, 501)
    return {
        "id": prompt_id, "name": f"Mock {task_type}", "code": f"MOCK_{task_type}",
        "task_type": task_type, "description": None, "enabled": True,
        "current_version_id": prompt_id + 100, "created_by": "mock-user",
        "created_at": NOW, "updated_at": NOW,
        "current_version": {
            "id": prompt_id + 100, "prompt_id": prompt_id, "version_no": 1,
            "system_prompt": "mock", "user_template": "mock", "output_schema_id": schema_id,
            "change_note": None, "created_by": "mock-user", "created_at": NOW,
        },
    }


async def json_response(route: Route, body: Any, status: int = 200) -> None:
    await route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    query = parse_qs(parsed.query)

    if path.endswith("/events/stream"):
        return await json_response(route, {"message": "mock stream unavailable"}, 503)
    fixtures: dict[tuple[str, str], Any] = {
        ("GET", "/api/v1/projects"): {"items": [{
            "id": 1, "name": "P5E Mock", "code": "P5E_MOCK", "description": None,
            "status": "ACTIVE", "owner_id": "mock-user", "created_at": NOW,
            "updated_at": NOW, "archived_at": None,
        }], "total": 1},
        ("GET", "/api/v1/environments"): {"items": [{
            "id": 11, "project_id": 1, "name": "Mock", "code": "MOCK", "base_url": None,
            "description": None, "is_default": True, "enabled": True,
            "created_at": NOW, "updated_at": NOW,
        }]},
        ("GET", "/api/v1/test-cases"): [],
        ("GET", "/api/v1/scenarios"): {"items": []},
        ("GET", "/api/v1/web-cases"): {"items": [{
            "id": 101, "project_id": 1, "code": "WEB-MOCK", "name": "Mock Web Case",
            "status": "APPROVED", "current_version_id": 201, "created_by": "mock-user",
            "created_at": NOW, "updated_at": NOW,
        }], "total": 1},
        ("GET", "/api/v1/session-profiles"): [],
        ("GET", "/api/v1/runners"): {"items": [], "total": 0},
        ("GET", "/api/v1/runs"): {
            "items": [summary(RUNS["run-1"]), summary(RUNS["run-2"])],
            "total": 2, "page": 1, "page_size": 10,
        },
        ("GET", "/api/v1/evidence"): {"items": [], "total": 0, "page": 1, "page_size": 100},
    }
    if (method, path) in fixtures:
        return await json_response(route, fixtures[(method, path)])
    if path.startswith("/api/v1/runs/") and method == "GET" and path.count("/") == 4:
        return await json_response(route, RUNS[path.rsplit("/", 1)[-1]])
    if path == "/api/v1/prompt-center" and method == "GET":
        task_type = query.get("task_type", ["WEB_FAILURE_ANALYSIS"])[0]
        return await json_response(route, {"items": [prompt(task_type)]})
    if path == "/api/v1/ai/output-schemas" and method == "GET":
        return await json_response(route, {"items": [
            {"id": 501, "name": "Failure", "version_no": 1, "description": None,
             "schema_json": {}, "enabled": True, "created_by": "mock-user", "created_at": NOW},
            {"id": 502, "name": "Healing", "version_no": 1, "description": None,
             "schema_json": {}, "enabled": True, "created_by": "mock-user", "created_at": NOW},
        ]})
    if path == "/api/v1/model-center/bindings/project" and method == "GET":
        return await json_response(route, {"items": [
            {"id": 801, "project_id": 1, "task_type": "WEB_FAILURE_ANALYSIS",
             "primary_model_id": 1, "fallback_model_id": None, "max_fallback": 0,
             "updated_by": "mock-user", "created_at": NOW, "updated_at": NOW},
            {"id": 802, "project_id": 1, "task_type": "LOCATOR_HEALING",
             "primary_model_id": 1, "fallback_model_id": None, "max_fallback": 0,
             "updated_by": "mock-user", "created_at": NOW, "updated_at": NOW},
        ]})

    if path.endswith("/web-failure-analyses"):
        run_id = path.split("/")[4]
        if method == "GET":
            if state.failure_history_status != 200:
                return await json_response(route, {"message": "mock history failure"}, state.failure_history_status)
            items = state.failure_history[run_id]
            return await json_response(route, {"items": items, "total": len(items)})
        state.failure_count += 1
        if state.failure_mode == "timeout":
            return await json_response(route, {"message": "mock gateway timeout"}, 504)
        if state.failure_mode == "deferred":
            state.failure_started.set()
            await state.failure_release.wait()
            return await json_response(route, failure_item(run_id, 9901, "STALE_FAILURE_MARKER"))
        item = failure_item(run_id, 9000 + state.failure_count, "受控失败分析")
        state.failure_history[run_id] = [item, *state.failure_history[run_id]]
        await asyncio.sleep(0.15)
        return await json_response(route, item)

    if path.endswith("/web-healing-proposals"):
        run_id = path.split("/")[4]
        if method == "GET":
            if state.healing_history_status != 200:
                return await json_response(route, {"message": "mock history failure"}, state.healing_history_status)
            items = state.healing_history[run_id]
            return await json_response(route, {"items": items, "total": len(items)})
        state.healing_count += 1
        if state.healing_mode == "timeout":
            return await json_response(route, {"message": "mock gateway timeout"}, 504)
        if state.healing_mode == "deferred":
            state.healing_started.set()
            await state.healing_release.wait()
            return await json_response(route, healing_item(run_id, 9902, "STALE_HEALING_MARKER"))
        item = healing_item(run_id, 9200 + state.healing_count, "button[name=submit]")
        state.healing_history[run_id] = [item, *state.healing_history[run_id]]
        await asyncio.sleep(0.15)
        return await json_response(route, item)

    state.unknown.append(f"{method} {path}")
    await json_response(route, {"message": "unhandled mock request"}, 404)


class QuietSpaHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - inherited API
        request_path = urlparse(self.path).path
        candidate = Path(self.directory or ".") / request_path.lstrip("/")
        if request_path != "/" and not candidate.exists() and "." not in Path(request_path).name:
            self.path = "/index.html"
        super().do_GET()


async def open_run(page: Page, code: str) -> None:
    drawer = page.locator(".el-drawer")
    if await drawer.is_visible():
        await page.locator(".el-drawer__close-btn").click()
        await expect(drawer).to_be_hidden()
    row = page.locator(".run-table .el-table__row").filter(has_text=code)
    await expect(row).to_have_count(1)
    await row.get_by_role("button", name="详情").click()
    await expect(drawer).to_be_visible()
    await expect(drawer.locator(".detail-heading .eyebrow")).to_have_text(code)


async def select_healing(page: Page):
    await page.locator("section.web-traces-section").get_by_role(
        "button", name="生成 Healing 提案"
    ).click()
    section = page.locator("section.healing-section")
    await expect(section).to_be_visible()
    return section


async def validate_failure(page: Page, state: State, checks: list[str]) -> None:
    generate = page.get_by_role("button", name="生成分析")
    await expect(generate).to_be_enabled()
    state.failure_mode = "timeout"
    await generate.click()
    notice = page.locator(".failure-analysis-section .el-alert").filter(
        has_text="服务端可能仍在处理，结果也可能已经落库"
    )
    await expect(notice).to_be_visible()
    await expect(generate).to_be_disabled()
    checks.append("failure timeout locks duplicate generation")

    state.failure_history_status = 500
    await page.get_by_role("button", name="刷新分析历史").click()
    await expect(page.get_by_text("mock history failure", exact=False)).to_be_visible()
    await expect(notice).to_be_visible()
    await expect(generate).to_be_disabled()
    checks.append("failure failed refresh keeps lock")

    state.failure_history_status = 200
    state.failure_history["run-1"] = [failure_item("run-1", 9001, "刷新后结果")]
    await page.get_by_role("button", name="刷新分析历史").click()
    await expect(notice).to_have_count(0)
    await expect(generate).to_be_enabled()
    checks.append("failure successful refresh unlocks generation")

    state.failure_count = 0
    state.failure_mode = "success"
    await generate.evaluate("element => { element.click(); element.click(); }")
    await expect(generate).to_be_enabled()
    assert state.failure_count == 1, f"failure double click sent {state.failure_count} POSTs"
    checks.append("failure double click sends one POST")

    await open_run(page, "RUN-MOCK-1")
    state.failure_mode = "deferred"
    state.failure_started, state.failure_release = asyncio.Event(), asyncio.Event()
    await page.get_by_role("button", name="生成分析").click()
    await asyncio.wait_for(state.failure_started.wait(), 5)
    await open_run(page, "RUN-MOCK-2")
    state.failure_release.set()
    await page.wait_for_timeout(300)
    assert not await page.get_by_text("STALE_FAILURE_MARKER", exact=False).count()
    checks.append("failure stale response is ignored after Run switch")


async def validate_healing(page: Page, state: State, checks: list[str]) -> None:
    await open_run(page, "RUN-MOCK-1")
    section = await select_healing(page)
    generate = section.get_by_role("button", name="生成 Healing 提案")
    await expect(generate).to_be_enabled()
    state.healing_mode = "timeout"
    await generate.click()
    notice = section.locator(".el-alert").filter(
        has_text="服务端可能仍在处理，结果也可能已经落库"
    )
    await expect(notice).to_be_visible()
    await expect(generate).to_be_disabled()
    checks.append("healing timeout locks duplicate generation")

    state.healing_history_status = 500
    await section.get_by_role("button", name="刷新提案历史").click()
    await expect(section.get_by_text("mock history failure", exact=False)).to_be_visible()
    await expect(notice).to_be_visible()
    await expect(generate).to_be_disabled()
    checks.append("healing failed refresh keeps lock")

    state.healing_history_status = 200
    state.healing_history["run-1"] = [healing_item("run-1", 9201, "button[name=submit]")]
    await section.get_by_role("button", name="刷新提案历史").click()
    await expect(notice).to_have_count(0)
    await expect(generate).to_be_enabled()
    checks.append("healing successful refresh unlocks generation")

    state.healing_count = 0
    state.healing_mode = "success"
    await generate.evaluate("element => { element.click(); element.click(); }")
    await expect(generate).to_be_enabled()
    assert state.healing_count == 1, f"healing double click sent {state.healing_count} POSTs"
    checks.append("healing double click sends one POST")

    await open_run(page, "RUN-MOCK-1")
    section = await select_healing(page)
    state.healing_mode = "deferred"
    state.healing_started, state.healing_release = asyncio.Event(), asyncio.Event()
    await section.get_by_role("button", name="生成 Healing 提案").click()
    await asyncio.wait_for(state.healing_started.wait(), 5)
    await open_run(page, "RUN-MOCK-2")
    state.healing_release.set()
    await page.wait_for_timeout(300)
    assert not await page.get_by_text("STALE_HEALING_MARKER", exact=False).count()
    checks.append("healing stale response is ignored after Run switch")


async def run_browser(base_url: str) -> dict[str, Any]:
    state, checks, script_errors = State(), [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context()
        await context.add_init_script("""
          localStorage.setItem('access_token', 'synthetic-browser-test-token');
          localStorage.setItem('current_user', JSON.stringify({
            id: 'mock-user', username: 'mock-user', display_name: 'Mock User', roles: ['ADMIN']
          }));
        """)
        page = await context.new_page()
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        await page.route("**/api/v1/**", lambda route: handle_api(state, route))
        await page.goto(f"{base_url}/runs?project_id=1&run_id=run-1")
        await expect(page.locator(".el-drawer")).to_be_visible(timeout=15_000)
        await validate_failure(page, state, checks)
        await validate_healing(page, state, checks)
        await context.close()
        await browser.close()
    assert not state.unknown, f"unhandled requests: {state.unknown}"
    assert not script_errors, f"browser script errors: {script_errors}"
    return {"checks": checks, "script_errors": 0}


def main() -> None:
    workspace = Path(__file__).resolve().parents[2]
    dist = workspace / ".codex-validation" / "p5e-frontend-dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("isolated frontend build is missing")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietSpaHandler, directory=str(dist))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(json.dumps(asyncio.run(run_browser(f"http://127.0.0.1:{server.server_port}")), ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
