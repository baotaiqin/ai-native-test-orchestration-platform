from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from threading import Event

import httpx
import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import ProtocolError, TargetExecutionError, TargetTimeoutError
from runner.executors.api import ApiExecutor
from runner.isolation import (
    InlineTaskProcess,
    IsolatedOutcome,
    _run_scenario_task_worker,
    spawn_scenario_task_process,
)
from runner.models import (
    ClaimResult,
    ExecutionCancellationResult,
    HttpResponse,
    RunnerIdentity,
    ScenarioExecutionCompleteResult,
    ScenarioExecutionEvaluationResult,
    ScenarioExecutionPlanResult,
    ScenarioExecutionSecret,
    ScenarioExecutionStartResult,
    ScenarioNodePlan,
)
from runner.protocol import RunnerClient
from runner.scenario import ScenarioCleanupTask, ScenarioExecutor
from tests.helpers import FakeTransport

IDENTITY = RunnerIdentity("runner-001", "credential-value-123456789")


def _node(
    node_id: str,
    node_type: str,
    *,
    parent_id: str | None = None,
    config: dict[str, object] | None = None,
    enabled: bool = True,
    failure_policy: str | None = None,
) -> ScenarioNodePlan:
    return ScenarioNodePlan(
        node_id,
        node_type,
        node_id,
        parent_id,
        enabled,
        1000,
        failure_policy,
        config or {},
    )


def _plan(*nodes: ScenarioNodePlan, total_timeout_ms: int = 10_000) -> ScenarioExecutionPlanResult:
    return ScenarioExecutionPlanResult(
        1,
        "run-001",
        "message-001",
        "runner-001",
        42,
        7,
        8,
        total_timeout_ms,
        {"flag": True, "number": 2, "obj": {"value": "ok"}},
        {
            "stop_on_failure": True,
            "cleanup_policy": "ALWAYS",
            "max_loop_iterations": 10,
            "initial_variables": [],
        },
        tuple(nodes),
        (),
        (ScenarioExecutionSecret(1, "secret-not-in-output"),),
    )


def _scenario_body() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "message_id": "message-001",
            "run_id": "run-001",
            "runner_id": "runner-001",
            "run_type": "SCENARIO",
            "required_slot_type": "API",
            "attempt": 1,
            "enqueued_at": "2026-08-30T10:00:00Z",
        }
    ).encode()


def _scenario_plan_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "scenario_id": 7,
        "scenario_version_id": 8,
        "total_timeout_ms": 10_000,
        "initial_context": {"flag": True, "number": 2},
        "settings": {
            "stop_on_failure": True,
            "cleanup_policy": "ALWAYS",
            "max_loop_iterations": 10,
            "initial_variables": [],
        },
        "nodes": [
            {
                "node_id": "start",
                "node_type": "START",
                "name": "Start",
                "parent_id": None,
                "enabled": True,
                "timeout_ms": 1000,
                "failure_policy": None,
                "config": {},
            },
            {
                "node_id": "end",
                "node_type": "END",
                "name": "End",
                "parent_id": None,
                "enabled": True,
                "timeout_ms": 1000,
                "failure_policy": None,
                "config": {},
            },
        ],
        "sql_connections": [],
        "secrets": [{"secret_id": 1, "value": "secret-not-in-output"}],
    }


def _start_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "status": "RUNNING",
        "started_at": "2026-08-30T10:00:00Z",
        "idempotent": False,
    }


def _complete_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "outcome": "SUCCESS",
        "run_status": "SUCCESS",
        "case_run_status": "SUCCESS",
        "traces": [
            {
                "node_id": "start",
                "status": "SUCCESS",
                "duration_ms": 0,
                "retry_count": 0,
                "error_type": None,
                "error_message": None,
            },
            {
                "node_id": "end",
                "status": "SUCCESS",
                "duration_ms": 0,
                "retry_count": 0,
                "error_type": None,
                "error_message": None,
            },
        ],
        "error_type": None,
        "error_message": None,
        "completed_at": "2026-08-30T10:01:00Z",
        "idempotent": False,
    }


