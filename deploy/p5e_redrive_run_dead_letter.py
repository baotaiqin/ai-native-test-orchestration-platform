"""Inspect or explicitly redrive one approved P5-E dead-lettered Run.

Inspection is the default. Execution requires explicit expected metadata and keeps
the original JSON bytes/message ID. No SQL state is changed by this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import pika
from sqlalchemy import select

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
RUNNER_ROOT = WORKSPACE_ROOT / "runner"
sys.path.insert(0, str(RUNNER_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.infrastructure.db.session import SessionLocal
from app.modules.runners.models import Runner
from app.modules.runners.security import matches_digest
from app.modules.runs.models import RunDispatchOutbox, TestRun
from app.modules.runs.service import _validate_dispatch_payload
from runner.config import default_state_dir
from runner.envelope import parse_task_or_recording_envelope
from runner.state import RunnerStateStore

TARGET_RUN_ID = "run_15c59b0b27964dd8a6fec2fcca840f9c"
TASK_EXCHANGE = "ai_test.tasks.v1"
DEAD_QUEUE = "ai_test.tasks.dead.v1"
MAX_DEAD_LETTERS_TO_INSPECT = 20
RUN_CONFIGURATION = WORKSPACE_ROOT / ".run" / "Runner Worker.run.xml"
JOURNAL_PATH = (
    WORKSPACE_ROOT
    / ".codex-validation"
    / "p5e-services"
    / f"redrive-{TARGET_RUN_ID}.json"
)


@dataclass(frozen=True)
class DatabasePreflight:
    runner_id: str
    message_id: str
    routing_key: str
    payload: dict[str, Any]
    ready: bool
    checks: dict[str, bool]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Publish-confirm the exact original body, then ACK only its dead letter.",
    )
    parser.add_argument("--expected-message-id")
    parser.add_argument("--expected-death-time")
    return parser.parse_args()


def _broker_url() -> str:
    root = ElementTree.parse(RUN_CONFIGURATION).getroot()
    for node in root.findall(".//env"):
        if node.attrib.get("name") != "AI_TEST_RABBITMQ_URL":
            continue
        value = node.attrib.get("value", "")
        parsed = urlparse(value)
        virtual_host = unquote(parsed.path.removeprefix("/"))
        if (
            parsed.scheme in {"amqp", "amqps"}
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == 5672
            and virtual_host in {"", "/"}
        ):
            return value
    raise RuntimeError("approved local broker configuration unavailable")


def _database_preflight() -> DatabasePreflight:
    identity = RunnerStateStore(default_state_dir()).load_identity()
    with SessionLocal() as session:
        run = session.get(TestRun, TARGET_RUN_ID)
        outbox = session.scalar(
            select(RunDispatchOutbox).where(RunDispatchOutbox.run_id == TARGET_RUN_ID)
        )
        runner = session.get(Runner, identity.runner_id)
        if run is None or outbox is None or runner is None:
            raise LookupError("control-plane record")
        envelope = _validate_dispatch_payload(run, outbox)
        checks = {
            "runner_id_matches": run.runner_id == identity.runner_id,
            "credential_digest_matches": matches_digest(
                identity.credential,
                runner.credential_digest,
            ),
            "run_status_queued": run.status == "QUEUED",
            "outbox_status_published": outbox.status == "PUBLISHED",
            "outbox_unclaimed": outbox.claimed_runner_id is None
            and outbox.claimed_at is None,
            "dispatch_payload_valid": envelope is not None,
            "message_id_matches_payload": envelope is not None
            and envelope.message_id == outbox.message_id,
            "web_route_matches": run.run_type == "WEB_CASE"
            and run.required_slot_type == "WEB"
            and outbox.routing_key
            == f"runner.{identity.runner_id}.web",
        }
        payload = outbox.payload
        if not isinstance(payload, dict):
            raise TypeError("dispatch payload")
        return DatabasePreflight(
            runner_id=identity.runner_id,
            message_id=outbox.message_id,
            routing_key=outbox.routing_key,
            payload=dict(payload),
            ready=all(checks.values()),
            checks=checks,
        )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _death_metadata(properties: pika.BasicProperties) -> dict[str, Any]:
    headers = properties.headers or {}
    deaths = headers.get("x-death", headers.get(b"x-death", []))
    reasons: set[str] = set()
    queues: set[str] = set()
    routing_keys: set[str] = set()
    times: set[str] = set()
    total_count = 0
    for death in deaths if isinstance(deaths, list) else []:
        if not isinstance(death, dict):
            continue
        normalized = {_text(key): value for key, value in death.items()}
        if normalized.get("reason") is not None:
            reasons.add(_text(normalized["reason"]))
        if normalized.get("queue") is not None:
            queues.add(_text(normalized["queue"]))
        raw_count = normalized.get("count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            total_count += raw_count
        raw_time = normalized.get("time")
        if raw_time is not None:
            times.add(
                raw_time.isoformat()
                if hasattr(raw_time, "isoformat")
                else _text(raw_time)
            )
        for routing_key in normalized.get("routing-keys", []):
            routing_keys.add(_text(routing_key))
    return {
        "reasons": sorted(reasons),
        "queues": sorted(queues),
        "routing_keys": sorted(routing_keys),
        "times": sorted(times),
        "count": total_count,
    }


def _atomic_write_journal(payload: dict[str, Any]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = JOURNAL_PATH.with_suffix(f".tmp-{os.getpid()}.json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, JOURNAL_PATH)


def _journal_payload(
    *, state: str, preflight: DatabasePreflight, death_time: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "state": state,
        "run_id": TARGET_RUN_ID,
        "message_id": preflight.message_id,
        "routing_key": preflight.routing_key,
        "x_death_time": death_time,
        "note": "No message body or connection configuration is stored here.",
    }


def main() -> int:
    args = _parse_args()
    if args.execute and (
        not args.expected_message_id or not args.expected_death_time
    ):
        print(json.dumps({"ok": False, "error_type": "ExecutionGuardMissing"}))
        return 2
    if not args.execute and (
        args.expected_message_id is not None or args.expected_death_time is not None
    ):
        print(json.dumps({"ok": False, "error_type": "InspectModeGuardConflict"}))
        return 2
    if args.execute and JOURNAL_PATH.exists():
        print(json.dumps({"ok": False, "error_type": "ExistingRecoveryJournal"}))
        return 2

    connection: pika.BlockingConnection | None = None
    channel: Any = None
    outstanding: set[int] = set()
    target_tag: int | None = None
    target_body: bytes | None = None
    preflight: DatabasePreflight | None = None
    safe_result: dict[str, Any] = {"ok": False}
    exit_code = 2
    publish_attempted = False
    publish_confirmed = False
    source_ack_sent = False
    source_requeue_requested = False
    try:
        preflight = _database_preflight()
        parameters = pika.URLParameters(_broker_url())
        if parameters.virtual_host != "/":
            raise RuntimeError("unexpected broker virtual host")
        parameters.socket_timeout = 5
        parameters.stack_timeout = 10
        parameters.blocked_connection_timeout = 5
        parameters.heartbeat = 30
        parameters.connection_attempts = 2
        parameters.retry_delay = 0.5
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.exchange_declare(exchange=TASK_EXCHANGE, passive=True)
        target_queue = f"ai_test.runner.{preflight.runner_id}.v1"
        channel.queue_declare(queue=target_queue, passive=True)
        dead_state = channel.queue_declare(queue=DEAD_QUEUE, passive=True)
        ready_count = int(dead_state.method.message_count)
        if ready_count > MAX_DEAD_LETTERS_TO_INSPECT:
            raise RuntimeError("dead queue exceeds the bounded inspection limit")

        target_matches: list[tuple[int, bytes, dict[str, Any]]] = []
        for _ in range(ready_count):
            method, properties, body = channel.basic_get(
                queue=DEAD_QUEUE,
                auto_ack=False,
            )
            if method is None:
                break
            delivery_tag = int(method.delivery_tag)
            outstanding.add(delivery_tag)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(payload, dict) or payload.get("run_id") != TARGET_RUN_ID:
                continue
            metadata = _death_metadata(properties)
            complete_envelope_match = payload == preflight.payload
            protocol_valid = True
            try:
                envelope = parse_task_or_recording_envelope(
                    body,
                    expected_runner_id=preflight.runner_id,
                )
            except Exception:  # noqa: BLE001 - only a safe boolean is emitted
                protocol_valid = False
                envelope = None
            metadata.update(
                {
                    "message_id": payload.get("message_id"),
                    "property_message_id": properties.message_id,
                    "complete_envelope_match": complete_envelope_match,
                    "protocol_valid": protocol_valid,
                    "run_type": getattr(envelope, "run_type", None),
                    "required_slot_type": getattr(
                        envelope,
                        "required_slot_type",
                        None,
                    ),
                }
            )
            target_matches.append((delivery_tag, body, metadata))

        for delivery_tag in tuple(outstanding):
            if any(match[0] == delivery_tag for match in target_matches):
                continue
            channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
            outstanding.remove(delivery_tag)

        if len(target_matches) != 1:
            raise RuntimeError("exact dead letter match count is not one")
        target_tag, target_body, metadata = target_matches[0]
        death_time = metadata["times"][0] if len(metadata["times"]) == 1 else ""
        expected_queue = f"ai_test.runner.{preflight.runner_id}.v1"
        recovery_checks = {
            "database_preflight_ready": preflight.ready,
            "body_message_id_matches_database": metadata["message_id"]
            == preflight.message_id,
            "property_message_id_matches_database": metadata["property_message_id"]
            == preflight.message_id,
            "complete_envelope_matches_database": metadata["complete_envelope_match"]
            is True,
            "runner_protocol_valid": metadata["protocol_valid"] is True,
            "rejected_from_expected_queue": metadata["reasons"] == ["rejected"]
            and metadata["queues"] == [expected_queue],
            "expected_web_routing_key": metadata["routing_keys"]
            == [preflight.routing_key],
            "single_death_time": bool(death_time),
        }
        ready_for_redrive = all(recovery_checks.values())
        safe_result = {
            "ok": True,
            "mode": "execute" if args.execute else "inspect",
            "run_id": TARGET_RUN_ID,
            "message_id": preflight.message_id,
            "x_death_reason": metadata["reasons"],
            "x_death_count": metadata["count"],
            "x_death_time": death_time,
            "dead_queue_ready_count": ready_count,
            "target_match_count": len(target_matches),
            "ready_for_redrive": ready_for_redrive,
            "checks": {**preflight.checks, **recovery_checks},
            "publish_attempted": publish_attempted,
            "publish_confirmed": publish_confirmed,
            "source_ack_sent": source_ack_sent,
        }
        if not args.execute:
            channel.basic_nack(delivery_tag=target_tag, requeue=True)
            outstanding.remove(target_tag)
            source_requeue_requested = True
            exit_code = 0 if ready_for_redrive else 2
        else:
            if (
                not ready_for_redrive
                or args.expected_message_id != preflight.message_id
                or args.expected_death_time != death_time
            ):
                raise RuntimeError("execution guards do not match safe metadata")
            fresh_preflight = _database_preflight()
            fresh_matches = (
                fresh_preflight.ready
                and fresh_preflight.runner_id == preflight.runner_id
                and fresh_preflight.message_id == preflight.message_id
                and fresh_preflight.routing_key == preflight.routing_key
                and fresh_preflight.payload == preflight.payload
                and json.loads(target_body.decode("utf-8"))
                == fresh_preflight.payload
            )
            safe_result["checks"]["fresh_database_preflight_matches"] = fresh_matches
            if not fresh_matches:
                raise RuntimeError("database preflight changed before publish")
            preflight = fresh_preflight
            _atomic_write_journal(
                _journal_payload(
                    state="intent_written",
                    preflight=preflight,
                    death_time=death_time,
                )
            )
            channel.confirm_delivery()
            publish_attempted = True
            accepted = channel.basic_publish(
                exchange=TASK_EXCHANGE,
                routing_key=preflight.routing_key,
                body=target_body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    message_id=preflight.message_id,
                ),
                mandatory=True,
            )
            if accepted is False:
                raise RuntimeError("broker did not confirm republish")
            publish_confirmed = True
            _atomic_write_journal(
                _journal_payload(
                    state="publish_confirmed",
                    preflight=preflight,
                    death_time=death_time,
                )
            )
            source_ack_sent = True
            channel.basic_ack(delivery_tag=target_tag)
            outstanding.remove(target_tag)
            _atomic_write_journal(
                _journal_payload(
                    state="source_acked",
                    preflight=preflight,
                    death_time=death_time,
                )
            )
            safe_result["publish_attempted"] = publish_attempted
            safe_result["publish_confirmed"] = publish_confirmed
            safe_result["source_ack_sent"] = source_ack_sent
            exit_code = 0
    except Exception as exc:  # noqa: BLE001 - never serialize body, URL, or message
        safe_result = {
            "ok": False,
            "mode": "execute" if args.execute else "inspect",
            "run_id": TARGET_RUN_ID,
            "error_type": type(exc).__name__,
            "publish_attempted": publish_attempted,
            "publish_confirmed": publish_confirmed,
            "source_ack_sent": source_ack_sent,
        }
        exit_code = 2
    finally:
        if channel is not None and getattr(channel, "is_open", False):
            for delivery_tag in sorted(outstanding):
                try:
                    channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
                    source_requeue_requested = True
                except Exception:  # noqa: BLE001, S110 - close also requeues unacked data
                    pass
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except Exception:  # noqa: BLE001, S110 - no unsafe recovery follows close
                pass

    if source_ack_sent:
        safe_result["source_state"] = "ack_sent"
        safe_result["source_preserved"] = None
    elif source_requeue_requested:
        safe_result["source_state"] = "requeue_requested"
        safe_result["source_preserved"] = True
    else:
        safe_result["source_state"] = "not_touched"
        safe_result["source_preserved"] = True

    print(json.dumps(safe_result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
