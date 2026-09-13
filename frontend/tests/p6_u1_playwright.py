"""Controlled-response Chrome validation for P6-U1 frontend behavior.

The SPA is served from the isolated production build and every API response is
synthetic. No formal backend, asset, Run, AI service, or credential is used.
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
PROJECTS = [
    {"id": 1, "name": "Alpha", "code": "ALPHA", "description": None, "status": "ACTIVE", "owner_id": "u1", "created_at": NOW, "updated_at": NOW, "archived_at": None},
    {"id": 2, "name": "Beta", "code": "BETA", "description": None, "status": "ACTIVE", "owner_id": "u1", "created_at": NOW, "updated_at": NOW, "archived_at": None},
    {"id": 3, "name": "Archive", "code": "ARCH", "description": None, "status": "ARCHIVED", "owner_id": "u1", "created_at": NOW, "updated_at": NOW, "archived_at": NOW},
    {"id": 4, "name": "Empty", "code": "EMPTY", "description": None, "status": "ACTIVE", "owner_id": "u1", "created_at": NOW, "updated_at": NOW, "archived_at": None},
    {"id": 5, "name": "Denied", "code": "DENIED", "description": None, "status": "ACTIVE", "owner_id": "u1", "created_at": NOW, "updated_at": NOW, "archived_at": None},
]
MALICIOUS_TEXT = '<img src=x onerror="window.__p6Injected=1"><script>window.__p6Injected=2</script>'


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "unit": "RATIO",
        "definition": "成功用例数 /（成功 + 失败 + 待复核 + 超时用例数）；已跳过、已取消与未完成用例不进入分母",
    }


def summary(
    run_id: str,
    run_type: str = "WEB_CASE",
    status: str = "SUCCESS",
    project_id: int = 1,
    project_name: str = "Alpha",
    case_total: int = 1,
) -> dict[str, Any]:
    failed = 1 if status == "FAILED" else 0
    timeout = 1 if status == "TIMEOUT" else 0
    cancelled = 1 if status == "CANCELLED" else 0
    success = case_total if status == "SUCCESS" or run_id == "run-platform-failed" else 0
    asset_id = {"API_CASE": 11, "SCENARIO": 21, "WEB_CASE": 31}[run_type]
    return {
        "run_id": run_id,
        "run_code": run_id.upper(),
        "run_type": run_type,
        "project": {"id": project_id, "name": project_name, "status": "ARCHIVED" if project_id == 3 else "ACTIVE", "metadata_basis": "CURRENT"},
        "environment": {"id": 9, "name": "测试环境", "enabled": True, "available": True, "metadata_basis": "CURRENT"},
        "runner": {"id": "runner-1", "name": "Runner One", "status": "ACTIVE", "available": True, "metadata_basis": "CURRENT"},
        "target": {
            "kind": run_type, "asset_id": asset_id, "version_id": 101,
            "asset_name": f"{run_type} Target", "asset_code": f"T-{asset_id}",
            "asset_status": "ACTIVE", "current_version_id": 102, "version_no": 1,
            "version_status": "APPROVED", "version_available": True,
            "is_current_version": False, "asset_metadata_basis": "CURRENT",
            "version_basis": "LOCKED_BY_RUN", "definition_exposure": "REFERENCE_ONLY",
        },
        "status": status, "trigger_type": "MANUAL", "started_at": NOW,
        "ended_at": NOW, "created_at": NOW, "updated_at": NOW,
        "duration": {"milliseconds": 1234, "kind": "COMPLETED", "observed_at": NOW},
        "error_type": "WEB_EVIDENCE_ERROR" if run_id == "run-platform-failed" else None,
        "error_message": "平台证据上传失败" if run_id == "run-platform-failed" else None,
        "recorded_counts": {"total": case_total, "passed": 0 if failed else success, "failed": failed, "review": 0, "timeout": timeout, "source": "RUN_RECORD"},
        "case_status_counts": {
            "created": 0, "assigned": 0, "running": 0, "cancelling": 0,
            "success": success, "failed": 0 if run_id == "run-platform-failed" else failed,
            "review": 0, "timeout": timeout, "cancelled": cancelled, "skipped": 0,
            "unfinished": 0, "total": case_total, "source": "CASE_RUN_GROUPING",
        },
        "case_success_rate": rate(success, success + (0 if run_id == "run-platform-failed" else failed) + timeout),
    }


def data_field(availability: str, value: Any, note: str) -> dict[str, Any]:
    return {"availability": availability, "value": value, "note": note}


def report_case(run_id: str, index: int, run_type: str) -> dict[str, Any]:
    case_id = 500 + index
    recorded = run_type != "SCENARIO"
    return {
        "id": case_id, "sequence_no": index + 1, "run_id": run_id,
        "target": summary(run_id, run_type)["target"], "status": "SUCCESS",
        "duration_ms": 200 + index, "retry_count": 0, "started_at": NOW,
        "ended_at": NOW, "error_type": None, "error_message": None,
        "execution_result": {
            "kind": run_type, "availability": "RECORDED" if recorded else "NOT_RECORDED",
            "message_id": f"msg-{case_id}" if recorded else None,
            "outcome": "SUCCESS" if recorded else None, "status": "SUCCESS" if recorded else None,
            "retry_count": 0 if recorded else None, "error_type": None,
            "error_message": None, "completed_at": NOW if recorded else None,
            "actual_request": data_field(
                "RECORDED" if run_type == "API_CASE" else "NOT_APPLICABLE",
                {
                    "method": "POST",
                    "url": "https://api.example.test/login",
                    "headers": [{"name": "Authorization", "value": "<redacted>"}],
                    "body": {"type": "JSON", "content": {"username": "demo", "password": "<redacted>"}},
                } if run_type == "API_CASE" else None,
                "执行锁定版本中的请求配置快照；敏感值已脱敏，动态变量和 Secret 保留引用，不代表解析后的网络报文"
                if run_type == "API_CASE" else "该执行类型不使用单一 API 请求配置",
            ),
            "response": data_field("RECORDED" if run_type == "API_CASE" else "NOT_APPLICABLE", {"safe_text": MALICIOUS_TEXT} if run_type == "API_CASE" and index == 0 else {"status_code": 200}, "执行时保存的安全响应摘要"),
            "extractions": data_field("NOT_RECORDED", None, "执行时提取结果未持久化"),
            "assertions": data_field("RECORDED" if run_type == "API_CASE" else "NOT_APPLICABLE", [{"status": "PASS"}], "执行时保存的断言结果"),
            "traces": data_field(
                "RECORDED" if run_type != "API_CASE" else "NOT_APPLICABLE",
                [{"node_id": "action_1", "status": "SUCCESS", "safe_text": MALICIOUS_TEXT if run_id == "run-platform-failed" and index == 0 else "safe"}],
                "执行时保存的安全 Trace",
            ),
        },
    }


def step_item(index: int, case_run_id: int = 500) -> dict[str, Any]:
    return {
        "id": 900 + index, "case_run_id": case_run_id, "sequence_no": index + 1,
        "node_id": f"node_{index + 1}", "name": f"步骤 {index + 1}", "type": "CLICK",
        "status": "SUCCESS", "duration_ms": 20, "retry_count": 0,
        "started_at": NOW, "ended_at": NOW, "error_type": None, "error_message": None,
    }


def evidence_item(index: int, run_id: str = "run-platform-failed") -> dict[str, Any]:
    artifact_id = "artifact-fail" if index == 1 else "artifact-success" if index == 0 else f"artifact-{index}"
    return {
        "id": artifact_id, "project_id": 1, "run_id": run_id,
        "case_run_id": 500, "step_run_id": 900 + index, "artifact_type": "RESPONSE",
        "file_name": f"evidence-{index}.json", "mime": "application/json", "size": 128 + index,
        "sha256": f"{index % 10}" * 64, "metadata": {"safe": MALICIOUS_TEXT if index == 0 else True},
        "created_at": NOW, "download_path": f"https://untrusted.invalid/{artifact_id}",
    }


def page_payload(items: list[dict[str, Any]], total: int, page: int, size: int, run_id: str, kind: str, **filters: Any) -> dict[str, Any]:
    payload = {"run_id": run_id, "items": items, "total": total, "page": page, "page_size": size, "has_more": page * size < total, "next_page": page + 1 if page * size < total else None, "continuation_path": f"/api/v1/reports/{run_id}/{kind}?page={page + 1}" if page * size < total else None}
    payload.update(filters)
    return payload


def detail_payload(run_id: str) -> dict[str, Any]:
    run_type = "API_CASE" if run_id == "run-api" else "SCENARIO" if run_id == "run-scenario" else "WEB_CASE"
    total = 21 if run_id == "run-platform-failed" else 1
    cases = [report_case(run_id, index, run_type) for index in range(min(total, 20))]
    steps = [step_item(index) for index in range(50 if total > 1 else 1)]
    evidence = [evidence_item(index, run_id) for index in range(50 if total > 1 else 1)]
    return {
        "summary": summary(run_id, run_type, "FAILED" if run_id == "run-platform-failed" else "SUCCESS", case_total=total),
        "cases": page_payload(cases, total, 1, 20, run_id, "cases"),
        "steps": page_payload(steps, 51 if total > 1 else 1, 1, 50, run_id, "steps", case_run_id=None),
        "evidence": page_payload(evidence, 51 if total > 1 else 1, 1, 50, run_id, "evidence", case_run_id=None, step_run_id=None),
        "related_ai": {
            "failure_analyses": {"total": 1, "latest": {"id": 2, "kind": "WEB_FAILURE_ANALYSIS", "case_run_id": 500, "status": "COMPLETED", "ai_call_id": 20, "created_at": NOW, "summary": {"safe": True}}, "has_more": False, "continuation_path": f"/api/v1/runs/{run_id}/web-failure-analyses"},
            "healing_proposals": {"total": 2, "latest": {"id": 3, "kind": "WEB_HEALING_PROPOSAL", "case_run_id": 500, "status": "ACCEPTED", "ai_call_id": 18, "created_at": NOW, "summary": {"safe": True}}, "has_more": False, "continuation_path": f"/api/v1/runs/{run_id}/web-healing-proposals"},
            "raw_ai_content_exposed": False,
        },
        "pagination_note": "cases、steps、evidence 均为首屏有界分页；has_more=true 时继续读取",
    }


def dashboard_payload(project_id: int | None) -> dict[str, Any]:
    empty = project_id == 4
    unknown = project_id == 2
    return {
        "generated_at": NOW, "timezone": "Asia/Shanghai", "project_scope_id": project_id,
        "active_project_count": 0 if empty else 2,
        "date_range": {"timezone": "Asia/Shanghai", "start_utc": "2026-09-09T16:00:00Z", "end_utc_exclusive": "2026-09-10T16:00:00Z", "run_time_field": "created_at"},
        "cases": {"api_cases": 0 if empty else 4, "web_cases": 0 if empty else 3, "case_total": 0 if empty else 7, "scenarios": 0 if empty else 2, "definition": "API/Web Case 只统计非归档资产，Scenario 单独统计"},
        "today_runs": {"total": 0 if empty else 6, "success": 0 if empty else 2, "failed": 0 if empty else 1, "timeout": 0 if empty else 1, "cancelled": 0 if empty else 1, "unfinished": 0 if empty else 1, "success_rate": rate(0 if empty else 2, 0 if project_id in (None, 4) else 4)},
        "pending_reviews": {"requirement_reviews": 0, "ai_case_suggestions": 0, "web_recording_ai_suggestions": 0, "web_healing_proposals": 0, "total": 0, "definition": "仅统计四类可处理 DRAFT"},
        "runners": {"visibility": "VISIBLE" if unknown else "ADMIN_ONLY", "available": False if unknown else None, "status": "UNKNOWN" if unknown else "HIDDEN", "registered_total": None, "active_total": None, "online": None, "offline": None, "unknown": None},
        "recent_runs": [] if empty else [
            {"run_id": "run-cancelled", "run_code": "RUN-CANCELLED", "run_type": "API_CASE", "project_id": 1, "project_name": "Alpha", "status": "CANCELLED", "created_at": NOW, "ended_at": NOW, "report_path": "/api/v1/reports/run-cancelled"},
            {"run_id": "run-timeout", "run_code": "RUN-TIMEOUT", "run_type": "WEB_CASE", "project_id": 1, "project_name": "Alpha", "status": "TIMEOUT", "created_at": NOW, "ended_at": NOW, "report_path": "/api/v1/reports/run-timeout"},
        ],
        "read_only": True,
    }


class State:
    def __init__(self) -> None:
        self.unknown: list[str] = []
        self.mutations: list[str] = []
        self.queries: list[dict[str, list[str]]] = []
        self.delay_reports_project1 = False
        self.report1_started = asyncio.Event()
        self.report1_release = asyncio.Event()
        self.delay_dashboard_project1 = False
        self.dashboard1_started = asyncio.Event()
        self.dashboard1_release = asyncio.Event()
        self.section_gates: list[dict[str, Any]] = []
        self.section_queries: list[dict[str, Any]] = []


async def fulfill_json(route: Route, body: Any, status: int = 200) -> None:
    await route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


async def handle_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    query = parse_qs(parsed.query)
    if method not in {"GET", "HEAD", "OPTIONS"}:
        state.mutations.append(f"{method} {path}")
        return await fulfill_json(route, {"message": "mutation blocked"}, 405)
    if path == "/api/v1/projects":
        return await fulfill_json(route, {"items": PROJECTS, "total": len(PROJECTS)})
    if path == "/api/v1/reports":
        state.queries.append(query)
        project_id = int(query.get("project_id", ["1"])[0])
        if project_id == 5:
            return await fulfill_json(route, {"message": "forbidden"}, 403)
        if project_id == 4:
            return await fulfill_json(route, {"items": [], "total": 0, "page": 1, "page_size": 20, "has_more": False})
        if state.delay_reports_project1 and project_id == 1:
            state.report1_started.set()
            await state.report1_release.wait()
        page = int(query.get("page", ["1"])[0])
        if project_id == 2:
            items = [summary("run-beta", "SCENARIO", project_id=2, project_name="Beta")]
            return await fulfill_json(route, {"items": items, "total": 1, "page": page, "page_size": 20, "has_more": False})
        if project_id == 3:
            items = [summary("run-archived", "API_CASE", project_id=3, project_name="Archive")]
            return await fulfill_json(route, {"items": items, "total": 1, "page": page, "page_size": 20, "has_more": False})
        total = 45
        start = (page - 1) * 20
        statuses = ["SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"]
        types = ["API_CASE", "SCENARIO", "WEB_CASE"]
        items = [summary(f"run-p{page}-{i}", types[(start + i) % 3], statuses[(start + i) % 4]) for i in range(min(20, total - start))]
        return await fulfill_json(route, {"items": items, "total": total, "page": page, "page_size": 20, "has_more": page * 20 < total})
    if path.startswith("/api/v1/reports/"):
        parts = path.split("/")
        run_id = parts[4]
        if len(parts) == 5:
            if run_id == "missing": return await fulfill_json(route, {"message": "not found"}, 404)
            return await fulfill_json(route, detail_payload(run_id))
        page = int(query.get("page", ["1"])[0])
        state.section_queries.append({"run_id": run_id, "section": parts[5], "query": query})
        for gate in state.section_gates:
            if gate["claimed"] or gate["run_id"] != run_id or gate["section"] != parts[5] or gate["page"] != page:
                continue
            gate["claimed"] = True
            gate["started"].set()
            await asyncio.wait_for(gate["release"].wait(), 15)
            if gate["status"] is not None:
                return await fulfill_json(route, {"message": gate["message"]}, gate["status"])
            break
        if parts[5] == "cases":
            items = [report_case(run_id, 20, "WEB_CASE")] if page == 2 else [report_case(run_id, i, "WEB_CASE") for i in range(20)]
            return await fulfill_json(route, page_payload(items, 21, page, 20, run_id, "cases"))
        if parts[5] == "steps":
            case_id = int(query["case_run_id"][0]) if "case_run_id" in query else None
            items = [step_item(50, case_id or 500)] if page == 2 else [step_item(i, case_id or 500) for i in range(50)]
            return await fulfill_json(route, page_payload(items, 51, page, 50, run_id, "steps", case_run_id=case_id))
        if parts[5] == "evidence":
            case_id = int(query["case_run_id"][0]) if "case_run_id" in query else None
            step_id = int(query["step_run_id"][0]) if "step_run_id" in query else None
            items = [evidence_item(50, run_id)] if page == 2 else [evidence_item(i, run_id) for i in range(50)]
            if step_id is not None: items = [{**items[0], "case_run_id": case_id or 500, "step_run_id": step_id}]
            return await fulfill_json(route, page_payload(items, 51 if step_id is None else 1, page, 50, run_id, "evidence", case_run_id=case_id, step_run_id=step_id))
    if path == "/api/v1/evidence":
        project_id = int(query.get("project_id", ["1"])[0])
        if project_id == 5: return await fulfill_json(route, {"message": "forbidden"}, 403)
        if project_id == 4: return await fulfill_json(route, {"items": [], "total": 0, "page": 1, "page_size": 20})
        page = int(query.get("page", ["1"])[0])
        run_id = query.get("run_id", ["run-filtered" if project_id == 3 else "run-platform-failed"])[0]
        total = 25
        start = (page - 1) * 20
        items = [evidence_item(start + i, run_id) for i in range(min(20, total - start))]
        return await fulfill_json(route, {"items": items, "total": total, "page": page, "page_size": 20})
    if path.startswith("/api/v1/evidence/") and path.endswith("/download"):
        artifact_id = path.split("/")[-2]
        if artifact_id == "artifact-fail": return await fulfill_json(route, {"message": "download denied"}, 500)
        return await route.fulfill(status=200, content_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="safe.json"'}, body=b"safe")
    if path == "/api/v1/dashboard":
        project_id = int(query["project_id"][0]) if "project_id" in query else None
        if project_id == 5: return await fulfill_json(route, {"message": "forbidden"}, 403)
        if state.delay_dashboard_project1 and project_id == 1:
            state.dashboard1_started.set()
            await state.dashboard1_release.wait()
        return await fulfill_json(route, dashboard_payload(project_id))
    state.unknown.append(f"{method} {path}")
    await fulfill_json(route, {"message": "unhandled"}, 404)


class QuietSpaHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None: pass
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        candidate = Path(self.directory or ".") / path.lstrip("/")
        if path != "/" and not candidate.exists() and "." not in Path(path).name:
            self.path = "/index.html"
        super().do_GET()


async def choose_select(container: Any, label: str) -> None:
    await container.click()
    await container.page.get_by_role("option", name=label, exact=True).click()


def add_section_gate(
    state: State,
    section: str,
    *,
    run_id: str = "run-platform-failed",
    page: int = 2,
    status: int | None = None,
    message: str = "delayed section error",
) -> dict[str, Any]:
    gate = {
        "section": section,
        "run_id": run_id,
        "page": page,
        "status": status,
        "message": message,
        "claimed": False,
        "started": asyncio.Event(),
        "release": asyncio.Event(),
    }
    state.section_gates.append(gate)
    return gate


def report_section(page: Page, section: str) -> Any:
    if section == "cases":
        return page.locator(".cases-card")
    heading = "步骤运行" if section == "steps" else "运行证据"
    return page.locator(".section-card").filter(has=page.get_by_role("heading", name=heading, exact=True))


async def spa_report_navigation(page: Page, run_id: str) -> None:
    await page.evaluate(
        "runId => { history.pushState({}, '', `/reports/${runId}`); window.dispatchEvent(new PopStateEvent('popstate')); }",
        run_id,
    )
    await expect(page.get_by_role("heading", name=run_id.upper())).to_be_visible()


async def validate_reports(page: Page, state: State, base_url: str, out_dir: Path, checks: list[str]) -> None:
    state.delay_reports_project1 = True
    await page.goto(f"{base_url}/reports")
    await expect(page.get_by_role("heading", name="测试报告")).to_be_visible()
    await asyncio.wait_for(state.report1_started.wait(), 5)
    project_select = page.locator(".filter-grid label").filter(has_text="项目").locator(".el-select")
    await choose_select(project_select, "Beta（BETA）")
    await expect(page.get_by_text("RUN-BETA", exact=True)).to_be_visible()
    state.report1_release.set()
    await page.wait_for_timeout(200)
    await expect(page.get_by_text("RUN-BETA", exact=True)).to_be_visible()
    checks.append("report project race ignores stale response")
    state.delay_reports_project1 = False

    await choose_select(project_select, "Archive（ARCH） · 已归档")
    await expect(page.get_by_text("归档项目历史只读", exact=True)).to_be_visible()
    await expect(page.get_by_text("RUN-ARCHIVED", exact=True)).to_be_visible()
    checks.append("archived project report remains readable")
    await choose_select(project_select, "Empty（EMPTY）")
    await expect(page.get_by_text("当前筛选条件下暂无报告", exact=True)).to_be_visible()
    checks.append("report empty state")
    await choose_select(project_select, "Denied（DENIED）")
    await expect(page.get_by_text("无权访问该项目报告", exact=False)).to_be_visible()
    checks.append("report authorization error and retry")
    await choose_select(project_select, "Alpha（ALPHA）")
    await expect(page.get_by_text("RUN-P1-0", exact=True)).to_be_visible()
    type_select = page.locator(".filter-grid label").filter(has_text="运行类型").locator(".el-select")
    status_select = page.locator(".filter-grid label").filter(has_text="状态").locator(".el-select")
    await choose_select(type_select, "Web Case")
    await choose_select(status_select, "超时")
    await page.locator(".filter-grid label").filter(has_text="环境 ID").locator("input").fill("9")
    await page.locator("label.date-field .el-date-editor").click()
    date_panel = page.locator(".el-picker-panel:visible .el-date-range-picker__content.is-left")
    current_month_days = date_panel.locator("td:not(.prev-month):not(.next-month)")
    await current_month_days.get_by_text("1", exact=True).click()
    await current_month_days.get_by_text("10", exact=True).click()
    await page.get_by_role("button", name="查询", exact=True).click()
    await expect(page.get_by_text("RUN-P1-0", exact=True)).to_be_visible()
    assert any(
        q.get("run_type") == ["WEB_CASE"]
        and q.get("status") == ["TIMEOUT"]
        and q.get("environment_id") == ["9"]
        and q.get("created_from") == ["2026-09-01"]
        and q.get("created_to") == ["2026-09-10"]
        for q in state.queries
    )
    checks.append("report type status environment and Asia/Shanghai date filters")
    await page.locator(".report-list-card .el-pagination").get_by_text("2", exact=True).click()
    await expect(page.get_by_text("RUN-P2-0", exact=True)).to_be_visible()
    checks.append("report catalog server pagination")
    await page.screenshot(path=str(out_dir / "reports-catalog.png"), full_page=True)


async def validate_report_detail(page: Page, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await page.goto(f"{base_url}/reports/run-platform-failed")
    await expect(page.get_by_role("heading", name="RUN-PLATFORM-FAILED")).to_be_visible()
    await expect(page.get_by_text("平台运行失败，但已记录的用例运行均成功", exact=True)).to_be_visible()
    await expect(page.get_by_text("执行版本与当前版本不同", exact=True)).to_be_visible()
    await expect(page.get_by_text("暂无已评估结果", exact=True)).to_have_count(0)
    trace_field = page.locator('[data-field="traces"]').first
    await expect(trace_field.locator("pre")).to_contain_text("<img src=x onerror=")
    await expect(trace_field.locator("pre")).to_contain_text("<script>window.__p6Injected=2</script>")
    assert await trace_field.locator("img,script").count() == 0
    assert await page.evaluate("() => window.__p6Injected") is None
    await expect(page.locator('[data-field="actual_request"]').first.locator("pre")).to_have_text("不适用")
    checks.append("detail preserves platform failure and renders sensitive HTML as text")
    await page.locator(".cases-card .el-pagination").get_by_text("2", exact=True).click()
    await expect(page.get_by_text("第 21 项", exact=False)).to_be_visible()
    checks.append("case continuation page")
    step_section = page.locator(".section-card").filter(has_text="步骤运行")
    await step_section.locator(".el-pagination").get_by_text("2", exact=True).click()
    await expect(step_section.get_by_text("步骤 51", exact=True)).to_be_visible()
    await choose_select(step_section.locator(".section-filter .el-select"), "第 1 项 · T-31 · WEB_CASE Target")
    await step_section.get_by_role("button", name="应用过滤", exact=True).click()
    await expect(step_section.get_by_text("第 1 项 · T-31 · WEB_CASE Target", exact=True).first).to_be_visible()
    checks.append("step pagination and case filter")
    evidence_section = page.locator(".section-card").filter(has_text="运行证据")
    await evidence_section.locator(".el-pagination").get_by_text("2", exact=True).click()
    await expect(evidence_section.get_by_text("evidence-50.json", exact=True)).to_be_visible()
    selects = evidence_section.locator(".section-filter .el-select")
    await choose_select(selects.nth(0), "第 1 项 · T-31 · WEB_CASE Target")
    await choose_select(selects.nth(1), "第 51 步 · 步骤 51")
    await evidence_section.get_by_role("button", name="应用过滤", exact=True).click()
    await expect(evidence_section.get_by_text("第 51 步 · 步骤 51", exact=False)).to_be_visible()
    checks.append("evidence pagination and case/step filters")
    await expect(page.get_by_text("最新 #2", exact=False)).to_be_visible()
    await expect(page.get_by_text("最新 #3", exact=False)).to_be_visible()
    checks.append("AI audit references")
    await page.screenshot(path=str(out_dir / "report-detail.png"), full_page=True)
    for run_id, label in (("run-api", "API 用例"), ("run-scenario", "编排场景")):
        await page.goto(f"{base_url}/reports/{run_id}")
        await expect(page.get_by_text(label, exact=True).first).to_be_visible()
        if run_id == "run-api":
            request_field = page.locator('[data-field="actual_request"]').first
            await expect(request_field.locator("pre")).to_contain_text("POST")
            await expect(request_field.locator("pre")).to_contain_text("<redacted>")
            await expect(page.get_by_text("CaseRun #", exact=False)).to_have_count(0)
            await expect(page.get_by_text("资产 #", exact=False)).to_have_count(0)
    checks.append("API Scenario and Web report types")
    await page.goto(f"{base_url}/reports/missing")
    await expect(page.get_by_text("报告不存在，或当前用户无权访问", exact=False)).to_be_visible()
    checks.append("detail not-found state")


async def validate_report_detail_context_races(
    page: Page,
    state: State,
    base_url: str,
    out_dir: Path,
    checks: list[str],
) -> None:
    section_markers = {
        "cases": ("第 21 项", False, "第 1 项", False),
        "steps": ("步骤 51", True, "步骤 1", True),
        "evidence": ("evidence-50.json", True, "evidence-0.json", True),
    }

    for section, (old_marker, old_exact, first_marker, first_exact) in section_markers.items():
        gate = add_section_gate(state, section)
        try:
            await page.goto(f"{base_url}/reports/run-platform-failed")
            card = report_section(page, section)
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(gate["started"].wait(), 5)
            async with page.expect_response(
                lambda response: urlparse(response.url).path == "/api/v1/reports/run-platform-failed"
            ):
                await page.get_by_role("button", name="刷新", exact=True).click()
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                gate["release"].set()
            await page.wait_for_timeout(100)
            await expect(card.get_by_text(old_marker, exact=old_exact)).to_have_count(0)
            await expect(card.get_by_text(first_marker, exact=first_exact).first).to_be_visible()
        finally:
            gate["release"].set()
    checks.append("detail refresh invalidates late Case Step and Evidence successes")

    for section, (_, _, first_marker, first_exact) in section_markers.items():
        gate = add_section_gate(state, section, status=500, message=f"late {section} failure")
        try:
            await page.goto(f"{base_url}/reports/run-platform-failed")
            card = report_section(page, section)
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(gate["started"].wait(), 5)
            async with page.expect_response(
                lambda response: urlparse(response.url).path == "/api/v1/reports/run-platform-failed"
            ):
                await page.get_by_role("button", name="刷新", exact=True).click()
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                gate["release"].set()
            await page.wait_for_timeout(100)
            await expect(card.locator(".el-alert--error")).to_have_count(0)
            await expect(card.get_by_text(first_marker, exact=first_exact).first).to_be_visible()
        finally:
            gate["release"].set()
    checks.append("detail refresh ignores late section errors")

    for section, (old_marker, old_exact, first_marker, first_exact) in section_markers.items():
        gate = add_section_gate(state, section)
        try:
            await page.goto(f"{base_url}/reports/run-platform-failed")
            card = report_section(page, section)
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(gate["started"].wait(), 5)
            await spa_report_navigation(page, "run-api")
            await spa_report_navigation(page, "run-platform-failed")
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                gate["release"].set()
            await page.wait_for_timeout(100)
            await expect(card.get_by_text(old_marker, exact=old_exact)).to_have_count(0)
            await expect(card.get_by_text(first_marker, exact=first_exact).first).to_be_visible()
        finally:
            gate["release"].set()
    checks.append("detail context rejects stale A to B to A section responses")

    for section, (page_two_marker, marker_exact, _, _) in section_markers.items():
        old_gate = add_section_gate(state, section)
        new_gate = add_section_gate(state, section)
        try:
            await page.goto(f"{base_url}/reports/run-platform-failed")
            card = report_section(page, section)
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(old_gate["started"].wait(), 5)
            async with page.expect_response(
                lambda response: urlparse(response.url).path == "/api/v1/reports/run-platform-failed"
            ):
                await page.get_by_role("button", name="刷新", exact=True).click()
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(new_gate["started"].wait(), 5)
            loading_mask = card.locator(".el-loading-mask")
            await expect(loading_mask).to_be_visible()
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                old_gate["release"].set()
            await page.wait_for_timeout(100)
            await expect(loading_mask).to_be_visible()
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                new_gate["release"].set()
            await expect(loading_mask).to_be_hidden()
            await expect(card.get_by_text(page_two_marker, exact=marker_exact).first).to_be_visible()
        finally:
            old_gate["release"].set()
            new_gate["release"].set()
    checks.append("late finally cannot end newer section loading")
    await page.screenshot(path=str(out_dir / "report-refresh-context.png"), full_page=True)


async def validate_report_filter_lifecycle(
    page: Page,
    state: State,
    base_url: str,
    out_dir: Path,
    checks: list[str],
) -> None:
    for section, heading, page_two_marker in (
        ("steps", "步骤运行", "步骤 51"),
        ("evidence", "运行证据", "evidence-50.json"),
    ):
        gate = add_section_gate(state, section)
        query_start = len(state.section_queries)
        try:
            await page.goto(f"{base_url}/reports/run-platform-failed")
            card = page.locator(".section-card").filter(
                has=page.get_by_role("heading", name=heading, exact=True)
            )
            await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            await asyncio.wait_for(gate["started"].wait(), 5)
            await choose_select(
                card.locator(".section-filter .el-select").first,
                "第 1 项 · T-31 · WEB_CASE Target",
            )
            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                gate["release"].set()
            await expect(card.locator(".el-loading-mask:visible")).to_have_count(0)
            await expect(card.get_by_text(page_two_marker, exact=True).first).to_be_visible()

            section_queries = [
                item for item in state.section_queries[query_start:]
                if item["section"] == section
            ]
            assert section_queries[-1]["query"].get("page") == ["2"]
            assert "case_run_id" not in section_queries[-1]["query"]

            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                await card.get_by_role("button", name="应用过滤", exact=True).click()
            applied_query = [item for item in state.section_queries if item["section"] == section][-1]["query"]
            assert applied_query.get("page") == ["1"]
            assert applied_query.get("case_run_id") == ["500"]

            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                await card.locator(".el-pagination").get_by_text("2", exact=True).click()
            continued_query = [item for item in state.section_queries if item["section"] == section][-1]["query"]
            assert continued_query.get("page") == ["2"]
            assert continued_query.get("case_run_id") == ["500"]

            async with page.expect_response(
                lambda response, suffix=section: urlparse(response.url).path.endswith(f"/{suffix}")
            ):
                await card.get_by_role("button", name="清除", exact=True).click()
            cleared_query = [item for item in state.section_queries if item["section"] == section][-1]["query"]
            assert cleared_query.get("page") == ["1"]
            assert "case_run_id" not in cleared_query
        finally:
            gate["release"].set()
    checks.append("unapplied step and evidence drafts do not strand current loading")
    checks.append("applied filters persist across pagination and clear restores unfiltered queries")
    await page.screenshot(path=str(out_dir / "report-filter-lifecycle.png"), full_page=True)


async def validate_evidence(page: Page, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await page.goto(f"{base_url}/evidence")
    await expect(page.get_by_role("heading", name="证据中心")).to_be_visible()
    project_select = page.locator(".filter-row label").filter(has_text="项目").locator(".el-select")
    await choose_select(project_select, "Archive（ARCH） · 已归档")
    await expect(page.get_by_text("归档项目历史只读", exact=True)).to_be_visible()
    await expect(page.get_by_text("RUN RUN-FILTERED", exact=False).first).to_be_visible()
    checks.append("evidence archived project")
    await choose_select(project_select, "Alpha（ALPHA）")
    await page.locator(".filter-row label").filter(has_text="Run ID").locator("input").fill("run-filtered")
    await page.get_by_role("button", name="查询", exact=True).click()
    await expect(page.get_by_text("Run run-filtered", exact=False).first).to_be_visible()
    checks.append("evidence optional Run filter")
    await page.locator(".evidence-card .el-pagination").get_by_text("2", exact=True).click()
    await expect(page.get_by_text("evidence-20.json", exact=True)).to_be_visible()
    checks.append("evidence center server pagination")
    await page.get_by_role("button", name="清除", exact=True).click()
    first_row = page.locator(".evidence-card .el-table__row").first
    async with page.expect_download() as download_info:
        await first_row.get_by_role("button", name="下载", exact=True).click()
    download = await download_info.value
    assert download.suggested_filename == "evidence-0.json"
    checks.append("authenticated evidence download success")
    failed_row = page.locator(".evidence-card .el-table__row").nth(1)
    await failed_row.get_by_role("button", name="下载", exact=True).click()
    download_error = page.locator(".el-message--error").last
    await expect(download_error).to_be_visible()
    await expect(download_error).to_contain_text("download denied")
    checks.append("evidence download failure feedback")
    await choose_select(project_select, "Empty（EMPTY）")
    await expect(page.get_by_text("当前筛选条件下暂无证据", exact=True)).to_be_visible()
    await choose_select(project_select, "Denied（DENIED）")
    await expect(page.get_by_text("证据不存在，或当前用户无权访问", exact=False)).to_be_visible()
    checks.append("evidence empty and authorization states")
    await choose_select(project_select, "Alpha（ALPHA）")
    await expect(page.get_by_text("evidence-0.json", exact=True)).to_be_visible()
    await expect(page.locator(".el-message--error")).to_have_count(0, timeout=5_000)
    await page.screenshot(path=str(out_dir / "evidence-center.png"), full_page=True)


async def validate_dashboard(page: Page, state: State, base_url: str, out_dir: Path, checks: list[str]) -> None:
    await page.goto(base_url)
    await expect(page.get_by_role("heading", name="测试业务概览")).to_be_visible()
    await expect(page.get_by_text("暂无已评估结果", exact=True)).to_be_visible()
    await expect(page.get_by_text("仅管理员可见", exact=True)).to_be_visible()
    await expect(page.get_by_text("失败 / 超时 / 取消", exact=True)).to_be_visible()
    assert await page.get_by_text("Phase 5", exact=False).count() == 0
    checks.append("dashboard null rate hidden runner and separate terminal counts")
    scope_select = page.locator(".dashboard-actions .el-select")
    await choose_select(scope_select, "Beta（BETA）")
    await expect(page.get_by_text("状态未知", exact=True)).to_be_visible()
    await expect(page.get_by_text("未知不等于 0 台在线", exact=False)).to_be_visible()
    checks.append("dashboard unknown runner is not zero")
    await choose_select(scope_select, "Empty（EMPTY）")
    await expect(page.get_by_text("当前范围暂无最近 Run", exact=True)).to_be_visible()
    checks.append("dashboard empty state")
    await choose_select(scope_select, "Denied（DENIED）")
    await expect(page.get_by_text("项目不存在，或当前用户无权查看", exact=False)).to_be_visible()
    checks.append("dashboard authorization error and retry")
    state.delay_dashboard_project1 = True
    await choose_select(scope_select, "Alpha（ALPHA）")
    await asyncio.wait_for(state.dashboard1_started.wait(), 5)
    await choose_select(scope_select, "Beta（BETA）")
    await expect(page.get_by_text("状态未知", exact=True)).to_be_visible()
    state.dashboard1_release.set()
    await page.wait_for_timeout(200)
    await expect(page.get_by_text("状态未知", exact=True)).to_be_visible()
    checks.append("dashboard project race ignores stale response")
    await page.screenshot(path=str(out_dir / "dashboard.png"), full_page=True)


async def run_browser(base_url: str, out_dir: Path) -> dict[str, Any]:
    state, checks, script_errors, console_errors = State(), [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1050}, accept_downloads=True)
        await context.add_init_script("""
          localStorage.setItem('access_token', 'synthetic-p6-token');
          localStorage.setItem('current_user', JSON.stringify({id:'u1',username:'mock',display_name:'Mock User',roles:['TESTER']}));
        """)
        page = await context.new_page()
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        await page.route("**/api/v1/**", lambda route: handle_api(state, route))
        await validate_reports(page, state, base_url, out_dir, checks)
        await validate_report_detail(page, base_url, out_dir, checks)
        await validate_report_detail_context_races(page, state, base_url, out_dir, checks)
        await validate_report_filter_lifecycle(page, state, base_url, out_dir, checks)
        await validate_evidence(page, base_url, out_dir, checks)
        await validate_dashboard(page, state, base_url, out_dir, checks)
        await context.close()
        await browser.close()
    assert not state.unknown, f"unhandled API requests: {state.unknown}"
    assert not state.mutations, f"unexpected mutations: {state.mutations}"
    assert not script_errors, f"page errors: {script_errors}"
    expected_http_errors = [message for message in console_errors if message.startswith("Failed to load resource: the server responded with a status of ")]
    unexpected_console_errors = [message for message in console_errors if message not in expected_http_errors]
    assert not unexpected_console_errors, f"unexpected console errors: {unexpected_console_errors}"
    return {
        "checks": checks,
        "business_mutations": 0,
        "page_errors": 0,
        "unexpected_console_errors": 0,
        "expected_negative_http_console_events": len(expected_http_errors),
        "screenshots": sorted(path.name for path in out_dir.glob("*.png")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[2] / ".codex-validation" / "p6-u1" / "dist")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / ".codex-validation" / "p6-u1")
    parser.add_argument("--result-name", default="playwright-result.json")
    args = parser.parse_args()
    if Path(args.result_name).name != args.result_name:
        raise ValueError("--result-name must be a file name without directories")
    out_dir = args.out.resolve(); out_dir.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietSpaHandler, directory=str(args.dist.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = asyncio.run(run_browser(f"http://127.0.0.1:{server.server_port}", out_dir))
        (out_dir / args.result_name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    main()
