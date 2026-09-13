import json
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import pika

from app.core.config import get_settings

TASK_EXCHANGE_NAME = "ai_test.tasks.v1"


@dataclass(frozen=True)
class TaskPublishResult:
    published: bool
    code: str | None = None
    message: str | None = None


class TaskPublisher(Protocol):
    def publish(self, *, routing_key: str, payload: dict[str, Any]) -> TaskPublishResult:
        """Publish one task and return a safe, user-facing outcome."""


class RabbitMQTaskPublisher:
    def __init__(self, url: str, *, exchange_name: str = TASK_EXCHANGE_NAME) -> None:
        self.url = url
        self.exchange_name = exchange_name

    def publish(self, *, routing_key: str, payload: dict[str, Any]) -> TaskPublishResult:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        connection: pika.BlockingConnection | None = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(self.url))
            channel = connection.channel()
            channel.exchange_declare(
                exchange=self.exchange_name,
                exchange_type="topic",
                durable=True,
            )
            channel.confirm_delivery()
            accepted = channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    message_id=str(payload["message_id"]),
                ),
                mandatory=True,
            )
            if accepted is False:
                return TaskPublishResult(
                    published=False,
                    code="BROKER_NACK",
                    message="RabbitMQ 未确认任务消息",
                )
            return TaskPublishResult(published=True)
        except pika.exceptions.UnroutableError:
            return TaskPublishResult(
                published=False,
                code="BROKER_UNROUTABLE",
                message="RabbitMQ 未找到可接收该任务的 Runner 路由",
            )
        except (pika.exceptions.AMQPError, OSError, TimeoutError):
            return TaskPublishResult(
                published=False,
                code="BROKER_UNAVAILABLE",
                message="RabbitMQ 暂不可用，任务已保留待重试",
            )
        finally:
            if connection is not None and not connection.is_closed:
                with suppress(pika.exceptions.AMQPError, OSError):
                    connection.close()


@lru_cache
def get_task_publisher() -> RabbitMQTaskPublisher:
    return RabbitMQTaskPublisher(get_settings().rabbitmq_url)
