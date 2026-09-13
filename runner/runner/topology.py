"""Runner RabbitMQ durable exchange、queue、DLX 与 routing binding。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from runner.errors import ConfigurationError

TASK_EXCHANGE = "ai_test.tasks.v1"
TASK_EXCHANGE_TYPE = "topic"
DEAD_LETTER_EXCHANGE = "ai_test.tasks.dlx.v1"
DEAD_QUEUE = "ai_test.tasks.dead.v1"
PERSISTENT_DELIVERY_MODE = 2
SLOT_TYPES = ("API", "WEB", "PERFORMANCE")
_RUNNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class RunnerTaskTopology:
    runner_id: str
    queue: str
    bindings: tuple[str, ...]


def runner_queue_name(runner_id: str) -> str:
    _validate_runner_id(runner_id)
    return f"ai_test.runner.{runner_id}.v1"


def runner_routing_key(runner_id: str, slot_type: str) -> str:
    _validate_runner_id(runner_id)
    normalized_slot = slot_type.upper()
    if normalized_slot not in SLOT_TYPES:
        raise ConfigurationError("Runner routing slot type 无效")
    return f"runner.{runner_id}.{normalized_slot.lower()}"


def active_slot_types(slots: Mapping[str, int]) -> tuple[str, ...]:
    active: list[str] = []
    for slot_type in SLOT_TYPES:
        value = slots.get(slot_type, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigurationError("Runner slot 配置无效")
        if value > 0:
            active.append(slot_type)
    return tuple(active)


def declare_runner_topology(
    channel: object,
    runner_id: str,
    slots: Mapping[str, int],
) -> RunnerTaskTopology:
    """声明 durable topology，并按 slots>0 绑定本机 routing key。"""

    queue = runner_queue_name(runner_id)
    active = active_slot_types(slots)
    bindings = tuple(runner_routing_key(runner_id, slot_type) for slot_type in active)
    channel.exchange_declare(
        exchange=TASK_EXCHANGE,
        exchange_type=TASK_EXCHANGE_TYPE,
        durable=True,
    )
    channel.exchange_declare(
        exchange=DEAD_LETTER_EXCHANGE,
        exchange_type=TASK_EXCHANGE_TYPE,
        durable=True,
    )
    channel.queue_declare(queue=DEAD_QUEUE, durable=True)
    channel.queue_bind(exchange=DEAD_LETTER_EXCHANGE, queue=DEAD_QUEUE, routing_key="#")
    channel.queue_declare(
        queue=queue,
        durable=True,
        arguments={"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE},
    )
    for binding in bindings:
        channel.queue_bind(exchange=TASK_EXCHANGE, queue=queue, routing_key=binding)
    return RunnerTaskTopology(runner_id, queue, bindings)


def _validate_runner_id(runner_id: str) -> None:
    if not isinstance(runner_id, str) or not _RUNNER_ID_PATTERN.fullmatch(runner_id):
        raise ConfigurationError("Runner runner_id 格式无效")
