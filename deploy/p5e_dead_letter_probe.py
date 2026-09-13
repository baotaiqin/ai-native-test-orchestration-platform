"""Inspect only approved metadata from RabbitMQ dead letters and requeue them."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import pika

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT / "runner"))

from runner.envelope import parse_task_or_recording_envelope

RUN_CONFIGURATION = WORKSPACE_ROOT / ".run" / "Runner Worker.run.xml"
DEAD_QUEUE = "ai_test.tasks.dead.v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def _broker_url() -> str:
    root = ElementTree.parse(RUN_CONFIGURATION).getroot()
    for node in root.findall(".//env"):
        if node.attrib.get("name") == "AI_TEST_RABBITMQ_URL":
            value = node.attrib.get("value", "")
            parsed = urlparse(value)
            if (
                parsed.scheme in {"amqp", "amqps"}
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == 5672
            ):
                return value
    raise RuntimeError("approved broker configuration unavailable")


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def main() -> int:
    args = _parse_args()
    connection: pika.BlockingConnection | None = None
    channel: Any = None
    last_delivery_tag: int | None = None
    inspected = 0
    matches: list[dict[str, Any]] = []
    preserved = False
    try:
        connection = pika.BlockingConnection(pika.URLParameters(_broker_url()))
        channel = connection.channel()
        for _ in range(20):
            method, properties, body = channel.basic_get(
                queue=DEAD_QUEUE,
                auto_ack=False,
            )
            if method is None:
                break
            inspected += 1
            last_delivery_tag = int(method.delivery_tag)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(payload, dict) or payload.get("run_id") != args.run_id:
                continue

            envelope_valid = True
            parse_error_type: str | None = None
            try:
                envelope = parse_task_or_recording_envelope(
                    body,
                    expected_runner_id=str(payload.get("runner_id", "")),
                )
                run_type = getattr(envelope, "run_type", None)
                required_slot_type = getattr(envelope, "required_slot_type", None)
            except Exception as exc:  # noqa: BLE001 - emit only the safe type
                envelope_valid = False
                parse_error_type = type(exc).__name__
                run_type = None
                required_slot_type = None

            headers = properties.headers or {}
            deaths = headers.get("x-death", headers.get(b"x-death", []))
            reasons: set[str] = set()
            queues: set[str] = set()
            routing_keys: set[str] = set()
            death_times: set[str] = set()
            for death in deaths if isinstance(deaths, list) else []:
                if not isinstance(death, dict):
                    continue
                normalized = {_text(key): value for key, value in death.items()}
                if normalized.get("reason") is not None:
                    reasons.add(_text(normalized["reason"]))
                if normalized.get("queue") is not None:
                    queues.add(_text(normalized["queue"]))
                if normalized.get("time") is not None:
                    death_time = normalized["time"]
                    death_times.add(
                        death_time.isoformat()
                        if hasattr(death_time, "isoformat")
                        else _text(death_time)
                    )
                for routing_key in normalized.get("routing-keys", []):
                    routing_keys.add(_text(routing_key))
            matches.append(
                {
                    "run_id": args.run_id,
                    "x_death_reasons": sorted(reasons),
                    "x_death_queues": sorted(queues),
                    "routing_keys": sorted(routing_keys),
                    "x_death_times": sorted(death_times),
                    "envelope_valid": envelope_valid,
                    "parse_error_type": parse_error_type,
                    "run_type": run_type,
                    "required_slot_type": required_slot_type,
                }
            )
    except Exception as exc:  # noqa: BLE001 - never serialize the exception message
        result = {"ok": False, "error_type": type(exc).__name__, "preserved": preserved}
        print(json.dumps(result, ensure_ascii=False))
        return 2
    finally:
        if channel is not None and last_delivery_tag is not None:
            try:
                channel.basic_nack(
                    delivery_tag=last_delivery_tag,
                    multiple=True,
                    requeue=True,
                )
                preserved = True
            except Exception:  # noqa: BLE001, S110 - best effort before safe close
                pass
        if connection is not None and connection.is_open:
            connection.close()

    result = {
        "ok": True,
        "queue": DEAD_QUEUE,
        "inspected_count": inspected,
        "target_match_count": len(matches),
        "matches": matches,
        "preserved": preserved,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
