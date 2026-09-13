"""可终止的执行隔离边界。

API_CASE 的目标 HTTP 请求在独立子进程（multiprocessing spawn）中执行：
Worker 主线程轮询子进程结果，同时检查 Force Stop 与 Run 总超时，必要时
terminate 子进程，使阻塞中的外部 HTTP/SQL/Script 不再无限占用 Worker。
spawn 参数通过继承管道传输，不进入进程命令行；子进程不输出请求、响应
正文或凭据。Inline 模式仅用于调试与单元测试，无法在阻塞时被终止。
"""

from __future__ import annotations

import multiprocessing
import os
import pickle
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any, Protocol

from runner.api_pipeline import (
    ApiCasePipelineExecutor,
    ApiPipelineCleanupResult,
    ApiPipelineCleanupTask,
    ApiPipelineExecutionResult,
)
from runner.errors import ExecutionError, RunnerError, TargetExecutionError, TargetTimeoutError
from runner.executors.api import ApiExecutor
from runner.executors.performance import PerformanceExecutor
from runner.executors.web import WebExecutor
from runner.scenario import ScenarioCleanupTask, ScenarioExecutor

# 无结果时 Worker 轮询管道的步长；Force Stop/总超时检查按轮次节流。
PIPE_POLL_SECONDS = 0.2

FORCE_STOPPED_ERROR_TYPE = "FORCE_STOP_REQUESTED"
FORCE_STOPPED_ERROR_MESSAGE = "执行已被强制停止，Cleanup 可能未完成"
TOTAL_TIMEOUT_ERROR_TYPE = "TOTAL_TIMEOUT"
TOTAL_TIMEOUT_ERROR_MESSAGE = "Run 总超时已终止执行"
RUNNER_RESTART_REQUIRED_ERROR_TYPE = "RUNNER_RESTART_REQUIRED"
RUNNER_RESTART_REQUIRED_ERROR_MESSAGE = "Runner 代码已更新，请重启 Runner 后重试"


def _runner_source_revision() -> str:
    """Fingerprint worker code so Windows spawn never mixes two live revisions."""

    package_root = Path(__file__).resolve().parent
    digest = sha256()
    try:
        paths = sorted(package_root.rglob("*.py"), key=lambda item: item.as_posix())
        for path in paths:
            digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError:
        return "unavailable"
    return digest.hexdigest()


_RUNNER_SOURCE_REVISION = _runner_source_revision()


class _RunnerRestartRequiredError(RuntimeError):
    """The resident parent and newly spawned child loaded different source."""


def _ensure_runner_source_revision(payload: Mapping[str, Any]) -> None:
    expected = payload.get("runner_source_revision")
    if isinstance(expected, str) and expected != _RUNNER_SOURCE_REVISION:
        raise _RunnerRestartRequiredError