def test_scenario_protocol_uses_protected_routes_and_exact_fields() -> None:
    transport = FakeTransport(
        HttpResponse(200, _scenario_plan_body()),
        HttpResponse(200, _start_body()),
        HttpResponse(200, _complete_body()),
    )
    client = RunnerClient("https://backend.example.test", transport)
    plan = client.get_scenario_execution_plan(IDENTITY, "run-001", "message-001")
    started = client.scenario_execution_start(IDENTITY, "run-001", "message-001", 42)
    complete = client.scenario_execution_complete(
        IDENTITY,
        "run-001",
        "message-001",
        42,
        outcome="SUCCESS",
        traces=_complete_body()["traces"],  # type: ignore[arg-type]
    )
    assert plan.scenario_version_id == 8
    assert plan.initial_context["number"] == 2
    assert started.status == "RUNNING"
    assert complete.outcome == "SUCCESS"
    assert (
        "scenario-execution-plan?message_id=message-001"
        in transport.calls[0]["url"]
    )
    assert "case_run_id=" not in transport.calls[0]["url"]
    assert transport.calls[2]["json"] == {
        "message_id": "message-001",
        "case_run_id": 42,
        "outcome": "SUCCESS",
        "traces": _complete_body()["traces"],
        "error_type": None,
        "error_message": None,
    }
    assert "secret-not-in-output" not in repr(plan)


@pytest.mark.parametrize(
    "field,value",
    [
        ("unexpected", True),
        ("initial_context", {"token": "secret"}),
        ("initial_context", {"message": "Bearer abcdefghijkl"}),
        ("settings", {"stop_on_failure": True}),
    ],
)
def test_scenario_protocol_rejects_unknown_or_sensitive_plan_fields(
    field: str, value: object
) -> None:
    body = _scenario_plan_body()
    if field == "unexpected":
        body[field] = value
    else:
        body[field] = value
    with pytest.raises(ProtocolError):
        RunnerClient(
            "https://backend.example.test", FakeTransport(HttpResponse(200, body))
        ).get_scenario_execution_plan(IDENTITY, "run-001", "message-001")


def test_scenario_control_flow_preserves_typed_context_and_one_trace_per_node() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("set", "SET_VARIABLE", config={"name": "value", "value": "{{number}}"}),
        _node("branch", "IF", config={"condition": "{{flag}} == true"}),
        _node(
            "then_set",
            "SET_VARIABLE",
            parent_id="branch",
            config={"name": "branch", "value": "then"},
        ),
        _node("else", "ELSE", parent_id="branch"),
        _node(
            "else_set",
            "SET_VARIABLE",
            parent_id="else",
            config={"name": "branch", "value": "else"},
        ),
        _node("loop", "LOOP", config={"iterations": 2, "item_variable": "item"}),
        _node(
            "loop_set",
            "SET_VARIABLE",
            parent_id="loop",
            config={"name": "last_item", "value": "{{item}}"},
        ),
        _node("end", "END"),
    )
    result = ScenarioExecutor(plan, sleeper=lambda _seconds: None).execute()
    assert result.outcome == "SUCCESS"
    assert len(result.traces) == len(plan.nodes)
    assert len({trace["node_id"] for trace in result.traces}) == len(plan.nodes)
    assert [trace["status"] for trace in result.traces if trace["node_id"] == "else"] == [
        "SKIPPED"
    ]
    assert result.traces[1]["status"] == "SUCCESS"


class _ScenarioHttpClient:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _scenario_http_response(status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"data": {"id": 7, "ok": True}, "private": "do-not-trace"},
        headers={"X-Trace": "trace-value", "Set-Cookie": "session=cookie-value; Path=/"},
        request=httpx.Request("GET", "https://target.example.test"),
    )


