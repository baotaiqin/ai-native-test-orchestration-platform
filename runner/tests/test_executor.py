from __future__ import annotations

from typing import Any

import httpx
import pytest

from runner.errors import (
    ExecutionError,
    ProtocolError,
    TargetExecutionError,
    TargetTimeoutError,
)
from runner.executors.api import ApiExecutor, ApiRequestTemplate, RetryPolicy


class FakeApiClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize("max_retries", [True, -1, 2, 3])
def test_retry_policy_constructor_rejects_non_v1_retry_limits(
    max_retries: object,
) -> None:
    with pytest.raises(ProtocolError, match="仅允许 0 或 1"):
        RetryPolicy(  # type: ignore[arg-type]
            max_retries=max_retries,
            retry_on=("TARGET_NETWORK_ERROR",),
        )


def test_api_executor_revalidates_tampered_policy_before_target_request() -> None:
    policy = RetryPolicy(1, 0, ("TARGET_NETWORK_ERROR",))
    request = ApiRequestTemplate(
        "GET",
        "https://target.example.test/api",
        retry_policy=policy,
    )
    object.__setattr__(policy, "max_retries", 2)
    client = FakeApiClient(httpx.Response(200, content=b"ok"))

    with pytest.raises(ProtocolError, match="仅允许 0 或 1"):
        ApiExecutor(client).execute(request)

    assert client.calls == []


def _template(*, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": "https://target.example.test/api",
        "query_params": [{"name": "q", "value": "1", "enabled": True}],
        "headers": [{"name": "X-Test", "value": "ok", "enabled": True}],
        "cookies": [{"name": "session", "value": "opaque", "enabled": True}],
        "body": body or {"type": "NONE", "content": None, "content_type": None},
        "auth": {
            "type": "NONE",
            "token": None,
            "username": None,
            "password": None,
            "key_name": None,
            "key_value": None,
            "placement": "HEADER",
        },
        "timeout_ms": 1500,
        "follow_redirects": True,
    }


def test_api_executor_maps_enabled_request_parts_and_safe_response() -> None:
    client = FakeApiClient(
        httpx.Response(
            201,
            content=b"sensitive response body",
            headers={
                "X-Result": "ok",
                "Set-Cookie": "sid=secret-cookie; Path=/",
                "X-Secret": "another-secret",
            },
            request=httpx.Request("GET", "https://target.example.test"),
        )
    )
    result = ApiExecutor(client).execute(_template())

    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://target.example.test/api")
    assert kwargs["params"] == [("q", "1")]
    assert kwargs["headers"] == {"X-Test": "ok"}
    assert kwargs["cookies"] == {"session": "opaque"}
    assert kwargs["timeout"] == 1.5
    assert kwargs["follow_redirects"] is False
    assert result.status_code == 201
    assert result.headers["x-result"] == "ok"
    assert result.headers["set-cookie"] == "sid=secret-cookie; Path=/"
    assert result.cookies == {"sid": "secret-cookie"}
    assert result.text == "sensitive response body"
    assert result.json_body is None
    assert "sensitive response body" not in repr(result)
    assert "secret-cookie" not in repr(result)


def test_api_executor_keeps_json_snapshot_for_server_assertions() -> None:
    client = FakeApiClient(
        httpx.Response(
            200,
            content=b'{"id":7,"ok":true}',
            headers={"Content-Type": "application/json", "X-Result": "ok"},
            request=httpx.Request("GET", "https://target.example.test"),
        )
    )

    result = ApiExecutor(client).execute(_template())

    assert result.json_body == {"id": 7, "ok": True}
    assert result.text is None
    assert result.to_wire() == {
        "status_code": 200,
        "json_body": {"id": 7, "ok": True},
        "text": None,
        "headers": {"content-length": "18", "content-type": "application/json", "x-result": "ok"},
        "cookies": {},
        "elapsed_ms": result.elapsed_ms,
        "response_time_ms": result.elapsed_ms,
    }