@dataclass(frozen=True, repr=False)
class IsolatedOutcome:
    """子进程回传的脱敏结果；kind 决定消费者如何映射为 completion。"""

    # HTTP_RESPONSE | SCENARIO_RESULT | TARGET_TIMEOUT | TARGET_NETWORK_ERROR | EXECUTION_ERROR
    kind: str
    result: Mapping[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __repr__(self) -> str:
        result_keys = tuple(sorted(self.result)) if isinstance(self.result, Mapping) else ()
        return (
            "IsolatedOutcome("
            f"kind={self.kind!r}, result_keys={result_keys!r}, "
            f"error_type={self.error_type!r}, "
            f"error_message={'[REDACTED]' if self.error_message else None!r})"
        )


class TaskProcess(Protocol):
    def start(self) -> None: ...

    def poll(self, timeout_seconds: float) -> bool: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def request_cancel(self) -> None: ...

    def request_stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    @property
    def exitcode(self) -> int | None: ...

    def outcome(self) -> IsolatedOutcome | None: ...

    def close(self) -> None: ...


def _run_api_task_worker(conn: Any, payload: Mapping[str, Any]) -> None:
    """子进程入口：执行目标 HTTP 并回传脱敏结果，绝不上抛异常。"""

    executor = ApiExecutor()
    try:
        _ensure_runner_source_revision(payload)
        target = payload["request"]
        if isinstance(target, ApiPipelineCleanupTask):
            result = ApiCasePipelineExecutor.execute_cleanup_task(target, api_executor=executor)
        else:
            result = (
                ApiCasePipelineExecutor(target, api_executor=executor).execute()
                if getattr(target, "has_pipeline", False)
                else executor.execute(target)
            )
        outcome = IsolatedOutcome(
            kind=(
                "API_CLEANUP_RESULT"
                if isinstance(result, ApiPipelineCleanupResult)
                else "API_PIPELINE_RESULT"
                if isinstance(result, ApiPipelineExecutionResult)
                else "HTTP_RESPONSE"
            ),
            result=(
                result.to_worker_wire()
                if isinstance(result, ApiPipelineExecutionResult)
                else result.to_wire()
            ),
        )
    except TargetTimeoutError as exc:
        outcome = IsolatedOutcome(
            kind="TARGET_TIMEOUT",
            result={
                "action_traces": list(getattr(exc, "action_traces", ())),
                "retry_count": getattr(exc, "retry_count", 0),
            },
            error_type="TARGET_TIMEOUT",
            error_message="目标执行超时",
        )
    except TargetExecutionError as exc:
        outcome = IsolatedOutcome(
            kind="TARGET_NETWORK_ERROR",
            result={
                "action_traces": list(getattr(exc, "action_traces", ())),
                "retry_count": getattr(exc, "retry_count", 0),
            },
            error_type="TARGET_NETWORK_ERROR",
            error_message="目标 API 网络请求失败",
        )
    except ExecutionError as exc:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            result={
                "action_traces": list(getattr(exc, "action_traces", ())),
                "retry_count": getattr(exc, "retry_count", 0),
            },
            error_type=exc.error_type or "EXECUTOR_ERROR",
            error_message="API 执行失败",
        )
    except _RunnerRestartRequiredError:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type=RUNNER_RESTART_REQUIRED_ERROR_TYPE,
            error_message=RUNNER_RESTART_REQUIRED_ERROR_MESSAGE,
        )
    except BaseException:  # noqa: BLE001 - 子进程终边界，不输出 traceback 或异常文本
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type="EXECUTOR_ERROR",
            error_message="隔离执行异常终止",
        )
    finally:
        executor.close()
    with suppress(BrokenPipeError, OSError):
        conn.send(pickle.dumps(outcome))
    with suppress(OSError):
        conn.close()


def _run_scenario_task_worker(conn: Any, payload: Mapping[str, Any]) -> None:
    """Spawn entry for the bounded C2.1 Scenario control-flow runtime."""

    try:
        _ensure_runner_source_revision(payload)
        target = payload["plan"]
        if isinstance(target, ScenarioCleanupTask):
            result = ScenarioExecutor.execute_cleanup_task(target)
            outcome = IsolatedOutcome(
                kind="SCENARIO_CLEANUP_RESULT", result=result.to_worker_wire()
            )
        else:
            result = ScenarioExecutor(target, stop_event=payload.get("cancel_event")).execute()
            outcome = IsolatedOutcome(kind="SCENARIO_RESULT", result=result.to_worker_wire())
    except _RunnerRestartRequiredError:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type=RUNNER_RESTART_REQUIRED_ERROR_TYPE,
            error_message=RUNNER_RESTART_REQUIRED_ERROR_MESSAGE,
        )
    except BaseException:  # noqa: BLE001 - process boundary must never leak details
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type="SCENARIO_EXECUTOR_ERROR",
            error_message="Scenario 隔离执行异常终止",
        )
    with suppress(BrokenPipeError, OSError):
        conn.send(pickle.dumps(outcome))
    with suppress(OSError):
        conn.close()


