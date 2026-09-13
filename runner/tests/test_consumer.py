import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import (
    AuthenticationError,
    ExecutionError,
    ProtocolError,
    TargetExecutionError,
    TargetTimeoutError,
    TransportError,
)
from runner.executors.api import ApiExecutionResult, ApiRequestTemplate, RetryPolicy
from runner.isolation import IsolatedOutcome
from runner.models import (
    ClaimResult,
    ExecutionCancellationResult,
    ExecutionCompleteResult,
    ExecutionEvaluationResult,
    ExecutionPlanResult,
    ExecutionStartResult,
    HttpResponse,
    RunnerIdentity,
)
from runner.protocol import RunnerClient
from runner.slots import SlotState
from runner.topology import runner_queue_name
from tests.helpers import FakeTransport


def _body(runner_id: str = "runner-001") -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": runner_id,
            "run_type": "API_CASE",
            "required_slot_type": "API",
            "attempt": 1,
            "enqueued_at": "2026-08-25T10:00:00Z",
        }
    ).encode()


@dataclass
class DeliveryMethod:
    delivery_tag: int


class FakeChannel:
    def __init__(self, delivery: tuple[DeliveryMethod, object, bytes] | None = None) -> None:
        self.delivery = delivery
        self.calls: list[tuple[str, dict[str, object]]] = []

    def exchange_declare(self, **kwargs):
        self.calls.append(("exchange_declare", kwargs))

    def queue_declare(self, **kwargs):
        self.calls.append(("queue_declare", kwargs))

    def queue_bind(self, **kwargs):
        self.calls.append(("queue_bind", kwargs))

    def basic_qos(self, **kwargs):
        self.calls.append(("basic_qos", kwargs))

    def basic_get(self, **kwargs):
        self.calls.append(("basic_get", kwargs))
        return self.delivery or (None, None, None)

    def basic_consume(self, **kwargs):
        self.calls.append(("basic_consume", kwargs))

    def start_consuming(self):
        self.calls.append(("start_consuming", {}))

    def basic_ack(self, **kwargs):
        self.calls.append(("basic_ack", kwargs))

    def basic_nack(self, **kwargs):
        self.calls.append(("basic_nack", kwargs))

    def basic_reject(self, **kwargs):
        self.calls.append(("basic_reject", kwargs))

    def stop_consuming(self):
        self.calls.append(("stop_consuming", {}))


class FakeClaimClient:
    def __init__(self, *results: ClaimResult | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    def claim(self, identity, run_id: str, message_id: str):
        self.calls.append((run_id, message_id))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeExecutionClient(FakeClaimClient):
    def __init__(
        self,
        claim: ClaimResult | Exception,
        plan: ExecutionPlanResult | Exception | list[ExecutionPlanResult | Exception],
        start: ExecutionStartResult | Exception | list[ExecutionStartResult | Exception],
        complete: ExecutionCompleteResult | Exception | list[ExecutionCompleteResult | Exception],
        cancellation: (
            ExecutionCancellationResult
            | Exception
            | list[ExecutionCancellationResult | Exception]
            | None
        ) = None,
        evaluation: ExecutionEvaluationResult | Exception | None = None,
    ) -> None:
        super().__init__(claim)
        self.plan = plan
        self.start = start
        self.complete = complete
        self.execution_calls: list[tuple[str, object]] = []
        self.cancellation = cancellation
        self.cancellation_calls: list[tuple[str, str, int]] = []
        self.evaluation = evaluation
        self.evaluation_calls: list[tuple[str, str, int, object]] = []

    def get_execution_plan(self, identity, run_id: str, message_id: str):
        self.execution_calls.append(("plan", (run_id, message_id)))
        plan = self.plan.pop(0) if isinstance(self.plan, list) else self.plan
        if isinstance(plan, Exception):
            raise plan
        return plan

    def execution_start(self, identity, run_id: str, message_id: str, case_run_id: int):
        self.execution_calls.append(("start", case_run_id))
        start = self.start.pop(0) if isinstance(self.start, list) else self.start
        if isinstance(start, Exception):
            raise start
        return start

    def execution_complete(
        self, identity, run_id: str, message_id: str, case_run_id: int, **kwargs
    ):
        self.execution_calls.append(("complete", kwargs))
        complete = self.complete.pop(0) if isinstance(self.complete, list) else self.complete
        if isinstance(complete, Exception):
            raise complete
        return complete

    def execution_evaluate(
        self,
        identity,
        run_id: str,
        message_id: str,
        case_run_id: int,
        response: object,
    ) -> ExecutionEvaluationResult:
        del identity
        self.evaluation_calls.append((run_id, message_id, case_run_id, response))
        if isinstance(self.evaluation, Exception):
            raise self.evaluation
        if self.evaluation is None:
            raise AssertionError("unexpected execution_evaluate")
        return self.evaluation

    def get_execution_cancellation(
        self, identity, run_id: str, message_id: str, case_run_id: int
    ) -> ExecutionCancellationResult:
        del identity
        self.cancellation_calls.append((run_id, message_id, case_run_id))
        cancellation = self.cancellation
        if isinstance(cancellation, list):
            cancellation = cancellation.pop(0)
        if cancellation is None:
            return ExecutionCancellationResult(
                1,
                run_id,
                message_id,
                case_run_id,
                "RUNNING",
                False,
                False,
            )
        if isinstance(cancellation, Exception):
            raise cancellation
        return cancellation


class FakeExecutor:
    def __init__(
        self, result: ApiExecutionResult | Exception | list[ApiExecutionResult | Exception]
    ) -> None:
        self.results = result if isinstance(result, list) else [result]
        self.calls: list[object] = []

    def execute(self, request: object) -> ApiExecutionResult:
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _consumer(channel: FakeChannel, client: FakeClaimClient) -> RunnerTaskConsumer:
    return RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value-123456789"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
    )


