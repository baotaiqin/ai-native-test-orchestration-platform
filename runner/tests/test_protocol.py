from datetime import UTC, datetime

import pytest

from runner.errors import (
    AuthenticationError,
    ConfigurationError,
    ProtocolError,
    RegistrationOutcomeUnknownError,
    TransportError,
)
from runner.models import HttpResponse, RunnerIdentity
from runner.protocol import RunnerClient, _web_trace_payload
from tests.helpers import FakeTransport, snapshot


def test_performance_plan_protocol_accepts_fixed_rps_v2() -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "schema_version": 2,
                "run_id": "run-001",
                "message_id": "message-001",
                "runner_id": "runner-001",
                "case_run_id": 42,
                "request": _request_template(),
                "concurrency": 4,
                "iterations": 6,
                "load_mode": "FIXED_RPS",
                "target_rps": 3,
                "duration_seconds": 2,
                "warmup_iterations": 1,
                "request_timeout_ms": 1000,
                "total_timeout_ms": 60_000,
            },
        )
    )

    plan = RunnerClient("https://example.test", transport).get_performance_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert plan.schema_version == 2
    assert plan.load_mode == "FIXED_RPS"
    assert plan.target_rps == 3
    assert plan.duration_seconds == 2
    assert plan.iterations == 6


def test_performance_plan_protocol_accepts_step_load_scenario_v3() -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "schema_version": 3,
                "run_id": "run-001",
                "message_id": "message-001",
                "runner_id": "runner-001",
                "case_run_id": 42,
                "target_type": "SCENARIO",
                "engine": "PYTHON_HTTP",
                "request": None,
                "concurrency": 8,
                "iterations": 100,
                "load_mode": "STEP_LOAD",
                "target_rps": None,
                "duration_seconds": None,
                "step_stages": [
                    {"concurrency": 2, "duration_seconds": 10},
                    {"concurrency": 8, "duration_seconds": 20},
                ],
                "warmup_iterations": 2,
                "request_timeout_ms": 1_000,
                "cleanup_mode": "BATCH_CLEANUP",
                "stream_config": {
                    "protocol": "SSE",
                    "completion_marker": "[DONE]",
                    "require_completion_marker": True,
                },
                "total_timeout_ms": 60_000,
            },
        )
    )

    plan = RunnerClient("https://example.test", transport).get_performance_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert plan.schema_version == 3
    assert plan.target_type == "SCENARIO"
    assert plan.request is None
    assert plan.cleanup_mode == "BATCH_CLEANUP"
    assert plan.step_stages[-1]["concurrency"] == 8


def _registration_response() -> HttpResponse:
    return HttpResponse(
        201,
        {
            "runner_id": "runner-001",
            "credential": "credential-value-123456789",
            "heartbeat_interval_seconds": 30,
        },
    )


def _heartbeat_response(status: int = 200) -> HttpResponse:
    return HttpResponse(
        status,
        {
            "runner_id": "runner-001",
            "status": "ACTIVE",
            "heartbeat_interval_seconds": 30,
            "server_time": datetime.now(UTC).isoformat(),
        },
    )


def _claim_response(*, idempotent: bool = False, **overrides: object) -> HttpResponse:
    body: dict[str, object] = {
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "status": "ASSIGNED",
        "claimed_at": "2026-08-25T10:00:00Z",
        "idempotent": idempotent,
    }
    body.update(overrides)
    return HttpResponse(
        200,
        body,
    )


def _request_template(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "method": "POST",
        "url": "https://target.example.test/api",
        "query_params": [{"name": "q", "value": "1", "enabled": True}],
        "headers": [{"name": "X-Test", "value": "ok", "enabled": True}],
        "cookies": [],
        "body": {"type": "JSON", "content": {"ok": True}, "content_type": None},
        "auth": {
            "type": "NONE",
            "token": None,
            "username": None,
            "password": None,
            "key_name": None,
            "key_value": None,
            "placement": "HEADER",
        },
        "timeout_ms": 1000,
        "follow_redirects": False,
    }
    request.update(overrides)
    return request


def _plan_response(**overrides: object) -> HttpResponse:
    body: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "case_version_id": 7,
        "request": _request_template(),
        "total_timeout_ms": 900_000,
    }
    body.update(overrides)
    return HttpResponse(200, body)


def _start_response(**overrides: object) -> HttpResponse:
    body: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "status": "RUNNING",
        "started_at": "2026-08-25T10:01:00Z",
        "idempotent": False,
    }
    body.update(overrides)
    return HttpResponse(200, body)


def _complete_response(**overrides: object) -> HttpResponse:
    body: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "case_run_id": 42,
        "outcome": "HTTP_RESPONSE",
        "run_status": "SUCCESS",
        "case_run_status": "SUCCESS",
        "assertion_results": [],
        "error_type": None,
        "error_message": None,
        "completed_at": "2026-08-25T10:02:00Z",
        "idempotent": False,
        "retry_count": 0,
    }
    body.update(overrides)
    return HttpResponse(200, body)


