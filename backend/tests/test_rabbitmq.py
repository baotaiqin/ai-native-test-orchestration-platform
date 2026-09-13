import json

import pika

from app.infrastructure.rabbitmq.client import (
    TASK_EXCHANGE_NAME,
    RabbitMQTaskPublisher,
)


class FakeChannel:
    def __init__(self) -> None:
        self.exchange_declare_calls: list[dict[str, object]] = []
        self.confirm_delivery_calls = 0
        self.publish_calls: list[dict[str, object]] = []
        self.publish_error: Exception | None = None

    def exchange_declare(self, **kwargs: object) -> None:
        self.exchange_declare_calls.append(kwargs)

    def confirm_delivery(self) -> None:
        self.confirm_delivery_calls += 1

    def basic_publish(self, **kwargs: object) -> bool:
        if self.publish_error is not None:
            raise self.publish_error
        self.publish_calls.append(kwargs)
        return True


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel_instance = channel
        self.is_closed = False
        self.close_calls = 0

    def channel(self) -> FakeChannel:
        return self.channel_instance

    def close(self) -> None:
        self.close_calls += 1
        self.is_closed = True


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "message_id": "msg_test_1",
        "run_id": "run_test_1",
        "runner_id": "runner_test_1",
        "run_type": "API_CASE",
        "required_slot_type": "API",
        "attempt": 1,
        "enqueued_at": "2026-08-25T00:00:00Z",
    }


def test_publisher_declares_durable_topic_and_publishes_persistent_message(monkeypatch) -> None:
    channel = FakeChannel()
    connection = FakeConnection(channel)
    monkeypatch.setattr(pika, "BlockingConnection", lambda _: connection)

    result = RabbitMQTaskPublisher("amqp://test").publish(
        routing_key="runner.runner_test_1.api",
        payload=_payload(),
    )

    assert result.published is True
    assert channel.exchange_declare_calls == [
        {
            "exchange": TASK_EXCHANGE_NAME,
            "exchange_type": "topic",
            "durable": True,
        }
    ]
    assert channel.confirm_delivery_calls == 1
    assert len(channel.publish_calls) == 1
    call = channel.publish_calls[0]
    assert call["mandatory"] is True
    assert call["exchange"] == TASK_EXCHANGE_NAME
    assert call["routing_key"] == "runner.runner_test_1.api"
    assert call["body"] == json.dumps(
        _payload(), ensure_ascii=False, separators=(",", ":")
    ).encode()
    properties = call["properties"]
    assert isinstance(properties, pika.BasicProperties)
    assert properties.delivery_mode == 2
    assert properties.message_id == "msg_test_1"
    assert connection.close_calls == 1


def test_publisher_reports_mandatory_unroutable_without_leaking_payload(monkeypatch) -> None:
    channel = FakeChannel()
    channel.publish_error = pika.exceptions.UnroutableError([])
    connection = FakeConnection(channel)
    monkeypatch.setattr(pika, "BlockingConnection", lambda _: connection)

    result = RabbitMQTaskPublisher("amqp://test").publish(
        routing_key="runner.runner_test_1.api",
        payload=_payload(),
    )

    assert result.published is False
    assert result.code == "BROKER_UNROUTABLE"
    assert "msg_test_1" not in (result.message or "")


def test_publisher_reports_connection_failure_as_retryable(monkeypatch) -> None:
    monkeypatch.setattr(
        pika,
        "BlockingConnection",
        lambda _: (_ for _ in ()).throw(pika.exceptions.AMQPConnectionError()),
    )

    result = RabbitMQTaskPublisher("amqp://test").publish(
        routing_key="runner.runner_test_1.api",
        payload=_payload(),
    )

    assert result.published is False
    assert result.code == "BROKER_UNAVAILABLE"
    assert "credential" not in (result.message or "").lower()