def _run_performance_task_worker(conn: Any, payload: Mapping[str, Any]) -> None:
    """Run one complete HTTP load in a cancellable child process."""

    executor = ApiExecutor()
    try:
        _ensure_runner_source_revision(payload)
        cancel_event = payload.get("cancel_event")
        result = PerformanceExecutor(executor).execute(
            payload["request"],
            concurrency=payload["concurrency"],
            iterations=payload["iterations"],
            warmup_iterations=payload["warmup_iterations"],
            load_mode=payload["load_mode"],
            target_rps=payload.get("target_rps"),
            duration_seconds=payload.get("duration_seconds"),
            step_stages=payload.get("step_stages", ()),
            target_type=payload.get("target_type", "API_CASE"),
            engine=payload.get("engine", "PYTHON_HTTP"),
            target_plan=payload.get("target_plan"),
            cleanup_mode=payload.get("cleanup_mode", "NONE"),
            stream_config=payload.get("stream_config", {}),
            request_timeout_ms=payload["request_timeout_ms"],
            total_timeout_ms=payload["total_timeout_ms"],
            should_cancel=(
                (lambda: bool(cancel_event.is_set())) if cancel_event is not None else None
            ),
        )
        outcome = IsolatedOutcome(kind="PERFORMANCE_RESULT", result=result.to_worker_wire())
    except ExecutionError as exc:
        outcome = IsolatedOutcome(
            kind="CANCELLED" if exc.error_type == "CANCEL_REQUESTED" else "EXECUTION_ERROR",
            error_type=exc.error_type or "PERFORMANCE_EXECUTOR_ERROR",
            error_message=(
                "Runner 检测到取消请求" if exc.error_type == "CANCEL_REQUESTED" else "性能执行失败"
            ),
        )
    except _RunnerRestartRequiredError:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type=RUNNER_RESTART_REQUIRED_ERROR_TYPE,
            error_message=RUNNER_RESTART_REQUIRED_ERROR_MESSAGE,
        )
    except BaseException:  # noqa: BLE001 - child boundary must not leak target details
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type="PERFORMANCE_EXECUTOR_ERROR",
            error_message="性能隔离执行异常终止",
        )
    finally:
        executor.close()
    with suppress(BrokenPipeError, OSError):
        conn.send(pickle.dumps(outcome))
    with suppress(OSError):
        conn.close()


def _run_web_task_worker(conn: Any, payload: Mapping[str, Any]) -> None:
    """Spawn entry for one Web_CASE; browser resources never live in Worker."""

    try:
        _ensure_runner_source_revision(payload)
        result = WebExecutor(evidence_dir=payload.get("evidence_dir")).execute(
            payload["plan"],
            stop_event=payload.get("cancel_event"),
        )
        outcome = IsolatedOutcome(kind="WEB_RESULT", result=result.to_wire())
    except _RunnerRestartRequiredError:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type=RUNNER_RESTART_REQUIRED_ERROR_TYPE,
            error_message=RUNNER_RESTART_REQUIRED_ERROR_MESSAGE,
        )
    except BaseException:  # noqa: BLE001 - process boundary must not leak browser details
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type="WEB_EXECUTOR_ERROR",
            error_message="Web 隔离执行异常终止",
        )
    with suppress(BrokenPipeError, OSError):
        conn.send(pickle.dumps(outcome))
    with suppress(OSError):
        conn.close()


def _run_web_recording_task_worker(conn: Any, payload: Mapping[str, Any]) -> None:
    """Spawn entry for headed Chrome recording; all browser details stay in child memory."""

    try:
        _ensure_runner_source_revision(payload)
        from runner.executors.web_recording import WebRecordingExecutor

        result = WebRecordingExecutor().execute(
            payload["plan"],
            cancel_event=payload.get("cancel_event"),
            stop_event=payload.get("stop_event"),
        )
        outcome = IsolatedOutcome(kind="WEB_RECORDING_RESULT", result=result.to_wire())
    except _RunnerRestartRequiredError:
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type=RUNNER_RESTART_REQUIRED_ERROR_TYPE,
            error_message=RUNNER_RESTART_REQUIRED_ERROR_MESSAGE,
        )
    except BaseException:  # noqa: BLE001 - child boundary is deliberately opaque
        outcome = IsolatedOutcome(
            kind="EXECUTION_ERROR",
            error_type="WEB_RECORDING_EXECUTOR_ERROR",
            error_message="Web 录制隔离执行异常终止",
        )
    with suppress(BrokenPipeError, OSError):
        conn.send(pickle.dumps(outcome))
    with suppress(OSError):
        conn.close()