def test_register_heartbeat_and_invalid_credential_stop_without_retry() -> None:
    token = "registration-token-123456"
    credential = "credential-value-123456789"
    transport = FakeTransport(
        _registration_response(),
        _heartbeat_response(),
        _heartbeat_response(401),
    )
    sleeps: list[float] = []
    client = RunnerClient(
        " http://localhost:8000/ ",
        transport,
        sleeper=sleeps.append,
        max_attempts=4,
    )

    registration = client.register(snapshot(), token)
    assert registration.runner_id == "runner-001"
    assert registration.credential == credential
    first_heartbeat = client.heartbeat(
        RunnerIdentity(registration.runner_id, registration.credential), snapshot()
    )
    assert first_heartbeat.status == "ACTIVE"

    with pytest.raises(AuthenticationError, match="已撤销"):
        client.heartbeat(
            RunnerIdentity(registration.runner_id, registration.credential), snapshot()
        )

    assert transport.calls[0]["url"] == "http://localhost:8000/api/v1/runners/register"
    assert transport.calls[1]["url"].endswith("/api/v1/runners/runner-001/heartbeat")
    assert transport.calls[1]["headers"] == {"Authorization": f"Bearer {credential}"}
    for call in transport.calls[:2]:
        capabilities = {item["name"]: item for item in call["json"]["capabilities"]}
        assert all(
            capabilities[name] == {"name": name, "status": "READY", "reason": None}
            for name in ("API", "SQL", "SCRIPT", "SSE")
        )
        for name in ("WEB", "JMETER"):
            assert capabilities[name]["status"] in {"READY", "UNAVAILABLE"}
            if capabilities[name]["status"] == "READY":
                assert capabilities[name]["reason"] is None
            else:
                assert capabilities[name]["reason"]
    assert sleeps == []
    assert token not in str(transport.calls[0]["headers"])


def test_network_and_5xx_use_bounded_exponential_backoff() -> None:
    transport = FakeTransport(
        TransportError("temporary", transient=True),
        HttpResponse(503, {"credential": "must-not-leak"}),
        _heartbeat_response(),
    )
    sleeps: list[float] = []
    client = RunnerClient(
        "https://example.test",
        transport,
        max_attempts=3,
        backoff_base_seconds=0.25,
        backoff_max_seconds=0.3,
        sleeper=sleeps.append,
    )

    result = client.heartbeat(
        RunnerIdentity("runner-001", "credential-value-123456789"), snapshot()
    )

    assert result.runner_id == "runner-001"
    assert sleeps == [0.25, 0.3]


def test_claim_posts_message_id_with_bearer_and_accepts_idempotent_result() -> None:
    credential = "credential-value-123456789"
    transport = FakeTransport(_claim_response(idempotent=True))
    client = RunnerClient("https://example.test", transport)

    result = client.claim(
        RunnerIdentity("runner-001", credential),
        "run-001",
        "message-001",
    )

    assert result.status == "ASSIGNED"
    assert result.runner_id == "runner-001"
    assert result.idempotent is True
    assert transport.calls[0]["url"] == "https://example.test/api/v1/runs/run-001/claim"
    assert transport.calls[0]["json"] == {"message_id": "message-001"}
    assert transport.calls[0]["headers"] == {"Authorization": f"Bearer {credential}"}


@pytest.mark.parametrize("status_code", [401, 403])
def test_claim_auth_failure_is_immediate_and_not_retried(status_code: int) -> None:
    transport = FakeTransport(HttpResponse(status_code, {"credential": "must-not-leak"}))
    sleeps: list[float] = []
    client = RunnerClient("https://example.test", transport, sleeper=sleeps.append)

    with pytest.raises(AuthenticationError):
        client.claim(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
        )

    assert len(transport.calls) == 1
    assert sleeps == []


def test_claim_retries_temporary_5xx_and_network_failures() -> None:
    transport = FakeTransport(
        HttpResponse(503, {"message": "temporary"}),
        TransportError("temporary network", transient=True),
        _claim_response(),
    )
    sleeps: list[float] = []
    client = RunnerClient(
        "https://example.test",
        transport,
        max_attempts=3,
        backoff_base_seconds=0.1,
        sleeper=sleeps.append,
    )

    result = client.claim(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert result.status == "ASSIGNED"
    assert len(transport.calls) == 3
    assert sleeps == [0.1, 0.2]


def test_claim_response_is_strictly_validated() -> None:
    valid = _claim_response().body
    invalid_responses = (
        {**valid, "extra": True},
        {**valid, "runner_id": "other-runner"},
        {**valid, "status": "CLAIMED"},
        {**valid, "claimed_at": "2026-08-25T10:00:00"},
        {**valid, "idempotent": 1},
    )
    for body in invalid_responses:
        client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, body)))
        with pytest.raises(ProtocolError):
            client.claim(
                RunnerIdentity("runner-001", "credential-value-123456789"),
                "run-001",
                "message-001",
            )