def _plan(
    *,
    retry_policy: RetryPolicy | None = None,
    cleanups: tuple[dict[str, object], ...] = (),
    defer_cleanup: bool = False,
) -> ExecutionPlanResult:
    return ExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        ApiRequestTemplate(
            "GET",
            "https://target.example.test",
            retry_policy=retry_policy or RetryPolicy(),
        ),
        900_000,
        cleanups=cleanups,
        defer_cleanup_until_assertions=defer_cleanup,
    )


def test_deferred_cleanup_uses_assertion_evaluation_before_final_completion() -> None:
    cleanup = {
        "cleanup_id": "on-failure",
        "cleanup_type": "API",
        "policy": "ON_FAILURE",
        "enabled": True,
        "timeout_ms": 30000,
        "method": "DELETE",
        "url": "https://target.example.test/cleanup",
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
    plan = _plan(cleanups=(cleanup,), defer_cleanup=True)
    evaluation = ExecutionEvaluationResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "FAIL",
        "FAILURE",
        [],
        "signed-evaluation",
    )
    completed = ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "HTTP_RESPONSE",
        "FAILED",
        "FAILED",
        [],
        "ASSERTION_FAILED",
        "assertion failed",
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        False,
        0,
    )
    client = FakeExecutionClient(
        _claimed(), plan, _started(), completed, evaluation=evaluation
    )
    executor = FakeExecutor(
        [
            ApiExecutionResult(200, {"ok": False}, None, {}, {}, 1, 1, 10),
            ApiExecutionResult(204, None, None, {}, {}, 1, 1, 0),
        ]
    )
    channel = FakeChannel()

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        32, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert len(client.evaluation_calls) == 1
    assert len(executor.calls) == 2
    completion = client.execution_calls[-1][1]
    assert completion["evaluation_token"] == "signed-evaluation"
    assert completion["outcome"] == "HTTP_RESPONSE"


def _started() -> ExecutionStartResult:
    return ExecutionStartResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "RUNNING",
        datetime(2026, 8, 25, 10, 1, tzinfo=UTC),
        False,
    )


def _cancelling_start() -> ExecutionStartResult:
    return ExecutionStartResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "CANCELLING",
        None,
        False,
    )


def _completed(
    *, idempotent: bool = False, retry_count: int = 0
) -> ExecutionCompleteResult:
    return ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "HTTP_RESPONSE",
        "SUCCESS",
        "SUCCESS",
        [],
        None,
        None,
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        idempotent,
        retry_count,
    )


def _execution_consumer(
    channel: FakeChannel,
    client: FakeExecutionClient,
    executor: FakeExecutor,
    stop_event: Event | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    slot_state: SlotState | None = None,
    isolation_factory: Callable[[object], object] | None = None,
    force_stop_poll_seconds: float = 1.0,
    clock: Callable[[], float] = lambda: 0.0,
) -> RunnerTaskConsumer:
    return RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value-123456789"),
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
        executor=executor,  # type: ignore[arg-type]
        stop_event=stop_event,
        sleeper=sleeper,
        slot_state=slot_state,
        isolation_factory=isolation_factory,
        force_stop_poll_seconds=force_stop_poll_seconds,
        clock=clock,
    )


def _claimed(*, idempotent: bool = False) -> ClaimResult:
    return ClaimResult(
        "run-001",
        "message-001",
        "runner-001",
        "ASSIGNED",
        datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        idempotent,
    )


def _cancelled() -> ClaimResult:
    return ClaimResult(
        "run-001",
        "message-001",
        "runner-001",
        "CANCELLED",
        None,
        True,
    )


def _timeout_completed() -> ExecutionCompleteResult:
    return ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "TIMEOUT",
        "TIMEOUT",
        "TIMEOUT",
        [],
        "TARGET_TIMEOUT",
        "目标 API 请求超时",
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        False,
    )


def _cancelled_completed(*, retry_count: int = 0) -> ExecutionCompleteResult:
    return ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "CANCELLED",
        "CANCELLED",
        "CANCELLED",
        [],
        "CANCEL_REQUESTED",
        "Runner 检测到取消请求",
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        False,
        retry_count,
    )


def _cancellation_requested() -> ExecutionCancellationResult:
    return ExecutionCancellationResult(
        1,
        "run-001",
        "message-001",
        42,
        "CANCELLING",
        True,
        False,
    )


def _force_stop_requested() -> ExecutionCancellationResult:
    return ExecutionCancellationResult(
        1,
        "run-001",
        "message-001",
        42,
        "CANCELLING",
        True,
        True,
    )


def test_consume_once_uses_prefetch_one_manual_ack_and_claims_before_ack() -> None:
    channel = FakeChannel((DeliveryMethod(7), object(), _body()))
    client = FakeClaimClient(_claimed())

    outcome = _consumer(channel, client).consume_once()

    assert outcome is ConsumeOutcome.ACKED
    assert client.calls == [("run-001", "message-001")]
    assert ("basic_qos", {"prefetch_count": 1}) in channel.calls
    assert (
        "basic_get",
        {"queue": runner_queue_name("runner-001"), "auto_ack": False},
    ) in channel.calls
    assert ("basic_ack", {"delivery_tag": 7}) in channel.calls
    assert not [call for call in channel.calls if call[0] in {"basic_nack", "basic_reject"}]


def test_configured_poll_is_nonblocking_and_topology_is_declared_once() -> None:
    channel = FakeChannel()
    consumer = _consumer(channel, FakeClaimClient())

    topology = consumer.configure()
    assert consumer.configure() is topology
    assert consumer.consume_once_configured(topology) is ConsumeOutcome.EMPTY

    assert len([call for call in channel.calls if call[0] == "exchange_declare"]) == 2
    assert len([call for call in channel.calls if call[0] == "queue_declare"]) == 2
    assert len([call for call in channel.calls if call[0] == "queue_bind"]) == 2
    assert [call for call in channel.calls if call[0] == "basic_qos"] == [
        ("basic_qos", {"prefetch_count": 1})
    ]


