import json

import pytest

from runner.cli import main
from runner.config import RunnerConfig
from runner.errors import ProtectionError, TransportError
from runner.models import HttpResponse, RunnerIdentity
from runner.protection import MemoryProtector
from runner.state import RunnerStateStore
from tests.helpers import FakeTransport


def test_cli_register_and_heartbeat_once_use_encrypted_identity(tmp_path) -> None:
    token = "registration-token-123456"
    credential = "credential-value-123456789"
    register_response = HttpResponse(
        201,
        {
            "runner_id": "runner-001",
            "credential": credential,
            "heartbeat_interval_seconds": 30,
        },
    )
    heartbeat_response = HttpResponse(
        200,
        {
            "runner_id": "runner-001",
            "status": "ACTIVE",
            "heartbeat_interval_seconds": 30,
            "server_time": "2026-08-22T12:00:00+00:00",
        },
    )
    output: list[str] = []
    state_dir = str(tmp_path)

    assert (
        main(
            [
                "--state-dir",
                state_dir,
                "register",
                "--backend-url",
                "https://example.test/",
                "--name",
                "runner-test",
            ],
            protector=MemoryProtector(),
            transport=FakeTransport(register_response),
            get_token=lambda: token,
            output=output.append,
        )
        == 0
    )
    assert token not in "".join(output)
    assert credential not in "".join(output)

    heartbeat_output: list[str] = []
    assert (
        main(
            ["--state-dir", state_dir, "heartbeat-once"],
            protector=MemoryProtector(),
            transport=FakeTransport(heartbeat_response),
            output=heartbeat_output.append,
        )
        == 0
    )
    result = json.loads(heartbeat_output[0])
    assert result["runner_id"] == "runner-001"
    assert token not in heartbeat_output[0]
    assert credential not in heartbeat_output[0]


def test_cli_inspect_is_json_only(tmp_path) -> None:
    output: list[str] = []

    assert (
        main(
            ["--state-dir", str(tmp_path), "inspect", "--name", "runner-test"],
            output=output.append,
        )
        == 0
    )
    payload = json.loads(output[0])
    assert payload["name"] == "runner-test"


def test_cli_register_preflight_failure_does_not_prompt_or_send_token(tmp_path) -> None:
    class FailingProtector:
        def protect(self, value: bytes) -> bytes:
            raise ProtectionError("DPAPI system error 2")

        def unprotect(self, value: bytes) -> bytes:
            raise AssertionError("unprotect 不应被调用")

    token_prompted = False

    def get_token() -> str:
        nonlocal token_prompted
        token_prompted = True
        return "registration-token-123456"

    transport = FakeTransport()
    errors: list[str] = []
    result = main(
        [
            "--state-dir",
            str(tmp_path),
            "register",
            "--backend-url",
            "https://example.test",
            "--name",
            "runner-test",
        ],
        protector=FailingProtector(),
        transport=transport,
        get_token=get_token,
        error=errors.append,
    )

    assert result != 0
    assert token_prompted is False
    assert transport.calls == []
    assert errors and "DPAPI" in errors[0]


def test_cli_register_atomic_preflight_failure_does_not_prompt_or_send_token(
    tmp_path, monkeypatch
) -> None:
    from runner import state as state_module

    original_atomic_write = state_module._atomic_write
    atomic_calls = 0

    def fail_preflight_atomic_write(path, content):
        nonlocal atomic_calls
        atomic_calls += 1
        if atomic_calls == 1:
            return original_atomic_write(path, content)
        raise OSError("atomic write unavailable")

    monkeypatch.setattr(state_module, "_atomic_write", fail_preflight_atomic_write)
    token_prompted = False

    def get_token() -> str:
        nonlocal token_prompted
        token_prompted = True
        return "registration-token-123456"

    transport = FakeTransport()
    errors: list[str] = []
    result = main(
        [
            "--state-dir",
            str(tmp_path),
            "register",
            "--backend-url",
            "https://example.test",
            "--name",
            "runner-test",
        ],
        protector=MemoryProtector(),
        transport=transport,
        get_token=get_token,
        error=errors.append,
    )

    assert result != 0
    assert token_prompted is False
    assert transport.calls == []
    assert errors and "原子" in errors[0]


