from __future__ import annotations

from typing import Any

import httpx
import pytest

from runner.api_pipeline import ApiCasePipelineExecutor, ApiPipelineCleanupTask
from runner.errors import ExecutionError
from runner.executors.api import ApiExecutor, ApiRequestTemplate
from runner.models import (
    ExecutionPlanResult,
    ScenarioExecutionSecret,
    ScenarioSqlConnectionPlan,
)


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        return httpx.Response(
            200,
            json={"ok": True, "data": {"id": 42}},
            headers={"X-Cursor": "next-page", "Set-Cookie": "sid=cookie-value"},
            request=httpx.Request(method, url),
        )


class SequenceClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        response.request = httpx.Request(method, url)
        return response


class SqlCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    def execute(self, statement: str, parameters: object) -> int:
        self.calls.append((statement, parameters))
        return 1

    def fetchmany(self, size: int) -> list[object]:
        assert size == 2
        return [{"code": "from-sql"}]

    def close(self) -> None:
        self.closed = True


class SqlConnection:
    def __init__(self, cursor: SqlCursor) -> None:
        self.cursor_instance = cursor
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> SqlCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class SqlFactory:
    def __init__(self, connection: SqlConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[object, str, int]] = []

    def __call__(self, plan: object, password: str, timeout_ms: int) -> SqlConnection:
        self.calls.append((plan, password, timeout_ms))
        return self.connection


def test_assertion_deferred_cleanup_uses_backend_outcome_before_policy_match() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-deferred-cleanup",
        message_id="message-deferred-cleanup",
        runner_id="runner-deferred-cleanup",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate("GET", "https://pipeline.example.test/orders"),
        total_timeout_ms=900000,
        initial_context={"base_url": "https://pipeline.example.test"},
        cleanups=(
            {
                "cleanup_id": "failure-cleanup",
                "cleanup_type": "API",
                "policy": "ON_FAILURE",
                "enabled": True,
                "timeout_ms": 30000,
                "method": "DELETE",
                "url": "{{base_url}}/cleanup",
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
            },
        ),
        defer_cleanup_until_assertions=True,
    )
    http = RecordingClient()
    executor = ApiExecutor(http)

    primary = ApiCasePipelineExecutor(plan, api_executor=executor).execute()

    assert primary.cleanup_pending is True
    assert [(method, url) for method, url, _ in http.calls] == [
        ("GET", "https://pipeline.example.test/orders")
    ]
    task = ApiPipelineCleanupTask(
        plan,
        primary.cleanup_context or {},
        primary.resources,
        "FAILURE",
    )
    cleaned = ApiCasePipelineExecutor.execute_cleanup_task(
        task, api_executor=executor
    )

    assert cleaned.resources == ()
    assert [(method, url) for method, url, _ in http.calls] == [
        ("GET", "https://pipeline.example.test/orders"),
        ("DELETE", "https://pipeline.example.test/cleanup"),
    ]


def test_pre_actions_update_context_before_the_real_http_request() -> None:
    request = ApiRequestTemplate.from_mapping(
        {
            "method": "POST",
            "url": "{{base_url}}/orders/{{tenant_code}}",
            "headers": [
                {"name": "X-Request-Id", "value": "{{request_id}}", "enabled": True}
            ],
            "query_params": [
                {"name": "tenant", "value": "{{tenant_code}}", "enabled": True}
            ],
            "cookies": [],
            "body": {"type": "JSON", "content": {"tenant": "{{tenant_code}}"}},
            "auth": {"type": "NONE"},
            "timeout_ms": 30000,
            "follow_redirects": False,
        }
    )
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-pipeline",
        message_id="message-pipeline",
        runner_id="runner-pipeline",
        case_run_id=1,
        case_version_id=2,
        request=request,
        total_timeout_ms=900000,
        initial_context={
            "base_url": "https://pipeline.example.test",
            "tenant_code": "before-pre",
        },
        pre_actions=(
            {"type": "SET_VARIABLE", "name": "tenant_code", "value": "after-pre", "enabled": True},
            {
                "type": "FAKER",
                "name": "request_id",
                "generator": "uuid",
                "seed": 7,
                "enabled": True,
            },
            {
                "type": "PYTHON_SCRIPT",
                "script": 'context["tenant_code"] = context["tenant_code"] + "-script"',
                "enabled": True,
            },
        ),
    )
    client = RecordingClient()

    pipeline = ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(client))
    result = pipeline.execute()

    assert result.status_code == 200
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://pipeline.example.test/orders/after-pre-script")
    assert kwargs["params"] == [("tenant", "after-pre-script")]
    assert kwargs["json"] == {"tenant": "after-pre-script"}
    request_id = kwargs["headers"]["X-Request-Id"]
    assert isinstance(request_id, str) and len(request_id) == 36
    assert plan.initial_context["tenant_code"] == "before-pre"