def test_cancelled_claim_response_has_null_time_and_is_idempotent() -> None:
    transport = FakeTransport(
        _claim_response(status="CANCELLED", claimed_at=None, idempotent=True)
    )
    client = RunnerClient("https://example.test", transport)

    result = client.claim(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert result.status == "CANCELLED"
    assert result.claimed_at is None
    assert result.idempotent is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "ASSIGNED", "claimed_at": None},
        {"status": "CANCELLED", "claimed_at": "2026-08-25T10:00:00Z"},
        {"status": "CANCELLED", "claimed_at": None, "idempotent": False},
        {"status": "UNKNOWN", "claimed_at": None},
    ],
)
def test_claim_response_rejects_invalid_cancelled_or_assigned_combinations(
    overrides: dict[str, object],
) -> None:
    transport = FakeTransport(HttpResponse(200, _claim_response(**overrides).body))
    client = RunnerClient("https://example.test", transport)

    with pytest.raises(ProtocolError):
        client.claim(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
        )


def test_register_network_failure_is_unknown_and_never_retried() -> None:
    transport = FakeTransport(TransportError("timeout", transient=True))
    sleeps: list[float] = []
    client = RunnerClient(
        "https://example.test",
        transport,
        max_attempts=4,
        sleeper=sleeps.append,
    )

    with pytest.raises(RegistrationOutcomeUnknownError, match="结果未知"):
        client.register(snapshot(), "registration-token-123456")

    assert len(transport.calls) == 1
    assert sleeps == []


def test_register_5xx_is_unknown_and_never_retried() -> None:
    transport = FakeTransport(HttpResponse(503, {"message": "temporary"}))
    client = RunnerClient("https://example.test", transport, max_attempts=4)

    with pytest.raises(RegistrationOutcomeUnknownError, match="结果未知"):
        client.register(snapshot(), "registration-token-123456")

    assert len(transport.calls) == 1


def test_invalid_backend_response_is_rejected() -> None:
    transport = FakeTransport(HttpResponse(201, {"runner_id": "runner-001"}))
    client = RunnerClient("https://example.test", transport)

    with pytest.raises(ProtocolError, match="credential"):
        client.register(snapshot(), "registration-token-123456")


def test_protocol_error_redacts_registration_token_and_credential() -> None:
    token = "registration-token-123456"
    credential = "credential-value-123456789"
    transport = FakeTransport(
        HttpResponse(
            422,
            {"registration_token": token, "credential": credential},
        )
    )
    client = RunnerClient("https://example.test", transport)

    with pytest.raises(ProtocolError) as error:
        client.register(snapshot(), token)
    assert token not in str(error.value)
    assert credential not in str(error.value)
    assert "REDACTED" in str(error.value)


def test_url_rejects_embedded_credentials_and_query() -> None:
    with pytest.raises(ConfigurationError):
        RunnerClient("https://user:password@example.test", FakeTransport())
    with pytest.raises(ConfigurationError):
        RunnerClient("https://example.test/?token=secret", FakeTransport())


def test_execution_plan_is_strict_and_uses_bearer_get() -> None:
    credential = "credential-value-123456789"
    transport = FakeTransport(_plan_response())
    client = RunnerClient("https://example.test", transport)

    result = client.get_execution_plan(
        RunnerIdentity("runner-001", credential), "run-001", "message-001"
    )

    assert result.schema_version == 1
    assert result.case_run_id == 42
    assert result.case_version_id == 7
    assert result.request.method == "POST"
    assert transport.calls[0]["url"].endswith(
        "/api/v1/runs/run-001/execution-plan?message_id=message-001"
    )
    assert transport.calls[0]["json"] is None
    assert transport.calls[0]["headers"] == {"Authorization": f"Bearer {credential}"}