class SpawnTaskProcess:
    """multiprocessing spawn 子进程；terminate 可立即终止阻塞中的外部调用。"""

    def __init__(
        self,
        entry: Callable[[Any, Mapping[str, Any]], None],
        payload: Mapping[str, Any],
        *,
        mp_context: Any | None = None,
        cancel_event: Any | None = None,
        stop_event: Any | None = None,
    ) -> None:
        self._entry = entry
        self._payload = payload
        self._context = mp_context or multiprocessing.get_context("spawn")
        self._cancel_event = cancel_event
        self._stop_event = stop_event
        self._process: Any | None = None
        self._parent_conn: Any | None = None
        self._outcome: IsolatedOutcome | None = None
        self._closed = False

    def start(self) -> None:
        if self._process is not None:
            return
        self._parent_conn, child_conn = self._context.Pipe(duplex=False)
        try:
            self._process = self._context.Process(
                target=self._entry, args=(child_conn, self._payload), daemon=True
            )
            self._process.start()
        finally:
            # Process.start 已将可传递的端点交给子进程；父进程不再保留 child_conn。
            with suppress(OSError):
                child_conn.close()

    def poll(self, timeout_seconds: float) -> bool:
        if self._outcome is not None:
            return True
        if self._process is None or self._parent_conn is None:
            raise RunnerError("隔离进程尚未启动")
        with suppress(EOFError, OSError):
            if self._parent_conn.poll(timeout_seconds):
                payload = self._parent_conn.recv()
                self._outcome = pickle.loads(payload)
                return True
        return self.exitcode is not None

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def terminate(self) -> None:
        if self._process is not None and self._process.is_alive():
            if os.name == "nt" and _terminate_windows_process_tree(self._process):
                return
            self._process.terminate()

    def request_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._process is not None:
            self._process.join(timeout)

    @property
    def exitcode(self) -> int | None:
        return self._process.exitcode if self._process is not None else None

    def outcome(self) -> IsolatedOutcome | None:
        return self._outcome

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            self.terminate()
            # 正常结束也必须 join，避免长期 Worker 累积未回收的进程句柄。
            process.join(timeout=5.0)
            if process.is_alive():
                # Windows TerminateProcess 通常已足够；若首次 terminate 未收口，
                # 使用 kill（可用时）再进行一次有界 join，仍不无限等待。
                if os.name == "nt":
                    self.terminate()
                else:
                    kill = getattr(process, "kill", None)
                    if callable(kill):
                        kill()
                    else:
                        process.terminate()
                process.join(timeout=5.0)
            if not process.is_alive():
                close_process = getattr(process, "close", None)
                if callable(close_process):
                    with suppress(OSError, ValueError):
                        close_process()
        with suppress(OSError):
            if self._parent_conn is not None:
                self._parent_conn.close()


def _terminate_windows_process_tree(process: Any) -> bool:
    """Force-stop only the live spawn root PID and descendants owned by this task."""

    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


class InlineTaskProcess:
    """同进程执行：仅用于调试与单元测试，阻塞期间无法被 terminate 打断。"""

    def __init__(
        self,
        entry: Callable[[Any, Mapping[str, Any]], None],
        payload: Mapping[str, Any],
        *,
        cancel_event: ThreadEvent | None = None,
        stop_event: ThreadEvent | None = None,
    ) -> None:
        self._entry = entry
        self._payload = payload
        self._cancel_event = cancel_event or ThreadEvent()
        self._stop_event = stop_event or ThreadEvent()
        self._conn: _InlineConnection | None = None
        self._started = False
        self._closed = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._conn = _InlineConnection()
        payload = dict(self._payload)
        payload.setdefault("cancel_event", self._cancel_event)
        self._entry(self._conn, payload)

    def poll(self, timeout_seconds: float) -> bool:
        return self._conn is not None

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        return None

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def request_stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        return None

    @property
    def exitcode(self) -> int | None:
        return 0 if self._conn is not None else None

    def outcome(self) -> IsolatedOutcome | None:
        if self._conn is None:
            return None
        return pickle.loads(self._conn.payload) if self._conn.payload is not None else None

    def close(self) -> None:
        self._closed = True


