"""Synthetic real-Chrome acceptance for V1-W6 expanded WebCase assertions.

The production SPA is served on a random loopback port. Every API response is
synthetic; no formal service, AI provider, Runner, database, asset, or Run is used.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Page, Route, async_playwright, expect

from v1_w3_web_actions_playwright import (
    ASSERTIONS as LEGACY_ASSERTIONS,
    LEGACY_ACTIONS,
    SpaHandler,
    State,
    case_item,
    case_version,
    choose,
    close_recording,
    direct_locator,
    field_input,
    fill_number,
    fulfill,
    handle_api,
    new_action_payloads,
    open_recording,
    suggestion,
)


NEW_ASSERTIONS = [
    "ASSERT_EXISTS",
    "ASSERT_ENABLED",
    "ASSERT_TEXT_EQUAL",
    "ASSERT_INPUT_VALUE",
    "ASSERT_TITLE",
]
ALL_ASSERTIONS = [
    "ASSERT_VISIBLE",
    "ASSERT_EXISTS",
    "ASSERT_ENABLED",
    "ASSERT_TEXT",
    "ASSERT_TEXT_EQUAL",
    "ASSERT_INPUT_VALUE",
    "ASSERT_URL",
    "ASSERT_TITLE",
]
ASSERTION_LABELS = {
    "ASSERT_VISIBLE": "ASSERT_VISIBLE · 元素可见",
    "ASSERT_EXISTS": "ASSERT_EXISTS · 元素存在（含隐藏）",
    "ASSERT_ENABLED": "ASSERT_ENABLED · 元素启用",
    "ASSERT_TEXT": "ASSERT_TEXT · 文本包含",
    "ASSERT_TEXT_EQUAL": "ASSERT_TEXT_EQUAL · 文本完整相等",
    "ASSERT_INPUT_VALUE": "ASSERT_INPUT_VALUE · 输入值完整相等",
    "ASSERT_URL": "ASSERT_URL · URL 匹配",
    "ASSERT_TITLE": "ASSERT_TITLE · 页面标题完整相等",
}


def assertion_payloads() -> list[dict[str, Any]]:
    return [
        {"type": "ASSERT_TITLE", "expected": "", "timeout_ms": 100},
        {"type": "ASSERT_EXISTS", "locator": direct_locator("#exists"), "timeout_ms": 600000},
        {
            "type": "ASSERT_ENABLED",
            "locator": {"strategy": None, "value": None, "element_version_id": 501},
            "timeout_ms": 1002,
        },
        {"type": "ASSERT_TEXT_EQUAL", "locator": direct_locator("#text-equal-empty"), "expected": "", "timeout_ms": 1003},
        {"type": "ASSERT_INPUT_VALUE", "locator": direct_locator("#input-empty"), "expected": "", "timeout_ms": 1004},
        {"type": "ASSERT_TEXT", "locator": direct_locator("#contains"), "expected": "Needle", "timeout_ms": 1005},
        {"type": "ASSERT_TEXT_EQUAL", "locator": direct_locator("#text-case"), "expected": "  MiXeD  ", "timeout_ms": 1006},
        {"type": "ASSERT_TITLE", "expected": "  TiTle  ", "timeout_ms": 1007},
    ]


def web_content(
    actions: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    session_profile_id: int | None = 21,
) -> dict[str, Any]:
    return {
        "start_url": "https://synthetic.invalid/start",
        "natural_language_steps": ["打开合成页面"],
        "actions": copy.deepcopy(actions),
        "assertions": copy.deepcopy(assertions),
        "session_profile_id": session_profile_id,
        "browser": "CHROME",
        "headless": True,
        "total_timeout_ms": 900000,
        "parameters": {"suite": "v1-w6"},
    }


def prepare_state() -> State:
    state = State()
    legacy = web_content(LEGACY_ACTIONS, LEGACY_ASSERTIONS)
    state.cases = [
        case_item(1, "Legacy assertions", current_version_id=101),
        case_item(3, "Archived assertions", "ARCHIVED", 301),
    ]
    state.versions = {
        1: [case_version(1, 101, 1, legacy)],
        3: [case_version(3, 301, 1, legacy, "APPROVED")],
    }
    candidate = web_content(new_action_payloads(), assertion_payloads(), session_profile_id=999)
    for index, recording_id in enumerate(state.recordings, start=11):
        state.suggestions[recording_id] = suggestion(index, recording_id, candidate)
    return state


async def handle_w6_api(state: State, route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    if request.method == "GET" and parsed.path == "/api/v1/projects/1/members":
        state.requests.append(
            {
                "method": request.method,
                "path": parsed.path,
                "query": {},
                "authorization": request.headers.get("authorization"),
            }
        )
        return await fulfill(
            route,
            {
                "items": [
                    {
                        "project_id": 1,
                        "user_id": "w6-viewer",
                        "role": "VIEWER",
                        "created_at": "2026-09-10T09:00:00Z",
                    }
                ]
            },
        )
    await handle_api(state, route)


async def choose_assertion(page: Page, prefix: str, index: int, assertion_type: str) -> None:
    await choose(page, f"{prefix}-assertion-type-{index}", ASSERTION_LABELS[assertion_type])


async def add_manual_assertion(page: Page) -> int:
    rows = page.locator("[data-testid^='manual-assertion-row-']")
    index = await rows.count()
    await page.get_by_role("button", name="添加 Assertion", exact=True).click()
    await expect(rows).to_have_count(index + 1)
    return index


async def construct_manual_assertions(page: Page) -> None:
    # Element -> text -> title proves forbidden locator/expected fields are rebuilt.
    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TEXT")
    await (await field_input(page, f"manual-assertion-expected-{index}")).fill("remove-me")
    await (await field_input(page, f"manual-assertion-locator-value-{index}")).fill("#remove-me")
    await choose_assertion(page, "manual", index, "ASSERT_TITLE")
    await expect(await field_input(page, f"manual-assertion-expected-{index}")).to_have_value("")
    await expect(page.get_by_test_id(f"manual-assertion-locator-value-{index}")).to_have_count(0)

    # Page -> element proves TITLE expected is removed and a fresh locator is required.
    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TITLE")
    await (await field_input(page, f"manual-assertion-expected-{index}")).fill("remove-title")
    await choose_assertion(page, "manual", index, "ASSERT_EXISTS")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_ENABLED")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TEXT_EQUAL")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_INPUT_VALUE")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TEXT")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TEXT_EQUAL")

    index = await add_manual_assertion(page)
    await choose_assertion(page, "manual", index, "ASSERT_TITLE")


async def validate(base_url: str, evidence: Path) -> dict[str, Any]:
    state = prepare_state()
    page_errors: list[str] = []
    console_errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        admin_context = await browser.new_context(viewport={"width": 1800, "height": 1200}, device_scale_factor=1)
        await admin_context.add_init_script(
            """
            localStorage.setItem('access_token', 'w6-admin-token');
            localStorage.setItem('current_user', JSON.stringify({id:'w6-admin',username:'w6-admin',display_name:'W6 Admin',roles:['ADMIN']}));
            """
        )
        page = await admin_context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        await page.route("**/api/v1/**", lambda route: handle_w6_api(state, route))

        await page.goto(f"{base_url}/web-assets?project_id=1")
        await expect(page.get_by_role("heading", name="Legacy assertions")).to_be_visible()

        # The original seven actions and three assertions remain byte-for-byte compatible.
        await (await field_input(page, "web-case-change-note")).fill("legacy assertion preserve")
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("Web Case 新版本已保存", exact=False)).to_be_visible()
        assert state.version_creates[-1]["body"]["content"] == web_content(LEGACY_ACTIONS, LEGACY_ASSERTIONS)

        # Construct all five new assertions plus the old contains assertion from the UI.
        await page.get_by_test_id("new-web-case").click()
        await (await field_input(page, "web-case-name")).fill("Expanded manual assertions")
        await (await field_input(page, "web-case-start-url")).fill("https://synthetic.invalid/start")
        await (await field_input(page, "manual-action-url-0")).fill("https://synthetic.invalid/start")
        await construct_manual_assertions(page)
        await expect(page.locator("[data-testid^='manual-assertion-row-']")).to_have_count(8)

        # Empty locator blocks before request; empty expected on equal assertions does not.
        before_missing_locator = len(state.case_creates)
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("请为每个目标配置完整的 Locator", exact=False)).to_be_visible()
        assert len(state.case_creates) == before_missing_locator

        await (await field_input(page, "manual-assertion-locator-value-1")).fill("#exists")
        await choose(page, "manual-assertion-locator-mode-2", "引用 Element")
        await choose(page, "manual-assertion-element-version-2", "提交按钮 · V#501")
        await (await field_input(page, "manual-assertion-locator-value-3")).fill("#text-equal-empty")
        await (await field_input(page, "manual-assertion-locator-value-4")).fill("#input-empty")
        await (await field_input(page, "manual-assertion-locator-value-5")).fill("#contains")
        await (await field_input(page, "manual-assertion-expected-5")).fill("Needle")
        await (await field_input(page, "manual-assertion-locator-value-6")).fill("#text-case")
        await (await field_input(page, "manual-assertion-expected-6")).fill("  MiXeD  ")
        await (await field_input(page, "manual-assertion-expected-7")).fill("  TiTle  ")

        # Assert all eight option labels and their deliberately unambiguous Chinese semantics.
        select = page.get_by_test_id("manual-assertion-type-0")
        await select.click()
        controlled_id = await select.evaluate(
            "node => node.getAttribute('aria-controls') || node.querySelector('[aria-controls]')?.getAttribute('aria-controls')"
        )
        option_texts = [text.strip() for text in await page.locator(f"#{controlled_id}").get_by_role("option").all_text_contents()]
        assert option_texts == [ASSERTION_LABELS[item] for item in ALL_ASSERTIONS]
        await page.keyboard.press("Escape")
        await expect(page.get_by_text("文本包含", exact=False).first).to_be_visible()
        await expect(page.get_by_text("文本完整相等", exact=False).first).to_be_visible()
        await expect(page.get_by_text("期望空字符串", exact=False).first).to_be_visible()

        # InputNumber constrains values outside both frozen boundaries without a request.
        await fill_number(page, "manual-assertion-timeout-0", 99)
        await expect(await field_input(page, "manual-assertion-timeout-0")).to_have_value("100")
        assert len(state.case_creates) == before_missing_locator
        await fill_number(page, "manual-assertion-timeout-1", 600001)
        await expect(await field_input(page, "manual-assertion-timeout-1")).to_have_value("600000")
        assert len(state.case_creates) == before_missing_locator
        for index in range(2, 8):
            await fill_number(page, f"manual-assertion-timeout-{index}", 1000 + index)

        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("Web Case V1 已创建", exact=False)).to_be_visible()
        expected = assertion_payloads()
        saved = state.case_creates[-1]["content"]
        assert saved["assertions"] == expected
        assert saved["natural_language_steps"] == []
        for assertion in saved["assertions"]:
            keys = {"type", "timeout_ms"}
            if assertion["type"] in {"ASSERT_EXISTS", "ASSERT_ENABLED", "ASSERT_TEXT_EQUAL", "ASSERT_INPUT_VALUE", "ASSERT_TEXT"}:
                keys.add("locator")
            if assertion["type"] in {"ASSERT_TEXT_EQUAL", "ASSERT_INPUT_VALUE", "ASSERT_TEXT", "ASSERT_TITLE"}:
                keys.add("expected")
            assert set(assertion) == keys, assertion
        assert saved["assertions"][0]["expected"] == ""
        assert saved["assertions"][3]["expected"] == ""
        assert saved["assertions"][4]["expected"] == ""
        assert saved["assertions"][6]["expected"] == "  MiXeD  "
        assert saved["assertions"][7]["expected"] == "  TiTle  "
        await page.wait_for_timeout(3200)
        await page.screenshot(path=evidence / "01-manual-assertions.png", full_page=True)

        # Saving creates V2 and leaves V1 immutable/readable.
        await (await field_input(page, "manual-assertion-expected-7")).fill("V2 Title")
        await (await field_input(page, "web-case-change-note")).fill("assertion v2")
        await page.get_by_test_id("save-web-case").click()
        await expect(page.get_by_text("Web Case 新版本已保存", exact=False)).to_be_visible()
        assert state.version_creates[-1]["body"]["content"]["assertions"][7]["expected"] == "V2 Title"
        assert state.versions[2][-1]["content"]["assertions"][7]["expected"] == "  TiTle  "
        v1_row = page.locator(".version-history .el-table__row").filter(has_text="V1")
        await v1_row.get_by_role("button", name="查看").click()
        await expect(await field_input(page, "manual-assertion-expected-7")).to_have_value("  TiTle  ")

        # Archived WebCase is read-only and no approval is implicit.
        assert not state.approvals
        await page.locator("button.asset-list-item").filter(has_text="Archived assertions").click()
        await expect(page.get_by_test_id("save-web-case")).to_be_disabled()

        await page.get_by_role("tab", name="录制工作台").click()

        # Unedited acceptance continues to omit human content.
        await open_recording(page, "rec-unedited-0001")
        await page.get_by_test_id("open-ai-accept").click()
        await page.get_by_test_id("confirm-ai-accept").click()
        await expect(page.get_by_text("AI 建议已接受", exact=False)).to_be_visible()
        unedited = next(item for item in state.ai_confirms if item["recording_id"] == "rec-unedited-0001")
        assert "content" not in unedited["body"]
        await close_recording(page)

        # Edited acceptance submits the complete approved asset DSL and exact Session.
        await open_recording(page, "rec-edited-0002")
        await expect(page.locator("[data-testid^='ai-assertion-row-']")).to_have_count(8)
        await fill_number(page, "ai-assertion-timeout-1", 599999)
        await expect(page.get_by_text("已编辑：接受时会携带 content")).to_be_visible()
        await page.get_by_test_id("ai-assertion-row-7").scroll_into_view_if_needed()
        await page.locator(".el-drawer").screenshot(path=evidence / "02-ai-assertions-editing.png")
        await page.get_by_test_id("open-ai-accept").click()
        await page.get_by_test_id("confirm-ai-accept").click()
        await expect(page.get_by_text("AI 建议已接受", exact=False)).to_be_visible()
        edited = next(item for item in state.ai_confirms if item["recording_id"] == "rec-edited-0002")
        expected_ai = assertion_payloads()
        expected_ai[1]["timeout_ms"] = 599999
        assert edited["body"]["content"] == web_content(new_action_payloads(), expected_ai, session_profile_id=22)
        await close_recording(page)

        # An invalid old contains assertion blocks accept but never blocks reject.
        before_invalid_confirm = len(state.ai_confirms)
        await open_recording(page, "rec-reject-0003")
        await (await field_input(page, "ai-assertion-expected-5")).fill("")
        await page.get_by_test_id("open-ai-accept").click()
        await page.get_by_test_id("confirm-ai-accept").click()
        await expect(page.get_by_text("ASSERT_TEXT 的期望值", exact=False)).to_be_visible()
        assert len(state.ai_confirms) == before_invalid_confirm
        await page.locator(".el-dialog__headerbtn:visible").last.click()
        await page.get_by_test_id("open-ai-reject").click()
        await page.get_by_test_id("confirm-ai-reject").click()
        await page.locator(".el-message-box__btns .el-button--primary").click()
        await expect(page.get_by_text("AI 建议已拒绝", exact=False)).to_be_visible()
        assert len(state.ai_rejects) == 1
        await close_recording(page)

        # A project VIEWER can read history and AI drafts but cannot dispatch writes.
        writes_before_viewer = len(state.case_creates) + len(state.version_creates) + len(state.approvals) + len(state.ai_confirms) + len(state.ai_rejects)
        viewer_context = await browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        await viewer_context.add_init_script(
            """
            localStorage.setItem('access_token', 'w6-viewer-token');
            localStorage.setItem('current_user', JSON.stringify({id:'w6-viewer',username:'w6-viewer',display_name:'W6 Viewer',roles:[]}));
            """
        )
        viewer = await viewer_context.new_page()
        viewer.on("pageerror", lambda error: page_errors.append(str(error)))
        viewer.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        await viewer.route("**/api/v1/**", lambda route: handle_w6_api(state, route))
        await viewer.goto(f"{base_url}/web-assets?project_id=1")
        await expect(viewer.get_by_text("当前角色为只读", exact=False).first).to_be_visible()
        await expect(viewer.get_by_test_id("new-web-case")).to_be_disabled()
        await expect(viewer.get_by_test_id("save-web-case")).to_be_disabled()
        await viewer.get_by_role("tab", name="录制工作台").click()
        await open_recording(viewer, "rec-stale-0004")
        await expect(viewer.get_by_test_id("open-ai-accept")).to_be_disabled()
        await expect(viewer.get_by_test_id("open-ai-reject")).to_be_disabled()
        await viewer.screenshot(path=evidence / "03-viewer-readonly.png", full_page=True)
        writes_after_viewer = len(state.case_creates) + len(state.version_creates) + len(state.approvals) + len(state.ai_confirms) + len(state.ai_rejects)
        assert writes_after_viewer == writes_before_viewer
        await viewer_context.close()

        assert not state.unknown, state.unknown
        assert not page_errors, page_errors
        assert not console_errors, console_errors
        await admin_context.close()
        await browser.close()

    return {
        "status": "passed",
        "mode": "synthetic-real-chrome",
        "formal_services_used": False,
        "new_assertions": NEW_ASSERTIONS,
        "manual_case_create_count": len(state.case_creates),
        "version_create_count": len(state.version_creates),
        "approval_count": len(state.approvals),
        "ai_confirm_count": len(state.ai_confirms),
        "ai_reject_count": len(state.ai_rejects),
        "viewer_member_reads": sum(1 for item in state.requests if item["path"] == "/api/v1/projects/1/members"),
        "request_count": len(state.requests),
        "page_errors": len(page_errors),
        "unexpected_console_errors": len(console_errors),
        "checks": [
            "five new assertions construct save reload with exact WebCase asset payloads",
            "old ASSERT_TEXT remains contains semantics and legacy assertions remain exact",
            "empty expected is preserved for text equal input value and title",
            "whitespace and case in equality expectations are not trimmed or normalized",
            "element and page assertion switches remove forbidden fields",
            "missing locator blocks before request and timeout controls constrain values to 100 through 600000",
            "ElementVersion reference remains exact and V1 remains immutable after V2",
            "archived WebCase and project VIEWER cannot write",
            "unedited AI accept omits content while edited accept sends full content and Session",
            "invalid AI draft blocks accept without blocking reject",
            "W3 actions remain exact in edited AI content",
        ],
        "screenshots": [
            "01-manual-assertions.png",
            "02-ai-assertions-editing.png",
            "03-viewer-readonly.png",
        ],
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