def test_execution_plan_accepts_bounded_pre_action_pipeline_fields() -> None:
    body = dict(_plan_response().body)
    body["initial_context"] = {"base_url": "https://pipeline.example.test"}
    body["pre_actions"] = [
        {"type": "SET_VARIABLE", "name": "tenant", "value": "alpha", "enabled": True},
        {"type": "FAKER", "name": "request_id", "generator": "uuid", "seed": 7, "enabled": True},
        {
            "type": "GET_TOKEN",
            "name": "access_token",
            "source": "JSONPATH",
            "expression": "$.access_token",
            "required": True,
            "default_value": None,
            "request": {
                **_request_template(),
                "url": "https://auth.example.test/login",
                "retry_policy": {"max_retries": 0, "backoff_ms": 0, "retry_on": []},
            },
            "enabled": True,
        },
        {
            "type": "API_SETUP",
            "name": "resource_id",
            "source": "JSONPATH",
            "expression": "$.data.id",
            "required": True,
            "default_value": None,
            "request": {
                **_request_template(),
                "url": "https://api.example.test/resources",
                "retry_policy": {"max_retries": 0, "backoff_ms": 0, "retry_on": []},
            },
            "enabled": True,
        },
        {
            "type": "PYTHON_SCRIPT",
            "script": 'context["tenant"] = context["tenant"] + "-script"',
            "enabled": True,
        },
        {
            "type": "SQL_QUERY",
            "connection_id": 3,
            "sql": "SELECT code FROM tenants LIMIT 1",
            "params": {},
            "result_variable": "tenant_rows",
            "max_rows": 1,
            "enabled": True,
        },
    ]
    body["extractors"] = [
        {
            "name": "order_id",
            "source": "JSONPATH",
            "expression": "$.id",
            "required": True,
            "default_value": None,
            "enabled": True,
        }
    ]
    body["post_actions"] = [
        {
            "type": "SET_VARIABLE",
            "name": "summary",
            "value": "{{order_id}}",
            "enabled": True,
        }
        ,
        {
            "type": "PYTHON_SCRIPT",
            "script": 'context["done"] = True',
            "enabled": True,
        },
        {
            "type": "REGISTER_RESOURCE",
            "name": "created_order",
            "resource_type": "order",
            "value": "{{order_id}}",
            "metadata": {},
            "cleanup": None,
            "cleanup_ref": "delete-order",
            "enabled": True,
        },
    ]
    body["sql_connections"] = [
        {
            "connection_id": 3,
            "db_type": "MYSQL",
            "host": "db.example.test",
            "port": 3306,
            "database_name": "test_db",
            "username": "runner",
            "password_secret_id": 9,
            "ssl_enabled": False,
        }
    ]
    body["secrets"] = [{"secret_id": 9, "value": "db-password-never-output"}]
    body["cleanups"] = [
        {
            "cleanup_id": "delete-order",
            "cleanup_type": "API",
            "policy": "ALWAYS",
            "enabled": True,
            "timeout_ms": 30000,
            "method": "DELETE",
            "url": "{{base_url}}/orders/{{order_id}}",
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
    ]
    client = RunnerClient(
        "https://example.test",
        FakeTransport(HttpResponse(200, body)),
    )

    result = client.get_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert result.has_pipeline is True
    assert result.initial_context == {"base_url": "https://pipeline.example.test"}
    assert [item.get("type") for item in result.pre_actions] == [
        "SET_VARIABLE",
        "FAKER",
        "GET_TOKEN",
        "API_SETUP",
        "PYTHON_SCRIPT",
        "SQL_QUERY",
    ]
    assert result.extractors[0]["expression"] == "$.id"
    assert result.post_actions[0]["name"] == "summary"
    assert result.sql_connections[0].connection_id == 3
    assert result.secrets[0].secret_id == 9
    assert result.cleanups[0]["cleanup_id"] == "delete-order"


@pytest.mark.parametrize(
    "body",
    [
        {**_plan_response().body, "extra": True},
        {**_plan_response().body, "schema_version": 2},
        {**_plan_response().body, "case_run_id": True},
        {**_plan_response().body, "runner_id": "other-runner"},
        {
            **_plan_response().body,
            "request": {**_request_template(), "unknown": True},
        },
    ],
)
def test_execution_plan_rejects_unknown_or_mismatched_fields(body: dict[str, object]) -> None:
    client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, body)))

    with pytest.raises(ProtocolError):
        client.get_execution_plan(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
        )


def test_execution_plan_sends_optional_case_run_id_query() -> None:
    transport = FakeTransport(_plan_response())
    client = RunnerClient("https://example.test", transport)

    client.get_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        case_run_id=42,
    )

    assert transport.calls[0]["url"].endswith(
        "/api/v1/runs/run-001/execution-plan?message_id=message-001&case_run_id=42"
    )


def test_execution_plan_without_retry_policy_disables_retries() -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(_plan_response()),
    )

    result = client.get_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert result.request.retry_policy.max_retries == 0
    assert result.request.retry_policy.backoff_ms == 0
    assert result.request.retry_policy.retry_on == ()


def test_execution_plan_parses_explicit_retry_policy() -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(
            _plan_response(
                request=_request_template(
                    retry_policy={
                        "max_retries": 1,
                        "backoff_ms": 250,
                        "retry_on": ["TARGET_NETWORK_ERROR", "HTTP_5XX"],
                    }
                )
            )
        ),
    )

    result = client.get_execution_plan(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
    )

    assert result.request.retry_policy.max_retries == 1
    assert result.request.retry_policy.backoff_ms == 250
    assert result.request.retry_policy.retry_on == ("TARGET_NETWORK_ERROR", "HTTP_5XX")


