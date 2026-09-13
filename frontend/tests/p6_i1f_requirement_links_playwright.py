"""Controlled real-Chrome validation for P6-I1F requirement links and impact UI.

The production SPA build is served locally and every API response is synthetic.
No formal backend, database, AI, Runner, Run, or shared demo data is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route, async_playwright, expect


NOW = "2026-09-10T08:00:00Z"


def project(project_id: int, name: str, status: str = "ACTIVE", owner: str = "mock") -> dict[str, Any]:
    return {
        "id": project_id, "name": name, "code": f"P{project_id}", "description": None,
        "status": status, "owner_id": owner, "created_at": NOW, "updated_at": NOW,
        "archived_at": NOW if status == "ARCHIVED" else None,
    }


PROJECTS = [
    project(1, "关联验证项目"),
    project(2, "归档历史项目", "ARCHIVED"),
    project(3, "只读项目", owner="other-owner"),
]


def requirement_version(requirement_id: int, version_id: int, version_no: int, body: str, digest: str) -> dict[str, Any]:
    return {
        "id": version_id, "requirement_id": requirement_id, "version_no": version_no,
        "markdown_content": body, "content_hash": digest, "source_type": "MANUAL",
        "source_filename": None, "change_summary": f"V{version_no}", "created_by": "mock",
        "created_at": NOW,
    }


REQ_VERSIONS = {
    101: [
        requirement_version(101, 1002, 2, "# 结算\n支持优惠券和发票", "hash-v2"),
        requirement_version(101, 1001, 1, "# 结算\n支持优惠券", "hash-v1"),
    ],
    102: [requirement_version(102, 1021, 1, "# 退款", "hash-refund")],
    201: [requirement_version(201, 2011, 1, "# 归档需求", "hash-archive")],
    301: [requirement_version(301, 3011, 1, "# 只读需求", "hash-viewer")],
}


def requirement(requirement_id: int, project_id: int, title: str, status: str = "ACTIVE") -> dict[str, Any]:
    versions = REQ_VERSIONS[requirement_id]
    current = versions[0]
    return {
        "id": requirement_id, "project_id": project_id, "parent_id": None,
        "code": f"REQ-{requirement_id}", "title": title, "type": "FEATURE",
        "order_index": requirement_id, "status": status, "current_version_id": current["id"],
        "created_by": "mock", "created_at": NOW, "updated_at": NOW,
        "current_version": current, "children": [],
    }


REQUIREMENTS = {
    101: requirement(101, 1, "结算需求"),
    102: requirement(102, 1, "退款需求"),
    201: requirement(201, 2, "归档需求", "ARCHIVED"),
    301: requirement(301, 3, "只读需求"),
}


def test_content(title: str) -> dict[str, Any]:
    return {
        "title": title, "case_type": "WEB", "priority": "P1", "preconditions": [],
        "steps": [{"order": 1, "action": "打开结算", "expected": "显示成功"}],
        "test_data": {}, "expected_result": "成功", "tags": ["p6-i1f"], "confidence": 1,
        "request": None, "pre_actions": [], "post_actions": [], "extractors": [],
        "data_source": None, "assertions": [], "cleanup": [],
    }


TEST_VERSIONS = [
    {"id": 7002, "case_id": 7, "version_no": 2, "content": test_content("原 TestCase WEB V2"), "change_note": "V2", "created_by": "mock", "created_at": NOW},
    {"id": 7001, "case_id": 7, "version_no": 1, "content": test_content("原 TestCase WEB V1"), "change_note": "V1", "created_by": "mock", "created_at": NOW},
]
TEST_ASSET = {
    "id": 7, "project_id": 1, "code": "TC-7", "name": "同号原 TestCase",
    "case_type": "WEB", "status": "ACTIVE", "source": "MANUAL", "current_version_id": 7002,
    "created_by": "mock", "created_at": NOW, "updated_at": NOW, "current_version": TEST_VERSIONS[0],
}


def web_content(version: int) -> dict[str, Any]:
    return {
        "start_url": "https://example.test/checkout", "natural_language_steps": [f"独立 WebCase V{version}"],
        "actions": [{"type": "GOTO", "url": "https://example.test/checkout", "timeout_ms": 30000, "failure_policy": "STOP"}],
        "assertions": [], "session_profile_id": None, "browser": "CHROME", "headless": True,
        "total_timeout_ms": 900000, "parameters": {},
    }


WEB_VERSIONS = [
    {"id": 7102, "web_case_id": 7, "version_no": 2, "content": web_content(2), "change_note": "V2", "status": "APPROVED", "approved_by": "mock", "approved_at": NOW, "created_by": "mock", "created_at": NOW},
    {"id": 7101, "web_case_id": 7, "version_no": 1, "content": web_content(1), "change_note": "V1", "status": "APPROVED", "approved_by": "mock", "approved_at": NOW, "created_by": "mock", "created_at": NOW},
]
WEB_ASSET = {
    "id": 7, "project_id": 1, "code": "WEB-7", "name": "同号独立 WebCase",
    "status": "APPROVED", "current_version_id": 7102, "created_by": "mock",
    "created_at": NOW, "updated_at": NOW,
}


def asset_reference(asset_type: str, asset_id: int = 7) -> dict[str, Any]:
    if asset_type == "TEST_CASE":
        return {"asset_type": asset_type, "id": asset_id, "code": "TC-7", "name": "同号原 TestCase", "status": "ACTIVE", "case_type": "WEB", "current_version_id": 7002}
    return {"asset_type": asset_type, "id": asset_id, "code": "WEB-7", "name": "同号独立 WebCase", "status": "APPROVED", "case_type": None, "current_version_id": 7102}


def link(
    link_id: int,
    asset_type: str,
    *,
    status: str = "ACTIVE",
    requirement_id: int = 101,
    requirement_version_id: int | None = 1001,
    asset_version_id: int | None = None,
    supersedes: int | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    req = REQUIREMENTS[requirement_id]
    req_version = next((item for item in REQ_VERSIONS[requirement_id] if item["id"] == requirement_version_id), None)
    if asset_version_id is None and asset_type == "TEST_CASE":
        asset_version = None
    elif asset_version_id is None:
        asset_version = None
    else:
        candidates = TEST_VERSIONS if asset_type == "TEST_CASE" else WEB_VERSIONS
        found = next(item for item in candidates if item["id"] == asset_version_id)
        asset_version = {"id": found["id"], "version_no": found["version_no"], "status": found.get("status")}
    return {
        "id": link_id, "requirement_id": requirement_id, "requirement_code": req["code"],
        "requirement_title": req["title"], "requirement_status": req["status"],
        "requirement_version_id": requirement_version_id,
        "requirement_version": None if req_version is None else {"id": req_version["id"], "version_no": req_version["version_no"], "content_hash": req_version["content_hash"]},
        "requirement_baseline_known": req_version is not None, "asset_type": asset_type,
        "asset_id": 7, "asset": asset_reference(asset_type), "asset_version_id": asset_version_id,
        "asset_version": asset_version, "asset_version_known": asset_version is not None,
        "binding_note": "精确人工覆盖", "relation_type": "COVERAGE", "source": "MANUAL",
        "confidence": 1.0, "status": status, "is_current_relation": status == "ACTIVE",
        "supersedes_link_id": supersedes, "created_by": "mock",
        "created_at_time_basis": "LEGACY_UNKNOWN" if legacy else "UTC",
        "created_at": "2026-09-09 08:00:00" if legacy else NOW,
        "removed_by": "mock" if status == "REMOVED" else None,
        "removed_at": NOW if status == "REMOVED" else None,
        "removed_at_time_basis": "UTC" if status == "REMOVED" else None,
        "historical_scope": "CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT",
    }


class Gate:
    def __init__(self) -> None:
        self.claimed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()


class State:
    def __init__(self) -> None:
        self.links = [
            link(1, "TEST_CASE", status="REMOVED", asset_version_id=7001, legacy=True),
            link(2, "TEST_CASE", asset_version_id=7002, supersedes=1),
            link(3, "WEB_CASE", requirement_version_id=None, asset_version_id=None),
            link(4, "WEB_CASE", requirement_version_id=1002, asset_version_id=7102),
        ]
        self.requests: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.unknown: list[str] = []
        self.fail_next_post = False
        self.fail_next_impact = False
        self.fail_next_links: int | None = None
        self.empty_impact = False
        self.link_gate: Gate | None = None
        self.tree_gate: Gate | None = None
        self.post_gate: Gate | None = None


async def fulfill(route: Route, body: Any, status: int = 200) -> None:
    await route.fulfill(status=status, content_type="application/json; charset=utf-8", body=json.dumps(body, ensure_ascii=False))


def page_payload(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size, "has_more": start + page_size < len(items)}


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    query = parse_qs(parsed.query)
    auth = request.headers.get("authorization", "")
    state.requests.append({"method": method, "path": path, "query": query, "authorization": auth})

    if (method, path) == ("GET", "/api/v1/projects"):
        include_archived = query.get("include_archived", ["false"])[0] == "true"
        items = PROJECTS if include_archived else [item for item in PROJECTS if item["status"] == "ACTIVE"]
        return await fulfill(route, {"items": items, "total": len(items)})
    if method == "GET" and path.startswith("/api/v1/projects/") and path.endswith("/members"):
        project_id = int(path.split("/")[4])
        role = "VIEWER" if project_id == 3 else "PROJECT_OWNER"
        return await fulfill(route, {"items": [
            {"project_id": project_id, "user_id": "mock", "role": role, "created_at": NOW},
            {"project_id": project_id, "user_id": "mock2", "role": role, "created_at": NOW},
        ]})
    if (method, path) == ("GET", "/api/v1/requirements"):
        project_id = int(query["project_id"][0])
        items = [item for item in REQUIREMENTS.values() if item["project_id"] == project_id]
        if project_id == 1 and state.tree_gate and not state.tree_gate.claimed:
            state.tree_gate.claimed = True
            state.tree_gate.started.set()
            await asyncio.wait_for(state.tree_gate.release.wait(), 20)
            items = [{**REQUIREMENTS[101], "title": "过期树响应"}]
        return await fulfill(route, {"items": items})
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/versions"):
        requirement_id = int(path.split("/")[4])
        return await fulfill(route, REQ_VERSIONS[requirement_id])
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/diff"):
        requirement_id = int(path.split("/")[4])
        from_no, to_no = int(query["from_version"][0]), int(query["to_version"][0])
        changed = from_no != to_no
        return await fulfill(route, {"requirement_id": requirement_id, "from_version": from_no, "to_version": to_no, "unified_diff": "-支持优惠券\n+支持优惠券和发票" if changed else "", "additions": 1 if changed else 0, "deletions": 1 if changed else 0})
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.count("/") == 4:
        requirement_id = int(path.split("/")[4])
        return await fulfill(route, REQUIREMENTS[requirement_id])
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/links"):
        requirement_id = int(path.split("/")[4])
        if state.fail_next_links is not None:
            failure_status = state.fail_next_links
            state.fail_next_links = None
            message = "无权读取该需求关联" if failure_status == 403 else "合成关联读取失败"
            return await fulfill(route, {"message": message}, failure_status)
        relevant = [item for item in state.links if item["requirement_id"] == requirement_id]
        if state.link_gate and not state.link_gate.claimed and auth == "Bearer token-a":
            state.link_gate.claimed = True
            state.link_gate.started.set()
            await asyncio.wait_for(state.link_gate.release.wait(), 20)
            relevant = [{**relevant[0], "asset": {**relevant[0]["asset"], "name": "旧身份迟到资产"}}]
        page_no, page_size = int(query.get("page", ["1"])[0]), int(query.get("page_size", ["10"])[0])
        return await fulfill(route, page_payload(relevant, page_no, page_size))
    if method == "POST" and path.startswith("/api/v1/requirements/") and path.endswith("/links"):
        body = request.post_data_json
        state.writes.append({"method": method, "path": path, "body": body, "authorization": auth})
        if state.post_gate and not state.post_gate.claimed:
            state.post_gate.claimed = True
            state.post_gate.started.set()
            await asyncio.wait_for(state.post_gate.release.wait(), 20)
        if state.fail_next_post:
            state.fail_next_post = False
            return await fulfill(route, {"message": "合成写入失败"}, 500)
        requirement_id = int(path.split("/")[4])
        previous = next((item for item in reversed(state.links) if item["requirement_id"] == requirement_id and item["asset_type"] == body["asset_type"] and item["asset_id"] == body["asset_id"]), None)
        created = link(len(state.links) + 1, body["asset_type"], requirement_id=requirement_id, requirement_version_id=body["requirement_version_id"], asset_version_id=body["asset_version_id"], supersedes=previous["id"] if previous else None)
        state.links.append(created)
        return await fulfill(route, created, 201)
    if method == "DELETE" and "/links/" in path:
        link_id = int(path.rsplit("/", 1)[1])
        state.writes.append({"method": method, "path": path, "authorization": auth})
        target = next(item for item in state.links if item["id"] == link_id)
        target.update(status="REMOVED", is_current_relation=False, removed_by="mock", removed_at=NOW, removed_at_time_basis="UTC")
        return await fulfill(route, target)
    if method == "GET" and path.startswith("/api/v1/requirements/") and path.endswith("/impact"):
        if state.fail_next_impact:
            state.fail_next_impact = False
            return await fulfill(route, {"message": "合成影响分析失败"}, 500)
        requirement_id = int(path.split("/")[4])
        from_id, to_id = int(query["from_version_id"][0]), int(query["to_version_id"][0])
        from_v = next(item for item in REQ_VERSIONS[requirement_id] if item["id"] == from_id)
        to_v = next(item for item in REQ_VERSIONS[requirement_id] if item["id"] == to_id)
        changed = from_v["content_hash"] != to_v["content_hash"]
        active = [item for item in state.links if item["requirement_id"] == requirement_id and item["status"] == "ACTIVE"]
        while not state.empty_impact and len(active) < 11:
            template = dict(active[len(active) % max(1, len(active))]) if active else link(90, "TEST_CASE", asset_version_id=7002)
            template["id"] = 90 + len(active)
            active.append(template)
        impact_items = []
        for item in active:
            baseline = item["requirement_version"]
            if baseline is None:
                baseline_status, baseline_reason = "BASELINE_UNKNOWN", "链接未记录可靠需求来源版本"
            elif baseline["content_hash"] == to_v["content_hash"]:
                baseline_status, baseline_reason = "NO_CHANGE", "链接记录的需求基线与目标版本内容相同"
            else:
                baseline_status, baseline_reason = "POSSIBLY_OUTDATED", "确定性潜在关联范围，尚未经人工确认"
            impact_items.append({
                **item,
                "selected_scope_status": "POSSIBLY_OUTDATED" if changed else "NO_CHANGE",
                "selected_scope_reason": "确定性潜在关联范围，尚未经人工确认" if changed else "选定版本内容没有变化",
                "recorded_baseline_status": baseline_status,
                "recorded_baseline_reason": baseline_reason,
            })
        page_no, page_size = int(query.get("page", ["1"])[0]), int(query.get("page_size", ["10"])[0])
        payload = page_payload(impact_items, page_no, page_size)
        payload.update({
            "comparison": {"requirement_id": requirement_id, "from_version": {"id": from_v["id"], "version_no": from_v["version_no"], "content_hash": from_v["content_hash"]}, "to_version": {"id": to_v["id"], "version_no": to_v["version_no"], "content_hash": to_v["content_hash"]}, "content_changed": changed, "additions": 1 if changed else 0, "deletions": 1 if changed else 0},
            "scope_note": "确定性关联范围，尚未经人工确认实际受影响",
        })
        return await fulfill(route, payload)

    if (method, path) == ("GET", "/api/v1/test-cases"):
        return await fulfill(route, [TEST_ASSET] if int(query["project_id"][0]) == 1 else [])
    if (method, path) == ("GET", "/api/v1/test-cases/7/versions"):
        return await fulfill(route, TEST_VERSIONS)
    if (method, path) == ("GET", "/api/v1/test-cases/7/requirements"):
        relevant = [item for item in state.links if item["asset_type"] == "TEST_CASE"]
        return await fulfill(route, page_payload(relevant, int(query.get("page", ["1"])[0]), int(query.get("page_size", ["10"])[0])))
    if (method, path) == ("GET", "/api/v1/web-cases"):
        return await fulfill(route, {"items": [WEB_ASSET] if int(query["project_id"][0]) == 1 else [], "total": 1})
    if (method, path) == ("GET", "/api/v1/web-cases/7"):
        return await fulfill(route, {**WEB_ASSET, "current_version": WEB_VERSIONS[0]})
    if (method, path) == ("GET", "/api/v1/web-cases/7/versions"):
        return await fulfill(route, WEB_VERSIONS)
    if (method, path) == ("GET", "/api/v1/web-cases/7/requirements"):
        relevant = [item for item in state.links if item["asset_type"] == "WEB_CASE"]
        return await fulfill(route, page_payload(relevant, int(query.get("page", ["1"])[0]), int(query.get("page_size", ["10"])[0])))
    if (method, path) in {
        ("GET", "/api/v1/datasets"), ("GET", "/api/v1/prompts"),
        ("GET", "/api/v1/web-pages"), ("GET", "/api/v1/session-profiles"),
        ("GET", "/api/v1/environments"),
    }:
        if path == "/api/v1/datasets": return await fulfill(route, {"items": [], "total": 0})
        if path == "/api/v1/prompts": return await fulfill(route, [])
        return await fulfill(route, [])
    if method == "GET" and path.endswith("/ai-reviews"):
        return await fulfill(route, {"items": []})
    if method == "GET" and "/test-cases/requirements/" in path:
        return await fulfill(route, {"items": []} if path.endswith("/generations") else [])
    if (method, path) == ("GET", "/api/v1/prompt-center"):
        return await fulfill(route, {"items": []})
    if (method, path) == ("GET", "/api/v1/runners"):
        return await fulfill(route, {"items": [], "total": 0})
    if (method, path) == ("GET", "/api/v1/web-recordings"):
        return await fulfill(route, {"items": [], "total": 0, "page": 1, "page_size": 20, "has_more": False})

    state.unknown.append(f"{method} {path}?{parsed.query}")
    await fulfill(route, {"message": "unhandled synthetic endpoint"}, 500)


class SpaHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == 404:
            self.path = "/index.html"
            return self.do_GET()
        super().send_error(code, message, explain)


async def choose(page: Page, test_id: str, text: str) -> None:
    await page.get_by_test_id(test_id).click()
    option = page.locator(".el-select-dropdown__item:visible", has_text=text)
    await expect(option.last).to_be_visible()
    await option.last.click()


async def prepare_page(page: Page, state: State) -> None:
    await page.route("**/api/v1/**", lambda route: handle_api(state, route))


async def validate(base_url: str, evidence: Path) -> dict[str, Any]:
    state = State()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        await context.add_init_script("""
          localStorage.setItem('access_token', 'token-a');
          localStorage.setItem('current_user', JSON.stringify({id:'mock',username:'mock',display_name:'合成测试员',roles:['TESTER']}));
        """)
        page = await context.new_page()
        await prepare_page(page, state)

        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101&requirement_version_id=1001&tab=links")
        await expect(page.get_by_role("heading", name="结算需求")).to_be_visible()
        await expect(page.get_by_text("原 TestCase · WEB", exact=False).first).to_be_visible()
        await expect(page.get_by_text("独立 WebCase", exact=False).first).to_be_visible()
        await expect(page.get_by_text("旧记录时区未知", exact=False)).to_be_visible()
        await expect(page.get_by_text("历史版本未记录", exact=False)).to_be_visible()
        await page.screenshot(path=evidence / "01-link-history-same-id.png", full_page=True)

        await page.get_by_test_id("remove-link-2").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await expect(page.get_by_text("关联已移除，历史仍可追溯")).to_be_visible()
        await choose(page, "link-asset", "同号原 TestCase")
        await choose(page, "link-requirement-version", "V1 (#1001)")
        await choose(page, "link-asset-version", "V1 (#7001)")
        await page.get_by_test_id("create-link").click()
        await expect(page.get_by_text("精确版本关联已创建", exact=False).last).to_be_visible()
        created_id = state.links[-1]["id"]
        await page.get_by_test_id(f"remove-link-{created_id}").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await page.get_by_test_id("create-link").click()
        await expect(page.get_by_text("精确版本关联已创建", exact=False).last).to_be_visible()
        assert state.links[-1]["supersedes_link_id"] == created_id
        post_bodies = [item["body"] for item in state.writes if item["method"] == "POST"]
        assert post_bodies[-1] == {
            "asset_type": "TEST_CASE", "asset_id": 7, "requirement_version_id": 1001,
            "asset_version_id": 7001, "relation_type": "COVERAGE", "confidence": 1,
        }

        await page.get_by_role("tab", name="影响分析").click()
        await choose(page, "impact-from", "From V1 (#1001)")
        await choose(page, "impact-to", "To V2 (#1002)")
        await page.get_by_test_id("analyze-impact").click()
        await expect(page.get_by_text("内容变化事实")).to_be_visible()
        await expect(page.get_by_text("基线未知").first).to_be_visible()
        await expect(page.get_by_text("记录基线 → 目标")).to_be_visible()
        await expect(page.get_by_text("+支持优惠券和发票")).to_be_visible()
        await expect(page.get_by_text("11").first).to_be_visible()
        await page.locator(".el-pagination .btn-next").last.click()
        await page.wait_for_timeout(300)
        assert any(item["path"].endswith("/impact") and item["query"].get("page") == ["2"] for item in state.requests)
        await page.screenshot(path=evidence / "02-impact-baselines-diff-pagination.png", full_page=True)

        await choose(page, "impact-from", "From V2 (#1002)")
        await page.get_by_test_id("analyze-impact").click()
        await expect(page.get_by_text("选定版本内容无变化", exact=False)).to_be_visible()
        state.fail_next_impact = True
        await choose(page, "impact-from", "From V1 (#1001)")
        await page.get_by_test_id("analyze-impact").click()
        await expect(page.get_by_text("合成影响分析失败", exact=False)).to_be_visible()
        await expect(page.get_by_text("当前没有有效关联", exact=False)).to_have_count(0)

        state.fail_next_links = 500
        await page.reload()
        await expect(page.get_by_text("合成关联读取失败", exact=False)).to_be_visible()
        await expect(page.get_by_text("当前需求暂无关联历史", exact=False)).to_have_count(0)

        state.fail_next_links = 403
        await page.reload()
        await expect(page.get_by_text("无权读取该需求关联", exact=False)).to_be_visible()
        await expect(page.get_by_text("当前需求暂无关联历史", exact=False)).to_have_count(0)

        state.empty_impact = True
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=102&tab=links")
        await expect(page.get_by_text("当前需求暂无关联历史", exact=False)).to_be_visible()
        await page.get_by_role("tab", name="影响分析").click()
        await page.get_by_test_id("analyze-impact").click()
        await expect(page.get_by_text("当前没有有效关联", exact=False)).to_be_visible()
        state.empty_impact = False

        await page.goto(f"{base_url}/requirements?project_id=2&requirement_id=201&tab=links")
        await expect(page.get_by_text("当前项目已归档", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("create-link")).to_be_disabled()
        await page.goto(f"{base_url}/requirements?project_id=3&requirement_id=301&tab=links")
        await expect(page.get_by_text("当前角色为只读", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("create-link")).to_be_disabled()
        await page.screenshot(path=evidence / "03-archived-readonly.png", full_page=True)

        await page.goto(f"{base_url}/test-cases?project_id=1&test_case_id=7&version_id=7001&link_source=requirement")
        await expect(page.get_by_text("资产种类固定为 TEST_CASE", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("reverse-requirement-links")).to_be_visible()
        await expect(page.get_by_text("原 TestCase（即使类型为 WEB）", exact=False)).to_be_visible()
        assert any(item["path"] == "/api/v1/test-cases/7/requirements" for item in state.requests)
        await page.wait_for_timeout(400)
        await page.screenshot(path=evidence / "04-testcase-reverse-link.png")

        await page.goto(f"{base_url}/web-assets?project_id=1&web_case_id=7&version_id=7101&link_source=requirement")
        await expect(page.get_by_text("需求关联固定的独立 WebCase 版本", exact=False)).to_be_visible()
        await expect(page.get_by_test_id("reverse-requirement-links")).to_be_visible()
        await expect(page.get_by_text("独立 WebCase", exact=False).last).to_be_visible()
        assert any(item["path"] == "/api/v1/web-cases/7/requirements" for item in state.requests)
        await page.screenshot(path=evidence / "05-webcase-reverse-link.png", full_page=True)

        state.tree_gate = Gate()
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101")
        await asyncio.wait_for(state.tree_gate.started.wait(), 10)
        await choose(page, "requirement-project", "只读项目")
        await expect(page.get_by_role("heading", name="只读需求")).to_be_visible()
        await choose(page, "requirement-project", "关联验证项目")
        await expect(page.get_by_role("heading", name="结算需求")).to_be_visible()
        state.tree_gate.release.set()
        await page.wait_for_timeout(300)
        await expect(page.get_by_role("heading", name="过期树响应")).to_have_count(0)

        state.link_gate = Gate()
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101&tab=links")
        await asyncio.wait_for(state.link_gate.started.wait(), 10)
        await page.get_by_role("link", name="项目管理", exact=True).click()
        await page.wait_for_url("**/projects")
        state.link_gate.release.set()
        await page.wait_for_timeout(300)
        await expect(page.get_by_text("旧身份迟到资产")).to_have_count(0)

        state.link_gate = Gate()
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101&tab=links")
        await asyncio.wait_for(state.link_gate.started.wait(), 10)
        await page.evaluate("""
          localStorage.setItem('access_token', 'token-b');
          localStorage.setItem('current_user', JSON.stringify({id:'mock2',username:'mock2',display_name:'第二身份',roles:['TESTER']}));
          window.dispatchEvent(new StorageEvent('storage', {key:'access_token', oldValue:'token-a', newValue:'token-b', storageArea:localStorage}));
        """)
        state.link_gate.release.set()
        await expect(page.get_by_role("heading", name="结算需求")).to_be_visible()
        await page.wait_for_timeout(300)
        await expect(page.get_by_text("旧身份迟到资产")).to_have_count(0)

        state.post_gate = Gate()
        await choose(page, "link-asset", "同号原 TestCase")
        await choose(page, "link-requirement-version", "V1 (#1001)")
        await choose(page, "link-asset-version", "V1 (#7001)")
        await page.get_by_test_id("create-link").click()
        await asyncio.wait_for(state.post_gate.started.wait(), 10)
        await choose(page, "requirement-project", "只读项目")
        await expect(page.get_by_role("heading", name="只读需求")).to_be_visible()
        before_release_link_gets = sum(1 for item in state.requests if item["path"] == "/api/v1/requirements/101/links")
        state.post_gate.release.set()
        await page.wait_for_timeout(500)
        after_link_gets = sum(1 for item in state.requests if item["path"] == "/api/v1/requirements/101/links")
        assert after_link_gets == before_release_link_gets, "late write result refreshed the old requirement context"
        await expect(page.get_by_text("精确版本关联已创建", exact=False)).to_have_count(0)

        await choose(page, "requirement-project", "关联验证项目")
        await page.get_by_role("tab", name="关联与影响").click()
        await choose(page, "link-asset", "同号原 TestCase")
        await choose(page, "link-requirement-version", "V1 (#1001)")
        await choose(page, "link-asset-version", "V1 (#7001)")
        state.fail_next_post = True
        before_posts = len([item for item in state.writes if item["method"] == "POST"])
        await page.get_by_test_id("create-link").click()
        await expect(page.get_by_text("合成写入失败", exact=False)).to_be_visible()
        await page.wait_for_timeout(600)
        assert len([item for item in state.writes if item["method"] == "POST"]) == before_posts + 1
        await expect(page.get_by_test_id("create-link")).to_be_enabled()
        await page.screenshot(path=evidence / "06-request-generation-and-write-failure.png", full_page=True)

        # A confirmation belongs to the identity and component target that opened it.
        # Navigating away under the same identity must invalidate the old callback.
        before_stale_confirmation_deletes = len([item for item in state.writes if item["method"] == "DELETE"])
        await page.get_by_test_id("remove-link-3").click()
        stale_confirm = page.locator(".el-message-box__btns .el-button--primary")
        await expect(stale_confirm).to_be_visible()
        await page.get_by_role("link", name="项目管理", exact=True).evaluate("node => node.click()")
        await page.wait_for_url("**/projects")
        if await stale_confirm.is_visible():
            await stale_confirm.click()
        await page.wait_for_timeout(300)
        assert len([item for item in state.writes if item["method"] == "DELETE"]) == before_stale_confirmation_deletes

        # Switching to another requirement remounts the panel but can leave the
        # global message box alive. Confirming that old box must also be inert.
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101&tab=links")
        await expect(page.get_by_role("heading", name="结算需求")).to_be_visible()
        await page.get_by_test_id("remove-link-3").click()
        await expect(stale_confirm).to_be_visible()
        await page.get_by_text("退款需求", exact=True).first.evaluate("node => node.click()")
        await expect(page.get_by_role("heading", name="退款需求")).to_be_visible()
        if await stale_confirm.is_visible():
            await stale_confirm.click()
        await page.wait_for_timeout(300)
        assert len([item for item in state.writes if item["method"] == "DELETE"]) == before_stale_confirmation_deletes

        # The invalidated confirmations must not leave the write mutex stuck.
        # A fresh remove and create in the current context still complete once.
        await page.goto(f"{base_url}/requirements?project_id=1&requirement_id=101&tab=links")
        await expect(page.get_by_role("heading", name="结算需求")).to_be_visible()
        await page.get_by_test_id("remove-link-3").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await expect(page.get_by_text("关联已移除，历史仍可追溯").last).to_be_visible()
        assert len([item for item in state.writes if item["method"] == "DELETE"]) == before_stale_confirmation_deletes + 1
        await choose(page, "link-asset", "同号原 TestCase")
        await choose(page, "link-requirement-version", "V1 (#1001)")
        await choose(page, "link-asset-version", "V1 (#7001)")
        before_fresh_posts = len([item for item in state.writes if item["method"] == "POST"])
        await page.get_by_test_id("create-link").click()
        await expect(page.get_by_text("精确版本关联已创建", exact=False).last).to_be_visible()
        assert len([item for item in state.writes if item["method"] == "POST"]) == before_fresh_posts + 1
        await page.screenshot(path=evidence / "07-confirmation-context-and-write-mutex.png", full_page=True)

        assert not state.unknown, state.unknown
        await browser.close()

    return {
        "status": "passed",
        "chrome": "real channel=chrome",
        "synthetic_only": True,
        "write_count": len(state.writes),
        "request_count": len(state.requests),
        "post_payloads": [item["body"] for item in state.writes if item["method"] == "POST"],
        "checks": [
            "same integer ID remains distinct between TEST_CASE and WEB_CASE",
            "explicit requirement and asset version payload",
            "remove and relink preserve revision predecessor",
            "legacy timezone and missing historical versions",
            "selected-scope versus recorded-baseline impact, diff and pagination",
            "no-change, empty-safe failure, archived and viewer read-only",
            "no links and explicit permission-denied reads stay distinct from failures",
            "both reverse paths and deep-link versions",
            "project A-B-A stale tree response rejected",
            "cross-tab identity generation rejects late response",
            "component unmount rejects late link response",
            "late successful write does not refresh old context",
            "failed write keeps form and is not automatically retried",
            "same-identity unload and requirement switch invalidate old remove confirmations",
            "invalidated confirmations release the write mutex for fresh remove and create",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    dist, evidence = Path(args.dist).resolve(), Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SpaHandler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(validate(f"http://127.0.0.1:{server.server_port}", evidence))
        (evidence / "playwright-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
