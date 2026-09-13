import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from xml.etree import ElementTree

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.envelope import parse_task_envelope
from runner.errors import TransportError
from runner.executors.api import ApiExecutionResult, ApiRequestTemplate
from runner.executors.jmeter import build_jmx
from runner.executors.performance import PerformanceExecutor
from runner.executors.streaming import StreamingApiExecutor
from runner.isolation import IsolatedOutcome
from runner.models import (
    ClaimResult,
    ExecutionCancellationResult,
    ExecutionCompleteResult,
    ExecutionPlanResult,
    ExecutionStartResult,
    PerformanceCompleteResult,
    PerformanceExecutionPlanResult,
    RunnerIdentity,
    ScenarioExecutionPlanResult,
    ScenarioExecutionStartResult,
    ScenarioNodePlan,
)
from runner.performance_store import PerformanceResultStore


class FakeApiExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = Lock()

    def execute(self, _request: ApiRequestTemplate) -> ApiExecutionResult:
        with self._lock:
            self.calls += 1
            call = self.calls
        return ApiExecutionResult(
            status_code=200 if call != 4 else 503,
            json_body=None,
            text="ok",
            headers={},
            cookies={},
            elapsed_ms=call,
            response_time_ms=call,
            body_size=2,
        )


def test_performance_executor_excludes_warmup_samples() -> None:
    executor = FakeApiExecutor()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")

    result = PerformanceExecutor(executor).execute(
        request,
        concurrency=2,
        iterations=4,
        warmup_iterations=2,
    )

    assert executor.calls == 6
    assert len(result.samples) == 4
    assert result.wall_duration_ms >= 1
    assert all(sample.status_code is not None for sample in result.samples)


def test_performance_executor_paces_fixed_rps_and_keeps_exact_sample_count() -> None:
    executor = FakeApiExecutor()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    result = PerformanceExecutor(executor, clock=lambda: now[0], sleeper=sleep).execute(
        request,
        concurrency=2,
        iterations=4,
        load_mode="FIXED_RPS",
        target_rps=4,
        duration_seconds=1,
    )

    assert executor.calls == 4
    assert len(result.samples) == 4
    assert result.wall_duration_ms >= 1


def test_api_case_can_use_performance_slot() -> None:
    envelope = parse_task_envelope(
        b'{"schema_version":1,"message_id":"msg_1","run_id":"run_1",'
        b'"runner_id":"runner_1","run_type":"API_CASE",'
        b'"required_slot_type":"PERFORMANCE","attempt":1,'
        b'"enqueued_at":"2026-09-13T10:00:00Z"}'
    )

    assert envelope.required_slot_type == "PERFORMANCE"


class FakeChannel:
    def __init__(self) -> None:
        self.acked = False
        self.rejected = False
        self.nacked = False

    def basic_ack(self, **_kwargs: object) -> None:
        self.acked = True

    def basic_reject(self, **_kwargs: object) -> None:
        self.rejected = True

    def basic_nack(self, **_kwargs: object) -> None:
        self.nacked = True


class FakePerformanceClient:
    def __init__(self, request: ApiRequestTemplate) -> None:
        self.request = request
        self.completed_samples: list[dict[str, object]] = []

    def claim(self, _identity: object, run_id: str, message_id: str) -> ClaimResult:
        return ClaimResult(
            run_id,
            message_id,
            "runner-001",
            "ASSIGNED",
            datetime.now(UTC),
            False,
        )

    def get_performance_execution_plan(
        self, _identity: object, run_id: str, message_id: str
    ) -> PerformanceExecutionPlanResult:
        return PerformanceExecutionPlanResult(
            1,
            run_id,
            message_id,
            "runner-001",
            7,
            self.request,
            2,
            4,
            "FIXED_ITERATIONS",
            None,
            None,
            1,
            1_000,
            60_000,
        )

    def execution_start(
        self, _identity: object, run_id: str, message_id: str, case_run_id: int
    ) -> ExecutionStartResult:
        return ExecutionStartResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            "RUNNING",
            datetime.now(UTC),
            False,
        )

    def get_execution_cancellation(
        self, _identity: object, run_id: str, message_id: str, case_run_id: int
    ) -> ExecutionCancellationResult:
        return ExecutionCancellationResult(
            1, run_id, message_id, case_run_id, "RUNNING", False, False
        )

    def performance_complete(
        self,
        _identity: object,
        run_id: str,
        _message_id: str,
        _case_run_id: int,
        *,
        wall_duration_ms: int,
        samples: list[dict[str, object]],
    ) -> PerformanceCompleteResult:
        assert wall_duration_ms >= 1
        self.completed_samples = samples
        return PerformanceCompleteResult(run_id, "SUCCESS", False)


