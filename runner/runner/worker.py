"""Runner 前台常驻 Worker：心跳线程与主线程任务轮询。"""

from __future__ import annotations

import signal
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Event, RLock, current_thread, main_thread
from types import FrameType

from runner.broker import is_transient_rabbitmq_error
from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import AuthenticationError, ProtocolError, RunnerError
from runner.lifecycle import HeartbeatService


class SignalBoundary:
    """在主线程临时安装只负责 set stop event 的终止信号处理器。"""

    def __init__(self, stop_event: Event, *, signal_api: object = signal) -> None:
        self._stop_event = stop_event
        self._signal_api = signal_api
        self._installed: list[tuple[int, object]] = []

    @property
    def installed_signals(self) -> tuple[int, ...]:
        return tuple(signum for signum, _previous in self._installed)

    def install(self) -> None:
        if self._installed:
            return
        if current_thread() is not main_thread():
            return
        getter = getattr(self._signal_api, "getsignal", None)
        setter = getattr(self._signal_api, "signal", None)
        if not callable(getter) or not callable(setter):
            return
        seen: set[int] = set()
        for name in ("SIGINT", "SIGTERM"):
            signum = getattr(self._signal_api, name, None)
            if not isinstance(signum, int) or signum in seen:
                continue
            seen.add(signum)
            try:
                previous = getter(signum)
                setter(signum, self._handle)
            except (OSError, RuntimeError, ValueError):
                continue
            self._installed.append((signum, previous))

    def restore(self) -> None:
        setter = getattr(self._signal_api, "signal", None)
        if not callable(setter):
            self._installed.clear()
            return
        installed = self._installed
        self._installed = []
        for signum, previous in reversed(installed):
            try:
                setter(signum, previous)
            except (OSError, RuntimeError, ValueError):
                continue

    def __enter__(self) -> SignalBoundary:
        self.install()
        return self

    def __exit__(self, *_: object) -> None:
        self.restore()

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop_event.set()


class WorkerStopReason(StrEnum):
    STOP_REQUESTED = "STOP_REQUESTED"
    HEARTBEAT_AUTHENTICATION_FAILED = "HEARTBEAT_AUTHENTICATION_FAILED"
    HEARTBEAT_PROTOCOL_FAILED = "HEARTBEAT_PROTOCOL_FAILED"
    HEARTBEAT_ERROR = "HEARTBEAT_ERROR"
    TASK_AUTHENTICATION_FAILED = "TASK_AUTHENTICATION_FAILED"
    RABBITMQ_ERROR = "RABBITMQ_ERROR"
    WORKER_ERROR = "WORKER_ERROR"


@dataclass(frozen=True)
class RabbitMqReconnectPolicy:
    """RabbitMQ 故障恢复的有界指数退避策略。"""

    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.initial_delay_seconds, bool)
            or not isinstance(self.initial_delay_seconds, (int, float))
            or not 0.1 <= self.initial_delay_seconds <= 60
        ):
            raise RunnerError("RabbitMQ 重连初始退避必须在 0.1 到 60 秒之间")
        if (
            isinstance(self.max_delay_seconds, bool)
            or not isinstance(self.max_delay_seconds, (int, float))
            or not 0.1 <= self.max_delay_seconds <= 600
            or self.max_delay_seconds < self.initial_delay_seconds
        ):
            raise RunnerError("RabbitMQ 重连退避上限必须在 0.1 到 600 秒之间且不小于初始退避")

    def delay_for(self, failure_count: int) -> float:
        if failure_count < 0:
            raise RunnerError("RabbitMQ 重连失败次数不能为负数")
        return min(
            float(self.max_delay_seconds),
            float(self.initial_delay_seconds) * (2 ** min(failure_count, 30)),
        )


@dataclass
class WorkerStats:
    heartbeat_count: int = 0
    heartbeat_transient_failures: int = 0
    ack_count: int = 0
    requeued_count: int = 0
    rejected_count: int = 0
    empty_count: int = 0
    rabbitmq_reconnect_count: int = 0

    def record(self, outcome: ConsumeOutcome) -> None:
        if outcome is ConsumeOutcome.ACKED:
            self.ack_count += 1
        elif outcome is ConsumeOutcome.REQUEUED:
            self.requeued_count += 1
        elif outcome is ConsumeOutcome.REJECTED:
            self.rejected_count += 1
        elif outcome is ConsumeOutcome.EMPTY:
            self.empty_count += 1


