from playwright.sync_api import expect, sync_playwright

from demo.server import DEMO_PASSWORD, DEMO_USERNAME, running_demo


def test_chrome_login_cleanup_locator_change_and_expired_session() -> None:
    with running_demo() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()
            page.goto(url + "/login")
            page.get_by_label("用户名").fill(DEMO_USERNAME)
            page.get_by_label("密码").fill(DEMO_PASSWORD)
            page.get_by_role("button", name="登录", exact=True).click()
            expect(page.get_by_test_id("welcome")).to_have_text("登录成功")
            page.get_by_label("资源名称").fill("qat_demo_browser")
            page.get_by_role("button", name="创建资源", exact=True).click()
            expect(page.get_by_test_id("resources").locator("li")).to_have_count(1)
            page.get_by_role("button", name="删除资源 1", exact=True).click()
            expect(page.get_by_test_id("resources").locator("li")).to_have_count(0)
            page.get_by_label("商品查询").fill("键盘")
            page.get_by_role("button", name="查询商品", exact=True).click()
            expect(page.get_by_test_id("products").locator("li")).to_have_count(1)
            page.get_by_label("订单商品 ID").fill("1")
            page.get_by_label("订单数量").fill("2")
            page.get_by_role("button", name="创建订单", exact=True).click()
            expect(page.get_by_test_id("orders").locator("li")).to_have_count(1)
            page.get_by_role("button", name="删除订单 1", exact=True).click()
            expect(page.get_by_test_id("orders").locator("li")).to_have_count(0)
            assert page.request.post(url + "/control/expire-sessions", data={}).ok
            page.goto(url + "/app")
            expect(page).to_have_url(url + "/login")
            assert page.request.post(
                url + "/control/locator", data={"changed": True}
            ).ok
            page.reload()
            expect(page.locator("#login-btn")).to_have_count(0)
            expect(page.locator("#signin-confirm")).to_have_count(1)
            expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
        finally:
            browser.close()