def test_duplicate_delivery_accepts_idempotent_claim_and_acks_both() -> None:
    channel = FakeChannel()
    client = FakeClaimClient(_claimed(), _claimed(idempotent=True))
    consumer = _consumer(channel, client)

    assert consumer.process_delivery(1, _body()) is ConsumeOutcome.ACKED
    assert consumer.process_delivery(2, _body()) is ConsumeOutcome.ACKED
    assert [call for call in channel.calls if call[0] == "basic_ack"] == [
        ("basic_ack", {"delivery_tag": 1}),
        ("basic_ack", {"delivery_tag": 2}),
    ]


def test_cancelled_claim_is_acked_without_reject() -> None:
    channel = FakeChannel()
    client = FakeClaimClient(_cancelled(), _cancelled())
    consumer = _consumer(channel, client)

    assert consumer.process_delivery(16, _body()) is ConsumeOutcome.ACKED
    assert consumer.process_delivery(17, _body()) is ConsumeOutcome.ACKED
    assert [call for call in channel.calls if call[0] == "basic_ack"] == [
        ("basic_ack", {"delivery_tag": 16}),
        ("basic_ack", {"delivery_tag": 17}),
    ]
    assert not [call for call in channel.calls if call[0] in {"basic_nack", "basic_reject"}]


@pytest.mark.parametrize("body", [_body("other-runner"), b"not-json"])
def test_invalid_envelope_is_rejected_without_claim(body: bytes) -> None:
    channel = FakeChannel()
    client = FakeClaimClient()

    outcome = _consumer(channel, client).process_delivery(3, body)

    assert outcome is ConsumeOutcome.REJECTED
    assert client.calls == []
    assert ("basic_reject", {"delivery_tag": 3, "requeue": False}) in channel.calls


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (TransportError("temporary", transient=True), ConsumeOutcome.REQUEUED),
        (ProtocolError("invalid claim response"), ConsumeOutcome.REJECTED),
    ],
)
def test_claim_temporary_or_protocol_failure_has_correct_disposition(error, outcome) -> None:
    channel = FakeChannel()
    client = FakeClaimClient(error)

    assert _consumer(channel, client).process_delivery(4, _body()) is outcome
    if outcome is ConsumeOutcome.REQUEUED:
        assert ("basic_nack", {"delivery_tag": 4, "requeue": True}) in channel.calls
    else:
        assert ("basic_reject", {"delivery_tag": 4, "requeue": False}) in channel.calls


def test_claim_http_422_is_rejected_without_requeue() -> None:
    channel = FakeChannel()
    client = RunnerClient(
        "https://example.test",
        FakeTransport(HttpResponse(422, {"detail": "validation"})),
    )

    outcome = _consumer(channel, client).process_delivery(6, _body())

    assert outcome is ConsumeOutcome.REJECTED
    assert ("basic_reject", {"delivery_tag": 6, "requeue": False}) in channel.calls


def test_401_or_403_stops_consuming_after_non_requeue_reject() -> None:
    channel = FakeChannel()
    client = FakeClaimClient(AuthenticationError("credential invalid"))

    outcome = _consumer(channel, client).process_delivery(5, _body())

    assert outcome is ConsumeOutcome.STOPPED_AUTH
    assert ("basic_reject", {"delivery_tag": 5, "requeue": False}) in channel.calls
    assert ("stop_consuming", {}) in channel.calls


def test_consume_forever_registers_manual_ack_callback() -> None:
    channel = FakeChannel()
    client = FakeClaimClient()
    consumer = _consumer(channel, client)

    consumer.consume_forever()

    basic_consume = next(call for call in channel.calls if call[0] == "basic_consume")
    assert basic_consume[1]["queue"] == runner_queue_name("runner-001")
    assert basic_consume[1]["auto_ack"] is False
    assert callable(basic_consume[1]["on_message_callback"])
    assert ("start_consuming", {}) in channel.calls