def test_cli_doctor_success_and_failure_are_json_without_identity(tmp_path) -> None:
    success_output: list[str] = []
    assert (
        main(
            ["--state-dir", str(tmp_path / "success"), "doctor"],
            protector=MemoryProtector(),
            output=success_output.append,
        )
        == 0
    )
    success = json.loads(success_output[0])
    assert success["ok"] is True
    assert success["dpapi"]["ok"] is True
    assert success["atomic_write"]["ok"] is True
    assert not list((tmp_path / "success").glob("identity*"))

    class FailingProtector:
        def protect(self, value: bytes) -> bytes:
            raise ProtectionError("DPAPI system error 2")

        def unprotect(self, value: bytes) -> bytes:
            raise AssertionError("unprotect 不应被调用")

    failure_output: list[str] = []
    assert (
        main(
            ["--state-dir", str(tmp_path / "failure"), "doctor"],
            protector=FailingProtector(),
            output=failure_output.append,
        )
        != 0
    )
    failure = json.loads(failure_output[0])
    assert failure["ok"] is False
    assert failure["dpapi"]["ok"] is False
    assert failure["atomic_write"]["ok"] is True
    assert "DPAPI" in failure["dpapi"]["error"]
    assert not list((tmp_path / "failure").glob("identity*"))


def test_cli_doctor_atomic_failure_is_reported_independently(tmp_path, monkeypatch) -> None:
    from runner import state as state_module

    def fail_atomic_write(path, content):
        raise OSError("atomic write unavailable")

    monkeypatch.setattr(state_module, "_atomic_write", fail_atomic_write)
    output: list[str] = []

    assert (
        main(
            ["--state-dir", str(tmp_path / "atomic-failure"), "doctor"],
            protector=MemoryProtector(),
            output=output.append,
        )
        == 2
    )
    report = json.loads(output[0])
    assert report["ok"] is False
    assert report["dpapi"]["ok"] is True
    assert report["atomic_write"]["ok"] is False
    assert "原子" in report["atomic_write"]["error"]
    failure_dir = tmp_path / "atomic-failure"
    assert not failure_dir.exists() or list(failure_dir.iterdir()) == []
    assert not list((tmp_path / "atomic-failure").glob("identity*"))


def test_cli_run_handles_ctrl_c_without_traceback(tmp_path, monkeypatch) -> None:
    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_config(RunnerConfig("https://example.test", "runner-test"))
    store.save_identity(RunnerIdentity("runner-001", "credential-value-123456789"))

    def raise_keyboard_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("runner.cli.heartbeat_loop", raise_keyboard_interrupt)
    output: list[str] = []

    assert (
        main(
            ["--state-dir", str(tmp_path), "run"],
            protector=MemoryProtector(),
            transport=FakeTransport(),
            output=output.append,
        )
        == 0
    )
    assert output == ["收到停止信号，Runner 已安全停止。"]


