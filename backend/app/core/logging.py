import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.redaction import redact_exception, redact_text, redact_value


def _safe_log_message(record: logging.LogRecord) -> str:
    if not isinstance(record.msg, str):
        return f"<unsupported-log-message:{type(record.msg).__name__[:64]}>"
    if not record.args:
        return redact_text(record.msg)
    safe_args = redact_value(record.args)
    if isinstance(record.args, Mapping):
        format_args: Any = safe_args if isinstance(safe_args, dict) else {}
    elif isinstance(record.args, tuple):
        format_args = tuple(safe_args) if isinstance(safe_args, list) else ()
    else:
        format_args = safe_args
    try:
        formatted = record.msg % format_args
    except (KeyError, TypeError, ValueError):
        formatted = f"{record.msg} [log arguments unavailable]"
    return redact_text(formatted)


class JsonFormatter(logging.Formatter):
    standard_fields = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", "LOG")
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact_text(event) if isinstance(event, str) else "LOG",
            "message": _safe_log_message(record),
            "source": {
                "module": record.module,
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            },
        }
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self.standard_fields and key not in payload:
                extras[key] = value
        safe_extras = redact_value(extras)
        if isinstance(safe_extras, dict):
            payload.update(safe_extras)
        if record.exc_info:
            payload["exception"] = redact_exception(record.exc_info)
        elif record.exc_text:
            payload["exception"] = {
                "type": "Exception",
                "message": redact_text(record.exc_text),
                "frames": [],
            }
        if record.stack_info:
            payload["stack"] = redact_text(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