def test_execute_once_acks_only_after_complete_and_maps_http_response() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    executor = FakeExecutor(
        ApiExecutionResult(
            200,
            {"ok": True},
            None,
            {"X-Result": "ok"},
            {"sid": "opaque"},
            2,
            2,
            10,
        )
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(9, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert [name for name, _ in client.execution_calls] == ["plan", "start", "complete"]
    assert executor.calls == [_plan().request]
    complete_kwargs = client.execution_calls[-1][1]
    assert complete_kwargs == {
        "outcome": "HTTP_RESPONSE",
        "retry_count": 0,
        "response": {
            "status_code": 200,
            "json_body": {"ok": True},
            "text": None,
            "headers": {"X-Result": "ok"},
            "cookies": {"sid": "opaque"},
            "elapsed_ms": 2,
            "response_time_ms": 2,
        },
    }
    assert ("basic_ack", {"delivery_tag": 9}) in channel.calls


def test_data_driven_api_rows_execute_serially_before_message_ack() -> None:
    channel = FakeChannel()
    second_plan = ExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        43,
        7,
        ApiRequestTemplate("GET", "https://target.example.test/row-2"),
        900_000,
    )
    second_started = ExecutionStartResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        43,
        "RUNNING",
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        False,
    )
    first_complete = ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        "HTTP_RESPONSE",
        "RUNNING",
        "SUCCESS",
        [],
        None,
        None,
        datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
        False,
        0,
    )
    second_complete = ExecutionCompleteResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        43,
        "HTTP_RESPONSE",
        "SUCCESS",
        "SUCCESS",
        [],
        None,
        None,
        datetime(2026, 8, 25, 10, 3, tzinfo=UTC),
        False,
        0,
    )
    client = FakeExecutionClient(
        _claimed(),
        [_plan(), second_plan],
        [_started(), second_started],
        [first_complete, second_complete],
    )
    executor = FakeExecutor(
        [
            ApiExecutionResult(200, {"row": 1}, None, {}, {}, 1, 1, 1),
            ApiExecutionResult(200, {"row": 2}, None, {}, {}, 1, 1, 1),
        ]
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        91, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert [name for name, _ in client.execution_calls] == [
        "plan",
        "start",
        "complete",
        "plan",
        "start",
        "complete",
    ]
    assert executor.calls == [_plan().request, second_plan.request]
    assert [call for call in channel.calls if call[0] == "basic_ack"] == [
        ("basic_ack", {"delivery_tag": 91})
    ]


def test_execute_once_applies_api_pre_actions_before_target_request() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate.from_mapping(
        {
            "method": "POST",
            "url": "{{base_url}}/orders/{{tenant}}",
            "headers": [{"name": "X-Request-Id", "value": "{{request_id}}"}],
            "body": {"type": "JSON", "content": {"tenant": "{{tenant}}"}},
        }
    )
    plan = ExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        request,
        900_000,
        {"base_url": "https://target.example.test", "tenant": "before"},
        (
            {"type": "SET_VARIABLE", "name": "tenant", "value": "after"},
            {"type": "FAKER", "name": "request_id", "generator": "uuid", "seed": 7},
        ),
    )
    client = FakeExecutionClient(_claimed(), plan, _started(), _completed())
    executor = FakeExecutor(ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 2))

    outcome = _execution_consumer(
        channel, client, executor
    ).process_execution_delivery(91, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    rendered = executor.calls[0]
    assert isinstance(rendered, ApiRequestTemplate)
    assert rendered.url == "https://target.example.test/orders/after"
    assert rendered.headers[0].value != "{{request_id}}"
    assert rendered.body.content == {"tenant": "after"}
    traces = client.execution_calls[-1][1]["action_traces"]
    assert [trace["type"] for trace in traces] == ["SET_VARIABLE", "FAKER"]
    assert all(trace["phase"] == "PRE" for trace in traces)
    assert all(trace["status"] == "SUCCESS" for trace in traces)


def test_pipeline_retry_is_internal_and_reports_actual_retry_count() -> None:
    channel = FakeChannel()
    request = ApiRequestTemplate.from_mapping(
        {
            "method": "GET",
            "url": "https://target.example.test/{{tenant}}",
            "retry_policy": {
                "max_retries": 1,
                "backoff_ms": 0,
                "retry_on": ["HTTP_5XX"],
            },
        }
    )
    plan = ExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        request,
        900_000,
        pre_actions=(
            {"type": "SET_VARIABLE", "name": "tenant", "value": "tenant-a"},
        ),
    )
    client = FakeExecutionClient(
        _claimed(), plan, _started(), _completed(retry_count=1)
    )
    executor = FakeExecutor(
        [
            ApiExecutionResult(503, None, "unavailable", {}, {}, 1, 1, 11),
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor
    ).process_execution_delivery(93, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert [request.url for request in executor.calls] == [
        "https://target.example.test/tenant-a",
        "https://target.example.test/tenant-a",
    ]
    completion = client.execution_calls[-1][1]
    assert completion["outcome"] == "HTTP_RESPONSE"
    assert completion["retry_count"] == 1
    assert [trace["phase"] for trace in completion["action_traces"]] == ["PRE"]


def test_execute_once_runs_post_only_pipeline_and_reports_required_extractor_failure() -> None:
    channel = FakeChannel()
    plan = ExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        ApiRequestTemplate("GET", "https://target.example.test"),
        900_000,
        extractors=(
            {
                "name": "required_id",
                "source": "JSONPATH",
                "expression": "$.missing",
                "required": True,
                "default_value": None,
                "enabled": True,
            },
        ),
    )
    client = FakeExecutionClient(_claimed(), plan, _started(), _completed())
    executor = FakeExecutor(ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 2))

    outcome = _execution_consumer(
        channel, client, executor
    ).process_execution_delivery(92, _body())

    assert outcome is ConsumeOutcome.ACKED
    completion = client.execution_calls[-1][1]
    assert completion["outcome"] == "EXECUTION_ERROR"
    assert completion["retry_count"] == 0
    assert completion["error_type"] == "RESPONSE_VALUE_MISSING"
    assert completion["error_message"] == "Response Extractor 未匹配响应值"
    traces = completion["action_traces"]
    assert isinstance(traces, list) and len(traces) == 1
    assert traces[0] == {
        "sequence": 1,
        "phase": "EXTRACTOR",
        "type": "EXTRACT_JSONPATH",
        "name": "required_id",
        "status": "FAILED",
        "duration_ms": traces[0]["duration_ms"],
        "error_type": "RESPONSE_VALUE_MISSING",
    }


def test_tampered_retry_policy_is_rejected_before_start_or_target_request() -> None:
    channel = FakeChannel()
    policy = RetryPolicy(1, 0, ("TARGET_NETWORK_ERROR",))
    object.__setattr__(policy, "max_retries", 2)
    client = FakeExecutionClient(
        _claimed(), _plan(retry_policy=policy), _started(), _completed()
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "ok", {}, {}, 1, 1, 2))

    outcome = _execution_consumer(
        channel, client, executor
    ).process_execution_delivery(90, _body())

    assert outcome is ConsumeOutcome.REJECTED
    assert [name for name, _value in client.execution_calls] == ["plan"]
    assert executor.calls == []
    assert ("basic_reject", {"delivery_tag": 90, "requeue": False}) in channel.calls