def test_scenario_http_extract_and_assert_use_typed_request_and_private_snapshot() -> None:
    plan = _plan(
        _node("start", "START"),
        _node(
            "http",
            "HTTP",
            config={
                "method": "POST",
                "url": "https://target.example.test/{{number}}",
                "query_params": [{"name": "enabled", "value": "{{flag}}"}],
                "headers": [{"name": "X-Number", "value": "{{number}}"}],
                "body": {
                    "type": "JSON",
                    "content": {"number": "{{number}}"},
                },
                "auth": {"type": "NONE"},
                "cookies": [],
            },
        ),
        _node("extract_json", "EXTRACT", config={
            "name": "response_id", "source": "JSONPATH", "expression": "$.data.id"
        }),
        _node("extract_header", "EXTRACT", config={
            "name": "trace", "source": "HEADER", "expression": "x-trace"
        }),
        _node("extract_cookie", "EXTRACT", config={
            "name": "session", "source": "COOKIE", "expression": "SESSION"
        }),
        _node("assert_status", "ASSERT_STATUS", config={"expected": 200}),
        _node("assert_json", "ASSERT_JSONPATH", config={
            "expression": "$.data.id", "expected": "{{response_id}}"
        }),
        _node("end", "END"),
    )
    client = _ScenarioHttpClient(_scenario_http_response())
    executor = ScenarioExecutor(plan, api_executor=ApiExecutor(client))

    result = executor.execute()

    assert result.outcome == "SUCCESS"
    assert executor.context["response_id"] == 7
    assert executor.context["trace"] == "trace-value"
    assert executor.context["session"] == "cookie-value"
    assert client.calls[0][0:2] == ("POST", "https://target.example.test/2")
    assert client.calls[0][2]["params"] == [("enabled", "true")]
    assert client.calls[0][2]["headers"] == {"X-Number": "2"}
    assert client.calls[0][2]["json"] == {"number": 2}
    wire = json.dumps(result.to_wire(), ensure_ascii=False)
    assert "private" not in wire


def test_scenario_http_sends_resolved_auth_cookie_and_sensitive_fields() -> None:
    plan = _plan(
        _node("start", "START"),
        _node(
            "http",
            "HTTP",
            config={
                "method": "POST",
                "url": "https://target.example.test/secure",
                "query_params": [
                    {"name": "access_token", "value": "query-secret"}
                ],
                "headers": [{"name": "X-API-Key", "value": "header-secret"}],
                "cookies": [{"name": "session", "value": "cookie-secret"}],
                "body": {
                    "type": "JSON",
                    "content": {"password": "body-secret"},
                },
                "auth": {"type": "BEARER", "token": "bearer-secret"},
                "follow_redirects": False,
                "retry_policy": {"max_retries": 0},
            },
        ),
        _node("end", "END"),
    )
    client = _ScenarioHttpClient(_scenario_http_response())

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.outcome == "SUCCESS"
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://target.example.test/secure")
    assert kwargs["params"] == [("access_token", "query-secret")]
    assert kwargs["headers"] == {
        "X-API-Key": "header-secret",
        "Authorization": "Bearer bearer-secret",
    }
    assert kwargs["cookies"] == {"session": "cookie-secret"}
    assert kwargs["json"] == {"password": "body-secret"}
    wire = json.dumps(result.to_wire(), ensure_ascii=False)
    for secret in (
        "query-secret",
        "header-secret",
        "cookie-secret",
        "body-secret",
        "bearer-secret",
    ):
        assert secret not in wire
    assert "cookie-value" not in wire
    assert all(trace["duration_ms"] >= 0 for trace in result.traces)


@pytest.mark.parametrize(
    ("source", "expression", "expected"),
    [
        ("JSONPATH", "$.missing", "fallback"),
        ("HEADER", "Missing-Header", 9),
        ("COOKIE", "missing-cookie", False),
    ],
)
def test_scenario_extract_optional_missing_value_uses_default(
    source: str, expression: str, expected: object
) -> None:
    name = "extracted"
    plan = _plan(
        _node("start", "START"),
        _node("http", "HTTP", config={"url": "https://target.example.test"}),
        _node(
            "extract",
            "EXTRACT",
            config={
                "name": name,
                "source": source,
                "expression": expression,
                "required": False,
                "default_value": expected,
            },
        ),
        _node("end", "END"),
    )
    client = _ScenarioHttpClient(_scenario_http_response())

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.outcome == "SUCCESS"
    assert result.traces[2]["status"] == "SUCCESS"


