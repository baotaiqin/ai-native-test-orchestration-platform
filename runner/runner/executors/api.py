"""具备内存认证与受控重定向的 API_CASE HTTP 执行器。"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from runner.errors import (
    ConfigurationError,
    ExecutionError,
    ProtocolError,
    TargetExecutionError,
    TargetTimeoutError,
)

MAX_RESPONSE_BYTES = 2_000_000
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_HEADER_COUNT = 200
MAX_RESPONSE_HEADER_COUNT = 100
MAX_COOKIE_COUNT = 100
MAX_ITEM_NAME = 255
MAX_ITEM_VALUE = 10_000
MAX_BODY_CONTENT_TYPE = 255
MAX_TIMEOUT_MS = 120_000
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_ALLOWED_BODY_TYPES = frozenset({"NONE", "JSON", "FORM_URLENCODED", "MULTIPART", "RAW"})
_ALLOWED_AUTH_TYPES = frozenset({"NONE", "BEARER", "BASIC", "API_KEY"})
_ALLOWED_RETRY_ON = frozenset({"TARGET_NETWORK_ERROR", "TARGET_TIMEOUT", "HTTP_5XX"})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_HTTP_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
MAX_STEP_RETRIES = 1
MAX_REDIRECTS = 10


def _validate_retry_policy_values(
    max_retries: object,
    backoff_ms: object,
    retry_on: object,
) -> None:
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or not 0 <= max_retries <= MAX_STEP_RETRIES
    ):
        raise ProtocolError("request.retry_policy.max_retries 仅允许 0 或 1")
    if (
        isinstance(backoff_ms, bool)
        or not isinstance(backoff_ms, int)
        or not 0 <= backoff_ms <= 30_000
    ):
        raise ProtocolError("request.retry_policy.backoff_ms 超出允许范围")
    if not isinstance(retry_on, tuple) or len(retry_on) > len(_ALLOWED_RETRY_ON):
        raise ProtocolError("request.retry_policy.retry_on 必须是有限元组")
    if any(not isinstance(item, str) or item not in _ALLOWED_RETRY_ON for item in retry_on):
        raise ProtocolError("request.retry_policy.retry_on 包含不支持的条件")
    if len(retry_on) != len(set(retry_on)):
        raise ProtocolError("request.retry_policy.retry_on 不能重复")
    if max_retries > 0 and not retry_on:
        raise ProtocolError("启用重试时 retry_on 不能为空")


@dataclass(frozen=True, repr=False)
class RequestValue:
    name: str
    value: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= MAX_ITEM_NAME:
            raise ProtocolError("request item name 缺失或长度无效")
        if not isinstance(self.value, str) or len(self.value) > MAX_ITEM_VALUE:
            raise ProtocolError("request item value 长度无效")
        if "\r" in self.value or "\n" in self.value:
            raise ProtocolError("request item value 不能包含换行符")
        if type(self.enabled) is not bool:
            raise ProtocolError("request item enabled 必须是 bool")

    def __repr__(self) -> str:
        return f"RequestValue(name={self.name!r}, enabled={self.enabled!r}, value=<redacted>)"


@dataclass(frozen=True, repr=False)
class RequestBody:
    type: str = "NONE"
    content: Any | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        _validate_body(self)

    def __repr__(self) -> str:
        return f"RequestBody(type={self.type!r}, content=<redacted>)"


@dataclass(frozen=True, repr=False)
class RequestAuth:
    """Resolved target-API credentials kept only in Runner memory."""

    type: str = "NONE"
    token: str | None = None
    username: str | None = None
    password: str | None = None
    key_name: str | None = None
    key_value: str | None = None
    placement: str | None = "HEADER"

    def __post_init__(self) -> None:
        if self.placement is None:
            if self.type == "API_KEY":
                raise ProtocolError("request.auth.placement 无效")
            object.__setattr__(self, "placement", "HEADER")
        _validate_auth(self)

    def __repr__(self) -> str:
        return f"RequestAuth(type={self.type!r}, placement={self.placement!r}, value=<redacted>)"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    backoff_ms: int = 0
    retry_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_retry_policy_values(
            self.max_retries,
            self.backoff_ms,
            self.retry_on,
        )


def validate_retry_policy(value: object) -> RetryPolicy:
    """Validate a policy again at an execution boundary, including tampered values."""

    if not isinstance(value, RetryPolicy):
        raise ProtocolError("request.retry_policy 必须是 RetryPolicy")
    _validate_retry_policy_values(
        value.max_retries,
        value.backoff_ms,
        value.retry_on,
    )
    return value


@dataclass(frozen=True, repr=False)
class ApiRequestTemplate:
    method: str
    url: str
    query_params: tuple[RequestValue, ...] = ()
    headers: tuple[RequestValue, ...] = ()
    cookies: tuple[RequestValue, ...] = ()
    body: RequestBody = field(default_factory=RequestBody)
    auth: RequestAuth = field(default_factory=RequestAuth)
    timeout_ms: int = 30_000
    follow_redirects: bool = True
    retry_policy: RetryPolicy = RetryPolicy()

    def __post_init__(self) -> None:
        _validate_template(self, revalidate_retry=False)

    @property
    def auth_type(self) -> str:
        """Compatibility accessor for callers that only inspected the old field."""

        return self.auth.type

    def __repr__(self) -> str:
        return (
            "ApiRequestTemplate("
            f"method={self.method!r}, auth_type={self.auth.type!r}, values=<redacted>)"
        )

    @classmethod
    def from_mapping(cls, value: object) -> ApiRequestTemplate:
        if not isinstance(value, Mapping):
            raise ProtocolError("execution plan request 必须是 JSON 对象")
        _reject_extra(
            value,
            {
                "method",
                "url",
                "query_params",
                "headers",
                "cookies",
                "body",
                "auth",
                "timeout_ms",
                "follow_redirects",
                "retry_policy",
            },
            "request",
        )
        method = _text(value.get("method"), "request.method", max_length=16).upper()
        if method not in _ALLOWED_METHODS:
            raise ProtocolError("request.method 不在 API_CASE 允许范围")
        url = _text(value.get("url"), "request.url", max_length=4000).strip()
        _validate_target_url(url)
        query_params = _parse_items(value.get("query_params", []), "query_params", 200)
        headers = _parse_items(value.get("headers", []), "headers", MAX_HEADER_COUNT)
        cookies = _parse_items(value.get("cookies", []), "cookies", MAX_COOKIE_COUNT)
        body = _parse_body(value.get("body", {}))
        auth = _parse_auth(value.get("auth", {}))
        timeout_ms = value.get("timeout_ms", 30_000)
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
            raise ProtocolError("request.timeout_ms 必须是整数")
        if not 100 <= timeout_ms <= MAX_TIMEOUT_MS:
            raise ProtocolError("request.timeout_ms 超出允许范围")
        follow_redirects = value.get("follow_redirects", True)
        if type(follow_redirects) is not bool:
            raise ProtocolError("request.follow_redirects 必须是 bool")
        retry_policy = (
            _parse_retry_policy(value["retry_policy"]) if "retry_policy" in value else RetryPolicy()
        )
        _reject_duplicate_enabled(query_params, "query_params")
        _reject_duplicate_enabled(headers, "headers")
        _reject_duplicate_enabled(cookies, "cookies")
        return cls(
            method=method,
            url=url,
            query_params=tuple(query_params),
            headers=tuple(headers),
            cookies=tuple(cookies),
            body=body,
            auth=auth,
            timeout_ms=timeout_ms,
            follow_redirects=follow_redirects,
            retry_policy=retry_policy,
        )


@dataclass(frozen=True, repr=False)
class ApiExecutionResult:
    """内存中的 API 响应快照；默认 repr 和 wire 均不泄露正文。"""

    status_code: int
    json_body: Any | None
    text: str | None
    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    elapsed_ms: int
    response_time_ms: int
    body_size: int

    def __repr__(self) -> str:
        return (
            "ApiExecutionResult("
            f"status_code={self.status_code}, body_size={self.body_size}, "
            f"elapsed_ms={self.elapsed_ms})"
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "json_body": self.json_body,
            "text": self.text,
            "headers": dict(self.headers),
            "cookies": dict(self.cookies),
            "elapsed_ms": self.elapsed_ms,
            "response_time_ms": self.response_time_ms,
        }

    def to_summary(self) -> dict[str, Any]:
        """仅供本地安全输出，不可用于 execution-complete wire。"""

        return {
            "status_code": self.status_code,
            "body_size": self.body_size,
            "body_truncated": False,
            "elapsed_ms": self.elapsed_ms,
        }


class ApiHttpClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class ApiExecutor:
    """Execute API_CASE requests with in-memory auth and controlled redirects."""

    def __init__(
        self,
        http_client: ApiHttpClient | None = None,
        *,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ConfigurationError("响应读取上限必须在 1 到 2,000,000 bytes 之间")
        self._client = http_client or httpx.Client(follow_redirects=False, trust_env=False)
        self._max_response_bytes = max_response_bytes
        self._clock = clock

    def execute(self, request: ApiRequestTemplate | Mapping[str, Any]) -> ApiExecutionResult:
        template = (
            request
            if isinstance(request, ApiRequestTemplate)
            else ApiRequestTemplate.from_mapping(request)
        )
        _validate_template(template)
        if template.url.startswith("{{"):
            raise ExecutionError(
                "Runtime URL 模板在当前阶段不可执行", error_type="RUNTIME_TEMPLATE"
            )
        if template.body.type not in _ALLOWED_BODY_TYPES:
            raise ExecutionError("当前 API_CASE 请求体类型不可执行", error_type="BODY_UNSUPPORTED")
        started = self._clock()
        response: Any | None = None
        try:
            response = self._request_with_redirects(template, started)
            elapsed_ms = max(0, int((self._clock() - started) * 1000))
            return self._summarize_response(response, elapsed_ms)
        except httpx.TimeoutException as exc:
            raise TargetTimeoutError() from exc
        except TimeoutError as exc:
            raise TargetTimeoutError() from exc
        except httpx.RequestError as exc:
            raise TargetExecutionError(
                "目标 API 网络请求失败",
                error_type="TARGET_NETWORK_ERROR",
            ) from exc
        except OSError as exc:
            raise TargetExecutionError(
                "目标 API 网络请求失败",
                error_type="TARGET_NETWORK_ERROR",
            ) from exc
        except (httpx.InvalidURL, UnicodeError, ValueError) as exc:
            raise ExecutionError(
                "目标 API 请求无法安全编码",
                error_type="REQUEST_INVALID",
            ) from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

    def _request_with_redirects(
        self,
        template: ApiRequestTemplate,
        started: float,
    ) -> Any:
        request_kwargs = self._request_kwargs(template)
        current_method = template.method
        current_url = template.url
        current_headers = dict(request_kwargs.pop("headers"))
        current_cookies = dict(request_kwargs.pop("cookies"))
        current_params = list(request_kwargs.pop("params"))
        current_body = request_kwargs
        auth_query = _auth_query(template.auth)
        source_sensitive_query_names = _sensitive_query_names(template)
        credentials_active = True

        for redirect_count in range(MAX_REDIRECTS + 1):
            timeout_seconds = _remaining_timeout_seconds(
                template.timeout_ms,
                started,
                self._clock,
            )
            response = self._send_once(
                current_method,
                current_url,
                params=current_params,
                headers=current_headers,
                cookies=current_cookies,
                body=current_body,
                timeout_seconds=timeout_seconds,
            )
            status_code = getattr(response, "status_code", None)
            location = _redirect_location(response)
            if (
                not template.follow_redirects
                or status_code not in _REDIRECT_STATUS_CODES
                or location is None
            ):
                return response
            _close_response(response)
            if redirect_count >= MAX_REDIRECTS:
                raise ExecutionError(
                    "目标 API 重定向次数超过限制",
                    error_type="REDIRECT_LIMIT",
                )

            target_url = _resolve_redirect_url(current_url, location)
            cross_origin = _origin(current_url) != _origin(target_url)
            next_method, drop_body = _redirect_method(current_method, status_code)
            if cross_origin and current_body and not drop_body:
                raise ExecutionError(
                    "目标 API 跨源重定向无法安全转发请求体",
                    error_type="UNSAFE_REDIRECT",
                )
            if cross_origin and not callable(getattr(self._client, "send", None)):
                raise ExecutionError(
                    "目标 API 跨源重定向无法安全跟随",
                    error_type="UNSAFE_REDIRECT",
                )

            current_method = next_method
            if drop_body:
                current_body = {}
                current_headers = _without_entity_headers(current_headers)
            if cross_origin:
                credentials_active = False
                current_url = _without_named_query(target_url, source_sensitive_query_names)
                current_headers = _cross_origin_headers(current_headers)
                current_cookies = {}
                current_params = []
            else:
                current_url = target_url
                current_params = list(auth_query) if credentials_active else []
                if current_params:
                    current_url = _without_named_query(
                        current_url,
                        {name.lower() for name, _ in current_params},
                    )

        raise AssertionError("bounded redirect loop exhausted")

    def _send_once(
        self,
        method: str,
        url: str,
        *,
        params: list[tuple[str, str]],
        headers: dict[str, str],
        cookies: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        send = getattr(self._client, "send", None)
        if callable(send):
            request_url = _with_query_params(url, params)
            raw_request = httpx.Request(
                method,
                request_url,
                headers=headers,
                cookies=cookies,
                extensions={"timeout": httpx.Timeout(timeout_seconds).as_dict()},
                **body,
            )
            return send(
                raw_request,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
        return self._client.request(
            method,
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=timeout_seconds,
            follow_redirects=False,
            auth=None,
            **body,
        )

    def _request_kwargs(self, template: ApiRequestTemplate) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "params": [(item.name, item.value) for item in template.query_params if item.enabled],
            "headers": {item.name: item.value for item in template.headers if item.enabled},
            "cookies": {item.name: item.value for item in template.cookies if item.enabled},
        }
        _inject_auth(template.auth, kwargs["headers"], kwargs["params"])
        body = template.body
        if body.type == "JSON":
            encoded = json.dumps(body.content, ensure_ascii=False, separators=(",", ":")).encode()
            _check_body_size(encoded)
            kwargs["json"] = body.content
        elif body.type == "FORM_URLENCODED":
            data = _form_data(body.content)
            encoded = str(httpx.QueryParams(data)).encode()
            _check_body_size(encoded)
            kwargs["data"] = data
        elif body.type == "RAW":
            if not isinstance(body.content, str):
                raise ProtocolError("RAW 请求体 content 必须是字符串")
            encoded = body.content.encode()
            _check_body_size(encoded)
            kwargs["content"] = encoded
        elif body.type == "MULTIPART":
            files, encoded_size = _multipart_data(body.content)
            if encoded_size > MAX_REQUEST_BYTES:
                raise ExecutionError("API 请求体超过 2 MiB 限制", error_type="REQUEST_TOO_LARGE")
            kwargs["files"] = files
        if body.content_type:
            kwargs["headers"] = {**kwargs["headers"], "Content-Type": body.content_type}
        return kwargs

    def _summarize_response(self, response: Any, elapsed_ms: int) -> ApiExecutionResult:
        status_code = getattr(response, "status_code", None)
        if (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 100 <= status_code <= 599
        ):
            raise ProtocolError("目标 API 响应状态码无效")
        content, too_large = _read_capped_content(response, self._max_response_bytes)
        if too_large or _header_indicates_truncation(
            getattr(response, "headers", {}), self._max_response_bytes
        ):
            raise ExecutionError(
                "目标 API 响应超过 2,000,000 bytes",
                error_type="RESPONSE_TOO_LARGE",
            )
        headers = _response_map(
            getattr(response, "headers", {}),
            label="响应 Header",
            max_count=MAX_RESPONSE_HEADER_COUNT,
        )
        cookies = _response_cookies(response)
        decoded = _decode_body(content, response)
        json_body, text = _parse_response_body(decoded)
        _validate_snapshot_body_size(json_body, text)
        return ApiExecutionResult(
            status_code=status_code,
            json_body=json_body,
            text=text,
            headers=headers,
            cookies=cookies,
            elapsed_ms=elapsed_ms,
            response_time_ms=elapsed_ms,
            body_size=len(content),
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> ApiExecutor:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _reject_extra(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ProtocolError(f"{label} 包含未知字段")


def _validate_template(
    template: object,
    *,
    revalidate_retry: bool = True,
) -> ApiRequestTemplate:
    if not isinstance(template, ApiRequestTemplate):
        raise ProtocolError("request 必须是 ApiRequestTemplate")
    if not isinstance(template.method, str):
        raise ProtocolError("request.method 无效")
    normalized_method = template.method.upper()
    if normalized_method not in _ALLOWED_METHODS:
        raise ProtocolError("request.method 不在 API_CASE 允许范围")
    if template.method != normalized_method:
        object.__setattr__(template, "method", normalized_method)
    if not isinstance(template.url, str) or not 1 <= len(template.url) <= 4000:
        raise ProtocolError("request.url 缺失或长度无效")
    normalized_url = template.url.strip()
    if not normalized_url:
        raise ProtocolError("request.url 缺失或长度无效")
    if template.url != normalized_url:
        object.__setattr__(template, "url", normalized_url)
    _validate_target_url(normalized_url)
    _validate_request_items(template.query_params, "query_params", 200)
    _validate_request_items(template.headers, "headers", MAX_HEADER_COUNT)
    _validate_request_items(template.cookies, "cookies", MAX_COOKIE_COUNT)
    _validate_body(template.body)
    _validate_auth(template.auth)
    if (
        isinstance(template.timeout_ms, bool)
        or not isinstance(template.timeout_ms, int)
        or not 100 <= template.timeout_ms <= MAX_TIMEOUT_MS
    ):
        raise ProtocolError("request.timeout_ms 超出允许范围")
    if type(template.follow_redirects) is not bool:
        raise ProtocolError("request.follow_redirects 必须是 bool")
    if not isinstance(template.retry_policy, RetryPolicy):
        raise ProtocolError("request.retry_policy 必须是 RetryPolicy")
    if revalidate_retry:
        validate_retry_policy(template.retry_policy)
    _reject_duplicate_enabled(template.query_params, "query_params")
    _reject_duplicate_enabled(template.headers, "headers")
    _reject_duplicate_enabled(template.cookies, "cookies")
    _validate_auth_conflicts(template)
    return template


def _validate_request_items(
    items: object,
    label: str,
    max_count: int,
) -> None:
    if not isinstance(items, tuple) or len(items) > max_count:
        raise ProtocolError(f"request.{label} 必须是有限元组")
    for item in items:
        if not isinstance(item, RequestValue):
            raise ProtocolError(f"request.{label} 项必须是 RequestValue")
        if not isinstance(item.name, str) or not 1 <= len(item.name) <= MAX_ITEM_NAME:
            raise ProtocolError(f"request.{label}.name 缺失或长度无效")
        if not isinstance(item.value, str) or len(item.value) > MAX_ITEM_VALUE:
            raise ProtocolError(f"request.{label}.value 长度无效")
        if "\r" in item.value or "\n" in item.value:
            raise ProtocolError(f"request.{label}.value 不能包含换行符")
        if type(item.enabled) is not bool:
            raise ProtocolError(f"request.{label}.enabled 必须是 bool")
        if label == "headers":
            _validate_header_name(item.name, "request.headers.name")
            if _has_http_control(item.value):
                raise ProtocolError("request.headers.value 包含 HTTP 控制字符")
        elif label == "cookies":
            _validate_header_name(item.name, "request.cookies.name")
            if _has_http_control(item.value):
                raise ProtocolError("request.cookies.value 包含 HTTP 控制字符")


def _validate_body(body: object) -> RequestBody:
    if not isinstance(body, RequestBody):
        raise ProtocolError("request.body 必须是 RequestBody")
    if not isinstance(body.type, str) or body.type not in {
        "NONE",
        "JSON",
        "FORM_URLENCODED",
        "MULTIPART",
        "RAW",
    }:
        raise ProtocolError("request.body.type 无效")
    if body.type == "NONE" and body.content is not None:
        raise ProtocolError("NONE 请求体不能包含 content")
    if body.type != "NONE" and body.content is None:
        raise ProtocolError("非 NONE 请求体必须包含 content")
    if body.type == "JSON" and not isinstance(body.content, (dict, list)):
        raise ProtocolError("JSON 请求体必须是对象或数组")
    if body.type == "MULTIPART":
        _multipart_data(body.content)
        if body.content_type is not None:
            raise ProtocolError("MULTIPART Content-Type 必须由 Runner 生成 boundary")
    if body.content_type is not None and (
        not isinstance(body.content_type, str)
        or len(body.content_type) > MAX_BODY_CONTENT_TYPE
        or _has_http_control(body.content_type)
    ):
        raise ProtocolError("request.body.content_type 长度或字符无效")
    return body


def _validate_auth(auth: object) -> RequestAuth:
    if not isinstance(auth, RequestAuth):
        raise ProtocolError("request.auth 必须是 RequestAuth")
    if not isinstance(auth.type, str) or auth.type not in _ALLOWED_AUTH_TYPES:
        raise ProtocolError("request.auth.type 无效")
    if auth.placement not in {"HEADER", "QUERY"}:
        raise ProtocolError("request.auth.placement 无效")
    limits = {
        "token": 10_000,
        "username": 1_000,
        "password": 10_000,
        "key_name": 255,
        "key_value": 10_000,
    }
    for field_name, limit in limits.items():
        value = getattr(auth, field_name)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ProtocolError(f"request.auth.{field_name} 类型或长度无效")

    required: tuple[str, ...]
    unrelated: tuple[str, ...]
    if auth.type == "NONE":
        required = ()
        unrelated = ("token", "username", "password", "key_name", "key_value")
    elif auth.type == "BEARER":
        required = ("token",)
        unrelated = ("username", "password", "key_name", "key_value")
    elif auth.type == "BASIC":
        required = ("username", "password")
        unrelated = ("token", "key_name", "key_value")
    else:
        required = ("key_name", "key_value")
        unrelated = ("token", "username", "password")
    if any(not getattr(auth, field) for field in required):
        raise ProtocolError("request.auth 缺少当前认证类型的必需值")
    if any(getattr(auth, field) is not None for field in unrelated):
        raise ProtocolError("request.auth 包含当前认证类型不允许的值")
    if auth.type != "API_KEY" and auth.placement != "HEADER":
        raise ProtocolError("request.auth.placement 与认证类型不匹配")
    if auth.type == "BASIC" and auth.username is not None and ":" in auth.username:
        raise ProtocolError("request.auth.username 不能包含冒号")
    if auth.type == "BEARER" and auth.token is not None and _has_http_control(auth.token):
        raise ProtocolError("request.auth.token 包含 HTTP 控制字符")
    if auth.type == "API_KEY" and auth.key_name is not None:
        if auth.placement == "HEADER":
            _validate_header_name(auth.key_name, "request.auth.key_name")
            if auth.key_value is not None and _has_http_control(auth.key_value):
                raise ProtocolError("request.auth.key_value 包含 HTTP 控制字符")
        elif "\r" in auth.key_name or "\n" in auth.key_name:
            raise ProtocolError("request.auth.key_name 包含换行符")
    return auth


def _validate_auth_conflicts(template: ApiRequestTemplate) -> None:
    header_names = {item.name.lower() for item in template.headers if item.enabled}
    query_names = {item.name.lower() for item in template.query_params if item.enabled}
    query_names.update(name.lower() for name, _ in parse_qsl(urlsplit(template.url).query))
    cookie_names = {item.name.lower() for item in template.cookies if item.enabled}
    if cookie_names and "cookie" in header_names:
        raise ProtocolError("request.headers 与 request.cookies 存在 Cookie 冲突")
    if template.auth.type in {"BEARER", "BASIC"} and "authorization" in header_names:
        raise ProtocolError("request.auth 与显式 Header 名称冲突")
    if template.auth.type == "API_KEY" and template.auth.key_name is not None:
        name = template.auth.key_name.lower()
        names = header_names if template.auth.placement == "HEADER" else query_names
        if name in names:
            raise ProtocolError("request.auth 与显式请求项名称冲突")


def _has_http_control(value: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)


def _validate_header_name(name: str, label: str) -> None:
    if not _HTTP_TOKEN.fullmatch(name):
        raise ProtocolError(f"{label} 不是有效 HTTP 名称")


def _parse_retry_policy(value: object) -> RetryPolicy:
    if not isinstance(value, Mapping):
        raise ProtocolError("request.retry_policy 必须是对象")
    _reject_extra(
        value,
        {"max_retries", "backoff_ms", "retry_on"},
        "request.retry_policy",
    )
    max_retries = value.get("max_retries", 0)
    backoff_ms = value.get("backoff_ms", 0)
    retry_on = value.get("retry_on", [])
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or not 0 <= max_retries <= MAX_STEP_RETRIES
    ):
        raise ProtocolError("request.retry_policy.max_retries 仅允许 0 或 1")
    if (
        isinstance(backoff_ms, bool)
        or not isinstance(backoff_ms, int)
        or not 0 <= backoff_ms <= 30_000
    ):
        raise ProtocolError("request.retry_policy.backoff_ms 超出允许范围")
    if not isinstance(retry_on, list) or len(retry_on) > len(_ALLOWED_RETRY_ON):
        raise ProtocolError("request.retry_policy.retry_on 必须是有限数组")
    if any(not isinstance(item, str) or item not in _ALLOWED_RETRY_ON for item in retry_on):
        raise ProtocolError("request.retry_policy.retry_on 包含不支持的条件")
    if len(retry_on) != len(set(retry_on)):
        raise ProtocolError("request.retry_policy.retry_on 不能重复")
    if max_retries > 0 and not retry_on:
        raise ProtocolError("启用重试时 retry_on 不能为空")
    return RetryPolicy(max_retries, backoff_ms, tuple(retry_on))


def _text(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ProtocolError(f"{label} 缺失或长度无效")
    return value


def _parse_items(value: object, label: str, max_count: int) -> list[RequestValue]:
    if not isinstance(value, list) or len(value) > max_count:
        raise ProtocolError(f"request.{label} 数量无效")
    result: list[RequestValue] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ProtocolError(f"request.{label} 项必须是对象")
        _reject_extra(item, {"name", "value", "enabled", "description"}, f"request.{label} 项")
        name = _text(item.get("name"), f"request.{label}.name", max_length=MAX_ITEM_NAME).strip()
        value_text = item.get("value", "")
        if not isinstance(value_text, str) or len(value_text) > MAX_ITEM_VALUE:
            raise ProtocolError(f"request.{label}.value 长度无效")
        if "\r" in value_text or "\n" in value_text:
            raise ProtocolError(f"request.{label}.value 不能包含换行符")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise ProtocolError(f"request.{label}.enabled 必须是 bool")
        description = item.get("description")
        if description is not None and (not isinstance(description, str) or len(description) > 500):
            raise ProtocolError(f"request.{label}.description 长度无效")
        result.append(RequestValue(name, value_text, enabled))
    return result


def _parse_body(value: object) -> RequestBody:
    if not isinstance(value, Mapping):
        raise ProtocolError("request.body 必须是对象")
    _reject_extra(value, {"type", "content", "content_type"}, "request.body")
    body_type = value.get("type", "NONE")
    if not isinstance(body_type, str) or body_type not in {
        "NONE",
        "JSON",
        "FORM_URLENCODED",
        "MULTIPART",
        "RAW",
    }:
        raise ProtocolError("request.body.type 无效")
    content = value.get("content")
    if body_type == "NONE" and content is not None:
        raise ProtocolError("NONE 请求体不能包含 content")
    if body_type != "NONE" and content is None:
        raise ProtocolError("非 NONE 请求体必须包含 content")
    if body_type == "JSON" and not isinstance(content, (dict, list)):
        raise ProtocolError("JSON 请求体必须是对象或数组")
    content_type = value.get("content_type")
    if content_type is not None and (
        not isinstance(content_type, str)
        or len(content_type) > MAX_BODY_CONTENT_TYPE
        or "\r" in content_type
        or "\n" in content_type
    ):
        raise ProtocolError("request.body.content_type 长度无效")
    return RequestBody(body_type, content, content_type)


def _parse_auth(value: object) -> RequestAuth:
    if not isinstance(value, Mapping):
        raise ProtocolError("request.auth 必须是对象")
    _reject_extra(
        value,
        {"type", "token", "username", "password", "key_name", "key_value", "placement"},
        "request.auth",
    )
    auth_type = value.get("type", "NONE")
    if not isinstance(auth_type, str) or auth_type not in _ALLOWED_AUTH_TYPES:
        raise ProtocolError("request.auth.type 无效")
    placement = value.get("placement", "HEADER")
    if placement is None:
        if auth_type == "API_KEY":
            raise ProtocolError("request.auth.placement 无效")
        placement = "HEADER"
    if not isinstance(placement, str) or placement not in {"HEADER", "QUERY"}:
        raise ProtocolError("request.auth.placement 无效")
    return RequestAuth(
        type=auth_type,
        token=value.get("token"),
        username=value.get("username"),
        password=value.get("password"),
        key_name=value.get("key_name"),
        key_value=value.get("key_value"),
        placement=placement,
    )


def _inject_auth(
    auth: RequestAuth,
    headers: dict[str, str],
    query: list[tuple[str, str]],
) -> None:
    if auth.type == "BEARER":
        assert auth.token is not None
        headers["Authorization"] = f"Bearer {auth.token}"
    elif auth.type == "BASIC":
        assert auth.username is not None and auth.password is not None
        credential = f"{auth.username}:{auth.password}".encode()
        headers["Authorization"] = f"Basic {base64.b64encode(credential).decode('ascii')}"
    elif auth.type == "API_KEY":
        assert auth.key_name is not None and auth.key_value is not None
        if auth.placement == "HEADER":
            headers[auth.key_name] = auth.key_value
        else:
            query.append((auth.key_name, auth.key_value))


def _auth_query(auth: RequestAuth) -> list[tuple[str, str]]:
    if auth.type == "API_KEY" and auth.placement == "QUERY":
        assert auth.key_name is not None and auth.key_value is not None
        return [(auth.key_name, auth.key_value)]
    return []


def _remaining_timeout_seconds(
    timeout_ms: int,
    started: float,
    clock: Callable[[], float],
) -> float:
    remaining = timeout_ms / 1000 - max(0.0, clock() - started)
    if remaining <= 0:
        raise TargetTimeoutError()
    return remaining


def _redirect_location(response: object) -> str | None:
    headers = getattr(response, "headers", None)
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    location = get("location")
    return location if isinstance(location, str) and location else None


def _resolve_redirect_url(current_url: str, location: str) -> str:
    if len(location) > 4000 or "\r" in location or "\n" in location:
        raise ExecutionError(
            "目标 API 返回无效重定向地址",
            error_type="UNSAFE_REDIRECT",
        )
    try:
        target_url = urljoin(current_url, location)
        _validate_target_url(target_url)
    except (ProtocolError, ValueError) as exc:
        raise ExecutionError(
            "目标 API 返回无效重定向地址",
            error_type="UNSAFE_REDIRECT",
        ) from exc
    parsed = urlsplit(target_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, hostname, port


def _redirect_method(method: str, status_code: int) -> tuple[str, bool]:
    if status_code == 303 and method != "HEAD":
        return "GET", True
    if status_code in {301, 302} and method == "POST":
        return "GET", True
    return method, False


def _close_response(response: object) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _without_entity_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in {"content-length", "content-type", "transfer-encoding"}
    }


def _cross_origin_headers(headers: Mapping[str, str]) -> dict[str, str]:
    safe_names = {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "user-agent",
    }
    return {name: value for name, value in headers.items() if name.lower() in safe_names}


def _sensitive_query_names(template: ApiRequestTemplate) -> set[str]:
    names = {
        item.name.lower()
        for item in template.query_params
        if item.enabled and _looks_sensitive_name(item.name)
    }
    if template.auth.type == "API_KEY" and template.auth.placement == "QUERY":
        assert template.auth.key_name is not None
        names.add(template.auth.key_name.lower())
    return names


def _looks_sensitive_name(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    markers = (
        "auth",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "session",
        "cookie",
        "api_key",
        "apikey",
        "access_key",
    )
    return any(marker in normalized for marker in markers)


def _without_named_query(url: str, names: set[str]) -> str:
    if not names:
        return url
    parsed = urlsplit(url)
    query = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name.lower() not in names
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment)
    )


def _with_query_params(url: str, params: Sequence[tuple[str, str]]) -> str:
    if not params:
        return url
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment)
    )


def _validate_target_url(url: str) -> None:
    if url.startswith("{{"):
        return
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ProtocolError("request.url 必须使用 http 或 https")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProtocolError("request.url 端口无效") from exc
    if parsed.hostname is None or port is not None and not 1 <= port <= 65_535:
        raise ProtocolError("request.url 地址无效")
    if parsed.username is not None or parsed.password is not None:
        raise ProtocolError("request.url 不允许携带用户名或密码")
    if "\r" in url or "\n" in url:
        raise ProtocolError("request.url 不能包含换行符")


def _reject_duplicate_enabled(items: Sequence[RequestValue], label: str) -> None:
    names = [item.name.lower() for item in items if item.enabled]
    if len(names) != len(set(names)):
        raise ProtocolError(f"request.{label} 存在重复名称")


def _form_data(content: object) -> list[tuple[str, str]] | dict[str, str]:
    if isinstance(content, Mapping):
        values: list[tuple[str, str]] = []
        for key, value in content.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProtocolError("FORM_URLENCODED content 必须是字符串键值")
            values.append((key, value))
        return values
    if isinstance(content, list):
        values = []
        for item in content:
            if not isinstance(item, Mapping) or set(item) != {"name", "value"}:
                raise ProtocolError("FORM_URLENCODED content 列表项无效")
            name, value = item.get("name"), item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                raise ProtocolError("FORM_URLENCODED content 必须是字符串键值")
            values.append((name, value))
        return values
    raise ProtocolError("FORM_URLENCODED content 必须是对象或键值列表")


def _multipart_data(
    content: object,
) -> tuple[list[tuple[str, tuple[str | None, bytes | str, str | None]]], int]:
    files: list[tuple[str, tuple[str | None, bytes | str, str | None]]] = []
    total_size = 0
    if isinstance(content, Mapping):
        items: list[Mapping[str, Any]] = [
            {"name": key, "value": value, "enabled": True} for key, value in content.items()
        ]
    elif isinstance(content, list):
        items = content
    else:
        raise ProtocolError("MULTIPART content 必须是对象或字段列表")
    if len(items) > 200:
        raise ProtocolError("MULTIPART 字段数量超过限制")
    for item in items:
        if not isinstance(item, Mapping) or set(item) - {
            "name",
            "value",
            "enabled",
            "filename",
            "content_type",
            "encoding",
        }:
            raise ProtocolError("MULTIPART 字段配置无效")
        enabled = item.get("enabled", True)
        name = item.get("name")
        value = item.get("value")
        if type(enabled) is not bool or not isinstance(name, str) or not 1 <= len(name) <= 255:
            raise ProtocolError("MULTIPART 字段名称或 enabled 无效")
        if not enabled:
            continue
        filename = item.get("filename")
        content_type = item.get("content_type")
        encoding = item.get("encoding", "TEXT")
        if filename is None:
            if encoding != "TEXT" or content_type is not None or not isinstance(value, str):
                raise ProtocolError("MULTIPART 文本字段配置无效")
            total_size += len(name.encode()) + len(value.encode())
            if total_size > MAX_REQUEST_BYTES:
                raise ExecutionError("API 请求体超过 2 MiB 限制", error_type="REQUEST_TOO_LARGE")
            files.append((name, (None, value, None)))
            continue
        if (
            not isinstance(filename, str)
            or not 1 <= len(filename) <= 255
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or encoding != "BASE64"
            or not isinstance(value, str)
            or not isinstance(content_type, str)
            or not 1 <= len(content_type) <= 255
            or _has_http_control(content_type)
        ):
            raise ProtocolError("MULTIPART 文件字段配置无效")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ProtocolError("MULTIPART 文件字段不是有效 Base64") from exc
        total_size += len(name.encode()) + len(filename.encode()) + len(decoded)
        if total_size > MAX_REQUEST_BYTES:
            raise ExecutionError("API 请求体超过 2 MiB 限制", error_type="REQUEST_TOO_LARGE")
        files.append((name, (filename, decoded, content_type)))
    return files, total_size


def _check_body_size(content: bytes) -> None:
    if len(content) > MAX_REQUEST_BYTES:
        raise ExecutionError("API 请求体超过 2 MiB 限制", error_type="REQUEST_TOO_LARGE")


def _read_capped_content(response: Any, limit: int) -> tuple[bytes, bool]:
    try:
        content = getattr(response, "content", b"")
    except RuntimeError:
        content = None
    if isinstance(content, bytes):
        return content[:limit], len(content) > limit
    if isinstance(content, bytearray):
        return bytes(content[:limit]), len(content) > limit
    if isinstance(content, str):
        encoded = content.encode()
        return encoded[:limit], len(encoded) > limit
    iterator = getattr(response, "iter_bytes", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in iterator():
            if not isinstance(chunk, bytes):
                continue
            remaining = limit - total
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                return b"".join(chunks), True
            chunks.append(chunk[:remaining])
            total += len(chunk)
        return b"".join(chunks), False
    return b"", False


def _header_indicates_truncation(headers: object, limit: int) -> bool:
    if not isinstance(headers, Mapping):
        return False
    try:
        return int(headers.get("content-length", 0)) > limit
    except (TypeError, ValueError):
        return False


def _response_map(value: object, *, label: str, max_count: int) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > max_count:
        raise ExecutionError(f"{label} 数量无效", error_type="RESPONSE_INVALID")
    result: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str):
            raise ExecutionError(f"{label} 类型无效", error_type="RESPONSE_INVALID")
        if len(name) > MAX_ITEM_NAME or len(item) > MAX_ITEM_VALUE:
            raise ExecutionError(f"{label} 长度无效", error_type="RESPONSE_INVALID")
        result[name] = item
    return result


def _response_cookies(response: Any) -> dict[str, str]:
    try:
        cookies = getattr(response, "cookies", {})
    except RuntimeError:
        cookies = {}
    items = getattr(cookies, "items", None)
    if callable(items):
        try:
            cookies = dict(items())
        except (TypeError, ValueError):
            cookies = {}
    else:
        cookies = {}
    return _response_map(cookies, label="响应 Cookie", max_count=100)


def _decode_body(content: bytes, response: Any) -> str:
    encoding = getattr(response, "encoding", None) or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        return content.decode("utf-8", errors="replace")


def _parse_response_body(value: str) -> tuple[Any | None, str | None]:
    if not value:
        return None, None
    try:
        return json.loads(value), None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, value


def _validate_snapshot_body_size(json_body: Any | None, text: str | None) -> None:
    try:
        encoded = json.dumps(
            {"json_body": json_body, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutionError("目标 API 响应无法安全编码", error_type="RESPONSE_INVALID") from exc
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ExecutionError(
            "目标 API 响应超过 2,000,000 bytes",
            error_type="RESPONSE_TOO_LARGE",
        )