def test_target_execution_error_is_completed_then_acked() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    executor = FakeExecutor(
        ExecutionError("目标 API 网络请求失败", error_type="TARGET_NETWORK_ERROR")
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(10, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert client.execution_calls[-1][1] == {
        "outcome": "EXECUTION_ERROR",
        "retry_count": 0,
        "error_type": "TARGET_NETWORK_ERROR",
        "error_message": "目标 API 网络请求失败",
    }


def test_target_timeout_is_completed_as_timeout_then_acked() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    executor = FakeExecutor(TargetTimeoutError())

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        15, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert client.execution_calls[-1][1] == {
        "outcome": "TIMEOUT",
        "retry_count": 0,
        "error_type": "TARGET_TIMEOUT",
        "error_message": "目标 API 请求超时",
    }
    assert ("basic_ack", {"delivery_tag": 15}) in channel.calls


@pytest.mark.parametrize(
    ("first_result", "retry_on"),
    [
        (
            TargetExecutionError(
                "目标 API 网络请求失败", error_type="TARGET_NETWORK_ERROR"
            ),
            "TARGET_NETWORK_ERROR",
        ),
        (TargetTimeoutError(), "TARGET_TIMEOUT"),
        (
            ApiExecutionResult(503, None, "unavailable", {}, {}, 2, 2, 11),
            "HTTP_5XX",
        ),
    ],
)
def test_retryable_step_failure_retries_once_then_reports_final_response(
    first_result: ApiExecutionResult | Exception,
    retry_on: str,
) -> None:
    channel = FakeChannel()
    policy = RetryPolicy(max_retries=1, backoff_ms=100, retry_on=(retry_on,))
    client = FakeExecutionClient(
        _claimed(), _plan(retry_policy=policy), _started(), _completed(retry_count=1)
    )
    executor = FakeExecutor(
        [
            first_result,
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )
    sleeps: list[float] = []

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(25, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.1]
    assert client.execution_calls[-1][1]["outcome"] == "HTTP_RESPONSE"
    assert client.execution_calls[-1][1]["retry_count"] == 1


def test_retryable_timeout_exhausts_single_retry_with_bounded_backoff() -> None:
    channel = FakeChannel()
    policy = RetryPolicy(
        max_retries=1,
        backoff_ms=100,
        retry_on=("TARGET_TIMEOUT",),
    )
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=policy),
        _started(),
        _timeout_completed(),
    )
    executor = FakeExecutor(
        [TargetTimeoutError(), TargetTimeoutError()]
    )
    sleeps: list[float] = []

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(26, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.1]
    assert client.execution_calls[-1][1] == {
        "outcome": "TIMEOUT",
        "retry_count": 1,
        "error_type": "TARGET_TIMEOUT",
        "error_message": "目标 API 请求超时",
    }


@pytest.mark.parametrize(
    ("result", "expected_outcome"),
    [
        (
            ApiExecutionResult(404, None, "not found", {}, {}, 1, 1, 9),
            "HTTP_RESPONSE",
        ),
        (
            ExecutionError("响应过大", error_type="RESPONSE_TOO_LARGE"),
            "EXECUTION_ERROR",
        ),
    ],
)
def test_non_retryable_http_or_execution_failure_is_not_retried(
    result: ApiExecutionResult | Exception,
    expected_outcome: str,
) -> None:
    channel = FakeChannel()
    policy = RetryPolicy(
        max_retries=1,
        backoff_ms=100,
        retry_on=("TARGET_NETWORK_ERROR", "TARGET_TIMEOUT", "HTTP_5XX"),
    )
    client = FakeExecutionClient(
        _claimed(), _plan(retry_policy=policy), _started(), _completed()
    )
    executor = FakeExecutor(result)
    sleeps: list[float] = []

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(27, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    assert sleeps == []
    assert client.execution_calls[-1][1]["outcome"] == expected_outcome
    assert client.execution_calls[-1][1]["retry_count"] == 0


def test_cancellation_after_a_retry_preserves_actual_retry_count() -> None:
    channel = FakeChannel()
    policy = RetryPolicy(
        max_retries=1,
        backoff_ms=0,
        retry_on=("TARGET_NETWORK_ERROR",),
    )
    not_cancelled = ExecutionCancellationResult(
        1, "run-001", "message-001", 42, "RUNNING", False, False
    )
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=policy),
        _started(),
        _cancelled_completed(retry_count=1),
        cancellation=[
            not_cancelled,
            not_cancelled,
            not_cancelled,
            _cancellation_requested(),
        ],
    )
    executor = FakeExecutor(
        [
            TargetExecutionError(
                "目标 API 网络请求失败", error_type="TARGET_NETWORK_ERROR"
            ),
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=lambda _: None
    ).process_execution_delivery(28, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert client.execution_calls[-1][1] == {
        "outcome": "CANCELLED",
        "retry_count": 1,
        "error_type": "CANCEL_REQUESTED",
        "error_message": "Runner 检测到取消请求",
    }


def test_network_error_retries_then_succeeds_with_actual_retry_count() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(
        retry_policy=RetryPolicy(1, 100, ("TARGET_NETWORK_ERROR",))
    ), _started(), _completed())
    sleeps: list[float] = []
    executor = FakeExecutor(
        [
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(25, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.1]
    assert client.execution_calls[-1][1]["retry_count"] == 1


def test_timeout_retries_then_succeeds() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 0, ("TARGET_TIMEOUT",))),
        _started(),
        _completed(),
    )
    executor = FakeExecutor(
        [
            TargetTimeoutError(),
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        26, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert client.execution_calls[-1][1] == {
        "outcome": "HTTP_RESPONSE",
        "response": {
            "status_code": 200,
            "json_body": {"ok": True},
            "text": None,
            "headers": {},
            "cookies": {},
            "elapsed_ms": 1,
            "response_time_ms": 1,
        },
        "retry_count": 1,
    }


def test_http_5xx_retries_then_returns_final_response() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 250, ("HTTP_5XX",))),
        _started(),
        _completed(),
    )
    sleeps: list[float] = []
    executor = FakeExecutor(
        [
            ApiExecutionResult(503, None, "temporary", {}, {}, 1, 1, 9),
            ApiExecutionResult(503, None, "final", {}, {}, 2, 2, 5),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(27, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.25]
    assert client.execution_calls[-1][1] == {
        "outcome": "HTTP_RESPONSE",
        "response": {
            "status_code": 503,
            "json_body": None,
            "text": "final",
            "headers": {},
            "cookies": {},
            "elapsed_ms": 2,
            "response_time_ms": 2,
        },
        "retry_count": 1,
    }


def test_network_retry_is_bounded_to_one_and_reports_retry_count() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 100, ("TARGET_NETWORK_ERROR",))),
        _started(),
        _completed(),
    )
    sleeps: list[float] = []
    executor = FakeExecutor(
        [
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(28, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.1]
    assert client.execution_calls[-1][1]["outcome"] == "EXECUTION_ERROR"
    assert client.execution_calls[-1][1]["retry_count"] == 1


@pytest.mark.parametrize(
    "result",
    [
        ApiExecutionResult(404, None, "not found", {}, {}, 1, 1, 9),
        ExecutionError("assertion failure", error_type="ASSERTION_FAILED"),
    ],
)
def test_non_retryable_result_is_not_retried(result: ApiExecutionResult | Exception) -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 100, ("HTTP_5XX", "TARGET_NETWORK_ERROR"))),
        _started(),
        _completed(),
    )
    sleeps: list[float] = []
    executor = FakeExecutor(result)

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(29, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    assert sleeps == []
    assert client.execution_calls[-1][1]["retry_count"] == 0


def test_stop_event_interrupts_retry_backoff_without_second_attempt() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 30_000, ("TARGET_NETWORK_ERROR",))),
        _started(),
        _completed(),
    )
    stop_event = Event()
    stop_event.set()
    sleeps: list[float] = []
    executor = FakeExecutor(
        TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR")
    )

    outcome = _execution_consumer(
        channel, client, executor, stop_event=stop_event, sleeper=sleeps.append
    ).process_execution_delivery(30, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    assert sleeps == []
    assert client.execution_calls[-1][1]["retry_count"] == 0


def test_retry_backoff_is_truncated_by_run_deadline_without_second_attempt() -> None:
    channel = FakeChannel()
    started_at = _started().started_at
    assert started_at is not None
    deadline = started_at.timestamp() + 900.0
    now = [deadline - 0.5]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] = deadline

    client = FakeExecutionClient(
        _claimed(),
        _plan(
            retry_policy=RetryPolicy(
                max_retries=1, backoff_ms=30_000, retry_on=("TARGET_NETWORK_ERROR",)
            )
        ),
        _started(),
        _timeout_completed(),
    )
    executor = FakeExecutor(
        [
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
            ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeper, clock=clock
    ).process_execution_delivery(33, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    assert sleeps == [0.5]
    assert client.execution_calls[-1][1] == {
        "outcome": "TIMEOUT",
        "retry_count": 0,
        "error_type": "TOTAL_TIMEOUT",
        "error_message": "Run 总超时已终止执行",
    }


def test_retry_backoff_loop_is_truncated_by_run_deadline() -> None:
    channel = FakeChannel()
    started_at = _started().started_at
    assert started_at is not None
    deadline = started_at.timestamp() + 900.0
    now = [deadline - 0.25]

    class StopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, seconds: float) -> bool:
            self.waits.append(seconds)
            now[0] = deadline
            return False

        def is_set(self) -> bool:
            return False

    stop_event = StopEvent()
    client = FakeExecutionClient(
        _claimed(), _plan(), _started(), _timeout_completed()
    )
    consumer = _execution_consumer(
        channel,
        client,
        FakeExecutor(TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR")),
        stop_event=stop_event,  # type: ignore[arg-type]
        clock=lambda: now[0],
    )

    result = consumer._wait_retry_with_control(
        34,
        "run-001",
        "message-001",
        42,
        RetryPolicy(max_retries=1, backoff_ms=30_000, retry_on=("TARGET_NETWORK_ERROR",)),
        0,
        deadline=deadline,
    )

    assert stop_event.waits == [0.25]
    assert result == (False, None, ConsumeOutcome.ACKED)
    assert client.execution_calls[-1][1]["outcome"] == "TIMEOUT"


def test_cancellation_before_retry_completes_cancelled_with_zero_retry_count() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 100, ("TARGET_NETWORK_ERROR",))),
        _started(),
        _cancelled_completed(),
        cancellation=[
            ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False),
            _cancellation_requested(),
        ],
    )
    sleeps: list[float] = []
    executor = FakeExecutor(
        TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR")
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(31, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    assert sleeps == []
    assert client.execution_calls[-1][1]["outcome"] == "CANCELLED"
    assert client.execution_calls[-1][1]["retry_count"] == 0


def test_cancellation_after_retry_attempt_preserves_retry_count() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(retry_policy=RetryPolicy(1, 100, ("TARGET_NETWORK_ERROR",))),
        _started(),
        _cancelled_completed(),
        cancellation=[
            ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False),
            ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False),
            ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False),
            _cancellation_requested(),
        ],
    )
    sleeps: list[float] = []
    executor = FakeExecutor(
        [
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
            TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR"),
        ]
    )

    outcome = _execution_consumer(
        channel, client, executor, sleeper=sleeps.append
    ).process_execution_delivery(32, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 2
    assert sleeps == [0.1]
    assert client.execution_calls[-1][1]["outcome"] == "CANCELLED"
    assert client.execution_calls[-1][1]["retry_count"] == 1


def test_cancelled_execution_claim_is_acked_without_plan_start_or_http() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_cancelled(), _plan(), _started(), _completed())
    executor = FakeExecutor(ApiExecutionResult(200, None, "body", {}, {}, 1, 1, 4))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        18, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert client.execution_calls == []
    assert executor.calls == []
    assert ("basic_ack", {"delivery_tag": 18}) in channel.calls
    assert not [call for call in channel.calls if call[0] in {"basic_nack", "basic_reject"}]