class ScenarioPerformanceClient(FakePerformanceClient):
    def get_performance_execution_plan(
        self, _identity: object, run_id: str, message_id: str
    ) -> PerformanceExecutionPlanResult:
        return PerformanceExecutionPlanResult(
            3,
            run_id,
            message_id,
            "runner-001",
            7,
            None,
            2,
            4,
            "FIXED_ITERATIONS",
            None,
            None,
            1,
            1_000,
            60_000,
            target_type="SCENARIO",
        )

    def get_scenario_execution_plan(
        self,
        _identity: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ScenarioExecutionPlanResult:
        return ScenarioExecutionPlanResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            11,
            12,
            60_000,
            {},
            {"stop_on_failure": True, "cleanup_policy": "ALWAYS", "max_loop_iterations": 10},
            (
                ScenarioNodePlan("start", "START", "Start", None, True, 1_000, None, {}),
                ScenarioNodePlan("end", "END", "End", None, True, 1_000, None, {}),
            ),
            (),
            (),
        )

    def execution_start(self, *_args: object) -> ExecutionStartResult:
        raise AssertionError("Scenario 性能执行不能使用 API start 路由")

    def scenario_execution_start(
        self,
        _identity: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ScenarioExecutionStartResult:
        return ScenarioExecutionStartResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            "RUNNING",
            datetime.now(UTC),
            False,
        )


def test_consumer_routes_performance_envelope_through_performance_executor() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = FakePerformanceClient(request)
    api_executor = FakeApiExecutor()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=api_executor,  # type: ignore[arg-type]
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "API_CASE",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    outcome = consumer.process_execution_delivery(1, body)

    assert outcome == ConsumeOutcome.ACKED
    assert channel.acked and not channel.rejected and not channel.nacked
    assert api_executor.calls == 5
    assert len(client.completed_samples) == 4


def test_scenario_performance_envelope_uses_performance_mode_before_run_type() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = ScenarioPerformanceClient(request)
    api_executor = FakeApiExecutor()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=api_executor,  # type: ignore[arg-type]
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "SCENARIO",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    outcome = consumer.process_execution_delivery(1, body)

    assert outcome == ConsumeOutcome.ACKED
    assert channel.acked and not channel.rejected and not channel.nacked
    assert api_executor.calls == 0
    assert len(client.completed_samples) == 4


class CancellablePerformanceProcess:
    def __init__(self) -> None:
        self.started = False
        self.alive = False
        self.cancel_requested = False
        self.terminated = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        self.alive = True

    def poll(self, _timeout_seconds: float) -> bool:
        if self.cancel_requested:
            self.alive = False
            return True
        return False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def request_stop(self) -> None:
        return None

    def join(self, _timeout: float | None = None) -> None:
        return None

    @property
    def exitcode(self) -> int | None:
        return None

    def outcome(self) -> IsolatedOutcome | None:
        if not self.cancel_requested:
            return None
        return IsolatedOutcome(
            kind="CANCELLED",
            error_type="CANCEL_REQUESTED",
            error_message="Runner 检测到取消请求",
        )

    def close(self) -> None:
        self.closed = True


class CancelDuringPerformanceClient(FakePerformanceClient):
    def __init__(self, request: ApiRequestTemplate) -> None:
        super().__init__(request)
        self.control_calls = 0
        self.cancel_completion: dict[str, object] | None = None

    def get_execution_cancellation(
        self, _identity: object, run_id: str, message_id: str, case_run_id: int
    ) -> ExecutionCancellationResult:
        self.control_calls += 1
        cancelling = self.control_calls >= 2
        return ExecutionCancellationResult(
            1,
            run_id,
            message_id,
            case_run_id,
            "CANCELLING" if cancelling else "RUNNING",
            cancelling,
            False,
        )

    def execution_complete(
        self,
        _identity: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        **kwargs: object,
    ) -> ExecutionCompleteResult:
        self.cancel_completion = kwargs
        return ExecutionCompleteResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            "CANCELLED",
            "CANCELLED",
            "CANCELLED",
            [],
            "CANCEL_REQUESTED",
            "Runner 检测到取消请求",
            datetime.now(UTC),
            False,
        )