def test_cli_worker_outputs_only_safe_statistics(tmp_path, monkeypatch) -> None:
    from runner import cli as cli_module
    from runner.worker import WorkerRunResult, WorkerStats, WorkerStopReason

    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_config(
        RunnerConfig(
            "https://example.test",
            "runner-test",
            api_slots=2,
            web_slots=3,
            performance_slots=4,
        )
    )
    credential = "credential-value-123456789"
    store.save_identity(RunnerIdentity("runner-001", credential))

    class FakeConnection:
        def channel(self):
            return object()

        def close(self):
            pass

    class FakeResource:
        def close(self):
            pass

    boundary_calls: list[str] = []
    captured: dict[str, object] = {}

    class FakeBoundary:
        def __init__(self, stop_event):
            self.stop_event = stop_event

        def install(self):
            boundary_calls.append("install")

        def restore(self):
            boundary_calls.append("restore")

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            del kwargs
            captured["consumer"] = args[0]
            captured["heartbeat"] = args[1]
            self.resources = args[3]

        def run(self):
            return WorkerRunResult(
                WorkerStats(
                    heartbeat_count=2,
                    heartbeat_transient_failures=1,
                    ack_count=3,
                    requeued_count=1,
                    rejected_count=2,
                    empty_count=4,
                ),
                WorkerStopReason.STOP_REQUESTED,
            )

        def close(self):
            for resource in (
                self.resources.executor,
                self.resources.heartbeat_transport,
                self.resources.task_transport,
                self.resources.connection,
            ):
                resource.close()

    monkeypatch.setattr(cli_module, "RunnerWorker", FakeWorker)
    monkeypatch.setattr(cli_module, "SignalBoundary", FakeBoundary)
    args = cli_module.build_parser().parse_args(
        [
            "worker",
            "--state-dir",
            str(tmp_path),
            "--rabbitmq-url",
            "amqps://runner:secret@example.test/vhost",
        ]
    )
    output: list[str] = []
    result = cli_module._worker(
        args,
        protector=MemoryProtector(),
        output=output.append,
        broker=object(),
        connection=FakeConnection(),
        heartbeat_transport=FakeResource(),
        task_transport=FakeResource(),
        executor=FakeResource(),
    )

    assert result == 0
    rendered = output[0]
    assert credential not in rendered
    assert "secret" not in rendered
    assert "amqps" not in rendered
    assert json.loads(rendered)["stop_reason"] == "STOP_REQUESTED"
    assert boundary_calls == ["install", "restore"]
    consumer = captured["consumer"]
    heartbeat = captured["heartbeat"]
    assert consumer.slots == {"API": 2, "WEB": 3, "PERFORMANCE": 4}
    assert consumer._slot_state.totals() == consumer.slots
    assert heartbeat._config.api_slots == 2
    assert heartbeat._config.web_slots == 3
    assert heartbeat._config.performance_slots == 4


def test_cli_worker_slot_override_updates_heartbeat_consumer_and_state(
    tmp_path, monkeypatch
) -> None:
    from runner import cli as cli_module
    from runner.worker import WorkerRunResult, WorkerStats, WorkerStopReason

    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_config(
        RunnerConfig(
            "https://example.test",
            "runner-test",
            api_slots=2,
            web_slots=0,
            performance_slots=4,
        )
    )
    store.save_identity(RunnerIdentity("runner-001", "credential-value-123456789"))

    class FakeConnection:
        def channel(self):
            return object()

        def close(self):
            pass

    class FakeResource:
        def close(self):
            pass

    captured: dict[str, object] = {}

    class FakeBoundary:
        def __init__(self, stop_event):
            del stop_event

        def install(self):
            pass

        def restore(self):
            pass

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            del kwargs
            captured["consumer"] = args[0]
            captured["heartbeat"] = args[1]
            self.resources = args[3]

        def run(self):
            return WorkerRunResult(WorkerStats(), WorkerStopReason.STOP_REQUESTED)

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "RunnerWorker", FakeWorker)
    monkeypatch.setattr(cli_module, "SignalBoundary", FakeBoundary)
    args = cli_module.build_parser().parse_args(
        [
            "worker",
            "--state-dir",
            str(tmp_path),
            "--rabbitmq-url",
            "amqp://runner:secret@example.test/",
            "--web-slots",
            "1",
        ]
    )

    assert cli_module._worker(
        args,
        protector=MemoryProtector(),
        output=lambda _value: None,
        broker=object(),
        connection=FakeConnection(),
        heartbeat_transport=FakeResource(),
        task_transport=FakeResource(),
        executor=FakeResource(),
    ) == 0

    consumer = captured["consumer"]
    heartbeat = captured["heartbeat"]
    expected = {"API": 2, "WEB": 1, "PERFORMANCE": 4}
    assert consumer.slots == expected
    assert consumer._slot_state.totals() == expected
    assert heartbeat._config.api_slots == 2
    assert heartbeat._config.web_slots == 1
    assert heartbeat._config.performance_slots == 4