def test_scenario_extract_required_missing_value_fails_without_actual_value() -> None:
    secret = "response-secret-value"
    response = httpx.Response(200, json={"secret": secret})
    plan = _plan(
        _node("start", "START"),
        _node("http", "HTTP", config={"url": "https://target.example.test"}),
        _node(
            "extract",
            "EXTRACT",
            config={"name": "missing", "source": "JSONPATH", "expression": "$.id"},
        ),
        _node("end", "END"),
    )

    result = ScenarioExecutor(
        plan,
        api_executor=ApiExecutor(_ScenarioHttpClient(response)),
    ).execute()

    assert result.outcome == "FAILED"
    assert "RESPONSE_VALUE_MISSING" in json.dumps(result.to_wire())
    assert secret not in json.dumps(result.to_wire())


@pytest.mark.parametrize(
    "node_type,config",
    [
        ("ASSERT_STATUS", {"expected": 201}),
        ("ASSERT_JSONPATH", {"expression": "$.data.id", "expected": 8}),
    ],
)
def test_scenario_assertion_failure_is_safe_and_not_retryable(
    node_type: str, config: dict[str, object]
) -> None:
    secret = "assertion-private-value"
    plan = _plan(
        _node("start", "START"),
        _node("http", "HTTP", config={"url": "https://target.example.test"}),
        _node("assert", node_type, config=config, failure_policy="RETRY_ONCE"),
        _node("end", "END"),
    )
    client = _ScenarioHttpClient(
        httpx.Response(200, json={"data": {"id": 7, "secret": secret}})
    )

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.outcome == "FAILED"
    trace = next(item for item in result.traces if item["node_id"] == "assert")
    assert trace["error_type"] == "ASSERTION_FAILED"
    assert trace["retry_count"] == 0
    assert secret not in json.dumps(result.to_wire())
    assert len(client.calls) == 1


def test_scenario_http_5xx_retries_once_and_aggregates_trace() -> None:
    client = _ScenarioHttpClient(
        _scenario_http_response(503),
        _scenario_http_response(200),
    )
    plan = _plan(
        _node("start", "START"),
        _node(
            "http",
            "HTTP",
            failure_policy="RETRY_ONCE",
            config={"url": "https://target.example.test"},
        ),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.outcome == "SUCCESS"
    assert len(client.calls) == 2
    trace = next(item for item in result.traces if item["node_id"] == "http")
    assert trace["status"] == "SUCCESS"
    assert trace["retry_count"] == 1
    assert trace["duration_ms"] >= 0


@pytest.mark.parametrize("failure", [TargetExecutionError("hidden"), TargetTimeoutError()])
def test_scenario_http_target_failure_is_safe_and_retries_once(
    failure: Exception,
) -> None:
    client = _ScenarioHttpClient(failure, failure)
    plan = _plan(
        _node("start", "START"),
        _node(
            "http",
            "HTTP",
            failure_policy="RETRY_ONCE",
            config={"url": "https://target.example.test"},
        ),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.outcome == ("TIMEOUT" if isinstance(failure, TargetTimeoutError) else "FAILED")
    assert len(client.calls) == 2
    assert "hidden" not in json.dumps(result.to_wire())


def test_scenario_unsupported_node_fails_closed_without_secret_or_context_output() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("http", "HTTP", config={"url": "https://target.example.test", "token": "secret"}),
        _node("end", "END"),
    )
    result = ScenarioExecutor(plan).execute()
    assert result.outcome == "FAILED"
    assert next(item for item in result.traces if item["node_id"] == "http")["status"] == "FAILED"
    assert "secret" not in repr(result)
    assert "secret" not in json.dumps(result.to_wire())


def test_scenario_stop_failure_skips_following_siblings() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("failed", "HTTP", failure_policy="STOP"),
        _node("after", "SET_VARIABLE", config={"name": "after", "value": True}),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan).execute()

    assert result.outcome == "FAILED"
    assert next(trace for trace in result.traces if trace["node_id"] == "after")[
        "status"
    ] == "SKIPPED"
    assert next(trace for trace in result.traces if trace["node_id"] == "end")["status"] == (
        "SKIPPED"
    )


def test_scene_stop_overrides_explicit_node_continue() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("failed", "HTTP", failure_policy="CONTINUE"),
        _node("after", "SET_VARIABLE", config={"name": "after", "value": True}),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan).execute()

    assert result.outcome == "FAILED"
    assert next(trace for trace in result.traces if trace["node_id"] == "after")[
        "status"
    ] == "SKIPPED"