class _InlineConnection:
    def __init__(self) -> None:
        self.payload: bytes | None = None

    def send(self, payload: bytes) -> None:
        self.payload = payload

    def close(self) -> None:
        return None


def spawn_api_task_process(request: object) -> TaskProcess:
    """生产隔离工厂：为一次 API_CASE 目标请求创建 spawn 子进程。"""

    return SpawnTaskProcess(
        _run_api_task_worker,
        {"request": request, "runner_source_revision": _RUNNER_SOURCE_REVISION},
    )


def spawn_scenario_task_process(plan: object) -> TaskProcess:
    """Create one spawn-isolated process for an entire Scenario run."""

    context = multiprocessing.get_context("spawn")
    cancel_event = context.Event()
    return SpawnTaskProcess(
        _run_scenario_task_worker,
        {
            "plan": plan,
            "cancel_event": cancel_event,
            "runner_source_revision": _RUNNER_SOURCE_REVISION,
        },
        mp_context=context,
        cancel_event=cancel_event,
    )


def spawn_scenario_cleanup_task_process(task: object) -> TaskProcess:
    """Create a second isolated process for cleanup after Backend AI evaluation."""

    return SpawnTaskProcess(
        _run_scenario_task_worker,
        {"plan": task, "runner_source_revision": _RUNNER_SOURCE_REVISION},
    )


def spawn_performance_task_process(plan: Any) -> TaskProcess:
    """Create one spawn-isolated process for a complete performance run."""

    context = multiprocessing.get_context("spawn")
    cancel_event = context.Event()
    return SpawnTaskProcess(
        _run_performance_task_worker,
        {
            "request": plan.request,
            "concurrency": plan.concurrency,
            "iterations": plan.iterations,
            "warmup_iterations": plan.warmup_iterations,
            "load_mode": plan.load_mode,
            "target_rps": plan.target_rps,
            "duration_seconds": plan.duration_seconds,
            "step_stages": plan.step_stages,
            "target_type": plan.target_type,
            "engine": plan.engine,
            "target_plan": plan.target_plan,
            "cleanup_mode": plan.cleanup_mode,
            "stream_config": dict(plan.stream_config),
            "request_timeout_ms": plan.request_timeout_ms,
            "total_timeout_ms": plan.total_timeout_ms,
            "cancel_event": cancel_event,
            "runner_source_revision": _RUNNER_SOURCE_REVISION,
        },
        mp_context=context,
        cancel_event=cancel_event,
    )


def spawn_web_task_process(plan: object, evidence_dir: object | None = None) -> TaskProcess:
    """Create a spawn-isolated process for a complete Web_CASE execution."""

    context = multiprocessing.get_context("spawn")
    cancel_event = context.Event()
    return SpawnTaskProcess(
        _run_web_task_worker,
        {
            "plan": plan,
            "cancel_event": cancel_event,
            "evidence_dir": evidence_dir,
            "runner_source_revision": _RUNNER_SOURCE_REVISION,
        },
        mp_context=context,
        cancel_event=cancel_event,
    )


def spawn_web_recording_task_process(plan: object) -> TaskProcess:
    """Create one spawn-isolated headed Chrome recording process."""

    context = multiprocessing.get_context("spawn")
    cancel_event = context.Event()
    stop_event = context.Event()
    return SpawnTaskProcess(
        _run_web_recording_task_worker,
        {
            "plan": plan,
            "cancel_event": cancel_event,
            "stop_event": stop_event,
            "runner_source_revision": _RUNNER_SOURCE_REVISION,
        },
        mp_context=context,
        cancel_event=cancel_event,
        stop_event=stop_event,
    )
