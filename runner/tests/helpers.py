from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from runner.models import HttpResponse


class FakeTransport:
    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: Any,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "json": dict(json_body),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("fake transport 没有更多响应")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: Any,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "json": None,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("fake transport 没有更多响应")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeProbe:
    def hostname(self) -> str:
        return "host-01"

    def ip_address(self, hostname: str) -> str | None:
        return "10.0.0.10"

    def os(self) -> str | None:
        return "Windows Test"

    def cpu(self) -> str | None:
        return "CPU Test"

    def ram(self) -> str | None:
        return "16 GB"

    def disk(self) -> str | None:
        return "100 GB"

    def python(self) -> str | None:
        return "3.12.0"

    def chrome(self) -> str | None:
        return "Chrome Test"

    def playwright(self) -> str | None:
        return "1.50.0"

    def java(self) -> str | None:
        return "Java Test"

    def jmeter(self) -> str | None:
        return "JMeter Test"


def snapshot() -> dict[str, object]:
    from runner.snapshot import collect_snapshot

    return collect_snapshot(
        "runner-test",
        tags=("Windows", "api", "api"),
        api_slots=1,
        web_slots=0,
        performance_slots=0,
        probe=FakeProbe(),
    )