def test_scenario_loop_stop_failure_does_not_continue_siblings_or_iterations() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("loop", "LOOP", config={"iterations": 3}),
        _node("failed", "HTTP", parent_id="loop", failure_policy="STOP"),
        _node(
            "after_failed",
            "SET_VARIABLE",
            parent_id="loop",
            config={"name": "after", "value": True},
        ),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan).execute()

    assert result.outcome == "FAILED"
    assert next(
        trace for trace in result.traces if trace["node_id"] == "after_failed"
    )["status"] == "SKIPPED"
    assert next(trace for trace in result.traces if trace["node_id"] == "end")["status"] == (
        "SKIPPED"
    )


def test_scenario_cancel_and_total_timeout_are_terminal() -> None:
    cancelled = Event()
    cancelled.set()
    plan = _plan(_node("start", "START"), _node("end", "END"))
    result = ScenarioExecutor(plan, stop_event=cancelled).execute()
    assert result.outcome == "CANCELLED"
    timed_out = ScenarioExecutor(plan, clock=lambda: 10.0, deadline=9.0).execute()
    assert timed_out.outcome == "TIMEOUT"


def test_scenario_spawn_entry_returns_safe_result() -> None:
    plan = _plan(_node("start", "START"), _node("end", "END"))
    process = InlineTaskProcess(_run_scenario_task_worker, {"plan": plan})
    process.start()
    assert process.poll(0) is True
    outcome = process.outcome()
    assert outcome is not None and outcome.kind == "SCENARIO_RESULT"
    assert outcome.result is not None and outcome.result["outcome"] == "SUCCESS"
    process.close()


def test_inline_scenario_process_propagates_cooperative_cancel_event() -> None:
    plan = _plan(_node("start", "START"), _node("end", "END"))
    process = InlineTaskProcess(_run_scenario_task_worker, {"plan": plan})
    process.request_cancel()

    process.start()

    outcome = process.outcome()
    assert outcome is not None and outcome.kind == "SCENARIO_RESULT"
    assert outcome.result is not None and outcome.result["outcome"] == "CANCELLED"
    process.close()


def test_scenario_spawn_process_runs_child_and_closes_cleanly() -> None:
    plan = _plan(_node("start", "START"), _node("end", "END"))
    process = spawn_scenario_task_process(plan)
    try:
        process.start()
        assert process.poll(5.0) is True
        outcome = process.outcome()
        assert outcome is not None and outcome.kind == "SCENARIO_RESULT"
        assert outcome.result is not None and outcome.result["outcome"] == "SUCCESS"
    finally:
        process.close()


def test_real_windows_spawn_wait_can_be_cooperatively_cancelled() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("wait", "WAIT", config={"duration_ms": 5_000},),
        _node("end", "END"),
    )
    process = spawn_scenario_task_process(plan)
    try:
        process.start()
        assert process.is_alive()
        time.sleep(0.1)
        process.request_cancel()
        assert process.poll(5.0) is True
        outcome = process.outcome()
        assert outcome is not None and outcome.kind == "SCENARIO_RESULT"
        assert outcome.result is not None and outcome.result["outcome"] == "CANCELLED"
    finally:
        process.close()


class _Channel:
    def __init__(self) -> None:
        self.acks: list[object] = []
        self.rejects: list[object] = []
        self.nacks: list[object] = []

    def basic_ack(self, **kwargs: object) -> None:
        self.acks.append(kwargs["delivery_tag"])

    def basic_reject(self, **kwargs: object) -> None:
        self.rejects.append(kwargs["delivery_tag"])

    def basic_nack(self, **kwargs: object) -> None:
        self.nacks.append(kwargs["delivery_tag"])


