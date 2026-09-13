import json
from http.client import HTTPMessage
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo.server import DEMO_PASSWORD, DEMO_USERNAME, running_demo


def call(
    url: str,
    path: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any, HTTPMessage]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url + path,
        method=method,
        headers=headers,
        data=None if data is None else json.dumps(data).encode(),
    )
    try:
        response = urlopen(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        content = response.read().decode()
        payload = (
            json.loads(content)
            if "application/json" in response.headers["Content-Type"]
            else content
        )
        return response.status, payload, response.headers


def test_login_resource_cleanup_and_revocation() -> None:
    with running_demo() as url:
        assert call(url, "/api/resources")[0] == 401
        assert (
            call(
                url,
                "/api/login",
                "POST",
                {"username": DEMO_USERNAME, "password": "wrong"},
            )[0]
            == 401
        )
        status, payload, headers = call(
            url,
            "/api/login",
            "POST",
            {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        )
        assert status == 200
        assert "HttpOnly" in headers["Set-Cookie"]
        token = payload["data"]["token"]
        status, payload, _ = call(
            url, "/api/resources", "POST", {"name": "qat_acceptance"}, token
        )
        assert status == 201
        resource_id = payload["data"]["id"]
        assert call(url, "/api/resources", token=token)[1]["items"] == [payload["data"]]
        assert call(url, f"/api/resources/{resource_id}", "DELETE", token=token)[1][
            "deleted"
        ]
        assert call(url, "/control/state")[1]["resource_count"] == 0
        assert call(url, "/control/expire-sessions", "POST", {})[0] == 200
        assert call(url, "/api/resources", token=token)[0] == 401


def test_locator_mutation_and_bounded_failure_controls() -> None:
    with running_demo() as url:
        assert 'id="login-btn"' in call(url, "/login")[1]
        assert call(url, "/control/locator", "POST", {"changed": "false"})[0] == 400
        assert call(url, "/control/locator", "POST", {"changed": True})[0] == 200
        html = call(url, "/login")[1]
        assert 'id="login-btn"' not in html
        assert 'id="signin-confirm"' in html and 'aria-label="登录"' in html
        assert call(url, "/api/flaky?key=retry")[0] == 503
        assert call(url, "/api/flaky?key=retry")[0] == 200
        assert call(url, "/api/slow?delay_ms=30001")[0] == 400
        assert call(url, "/api/slow?delay_ms=0")[1] == {"completed": True}


def test_user_product_query_order_and_exact_cleanup() -> None:
    with running_demo() as url:
        token = call(
            url,
            "/api/login",
            "POST",
            {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        )[1]["data"]["token"]

        users = call(url, "/api/users?q=buyer", token=token)[1]["items"]
        assert users == [{"id": 2, "username": "buyer", "display_name": "采购员"}]
        products = call(url, "/api/products?q=%E9%94%AE%E7%9B%98", token=token)[1][
            "items"
        ]
        assert len(products) == 1 and products[0]["stock"] == 20

        status, payload, _ = call(
            url,
            "/api/orders",
            "POST",
            {"product_id": products[0]["id"], "quantity": 2},
            token,
        )
        assert status == 201
        order = payload["data"]
        assert order["total_cents"] == 39800
        assert call(url, "/api/orders?q=%E9%94%AE%E7%9B%98", token=token)[1][
            "items"
        ] == [order]
        assert call(
            url, "/api/products?q=%E9%94%AE%E7%9B%98", token=token
        )[1]["items"][0]["stock"] == 18
        assert call(url, f"/api/orders/{order['id']}", "DELETE", token=token)[1] == {
            "deleted": True
        }
        assert call(
            url, "/api/products?q=%E9%94%AE%E7%9B%98", token=token
        )[1]["items"][0]["stock"] == 20
        assert call(url, "/control/state")[1]["order_count"] == 0


def test_order_validation_and_auth_boundaries() -> None:
    with running_demo() as url:
        assert call(url, "/api/users")[0] == 401
        assert call(url, "/api/products")[0] == 401
        assert call(url, "/api/orders")[0] == 401
        token = call(
            url,
            "/api/login",
            "POST",
            {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        )[1]["data"]["token"]
        assert call(
            url, "/api/orders", "POST", {"product_id": 1, "quantity": 0}, token
        )[0] == 400
        assert call(
            url, "/api/orders", "POST", {"product_id": 999, "quantity": 1}, token
        )[0] == 404


def test_instances_do_not_share_sessions_or_resources() -> None:
    with running_demo() as first, running_demo() as second:
        token = call(
            first,
            "/api/login",
            "POST",
            {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        )[1]["data"]["token"]
        assert call(second, "/api/resources", token=token)[0] == 401
        assert call(second, "/control/state")[1]["login_count"] == 0