def test_get_token_executes_login_and_refreshes_main_request_only_once_on_401() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-token",
        message_id="message-token",
        runner_id="runner-token",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate.from_mapping(
            {
                "method": "GET",
                "url": "https://api.example.test/profile",
                "auth": {"type": "BEARER", "token": "{{access_token}}"},
            }
        ),
        total_timeout_ms=900000,
        pre_actions=(
            {
                "type": "GET_TOKEN",
                "name": "access_token",
                "source": "JSONPATH",
                "expression": "$.access_token",
                "required": True,
                "default_value": None,
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.test/login",
                    "query_params": [],
                    "headers": [],
                    "cookies": [],
                    "body": {
                        "type": "JSON",
                        "content": {"username": "runner", "password": "in-memory"},
                        "content_type": None,
                    },
                    "auth": {"type": "NONE"},
                    "timeout_ms": 30000,
                    "follow_redirects": False,
                    "retry_policy": {
                        "max_retries": 0,
                        "backoff_ms": 0,
                        "retry_on": [],
                    },
                },
                "enabled": True,
            },
        ),
    )
    client = SequenceClient(
        [
            httpx.Response(200, json={"access_token": "token-one"}),
            httpx.Response(401, json={"error": "expired"}),
            httpx.Response(200, json={"access_token": "token-two"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    result = ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.status_code == 200
    assert [call[1] for call in client.calls] == [
        "https://auth.example.test/login",
        "https://api.example.test/profile",
        "https://auth.example.test/login",
        "https://api.example.test/profile",
    ]
    assert client.calls[1][2]["headers"]["Authorization"] == "Bearer token-one"
    assert client.calls[3][2]["headers"]["Authorization"] == "Bearer token-two"
    assert client.responses == []
    assert [trace["phase"] for trace in result.action_traces] == [
        "PRE",
        "AUTH_REFRESH",
    ]
    assert all(trace["type"] == "GET_TOKEN" for trace in result.action_traces)
    assert all(trace["status"] == "SUCCESS" for trace in result.action_traces)
    assert "token-one" not in repr(result.action_traces)
    assert "token-two" not in repr(result.action_traces)


def test_get_token_reports_rejected_login_before_token_extraction() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-token-rejected",
        message_id="message-token-rejected",
        runner_id="runner-token-rejected",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate.from_mapping(
            {"method": "GET", "url": "https://api.example.test/profile"}
        ),
        total_timeout_ms=900000,
        pre_actions=(
            {
                "type": "GET_TOKEN",
                "name": "access_token",
                "source": "JSONPATH",
                "expression": "$.data.token",
                "required": True,
                "default_value": None,
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.test/login",
                    "body": {
                        "type": "JSON",
                        "content": {"username": "wrong", "password": "wrong"},
                    },
                    "auth": {"type": "NONE"},
                },
                "enabled": True,
            },
        ),
    )
    client = SequenceClient([httpx.Response(401, json={"error": "INVALID_CREDENTIALS"})])

    with pytest.raises(ExecutionError) as raised:
        ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert raised.value.error_type == "AUTH_REQUEST_REJECTED"
    assert "HTTP 401" in str(raised.value)
    trace = raised.value.action_traces[0]  # type: ignore[attr-defined]
    assert trace | {"duration_ms": 0} == {
        "sequence": 1,
        "phase": "PRE",
        "type": "GET_TOKEN",
        "name": "access_token",
        "status": "FAILED",
        "duration_ms": 0,
        "error_type": "AUTH_REQUEST_REJECTED",
    }


def test_api_setup_creates_data_and_supplies_id_to_target_request() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-api-setup",
        message_id="message-api-setup",
        runner_id="runner-api-setup",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate.from_mapping(
            {
                "method": "DELETE",
                "url": "https://api.example.test/resources/{{resource_id}}",
                "auth": {"type": "BEARER", "token": "{{access_token}}"},
            }
        ),
        total_timeout_ms=900000,
        pre_actions=(
            {
                "type": "GET_TOKEN",
                "name": "access_token",
                "source": "JSONPATH",
                "expression": "$.access_token",
                "required": True,
                "default_value": None,
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/login",
                    "body": {"type": "JSON", "content": {"username": "runner"}},
                    "auth": {"type": "NONE"},
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
                    "method": "POST",
                    "url": "https://api.example.test/resources",
                    "body": {"type": "JSON", "content": {"name": "generated"}},
                    "auth": {"type": "BEARER", "token": "{{access_token}}"},
                },
                "enabled": True,
            },
        ),
    )
    client = SequenceClient(
        [
            httpx.Response(200, json={"access_token": "token-one"}),
            httpx.Response(201, json={"data": {"id": 42}}),
            httpx.Response(200, json={"deleted": True}),
        ]
    )

    result = ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(client)).execute()

    assert result.status_code == 200
    assert [call[1] for call in client.calls] == [
        "https://api.example.test/login",
        "https://api.example.test/resources",
        "https://api.example.test/resources/42",
    ]
    assert client.calls[1][2]["headers"]["Authorization"] == "Bearer token-one"
    assert client.calls[2][2]["headers"]["Authorization"] == "Bearer token-one"
    assert [trace["type"] for trace in result.action_traces] == [
        "GET_TOKEN",
        "API_SETUP",
    ]


