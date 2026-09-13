"""Crash-tolerant spool for completed performance samples awaiting Backend acknowledgement."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from runner.errors import StateError
from runner.isolation import IsolatedOutcome
from runner.state import _atomic_write

_MAX_RESULT_BYTES = 8_000_000


class PerformanceResultStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def load(self, run_id: str, message_id: str) -> IsolatedOutcome | None:
        path = self._path(run_id, message_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateError("性能结果缓存读取失败") from exc
        if len(raw) > _MAX_RESULT_BYTES:
            raise StateError("性能结果缓存超过安全上限")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise StateError("性能结果缓存损坏") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "run_id", "message_id", "result"}
            or value.get("version") != 1
            or value.get("run_id") != run_id
            or value.get("message_id") != message_id
            or not isinstance(value.get("result"), dict)
        ):
            raise StateError("性能结果缓存身份无效")
        result = value["result"]
        if set(result) != {"wall_duration_ms", "samples"}:
            raise StateError("性能结果缓存内容无效")
        wall_duration_ms = result.get("wall_duration_ms")
        samples = result.get("samples")
        if (
            type(wall_duration_ms) is not int
            or not 1 <= wall_duration_ms <= 86_400_000
            or not isinstance(samples, list)
            or not 1 <= len(samples) <= 10_000
            or not all(isinstance(sample, dict) for sample in samples)
        ):
            raise StateError("性能结果缓存范围无效")
        return IsolatedOutcome(kind="PERFORMANCE_RESULT", result=result)

    def save(self, run_id: str, message_id: str, outcome: IsolatedOutcome) -> None:
        if outcome.kind != "PERFORMANCE_RESULT" or not isinstance(outcome.result, Mapping):
            raise StateError("仅允许缓存已完成的性能结果")
        encoded = json.dumps(
            {
                "version": 1,
                "run_id": run_id,
                "message_id": message_id,
                "result": dict(outcome.result),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_RESULT_BYTES:
            raise StateError("性能结果缓存超过安全上限")
        _atomic_write(self._path(run_id, message_id), encoded)

    def delete(self, run_id: str, message_id: str) -> None:
        try:
            self._path(run_id, message_id).unlink(missing_ok=True)
        except OSError as exc:
            raise StateError("性能结果缓存清理失败") from exc

    def _path(self, run_id: str, message_id: str) -> Path:
        digest = sha256(f"{run_id}\0{message_id}".encode()).hexdigest()
        return self.directory / f"{digest}.json"
