"""Run Task Envelope V1 的严格、无 Secret 解析器。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runner.errors import EnvelopeError

MAX_ENVELOPE_BYTES = 64 * 1024
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_ALLOWED_FIELDS = {
    "schema_version",
    "message_id",
    "run_id",
    "runner_id",
    "run_type",
    "required_slot_type",
    "attempt",
    "enqueued_at",
}


@dataclass(frozen=True, repr=False)
class RunTaskEnvelopeV1:
    schema_version: int
    message_id: str
    run_id: str
    runner_id: str
    run_type: str
    required_slot_type: str
    attempt: int
    enqueued_at: datetime

    def __repr__(self) -> str:
        return (
            "RunTaskEnvelopeV1("
            f"message_id={self.message_id!r}, run_id={self.run_id!r}, "
            f"runner_id={self.runner_id!r}, run_type={self.run_type!r}, attempt={self.attempt!r})"
        )


@dataclass(frozen=True, repr=False)
class WebRecordingTaskEnvelopeV1:
    schema_version: int
    task_type: str
    recording_id: str
    message_id: str
    runner_id: str
    project_id: int
    attempt: int
    enqueued_at: datetime

    def __repr__(self) -> str:
        return (
            "WebRecordingTaskEnvelopeV1("
            f"recording_id={self.recording_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, project_id={self.project_id!r}, "
            f"attempt={self.attempt!r})"
        )


@dataclass(frozen=True, repr=False)
class WebExplorationTaskEnvelopeV1:
    schema_version: int
    task_type: str
    exploration_id: str
    message_id: str
    runner_id: str
    project_id: int
    attempt: int
    enqueued_at: datetime

    def __repr__(self) -> str:
        return (
            "WebExplorationTaskEnvelopeV1("
            f"exploration_id={self.exploration_id!r}, message_id={self.message_id!r}, "
            f"runner_id={self.runner_id!r}, project_id={self.project_id!r}, "
            f"attempt={self.attempt!r})"
        )


def parse_task_envelope(
    body: bytes | bytearray,
    *,
    expected_runner_id: str | None = None,
) -> RunTaskEnvelopeV1:
    if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError("任务信封为空、类型无效或超过大小限制")
    try:
        payload = json.loads(
            bytes(body).decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise EnvelopeError("任务信封 JSON 无效") from None
    if not isinstance(payload, dict):
        raise EnvelopeError("任务信封必须是 JSON 对象")
    if set(payload) != _ALLOWED_FIELDS:
        raise EnvelopeError("任务信封字段集合不符合 V1 协议")
    if payload.get("schema_version") != 1 or isinstance(payload.get("schema_version"), bool):
        raise EnvelopeError("任务信封 schema_version 不受支持")
    message_id = _identifier(payload, "message_id", max_length=64)
    run_id = _identifier(payload, "run_id", max_length=128)
    runner_id = _identifier(payload, "runner_id", max_length=64)
    if expected_runner_id is not None and runner_id != expected_runner_id:
        raise EnvelopeError("任务信封 runner_id 与本机身份不一致")
    run_type = payload.get("run_type")
    if run_type not in {"API_CASE", "SCENARIO", "WEB_CASE"}:
        raise EnvelopeError("任务信封 run_type 无效")
    slot_type = payload.get("required_slot_type")
    if slot_type not in {"API", "WEB", "PERFORMANCE"}:
        raise EnvelopeError("任务信封 required_slot_type 无效")
    if (run_type == "WEB_CASE" and slot_type != "WEB") or (
        run_type in {"API_CASE", "SCENARIO"} and slot_type not in {"API", "PERFORMANCE"}
    ):
        raise EnvelopeError("任务信封 required_slot_type 与 run_type 不匹配")
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10:
        raise EnvelopeError("任务信封 attempt 无效")
    enqueued_at = _parse_enqueued_at(payload.get("enqueued_at"))
    return RunTaskEnvelopeV1(
        schema_version=1,
        message_id=message_id,
        run_id=run_id,
        runner_id=runner_id,
        run_type=run_type,
        required_slot_type=slot_type,
        attempt=attempt,
        enqueued_at=enqueued_at,
    )


def parse_web_recording_envelope(
    body: bytes | bytearray,
    *,
    expected_runner_id: str | None = None,
) -> WebRecordingTaskEnvelopeV1:
    """Parse the independent Web Recording envelope without loosening Run V1."""

    payload = _decode_object(body, "录制任务信封")
    allowed = {
        "schema_version",
        "task_type",
        "recording_id",
        "message_id",
        "runner_id",
        "project_id",
        "attempt",
        "enqueued_at",
    }
    if set(payload) != allowed:
        raise EnvelopeError("录制任务信封字段集合不符合 V1 协议")
    if payload.get("schema_version") != 1 or isinstance(payload.get("schema_version"), bool):
        raise EnvelopeError("录制任务信封 schema_version 不受支持")
    if payload.get("task_type") != "WEB_RECORDING":
        raise EnvelopeError("录制任务信封 task_type 无效")
    recording_id = _plain_text(payload, "recording_id", max_length=128)
    message_id = _identifier(payload, "message_id", max_length=64)
    runner_id = _plain_text(payload, "runner_id", max_length=64)
    if expected_runner_id is not None and runner_id != expected_runner_id:
        raise EnvelopeError("录制任务信封 runner_id 与本机身份不一致")
    project_id = payload.get("project_id")
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise EnvelopeError("录制任务信封 project_id 无效")
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10:
        raise EnvelopeError("录制任务信封 attempt 无效")
    return WebRecordingTaskEnvelopeV1(
        1,
        "WEB_RECORDING",
        recording_id,
        message_id,
        runner_id,
        project_id,
        attempt,
        _parse_enqueued_at(payload.get("enqueued_at")),
    )


def parse_web_exploration_envelope(
    body: bytes | bytearray,
    *,
    expected_runner_id: str | None = None,
) -> WebExplorationTaskEnvelopeV1:
    """Parse the independent MCP exploration envelope without loosening Run V1."""

    payload = _decode_object(body, "探索任务信封")
    allowed = {
        "schema_version",
        "task_type",
        "exploration_id",
        "message_id",
        "runner_id",
        "project_id",
        "attempt",
        "enqueued_at",
    }
    if set(payload) != allowed:
        raise EnvelopeError("探索任务信封字段集合不符合 V1 协议")
    if payload.get("schema_version") != 1 or isinstance(payload.get("schema_version"), bool):
        raise EnvelopeError("探索任务信封 schema_version 不受支持")
    if payload.get("task_type") != "WEB_EXPLORATION":
        raise EnvelopeError("探索任务信封 task_type 无效")
    exploration_id = _plain_text(payload, "exploration_id", max_length=128)
    message_id = _identifier(payload, "message_id", max_length=64)
    runner_id = _plain_text(payload, "runner_id", max_length=64)
    if expected_runner_id is not None and runner_id != expected_runner_id:
        raise EnvelopeError("探索任务信封 runner_id 与本机身份不一致")
    project_id = payload.get("project_id")
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise EnvelopeError("探索任务信封 project_id 无效")
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10:
        raise EnvelopeError("探索任务信封 attempt 无效")
    return WebExplorationTaskEnvelopeV1(
        1,
        "WEB_EXPLORATION",
        exploration_id,
        message_id,
        runner_id,
        project_id,
        attempt,
        _parse_enqueued_at(payload.get("enqueued_at")),
    )


def parse_task_or_recording_envelope(
    body: bytes | bytearray,
    *,
    expected_runner_id: str | None = None,
) -> RunTaskEnvelopeV1 | WebRecordingTaskEnvelopeV1 | WebExplorationTaskEnvelopeV1:
    """Dispatch only the explicit recording task type to its strict union branch."""

    payload = _decode_object(body, "任务信封")
    if payload.get("task_type") == "WEB_RECORDING":
        return parse_web_recording_envelope(body, expected_runner_id=expected_runner_id)
    if payload.get("task_type") == "WEB_EXPLORATION":
        return parse_web_exploration_envelope(body, expected_runner_id=expected_runner_id)
    return parse_task_envelope(body, expected_runner_id=expected_runner_id)


def _identifier(payload: dict[str, Any], field_name: str, *, max_length: int) -> str:
    value = payload.get(field_name)
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= max_length
        or not _IDENTIFIER_PATTERN.fullmatch(value)
    ):
        raise EnvelopeError(f"任务信封 {field_name} 无效")
    return value


def _plain_text(payload: dict[str, Any], field_name: str, *, max_length: int) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise EnvelopeError(f"任务信封 {field_name} 无效")
    return value


def _decode_object(body: bytes | bytearray, label: str) -> dict[str, Any]:
    if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError(f"{label}为空、类型无效或超过大小限制")
    try:
        payload = json.loads(
            bytes(body).decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise EnvelopeError(f"{label} JSON 无效") from None
    if not isinstance(payload, dict):
        raise EnvelopeError(f"{label}必须是 JSON 对象")
    return payload


def _parse_enqueued_at(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or not _DATETIME_PATTERN.fullmatch(value):
        raise EnvelopeError("任务信封 enqueued_at 格式无效")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EnvelopeError("任务信封 enqueued_at 格式无效") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvelopeError("任务信封 enqueued_at 必须包含时区")
    return parsed


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")
