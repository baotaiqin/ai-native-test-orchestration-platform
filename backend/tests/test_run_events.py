from typing import Any

import pytest

from app.infrastructure.redis.client import RedisRunEventStream


class FakePipeline:
    def __init__(self) -> None:
        self.xadd_call: tuple[str, dict[str, str], int, bool] | None = None
        self.expire_call: tuple[str, int] | None = None

    def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> None:
        self.xadd_call = (key, fields, maxlen, approximate)

    def expire(self, key: str, ttl_seconds: int) -> None:
        self.expire_call = (key, ttl_seconds)

    def execute(self) -> list[Any]:
        return ["1-0", True]


class FakeRedis:
    def __init__(self) -> None:
        self.pipeline_instance = FakePipeline()
        self.entries: list[tuple[str, dict[str, str]]] = []

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return self.pipeline_instance

    def xrange(
        self, key: str, *, min: str, count: int
    ) -> list[tuple[str, dict[str, str]]]:
        assert key == "ai-test:run-events:project:7:run:run_1"
        assert min == "(0-0"
        return self.entries[:count]


def test_run_event_stream_uses_bounded_maxlen_and_ttl() -> None:
    client = FakeRedis()
    stream = RedisRunEventStream(
        client,
        key_prefix="ai-test",
        maxlen=100,
        ttl_seconds=300,
    )

    event_id = stream.append_event(
        7,
        "run_1",
        {"schema_version": 1, "event_type": "RUN_CREATED", "case_run_id": None},
    )

    assert event_id == "1-0"
    assert client.pipeline_instance.xadd_call == (
        "ai-test:run-events:project:7:run:run_1",
        {"schema_version": "1", "event_type": "RUN_CREATED", "case_run_id": ""},
        100,
        True,
    )
    assert client.pipeline_instance.expire_call == (
        "ai-test:run-events:project:7:run:run_1",
        300,
    )

    client.entries = [("1-0", {"event_type": "RUN_CREATED"})]
    assert stream.read_events(7, "run_1", after_id="0-0", limit=10) == client.entries


def test_run_event_stream_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError):
        RedisRunEventStream(FakeRedis(), key_prefix="ai-test", maxlen=99, ttl_seconds=300)
    with pytest.raises(ValueError):
        RedisRunEventStream(FakeRedis(), key_prefix="ai-test", maxlen=100, ttl_seconds=299)
