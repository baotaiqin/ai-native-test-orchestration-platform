"""HTTP 传输抽象与 httpx 实现。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from runner.errors import TransportError
from runner.models import HttpResponse


@dataclass(frozen=True)
class TimeoutSettings:
    connect: float = 5.0
    read: float = 15.0

    def as_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect,
            read=self.read,
            write=self.read,
            pool=self.connect,
        )


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse: ...

    def post(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse: ...

    def post_multipart(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        file_path: str,
        file_name: str,
        mime: str,
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse: ...


class HttpxTransport:
    """只提供本任务需要的 POST，不暴露其他后端接口。"""

    def __init__(self) -> None:
        self._client = httpx.Client(follow_redirects=False)

    def post(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse:
        return self._request(
            "POST",
            url,
            headers=headers,
            timeout=timeout,
            json_body=json_body,
        )

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse:
        return self._request("GET", url, headers=headers, timeout=timeout)

    def post_multipart(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        file_path: str,
        file_name: str,
        mime: str,
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
    ) -> HttpResponse:
        try:
            with open(file_path, "rb") as handle:
                response = self._client.request(
                    "POST",
                    url,
                    data=dict(fields),
                    files={"file": (file_name, handle, mime)},
                    headers=dict(headers),
                    timeout=timeout.as_httpx_timeout(),
                )
        except OSError as exc:
            raise TransportError("Runner Evidence 文件读取失败", transient=False) from exc
        except httpx.RequestError as exc:
            raise TransportError("Runner HTTP 网络请求失败", transient=True) from exc
        return self._response(response)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: TimeoutSettings,
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        try:
            response = self._client.request(
                method,
                url,
                json=dict(json_body) if json_body is not None else None,
                headers=dict(headers),
                timeout=timeout.as_httpx_timeout(),
            )
        except httpx.RequestError as exc:
            raise TransportError("Runner HTTP 网络请求失败", transient=True) from exc
        return self._response(response)

    @staticmethod
    def _response(response: httpx.Response) -> HttpResponse:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:1024]
        return HttpResponse(status_code=response.status_code, body=body)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpxTransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
