from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Thread

import pytest
from pika.exceptions import ConnectionClosedByBroker

from runner.config import RunnerConfig
from runner.consumer import ConsumeOutcome
from runner.errors import AuthenticationError, ProtocolError, RunnerError, TransportError
from runner.lifecycle import HeartbeatService
from runner.models import HeartbeatResult, RunnerIdentity
from runner.topology import RunnerTaskTopology
from runner.worker import (
    RabbitMqReconnectPolicy,
    RunnerWorker,
    SignalBoundary,
    WorkerResources,
    WorkerStopReason,
)

CONFIG = RunnerConfig("https://backend.example.test", "runner-test")
IDENTITY = RunnerIdentity("runner-001", "credential-value-123456789")
TOPOLOGY = RunnerTaskTopology(
    "runner-001",
    "ai_test.runner.runner-001.v1",
    ("runner.runner-001.api",),
)


def _heartbeat_result() -> HeartbeatResult:
    return HeartbeatResult(
        "runner-001",
        "ACTIVE",
        5,
        datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )


class FakeHeartbeatClient:
    def __init__(self, *responses: HeartbeatResult | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.called = Event()
        self.second_called = Event()

    def heartbeat(self, identity, snapshot) -> HeartbeatResult:
        del identity, snapshot
        self.calls += 1
        self.called.set()
        if self.calls >= 2:
            self.second_called.set()
        if not self.responses:
            return _heartbeat_result()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeConsumer:
    def __init__(self, *outcomes: ConsumeOutcome) -> None:
        self.outcomes = list(outcomes)
        self.configure_calls = 0
        self.poll_calls = 0
        self.task_started = Event()
        self.release_task = Event()

    def configure(self) -> RunnerTaskTopology:
        self.configure_calls += 1
        return TOPOLOGY

    def execute_once_configured(self, topology: RunnerTaskTopology) -> ConsumeOutcome:
        assert topology is TOPOLOGY
        self.poll_calls += 1
        if not self.outcomes:
            return ConsumeOutcome.EMPTY
        return self.outcomes.pop(0)


class BlockingConsumer(FakeConsumer):
    def execute_once_configured(self, topology: RunnerTaskTopology) -> ConsumeOutcome:
        assert topology is TOPOLOGY
        self.poll_calls += 1
        if self.poll_calls == 1:
            self.task_started.set()
            assert self.release_task.wait(2)
            return ConsumeOutcome.ACKED
        return ConsumeOutcome.EMPTY


class RabbitMqTransientFailure(Exception):
    pass


class FailingConsumer(FakeConsumer):
    def execute_once_configured(self, topology: RunnerTaskTopology) -> ConsumeOutcome:
        assert topology is TOPOLOGY
        self.poll_calls += 1
        raise RabbitMqTransientFailure("channel closed")


class BrokerRestartConsumer(FakeConsumer):
    def execute_once_configured(self, topology: RunnerTaskTopology) -> ConsumeOutcome:
        assert topology is TOPOLOGY
        self.poll_calls += 1
        raise ConnectionClosedByBroker(320, "CONNECTION_FORCED")


class PermanentFailureConsumer(FakeConsumer):
    def execute_once_configured(self, topology: RunnerTaskTopology) -> ConsumeOutcome:
        assert topology is TOPOLOGY
        self.poll_calls += 1
        raise ProtocolError("permanent topology failure")


class Closeable:
    def __init__(self, name: str, closed: list[str]) -> None:
        self.name = name
        self.closed = closed
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed.append(self.name)


class FakeSignalApi:
    SIGINT = 2
    SIGTERM = 15

    def __init__(self) -> None:
        self.handlers: dict[int, object] = {
            self.SIGINT: "old-int-handler",
            self.SIGTERM: "old-term-handler",
        }
        self.calls: list[tuple[int, object]] = []

    def getsignal(self, signum: int) -> object:
        return self.handlers[signum]

    def signal(self, signum: int, handler: object) -> object:
        previous = self.handlers[signum]
        self.handlers[signum] = handler
        self.calls.append((signum, handler))
        return previous


def _resources(closed: list[str]) -> WorkerResources:
    return WorkerResources(
        executor=Closeable("executor", closed),
        heartbeat_transport=Closeable("heartbeat_transport", closed),
        task_transport=Closeable("task_transport", closed),
        connection=Closeable("connection", closed),
    )


def _heartbeat_service(
    client: FakeHeartbeatClient,
    stop_event: Event,
    *,
    waiter=None,
) -> HeartbeatService:
    return HeartbeatService(
        client,
        CONFIG,
        IDENTITY,
        lambda: {"name": "runner-test"},
        stop_event,
        waiter=waiter,
    )


def test_worker_keeps_heartbeat_running_during_api_execution() -> None:
    stop_event = Event()
    task_started = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    heartbeat_waits = 0

    def heartbeat_waiter(seconds: float) -> bool:
        nonlocal heartbeat_waits
        assert seconds == 5
        heartbeat_waits += 1
        if heartbeat_waits == 1:
            assert task_started.wait(2)
            return False
        return stop_event.wait()

    consumer = BlockingConsumer()
    consumer.task_started = task_started
    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event, waiter=heartbeat_waiter),
        stop_event,
        _resources([]),
        poll_interval_seconds=0.01,
        waiter=lambda seconds: (stop_event.set() or True),
    )
    result_holder: list[object] = []
    thread = Thread(target=lambda: result_holder.append(worker.run()))
    thread.start()

    assert task_started.wait(2)
    assert heartbeat_client.called.wait(2)
    assert heartbeat_client.second_called.wait(2)
    stop_event.set()
    consumer.release_task.set()
    thread.join(2)

    assert not thread.is_alive()
    result = result_holder[0]
    assert result.stats.heartbeat_count >= 2
    assert result.stats.ack_count == 1
    assert result.stats.empty_count == 0
    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED


def test_worker_empty_queue_waits_without_reconfiguring_topology() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    consumer = FakeConsumer(ConsumeOutcome.EMPTY)
    waits: list[float] = []

    def poll_waiter(seconds: float) -> bool:
        waits.append(seconds)
        stop_event.set()
        return True

    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        _resources([]),
        poll_interval_seconds=0.25,
        waiter=poll_waiter,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert result.stats.empty_count == 1
    assert waits == [0.25]
    assert consumer.configure_calls == 1
    assert consumer.poll_calls == 1


def test_worker_heartbeat_authentication_failure_stops_before_topology() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(AuthenticationError("credential invalid"))
    consumer = FakeConsumer(ConsumeOutcome.EMPTY)

    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        _resources([]),
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.HEARTBEAT_AUTHENTICATION_FAILED
    assert consumer.configure_calls == 0
    assert result.exit_code == 2


def test_worker_reconnects_after_transient_rabbitmq_failure_and_recovers() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    initial_consumer = FailingConsumer()
    recovered_consumer = FakeConsumer(ConsumeOutcome.EMPTY)
    closed: list[str] = []
    old_connection = Closeable("old_connection", closed)
    new_connection = Closeable("new_connection", closed)
    reconnect_calls = 0
    reconnect_waits: list[float] = []

    def connect() -> object:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return new_connection

    def consumer_factory(connection: object) -> FakeConsumer:
        assert connection is new_connection
        return recovered_consumer

    def poll_waiter(seconds: float) -> bool:
        assert seconds == 0.25
        stop_event.set()
        return True

    def reconnect_waiter(seconds: float) -> bool:
        reconnect_waits.append(seconds)
        return False

    resources = WorkerResources(
        executor=Closeable("executor", closed),
        heartbeat_transport=Closeable("heartbeat_transport", closed),
        task_transport=Closeable("task_transport", closed),
        connection=old_connection,
    )
    worker = RunnerWorker(
        initial_consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        resources,
        poll_interval_seconds=0.25,
        waiter=poll_waiter,
        connection_factory=connect,
        consumer_factory=consumer_factory,  # type: ignore[arg-type]
        rabbitmq_error_classifier=lambda error: isinstance(error, RabbitMqTransientFailure),
        reconnect_policy=RabbitMqReconnectPolicy(0.2, 0.5),
        reconnect_waiter=reconnect_waiter,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert result.stats.rabbitmq_reconnect_count == 1
    assert reconnect_calls == 1
    assert reconnect_waits == [0.2]
    assert initial_consumer.poll_calls == 1
    assert recovered_consumer.configure_calls == 1
    assert recovered_consumer.poll_calls == 1
    assert old_connection.close_calls == 1
    assert new_connection.close_calls == 1
    assert closed[:1] == ["old_connection"]


def test_worker_reconnects_after_pika_broker_restart_with_default_classifier() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    initial_consumer = BrokerRestartConsumer()
    recovered_consumer = FakeConsumer(ConsumeOutcome.EMPTY)
    old_connection = Closeable("old_connection", [])
    new_connection = Closeable("new_connection", [])
    reconnect_waits: list[float] = []

    def reconnect_waiter(seconds: float) -> bool:
        reconnect_waits.append(seconds)
        return False

    def poll_waiter(_seconds: float) -> bool:
        stop_event.set()
        return True

    worker = RunnerWorker(
        initial_consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        WorkerResources(None, None, None, old_connection),
        poll_interval_seconds=0.1,
        waiter=poll_waiter,
        connection_factory=lambda: new_connection,
        consumer_factory=lambda connection: recovered_consumer,  # type: ignore[arg-type]
        reconnect_policy=RabbitMqReconnectPolicy(0.1, 0.2),
        reconnect_waiter=reconnect_waiter,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert result.stats.rabbitmq_reconnect_count == 1
    assert reconnect_waits == [0.1]
    assert initial_consumer.poll_calls == 1
    assert recovered_consumer.configure_calls == 1
    assert recovered_consumer.poll_calls == 1
    assert old_connection.close_calls == 1
    assert new_connection.close_calls == 1


def test_worker_retries_transient_reconnect_failures_before_recovering() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    initial_consumer = BrokerRestartConsumer()
    recovered_consumer = FakeConsumer(ConsumeOutcome.EMPTY)
    old_connection = Closeable("old_connection", [])
    new_connection = Closeable("new_connection", [])
    reconnect_failures: list[Exception] = [
        TransportError("broker temporarily unavailable", transient=True),
        ConnectionRefusedError(10061, "broker temporarily unavailable"),
    ]
    reconnect_waits: list[float] = []
    connect_calls = 0

    def connect() -> object:
        nonlocal connect_calls
        connect_calls += 1
        if reconnect_failures:
            raise reconnect_failures.pop(0)
        return new_connection

    def reconnect_waiter(seconds: float) -> bool:
        reconnect_waits.append(seconds)
        return False

    def poll_waiter(_seconds: float) -> bool:
        stop_event.set()
        return True

    worker = RunnerWorker(
        initial_consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        WorkerResources(None, None, None, old_connection),
        poll_interval_seconds=0.1,
        waiter=poll_waiter,
        connection_factory=connect,
        consumer_factory=lambda connection: recovered_consumer,  # type: ignore[arg-type]
        reconnect_policy=RabbitMqReconnectPolicy(0.1, 0.2),
        reconnect_waiter=reconnect_waiter,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert result.stats.rabbitmq_reconnect_count == 1
    assert connect_calls == 3
    assert reconnect_waits == [0.1, 0.2, 0.2]
    assert recovered_consumer.configure_calls == 1
    assert recovered_consumer.poll_calls == 1
    assert old_connection.close_calls == 1
    assert new_connection.close_calls == 1


def test_worker_stop_event_interrupts_rabbitmq_reconnect_backoff() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    initial_consumer = FailingConsumer()
    old_connection = Closeable("old_connection", [])
    reconnect_calls = 0

    def connect() -> object:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return object()

    def reconnect_waiter(seconds: float) -> bool:
        assert seconds == 0.2
        stop_event.set()
        return True

    worker = RunnerWorker(
        initial_consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        WorkerResources(None, None, None, old_connection),
        connection_factory=connect,
        consumer_factory=lambda connection: FakeConsumer(),  # type: ignore[arg-type]
        rabbitmq_error_classifier=lambda error: isinstance(error, RabbitMqTransientFailure),
        reconnect_policy=RabbitMqReconnectPolicy(0.2, 0.5),
        reconnect_waiter=reconnect_waiter,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert result.stats.rabbitmq_reconnect_count == 0
    assert reconnect_calls == 0
    assert old_connection.close_calls == 1


def test_worker_does_not_reconnect_permanent_rabbitmq_error() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    initial_consumer = PermanentFailureConsumer()
    reconnect_calls = 0

    def connect() -> object:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return object()

    worker = RunnerWorker(
        initial_consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        WorkerResources(None, None, None, Closeable("connection", [])),
        connection_factory=connect,
        consumer_factory=lambda connection: FakeConsumer(),  # type: ignore[arg-type]
        rabbitmq_error_classifier=lambda _error: False,
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.RABBITMQ_ERROR
    assert result.stats.rabbitmq_reconnect_count == 0
    assert reconnect_calls == 0


def test_rabbitmq_reconnect_policy_is_bounded_and_validated() -> None:
    policy = RabbitMqReconnectPolicy(0.2, 0.5)

    assert [policy.delay_for(index) for index in range(4)] == [0.2, 0.4, 0.5, 0.5]
    with pytest.raises(RunnerError):
        RabbitMqReconnectPolicy(0.05, 0.5)
    with pytest.raises(RunnerError):
        RabbitMqReconnectPolicy(1.0, 0.5)


def test_worker_task_authentication_failure_stops_whole_worker() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    consumer = FakeConsumer(ConsumeOutcome.STOPPED_AUTH)

    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        _resources([]),
    )

    result = worker.run()

    assert result.stop_reason is WorkerStopReason.TASK_AUTHENTICATION_FAILED
    assert result.stats.ack_count == 0
    assert result.exit_code == 2


def test_heartbeat_authentication_failure_during_task_stops_after_task_returns() -> None:
    stop_event = Event()
    task_started = Event()
    heartbeat_client = FakeHeartbeatClient(
        _heartbeat_result(),
        AuthenticationError("credential invalid"),
    )
    heartbeat_waits = 0

    def heartbeat_waiter(seconds: float) -> bool:
        nonlocal heartbeat_waits
        assert seconds == 5
        heartbeat_waits += 1
        if heartbeat_waits == 1:
            assert task_started.wait(2)
            return False
        return stop_event.wait()

    consumer = BlockingConsumer()
    consumer.task_started = task_started
    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event, waiter=heartbeat_waiter),
        stop_event,
        _resources([]),
        waiter=lambda seconds: (stop_event.set() or True),
    )
    result_holder: list[object] = []
    thread = Thread(target=lambda: result_holder.append(worker.run()))
    thread.start()

    assert task_started.wait(2)
    assert heartbeat_client.second_called.wait(2)
    consumer.release_task.set()
    thread.join(2)

    assert not thread.is_alive()
    result = result_holder[0]
    assert result.stop_reason is WorkerStopReason.HEARTBEAT_AUTHENTICATION_FAILED
    assert result.stats.ack_count == 1
    assert result.exit_code == 2


def test_heartbeat_transient_failure_recovers_on_next_cycle() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(
        TransportError("temporary", transient=True),
        HeartbeatResult(
            "runner-001",
            "ACTIVE",
            30,
            datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        ),
    )
    waits = 0

    def waiter(seconds: float) -> bool:
        nonlocal waits
        assert seconds == 30
        waits += 1
        if waits == 1:
            return False
        stop_event.set()
        return True

    service = _heartbeat_service(heartbeat_client, stop_event, waiter=waiter)
    service.start()
    assert service.wait_initial_attempt(2)
    result = service.join()

    assert result.cycles == 1
    assert result.transient_failures == 1
    assert heartbeat_client.calls == 2
    assert result.fatal_kind is None


def test_worker_closes_resources_in_order_and_only_once() -> None:
    stop_event = Event()
    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    consumer = FakeConsumer(ConsumeOutcome.EMPTY)
    closed: list[str] = []

    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        _resources(closed),
        waiter=lambda seconds: (stop_event.set() or True),
    )

    worker.run()
    worker.close()

    assert closed == [
        "executor",
        "heartbeat_transport",
        "task_transport",
        "connection",
    ]


def test_signal_boundary_stops_inflight_worker_and_restores_handlers() -> None:
    stop_event = Event()
    signal_api = FakeSignalApi()
    boundary = SignalBoundary(stop_event, signal_api=signal_api)
    boundary.install()
    assert boundary.installed_signals == (signal_api.SIGINT, signal_api.SIGTERM)
    assert signal_api.handlers[signal_api.SIGINT] != "old-int-handler"

    heartbeat_client = FakeHeartbeatClient(_heartbeat_result())
    consumer = BlockingConsumer()
    closed: list[str] = []
    worker = RunnerWorker(
        consumer,  # type: ignore[arg-type]
        _heartbeat_service(heartbeat_client, stop_event),
        stop_event,
        _resources(closed),
    )
    result_holder: list[object] = []
    thread = Thread(target=lambda: result_holder.append(worker.run()))
    thread.start()

    assert consumer.task_started.wait(2)
    handler = signal_api.handlers[signal_api.SIGINT]
    assert callable(handler)
    handler(signal_api.SIGINT, None)
    assert thread.is_alive()
    consumer.release_task.set()
    thread.join(2)
    boundary.restore()

    assert not thread.is_alive()
    result = result_holder[0]
    assert result.stats.ack_count == 1
    assert result.stop_reason is WorkerStopReason.STOP_REQUESTED
    assert signal_api.handlers == {
        signal_api.SIGINT: "old-int-handler",
        signal_api.SIGTERM: "old-term-handler",
    }
    assert closed == [
        "executor",
        "heartbeat_transport",
        "task_transport",
        "connection",
    ]


def test_signal_boundary_safely_noops_off_main_thread() -> None:
    stop_event = Event()
    signal_api = FakeSignalApi()
    boundary = SignalBoundary(stop_event, signal_api=signal_api)

    thread = Thread(target=boundary.install)
    thread.start()
    thread.join(2)

    assert not thread.is_alive()
    assert boundary.installed_signals == ()
    assert signal_api.handlers == {
        signal_api.SIGINT: "old-int-handler",
        signal_api.SIGTERM: "old-term-handler",
    }
