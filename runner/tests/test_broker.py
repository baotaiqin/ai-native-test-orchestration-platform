import pytest

from runner.broker import (
    RABBITMQ_URL_ENV,
    PikaBroker,
    RabbitMqConfig,
    is_transient_rabbitmq_error,
)
from runner.errors import ConfigurationError
from runner.topology import (
    DEAD_LETTER_EXCHANGE,
    DEAD_QUEUE,
    TASK_EXCHANGE,
    declare_runner_topology,
)


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def exchange_declare(self, **kwargs):
        self.calls.append(("exchange_declare", kwargs))

    def queue_declare(self, **kwargs):
        self.calls.append(("queue_declare", kwargs))

    def queue_bind(self, **kwargs):
        self.calls.append(("queue_bind", kwargs))


def test_rabbitmq_url_redacts_userinfo_and_uses_environment(monkeypatch) -> None:
    secret_url = "amqps://runner:super-secret@example.test:5671/vhost?heartbeat=30"
    monkeypatch.setenv(RABBITMQ_URL_ENV, secret_url)

    config = RabbitMqConfig.from_environment()

    assert config.url == secret_url
    assert "super-secret" not in repr(config)
    assert "runner" not in config.redacted_url
    assert config.redacted_url == "amqps://example.test:5671/vhost"


def test_rabbitmq_url_rejects_invalid_scheme() -> None:
    with pytest.raises(ConfigurationError):
        RabbitMqConfig("https://example.test/rabbit")


def test_pika_broker_connection_factory_is_injectable() -> None:
    config = RabbitMqConfig("amqp://guest:guest@example.test/")
    connection = object()
    broker = PikaBroker(config, connection_factory=lambda received: connection)

    assert broker.connect() is connection


def test_broker_restart_connection_close_is_transient_but_config_errors_are_not() -> None:
    from pika.exceptions import ChannelClosedByBroker, ConnectionClosedByBroker

    assert is_transient_rabbitmq_error(ConnectionRefusedError(10061, "refused"))
    assert is_transient_rabbitmq_error(TimeoutError("connect timeout"))
    assert is_transient_rabbitmq_error(
        ConnectionClosedByBroker(320, "CONNECTION_FORCED")
    )
    assert is_transient_rabbitmq_error(
        ChannelClosedByBroker(320, "CONNECTION_FORCED")
    )
    assert not is_transient_rabbitmq_error(
        ConnectionClosedByBroker(403, "ACCESS_REFUSED")
    )
    assert not is_transient_rabbitmq_error(
        ChannelClosedByBroker(406, "PRECONDITION_FAILED")
    )
    assert not is_transient_rabbitmq_error(OSError("invalid broker configuration"))


def test_runner_topology_is_durable_and_binds_only_active_slots() -> None:
    channel = FakeChannel()

    topology = declare_runner_topology(
        channel,
        "runner-001",
        {"API": 1, "WEB": 0, "PERFORMANCE": 2},
    )

    assert topology.queue == "ai_test.runner.runner-001.v1"
    assert topology.bindings == (
        "runner.runner-001.api",
        "runner.runner-001.performance",
    )
    exchanges = [kwargs for name, kwargs in channel.calls if name == "exchange_declare"]
    assert exchanges == [
        {"exchange": TASK_EXCHANGE, "exchange_type": "topic", "durable": True},
        {
            "exchange": DEAD_LETTER_EXCHANGE,
            "exchange_type": "topic",
            "durable": True,
        },
    ]
    queues = [kwargs for name, kwargs in channel.calls if name == "queue_declare"]
    assert queues[0] == {"queue": DEAD_QUEUE, "durable": True}
    assert queues[1] == {
        "queue": "ai_test.runner.runner-001.v1",
        "durable": True,
        "arguments": {"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE},
    }
    bindings = [kwargs for name, kwargs in channel.calls if name == "queue_bind"]
    assert bindings[0] == {
        "exchange": DEAD_LETTER_EXCHANGE,
        "queue": DEAD_QUEUE,
        "routing_key": "#",
    }
    assert [item["routing_key"] for item in bindings[1:]] == list(topology.bindings)