@dataclass(frozen=True)
class WorkerRunResult:
    stats: WorkerStats
    stop_reason: WorkerStopReason

    @property
    def exit_code(self) -> int:
        return 0 if self.stop_reason is WorkerStopReason.STOP_REQUESTED else 2

    def to_mapping(self) -> dict[str, object]:
        return {
            "heartbeat_count": self.stats.heartbeat_count,
            "heartbeat_transient_failures": self.stats.heartbeat_transient_failures,
            "ack_count": self.stats.ack_count,
            "requeued_count": self.stats.requeued_count,
            "rejected_count": self.stats.rejected_count,
            "empty_count": self.stats.empty_count,
            "rabbitmq_reconnect_count": self.stats.rabbitmq_reconnect_count,
            "stop_reason": self.stop_reason.value,
        }


@dataclass
class WorkerResources:
    """Worker 持有的资源，关闭顺序固定且按对象身份去重。"""

    executor: object | None
    heartbeat_transport: object | None
    task_transport: object | None
    connection: object | None


class RunnerWorker:
    """前台 Worker；RabbitMQ channel 仅由调用 run 的主线程使用。"""

    def __init__(
        self,
        consumer: RunnerTaskConsumer,
        heartbeat_service: HeartbeatService,
        stop_event: Event,
        resources: WorkerResources,
        *,
        poll_interval_seconds: float = 1.0,
        waiter: Callable[[float], bool] | None = None,
        connection_factory: Callable[[], object] | None = None,
        consumer_factory: Callable[[object], RunnerTaskConsumer] | None = None,
        rabbitmq_error_classifier: Callable[[BaseException], bool] = is_transient_rabbitmq_error,
        reconnect_policy: RabbitMqReconnectPolicy | None = None,
        reconnect_waiter: Callable[[float], bool] | None = None,
    ) -> None:
        if not 0.01 <= poll_interval_seconds <= 60:
            raise RunnerError("Worker 空队列轮询间隔必须在 0.01 到 60 秒之间")
        self.consumer = consumer
        self.heartbeat_service = heartbeat_service
        self.stop_event = stop_event
        self.resources = resources
        self.poll_interval_seconds = poll_interval_seconds
        self._waiter = waiter or stop_event.wait
        self._connection_factory = connection_factory
        self._consumer_factory = consumer_factory
        self._rabbitmq_error_classifier = rabbitmq_error_classifier
        self._reconnect_policy = reconnect_policy or RabbitMqReconnectPolicy()
        self._reconnect_waiter = reconnect_waiter or stop_event.wait
        self._close_lock = RLock()
        self._closed_resources: list[object] = []
        self._closed = False
        self._started = False

    def run(self) -> WorkerRunResult:
        if self._started:
            raise RunnerError("Runner Worker 不能重复运行")
        self._started = True
        stats = WorkerStats()
        reason: WorkerStopReason | None = None
        try:
            set_stop_event = getattr(self.consumer, "set_stop_event", None)
            if callable(set_stop_event):
                set_stop_event(self.stop_event)
            self.heartbeat_service.start()
            self.heartbeat_service.wait_initial_attempt()
            reason = self._heartbeat_stop_reason()
            if reason is None and not self.stop_event.is_set():
                topology: object | None = None
                while topology is None and not self.stop_event.is_set():
                    try:
                        topology = self.consumer.configure()
                    except Exception as exc:
                        if not self._is_reconnectable(exc):
                            raise
                        topology = self._reconnect_after_failure()
                        if topology is None:
                            reason = (
                                WorkerStopReason.STOP_REQUESTED
                                if self.stop_event.is_set()
                                else WorkerStopReason.RABBITMQ_ERROR
                            )
                            self.stop_event.set()
                            break
                        stats.rabbitmq_reconnect_count += 1
                while topology is not None and not self.stop_event.is_set():
                    try:
                        outcome = self.consumer.execute_once_configured(topology)
                    except Exception as exc:
                        if not self._is_reconnectable(exc):
                            raise
                        topology = self._reconnect_after_failure()
                        if topology is None:
                            reason = (
                                WorkerStopReason.STOP_REQUESTED
                                if self.stop_event.is_set()
                                else WorkerStopReason.RABBITMQ_ERROR
                            )
                            self.stop_event.set()
                            break
                        stats.rabbitmq_reconnect_count += 1
                        continue
                    stats.record(outcome)
                    if outcome is ConsumeOutcome.STOPPED_AUTH:
                        reason = WorkerStopReason.TASK_AUTHENTICATION_FAILED
                        self.stop_event.set()
                        break
                    if outcome is ConsumeOutcome.EMPTY and self._waiter(self.poll_interval_seconds):
                        break
        except KeyboardInterrupt:
            reason = WorkerStopReason.STOP_REQUESTED
            self.stop_event.set()
        except AuthenticationError:
            reason = WorkerStopReason.TASK_AUTHENTICATION_FAILED
            self.stop_event.set()
        except (ProtocolError, RunnerError):
            reason = WorkerStopReason.RABBITMQ_ERROR
            self.stop_event.set()
        except Exception:
            reason = WorkerStopReason.RABBITMQ_ERROR
            self.stop_event.set()
        finally:
            self.stop_event.set()
            self.heartbeat_service.stop()
            heartbeat_result = self.heartbeat_service.join()
            stats.heartbeat_count = heartbeat_result.cycles
            stats.heartbeat_transient_failures = heartbeat_result.transient_failures
            if reason is None:
                reason = self._heartbeat_stop_reason() or WorkerStopReason.STOP_REQUESTED
            self.close()
        return WorkerRunResult(stats=stats, stop_reason=reason)

    def close(self) -> None:
        """按 executor、两个 HTTP transport、RabbitMQ connection 顺序关闭一次。"""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            for resource in (
                self.resources.executor,
                self.resources.heartbeat_transport,
                self.resources.task_transport,
                self.resources.connection,
            ):
                self._close_resource_once(resource)

    def _is_reconnectable(self, error: BaseException) -> bool:
        if self._connection_factory is None or self._consumer_factory is None:
            return False
        try:
            return self._rabbitmq_error_classifier(error)
        except Exception:
            return False

    def _reconnect_after_failure(self) -> object | None:
        """关闭旧连接并恢复新 channel/topology；停止时返回 None。"""

        self._close_resource_once(self.resources.connection)
        self.resources.connection = None
        failure_count = 0
        while not self.stop_event.is_set():
            delay = self._reconnect_policy.delay_for(failure_count)
            if self._reconnect_waiter(delay) or self.stop_event.is_set():
                return None
            connection: object | None = None
            try:
                connection = self._connection_factory()
                consumer = self._consumer_factory(connection)
                set_stop_event = getattr(consumer, "set_stop_event", None)
                if callable(set_stop_event):
                    set_stop_event(self.stop_event)
                topology = consumer.configure()
            except Exception as exc:
                if connection is not None:
                    self._close_resource_once(connection)
                if not self._rabbitmq_error_classifier(exc):
                    raise RunnerError("RabbitMQ 重连或 topology 配置失败") from exc
                failure_count += 1
                continue
            self.resources.connection = connection
            self.consumer = consumer
            return topology
        return None

    def _close_resource_once(self, resource: object | None) -> None:
        if resource is None:
            return
        with self._close_lock:
            if any(resource is closed for closed in self._closed_resources):
                return
            self._closed_resources.append(resource)
        close = getattr(resource, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # 关闭一个资源失败不能阻止后续资源释放。
                return

    def _heartbeat_stop_reason(self) -> WorkerStopReason | None:
        kind = self.heartbeat_service.result().fatal_kind
        if kind is None:
            return None
        try:
            return WorkerStopReason(kind)
        except ValueError:
            return WorkerStopReason.HEARTBEAT_ERROR
