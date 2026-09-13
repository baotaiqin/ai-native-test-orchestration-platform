"""Controlled real-Chrome validation for the P6-X2 report export frontend.

The SPA is served from an isolated production build and every API response is
synthetic. No formal backend, database, Runner, or credential is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from p6_u1_playwright import (
    PROJECTS,
    QuietSpaHandler,
    dashboard_payload,
    detail_payload,
)
from playwright.async_api import (
    BrowserContext,
    Download,
    Page,
    Route,
    async_playwright,
    expect,
)


class ExportPlan:
    def __init__(
        self,
        run_id: str,
        export_format: str,
        *,
        body: bytes = b"",
        status: int = 200,
        content_type: str = "text/markdown; charset=utf-8",
        disposition: str = "",
        gated: bool = False,
        abort: bool = False,
    ) -> None:
        self.run_id = run_id
        self.export_format = export_format
        self.body = body
        self.status = status
        self.content_type = content_type
        self.disposition = disposition
        self.abort = abort
        self.claimed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not gated:
            self.release.set()


class DetailGate:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.claimed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()


class State:
    def __init__(self) -> None:
        self.export_plans: list[ExportPlan] = []
        self.detail_gates: list[DetailGate] = []
        self.export_requests: list[dict[str, Any]] = []
        self.login_requests: list[dict[str, Any]] = []
        self.unknown: list[str] = []
        self.business_mutations: list[str] = []
        self.login_count = 0

    def plan_export(self, run_id: str, export_format: str, **kwargs: Any) -> ExportPlan:
        plan = ExportPlan(run_id, export_format, **kwargs)
        self.export_plans.append(plan)
        return plan

    def gate_detail(self, run_id: str) -> DetailGate:
        gate = DetailGate(run_id)
        self.detail_gates.append(gate)
        return gate


def json_bytes(message: str, code: str = "SYNTHETIC_ERROR") -> bytes:
    return json.dumps(
        {"code": code, "message": message, "request_id": "p6-x2"},
        ensure_ascii=False,
    ).encode("utf-8")


def synthetic_detail(run_id: str) -> dict[str, Any]:
    payload = detail_payload(run_id)
    if run_id == "run-running":
        payload["summary"]["status"] = "RUNNING"
        payload["summary"]["ended_at"] = None
        payload["summary"]["duration"] = {
            "milliseconds": 1250,
            "kind": "OBSERVED",
            "observed_at": "2026-09-10T08:00:00Z",
        }
    if run_id == "run-archived":
        payload["summary"]["project"] = {
            "id": 3,
            "name": "Archive",
            "status": "ARCHIVED",
            "metadata_basis": "CURRENT",
        }
    return payload


async def fulfill_json(route: Route, body: Any, status: int = 200) -> None:
    await route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(body, ensure_ascii=False),
    )


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    query = parse_qs(parsed.query)

    if path == "/api/v1/auth/login" and method == "POST":
        state.login_count += 1
        token = f"token-login-{state.login_count + 1}"
        user_id = f"u{state.login_count + 1}"
        state.login_requests.append({"method": method, "token": token, "user_id": user_id})
        return await fulfill_json(route, {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {
                "id": user_id,
                "username": f"mock-{user_id}",
                "display_name": f"Mock {user_id}",
                "roles": ["TESTER"],
            },
        })

    if method not in {"GET", "HEAD", "OPTIONS"}:
        state.business_mutations.append(f"{method} {path}")
        return await fulfill_json(route, {"message": "business mutation blocked"}, 405)

    if path == "/api/v1/projects":
        return await fulfill_json(route, {"items": PROJECTS, "total": len(PROJECTS)})
    if path == "/api/v1/dashboard":
        return await fulfill_json(route, dashboard_payload(None))
    if path == "/api/v1/reports":
        return await fulfill_json(route, {
            "items": [], "total": 0, "page": 1, "page_size": 20, "has_more": False,
        })

    if path.startswith("/api/v1/reports/"):
        parts = path.split("/")
        run_id = unquote(parts[4])
        if len(parts) == 6 and parts[5] == "export":
            export_format = query.get("format", [""])[0]
            state.export_requests.append({
                "run_id": run_id,
                "format": export_format,
                "method": method,
                "authorization": request.headers.get("authorization"),
                "url": request.url,
            })
            plan = next((
                item for item in state.export_plans
                if not item.claimed
                and item.run_id == run_id
                and item.export_format == export_format
            ), None)
            if plan is None:
                state.unknown.append(f"unplanned export {method} {path}?{parsed.query}")
                return await fulfill_json(route, {"message": "unplanned export"}, 500)
            plan.claimed = True
            plan.started.set()
            await asyncio.wait_for(plan.release.wait(), 20)
            if plan.abort:
                return await route.abort("failed")
            headers = {"Content-Type": plan.content_type}
            if plan.disposition:
                headers["Content-Disposition"] = plan.disposition
            return await route.fulfill(status=plan.status, headers=headers, body=plan.body)

        if len(parts) == 5:
            for gate in state.detail_gates:
                if gate.run_id == run_id and not gate.claimed:
                    gate.claimed = True
                    gate.started.set()
                    await asyncio.wait_for(gate.release.wait(), 20)
                    break
            return await fulfill_json(route, synthetic_detail(run_id))

    state.unknown.append(f"{method} {path}?{parsed.query}")
    await fulfill_json(route, {"message": "unhandled"}, 500)


async def goto_report(page: Page, base_url: str, run_id: str) -> None:
    await page.goto(f"{base_url}/reports/{run_id}")
    await expect(page.get_by_role("heading", name=run_id.upper())).to_be_visible()


async def spa_report_navigation(page: Page, run_id: str) -> None:
    await page.evaluate(
        "runId => { history.pushState({}, '', `/reports/${runId}`); "
        "window.dispatchEvent(new PopStateEvent('popstate')); }",
        run_id,
    )
    await expect(page.get_by_role("heading", name=run_id.upper())).to_be_visible()


async def download_bytes(download: Download) -> bytes:
    path = await download.path()
    if path is None:
        raise AssertionError("Chrome did not expose the completed download path")
    return Path(path).read_bytes()


async def clear_messages(page: Page) -> None:
    await page.locator(".el-message").evaluate_all("nodes => nodes.forEach(node => node.remove())")


async def assert_error_without_download(
    page: Page,
    state: State,
    base_url: str,
    run_id: str,
    export_format: str,
    expected_message: str,
    downloads: list[str],
) -> None:
    await goto_report(page, base_url, run_id)
    await clear_messages(page)
    before_downloads = len(downloads)
    before_requests = len(state.export_requests)
    test_id = "export-markdown" if export_format == "markdown" else "export-html"
    await page.get_by_test_id(test_id).click()
    message = page.locator(".el-message--error").last
    await expect(message).to_be_visible()
    await expect(message).to_contain_text(expected_message)
    await page.wait_for_timeout(150)
    assert len(downloads) == before_downloads
    assert len(state.export_requests) == before_requests + 1


async def validate_success_and_resources(
    page: Page,
    state: State,
    base_url: str,
    out_dir: Path,
    checks: list[str],
) -> None:
    markdown = "# 完整报告\n\nRun: run-basic\nCase: 全量第 105 条 ✅\n你好，世界\n".encode()
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head>"
        "<body><h1>完整 HTML 报告</h1><p>全量 Evidence 105 · 你好，世界 ✅</p></body></html>"
    ).encode()
    state.plan_export(
        "run-basic", "markdown", body=markdown,
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="ai-test-report-run-basic-a1b2c3.md"',
    )
    state.plan_export(
        "run-basic", "html", body=html,
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="ai-test-report-run-basic-a1b2c3.html"',
    )

    await goto_report(page, base_url, "run-basic")
    await expect(page.get_by_text("由服务端汇总全部 CaseRun", exact=False)).to_be_visible()
    assert not state.export_requests, "report detail must not export without an explicit click"

    async with page.expect_download() as markdown_info:
        await page.get_by_test_id("export-markdown").click()
    markdown_download = await markdown_info.value
    assert markdown_download.suggested_filename == "ai-test-report-run-basic-a1b2c3.md"
    assert await download_bytes(markdown_download) == markdown

    async with page.expect_download() as html_info:
        await page.get_by_test_id("export-html").click()
    html_download = await html_info.value
    assert html_download.suggested_filename == "ai-test-report-run-basic-a1b2c3.html"
    assert await download_bytes(html_download) == html

    requests = [item for item in state.export_requests if item["run_id"] == "run-basic"]
    assert [(item["method"], item["format"]) for item in requests] == [
        ("GET", "markdown"), ("GET", "html"),
    ]
    assert all(item["authorization"] == "Bearer token-one" for item in requests)
    assert all(parse_qs(urlparse(item["url"]).query) == {"format": [item["format"]]} for item in requests)
    assert all("access_token" not in item["url"] and "token-one" not in item["url"] for item in requests)
    checks.extend([
        "exports start only on explicit clicks and use authenticated GET format parameters",
        "Markdown download preserves exact full-report bytes and Unicode",
        "HTML download preserves exact full-report bytes and Unicode",
        "server full-report marker is independent from the visible first page",
    ])

    await page.wait_for_function(
        "() => window.__p6x2.created.length === window.__p6x2.revoked.length",
    )
    counters = await page.evaluate("window.__p6x2")
    assert len(counters["created"]) == 2
    assert counters["created"] == counters["revoked"]
    assert counters["anchorClicks"] == 2
    assert await page.locator('a[download][href^="blob:"]').count() == 0
    checks.append("object URLs are revoked and temporary anchors are removed")
    await page.screenshot(path=str(out_dir / "report-export-success.png"), full_page=True)


async def validate_duplicate_and_response_safety(
    page: Page,
    state: State,
    base_url: str,
    out_dir: Path,
    checks: list[str],
    downloads: list[str],
) -> None:
    duplicate = state.plan_export(
        "run-duplicate", "markdown", gated=True, body=b"# duplicate safe\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="ai-test-report-run-duplicate.md"',
    )
    await goto_report(page, base_url, "run-duplicate")
    await page.get_by_test_id("export-markdown").evaluate("button => { button.click(); button.click(); }")
    await asyncio.wait_for(duplicate.started.wait(), 5)
    assert len([item for item in state.export_requests if item["run_id"] == "run-duplicate"]) == 1
    await expect(page.get_by_test_id("export-markdown")).to_be_disabled()
    async with page.expect_download() as info:
        duplicate.release.set()
    await info.value
    checks.append("duplicate clicks create exactly one request while both export actions are disabled")

    malicious_body = b"<!doctype html><html><body>safe fallback</body></html>"
    state.plan_export(
        "run-malicious", "html", body=malicious_body,
        content_type="text/html; charset=utf-8",
        disposition="attachment; filename*=UTF-8''..%2F..%2Fowned.HTML",
    )
    await goto_report(page, base_url, "run-malicious")
    async with page.expect_download() as malicious_info:
        await page.get_by_test_id("export-html").click()
    malicious_download = await malicious_info.value
    assert malicious_download.suggested_filename == "ai-test-report-run-malicious.html"
    assert await download_bytes(malicious_download) == malicious_body
    checks.append("malicious Content-Disposition falls back to a controlled Run-based extension")

    state.plan_export(
        "run-json", "markdown", body=json_bytes("raw pseudo success must stay hidden"),
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="looks-safe.md"',
    )
    await assert_error_without_download(
        page, state, base_url, "run-json", "markdown",
        "报告格式与所选下载格式不匹配", downloads,
    )
    await expect(page.get_by_text("raw pseudo success must stay hidden", exact=False)).to_have_count(0)
    checks.append("JSON pseudo-success is rejected even with a matching MIME and its body stays hidden")

    state.plan_export(
        "run-mime-mismatch", "html", body=b"# not an HTML report\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="wrong.html"',
    )
    await assert_error_without_download(
        page, state, base_url, "run-mime-mismatch", "html",
        "报告格式与所选下载格式不匹配", downloads,
    )
    checks.append("a MIME mismatch is rejected before any attachment is saved")
    await page.screenshot(path=str(out_dir / "report-export-safety.png"), full_page=True)


async def validate_error_matrix(
    page: Page,
    state: State,
    base_url: str,
    checks: list[str],
    downloads: list[str],
) -> None:
    scenarios = [
        ("run-404", 404, "markdown", "此历史报告不可访问", "RESOURCE_NOT_FOUND"),
        ("run-409", 409, "html", "报告快照已变化，请重新导出", "REPORT_EXPORT_SNAPSHOT_CHANGED"),
        ("run-413", 413, "markdown", "完整报告超过 16 MiB 同步上限", "REPORT_EXPORT_LIMIT_EXCEEDED"),
    ]
    for run_id, status, export_format, message, code in scenarios:
        state.plan_export(
            run_id, export_format, status=status, body=json_bytes(message, code),
            content_type="application/json; charset=utf-8",
        )
        await assert_error_without_download(
            page, state, base_url, run_id, export_format, message, downloads,
        )
    checks.append("404 409 and 413 Blob errors preserve bounded safe backend messages without retry")

    oversized_marker = "OVERSIZED_ERROR_BODY_MUST_NOT_BE_DISPLAYED"
    oversized_body = json_bytes(oversized_marker + ("x" * (70 * 1024)))
    assert len(oversized_body) > 64 * 1024
    state.plan_export(
        "run-oversized-error", "markdown", status=413, body=oversized_body,
        content_type="application/json; charset=utf-8",
    )
    await assert_error_without_download(
        page, state, base_url, "run-oversized-error", "markdown",
        "完整报告超过同步导出上限", downloads,
    )
    await expect(page.get_by_text(oversized_marker, exact=False)).to_have_count(0)
    checks.append("oversized Blob error bodies are not parsed or exposed")

    state.plan_export("run-network", "html", gated=False, abort=True)
    await assert_error_without_download(
        page, state, base_url, "run-network", "html", "网络连接失败", downloads,
    )
    checks.append("network failure is understandable and is not retried")


async def validate_availability_and_archive(
    page: Page,
    state: State,
    base_url: str,
    checks: list[str],
) -> None:
    gate = state.gate_detail("run-detail-wait")
    navigation = asyncio.create_task(page.goto(f"{base_url}/reports/run-detail-wait"))
    await asyncio.wait_for(gate.started.wait(), 5)
    assert await page.get_by_test_id("export-markdown").count() == 0
    assert not [item for item in state.export_requests if item["run_id"] == "run-detail-wait"]
    gate.release.set()
    await navigation
    await expect(page.get_by_test_id("export-markdown")).to_be_visible()
    checks.append("export actions are unavailable until the authorized report detail is ready")

    await goto_report(page, base_url, "run-running")
    await expect(page.get_by_text("导出时刻的当前快照", exact=False)).to_be_visible()
    checks.append("non-terminal reports describe a changing current snapshot")

    archived_body = "# 归档项目授权历史报告\n完整内容\n".encode()
    state.plan_export(
        "run-archived", "markdown", body=archived_body,
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="ai-test-report-run-archived.md"',
    )
    await goto_report(page, base_url, "run-archived")
    await expect(page.get_by_text("Archive", exact=True)).to_be_visible()
    async with page.expect_download() as archived_info:
        await page.get_by_test_id("export-markdown").click()
    archived_download = await archived_info.value
    assert await download_bytes(archived_download) == archived_body
    checks.append("authorized archived-project history remains exportable")


async def validate_context_lifecycle(
    page: Page,
    state: State,
    base_url: str,
    out_dir: Path,
    checks: list[str],
    downloads: list[str],
) -> None:
    old_run = state.plan_export(
        "run-race", "markdown", gated=True, body=b"# stale run response\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="stale-run.md"',
    )
    current_run = state.plan_export(
        "run-race", "markdown", gated=True, body=b"# current run response\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="current-run.md"',
    )
    await goto_report(page, base_url, "run-race")
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(old_run.started.wait(), 5)
    await spa_report_navigation(page, "run-other")
    await spa_report_navigation(page, "run-race")
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(current_run.started.wait(), 5)
    before = len(downloads)
    old_run.release.set()
    await page.wait_for_timeout(200)
    assert len(downloads) == before
    await expect(page.get_by_test_id("export-markdown")).to_be_disabled()
    async with page.expect_download() as current_info:
        current_run.release.set()
    current_download = await current_info.value
    assert current_download.suggested_filename == "current-run.md"
    checks.append("Run A-to-B-to-A drops stale success without ending the current export loading")

    old_refresh = state.plan_export(
        "run-refresh", "html", gated=True,
        body=b"<!doctype html><html><body>stale refresh</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="stale-refresh.html"',
    )
    current_refresh = state.plan_export(
        "run-refresh", "html", gated=True,
        body=b"<!doctype html><html><body>current refresh</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="current-refresh.html"',
    )
    await goto_report(page, base_url, "run-refresh")
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(old_refresh.started.wait(), 5)
    await page.get_by_role("button", name="刷新", exact=True).click()
    await expect(page.get_by_test_id("export-html")).to_be_visible()
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(current_refresh.started.wait(), 5)
    before = len(downloads)
    old_refresh.release.set()
    await page.wait_for_timeout(200)
    assert len(downloads) == before
    await expect(page.get_by_test_id("export-html")).to_be_disabled()
    async with page.expect_download() as refresh_info:
        current_refresh.release.set()
    refresh_download = await refresh_info.value
    assert refresh_download.suggested_filename == "current-refresh.html"
    checks.append("detail refresh drops stale success without ending the newer export loading")

    stale_unmount = state.plan_export(
        "run-unmount", "markdown", gated=True, status=409,
        body=json_bytes("卸载后的旧错误不可见", "REPORT_EXPORT_SNAPSHOT_CHANGED"),
        content_type="application/json; charset=utf-8",
    )
    await goto_report(page, base_url, "run-unmount")
    await clear_messages(page)
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(stale_unmount.started.wait(), 5)
    await page.get_by_role("link", name="测试报告", exact=True).click()
    await expect(page.get_by_role("heading", name="测试报告", exact=True)).to_be_visible()
    before = len(downloads)
    stale_unmount.release.set()
    await page.wait_for_timeout(250)
    assert len(downloads) == before
    assert await page.locator(".el-message--error").count() == 0
    checks.append("component unmount suppresses a delayed export failure and any download")
    await page.screenshot(path=str(out_dir / "report-export-lifecycle.png"), full_page=True)


async def login(page: Page) -> None:
    await page.get_by_role("button", name="登录平台", exact=True).click()
    await page.wait_for_url(lambda url: urlparse(url).path == "/")


async def validate_identity_and_401(
    page: Page,
    state: State,
    base_url: str,
    checks: list[str],
    downloads: list[str],
) -> None:
    state.plan_export(
        "run-401", "markdown", status=401,
        body=json_bytes("登录已失效", "AUTH_ERROR"),
        content_type="application/json; charset=utf-8",
    )
    await goto_report(page, base_url, "run-401")
    await clear_messages(page)
    before = len(downloads)
    await page.get_by_test_id("export-markdown").click()
    await page.wait_for_url(lambda url: urlparse(url).path == "/login")
    assert len(downloads) == before
    local_storage = await page.evaluate("localStorage.getItem('access_token')")
    assert local_storage is None, f"401 should clear access token, still found {local_storage}"
    assert await page.locator(".el-message--error").count() == 0
    checks.append("401 keeps the shared identity interceptor redirect and does not save an attachment")

    await login(page)
    assert await page.evaluate("localStorage.getItem('access_token')") == "token-login-2"

    old_identity = state.plan_export(
        "run-identity", "markdown", gated=True, body=b"# old identity\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="old-identity.md"',
    )
    current_identity = state.plan_export(
        "run-identity", "html", gated=True,
        body=b"<!doctype html><html><body>new identity</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="new-identity.html"',
    )
    await spa_report_navigation(page, "run-identity")
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(old_identity.started.wait(), 5)
    await page.locator("button.user-trigger").click()
    await page.get_by_text("退出登录", exact=True).click()
    await page.wait_for_url(lambda url: urlparse(url).path == "/login")
    await login(page)
    assert await page.evaluate("localStorage.getItem('access_token')") == "token-login-3"
    await spa_report_navigation(page, "run-identity")
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(current_identity.started.wait(), 5)
    before = len(downloads)
    old_identity.release.set()
    await page.wait_for_timeout(200)
    assert len(downloads) == before
    await expect(page.get_by_test_id("export-html")).to_be_disabled()
    async with page.expect_download() as identity_info:
        current_identity.release.set()
    identity_download = await identity_info.value
    assert identity_download.suggested_filename == "new-identity.html"

    identity_requests = [item for item in state.export_requests if item["run_id"] == "run-identity"]
    assert [item["authorization"] for item in identity_requests] == [
        "Bearer token-login-2", "Bearer token-login-3",
    ]
    checks.append("re-login invalidates the old identity response without disturbing new identity loading")


async def validate_cross_tab_storage_lifecycle(
    context: BrowserContext,
    page: Page,
    state: State,
    base_url: str,
    checks: list[str],
    downloads: list[str],
) -> None:
    token_a = "token-login-3"
    token_b = "cross-tab-user-b-token"
    user_b = {
        "id": "cross-tab-b",
        "username": "cross-tab-b",
        "display_name": "Cross Tab B",
        "roles": ["VIEWER"],
    }
    token_d = "cross-tab-user-d-token"
    user_d = {
        "id": "cross-tab-d",
        "username": "cross-tab-d",
        "display_name": "Cross Tab D",
        "roles": ["TESTER"],
    }
    old_success = state.plan_export(
        "run-cross-tab-success", "markdown", gated=True, body=b"# old cross-tab identity\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="old-cross-tab.md"',
    )
    current_success = state.plan_export(
        "run-cross-tab-success", "html", gated=True,
        body=b"<!doctype html><html><body>current cross-tab identity</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="current-cross-tab.html"',
    )
    await spa_report_navigation(page, "run-cross-tab-success")
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(old_success.started.wait(), 5)

    other = await context.new_page()
    await other.goto(f"{base_url}/login")
    await other.evaluate(
        "identity => { localStorage.setItem('access_token', identity.token); "
        "localStorage.setItem('current_user', JSON.stringify(identity.user)); }",
        {"token": token_b, "user": user_b},
    )
    await expect(page.locator("button.user-trigger")).to_contain_text("Cross Tab B")
    await expect(page.get_by_test_id("export-html")).to_be_visible()
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(current_success.started.wait(), 5)
    before = len(downloads)
    old_success.release.set()
    await page.wait_for_timeout(200)
    assert len(downloads) == before
    await expect(page.get_by_test_id("export-html")).to_be_disabled()
    async with page.expect_download() as current_success_info:
        current_success.release.set()
    current_success_download = await current_success_info.value
    assert current_success_download.suggested_filename == "current-cross-tab.html"
    success_requests = [
        item for item in state.export_requests
        if item["run_id"] == "run-cross-tab-success"
    ]
    assert [item["authorization"] for item in success_requests] == [
        f"Bearer {token_a}", f"Bearer {token_b}",
    ]
    checks.append("another tab identity change drops old success and reloads detail before a new-token export")

    stale_401 = state.plan_export(
        "run-cross-tab-401", "markdown", gated=True, status=401,
        body=json_bytes("old cross-tab identity expired", "AUTH_ERROR"),
        content_type="application/json; charset=utf-8",
    )
    current_after_401 = state.plan_export(
        "run-cross-tab-401", "html", gated=True,
        body=b"<!doctype html><html><body>current identity survives stale 401</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="current-after-stale-401.html"',
    )
    await spa_report_navigation(page, "run-cross-tab-401")
    await clear_messages(page)
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(stale_401.started.wait(), 5)
    await other.evaluate(
        "identity => { localStorage.setItem('access_token', identity.token); "
        "localStorage.setItem('current_user', JSON.stringify(identity.user)); }",
        {"token": token_d, "user": user_d},
    )
    await expect(page.locator("button.user-trigger")).to_contain_text("Cross Tab D")
    await expect(page.get_by_test_id("export-html")).to_be_visible()
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(current_after_401.started.wait(), 5)
    before = len(downloads)
    stale_401.release.set()
    await page.wait_for_timeout(250)
    assert len(downloads) == before
    assert await page.evaluate("localStorage.getItem('access_token')") == token_d
    assert urlparse(page.url).path != "/login"
    assert await page.locator(".el-message--error").count() == 0
    await expect(page.get_by_test_id("export-html")).to_be_disabled()
    async with page.expect_download() as current_after_401_info:
        current_after_401.release.set()
    current_after_401_download = await current_after_401_info.value
    assert current_after_401_download.suggested_filename == "current-after-stale-401.html"
    stale_401_requests = [
        item for item in state.export_requests
        if item["run_id"] == "run-cross-tab-401"
    ]
    assert [item["authorization"] for item in stale_401_requests] == [
        f"Bearer {token_b}", f"Bearer {token_d}",
    ]
    checks.append("a stale cross-tab 401 cannot erase or redirect the new identity or end its loading")

    old_failure = state.plan_export(
        "run-cross-tab-aba", "markdown", gated=True, status=409,
        body=json_bytes("old cross-tab A-to-B-to-A error", "REPORT_EXPORT_SNAPSHOT_CHANGED"),
        content_type="application/json; charset=utf-8",
    )
    current_aba = state.plan_export(
        "run-cross-tab-aba", "html", gated=True,
        body=b"<!doctype html><html><body>current after identity ABA</body></html>",
        content_type="text/html; charset=utf-8",
        disposition='attachment; filename="current-cross-tab-aba.html"',
    )
    await spa_report_navigation(page, "run-cross-tab-aba")
    await clear_messages(page)
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(old_failure.started.wait(), 5)
    await other.evaluate(
        "values => {"
        "localStorage.setItem('access_token', values.tokenC);"
        "localStorage.setItem('current_user', JSON.stringify(values.userC));"
        "localStorage.setItem('access_token', values.tokenB);"
        "localStorage.setItem('current_user', JSON.stringify(values.userB));"
        "}",
        {
            "tokenB": token_d,
            "userB": user_d,
            "tokenC": "cross-tab-user-c-token",
            "userC": {
                "id": "cross-tab-c", "username": "cross-tab-c",
                "display_name": "Cross Tab C", "roles": ["TESTER"],
            },
        },
    )
    await page.wait_for_timeout(300)
    await expect(page.get_by_test_id("export-html")).to_be_visible()
    await page.get_by_test_id("export-html").click()
    await asyncio.wait_for(current_aba.started.wait(), 5)
    before = len(downloads)
    old_failure.release.set()
    await page.wait_for_timeout(200)
    assert len(downloads) == before
    assert await page.locator(".el-message--error").count() == 0
    await expect(page.get_by_test_id("export-html")).to_be_disabled()
    async with page.expect_download() as current_aba_info:
        current_aba.release.set()
    current_aba_download = await current_aba_info.value
    assert current_aba_download.suggested_filename == "current-cross-tab-aba.html"
    aba_requests = [
        item for item in state.export_requests
        if item["run_id"] == "run-cross-tab-aba"
    ]
    assert [item["authorization"] for item in aba_requests] == [
        f"Bearer {token_d}", f"Bearer {token_d}",
    ]
    checks.append("cross-tab identity A-to-B-to-A generation suppresses old failure and preserves new loading")

    old_logout = state.plan_export(
        "run-cross-tab-logout", "markdown", gated=True, body=b"# old logged-out identity\n",
        content_type="text/markdown; charset=utf-8",
        disposition='attachment; filename="old-logged-out.md"',
    )
    await spa_report_navigation(page, "run-cross-tab-logout")
    await clear_messages(page)
    await page.get_by_test_id("export-markdown").click()
    await asyncio.wait_for(old_logout.started.wait(), 5)
    await other.evaluate("localStorage.clear()")
    await page.wait_for_url(lambda url: urlparse(url).path == "/login")
    before = len(downloads)
    old_logout.release.set()
    await page.wait_for_timeout(250)
    assert len(downloads) == before
    assert await page.locator(".el-message--error").count() == 0
    checks.append("cross-tab logout clears old report detail and suppresses the pending attachment")
    await other.close()


async def run_browser(base_url: str, out_dir: Path) -> dict[str, Any]:
    state = State()
    checks: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    downloads: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1050},
            accept_downloads=True,
        )
        await context.add_init_script("""
          if (!sessionStorage.getItem('p6_x2_identity_initialized')
              && !localStorage.getItem('access_token')) {
            localStorage.setItem('access_token', 'token-one');
            localStorage.setItem('current_user', JSON.stringify({id:'u1',username:'mock-u1',display_name:'Mock u1',roles:['TESTER']}));
          }
          sessionStorage.setItem('p6_x2_identity_initialized', '1');
          window.__p6x2 = { created: [], revoked: [], anchorClicks: 0 };
          const originalCreateObjectURL = URL.createObjectURL.bind(URL);
          const originalRevokeObjectURL = URL.revokeObjectURL.bind(URL);
          const originalAnchorClick = HTMLAnchorElement.prototype.click;
          URL.createObjectURL = blob => {
            const value = originalCreateObjectURL(blob);
            window.__p6x2.created.push(value);
            return value;
          };
          URL.revokeObjectURL = value => {
            window.__p6x2.revoked.push(value);
            return originalRevokeObjectURL(value);
          };
          HTMLAnchorElement.prototype.click = function() {
            if (this.download && this.href.startsWith('blob:')) window.__p6x2.anchorClicks += 1;
            return originalAnchorClick.call(this);
          };
        """)
        page = await context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("download", lambda download: downloads.append(download.suggested_filename))
        await page.route("**/api/v1/**", lambda route: handle_api(state, route))

        await validate_success_and_resources(page, state, base_url, out_dir, checks)
        await validate_duplicate_and_response_safety(page, state, base_url, out_dir, checks, downloads)
        await validate_error_matrix(page, state, base_url, checks, downloads)
        await validate_availability_and_archive(page, state, base_url, checks)
        await validate_context_lifecycle(page, state, base_url, out_dir, checks, downloads)
        await validate_identity_and_401(page, state, base_url, checks, downloads)
        await validate_cross_tab_storage_lifecycle(
            context, page, state, base_url, checks, downloads,
        )

        for plan in state.export_plans:
            plan.release.set()
        for gate in state.detail_gates:
            gate.release.set()
        await context.close()
        await browser.close()

    assert all(plan.claimed for plan in state.export_plans), "some planned exports were not requested"
    assert not state.unknown, f"unhandled API requests: {state.unknown}"
    assert not state.business_mutations, f"unexpected business mutations: {state.business_mutations}"
    assert not page_errors, f"page errors: {page_errors}"
    expected_console_errors = [
        message for message in console_errors
        if message.startswith("Failed to load resource: the server responded with a status of ")
        or "net::ERR_FAILED" in message
    ]
    unexpected_console_errors = [message for message in console_errors if message not in expected_console_errors]
    assert not unexpected_console_errors, f"unexpected console errors: {unexpected_console_errors}"
    return {
        "checks": checks,
        "export_requests": len(state.export_requests),
        "downloads": len(downloads),
        "login_requests": len(state.login_requests),
        "business_mutations": 0,
        "page_errors": 0,
        "unexpected_console_errors": 0,
        "expected_negative_http_console_events": len(expected_console_errors),
        "screenshots": sorted(path.name for path in out_dir.glob("*.png")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--dist", type=Path, default=root / ".codex-validation" / "p6-x2" / "dist")
    parser.add_argument("--out", type=Path, default=root / ".codex-validation" / "p6-x2")
    args = parser.parse_args()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietSpaHandler, directory=str(args.dist.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(run_browser(f"http://127.0.0.1:{server.server_port}", out_dir))
        (out_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