def test_performance_isolation_cooperatively_cancels_before_ack() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = CancelDuringPerformanceClient(request)
    process = CancellablePerformanceProcess()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=FakeApiExecutor(),  # type: ignore[arg-type]
        performance_isolation_factory=lambda _plan: process,
        force_stop_poll_seconds=0.1,
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "API_CASE",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    outcome = consumer.process_execution_delivery(2, body)

    assert outcome == ConsumeOutcome.ACKED
    assert process.started and process.cancel_requested and process.closed
    assert not process.terminated
    assert client.cancel_completion is not None
    assert client.cancel_completion["outcome"] == "CANCELLED"
    assert channel.acked and not channel.rejected and not channel.nacked


class FinishedPerformanceProcess(CancellablePerformanceProcess):
    def __init__(self) -> None:
        super().__init__()
        self.result = IsolatedOutcome(
            kind="PERFORMANCE_RESULT",
            result={
                "wall_duration_ms": 1000,
                "samples": [
                    {"duration_ms": value, "status_code": 200, "bytes_received": 2}
                    for value in (10, 20, 30, 40)
                ],
            },
        )

    def start(self) -> None:
        self.started = True
        self.alive = False

    def poll(self, _timeout_seconds: float) -> bool:
        return True

    def outcome(self) -> IsolatedOutcome:
        return self.result


class RetryCompletionClient(FakePerformanceClient):
    def __init__(self, request: ApiRequestTemplate) -> None:
        super().__init__(request)
        self.completion_attempts = 0

    def performance_complete(
        self,
        identity: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        wall_duration_ms: int,
        samples: list[dict[str, object]],
    ) -> PerformanceCompleteResult:
        self.completion_attempts += 1
        if self.completion_attempts == 1:
            raise TransportError("temporary", transient=True)
        return super().performance_complete(
            identity,
            run_id,
            message_id,
            case_run_id,
            wall_duration_ms=wall_duration_ms,
            samples=samples,
        )


class V3PerformanceClient(FakePerformanceClient):
    def __init__(self, request: ApiRequestTemplate) -> None:
        super().__init__(request)
        self.target_plan_calls = 0

    def get_performance_execution_plan(
        self, _identity: object, run_id: str, message_id: str
    ) -> PerformanceExecutionPlanResult:
        return PerformanceExecutionPlanResult(
            3,
            run_id,
            message_id,
            "runner-001",
            7,
            self.request,
            2,
            4,
            "FIXED_ITERATIONS",
            None,
            None,
            1,
            1_000,
            60_000,
            target_type="API_CASE",
            engine="PYTHON_HTTP",
            cleanup_mode="RESOURCE_CLEANUP",
        )

    def get_execution_plan(
        self,
        _identity: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> ExecutionPlanResult:
        self.target_plan_calls += 1
        return ExecutionPlanResult(
            1,
            run_id,
            message_id,
            "runner-001",
            case_run_id,
            11,
            ApiRequestTemplate(method="GET", url="https://stale.example.test"),
            60_000,
            pre_actions=({"type": "WAIT", "milliseconds": 1},),
            cleanups=({"type": "WAIT", "milliseconds": 1},),
        )


def test_v3_performance_delivery_reuses_full_api_case_plan() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = V3PerformanceClient(request)
    captured_plan: list[PerformanceExecutionPlanResult] = []

    def factory(plan: PerformanceExecutionPlanResult) -> FinishedPerformanceProcess:
        captured_plan.append(plan)
        return FinishedPerformanceProcess()

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=FakeApiExecutor(),  # type: ignore[arg-type]
        performance_isolation_factory=factory,
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "API_CASE",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    assert consumer.process_execution_delivery(2, body) == ConsumeOutcome.ACKED
    assert client.target_plan_calls == 1
    assert len(captured_plan) == 1
    target_plan = captured_plan[0].target_plan
    assert isinstance(target_plan, ExecutionPlanResult)
    assert target_plan.request is request
    assert len(target_plan.pre_actions) == 1
    assert len(target_plan.cleanups) == 1


def test_performance_redelivery_reuses_completed_samples_without_reapplying_load() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = RetryCompletionClient(request)
    process = FinishedPerformanceProcess()
    factory_calls = 0

    def factory(_plan: object) -> FinishedPerformanceProcess:
        nonlocal factory_calls
        factory_calls += 1
        return process

    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=FakeApiExecutor(),  # type: ignore[arg-type]
        performance_isolation_factory=factory,
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "API_CASE",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    assert consumer.process_execution_delivery(3, body) == ConsumeOutcome.REQUEUED
    assert consumer.process_execution_delivery(4, body) == ConsumeOutcome.ACKED
    assert factory_calls == 1
    assert client.completion_attempts == 2
    assert len(client.completed_samples) == 4


def test_performance_redelivery_after_consumer_restart_uses_persisted_samples(
    tmp_path: Path,
) -> None:
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    client = RetryCompletionClient(request)
    process = FinishedPerformanceProcess()
    factory_calls = 0
    store = PerformanceResultStore(tmp_path / "performance-results")

    def factory(_plan: object) -> FinishedPerformanceProcess:
        nonlocal factory_calls
        factory_calls += 1
        return process

    body = json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "API_CASE",
            "required_slot_type": "PERFORMANCE",
            "attempt": 1,
            "enqueued_at": "2026-09-13T10:00:00Z",
        }
    ).encode()

    first_channel = FakeChannel()
    first_consumer = RunnerTaskConsumer(
        first_channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=FakeApiExecutor(),  # type: ignore[arg-type]
        performance_isolation_factory=factory,
        performance_result_store=store,
    )
    assert first_consumer.process_execution_delivery(3, body) == ConsumeOutcome.REQUEUED
    assert store.load("run-001", "message-001") is not None

    second_channel = FakeChannel()
    second_consumer = RunnerTaskConsumer(
        second_channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 1},
        executor=FakeApiExecutor(),  # type: ignore[arg-type]
        performance_isolation_factory=factory,
        performance_result_store=store,
    )
    assert second_consumer.process_execution_delivery(4, body) == ConsumeOutcome.ACKED
    assert factory_calls == 1
    assert client.completion_attempts == 2
    assert len(client.completed_samples) == 4
    assert store.load("run-001", "message-001") is None