def test_cancellation_check_before_executor_completes_cancelled_and_acks() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _cancelled_completed(),
        cancellation=_cancellation_requested(),
    )
    executor = FakeExecutor(ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        20, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert client.cancellation_calls == [("run-001", "message-001", 42)]
    assert executor.calls == []
    assert client.execution_calls[-1][1] == {
        "outcome": "CANCELLED",
        "retry_count": 0,
        "error_type": "CANCEL_REQUESTED",
        "error_message": "Runner 检测到取消请求",
    }
    assert ("basic_ack", {"delivery_tag": 20}) in channel.calls


def test_cancelling_start_completes_cancelled_without_cancellation_query_or_http() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _cancelling_start(), _cancelled_completed())
    executor = FakeExecutor(ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 10))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        21, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert client.cancellation_calls == []
    assert executor.calls == []
    assert client.execution_calls[-1][1]["outcome"] == "CANCELLED"
    assert ("basic_ack", {"delivery_tag": 21}) in channel.calls


def test_cancellation_check_after_executor_discards_response_and_acks_cancelled() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _cancelled_completed(),
        cancellation=[
            ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False),
            _cancellation_requested(),
        ],
    )
    executor = FakeExecutor(
        ApiExecutionResult(200, {"sensitive": True}, "sensitive body", {}, {}, 1, 1, 20)
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        22, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert executor.calls == [_plan().request]
    assert len(client.cancellation_calls) == 2
    assert client.execution_calls[-1][1] == {
        "outcome": "CANCELLED",
        "retry_count": 0,
        "error_type": "CANCEL_REQUESTED",
        "error_message": "Runner 检测到取消请求",
    }
    assert "sensitive body" not in str(client.execution_calls[-1][1])
    assert ("basic_ack", {"delivery_tag": 22}) in channel.calls


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        (TransportError("temporary", transient=True), ConsumeOutcome.REQUEUED),
        (ProtocolError("invalid cancellation response"), ConsumeOutcome.REJECTED),
        (AuthenticationError("credential invalid"), ConsumeOutcome.STOPPED_AUTH),
    ],
)
def test_cancellation_query_failure_has_safe_disposition(error, expected_outcome) -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed(), cancellation=error)
    executor = FakeExecutor(ApiExecutionResult(200, None, None, {}, {}, 1, 1, 0))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        23, _body()
    )

    assert outcome is expected_outcome
    assert executor.calls == []
    assert not [call for call in channel.calls if call[0] == "basic_ack"]
    if expected_outcome is ConsumeOutcome.REQUEUED:
        assert ("basic_nack", {"delivery_tag": 23, "requeue": True}) in channel.calls
    else:
        assert ("basic_reject", {"delivery_tag": 23, "requeue": False}) in channel.calls


