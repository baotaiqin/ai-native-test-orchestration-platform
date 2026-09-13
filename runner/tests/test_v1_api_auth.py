from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from runner.errors import ExecutionError, ProtocolError, TargetTimeoutError
from runner.executors.api import (
    ApiExecutor,
    ApiRequestTemplate,
    RequestAuth,
    RequestBody,
    RequestValue,
)


def _mapping(auth: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "method": "GET",
        "url": "https://target.example.test/api",
        "query_params": [],
        "headers": [],
        "cookies": [],
        "body": {"type": "NONE", "content": None, "content_type": None},
        "auth": auth,
        "timeout_ms": 1_500,
        "follow_redirects": True,
    }
    value.update(overrides)
    return value


def _execute_with_transport(
    request: ApiRequestTemplate | dict[str, Any],
    handler: Callable[[httpx.Request], httpx.Response],
    **client_kwargs: Any,
):
    with httpx.Client(transport=httpx.MockTransport(handler), **client_kwargs) as client:
        return ApiExecutor(client).execute(request)


def test_none_auth_preserves_explicit_request_parts_and_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.params.multi_items() == [("base", "0"), ("q", "A b")]
        assert request.headers["X-Case"] == "Exact"
        assert request.headers["Cookie"] == "sid=cookie-value"
        assert request.headers["Content-Type"] == "text/plain"
        assert request.content == b"request-body"
        return httpx.Response(201, json={"ok": True}, headers={"X-Result": "done"})

    result = _execute_with_transport(
        _mapping(
            {
                "type": "NONE",
                "token": None,
                "username": None,
                "password": None,
                "key_name": None,
                "key_value": None,
                "placement": "HEADER",
            },
            method="POST",
            url="https://target.example.test/api?base=0",
            query_params=[{"name": "q", "value": "A b", "enabled": True}],
            headers=[{"name": "X-Case", "value": "Exact", "enabled": True}],
            cookies=[{"name": "sid", "value": "cookie-value", "enabled": True}],
            body={"type": "RAW", "content": "request-body", "content_type": "text/plain"},
            follow_redirects=False,
        ),
        handler,
    )

    assert result.status_code == 201
    assert result.json_body == {"ok": True}
    assert result.headers["x-result"] == "done"


@pytest.mark.parametrize(
    ("auth", "expected_header", "expected_query"),
    [
        (
            {"type": "BEARER", "token": "Case-Sensitive.Token", "placement": None},
            "Bearer Case-Sensitive.Token",
            None,
        ),
        (
            {
                "type": "BASIC",
                "username": "用户",
                "password": "p:a:ss",
                "placement": "HEADER",
            },
            "Basic " + base64.b64encode("用户:p:a:ss".encode()).decode("ascii"),
            None,
        ),
        (
            {
                "type": "API_KEY",
                "key_name": "X-Target-Key",
                "key_value": "Header-Value",
                "placement": "HEADER",
            },
            "Header-Value",
            None,
        ),
        (
            {
                "type": "API_KEY",
                "key_name": "api_key",
                "key_value": "Query-Value",
                "placement": "QUERY",
            },
            None,
            "Query-Value",
        ),
    ],
)
def test_auth_types_are_injected_into_actual_request(
    auth: dict[str, Any],
    expected_header: str | None,
    expected_query: str | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if auth["type"] in {"BEARER", "BASIC"}:
            assert request.headers["Authorization"] == expected_header
        elif auth.get("placement") == "HEADER":
            assert request.headers[auth["key_name"]] == expected_header
        else:
            assert request.url.params[auth["key_name"]] == expected_query
        return httpx.Response(200, text="ok")

    result = _execute_with_transport(_mapping(auth, follow_redirects=False), handler)
    assert result.text == "ok"


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "BEARER"},
        {"type": "BEARER", "token": 7},
        {"type": "BEARER", "token": "secret", "username": "not-allowed"},
        {"type": "BASIC", "username": "left:right", "password": "secret"},
        {"type": "API_KEY", "key_name": "X-Key"},
        {"type": "API_KEY", "key_name": "X-Key", "key_value": "secret", "placement": None},
        {"type": "NONE", "placement": "QUERY"},
        {"type": "NONE", "extra": "not-allowed"},
        {"type": "BEARER", "token": "line\nbreak"},
    ],
)
def test_auth_mapping_rejects_missing_unrelated_or_invalid_values(
    auth: dict[str, Any],
) -> None:
    with pytest.raises(ProtocolError):
        ApiRequestTemplate.from_mapping(_mapping(auth))


def test_direct_construction_validates_auth_inputs_and_tampering() -> None:
    with pytest.raises(ProtocolError):
        RequestAuth(type="BASIC", username="user:name", password="secret")
    with pytest.raises(ProtocolError):
        ApiRequestTemplate(  # type: ignore[arg-type]
            "GET",
            "https://target.example.test",
            headers=[RequestValue("X-Test", "value")],
        )

    auth = RequestAuth(type="BEARER", token="secret")
    template = ApiRequestTemplate("GET", "https://target.example.test", auth=auth)
    object.__setattr__(auth, "username", "illegal")
    with ApiExecutor() as executor, pytest.raises(ProtocolError):
        executor.execute(template)


