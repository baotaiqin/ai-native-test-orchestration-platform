import io
import json
import logging
from urllib.parse import quote

import pytest

from app.core.logging import JsonFormatter
from app.core.redaction import (
    CYCLE,
    REDACTED,
    TRUNCATED,
    redact_text,
    redact_value,
)

CANARY = "synthetic_logging_secret_92f3"
SECOND_CANARY = "synthetic_bearer_secret_b614"


class DangerousDiagnostic:
    def __str__(self) -> str:
        raise AssertionError("untrusted __str__ must not run")

    def __repr__(self) -> str:
        raise AssertionError("untrusted __repr__ must not run")


def test_redact_value_masks_nested_keys_json_strings_and_header_pairs() -> None:
    nested_json = json.dumps(
        {
            "apiKey": CANARY,
            "ordinary": "kept",
            "nested": json.dumps({"cookie": SECOND_CANARY, "count": 3}),
        }
    )
    source = {
        "accessToken": f"{CANARY} with spaces\nand a second line",
        "secret-value": SECOND_CANARY,
        "input_token": 17,
        "outputToken": 9,
        "total-token": 26,
        "headers": [
            {"name": "Authorization", "value": f"Bearer {CANARY}"},
            {"name": "X-Request-ID", "value": "request-123"},
        ],
        "payload": nested_json,
        "ordinary": {"run_id": "run_123", "success": True},
    }

    displayed = redact_value(source)
    serialized = json.dumps(displayed, ensure_ascii=False)

    assert CANARY not in serialized
    assert SECOND_CANARY not in serialized
    assert displayed["accessToken"] == REDACTED
    assert displayed["secret-value"] == REDACTED
    assert displayed["input_token"] == 17
    assert displayed["outputToken"] == 9
    assert displayed["total-token"] == 26
    assert displayed["headers"][0]["value"] == REDACTED
    assert displayed["headers"][1]["value"] == "request-123"
    parsed_payload = json.loads(displayed["payload"])
    assert parsed_payload["apiKey"] == REDACTED
    assert parsed_payload["ordinary"] == "kept"
    assert json.loads(parsed_payload["nested"])["cookie"] == REDACTED
    assert source["accessToken"].startswith(CANARY)
    assert json.loads(source["payload"])["apiKey"] == CANARY


def test_redact_text_masks_assignments_bearer_and_sensitive_urls() -> None:
    text = (
        f'password="{CANARY} with spaces\nand newline"; '
        f"Authorization: Bearer {SECOND_CANARY}\n"
        f"url=https://user:{CANARY}@example.test/path?accessToken="
        f"{SECOND_CANARY}&page=2 credential={CANARY} with spaces; "
        f"client secret='{SECOND_CANARY} unterminated; code=E_TIMEOUT"
    )

    displayed = redact_text(text)

    assert CANARY not in displayed
    assert SECOND_CANARY not in displayed
    assert displayed.count(REDACTED) >= 4
    assert "example.test/path" in displayed
    assert "page=2" in displayed
    assert "code=E_TIMEOUT" in displayed


@pytest.mark.parametrize(
    "scheme",
    [
        "http",
        "https",
        "mysql+pymysql",
        "postgresql+psycopg",
        "amqp",
        "amqps",
        "redis",
        "rediss",
    ],
)
def test_redact_text_masks_generic_uri_credentials_and_sensitive_query(
    scheme: str,
) -> None:
    encoded_password = quote(f"{CANARY}:part@two", safe="")
    source = (
        f"{scheme}://service:{encoded_password}@[2001:db8::1]:1234/resource"
        f"?X-Amz-Signature={SECOND_CANARY}&page=2"
    )

    displayed = redact_text(source)

    assert CANARY not in displayed
    assert SECOND_CANARY not in displayed
    assert displayed.startswith(f"{scheme}://{REDACTED}@[2001:db8::1]:1234/")
    assert "X-Amz-Signature=<redacted>" in displayed
    assert displayed.endswith("&page=2")


def test_redact_text_preserves_ordinary_uri_and_fails_closed_on_malformed_dsn() -> None:
    ordinary = (
        "postgresql://[2001:db8::1]:5432/database"
        "?application_name=runner&sslmode=require"
    )
    malformed = f"redis://service:{CANARY}@[broken/service"
    invalid_port = "redis://cache:not-a-port/0"

    assert redact_text(ordinary) == ordinary
    assert redact_text("redis://[2001:db8::1]") == "redis://[2001:db8::1]"
    assert redact_text(malformed) == REDACTED
    assert redact_text(invalid_port) == REDACTED


def test_redaction_is_bounded_cycle_safe_and_never_reprs_unknown_objects() -> None:
    cyclic: dict[str, object] = {"run_id": "run_123"}
    cyclic["self"] = cyclic
    cyclic["items"] = list(range(100))
    cyclic["unknown"] = DangerousDiagnostic()
    cyclic["x" * 200] = CANARY
    cyclic["long"] = "x" * 50_000 + f" password={CANARY}"

    displayed = redact_value(cyclic)
    serialized = json.dumps(displayed, ensure_ascii=False)

    assert displayed["self"] == CYCLE
    assert len(displayed["items"]) == 65
    assert displayed["items"][-1] == TRUNCATED
    assert displayed["unknown"] == "<unsupported:DangerousDiagnostic>"
    assert CANARY not in serialized
    assert len(displayed["long"]) <= 16_384


def test_json_formatter_redacts_message_args_extra_and_exception_via_handler() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("privacy-test-logger")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.warning(
            "authorization=Bearer %s run_id=%s",
            SECOND_CANARY,
            "run_123",
            extra={
                "request_id": "request-123",
                "details": {
                    "clientSecret": CANARY,
                    "nested": json.dumps({"password": SECOND_CANARY}),
                },
                "API Key": CANARY,
                "broker_dsn": f"amqps://worker:{SECOND_CANARY}@mq.test/vhost",
                "unknown": DangerousDiagnostic(),
            },
        )
        try:
            raise RuntimeError(f'password="{CANARY} with spaces"')
        except RuntimeError:
            logger.exception(
                "call_id=%s failed cookie=%s", 20, SECOND_CANARY
            )
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    output = stream.getvalue()
    records = [json.loads(line) for line in output.splitlines()]

    assert len(records) == 2
    assert CANARY not in output
    assert SECOND_CANARY not in output
    assert records[0]["message"].endswith("run_id=run_123")
    assert records[0]["request_id"] == "request-123"
    assert records[0]["details"]["clientSecret"] == REDACTED
    assert records[0]["API Key"] == REDACTED
    assert records[0]["broker_dsn"] == f"amqps://{REDACTED}@mq.test/vhost"
    assert records[0]["unknown"] == "<unsupported:DangerousDiagnostic>"
    assert records[0]["source"]["file"] == "test_logging_redaction.py"
    assert isinstance(records[0]["source"]["line"], int)
    assert records[1]["message"].startswith("call_id=20 failed")
    assert records[1]["exception"]["type"] == "RuntimeError"
    assert records[1]["exception"]["message"].startswith("password=")
    assert records[1]["exception"]["frames"]
    assert records[1]["exception"]["frames"][-1]["file"] == (
        "test_logging_redaction.py"
    )