def test_timeout_completion_result_is_accepted_and_acked() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _timeout_completed())
    executor = FakeExecutor(ApiExecutionResult(200, None, "body", {}, {}, 1, 1, 4))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        19, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert ("basic_ack", {"delivery_tag": 19}) in channel.calls


def test_server_cancel_override_is_accepted_and_acked() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _cancelled_completed())
    executor = FakeExecutor(ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 4))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        24, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert client.execution_calls[-1][1]["outcome"] == "HTTP_RESPONSE"
    assert ("basic_ack", {"delivery_tag": 24}) in channel.calls


def test_ctrl_c_during_execution_reports_error_before_ack() -> None:
    class InterruptingExecutor:
        def execute(self, request):
            del request
            raise KeyboardInterrupt

    channel = FakeChannel()
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    stop_event = Event()
    consumer = _execution_consumer(
        channel,
        client,
        InterruptingExecutor(),  # type: ignore[arg-type]
        stop_event=stop_event,
    )

    outcome = consumer.process_execution_delivery(14, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert stop_event.is_set()
    assert client.execution_calls[-1][1] == {
        "outcome": "EXECUTION_ERROR",
        "retry_count": 0,
        "error_type": "EXECUTOR_INTERRUPTED",
        "error_message": "Runner 收到停止信号",
    }
    assert ("basic_ack", {"delivery_tag": 14}) in channel.calls


def test_unknown_completion_result_is_requeued_without_ack() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(), _plan(), _started(), TransportError("temporary", transient=True)
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(11, _body())

    assert outcome is ConsumeOutcome.REQUEUED
    assert ("basic_nack", {"delivery_tag": 11, "requeue": True}) in channel.calls
    assert not [call for call in channel.calls if call[0] == "basic_ack"]


def test_execution_auth_failure_stops_after_reject() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(), _plan(), AuthenticationError("credential invalid"), _completed()
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(12, _body())

    assert outcome is ConsumeOutcome.STOPPED_AUTH
    assert ("basic_reject", {"delivery_tag": 12, "requeue": False}) in channel.calls
    assert ("stop_consuming", {}) in channel.calls
    assert executor.calls == []


def test_api_slot_is_busy_during_execution_and_released_after_success() -> None:
    channel = FakeChannel()
    state = SlotState({"API": 1, "WEB": 2, "PERFORMANCE": 3})

    class ObservingExecutor:
        def execute(self, request: object) -> ApiExecutionResult:
            del request
            assert state.available() == {"API": 0, "WEB": 2, "PERFORMANCE": 3}
            return ApiExecutionResult(200, {"ok": True}, None, {}, {}, 1, 1, 1)

    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    outcome = _execution_consumer(
        channel,
        client,
        ObservingExecutor(),  # type: ignore[arg-type]
        slot_state=state,
    ).process_execution_delivery(30, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert state.available() == {"API": 1, "WEB": 2, "PERFORMANCE": 3}


@pytest.mark.parametrize(
    ("claim", "plan", "expected"),
    [
        (_cancelled(), _plan(), ConsumeOutcome.ACKED),
        (_claimed(), TransportError("temporary", transient=True), ConsumeOutcome.REQUEUED),
    ],
)
def test_api_slot_is_released_after_cancel_or_control_failure(
    claim: ClaimResult,
    plan: ExecutionPlanResult | Exception,
    expected: ConsumeOutcome,
) -> None:
    channel = FakeChannel()
    state = SlotState({"API": 1, "WEB": 0, "PERFORMANCE": 0})
    client = FakeExecutionClient(claim, plan, _started(), _completed())
    executor = FakeExecutor(ApiExecutionResult(200, None, None, {}, {}, 1, 1, 1))

    outcome = _execution_consumer(
        channel,
        client,
        executor,
        slot_state=state,
    ).process_execution_delivery(31, _body())

    assert outcome is expected
    assert state.available()["API"] == 1


def test_execution_rejects_non_api_slot_without_claim() -> None:
    channel = FakeChannel()
    body = json.loads(_body())
    body["required_slot_type"] = "WEB"
    client = FakeExecutionClient(_claimed(), _plan(), _started(), _completed())
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        13, json.dumps(body).encode()
    )

    assert outcome is ConsumeOutcome.REJECTED
    assert client.calls == []
    assert executor.calls == []


class FakeTaskProcess:
    """脚本化隔离进程：记录 start/terminate，可选"永远阻塞"。"""

    def __init__(
        self,
        *,
        outcome: object | None = None,
        alive_forever: bool = False,
    ) -> None:
        self.outcome_value = outcome
        self.alive_forever = alive_forever
        self.started = False
        self.terminated = False
        self.alive = False
        self.joined = False

    def start(self) -> None:
        self.started = True
        self.alive = True

    def poll(self, timeout_seconds: float) -> bool:
        return not (self.alive_forever and not self.terminated)

    def is_alive(self) -> bool:
        return self.alive and self.alive_forever and not self.terminated

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self.alive = False

    @property
    def exitcode(self) -> int | None:
        return None

    def outcome(self) -> object | None:
        return self.outcome_value

    def close(self) -> None:
        return None


class StepClock:
    """每次调用前进指定秒数，用于可控推进 Force Stop 节流与总超时判定。"""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.now
        self.now += self.step
        return current


def test_force_stop_during_isolated_execution_terminates_child_and_completes() -> None:
    channel = FakeChannel()
    clock = StepClock(1.0)
    process = FakeTaskProcess(alive_forever=True)
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _cancelled_completed(),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            _force_stop_requested(),
        ],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(
        channel,
        client,
        executor,
        isolation_factory=lambda _request: process,
        force_stop_poll_seconds=1.0,
        clock=clock,
    ).process_execution_delivery(40, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert process.started is True
    assert process.terminated is True
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "CANCELLED"
    assert kwargs["error_type"] == "FORCE_STOP_REQUESTED"
    assert "Cleanup" in kwargs["error_message"]


def test_total_timeout_during_isolated_execution_terminates_child_and_completes() -> None:
    channel = FakeChannel()
    started_at = datetime(2026, 8, 25, 10, 1, tzinfo=UTC)
    deadline = started_at.timestamp() + 900.0
    clock = StepClock(deadline + 1.0)
    process = FakeTaskProcess(alive_forever=True)
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _timeout_completed(),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
        ],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(
        channel,
        client,
        executor,
        isolation_factory=lambda _request: process,
        force_stop_poll_seconds=1.0,
        clock=clock,
    ).process_execution_delivery(41, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert process.terminated is True
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "TIMEOUT"
    assert kwargs["error_type"] == "TOTAL_TIMEOUT"


def test_total_timeout_before_first_attempt_reports_timeout_without_execution() -> None:
    channel = FakeChannel()
    started_at = datetime(2026, 8, 25, 10, 1, tzinfo=UTC)
    past_deadline = started_at.timestamp() + 900.0 + 1.0
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _timeout_completed(),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
        ],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(
        channel, client, executor, clock=lambda: past_deadline
    ).process_execution_delivery(42, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "TIMEOUT"
    assert kwargs["error_type"] == "TOTAL_TIMEOUT"
    assert kwargs["retry_count"] == 0


def test_force_stop_before_executor_completes_without_http() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _cancelled_completed(),
        cancellation=[_force_stop_requested()],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        43, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "CANCELLED"
    assert kwargs["error_type"] == "FORCE_STOP_REQUESTED"


def test_force_stop_during_retry_backoff_skips_second_attempt() -> None:
    channel = FakeChannel()
    client = FakeExecutionClient(
        _claimed(),
        _plan(
            retry_policy=RetryPolicy(
                max_retries=1, backoff_ms=0, retry_on=("TARGET_NETWORK_ERROR",)
            )
        ),
        _started(),
        _cancelled_completed(retry_count=0),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            _force_stop_requested(),
        ],
    )
    executor = FakeExecutor(
        TargetExecutionError("temporary", error_type="TARGET_NETWORK_ERROR")
    )

    outcome = _execution_consumer(channel, client, executor).process_execution_delivery(
        44, _body()
    )

    assert outcome is ConsumeOutcome.ACKED
    assert len(executor.calls) == 1
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "CANCELLED"
    assert kwargs["error_type"] == "FORCE_STOP_REQUESTED"
    assert kwargs["retry_count"] == 0