@pytest.mark.parametrize(
    "request_data",
    [
        _mapping(
            {"type": "BEARER", "token": "secret"},
            headers=[{"name": "authorization", "value": "explicit", "enabled": True}],
        ),
        _mapping(
            {
                "type": "API_KEY",
                "key_name": "api_key",
                "key_value": "secret",
                "placement": "QUERY",
            },
            query_params=[{"name": "API_KEY", "value": "explicit", "enabled": True}],
        ),
        _mapping(
            {
                "type": "API_KEY",
                "key_name": "X-Key",
                "key_value": "secret",
                "placement": "HEADER",
            },
            headers=[{"name": "x-key", "value": "explicit", "enabled": True}],
        ),
    ],
)
def test_auth_injection_rejects_explicit_name_conflicts(request_data: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError, match="冲突"):
        ApiRequestTemplate.from_mapping(request_data)


def test_same_origin_redirect_keeps_auth_cookie_and_body_within_budget() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/next?server=1"})
        return httpx.Response(200, json={"redirected": True})

    result = _execute_with_transport(
        _mapping(
            {
                "type": "API_KEY",
                "key_name": "api_key",
                "key_value": "secret-query-value",
                "placement": "QUERY",
            },
            method="POST",
            url="https://same.example.test/start",
            query_params=[{"name": "first", "value": "only-once", "enabled": True}],
            cookies=[{"name": "sid", "value": "manual-cookie", "enabled": True}],
            body={"type": "RAW", "content": "same-body", "content_type": "text/plain"},
        ),
        handler,
    )

    assert result.json_body == {"redirected": True}
    assert len(seen) == 2
    assert seen[0].url.params.multi_items() == [
        ("first", "only-once"),
        ("api_key", "secret-query-value"),
    ]
    assert seen[1].url.params.multi_items() == [
        ("server", "1"),
        ("api_key", "secret-query-value"),
    ]
    assert seen[1].headers["Cookie"] == "sid=manual-cookie"
    assert seen[1].content == b"same-body"


def test_cross_origin_redirect_strips_credentials_and_client_defaults() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "source.example.test":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        "http://other.example.test/next?"
                        "api_key=leaked&session_token=leaked&next=ok"
                    )
                },
            )
        return httpx.Response(200, text="safe")

    result = _execute_with_transport(
        _mapping(
            {
                "type": "API_KEY",
                "key_name": "api_key",
                "key_value": "auth-secret",
                "placement": "QUERY",
            },
            url="https://source.example.test/start",
            query_params=[
                {"name": "session_token", "value": "query-secret", "enabled": True},
                {"name": "q", "value": "public", "enabled": True},
            ],
            headers=[
                {"name": "X-Private", "value": "header-secret", "enabled": True},
                {"name": "Accept", "value": "application/json", "enabled": True},
            ],
            cookies=[{"name": "sid", "value": "manual-secret", "enabled": True}],
        ),
        handler,
        params={"default_token": "client-secret"},
        cookies={"ambient": "client-cookie"},
        headers={"X-Client-Secret": "client-header"},
    )

    assert result.text == "safe"
    assert len(seen) == 2
    assert seen[0].url.params.multi_items() == [
        ("session_token", "query-secret"),
        ("q", "public"),
        ("api_key", "auth-secret"),
    ]
    assert seen[0].headers["Cookie"] == "sid=manual-secret"
    assert seen[1].url.params.multi_items() == [("next", "ok")]
    assert "api_key" not in seen[1].url.params
    assert "session_token" not in seen[1].url.params
    assert "Cookie" not in seen[1].headers
    assert "X-Private" not in seen[1].headers
    assert "X-Client-Secret" not in seen[1].headers
    assert seen[1].headers["Accept"] == "application/json"


def test_cross_origin_preserved_body_redirect_is_rejected_before_second_send() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://other.example.test/unsafe"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        executor = ApiExecutor(client)
        with pytest.raises(ExecutionError) as raised:
            executor.execute(
                _mapping(
                    {"type": "BEARER", "token": "secret"},
                    method="POST",
                    body={"type": "RAW", "content": "sensitive", "content_type": None},
                )
            )

    assert raised.value.error_type == "UNSAFE_REDIRECT"
    assert len(seen) == 1


def test_redirect_chain_uses_one_total_timeout_budget() -> None:
    seen: list[httpx.Request] = []
    clock_values = iter((0.0, 0.0, 2.0))

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "/next"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        executor = ApiExecutor(client, clock=lambda: next(clock_values))
        with pytest.raises(TargetTimeoutError):
            executor.execute(_mapping({"type": "NONE"}))

    assert len(seen) == 1


def test_sensitive_request_objects_and_redirect_errors_have_safe_repr() -> None:
    secret = "credential-value-123456789"
    value = RequestValue("X-Secret", secret)
    body = RequestBody("RAW", secret, "text/plain")
    auth = RequestAuth(type="BEARER", token=secret)
    template = ApiRequestTemplate(
        "POST",
        f"https://target.example.test/api?token={secret}",
        headers=(value,),
        body=body,
        auth=auth,
    )

    for item in (value, body, auth, template):
        assert secret not in repr(item)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": f"ftp://host/path?token={secret}"})

    with pytest.raises(ExecutionError) as raised:
        _execute_with_transport(template, handler)
    assert secret not in str(raised.value)
    assert raised.value.error_type == "UNSAFE_REDIRECT"
