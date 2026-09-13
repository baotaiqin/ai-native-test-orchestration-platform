from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import ProtocolError
from runner.executors.api import ApiExecutor, ApiRequestTemplate, RetryPolicy
from runner.models import (
    ExecutionCancellationResult,
    ExecutionCompleteResult,
    RunnerIdentity,
)


class _CountingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CountingHandler)
        self.counts: dict[str, int] = {}
        self.lock = Lock()

    def increment(self, path: str) -> int:
        with self.lock:
            count = self.counts.get(path, 0) + 1
            self.counts[path] = count
            return count

    def count(self, path: str) -> int:
        with self.lock:
            return self.counts.get(path, 0)


class _CountingHandler(BaseHTTPRequestHandler):
    server: _CountingServer

    def do_GET(self) -> None:
        count = self.server.increment(self.path)
        if self.path == "/network-error":
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        if self.path == "/timeout":
            time.sleep(0.25)
            self._send(200, b"late")
            return
        if self.path == "/flaky":
            self._send(503 if count == 1 else 200, b"ok")
            return
        if self.path == "/assertion-mismatch":
            self._send(200, b'{"actual":"not-expected"}')
            return
        if self.path == "/budget":
            self._send(503, b"retryable")
            return
        self._send(404, b"not found")

    def _send(self, status: int, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def counting_target() -> Iterator[tuple[_CountingServer, str]]:
    server = _CountingServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _Channel:
    def __init__(self) -> None:
        self.acks: list[object] = []
        self.rejections: list[object] = []

    def basic_ack(self, *, delivery_tag: object) -> None:
        self.acks.append(delivery_tag)

    def basic_reject(self, *, delivery_tag: object, requeue: bool) -> None:
        self.rejections.append((delivery_tag, requeue))


def _control(*, cancelled: bool = False) -> ExecutionCancellationResult:
    return ExecutionCancellationResult(
        1,
        "run-v1-retry",
        "message-v1-retry",
        71,
        "CANCELLING" if cancelled else "RUNNING",
        cancelled,
        False,
    )


class _ControlClient:
    def __init__(
        self,
        controls: list[ExecutionCancellationResult] | None = None,
    ) -> None:
        self.controls = list(controls or [])
        self.completions: list[dict[str, object]] = []

    def get_execution_cancellation(
        self,
        _identity: RunnerIdentity,
        _run_id: str,
        _message_id: str,
        _case_run_id: int,
    ) -> ExecutionCancellationResult:
        return self.controls.pop(0) if self.controls else _control()

    def execution_complete(
        self,
        _identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        **kwargs: object,
    ) -> ExecutionCompleteResult:
        self.completions.append(dict(kwargs))
        outcome = str(kwargs["outcome"])
        if outcome == "TIMEOUT":
            status = "TIMEOUT"
        elif outcome == "CANCELLED":
            status = "CANCELLED"
        else:
            status = "SUCCESS"
        return ExecutionCompleteResult(
            1,
            run_id,
            message_id,
            "runner-v1-retry",
            case_run_id,
            outcome,
            status,
            status,
            [],
            kwargs.get("error_type"),  # type: ignore[arg-type]
            kwargs.get("error_message"),  # type: ignore[arg-type]
            datetime.now(UTC),
            False,
            int(kwargs.get("retry_count", 0)),
        )


def _request(
    base_url: str,
    path: str,
    retry_on: str,
    *,
    timeout_ms: int = 1_000,
    backoff_ms: int = 0,
) -> ApiRequestTemplate:
    return ApiRequestTemplate(
        "GET",
        base_url + path,
        timeout_ms=timeout_ms,
        retry_policy=RetryPolicy(1, backoff_ms, (retry_on,)),
    )


def _consumer(
    executor: ApiExecutor,
    client: _ControlClient | None = None,
) -> tuple[RunnerTaskConsumer, _Channel, _ControlClient]:
    channel = _Channel()
    active_client = client or _ControlClient()
    consumer = RunnerTaskConsumer(
        channel,
        active_client,  # type: ignore[arg-type]
        RunnerIdentity("runner-v1-retry", "credential-value-123456789"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
        executor=executor,
        sleeper=time.sleep,
    )
    return consumer, channel, active_client


def _execute(
    consumer: RunnerTaskConsumer,
    request: ApiRequestTemplate,
    *,
    deadline: float | None = None,
) -> tuple[dict[str, object] | None, ConsumeOutcome | None]:
    return consumer._execute_with_retries(
        71,
        "run-v1-retry",
        "message-v1-retry",
        71,
        request,
        request.retry_policy,
        deadline=deadline,
    )


@pytest.mark.parametrize(
    ("path", "retry_on", "timeout_ms"),
    [
        ("/network-error", "TARGET_NETWORK_ERROR", 1_000),
        ("/timeout", "TARGET_TIMEOUT", 100),
    ],
)
def test_real_target_persistent_failure_stops_after_one_retry(
    counting_target: tuple[_CountingServer, str],
    path: str,
    retry_on: str,
    timeout_ms: int,
) -> None:
    server, base_url = counting_target
    with ApiExecutor() as executor:
        consumer, _channel, _client = _consumer(executor)
        completion, failure = _execute(
            consumer,
            _request(base_url, path, retry_on, timeout_ms=timeout_ms),
        )

    assert failure is None
    assert completion is not None
    assert completion["retry_count"] == 1
    assert server.count(path) == 2


def test_real_target_second_attempt_success_reports_actual_retry_count(
    counting_target: tuple[_CountingServer, str],
) -> None:
    server, base_url = counting_target
    with ApiExecutor() as executor:
        consumer, _channel, _client = _consumer(executor)
        completion, failure = _execute(
            consumer,
            _request(base_url, "/flaky", "HTTP_5XX", backoff_ms=10),
        )

    assert failure is None
    assert completion is not None
    assert completion["outcome"] == "HTTP_RESPONSE"
    assert completion["response"]["status_code"] == 200  # type: ignore[index]
    assert completion["retry_count"] == 1
    assert server.count("/flaky") == 2


def test_real_success_response_for_backend_assertion_is_not_retried(
    counting_target: tuple[_CountingServer, str],
) -> None:
    server, base_url = counting_target
    request = _request(base_url, "/assertion-mismatch", "HTTP_5XX")
    with ApiExecutor() as executor:
        consumer, _channel, _client = _consumer(executor)
        completion, failure = _execute(consumer, request)

    assert failure is None
    assert completion is not None
    assert completion["outcome"] == "HTTP_RESPONSE"
    assert completion["retry_count"] == 0
    assert server.count("/assertion-mismatch") == 1


@pytest.mark.parametrize(
    "controls",
    [
        [_control(cancelled=True)],
        [_control(), _control(cancelled=True)],
    ],
)
def test_real_target_cancel_before_or_during_backoff_prevents_second_attempt(
    counting_target: tuple[_CountingServer, str],
    controls: list[ExecutionCancellationResult],
) -> None:
    server, base_url = counting_target
    client = _ControlClient(controls)
    with ApiExecutor() as executor:
        consumer, channel, active_client = _consumer(executor, client)
        completion, failure = _execute(
            consumer,
            _request(
                base_url,
                "/network-error",
                "TARGET_NETWORK_ERROR",
                backoff_ms=10,
            ),
        )

    assert completion is None
    assert failure is ConsumeOutcome.ACKED
    assert server.count("/network-error") == 1
    assert channel.acks == [71]
    assert active_client.completions[-1]["outcome"] == "CANCELLED"
    assert active_client.completions[-1]["retry_count"] == 0


def test_real_target_run_budget_exhaustion_prevents_second_attempt(
    counting_target: tuple[_CountingServer, str],
) -> None:
    server, base_url = counting_target
    client = _ControlClient()
    with ApiExecutor() as executor:
        consumer, channel, active_client = _consumer(executor, client)
        completion, failure = _execute(
            consumer,
            _request(base_url, "/budget", "HTTP_5XX", backoff_ms=500),
            deadline=time.time() + 0.2,
        )

    assert completion is None
    assert failure is ConsumeOutcome.ACKED
    assert server.count("/budget") == 1
    assert channel.acks == [71]
    assert active_client.completions[-1]["outcome"] == "TIMEOUT"
    assert active_client.completions[-1]["retry_count"] == 0


@pytest.mark.parametrize("max_retries", [True, -1, 2, 3])
def test_invalid_v1_policy_is_rejected_before_real_target_request(
    counting_target: tuple[_CountingServer, str],
    max_retries: object,
) -> None:
    server, base_url = counting_target
    request = {
        "method": "GET",
        "url": base_url + "/flaky",
        "retry_policy": {
            "max_retries": max_retries,
            "backoff_ms": 0,
            "retry_on": ["HTTP_5XX"],
        },
    }

    with ApiExecutor() as executor, pytest.raises(
        ProtocolError, match="仅允许 0 或 1"
    ):
        executor.execute(request)

    assert server.count("/flaky") == 0
