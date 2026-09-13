"""Strict read-only Playwright/Chrome audit for the real P5-E acceptance chain.

Only identifiers, terminal states, and safe audit metadata are written to the
result artifact. Apart from the normal development login request, every API
method other than GET/HEAD/OPTIONS is blocked by the browser route guard.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Locator, Page, Route, async_playwright, expect


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / ".codex-validation" / "p5e-live" / "result.json"
DEFAULT_ARTIFACTS_DIR = ROOT / ".codex-validation" / "p5e-frontend-h4"
EXPECTED_ACCEPTANCE_ID = "V1P5E_20260909_F294AC1E"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def required_value(mapping: dict[str, Any], name: str) -> Any:
    value = mapping.get(name)
    if value in (None, ""):
        raise AssertionError(f"manifest is missing required field: {name}")
    return value


def read_manifest_context(path: Path) -> dict[str, Any]:
    result_bytes = path.read_bytes()
    manifest = json.loads(result_bytes.decode("utf-8"))
    assert manifest.get("acceptance_id") == EXPECTED_ACCEPTANCE_ID
    assert manifest.get("status") == "PASSED"
    assert manifest.get("stage") == "complete"

    attempt_path = path.parent / "attempts" / f"{EXPECTED_ACCEPTANCE_ID}.json"
    ledger_path = path.parent / "ai-call-ledger.json"
    attempt_bytes = attempt_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    assert result_bytes == attempt_bytes, "result and attempt manifest differ"
    ledger = json.loads(ledger_bytes.decode("utf-8"))
    assert ledger.get("total_attempted") == 5
    assert len(ledger.get("calls") or []) == 5

    ids = manifest.get("ids") or {}
    statuses = manifest.get("statuses") or {}
    counts = manifest.get("counts") or {}
    run_ids = required_value(ids, "run_ids")
    proposal_ids = required_value(ids, "healing_proposal_ids")
    healing_call_ids = required_value(ids, "healing_ai_call_ids")
    prompt_version_ids = required_value(ids, "prompt_version_ids")
    output_schema_ids = required_value(ids, "output_schema_ids")
    evidence_ids = required_value(ids, "evidence_ids")

    assert isinstance(healing_call_ids, list) and len(healing_call_ids) == 2
    context = {
        "project_id": required_value(ids, "project_id"),
        "web_case_id": required_value(ids, "web_case_id"),
        "source_web_case_version_id": required_value(ids, "source_web_case_version_id"),
        "healed_web_case_version_id": required_value(ids, "healed_web_case_version_id"),
        "source_element_version_id": required_value(ids, "source_element_version_id"),
        "healed_element_version_id": required_value(ids, "healed_element_version_id"),
        "failed_run_id": required_value(run_ids, "failed_old_locator"),
        "healed_run_id": required_value(run_ids, "healed"),
        "rejected_proposal_id": required_value(proposal_ids, "rejected"),
        "accepted_proposal_id": required_value(proposal_ids, "accepted"),
        "rejected_healing_ai_call_id": healing_call_ids[0],
        "accepted_healing_ai_call_id": healing_call_ids[1],
        "healing_prompt_version_id": required_value(prompt_version_ids, "locator_healing"),
        "failure_prompt_version_id": required_value(prompt_version_ids, "web_failure_analysis"),
        "healing_output_schema_id": required_value(output_schema_ids, "locator_healing"),
        "failure_output_schema_id": required_value(output_schema_ids, "web_failure_analysis"),
        "failure_analysis_id": required_value(ids, "failure_analysis_id"),
        "failure_analysis_ai_call_id": required_value(ids, "failure_analysis_ai_call_id"),
        "failed_evidence_ids": required_value(evidence_ids, "failed_old_locator"),
        "healed_evidence_ids": required_value(evidence_ids, "healed"),
        "manifest_status": manifest["status"],
        "manifest_stage": manifest["stage"],
        "attempt_path": attempt_path,
        "ledger_path": ledger_path,
        "before_hashes": {
            "result": sha256_bytes(result_bytes),
            "attempt": sha256_bytes(attempt_bytes),
            "ledger": sha256_bytes(ledger_bytes),
        },
    }

    assert context["source_web_case_version_id"] != context["healed_web_case_version_id"]
    assert context["source_element_version_id"] != context["healed_element_version_id"]
    assert context["failed_run_id"] != context["healed_run_id"]
    assert context["rejected_proposal_id"] != context["accepted_proposal_id"]
    assert len(context["failed_evidence_ids"]) == counts.get("failed_old_locator_evidence") == 4
    assert len(context["healed_evidence_ids"]) == counts.get("healed_evidence") == 5
    assert counts.get("healing_proposals") == 2
    assert counts.get("failure_analysis_history") == 1
    assert counts.get("ai_business_calls_cumulative") == 5
    expected_statuses = {
        "failed_old_locator_run": "FAILED",
        "rejected_healing_proposal": "REJECTED",
        "accepted_healing_proposal": "ACCEPTED",
        "healed_web_case": "APPROVED",
        "healed_run": "SUCCESS",
        "failure_analysis": "COMPLETED",
    }
    assert all(statuses.get(key) == value for key, value in expected_statuses.items())
    return context


def public_ids(context: dict[str, Any]) -> dict[str, Any]:
    names = {
        "project_id",
        "web_case_id",
        "source_web_case_version_id",
        "healed_web_case_version_id",
        "source_element_version_id",
        "healed_element_version_id",
        "failed_run_id",
        "healed_run_id",
        "rejected_proposal_id",
        "accepted_proposal_id",
        "rejected_healing_ai_call_id",
        "accepted_healing_ai_call_id",
        "healing_prompt_version_id",
        "failure_prompt_version_id",
        "healing_output_schema_id",
        "failure_output_schema_id",
        "failure_analysis_id",
        "failure_analysis_ai_call_id",
    }
    return {key: value for key, value in context.items() if key in names}


async def install_read_only_guard(
    page: Page,
    blocked: list[str],
    allowed_login_posts: list[str],
) -> None:
    async def guard(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        method = request.method.upper()
        is_login = method == "POST" and parsed.path.endswith("/api/v1/auth/login")
        if is_login:
            allowed_login_posts.append(parsed.path)
            await route.continue_()
            return
        if method in {"GET", "HEAD", "OPTIONS"}:
            await route.continue_()
            return
        blocked.append(f"{method} {parsed.path}")
        await route.abort("blockedbyclient")

    await page.route("**/api/v1/**", guard)


async def sign_in_if_needed(page: Page) -> None:
    login = page.get_by_role("button", name="登录平台")
    try:
        await expect(login).to_be_visible(timeout=5_000)
    except AssertionError:
        return
    # The development login form is prefilled by the application. Do not copy
    # credentials into source, screenshots, or audit output.
    await login.click()
    await page.wait_for_url(lambda url: "/runs" in url, timeout=15_000)


async def audit_grid_value(grid: Locator, label: str) -> str:
    item = grid.locator(":scope > div").filter(has_text=label)
    await expect(item).to_have_count(1)
    return (await item.locator("strong").inner_text()).strip()


async def open_run(page: Page, base_url: str, project_id: int, run_id: str) -> Locator:
    await page.goto(
        f"{base_url}/runs?project_id={project_id}&run_id={run_id}",
        wait_until="domcontentloaded",
    )
    await sign_in_if_needed(page)
    drawer = page.locator(".el-drawer")
    await expect(drawer).to_be_visible(timeout=20_000)
    await expect(drawer.locator(".detail-heading")).to_be_visible()
    return drawer


async def validate_proposal_audit(
    history: Locator,
    audit_grid: Locator,
    proposal_id: int,
    expected_status: str,
    ai_call_id: int,
    prompt_version_id: int,
    output_schema_id: int,
) -> dict[str, Any]:
    item = history.locator(".healing-history-item").filter(has_text=f"#{proposal_id}")
    await expect(item).to_have_count(1)
    await expect(item.locator("strong")).to_have_text(f"#{proposal_id}")
    await expect(item.locator(".el-tag")).to_have_text(expected_status)
    await item.click()
    await expect(audit_grid).to_be_visible()
    assert await audit_grid_value(audit_grid, "状态") == expected_status
    assert await audit_grid_value(audit_grid, "AI Call ID") == str(ai_call_id)
    assert await audit_grid_value(audit_grid, "Prompt Version ID") == str(prompt_version_id)
    assert f"#{output_schema_id}" in await audit_grid_value(audit_grid, "Output Schema")
    assert await audit_grid_value(audit_grid, "实际模型") == "qwen3.7-plus"
    assert await audit_grid_value(audit_grid, "Fallback / Repair") == "无 / 无"
    source_summary = await audit_grid_value(audit_grid, "来源快照")
    assert "…" in source_summary and re.search(r"\d+(?:\.\d+)?\s*(?:B|KB|MB)", source_summary)
    return {
        "proposal_id": proposal_id,
        "status": expected_status,
        "ai_call_id": ai_call_id,
        "prompt_version_id": prompt_version_id,
        "output_schema_id": output_schema_id,
        "actual_model": "qwen3.7-plus",
        "fallback": False,
        "repair": False,
        "source_summary_visible": True,
    }


async def validate_failed_run(
    page: Page,
    base_url: str,
    context: dict[str, Any],
    artifacts_dir: Path,
) -> dict[str, Any]:
    drawer = await open_run(page, base_url, context["project_id"], context["failed_run_id"])
    await expect(drawer.locator(".detail-heading-actions .el-tag")).to_have_text("失败")
    await expect(drawer.get_by_text("锁定版本", exact=True)).to_be_visible()
    await expect(drawer.get_by_text(f"#{context['source_web_case_version_id']}", exact=True)).to_be_visible()
    await expect(drawer.get_by_text("WEB_LOCATOR_NOT_FOUND", exact=False)).to_be_visible()

    evidence_rows = drawer.locator(".evidence-section .el-table__body-wrapper tbody tr")
    traces = drawer.locator(".web-traces-section .el-table__body-wrapper tbody tr")
    await expect(evidence_rows).to_have_count(len(context["failed_evidence_ids"]))
    await expect(traces).to_have_count(1)
    failed_evidence_count = await evidence_rows.count()
    await expect(traces.first.locator(".el-tag").first).to_have_text("失败")
    await expect(traces.first.get_by_text("action_1", exact=True)).to_be_visible()
    await drawer.locator(".detail-heading").screenshot(
        path=str(artifacts_dir / "failed-run-header.png")
    )
    await drawer.locator(".web-traces-section").screenshot(
        path=str(artifacts_dir / "failed-run-trace.png")
    )

    failure = drawer.locator(".failure-analysis-section")
    await expect(failure).to_be_visible()
    failure_items = failure.locator(".failure-analysis-history-item")
    await expect(failure_items).to_have_count(1)
    analysis_item = failure_items.filter(has_text=f"#{context['failure_analysis_id']}")
    await expect(analysis_item).to_have_count(1)
    await expect(analysis_item.locator("strong")).to_have_text(
        f"#{context['failure_analysis_id']}"
    )
    await expect(analysis_item.locator(".el-tag")).to_have_text("已完成")
    await analysis_item.click()
    failure_meta = failure.locator(".failure-analysis-meta-grid")
    failure_result = failure.locator(".failure-analysis-result-grid")
    await expect(failure_meta).to_be_visible()
    await expect(failure_result).to_be_visible()
    assert await audit_grid_value(failure_meta, "状态") == "已完成"
    assert await audit_grid_value(failure_meta, "AI Call ID") == str(
        context["failure_analysis_ai_call_id"]
    )
    assert await audit_grid_value(failure_meta, "实际模型") == "qwen3.7-plus"
    assert await audit_grid_value(failure_meta, "Prompt Version ID") == str(
        context["failure_prompt_version_id"]
    )
    assert await audit_grid_value(failure_meta, "Output Schema ID") == str(
        context["failure_output_schema_id"]
    )
    assert await audit_grid_value(failure_meta, "Fallback / Repair") == "否 / 否"
    assert "…" in await audit_grid_value(failure_meta, "来源摘要")
    assert await audit_grid_value(failure_result, "关联节点") == "action_1"
    assert await audit_grid_value(failure_result, "失败类别") == "Locator 未找到"
    await failure.locator(".failure-analysis-history").screenshot(
        path=str(artifacts_dir / "failure-analysis-history.png")
    )
    await failure_meta.screenshot(path=str(artifacts_dir / "failure-analysis-audit.png"))

    trace_action = drawer.locator(".web-traces-section").get_by_role(
        "button", name="生成 Healing 提案", exact=True
    )
    await expect(trace_action).to_have_count(1)
    await trace_action.click()
    healing = drawer.locator(".healing-section")
    await expect(healing).to_be_visible()
    await expect(healing.get_by_text("只创建 DRAFT，不自动批准或重跑", exact=False)).to_be_visible()
    history = healing.locator(".healing-history")
    await expect(history.locator(".healing-history-item")).to_have_count(2)
    audit_grid = healing.locator(".healing-audit-grid")

    rejected = await validate_proposal_audit(
        history,
        audit_grid,
        context["rejected_proposal_id"],
        "已拒绝",
        context["rejected_healing_ai_call_id"],
        context["healing_prompt_version_id"],
        context["healing_output_schema_id"],
    )
    await expect(healing.locator(".healing-version-actions")).to_have_count(0)
    accepted = await validate_proposal_audit(
        history,
        audit_grid,
        context["accepted_proposal_id"],
        "已接受",
        context["accepted_healing_ai_call_id"],
        context["healing_prompt_version_id"],
        context["healing_output_schema_id"],
    )
    version_actions = healing.locator(".healing-version-actions")
    await expect(version_actions).to_contain_text(
        f"Web Case Version #{context['healed_web_case_version_id']}"
    )
    await expect(version_actions).to_contain_text(
        f"Element Version #{context['healed_element_version_id']}"
    )
    await expect(healing.locator(".healing-decision-actions")).to_have_count(0)
    await history.screenshot(path=str(artifacts_dir / "healing-proposal-history.png"))
    await audit_grid.screenshot(path=str(artifacts_dir / "accepted-proposal-audit.png"))

    load_button = version_actions.get_by_role(
        "button", name="检查并载入新版本到运行表单", exact=True
    )
    await expect(load_button).to_be_visible()
    await load_button.click()
    await expect(drawer).to_be_hidden(timeout=15_000)
    selected_version = page.locator(".resource-card").filter(
        has_text="测试 Web Case"
    ).locator(".scenario-version-select")
    await expect(selected_version).to_contain_text(
        re.compile(rf"#\s*{context['healed_web_case_version_id']}\b")
    )

    return {
        "run_id": context["failed_run_id"],
        "status": "FAILED",
        "locked_web_case_version_id": context["source_web_case_version_id"],
        "error_type": "WEB_LOCATOR_NOT_FOUND",
        "failed_node_id": "action_1",
        "trace_rows": 1,
        "evidence_rows": failed_evidence_count,
        "failure_analysis": {
            "id": context["failure_analysis_id"],
            "status": "COMPLETED",
            "ai_call_id": context["failure_analysis_ai_call_id"],
            "actual_model": "qwen3.7-plus",
            "prompt_version_id": context["failure_prompt_version_id"],
            "output_schema_id": context["failure_output_schema_id"],
            "fallback": False,
            "repair": False,
            "node_ids": ["action_1"],
        },
        "healing_proposals": [rejected, accepted],
        "healed_version_loaded_into_form": context["healed_web_case_version_id"],
        "decision_buttons_visible": False,
    }


async def version_row(page: Page, version_number: int) -> Locator:
    rows = page.locator(".version-history .el-table__body-wrapper tbody tr")
    count = await rows.count()
    for index in range(count):
        row = rows.nth(index)
        first_cell = (await row.locator("td").nth(0).inner_text()).strip()
        if first_cell == f"V{version_number}":
            return row
    raise AssertionError(f"version row V{version_number} not found")


async def validate_web_case_version(
    page: Page,
    base_url: str,
    context: dict[str, Any],
    version_id: int,
    version_number: int,
    screenshot_name: str,
    artifacts_dir: Path,
) -> dict[str, Any]:
    url = (
        f"{base_url}/web-assets?project_id={context['project_id']}"
        f"&web_case_id={context['web_case_id']}&version_id={version_id}"
        f"&run_id={context['failed_run_id']}"
    )
    await page.goto(url, wait_until="domcontentloaded")
    notice = page.locator(".web-assets-page > .el-alert").filter(has_text="已定位到 Web Case")
    await expect(notice).to_be_visible(timeout=20_000)
    await expect(notice).to_contain_text(f"V{version_number}")
    await expect(notice).to_contain_text("页面不会自动批准")
    await expect(notice).to_contain_text(context["failed_run_id"])
    await expect(page.get_by_role("button", name="返回原 Run", exact=True)).to_be_visible()
    row = await version_row(page, version_number)
    await expect(row.locator("td").nth(1)).to_have_text("已批准")
    assert f"version_id={version_id}" in page.url
    assert not await page.get_by_role("button", name="批准执行", exact=False).is_visible()
    await notice.screenshot(path=str(artifacts_dir / screenshot_name))
    return {
        "version_id": version_id,
        "version_number": version_number,
        "status": "APPROVED",
        "deep_link_located": True,
        "manual_approval_notice": True,
        "approve_button_visible": False,
    }


async def validate_healed_run(
    page: Page,
    base_url: str,
    context: dict[str, Any],
    artifacts_dir: Path,
) -> dict[str, Any]:
    drawer = await open_run(page, base_url, context["project_id"], context["healed_run_id"])
    await expect(drawer.locator(".detail-heading-actions .el-tag")).to_have_text("成功")
    await expect(drawer.get_by_text("锁定版本", exact=True)).to_be_visible()
    await expect(drawer.get_by_text(f"#{context['healed_web_case_version_id']}", exact=True)).to_be_visible()
    evidence_rows = drawer.locator(".evidence-section .el-table__body-wrapper tbody tr")
    traces = drawer.locator(".web-traces-section .el-table__body-wrapper tbody tr")
    await expect(evidence_rows).to_have_count(len(context["healed_evidence_ids"]))
    await expect(traces).to_have_count(1)
    await expect(traces.first.get_by_text("action_1", exact=True)).to_be_visible()
    await expect(traces.first.locator(".el-tag").first).to_have_text("成功")
    await expect(drawer.locator(".failure-analysis-section")).to_have_count(0)
    await expect(drawer.get_by_role("button", name="投递", exact=True)).to_have_count(0)
    await expect(drawer.get_by_role("button", name="取消", exact=True)).to_have_count(0)
    await expect(drawer.get_by_role("button", name="强制停止", exact=True)).to_have_count(0)
    await drawer.locator(".detail-heading").screenshot(
        path=str(artifacts_dir / "healed-run-header.png")
    )
    await drawer.locator(".evidence-section").screenshot(
        path=str(artifacts_dir / "healed-run-evidence.png")
    )
    await drawer.locator(".web-traces-section").screenshot(
        path=str(artifacts_dir / "healed-run-trace.png")
    )
    return {
        "run_id": context["healed_run_id"],
        "status": "SUCCESS",
        "locked_web_case_version_id": context["healed_web_case_version_id"],
        "node_id": "action_1",
        "trace_rows": 1,
        "evidence_rows": await evidence_rows.count(),
        "mutating_run_actions_visible": False,
    }


def current_file_hashes(manifest_path: Path, context: dict[str, Any]) -> dict[str, str]:
    return {
        "result": sha256_bytes(manifest_path.read_bytes()),
        "attempt": sha256_bytes(context["attempt_path"].read_bytes()),
        "ledger": sha256_bytes(context["ledger_path"].read_bytes()),
    }


async def run(manifest_path: Path, base_url: str, artifacts_dir: Path) -> dict[str, Any]:
    context = read_manifest_context(manifest_path)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    blocked: list[str] = []
    allowed_login_posts: list[str] = []
    script_errors: list[str] = []
    console_errors: list[str] = []
    response_errors: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        browser_context = await browser.new_context(viewport={"width": 1440, "height": 1100})
        page = await browser_context.new_page()
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on(
            "response",
            lambda response: response_errors.append(
                f"{response.status} {urlparse(response.url).path}"
            )
            if response.status >= 400 and not urlparse(response.url).path.endswith("/events/stream")
            else None,
        )
        await install_read_only_guard(page, blocked, allowed_login_posts)
        failed_run = await validate_failed_run(page, base_url, context, artifacts_dir)
        source_version = await validate_web_case_version(
            page,
            base_url,
            context,
            context["source_web_case_version_id"],
            1,
            "source-version-deep-link.png",
            artifacts_dir,
        )
        healed_version = await validate_web_case_version(
            page,
            base_url,
            context,
            context["healed_web_case_version_id"],
            2,
            "healed-version-deep-link.png",
            artifacts_dir,
        )
        healed_run = await validate_healed_run(page, base_url, context, artifacts_dir)
        await browser_context.close()
        await browser.close()

    after_hashes = current_file_hashes(manifest_path, context)
    assert context["before_hashes"] == after_hashes, "manifest or AI ledger changed during audit"
    assert after_hashes["result"] == after_hashes["attempt"]
    assert not blocked, f"read-only audit attempted business mutations: {blocked}"
    assert not script_errors, f"browser script errors: {script_errors}"
    assert not console_errors, f"browser console errors: {console_errors}"
    assert not response_errors, f"browser HTTP errors: {response_errors}"

    return {
        "acceptance_id": EXPECTED_ACCEPTANCE_ID,
        "manifest_status": context["manifest_status"],
        "manifest_stage": context["manifest_stage"],
        "ids": public_ids(context),
        "failed_run": failed_run,
        "source_version": source_version,
        "healed_version": healed_version,
        "healed_run": healed_run,
        "business_mutations_attempted": len(blocked),
        "normal_login_posts": len(allowed_login_posts),
        "script_errors": len(script_errors),
        "console_errors": len(console_errors),
        "http_errors": len(response_errors),
        "manifest_and_ledger_hashes_before": context["before_hashes"],
        "manifest_and_ledger_hashes_after": after_hashes,
        "result_and_attempt_bytes_equal": True,
        "screenshots": sorted(path.name for path in artifacts_dir.glob("*.png")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    args = parser.parse_args()
    artifacts_dir = args.artifacts_dir.resolve()
    result = asyncio.run(
        run(args.manifest.resolve(), args.base_url.rstrip("/"), artifacts_dir)
    )
    audit_path = artifacts_dir / "ui-audit.json"
    audit_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
