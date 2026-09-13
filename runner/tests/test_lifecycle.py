from datetime import UTC, datetime
from threading import Event

from runner.config import RunnerConfig
from runner.lifecycle import HeartbeatService, heartbeat_loop
from runner.models import HeartbeatResult, RunnerIdentity
from runner.slots import SlotState


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def heartbeat(self, identity, snapshot) -> HeartbeatResult:
        self.calls += 1
        return HeartbeatResult(
            runner_id=identity.runner_id,
            status="ACTIVE",
            heartbeat_interval_seconds=30,
            server_time=datetime.now(UTC),
        )


class WakingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.first = Event()
        self.second = Event()

    def heartbeat(self, identity, snapshot) -> HeartbeatResult:
        result = super().heartbeat(identity, snapshot)
        if self.calls == 1:
            self.first.set()
        else:
            self.second.set()
        return result


def test_heartbeat_loop_honors_stop_event_without_real_wait() -> None:
    client = FakeClient()
    stop_event = Event()
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        stop_event.set()

    result = heartbeat_loop(
        client,
        RunnerConfig("https://example.test", "runner-test"),
        RunnerIdentity("runner-001", "credential-value-123456789"),
        lambda: {"name": "runner-test"},
        stop_event,
        sleeper=sleeper,
    )

    assert result.cycles == 1
    assert result.stopped is True
    assert client.calls == 1
    assert sleeps


def test_heartbeat_service_can_be_woken_for_slot_state_change() -> None:
    client = WakingClient()
    stop_event = Event()
    service = HeartbeatService(
        client,
        RunnerConfig("https://example.test", "runner-test"),
        RunnerIdentity("runner-001", "credential-value-123456789"),
        lambda: {"name": "runner-test", "slots": []},
        stop_event,
    )

    service.start()
    assert client.first.wait(1)
    service.request_immediate_heartbeat()
    assert client.second.wait(1)
    service.stop()
    result = service.join()

    assert result.cycles >= 2
    assert result.fatal_kind is None


def test_slot_state_change_requests_immediate_heartbeat() -> None:
    client = WakingClient()
    stop_event = Event()
    service = HeartbeatService(
        client,
        RunnerConfig("https://example.test", "runner-test"),
        RunnerIdentity("runner-001", "credential-value-123456789"),
        lambda: {"name": "runner-test", "slots": []},
        stop_event,
    )
    state = SlotState({"API": 1, "WEB": 0, "PERFORMANCE": 0})
    state.set_on_change(service.request_immediate_heartbeat)

    service.start()
    assert client.first.wait(1)
    lease = state.try_acquire("API")
    assert lease is not None
    assert client.second.wait(1)
    lease.release()
    service.stop()
    service.join()