@pytest.mark.parametrize(
    "retry_policy",
    [
        {"max_retries": 1, "backoff_ms": 0, "retry_on": [],},
        {"max_retries": True, "backoff_ms": 0, "retry_on": ["HTTP_5XX"]},
        {"max_retries": -1, "backoff_ms": 0, "retry_on": ["HTTP_5XX"]},
        {"max_retries": 2, "backoff_ms": 0, "retry_on": ["HTTP_5XX"]},
        {"max_retries": 3, "backoff_ms": 0, "retry_on": ["HTTP_5XX"]},
        {"max_retries": 4, "backoff_ms": 0, "retry_on": ["HTTP_5XX"]},
        {"max_retries": 1, "backoff_ms": 30_001, "retry_on": ["HTTP_5XX"]},
        {"max_retries": 1, "backoff_ms": 0, "retry_on": ["HTTP_5XX", "HTTP_5XX"]},
        {"max_retries": 1, "backoff_ms": 0, "retry_on": ["UNSUPPORTED"]},
        {
            "max_retries": 1,
            "backoff_ms": 0,
            "retry_on": ["HTTP_5XX"],
            "extra": True,
        },
        None,
    ],
)
def test_execution_plan_rejects_invalid_retry_policy(retry_policy: object) -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(
            _plan_response(request=_request_template(retry_policy=retry_policy))
        ),
    )

    with pytest.raises(ProtocolError):
        client.get_execution_plan(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
        )


def test_execution_start_and_complete_use_exact_payloads() -> None:
    transport = FakeTransport(_start_response(), _complete_response(idempotent=True, retry_count=2))
    client = RunnerClient("https://example.test", transport)
    identity = RunnerIdentity("runner-001", "credential-value-123456789")

    started = client.execution_start(identity, "run-001", "message-001", 42)
    completed = client.execution_complete(
        identity,
        "run-001",
        "message-001",
        42,
        outcome="HTTP_RESPONSE",
        response={
            "status_code": 200,
            "json_body": {"ok": True},
            "text": None,
            "headers": {"X-Result": "ok"},
            "cookies": {"sid": "opaque"},
            "elapsed_ms": 5,
            "response_time_ms": 5,
        },
        retry_count=2,
        action_traces=[
            {
                "sequence": 1,
                "phase": "PRE",
                "type": "GET_TOKEN",
                "name": "access_token",
                "status": "SUCCESS",
                "duration_ms": 7,
                "error_type": None,
            }
        ],
    )

    assert started.status == "RUNNING"
    assert started.started_at.tzinfo is not None
    assert completed.idempotent is True
    assert completed.retry_count == 2
    assert transport.calls[0]["json"] == {"message_id": "message-001", "case_run_id": 42}
    assert transport.calls[1]["json"] == {
        "message_id": "message-001",
        "case_run_id": 42,
        "outcome": "HTTP_RESPONSE",
        "retry_count": 2,
        "action_traces": [
            {
                "sequence": 1,
                "phase": "PRE",
                "type": "GET_TOKEN",
                "name": "access_token",
                "status": "SUCCESS",
                "duration_ms": 7,
                "error_type": None,
            }
        ],
        "response": {
            "status_code": 200,
            "json_body": {"ok": True},
            "text": None,
            "headers": {"X-Result": "ok"},
            "cookies": {"sid": "opaque"},
            "elapsed_ms": 5,
            "response_time_ms": 5,
        },
    }


@pytest.mark.parametrize(
    "response",
    [
        {**_start_response().body, "idempotent": 1},
        {**_start_response().body, "status": "ASSIGNED"},
        {**_start_response().body, "started_at": "2026-08-25T10:01:00"},
        {**_start_response().body, "extra": True},
        {**_complete_response().body, "assertion_results": [{"bad": True}]},
        {**_complete_response().body, "run_status": "ASSIGNED"},
        {**_complete_response().body, "completed_at": "2026-08-25T10:02:00"},
        {**_complete_response().body, "idempotent": "false"},
    ],
)
def test_execution_responses_are_strictly_validated(response: dict[str, object]) -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    if "run_status" in response and "assertion_results" in response:
        client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, response)))
        with pytest.raises(ProtocolError):
            client.execution_complete(
                identity,
                "run-001",
                "message-001",
                42,
                outcome="HTTP_RESPONSE",
                response={
                    "status_code": 200,
                    "json_body": None,
                    "text": None,
                    "headers": {},
                    "cookies": {},
                    "elapsed_ms": 0,
                    "response_time_ms": 0,
                },
            )
    else:
        client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, response)))
        with pytest.raises(ProtocolError):
            client.execution_start(identity, "run-001", "message-001", 42)


