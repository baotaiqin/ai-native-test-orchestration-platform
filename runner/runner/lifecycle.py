"""可停止的心跳生命周期。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Condition, Event, Lock, Thread

from runner.config import RunnerConfig
from runner.errors import AuthenticationError, ProtocolError, RunnerError, TransportError
from runner.models import HeartbeatResult, RunnerIdentity
from runner.protocol import RunnerClient, clamp_heartbeat_interval

DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS = 30


@dataclass(frozen=True)
class HeartbeatLoopResult:
    cycles: int
    stopped: bool


@dataclass(frozen=True)
class HeartbeatServiceResult:
    """后台心跳线程的安全统计和终止状态。"""

    cycles: int
    transient_failures: int
    fatal_kind: str | None


class HeartbeatService:
    """在独立线程中维持心跳，不接触 Runner 的 RabbitMQ channel。"""

    def __init__(
        self,
        client: RunnerClient,
        config: RunnerConfig,
        identity: RunnerIdentity,
        snapshot_factory: Callable[[], Mapping[str, object]],
        stop_event: Event,
        *,
        default_interval_seconds: int = DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS,
        waiter: Callable[[float], bool] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._identity = identity
        self._snapshot_factory = snapshot_factory
        self._stop_event = stop_event
        self._default_interval = clamp_heartbeat_interval(default_interval_seconds)
        self._waiter = waiter or stop_event.wait
        self._uses_custom_waiter = waiter is not None
        self._initial_attempt = Event()
        self._lock = Lock()
        self._wake_condition = Condition()
        self._wake_requested = False
        self._cycles = 0
        self._transient_failures = 0
        self._fatal_kind: str | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RunnerError("Runner heartbeat service 不能重复启动")
        self._thread = Thread(
            target=self._run,
            name="ai-test-runner-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def wait_initial_attempt(self, timeout: float | None = None) -> bool:
        """等待首次心跳尝试完成，返回是否已收到尝试信号。"""

        return self._initial_attempt.wait(timeout)

    def stop(self) -> None:
        self._stop_event.set()
        with self._wake_condition:
            self._wake_condition.notify_all()

    def request_immediate_heartbeat(self) -> None:
        """请求在当前等待周期结束前尽快发送一次心跳。"""

        with self._wake_condition:
            self._wake_requested = True
            self._wake_condition.notify_all()

    def join(self) -> HeartbeatServiceResult:
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            return HeartbeatServiceResult(
                cycles=self._cycles,
                transient_failures=self._transient_failures,
                fatal_kind=self._fatal_kind,
            )

    def result(self) -> HeartbeatServiceResult:
        with self._lock:
            return HeartbeatServiceResult(
                cycles=self._cycles,
                transient_failures=self._transient_failures,
                fatal_kind=self._fatal_kind,
            )

    def _run(self) -> None:
        interval = self._default_interval
        if self._stop_event.is_set():
            self._initial_attempt.set()
            return
        while not self._stop_event.is_set():
            try:
                result: HeartbeatResult = self._client.heartbeat(
                    self._identity,
                    dict(self._snapshot_factory()),
                )
            except AuthenticationError:
                self._set_fatal("HEARTBEAT_AUTHENTICATION_FAILED")
                return
            except ProtocolError:
                self._set_fatal("HEARTBEAT_PROTOCOL_FAILED")
                return
            except TransportError:
                with self._lock:
                    self._transient_failures += 1
                self._initial_attempt.set()
                interval = self._default_interval
            except Exception:
                self._set_fatal("HEARTBEAT_ERROR")
                return
            else:
                with self._lock:
                    self._cycles += 1
                self._initial_attempt.set()
                try:
                    interval = clamp_heartbeat_interval(result.heartbeat_interval_seconds)
                except ProtocolError:
                    self._set_fatal("HEARTBEAT_PROTOCOL_FAILED")
                    return
            try:
                should_stop = self._wait_for_next(interval)
            except Exception:
                self._set_fatal("HEARTBEAT_ERROR")
                return
            if should_stop:
                return

    def _wait_for_next(self, interval: int) -> bool:
        if self._uses_custom_waiter:
            return self._waiter(interval)
        with self._wake_condition:
            if self._stop_event.is_set():
                return True
            if self._wake_requested:
                self._wake_requested = False
                return False
            self._wake_condition.wait_for(
                lambda: self._stop_event.is_set() or self._wake_requested,
                timeout=interval,
            )
            if self._stop_event.is_set():
                return True
            self._wake_requested = False
            return False

    def _set_fatal(self, kind: str) -> None:
        with self._lock:
            self._fatal_kind = kind
        self._initial_attempt.set()
        self._stop_event.set()
        with self._wake_condition:
            self._wake_condition.notify_all()


def _wait_for_interval(
    stop_event: Event,
    seconds: int,
    *,
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    deadline = clock() + seconds
    while not stop_event.is_set():
        remaining = deadline - clock()
        if remaining <= 0:
            return True
        sleeper(min(0.5, remaining))
    return False


def heartbeat_loop(
    client: RunnerClient,
    config: RunnerConfig,
    identity: RunnerIdentity,
    snapshot_factory: Callable[[], Mapping[str, object]],
    stop_event: Event,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_cycles: int | None = None,
) -> HeartbeatLoopResult:
    """立即发送一次心跳，随后按服务端间隔运行，直到停止事件或异常。"""

    cycles = 0
    while not stop_event.is_set():
        result: HeartbeatResult = client.heartbeat(identity, dict(snapshot_factory()))
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return HeartbeatLoopResult(cycles=cycles, stopped=False)
        interval = clamp_heartbeat_interval(result.heartbeat_interval_seconds)
        if not _wait_for_interval(
            stop_event,
            interval,
            sleeper=sleeper,
            clock=clock,
        ):
            return HeartbeatLoopResult(cycles=cycles, stopped=True)
    return HeartbeatLoopResult(cycles=cycles, stopped=True)