@pytest.mark.parametrize(
    ("body", "keyword"),
    [
        ({"type": "JSON", "content": {"a": 1}, "content_type": "application/json"}, "json"),
        (
            {"type": "FORM_URLENCODED", "content": {"a": "1"}, "content_type": None},
            "data",
        ),
        ({"type": "RAW", "content": "hello", "content_type": "text/plain"}, "content"),
    ],
)
def test_api_executor_supports_safe_body_types(body: dict[str, Any], keyword: str) -> None:
    client = FakeApiClient(httpx.Response(200, content=b"ok"))
    ApiExecutor(client).execute(_template(body=body))

    kwargs = client.calls[0][2]
    assert keyword in kwargs
    if keyword == "json":
        assert kwargs[keyword] == {"a": 1}
    if keyword == "data":
        assert list(kwargs[keyword]) == [("a", "1")]
    if keyword == "content":
        assert kwargs[keyword] == b"hello"


@pytest.mark.parametrize(
    "change",
    [
        {"url": "{{runtime.url}}"},
    ],
)
def test_api_executor_fails_closed_for_unresolved_runtime(
    change: dict[str, Any],
) -> None:
    request = _template()
    request.update(change)
    client = FakeApiClient(httpx.Response(200, content=b"should-not-send"))

    with pytest.raises((ExecutionError, ProtocolError)):
        ApiExecutor(client).execute(request)
    assert client.calls == []


def test_api_executor_supports_bounded_inline_multipart_without_host_paths() -> None:
    client = FakeApiClient(httpx.Response(200, content=b"ok"))
    body = {
        "type": "MULTIPART",
        "content": [
            {"name": "description", "value": "safe text", "enabled": True},
            {
                "name": "attachment",
                "value": "aGVsbG8=",
                "enabled": True,
                "filename": "hello.txt",
                "content_type": "text/plain",
                "encoding": "BASE64",
            },
        ],
    }

    ApiExecutor(client).execute(_template(body=body))

    assert client.calls[0][2]["files"] == [
        ("description", (None, "safe text", None)),
        ("attachment", ("hello.txt", b"hello", "text/plain")),
    ]
    unsafe = _template(
        body={
            "type": "MULTIPART",
            "content": [
                {
                    "name": "attachment",
                    "value": "aGVsbG8=",
                    "filename": "../secret.txt",
                    "content_type": "text/plain",
                    "encoding": "BASE64",
                }
            ],
        }
    )
    with pytest.raises(ProtocolError, match="MULTIPART"):
        ApiExecutor(FakeApiClient(httpx.Response(200))).execute(unsafe)


def test_api_executor_caps_response_reading_and_rejects_oversized_request() -> None:
    client = FakeApiClient(
        httpx.Response(
            200,
            content=b"x" * 2_000_001,
            headers={"content-length": "2000001"},
        )
    )
    with pytest.raises(ExecutionError, match="2,000,000"):
        ApiExecutor(client).execute(_template())

    huge = _template(body={"type": "RAW", "content": "x" * (2 * 1024 * 1024 + 1)})
    with pytest.raises(ExecutionError, match="2 MiB"):
        ApiExecutor(FakeApiClient(httpx.Response(200))).execute(huge)


def test_api_executor_converts_target_network_error_without_secret() -> None:
    secret = "credential-value-123456789"
    client = FakeApiClient(error=httpx.ConnectError(secret))

    with pytest.raises(TargetExecutionError) as error:
        ApiExecutor(client).execute(_template())
    assert secret not in str(error.value)
    assert error.value.error_type == "TARGET_NETWORK_ERROR"


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("secret-timeout"), TimeoutError("secret-timeout")],
)
def test_api_executor_converts_target_timeout_without_secret(error: Exception) -> None:
    client = FakeApiClient(error=error)

    with pytest.raises(TargetTimeoutError) as raised:
        ApiExecutor(client).execute(_template())

    assert str(raised.value) == "目标 API 请求超时"
    assert raised.value.error_type == "TARGET_TIMEOUT"
    assert "secret-timeout" not in str(raised.value)