class _ScenarioClient:
    def __init__(self, plan: ScenarioExecutionPlanResult) -> None:
        self.plan = plan
        self.calls: list[str] = []
        self.plan_case_run_ids: list[int | None] = []

    def claim(self, identity: RunnerIdentity, run_id: str, message_id: str) -> ClaimResult:
        del identity, run_id, message_id
        self.calls.append("claim")
        return ClaimResult(
            "run-001", "message-001", "runner-001", "ASSIGNED", datetime.now(UTC), False
        )

    def get_scenario_execution_plan(
        self,
        identity: RunnerIdentity,
        run_id: str,
        message_id: str,
        case_run_id: int | None = None,
    ) -> ScenarioExecutionPlanResult:
        del identity, run_id, message_id
        self.calls.append("plan")
        self.plan_case_run_ids.append(case_run_id)
        return self.plan

    def scenario_execution_start(self, *args: object) -> ScenarioExecutionStartResult:
        self.calls.append("start")
        return ScenarioExecutionStartResult(
            1, "run-001", "message-001", "runner-001", 42, "RUNNING", datetime.now(UTC), False
        )

    def get_execution_cancellation(self, *args: object) -> ExecutionCancellationResult:
        self.calls.append("cancel-check")
        return ExecutionCancellationResult(1, "run-001", "message-001", 42, "RUNNING", False, False)

    def scenario_execution_complete(
        self, *args: object, **kwargs: object
    ) -> ScenarioExecutionCompleteResult:
        self.calls.append("complete")
        return ScenarioExecutionCompleteResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            42,
            str(kwargs["outcome"]),
            str(kwargs["outcome"]),
            str(kwargs["outcome"]),
            [],
            kwargs.get("error_type"),
            kwargs.get("error_message"),
            datetime.now(UTC),
            False,
        )


class _CancelDuringScenarioClient(_ScenarioClient):
    def __init__(self, plan: ScenarioExecutionPlanResult) -> None:
        super().__init__(plan)
        self.cancel_checks = 0

    def get_execution_cancellation(self, *args: object) -> ExecutionCancellationResult:
        self.cancel_checks += 1
        self.calls.append("cancel-check")
        return ExecutionCancellationResult(
            1,
            "run-001",
            "message-001",
            42,
            "CANCELLING" if self.cancel_checks >= 2 else "RUNNING",
            self.cancel_checks >= 2,
            False,
        )


class _CancellableScenarioProcess:
    def __init__(self) -> None:
        self.cancel_requested = False
        self.started = False
        self.alive = False
        self.outcome_value: IsolatedOutcome | None = None
        self.terminated = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        self.alive = True

    def poll(self, timeout_seconds: float) -> bool:
        del timeout_seconds
        if self.cancel_requested:
            self.alive = False
            self.outcome_value = IsolatedOutcome(
                kind="SCENARIO_RESULT",
                result={
                    "outcome": "CANCELLED",
                    "traces": [],
                    "error_type": "CANCEL_REQUESTED",
                    "error_message": "Runner 检测到取消请求",
                },
            )
            return True
        return False

    def is_alive(self) -> bool:
        return self.alive

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        del timeout

    @property
    def exitcode(self) -> int | None:
        return None

    def outcome(self) -> IsolatedOutcome | None:
        return self.outcome_value

    def close(self) -> None:
        self.closed = True


def test_scenario_consumer_completes_before_ack_and_uses_spawn_boundary() -> None:
    plan = _plan(_node("start", "START"), _node("end", "END"))
    channel = _Channel()
    client = _ScenarioClient(plan)
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
        scenario_isolation_factory=lambda value: InlineTaskProcess(
            _run_scenario_task_worker, {"plan": value}
        ),
    )
    result = consumer.process_execution_delivery(11, _scenario_body())
    assert result is ConsumeOutcome.ACKED
    assert client.calls[:2] == ["claim", "plan"]
    assert client.plan_case_run_ids == [None]
    assert "start" in client.calls and "complete" in client.calls
    assert channel.acks == [11]
    assert channel.rejects == []


def test_scenario_consumer_requests_cooperative_cancel_and_completes_cancelled() -> None:
    plan = _plan(_node("start", "START"), _node("end", "END"))
    channel = _Channel()
    client = _CancelDuringScenarioClient(plan)
    process = _CancellableScenarioProcess()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
        scenario_isolation_factory=lambda value: process,
        force_stop_poll_seconds=0.1,
    )

    result = consumer.process_execution_delivery(12, _scenario_body())

    assert result is ConsumeOutcome.ACKED
    assert process.started is True
    assert process.cancel_requested is True
    assert process.terminated is False
    assert process.closed is True
    assert channel.acks == [12]
    assert "complete" in client.calls


