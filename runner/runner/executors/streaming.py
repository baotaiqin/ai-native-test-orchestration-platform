"""Bounded SSE/NDJSON execution with TTFT and streaming quality metrics."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from runner.errors import ExecutionError, TargetExecutionError, TargetTimeoutError
from runner.executors.api import ApiExecutor, ApiRequestTemplate

MAX_STREAM_BYTES = 10_000_000


@dataclass(frozen=True)
class StreamingExecutionResult:
    status_code: int
    duration_ms: int
    ttft_ms: int | None
    stream_duration_ms: int
    bytes_received: int
    input_tokens: int | None
    output_tokens: int | None
    chunk_count: int
    max_chunk_gap_ms: int
    completed: bool


class StreamingApiExecutor:
    def __init__(
        self,
        api_executor: ApiExecutor,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_executor = api_executor
        self._clock = clock

    def execute(
        self,
        request: ApiRequestTemplate,
        config: Mapping[str, Any],
    ) -> StreamingExecutionResult:
        protocol = config.get("protocol", "SSE")
        completion_marker = config.get("completion_marker", "[DONE]")
        require_marker = config.get("require_completion_marker", True)
        if (
            protocol not in {"SSE", "NDJSON"}
            or not isinstance(completion_marker, str)
            or not 1 <= len(completion_marker) <= 200
            or type(require_marker) is not bool
        ):
            raise ExecutionError("流式执行配置无效", error_type="STREAM_CONFIG_INVALID")
        started = self._clock()
        response: Any | None = None
        try:
            response = self._api_executor._request_with_redirects(request, started)  # noqa: SLF001
            status_code = getattr(response, "status_code", None)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise ExecutionError("流式响应状态码无效", error_type="STREAM_RESPONSE_INVALID")
            first_at: float | None = None
            previous_at: float | None = None
            max_gap_ms = 0
            byte_count = 0
            chunk_count = 0
            marker_seen = False
            input_tokens: int | None = None
            output_tokens: int | None = None
            for raw_line in response.iter_lines():
                now = self._clock()
                encoded = raw_line.encode("utf-8") if isinstance(raw_line, str) else bytes(raw_line)
                byte_count += len(encoded) + 1
                if byte_count > MAX_STREAM_BYTES:
                    raise ExecutionError(
                        "流式响应超过 10MB", error_type="STREAM_RESPONSE_TOO_LARGE"
                    )
                line = (
                    raw_line.decode("utf-8", errors="replace")
                    if isinstance(raw_line, bytes)
                    else raw_line
                )
                payload = line
                if protocol == "SSE":
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                elif not line.strip():
                    continue
                if first_at is None:
                    first_at = now
                if previous_at is not None:
                    max_gap_ms = max(max_gap_ms, max(0, int((now - previous_at) * 1000)))
                previous_at = now
                chunk_count += 1
                if payload.strip() == completion_marker:
                    marker_seen = True
                    continue
                parsed = _json_object(payload)
                if parsed is not None:
                    found_input, found_output = _usage_tokens(parsed)
                    input_tokens = found_input if found_input is not None else input_tokens
                    output_tokens = found_output if found_output is not None else output_tokens
            ended = self._clock()
            duration_ms = max(0, int((ended - started) * 1000))
            ttft_ms = max(0, int((first_at - started) * 1000)) if first_at is not None else None
            return StreamingExecutionResult(
                status_code=status_code,
                duration_ms=duration_ms,
                ttft_ms=ttft_ms,
                stream_duration_ms=(
                    max(0, int((ended - first_at) * 1000)) if first_at is not None else 0
                ),
                bytes_received=byte_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                chunk_count=chunk_count,
                max_chunk_gap_ms=max_gap_ms,
                completed=marker_seen or not require_marker,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise TargetTimeoutError() from exc
        except httpx.RequestError as exc:
            raise TargetExecutionError(
                "流式目标网络请求失败", error_type="TARGET_NETWORK_ERROR"
            ) from exc
        except OSError as exc:
            raise TargetExecutionError(
                "流式目标网络请求失败", error_type="TARGET_NETWORK_ERROR"
            ) from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()


def estimate_request_bytes(request: ApiRequestTemplate) -> int:
    total = len(request.method.encode()) + len(request.url.encode())
    total += sum(
        len(item.name.encode()) + len(item.value.encode())
        for item in request.headers
        if item.enabled
    )
    total += sum(
        len(item.name.encode()) + len(item.value.encode())
        for item in request.query_params
        if item.enabled
    )
    total += sum(
        len(item.name.encode()) + len(item.value.encode())
        for item in request.cookies
        if item.enabled
    )
    if request.body.content is not None:
        if isinstance(request.body.content, str):
            total += len(request.body.content.encode())
        else:
            total += len(json.dumps(request.body.content, ensure_ascii=False, default=str).encode())
    return total


def _json_object(value: str) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _usage_tokens(value: Mapping[str, Any]) -> tuple[int | None, int | None]:
    candidates = [value]
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        candidates.insert(0, usage)
    input_tokens: int | None = None
    output_tokens: int | None = None
    for candidate in candidates:
        for key in ("input_tokens", "prompt_tokens"):
            token = candidate.get(key)
            if type(token) is int and token >= 0:
                input_tokens = token
        for key in ("output_tokens", "completion_tokens"):
            token = candidate.get(key)
            if type(token) is int and token >= 0:
                output_tokens = token
    return input_tokens, output_tokens