def test_cli_worker_rejects_negative_slot_override(tmp_path) -> None:
    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_config(RunnerConfig("https://example.test", "runner-test"))
    errors: list[str] = []

    result = main(
        [
            "--state-dir",
            str(tmp_path),
            "worker",
            "--web-slots",
            "-1",
        ],
        protector=MemoryProtector(),
        error=errors.append,
    )

    assert result == 2
    assert errors and "web_slots" in errors[0]


def test_cli_worker_wires_broker_reconnect_factories(tmp_path, monkeypatch) -> None:
    from runner import cli as cli_module
    from runner.worker import WorkerRunResult, WorkerStats, WorkerStopReason

    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_config(RunnerConfig("https://example.test", "runner-test"))
    store.save_identity(RunnerIdentity("runner-001", "credential-value-123456789"))

    class FakeConnection:
        def channel(self):
            return object()

        def close(self):
            pass

    class FakeBroker:
        def __init__(self) -> None:
            self.connect_calls = 0

        def connect(self):
            self.connect_calls += 1
            return FakeConnection()

    class FakeResource:
        def close(self):
            pass

    captured: dict[str, object] = {}
    broker = FakeBroker()

    class FakeBoundary:
        def __init__(self, stop_event):
            del stop_event

        def install(self):
            pass

        def restore(self):
            pass

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.resources = args[3]

        def run(self):
            return WorkerRunResult(WorkerStats(), WorkerStopReason.STOP_REQUESTED)

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "RunnerWorker", FakeWorker)
    monkeypatch.setattr(cli_module, "SignalBoundary", FakeBoundary)
    args = cli_module.build_parser().parse_args(
        [
            "worker",
            "--state-dir",
            str(tmp_path),
            "--rabbitmq-url",
            "amqp://runner:secret@example.test/",
        ]
    )

    assert (
        cli_module._worker(
            args,
            protector=MemoryProtector(),
            output=lambda _value: None,
            broker=broker,
            connection=FakeConnection(),
            heartbeat_transport=FakeResource(),
            task_transport=FakeResource(),
            executor=FakeResource(),
        )
        == 0
    )
    connection_factory = captured["connection_factory"]
    consumer_factory = captured["consumer_factory"]
    assert callable(connection_factory)
    assert callable(consumer_factory)
    assert connection_factory() is not None
    assert broker.connect_calls == 1
    assert callable(consumer_factory)


def test_worker_initial_broker_connection_retries_with_bounded_backoff() -> None:
    from threading import Event

    from runner import cli as cli_module
    from runner.worker import RabbitMqReconnectPolicy

    connection = object()

    class RecoveringBroker:
        def __init__(self) -> None:
            self.calls = 0

        def connect(self):
            self.calls += 1
            if self.calls < 3:
                raise TransportError("RabbitMQ 暂时不可用", transient=True)
            return connection

    broker = RecoveringBroker()
    waits: list[float] = []

    result = cli_module._connect_broker_with_retry(
        broker,
        Event(),
        RabbitMqReconnectPolicy(initial_delay_seconds=1, max_delay_seconds=30),
        lambda seconds: waits.append(seconds) or False,
    )

    assert result is connection
    assert broker.calls == 3
    assert waits == [1.0, 2.0]


def test_worker_initial_broker_connection_does_not_retry_permanent_error() -> None:
    from threading import Event

    from runner import cli as cli_module
    from runner.worker import RabbitMqReconnectPolicy

    class RejectedBroker:
        def connect(self):
            raise TransportError("RabbitMQ 认证失败", transient=False)

    with pytest.raises(TransportError, match="认证失败"):
        cli_module._connect_broker_with_retry(
            RejectedBroker(),
            Event(),
            RabbitMqReconnectPolicy(),
            lambda _seconds: False,
        )