def test_isolated_child_crash_reports_execution_error_then_acks() -> None:
    channel = FakeChannel()
    process = FakeTaskProcess(outcome=None)
    client = FakeExecutionClient(
        _claimed(),
        _plan(),
        _started(),
        _completed(),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
        ],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(
        channel,
        client,
        executor,
        isolation_factory=lambda _request: process,
        clock=lambda: 0.0,
    ).process_execution_delivery(45, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "EXECUTION_ERROR"
    assert kwargs["error_type"] == "EXECUTOR_ERROR"


def test_isolated_child_timeout_maps_to_target_timeout_and_retries() -> None:
    channel = FakeChannel()
    process = FakeTaskProcess(
        outcome=IsolatedOutcome(
            kind="TARGET_TIMEOUT",
            error_type="TARGET_TIMEOUT",
            error_message="目标执行超时",
        )
    )
    client = FakeExecutionClient(
        _claimed(),
        _plan(
            retry_policy=RetryPolicy(
                max_retries=1, backoff_ms=0, retry_on=("TARGET_TIMEOUT",)
            )
        ),
        _started(),
        _timeout_completed(),
        cancellation=[
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
            ExecutionCancellationResult(
                1, "run-001", "message-001", 42, "RUNNING", False, False
            ),
        ],
    )
    executor = FakeExecutor(ApiExecutionResult(200, None, "", {}, {}, 0, 0, 0))

    outcome = _execution_consumer(
        channel,
        client,
        executor,
        isolation_factory=lambda _request: process,
        clock=lambda: 0.0,
    ).process_execution_delivery(46, _body())

    assert outcome is ConsumeOutcome.ACKED
    assert executor.calls == []
    kwargs = client.execution_calls[-1][1]
    assert kwargs["outcome"] == "TIMEOUT"
    assert kwargs["error_type"] == "TARGET_TIMEOUT"
    assert kwargs["retry_count"] == 1