def test_scenario_ai_assertion_defers_cleanup_and_keeps_response_off_public_wire() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={"healthy": True, "token": "private-response-value"},
            headers={"X-Trace": "trace-value"},
        )

    api_executor = ApiExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    plan = _plan(
        _node("start", "START"),
        _node(
            "request",
            "HTTP",
            config={"method": "GET", "url": "https://target.test/health"},
        ),
        _node(
            "semantic",
            "AI_ASSERTION",
            config={
                "prompt_id": 7,
                "criteria": "response is healthy",
                "confidence_threshold": 0.8,
            },
        ),
        _node(
            "cleanup",
            "API_CLEANUP",
            config={
                "cleanup_type": "API",
                "policy": "ON_FAILURE",
                "enabled": True,
                "timeout_ms": 1000,
                "method": "DELETE",
                "url": "https://target.test/resource/1",
                "query_params": [],
                "headers": [],
                "cookies": [],
                "body": {"type": "NONE"},
                "auth": {"type": "NONE"},
            },
        ),
        _node("end", "END"),
    )
    result = ScenarioExecutor(plan, api_executor=api_executor).execute()
    assert result.outcome == "SUCCESS"
    assert [item["node_id"] for item in result.traces] == [
        "start", "request", "semantic", "end"
    ]
    assert "private-response-value" not in json.dumps(result.to_wire())
    worker_wire = result.to_worker_wire()
    assert worker_wire["pending_ai"][0]["node_id"] == "semantic"
    assert worker_wire["pending_ai"][0]["response"]["json_body"]["healthy"] is True
    cleanup = ScenarioExecutor.execute_cleanup_task(
        ScenarioCleanupTask(plan, worker_wire["cleanup_context"], "FAILURE"),
        api_executor=api_executor,
    )
    assert cleanup.outcome == "SUCCESS"
    assert cleanup.traces[0]["node_id"] == "cleanup"
    assert cleanup.traces[0]["status"] == "SUCCESS"
    assert requests == ["GET /health", "DELETE /resource/1"]
    api_executor.close()


def test_scenario_ai_evaluation_protocol_is_strict_and_token_is_redacted() -> None:
    body = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "assertion_status": "REVIEW",
        "cleanup_outcome": "FAILURE",
        "assertion_results": [
            {
                "sequence": 1,
                "name": "Semantic",
                "type": "AI_SEMANTIC",
                "status": "REVIEW",
                "expected": None,
                "actual": None,
                "message": "review",
                "duration_ms": 1,
                "confidence": 0.4,
                "reason": "insufficient",
                "ai_call_id": 9,
                "actual_model": "model",
                "fallback_used": False,
                "repair_used": False,
            }
        ],
        "node_results": [{"node_id": "semantic", "status": "REVIEW"}],
        "evaluation_token": "signed-evaluation-token",
    }
    transport = FakeTransport(HttpResponse(200, body))
    client = RunnerClient("https://backend.example.test", transport)
    result = client.scenario_execution_evaluate(
        IDENTITY,
        "run-001",
        "message-001",
        42,
        [
            {
                "node_id": "semantic",
                "response": {
                    "status_code": 200,
                    "json_body": {"healthy": True},
                    "headers": {},
                    "cookies": {},
                    "elapsed_ms": 1,
                    "response_time_ms": 1,
                },
            }
        ],
    )
    assert isinstance(result, ScenarioExecutionEvaluationResult)
    assert result.node_results == [{"node_id": "semantic", "status": "REVIEW"}]
    assert "signed-evaluation-token" not in repr(result)
    assert transport.calls[0]["url"].endswith("/scenario-execution-evaluate")