class AdvancingApiExecutor(FakeApiExecutor):
    def __init__(self, now: list[float]) -> None:
        super().__init__()
        self.now = now

    def execute(self, request: ApiRequestTemplate) -> ApiExecutionResult:
        self.now[0] += 0.1
        return super().execute(request)


class SuccessfulApiExecutor(FakeApiExecutor):
    def __init__(self, now: list[float] | None = None) -> None:
        super().__init__()
        self.now = now

    def execute(self, _request: ApiRequestTemplate) -> ApiExecutionResult:
        with self._lock:
            self.calls += 1
            if self.now is not None:
                self.now[0] += 0.1
        return ApiExecutionResult(200, None, "ok", {}, {}, 1, 1, 2)


def test_fixed_concurrency_and_step_load_keep_duration_and_sample_cap() -> None:
    request = ApiRequestTemplate(method="GET", url="https://example.test/health")
    now = [0.0]
    executor = AdvancingApiExecutor(now)
    runtime = PerformanceExecutor(
        executor,  # type: ignore[arg-type]
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    fixed = runtime.execute(
        request,
        concurrency=2,
        iterations=4,
        load_mode="FIXED_CONCURRENCY",
        duration_seconds=1,
    )
    assert len(fixed.samples) == 4
    assert fixed.wall_duration_ms >= 1_000

    now[0] = 0.0
    stepped = runtime.execute(
        request,
        concurrency=4,
        iterations=6,
        load_mode="STEP_LOAD",
        step_stages=(
            {"concurrency": 1, "duration_seconds": 1},
            {"concurrency": 4, "duration_seconds": 1},
        ),
    )
    assert len(stepped.samples) == 6
    assert stepped.wall_duration_ms >= 2_000
    assert max(sample.started_offset_ms for sample in stepped.samples) >= 1_000


def test_scenario_can_be_reused_as_performance_target() -> None:
    nodes = (
        ScenarioNodePlan("start", "START", "Start", None, True, 1_000, None, {}),
        ScenarioNodePlan("end", "END", "End", None, True, 1_000, None, {}),
    )
    plan = ScenarioExecutionPlanResult(
        1,
        "run-1",
        "message-1",
        "runner-1",
        1,
        1,
        1,
        60_000,
        {},
        {"stop_on_failure": True, "cleanup_policy": "ALWAYS", "max_loop_iterations": 10},
        nodes,
        (),
        (),
    )

    result = PerformanceExecutor(FakeApiExecutor()).execute(  # type: ignore[arg-type]
        None,
        concurrency=1,
        iterations=2,
        target_type="SCENARIO",
        target_plan=plan,
    )

    assert len(result.samples) == 2
    assert all(sample.status_code == 200 for sample in result.samples)


class FakeStreamResponse:
    status_code = 200

    def __init__(self, now: list[float]) -> None:
        self.now = now
        self.closed = False

    def iter_lines(self):
        self.now[0] += 0.05
        yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
        self.now[0] += 0.10
        yield 'data: {"usage":{"prompt_tokens":3,"completion_tokens":5}}'
        self.now[0] += 0.02
        yield "data: [DONE]"

    def close(self) -> None:
        self.closed = True


class FakeStreamApiExecutor:
    def __init__(self, response: FakeStreamResponse) -> None:
        self.response = response

    def _request_with_redirects(self, _request: object, _started: float) -> FakeStreamResponse:
        return self.response


def test_streaming_executor_collects_ttft_tokens_and_completion() -> None:
    now = [0.0]
    response = FakeStreamResponse(now)
    executor = StreamingApiExecutor(
        FakeStreamApiExecutor(response),  # type: ignore[arg-type]
        clock=lambda: now[0],
    )
    result = executor.execute(
        ApiRequestTemplate(method="POST", url="https://example.test/chat"),
        {"protocol": "SSE", "completion_marker": "[DONE]", "require_completion_marker": True},
    )

    assert result.completed is True
    assert result.ttft_ms == 50
    assert result.input_tokens == 3
    assert result.output_tokens == 5
    assert result.chunk_count == 3
    assert result.max_chunk_gap_ms == 100
    assert response.closed is True


def test_jmeter_plan_is_well_formed_and_keeps_exact_iteration_split() -> None:
    request = ApiRequestTemplate(method="GET", url="https://example.test/items?q=one")
    xml = build_jmx(
        request,
        concurrency=3,
        iterations=8,
        warmup_iterations=2,
        target_rps=4,
        request_timeout_ms=1_000,
    )

    root = ElementTree.fromstring(xml)
    groups = root.findall(".//ThreadGroup")
    setup_groups = root.findall(".//SetupThreadGroup")
    assert len(groups) == 2
    assert len(setup_groups) == 1
    main_samples = sum(
        int(group.find("./stringProp[@name='ThreadGroup.num_threads']").text or "0")
        * int(
            group.find(
                "./elementProp[@name='ThreadGroup.main_controller']/stringProp[@name='LoopController.loops']"
            ).text
            or "0"
        )
        for group in groups
    )
    assert main_samples == 8


def test_api_cleanup_modes_none_resource_and_batch_are_executed_deterministically() -> None:
    request = ApiRequestTemplate(method="GET", url="https://example.test/items")
    cleanup = {
        "cleanup_id": "delete-item",
        "cleanup_type": "API",
        "policy": "ALWAYS",
        "enabled": True,
        "timeout_ms": 1_000,
        "method": "DELETE",
        "url": "https://example.test/items/1",
        "query_params": [],
        "headers": [],
        "cookies": [],
        "body": None,
        "auth": {
            "type": "NONE",
            "credential_ref": None,
            "secret_id": None,
            "key_name": None,
            "placement": "HEADER",
        },
        "connection_id": None,
        "sql": None,
        "params": {},
        "resource_id_param": None,
    }
    plan = ExecutionPlanResult(
        1,
        "run-1",
        "message-1",
        "runner-1",
        1,
        1,
        request,
        60_000,
        cleanups=(cleanup,),
    )

    for mode, expected_calls, wall_range in (
        ("NONE", 2, range(190, 300)),
        ("RESOURCE_CLEANUP", 4, range(390, 500)),
        ("BATCH_CLEANUP", 4, range(190, 300)),
    ):
        now = [0.0]
        api = SuccessfulApiExecutor(now)
        result = PerformanceExecutor(api, clock=lambda now=now: now[0]).execute(  # type: ignore[arg-type]
            request,
            concurrency=1,
            iterations=2,
            target_plan=plan,
            cleanup_mode=mode,
        )
        assert len(result.samples) == 2
        assert api.calls == expected_calls
        assert result.wall_duration_ms in wall_range