def test_execution_complete_accepts_intermediate_running_iteration() -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    client = RunnerClient(
        "https://example.test",
        FakeTransport(_complete_response(run_status="RUNNING")),
    )

    result = client.execution_complete(
        identity,
        "run-001",
        "message-001",
        42,
        outcome="HTTP_RESPONSE",
        response={
            "status_code": 200,
            "json_body": None,
            "text": None,
            "headers": {},
            "cookies": {},
            "elapsed_ms": 0,
            "response_time_ms": 0,
        },
    )

    assert result.run_status == "RUNNING"
    assert result.case_run_status == "SUCCESS"


@pytest.mark.parametrize("retry_count", [True, -1, 4, "1", None])
def test_execution_complete_rejects_invalid_retry_count_response(retry_count: object) -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(_complete_response(retry_count=retry_count)),
    )

    with pytest.raises(ProtocolError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="HTTP_RESPONSE",
            response={
                "status_code": 200,
                "json_body": None,
                "text": None,
                "headers": {},
                "cookies": {},
                "elapsed_ms": 0,
                "response_time_ms": 0,
            },
        )


def test_execution_complete_rejects_invalid_retry_count_request() -> None:
    client = RunnerClient("https://example.test", FakeTransport())

    with pytest.raises(ConfigurationError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="HTTP_RESPONSE",
            response={
                "status_code": 200,
                "json_body": None,
                "text": None,
                "headers": {},
                "cookies": {},
                "elapsed_ms": 0,
                "response_time_ms": 0,
            },
            retry_count=4,
        )


def test_execution_start_accepts_cancelling_with_null_started_at() -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(_start_response(status="CANCELLING", started_at=None)),
    )

    result = client.execution_start(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        42,
    )

    assert result.status == "CANCELLING"
    assert result.started_at is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "CANCELLING", "started_at": "2026-08-25T10:01:00Z"},
        {"status": "RUNNING", "started_at": None},
        {"status": "ASSIGNED", "started_at": None},
    ],
)
def test_execution_start_rejects_invalid_cancellation_shape(
    overrides: dict[str, object],
) -> None:
    client = RunnerClient(
        "https://example.test", FakeTransport(HttpResponse(200, _start_response(**overrides).body))
    )

    with pytest.raises(ProtocolError):
        client.execution_start(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
        )


def test_execution_cancellation_query_strictly_parses_and_uses_query_params() -> None:
    response = HttpResponse(
        200,
        {
            "schema_version": 1,
            "run_id": "run-001",
            "message_id": "message-001",
            "case_run_id": 42,
            "status": "CANCELLING",
            "cancellation_requested": True,
            "force_stop_requested": True,
        },
    )
    transport = FakeTransport(response)
    client = RunnerClient("https://example.test", transport)

    result = client.get_execution_cancellation(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        42,
    )

    assert result.cancellation_requested is True
    assert result.force_stop_requested is True
    assert transport.calls[0]["url"].endswith(
        "/api/v1/runs/run-001/execution-cancellation?message_id=message-001&case_run_id=42"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "RUNNING", "cancellation_requested": True},
        {"status": "CANCELLING", "cancellation_requested": False},
        {"status": "CANCELLED", "cancellation_requested": False},
        {"status": "UNKNOWN", "cancellation_requested": False},
        {"status": "RUNNING", "force_stop_requested": True},
        {"runner_id": "other-runner"},
        {"extra": True},
    ],
)
def test_execution_cancellation_query_rejects_invalid_response(
    overrides: dict[str, object],
) -> None:
    body = {
        "schema_version": 1,
        "run_id": "run-001",
        "message_id": "message-001",
        "case_run_id": 42,
        "status": "RUNNING",
        "cancellation_requested": False,
        "force_stop_requested": False,
        **overrides,
    }
    client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, body)))

    with pytest.raises(ProtocolError):
        client.get_execution_cancellation(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
        )


def test_execution_cancellation_query_redacts_credential_from_error() -> None:
    credential = "credential-value-123456789"
    client = RunnerClient(
        "https://example.test",
        FakeTransport(HttpResponse(422, {"credential": credential})),
    )

    with pytest.raises(ProtocolError) as error:
        client.get_execution_cancellation(
            RunnerIdentity("runner-001", credential),
            "run-001",
            "message-001",
            42,
        )

    assert credential not in str(error.value)
    assert "REDACTED" in str(error.value)


def test_execution_error_completion_omits_response_and_keeps_secret_out_of_errors() -> None:
    secret = "credential-value-123456789"
    transport = FakeTransport(_complete_response(outcome="EXECUTION_ERROR"))
    client = RunnerClient("https://example.test", transport)

    result = client.execution_complete(
        RunnerIdentity("runner-001", secret),
        "run-001",
        "message-001",
        42,
        outcome="EXECUTION_ERROR",
        error_type="TARGET_NETWORK_ERROR",
        error_message="目标 API 网络请求失败",
    )

    assert result.outcome == "EXECUTION_ERROR"
    assert transport.calls[0]["json"] == {
        "message_id": "message-001",
        "case_run_id": 42,
        "outcome": "EXECUTION_ERROR",
        "retry_count": 0,
        "error_type": "TARGET_NETWORK_ERROR",
        "error_message": "目标 API 网络请求失败",
    }
    assert secret not in str(transport.calls[0]["json"])


