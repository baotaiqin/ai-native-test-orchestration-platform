import json
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import redis
from redis.exceptions import RedisError

from app.core.config import get_settings


class RedisStoreUnavailableError(RuntimeError):
    """Raised when the Redis boundary cannot complete an operation."""


class RedisRunnerHeartbeatStore:
    """Small injectable Redis adapter for ephemeral Runner heartbeat state."""

    def __init__(self, client: redis.Redis, *, key_prefix: str, default_ttl: int) -> None:
        self.client = client
        self.key_prefix = key_prefix.strip(":")
        self.default_ttl = default_ttl

    def key_for(self, runner_id: str) -> str:
        return f"{self.key_prefix}:runner:heartbeat:{runner_id}"

    def set_heartbeat(
        self, runner_id: str, payload: dict[str, Any], *, ttl: int | None = None
    ) -> None:
        heartbeat_ttl = ttl if ttl is not None else self.default_ttl
        try:
            self.client.setex(
                self.key_for(runner_id),
                heartbeat_ttl,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc

    def get_heartbeat(self, runner_id: str) -> dict[str, Any] | None:
        try:
            value = self.client.get(self.key_for(runner_id))
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc
        if value is None:
            return None
        try:
            decoded = value.decode("utf-8") if isinstance(value, bytes) else value
            payload = json.loads(decoded)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RedisStoreUnavailableError from exc
        if not isinstance(payload, dict):
            raise RedisStoreUnavailableError
        return payload

    def delete_heartbeat(self, runner_id: str) -> None:
        try:
            self.client.delete(self.key_for(runner_id))
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc


class RedisRunEventStream:
    """Redis Stream adapter for bounded, project/run-scoped Run events."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        key_prefix: str,
        maxlen: int,
        ttl_seconds: int,
    ) -> None:
        if not 100 <= maxlen <= 100_000:
            raise ValueError("Run event stream MAXLEN must be between 100 and 100000")
        if not 300 <= ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("Run event stream TTL must be between 300 and 2592000 seconds")
        self.client = client
        self.key_prefix = key_prefix.strip(":")
        self.maxlen = maxlen
        self.ttl_seconds = ttl_seconds

    def key_for(self, project_id: int, run_id: str) -> str:
        encoded_run_id = quote(str(run_id), safe="")
        return f"{self.key_prefix}:run-events:project:{project_id}:run:{encoded_run_id}"

    @staticmethod
    def _field_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    @staticmethod
    def _decode(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def append_event(
        self, project_id: int, run_id: str, event: dict[str, Any]
    ) -> str:
        fields = {key: self._field_value(value) for key, value in event.items()}
        key = self.key_for(project_id, run_id)
        try:
            pipeline = self.client.pipeline(transaction=True)
            pipeline.xadd(
                key,
                fields,
                maxlen=self.maxlen,
                approximate=True,
            )
            pipeline.expire(key, self.ttl_seconds)
            results = pipeline.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc
        if not results or results[0] is None:
            raise RedisStoreUnavailableError
        return self._decode(results[0])

    def read_events(
        self,
        project_id: int,
        run_id: str,
        *,
        after_id: str,
        limit: int,
    ) -> list[tuple[str, dict[str, str]]]:
        key = self.key_for(project_id, run_id)
        try:
            entries = self.client.xrange(
                key,
                min=f"({after_id}",
                count=limit,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc
        return [
            (
                self._decode(entry_id),
                {self._decode(field): self._decode(value) for field, value in fields.items()},
            )
            for entry_id, fields in entries
        ]

    def read_events_blocking(
        self,
        project_id: int,
        run_id: str,
        *,
        after_id: str,
        limit: int,
        block_ms: int,
    ) -> list[tuple[str, dict[str, str]]]:
        key = self.key_for(project_id, run_id)
        try:
            batches = self.client.xread(
                {key: after_id},
                count=limit,
                block=block_ms,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisStoreUnavailableError from exc
        if not batches:
            return []
        entries = batches[0][1]
        return [
            (
                self._decode(entry_id),
                {self._decode(field): self._decode(value) for field, value in fields.items()},
            )
            for entry_id, fields in entries
        ]


@lru_cache
def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@lru_cache
def get_runner_heartbeat_store() -> RedisRunnerHeartbeatStore:
    settings = get_settings()
    return RedisRunnerHeartbeatStore(
        get_redis_client(),
        key_prefix=settings.redis_key_prefix,
        default_ttl=settings.runner_heartbeat_ttl_seconds,
    )


@lru_cache
def get_run_event_stream() -> RedisRunEventStream:
    settings = get_settings()
    return RedisRunEventStream(
        get_redis_client(),
        key_prefix=settings.redis_key_prefix,
        maxlen=settings.run_event_stream_maxlen,
        ttl_seconds=settings.run_event_stream_ttl_seconds,
    )