class _StaticScenarioProcess:
    def __init__(self, outcome: IsolatedOutcome) -> None:
        self._outcome = outcome

    def start(self) -> None:
        return None

    def poll(self, timeout_seconds: float) -> bool:
        del timeout_seconds
        return True

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        return None

    def request_cancel(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        del timeout

    @property
    def exitcode(self) -> int:
        return 0

    def outcome(self) -> IsolatedOutcome:
        return self._outcome

    def close(self) -> None:
        return None


class _ScenarioAiClient(_ScenarioClient):
    def __init__(self, plan: ScenarioExecutionPlanResult) -> None:
        super().__init__(plan)
        self.completion: dict[str, object] | None = None

    def scenario_execution_evaluate(
        self, *args: object
    ) -> ScenarioExecutionEvaluationResult:
        self.calls.append("evaluate")
        evaluations = args[-1]
        assert isinstance(evaluations, list)
        assert evaluations[0]["node_id"] == "semantic"
        return ScenarioExecutionEvaluationResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            42,
            "REVIEW",
            "FAILURE",
            [],
            [{"node_id": "semantic", "status": "REVIEW"}],
            "signed-token",
        )

    def scenario_execution_complete(
        self, *args: object, **kwargs: object
    ) -> ScenarioExecutionCompleteResult:
        self.calls.append("complete")
        self.completion = dict(kwargs)
        return ScenarioExecutionCompleteResult(
            1,
            "run-001",
            "message-001",
            "runner-001",
            42,
            "FAILED",
            "FAILED",
            "REVIEW",
            list(kwargs["traces"]),
            str(kwargs["error_type"]),
            str(kwargs["error_message"]),
            datetime.now(UTC),
            False,
            [],
        )


def test_scenario_consumer_evaluates_ai_before_failure_cleanup_and_completion() -> None:
    plan = _plan(
        _node("start", "START"),
        _node("request", "HTTP", config={"method": "GET", "url": "https://target.test"}),
        _node(
            "semantic",
            "AI_ASSERTION",
            config={"prompt_id": 1, "criteria": "healthy", "confidence_threshold": 0.8},
        ),
        _node(
            "cleanup",
            "API_CLEANUP",
            config={
                "cleanup_type": "API",
                "policy": "ON_FAILURE",
                "enabled": True,
                "timeout_ms": 1000,
                "method": "DELETE",
                "url": "https://target.test/resource/1",
                "query_params": [],
                "headers": [],
                "cookies": [],
                "body": {"type": "NONE"},
                "auth": {"type": "NONE"},
            },
        ),
        _node("end", "END"),
    )
    def trace(node_id: str, *, duration_ms: int = 0) -> dict[str, object]:
        return {
            "node_id": node_id,
            "status": "SUCCESS",
            "duration_ms": duration_ms,
            "retry_count": 0,
            "error_type": None,
            "error_message": None,
        }

    primary = IsolatedOutcome(
        kind="SCENARIO_RESULT",
        result={
            "outcome": "SUCCESS",
            "traces": [
                trace("start"),
                trace("request", duration_ms=1),
                trace("semantic"),
                trace("end"),
            ],
            "error_type": None,
            "error_message": None,
            "pending_ai": [
                {
                    "node_id": "semantic",
                    "response": {"status_code": 200, "json_body": {"ok": True}},
                }
            ],
            "cleanup_context": {"resource_id": "1"},
        },
    )
    cleanup = IsolatedOutcome(
        kind="SCENARIO_CLEANUP_RESULT",
        result={
            "outcome": "SUCCESS",
            "traces": [trace("cleanup", duration_ms=1)],
            "error_type": None,
            "error_message": None,
        },
    )
    channel = _Channel()
    client = _ScenarioAiClient(plan)
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 1, "WEB": 0, "PERFORMANCE": 0},
        scenario_isolation_factory=lambda value: _StaticScenarioProcess(primary),
        scenario_cleanup_isolation_factory=lambda value: _StaticScenarioProcess(cleanup),
    )
    outcome = consumer.process_execution_delivery(13, _scenario_body())
    assert outcome is ConsumeOutcome.ACKED
    assert client.calls.index("evaluate") < client.calls.index("complete")
    assert client.completion is not None
    assert client.completion["outcome"] == "FAILED"
    assert client.completion["evaluation_token"] == "signed-token"
    traces = client.completion["traces"]
    assert isinstance(traces, list)
    assert [item["node_id"] for item in traces] == [
        "start", "request", "semantic", "cleanup", "end"
    ]
    assert next(item for item in traces if item["node_id"] == "semantic")["status"] == "REVIEW"
    assert channel.acks == [13]