def test_timeout_completion_has_only_safe_error_fields_and_accepts_timeout_terminal_state() -> None:
    transport = FakeTransport(
        _complete_response(
            outcome="TIMEOUT",
            run_status="TIMEOUT",
            case_run_status="TIMEOUT",
            error_type="TARGET_TIMEOUT",
            error_message="目标 API 请求超时",
        )
    )
    client = RunnerClient("https://example.test", transport)

    result = client.execution_complete(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        42,
        outcome="TIMEOUT",
        error_type="TARGET_TIMEOUT",
        error_message="目标 API 请求超时",
    )

    assert result.outcome == "TIMEOUT"
    assert result.run_status == "TIMEOUT"
    assert result.case_run_status == "TIMEOUT"
    assert transport.calls[0]["json"] == {
        "message_id": "message-001",
        "case_run_id": 42,
        "outcome": "TIMEOUT",
        "retry_count": 0,
        "error_type": "TARGET_TIMEOUT",
        "error_message": "目标 API 请求超时",
    }
    assert "response" not in transport.calls[0]["json"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_status": "FAILED"},
        {"case_run_status": "FAILED"},
        {"error_type": None},
        {"error_message": None},
    ],
)
def test_timeout_completion_requires_timeout_terminal_state_and_errors(
    overrides: dict[str, object],
) -> None:
    response_body = {
        **_complete_response(
            outcome="TIMEOUT",
            run_status="TIMEOUT",
            case_run_status="TIMEOUT",
            error_type="TARGET_TIMEOUT",
            error_message="目标 API 请求超时",
        ).body,
        **overrides,
    }
    transport = FakeTransport(HttpResponse(200, response_body))
    client = RunnerClient("https://example.test", transport)

    with pytest.raises(ProtocolError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="TIMEOUT",
            error_type="TARGET_TIMEOUT",
            error_message="目标 API 请求超时",
        )


def test_timeout_completion_rejects_response_payload() -> None:
    client = RunnerClient("https://example.test", FakeTransport())

    with pytest.raises(ConfigurationError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="TIMEOUT",
            response={
                "status_code": 504,
                "json_body": None,
                "text": None,
                "headers": {},
                "cookies": {},
                "elapsed_ms": 0,
                "response_time_ms": 0,
            },
            error_type="TARGET_TIMEOUT",
            error_message="目标 API 请求超时",
        )


def test_web_trace_healing_context_is_strict_and_backward_compatible() -> None:
    context = {
        "schema_version": 1,
        "trigger": "ALL_LOCATORS_FAILED",
        "element_version_id": 17,
        "page_url": "https://example.test/form",
        "page_title": "Safe title",
        "dom_candidates": [
            {"tag": "button", "role": "button", "id": "submit", "type": "button"}
        ],
    }
    trace = {
        "node_id": "action_1",
        "status": "FAILED",
        "duration_ms": 10,
        "error_type": "WEB_LOCATOR_NOT_FOUND",
        "error_message": "Web Locator 未找到",
        "locator_attempts": [
            {"strategy": "css", "priority": 1, "status": "NOT_FOUND"}
        ],
        "healing_context": context,
    }
    parsed = _web_trace_payload([trace])
    assert parsed[0]["healing_context"] == context
    old_trace = dict(trace)
    old_trace.pop("healing_context")
    assert "healing_context" not in _web_trace_payload([old_trace])[0]

    with pytest.raises(ProtocolError):
        _web_trace_payload([{**trace, "healing_context": {**context, "unknown": True}}])
    with pytest.raises(ProtocolError):
        _web_trace_payload([{**trace, "healing_context": None}])
    with pytest.raises(ProtocolError):
        _web_trace_payload(
            [{
                **trace,
                "healing_context": {**context, "page_title": " Safe title"},
            }]
        )
    with pytest.raises(ProtocolError):
        _web_trace_payload([{**trace, "status": "SUCCESS"}])
    with pytest.raises(ProtocolError):
        _web_trace_payload([{**trace, "error_type": "WEB_ACTION_FAILED"}])
    with pytest.raises(ProtocolError):
        _web_trace_payload(
            [{
                **trace,
                "locator_attempts": [
                    {"strategy": "css", "priority": 1, "status": "NOT_FOUND"},
                    {"strategy": "text", "priority": 2, "status": "ACTION_FAILED"},
                ],
            }]
        )
    with pytest.raises(ProtocolError):
        _web_trace_payload(
            [{
                **trace,
                "healing_context": {
                    **context,
                    "page_url": "https://example.test/form?token=hidden",
                },
            }]
        )
    with pytest.raises(ProtocolError):
        _web_trace_payload(
            [{
                **trace,
                "healing_context": {
                    **context,
                    "dom_candidates": [
                        {
                            "tag": "button",
                            "role": "x" * 200,
                            "id": "x" * 200,
                            "name": "x" * 200,
                            "aria-label": "x" * 200,
                            "placeholder": "x" * 200,
                            "data-testid": "x" * 200,
                            "type": "x" * 200,
                            "title": "x" * 200,
                        }
                        for _ in range(40)
                    ],
                },
            }]
        )


