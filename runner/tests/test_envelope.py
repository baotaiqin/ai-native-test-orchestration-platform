import json
from datetime import UTC, datetime

import pytest

from runner.envelope import MAX_ENVELOPE_BYTES, parse_task_envelope
from runner.errors import EnvelopeError


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "message_id": "message-001",
        "run_id": "run-001",
        "runner_id": "runner-001",
        "run_type": "API_CASE",
        "required_slot_type": "API",
        "attempt": 1,
        "enqueued_at": "2026-08-25T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_valid_v1_envelope_is_strictly_parsed() -> None:
    envelope = parse_task_envelope(
        json.dumps(_payload()).encode(),
        expected_runner_id="runner-001",
    )

    assert envelope.message_id == "message-001"
    assert envelope.run_id == "run-001"
    assert envelope.enqueued_at == datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"unknown": "field"},
        {"runner_id": "other-runner"},
        {"run_type": "WEB_CASE"},
        {"required_slot_type": "SQL"},
        {"attempt": 0},
        {"enqueued_at": "2026-08-25T10:00:00"},
    ],
)
def test_invalid_or_unknown_envelope_is_rejected(overrides: dict[str, object]) -> None:
    body = json.dumps(_payload(**overrides)).encode()

    with pytest.raises(EnvelopeError):
        parse_task_envelope(body, expected_runner_id="runner-001")


def test_malformed_and_oversized_payload_are_rejected_without_echoing_body() -> None:
    secret_like = b'{"credential":"credential-value-123456789"'
    with pytest.raises(EnvelopeError) as malformed:
        parse_task_envelope(secret_like)
    assert "credential-value-123456789" not in str(malformed.value)

    oversized = b"{" + b"x" * MAX_ENVELOPE_BYTES
    with pytest.raises(EnvelopeError):
        parse_task_envelope(oversized)


def test_duplicate_json_field_is_rejected() -> None:
    body = (
        b'{"schema_version":1,"message_id":"message-001",'
        b'"message_id":"message-002","run_id":"run-001",'
        b'"runner_id":"runner-001","run_type":"API_CASE",'
        b'"required_slot_type":"API","attempt":1,'
        b'"enqueued_at":"2026-08-25T10:00:00Z"}'
    )

    with pytest.raises(EnvelopeError):
        parse_task_envelope(body)
