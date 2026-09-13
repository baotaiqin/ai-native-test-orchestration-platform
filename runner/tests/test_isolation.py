"""工作包 B：执行隔离边界专项测试。

真实 spawn 用例只用本机临时 HTTP 服务与短睡眠子进程，不等待真实 TTL；
逻辑用例使用 InlineTaskProcess 与脚本化假进程。
"""

import json
import pickle
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from runner.executors.api import ApiRequestTemplate
from runner.isolation import (
    RUNNER_RESTART_REQUIRED_ERROR_TYPE,
    InlineTaskProcess,
    IsolatedOutcome,
    SpawnTaskProcess,
    _run_api_task_worker,
)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def local_target() -> Generator[str, None, None]:
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)


def _request_mapping(url: str) -> ApiRequestTemplate:
    return ApiRequestTemplate("GET", url, timeout_ms=5000)


class _SleepingEntry:
    """子进程入口的替身：按秒数睡眠后回传结果（仅用于 terminate 验证）。"""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def __call__(self, conn: object, payload: dict) -> None:
        del payload
        time.sleep(self.seconds)
        conn.send(pickle.dumps(IsolatedOutcome(kind="EXECUTION_ERROR", error_message="slept")))
        conn.close()


class _FakePipe:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeProcess:
    def __init__(self, *, alive: bool) -> None:
        self.alive = alive
        self.started = False
        self.terminate_calls = 0
        self.join_calls: list[float] = []
        self.close_calls = 0

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout if timeout is not None else -1.0)

    @property
    def exitcode(self) -> int | None:
        return None if self.alive else 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeMultiprocessingContext:
    def __init__(self, process: _FakeProcess) -> None:
        self.parent = _FakePipe()
        self.child = _FakePipe()
        self.process = process

    def Pipe(self, *, duplex: bool) -> tuple[_FakePipe, _FakePipe]:
        assert duplex is False
        return self.parent, self.child

    def Process(self, **kwargs: object) -> _FakeProcess:
        assert kwargs["daemon"] is True
        return self.process


def test_inline_task_process_roundtrips_http_outcome(local_target: str) -> None:
    process = InlineTaskProcess(_run_api_task_worker, {"request": _request_mapping(local_target)})
    process.start()
    assert process.poll(0.1) is True
    outcome = process.outcome()
    assert outcome is not None
    assert outcome.kind == "HTTP_RESPONSE"
    assert outcome.result is not None
    assert outcome.result["status_code"] == 200
    assert outcome.result["json_body"] == {"ok": True}
    process.close()


def test_worker_reports_resident_source_drift_before_execution(local_target: str) -> None:
    process = InlineTaskProcess(
        _run_api_task_worker,
        {
            "request": _request_mapping(local_target),
            "runner_source_revision": "outdated-resident-runner",
        },
    )

    process.start()
    outcome = process.outcome()

    assert outcome is not None
    assert outcome.kind == "EXECUTION_ERROR"
    assert outcome.error_type == RUNNER_RESTART_REQUIRED_ERROR_TYPE
    assert outcome.error_message == "Runner 代码已更新，请重启 Runner 后重试"
    process.close()


def test_spawn_task_process_roundtrips_http_outcome(local_target: str) -> None:
    process = SpawnTaskProcess(_run_api_task_worker, {"request": _request_mapping(local_target)})
    process.start()
    deadline = time.monotonic() + 60.0
    while not process.poll(0.2) and time.monotonic() < deadline:
        continue
    outcome = process.outcome()
    assert outcome is not None
    assert outcome.kind == "HTTP_RESPONSE"
    assert outcome.result is not None
    assert outcome.result["status_code"] == 200
    assert outcome.result["json_body"] == {"ok": True}
    process.close()


def test_spawn_task_process_terminate_stops_blocking_child_promptly() -> None:
    process = SpawnTaskProcess(_SleepingEntry(60.0), {"request": {}})
    process.start()
    started = time.monotonic()
    assert process.is_alive()
    process.terminate()
    process.join(timeout=10.0)
    assert not process.is_alive()
    assert time.monotonic() - started < 10.0
    process.close()


def test_spawn_task_process_start_is_idempotent_and_close_is_safe() -> None:
    process = SpawnTaskProcess(_SleepingEntry(0.05), {"request": {}})
    process.start()
    process.start()
    deadline = time.monotonic() + 30.0
    while not process.poll(0.1) and time.monotonic() < deadline:
        continue
    process.close()
    process.close()
    assert process.outcome() is not None


def test_spawn_task_process_close_joins_and_closes_resources_after_normal_completion() -> None:
    fake_process = _FakeProcess(alive=False)
    context = _FakeMultiprocessingContext(fake_process)
    process = SpawnTaskProcess(_SleepingEntry(0.0), {}, mp_context=context)

    process.start()
    process.close()
    process.close()

    assert fake_process.started is True
    assert fake_process.terminate_calls == 0
    assert fake_process.join_calls == [5.0]
    assert fake_process.close_calls == 1
    assert context.child.close_calls == 1
    assert context.parent.close_calls == 1


def test_spawn_task_process_close_terminates_joins_and_closes_resources() -> None:
    fake_process = _FakeProcess(alive=True)
    context = _FakeMultiprocessingContext(fake_process)
    process = SpawnTaskProcess(_SleepingEntry(0.0), {}, mp_context=context)

    process.start()
    process.close()

    assert fake_process.terminate_calls == 1
    assert fake_process.join_calls == [5.0]
    assert fake_process.close_calls == 1
    assert context.child.close_calls == 1
    assert context.parent.close_calls == 1


def test_inline_task_process_terminate_is_noop() -> None:
    process = InlineTaskProcess(_SleepingEntry(0.0), {"request": {}})
    process.start()
    process.terminate()
    process.join()
    process.close()