def test_pipeline_retry_repeats_only_target_and_keeps_single_token_refresh() -> None:
    request = ApiRequestTemplate.from_mapping(
        {
            "method": "GET",
            "url": "https://api.example.test/profile/{{tenant}}",
            "auth": {"type": "BEARER", "token": "{{access_token}}"},
            "retry_policy": {
                "max_retries": 1,
                "backoff_ms": 100,
                "retry_on": ["HTTP_5XX"],
            },
        }
    )
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-token-retry",
        message_id="message-token-retry",
        runner_id="runner-token-retry",
        case_run_id=1,
        case_version_id=2,
        request=request,
        total_timeout_ms=900000,
        pre_actions=(
            {"type": "SET_VARIABLE", "name": "tenant", "value": "tenant-a"},
            {
                "type": "GET_TOKEN",
                "name": "access_token",
                "source": "JSONPATH",
                "expression": "$.access_token",
                "required": True,
                "default_value": None,
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.test/login",
                    "body": {"type": "JSON", "content": {"user": "runner"}},
                    "auth": {"type": "NONE"},
                },
                "enabled": True,
            },
        ),
        post_actions=(
            {"type": "SET_VARIABLE", "name": "finished", "value": "yes"},
        ),
    )
    client = SequenceClient(
        [
            httpx.Response(200, json={"access_token": "token-one"}),
            httpx.Response(503, json={"error": "unavailable"}),
            httpx.Response(401, json={"error": "expired"}),
            httpx.Response(200, json={"access_token": "token-two"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []

    pipeline = ApiCasePipelineExecutor(
        plan,
        api_executor=ApiExecutor(client),
        sleeper=sleeps.append,
    )
    result = pipeline.execute()

    assert result.status_code == 200
    assert result.retry_count == 1
    assert sleeps == [0.1]
    assert [call[1] for call in client.calls] == [
        "https://auth.example.test/login",
        "https://api.example.test/profile/tenant-a",
        "https://api.example.test/profile/tenant-a",
        "https://auth.example.test/login",
        "https://api.example.test/profile/tenant-a",
    ]
    assert [trace["phase"] for trace in result.action_traces] == [
        "PRE",
        "PRE",
        "AUTH_REFRESH",
        "POST",
    ]
    assert pipeline.context["finished"] == "yes"
    assert client.calls[1][2]["headers"]["Authorization"] == "Bearer token-one"
    assert client.calls[2][2]["headers"]["Authorization"] == "Bearer token-one"
    assert client.calls[4][2]["headers"]["Authorization"] == "Bearer token-two"


def test_extractors_and_post_actions_share_the_response_context() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-pipeline",
        message_id="message-pipeline",
        runner_id="runner-pipeline",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate.from_mapping(
            {"method": "GET", "url": "https://pipeline.example.test/orders"}
        ),
        total_timeout_ms=900000,
        extractors=(
            {
                "name": "order_id",
                "source": "JSONPATH",
                "expression": "$.data.id",
                "required": True,
                "default_value": None,
                "enabled": True,
            },
            {
                "name": "cursor",
                "source": "HEADER",
                "expression": "x-cursor",
                "required": True,
                "default_value": None,
                "enabled": True,
            },
            {
                "name": "optional_value",
                "source": "JSONPATH",
                "expression": "$.missing",
                "required": False,
                "default_value": "fallback",
                "enabled": True,
            },
        ),
        post_actions=(
            {
                "type": "SET_VARIABLE",
                "name": "summary",
                "value": "{{order_id}}:{{cursor}}",
                "enabled": True,
            },
            {
                "type": "EXTRACT_RESPONSE",
                "name": "session_id",
                "source": "COOKIE",
                "expression": "sid",
                "required": True,
                "default_value": None,
                "enabled": True,
            },
            {
                "type": "PYTHON_SCRIPT",
                "script": 'context["next_id"] = context["order_id"] + 1',
                "enabled": True,
            },
        ),
    )
    client = RecordingClient()
    pipeline = ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(client))

    result = pipeline.execute()

    assert result.status_code == 200
    assert pipeline.context["order_id"] == 42
    assert pipeline.context["cursor"] == "next-page"
    assert pipeline.context["optional_value"] == "fallback"
    assert pipeline.context["summary"] == "42:next-page"
    assert pipeline.context["session_id"] == "cookie-value"
    assert pipeline.context["next_id"] == 43


def test_sql_query_pre_action_reuses_bounded_scenario_sql_runtime() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-sql",
        message_id="message-sql",
        runner_id="runner-sql",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate.from_mapping(
            {"method": "GET", "url": "https://pipeline.example.test/{{row_code}}"}
        ),
        total_timeout_ms=900000,
        pre_actions=(
            {
                "type": "SQL_QUERY",
                "connection_id": 3,
                "sql": "SELECT code FROM tenants WHERE id=%s",
                "params": [7],
                "result_variable": "rows",
                "max_rows": 1,
                "enabled": True,
            },
            {
                "type": "PYTHON_SCRIPT",
                "script": 'context["row_code"] = context["rows"][0]["code"]',
                "enabled": True,
            },
        ),
        sql_connections=(
            ScenarioSqlConnectionPlan(
                3, "MYSQL", "db.example.test", 3306, "test_db", "runner", 9, False
            ),
        ),
        secrets=(ScenarioExecutionSecret(9, "db-password-never-output"),),
    )
    http = RecordingClient()
    cursor = SqlCursor()
    connection = SqlConnection(cursor)
    sql = SqlFactory(connection)
    pipeline = ApiCasePipelineExecutor(
        plan,
        api_executor=ApiExecutor(http),
        sql_connector=sql,
    )

    result = pipeline.execute()

    assert result.status_code == 200
    assert http.calls[0][1] == "https://pipeline.example.test/from-sql"
    assert cursor.calls == [("SELECT code FROM tenants WHERE id=%s", [7])]
    assert sql.calls[0][1] == "db-password-never-output"
    assert connection.rollbacks == 1
    assert connection.closed is True and cursor.closed is True


def test_always_api_cleanup_runs_after_extractors_with_runtime_context() -> None:
    plan = ExecutionPlanResult(
        schema_version=1,
        run_id="run-cleanup",
        message_id="message-cleanup",
        runner_id="runner-cleanup",
        case_run_id=1,
        case_version_id=2,
        request=ApiRequestTemplate("GET", "https://pipeline.example.test/orders"),
        total_timeout_ms=900000,
        initial_context={"base_url": "https://pipeline.example.test"},
        extractors=(
            {
                "name": "order_id",
                "source": "JSONPATH",
                "expression": "$.data.id",
                "required": True,
                "default_value": None,
                "enabled": True,
            },
        ),
        post_actions=(
            {
                "type": "REGISTER_RESOURCE",
                "name": "created_order",
                "resource_type": "order",
                "value": "{{order_id}}",
                "metadata": {"origin": "api-case"},
                "cleanup": None,
                "cleanup_ref": "delete-order",
                "enabled": True,
            },
        ),
        cleanups=(
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
            },
        ),
    )
    http = RecordingClient()

    result = ApiCasePipelineExecutor(plan, api_executor=ApiExecutor(http)).execute()

    assert result.status_code == 200
    assert [(method, url) for method, url, _ in http.calls] == [
        ("GET", "https://pipeline.example.test/orders"),
        ("DELETE", "https://pipeline.example.test/orders/42"),
    ]
    assert result.resources[0]["resource_id"] == "42"
    assert result.resources[0]["status"] == "CLEANED"
    assert result.resources[0]["attempt_count"] == 1