@pytest.mark.parametrize(
    ("outcome", "request_kwargs"),
    [
        (
            "HTTP_RESPONSE",
            {
                "response": {
                    "status_code": 200,
                    "json_body": {"ok": True},
                    "text": None,
                    "headers": {},
                    "cookies": {},
                    "elapsed_ms": 1,
                    "response_time_ms": 1,
                }
            },
        ),
        (
            "EXECUTION_ERROR",
            {"error_type": "TARGET_NETWORK_ERROR", "error_message": "目标 API 网络请求失败"},
        ),
        (
            "TIMEOUT",
            {"error_type": "TARGET_TIMEOUT", "error_message": "目标 API 请求超时"},
        ),
    ],
)
def test_execution_complete_accepts_server_cancel_override(
    outcome: str, request_kwargs: dict[str, object]
) -> None:
    transport = FakeTransport(
        _complete_response(
            outcome="CANCELLED",
            run_status="CANCELLED",
            case_run_status="CANCELLED",
            assertion_results=[],
            error_type="CANCEL_REQUESTED",
            error_message="Runner 检测到取消请求",
        )
    )
    client = RunnerClient("https://example.test", transport)

    result = client.execution_complete(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        42,
        outcome=outcome,
        **request_kwargs,
    )

    assert result.outcome == "CANCELLED"
    assert result.run_status == "CANCELLED"
    assert result.case_run_status == "CANCELLED"
    assert result.assertion_results == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"error_type": "TARGET_TIMEOUT"},
        {"assertion_results": [{"unexpected": True}]},
    ],
)
def test_execution_complete_cancel_override_requires_safe_terminal_fields(
    overrides: dict[str, object],
) -> None:
    response_body = {
        **_complete_response(
            outcome="CANCELLED",
            run_status="CANCELLED",
            case_run_status="CANCELLED",
            assertion_results=[],
            error_type="CANCEL_REQUESTED",
            error_message="Runner 检测到取消请求",
        ).body,
        **overrides,
    }
    client = RunnerClient("https://example.test", FakeTransport(HttpResponse(200, response_body)))

    with pytest.raises(ProtocolError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="HTTP_RESPONSE",
            response={
                "status_code": 200,
                "json_body": {"ok": True},
                "text": None,
                "headers": {},
                "cookies": {},
                "elapsed_ms": 1,
                "response_time_ms": 1,
            },
        )


def test_execution_complete_rejects_non_cancelled_outcome_mismatch() -> None:
    client = RunnerClient(
        "https://example.test",
        FakeTransport(
            _complete_response(
                outcome="EXECUTION_ERROR",
                run_status="FAILED",
                case_run_status="FAILED",
                error_type="TARGET_NETWORK_ERROR",
                error_message="目标 API 网络请求失败",
            )
        ),
    )

    with pytest.raises(ProtocolError):
        client.execution_complete(
            RunnerIdentity("runner-001", "credential-value-123456789"),
            "run-001",
            "message-001",
            42,
            outcome="HTTP_RESPONSE",
            response={
                "status_code": 200,
                "json_body": {"ok": True},
                "text": None,
                "headers": {},
                "cookies": {},
                "elapsed_ms": 1,
                "response_time_ms": 1,
            },
        )


def test_execution_complete_validates_full_assertion_result_shape() -> None:
    assertion = {
        "sequence": 1,
        "name": "status",
        "type": "STATUS_CODE",
        "status": "PASS",
        "expected": 200,
        "actual": 200,
        "message": "status matched",
        "duration_ms": 1,
        "confidence": None,
        "reason": None,
        "ai_call_id": None,
        "actual_model": None,
        "fallback_used": None,
        "repair_used": None,
    }
    transport = FakeTransport(_complete_response(assertion_results=[assertion]))
    result = RunnerClient("https://example.test", transport).execution_complete(
        RunnerIdentity("runner-001", "credential-value-123456789"),
        "run-001",
        "message-001",
        42,
        outcome="HTTP_RESPONSE",
        response={
            "status_code": 200,
            "json_body": {"ok": True},
            "text": None,
            "headers": {},
            "cookies": {},
            "elapsed_ms": 1,
            "response_time_ms": 1,
        },
    )

    assert result.assertion_results == [assertion]
