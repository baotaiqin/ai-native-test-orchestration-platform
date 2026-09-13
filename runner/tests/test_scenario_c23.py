from __future__ import annotations

import json
from threading import Event

import httpx
import pymysql
import pytest

from runner import scenario as scenario_module
from runner.executors.api import ApiExecutor
from runner.models import (
    ScenarioExecutionPlanResult,
    ScenarioExecutionSecret,
    ScenarioNodePlan,
    ScenarioSqlConnectionPlan,
)
from runner.scenario import ScenarioExecutor


def _node(
    node_id: str,
    node_type: str,
    *,
    parent_id: str | None = None,
    config: dict[str, object] | None = None,
    failure_policy: str | None = None,
    timeout_ms: int = 1_000,
) -> ScenarioNodePlan:
    return ScenarioNodePlan(
        node_id,
        node_type,
        node_id,
        parent_id,
        True,
        timeout_ms,
        failure_policy,
        config or {},
    )


def _plan(
    *nodes: ScenarioNodePlan,
    initial_context: dict[str, object] | None = None,
    stop_on_failure: bool = True,
    sql_connections: tuple[ScenarioSqlConnectionPlan, ...] = (),
    secrets: tuple[ScenarioExecutionSecret, ...] = (),
) -> ScenarioExecutionPlanResult:
    return ScenarioExecutionPlanResult(
        1,
        "run-c23",
        "message-c23",
        "runner-c23",
        1,
        1,
        1,
        30_000,
        initial_context or {},
        {
            "stop_on_failure": stop_on_failure,
            "cleanup_policy": "ALWAYS",
            "max_loop_iterations": 10,
            "initial_variables": [],
        },
        tuple(nodes),
        sql_connections,
        secrets,
    )


SQL_CONNECTION = ScenarioSqlConnectionPlan(
    1, "MYSQL", "db.example.test", 3306, "test_db", "runner", 10, False
)
SQL_SECRET = ScenarioExecutionSecret(10, "db-password-never-output")


class _FakeCursor:
    def __init__(self, *, rows: list[object] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    def execute(self, statement: str, parameters: object) -> int:
        self.calls.append((statement, parameters))
        if self.error is not None:
            raise self.error
        return 3

    def fetchmany(self, size: int) -> list[object]:
        return self.rows[:size]

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _SqlFactory:
    def __init__(self, *connections: _FakeConnection) -> None:
        self.connections = list(connections)
        self.calls: list[tuple[object, str, int]] = []

    def __call__(self, connection: object, password: str, timeout_ms: int) -> _FakeConnection:
        self.calls.append((connection, password, timeout_ms))
        return self.connections.pop(0)


class _FakeHttpClient:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def _response(status: int = 204) -> httpx.Response:
    return httpx.Response(
        status,
        json={"ok": True},
        request=httpx.Request("DELETE", "https://cleanup.example.test/resource"),
    )


def _cleanup_api(
    node_id: str,
    policy: str,
    *,
    failure_policy: str | None = None,
    auth: dict[str, object] | None = None,
) -> ScenarioNodePlan:
    return _node(
        node_id,
        "API_CLEANUP",
        failure_policy=failure_policy,
        config={
            "cleanup_type": "API",
            "cleanup_id": node_id,
            "policy": policy,
            "enabled": True,
            "timeout_ms": 1_000,
            "method": "DELETE",
            "url": f"https://cleanup.example.test/{node_id}",
            "query_params": [],
            "headers": [],
            "cookies": [],
            "body": {"type": "NONE"},
            "auth": auth or {"type": "NONE"},
            "connection_id": None,
            "sql": None,
            "params": [],
            "resource_id_param": None,
        },
    )


def _cleanup_sql(node_id: str, policy: str) -> ScenarioNodePlan:
    return _node(
        node_id,
        "SQL_CLEANUP",
        config={
            "cleanup_type": "SQL",
            "cleanup_id": node_id,
            "policy": policy,
            "enabled": True,
            "timeout_ms": 1_000,
            "method": None,
            "url": None,
            "query_params": [],
            "headers": [],
            "cookies": [],
            "body": None,
            "auth": {"type": "NONE"},
            "connection_id": 1,
            "sql": "DELETE FROM records WHERE id=%(id)s",
            "params": {"id": 1},
            "resource_id_param": None,
        },
    )


def test_sql_query_binds_params_keeps_json_safe_rows_and_closes_resources() -> None:
    cursor = _FakeCursor(rows=[(1, "ok"), {"value": 2}])
    connection = _FakeConnection(cursor)
    factory = _SqlFactory(connection)
    plan = _plan(
        _node("start", "START"),
        _node(
            "query",
            "SQL_QUERY",
            config={
                "connection_id": 1,
                "sql": "SELECT id, value FROM records WHERE id=%(id)s",
                "params": {"id": "{{number}}"},
                "result_variable": "rows",
                "max_rows": 1000,
            },
        ),
        _node("end", "END"),
        initial_context={"number": 7},
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    assert result.outcome == "SUCCESS"
    assert cursor.calls == [
        ("SELECT id, value FROM records WHERE id=%(id)s", {"id": 7})
    ]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert cursor.closed and connection.closed
    assert result.traces[1]["status"] == "SUCCESS"


def test_sql_execute_commits_and_rolls_back_on_driver_error_without_secret_output() -> None:
    success_cursor = _FakeCursor()
    success_connection = _FakeConnection(success_cursor)
    success_factory = _SqlFactory(success_connection)
    success_plan = _plan(
        _node("start", "START"),
        _node(
            "execute",
            "SQL_EXECUTE",
            config={
                "connection_id": 1,
                "sql": "UPDATE records SET value=%(value)s WHERE id=%(id)s",
                "params": {"value": "new", "id": 1},
            },
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )
    success = ScenarioExecutor(success_plan, sql_connector=success_factory).execute()

    error_cursor = _FakeCursor(error=pymysql.MySQLError("database-secret-error"))
    error_connection = _FakeConnection(error_cursor)
    error_factory = _SqlFactory(error_connection)
    error = ScenarioExecutor(
        success_plan, sql_connector=error_factory
    ).execute()

    assert success.outcome == "SUCCESS"
    assert success_connection.commits == 1
    assert success_connection.rollbacks == 0
    assert error.outcome == "FAILED"
    assert error_connection.rollbacks == 1
    assert error_connection.closed and error_cursor.closed
    assert "database-secret-error" not in json.dumps(error.to_wire())
    assert "db-password-never-output" not in repr(error)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT id; DELETE FROM records",
        "SELECT id -- comment\nFROM records",
        "SELECT SLEEP(10)",
        "UPDATE records SET value=1",
        "SELECT id FOR UPDATE",
    ],
)
def test_sql_query_rejects_comments_multistatement_mutation_and_locks(
    statement: str,
) -> None:
    factory = _SqlFactory()
    plan = _plan(
        _node("start", "START"),
        _node("query", "SQL_QUERY", config={"connection_id": 1, "sql": statement}),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    assert result.outcome == "FAILED"
    assert not factory.calls
    assert all("db-password-never-output" not in json.dumps(trace) for trace in result.traces)


@pytest.mark.parametrize(
    ("connections", "secrets"),
    [
        ((), (SQL_SECRET,)),
        ((SQL_CONNECTION, SQL_CONNECTION), (SQL_SECRET,)),
        ((ScenarioSqlConnectionPlan(1, "POSTGRES", "db", 1, "db", "u", 10, False),), (SQL_SECRET,)),
        ((SQL_CONNECTION,), ()),
        ((SQL_CONNECTION,), (SQL_SECRET, ScenarioExecutionSecret(10, "duplicate"))),
    ],
)
def test_sql_exact_connection_and_secret_resolution_fails_closed(
    connections: tuple[ScenarioSqlConnectionPlan, ...],
    secrets: tuple[ScenarioExecutionSecret, ...],
) -> None:
    factory = _SqlFactory()
    plan = _plan(
        _node("start", "START"),
        _node("query", "SQL_QUERY", config={"connection_id": 1, "sql": "SELECT 1"}),
        _node("end", "END"),
        sql_connections=connections,
        secrets=secrets,
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    assert result.outcome == "FAILED"
    assert not factory.calls


def test_sql_transient_error_retries_once_but_keeps_retry_count_aggregated() -> None:
    first = _FakeConnection(_FakeCursor(error=pymysql.OperationalError(1213, "deadlock")))
    second = _FakeConnection(_FakeCursor())
    factory = _SqlFactory(first, second)
    plan = _plan(
        _node("start", "START"),
        _node(
            "execute",
            "SQL_EXECUTE",
            failure_policy="RETRY_ONCE",
            config={"connection_id": 1, "sql": "DELETE FROM records WHERE id=%s", "params": [1]},
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    trace = next(item for item in result.traces if item["node_id"] == "execute")
    assert result.outcome == "SUCCESS"
    assert len(factory.calls) == 2
    assert trace["retry_count"] == 1


@pytest.mark.parametrize(
    "error",
    [pymysql.OperationalError(2013, "lost connection"), OSError("connection lost")],
)
def test_sql_execute_connection_loss_is_not_retried(
    error: Exception,
) -> None:
    connection = _FakeConnection(_FakeCursor(error=error))
    factory = _SqlFactory(connection)
    plan = _plan(
        _node("start", "START"),
        _node(
            "execute",
            "SQL_EXECUTE",
            failure_policy="RETRY_ONCE",
            config={"connection_id": 1, "sql": "DELETE FROM records WHERE id=%s", "params": [1]},
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    trace = next(item for item in result.traces if item["node_id"] == "execute")
    assert len(factory.calls) == 1
    assert trace["retry_count"] == 0
    assert result.outcome in {"FAILED", "TIMEOUT"}


def test_sql_query_connection_loss_remains_retryable() -> None:
    first = _FakeConnection(
        _FakeCursor(error=pymysql.OperationalError(2013, "lost connection"))
    )
    second = _FakeConnection(_FakeCursor(rows=[{"id": 1}]))
    factory = _SqlFactory(first, second)
    plan = _plan(
        _node("start", "START"),
        _node(
            "query",
            "SQL_QUERY",
            failure_policy="RETRY_ONCE",
            config={"connection_id": 1, "sql": "SELECT id FROM records"},
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    trace = next(item for item in result.traces if item["node_id"] == "query")
    assert result.outcome == "SUCCESS"
    assert len(factory.calls) == 2
    assert trace["retry_count"] == 1


def test_default_sql_connector_uses_dict_cursor_and_bounded_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(scenario_module.pymysql, "connect", fake_connect)

    connection = scenario_module._default_sql_connector(SQL_CONNECTION, "secret", 1_500)

    assert connection is sentinel
    assert captured["cursorclass"] is pymysql.cursors.DictCursor
    assert captured["connect_timeout"] == 1.5
    assert captured["read_timeout"] == 1.5
    assert captured["write_timeout"] == 1.5
    assert captured["autocommit"] is False


def test_cleanup_action_duration_exceeding_node_timeout_is_timeout() -> None:
    class _Clock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0.0 if self.calls == 1 else 2.0

    http = _FakeHttpClient(_response(204))
    plan = _plan(
        _cleanup_api("slow-cleanup", "ALWAYS"),
    )

    result = ScenarioExecutor(
        plan,
        clock=_Clock(),
        api_executor=ApiExecutor(http),
    ).execute()

    trace = next(item for item in result.traces if item["node_id"] == "slow-cleanup")
    assert result.outcome == "TIMEOUT"
    assert trace["status"] == "TIMEOUT"
    assert trace["error_type"] == "CLEANUP_NODE_TIMEOUT"
    assert trace["duration_ms"] == 2_000


def test_cleanup_retry_timeout_is_per_attempt_while_trace_duration_accumulates() -> None:
    class _Clock:
        def __init__(self) -> None:
            self.values = iter((0.0, 0.6, 0.6, 1.2))

        def __call__(self) -> float:
            return next(self.values)

    http = _FakeHttpClient(_response(503), _response(204))
    plan = _plan(
        _cleanup_api("retry-cleanup", "ALWAYS", failure_policy="RETRY_ONCE"),
    )

    result = ScenarioExecutor(
        plan,
        clock=_Clock(),
        api_executor=ApiExecutor(http),
    ).execute()

    trace = next(item for item in result.traces if item["node_id"] == "retry-cleanup")
    assert result.outcome == "SUCCESS"
    assert len(http.calls) == 2
    assert trace["retry_count"] == 1
    assert trace["duration_ms"] == 1_200


def test_python_script_updates_typed_context_without_running_python_code() -> None:
    plan = _plan(
        _node("start", "START"),
        _node(
            "script",
            "PYTHON_SCRIPT",
            config={"script": 'context["answer"] = int(context["number"]) + 1'},
        ),
        _node("end", "END"),
        initial_context={"number": 4},
    )
    executor = ScenarioExecutor(plan)

    result = executor.execute()

    assert result.outcome == "SUCCESS"
    assert executor.context["answer"] == 5
    assert result.traces[1]["error_type"] is None


@pytest.mark.parametrize(
    "script",
    [
        "import os",
        'context.__class__ = "bad"',
        'exec("context[\\"bad\\"] = 1")',
        "while True: pass",
        'context["x"] = ["x"] * 100000000',
    ],
)
def test_python_script_rejects_escape_holes_and_large_work(script: str) -> None:
    plan = _plan(
        _node("start", "START"),
        _node("script", "PYTHON_SCRIPT", config={"script": script}),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan).execute()

    assert result.outcome in {"FAILED", "TIMEOUT"}
    assert next(item for item in result.traces if item["node_id"] == "script")["status"] in {
        "FAILED",
        "TIMEOUT",
    }
    assert "import" not in json.dumps(result.to_wire())


def test_cleanup_runs_in_lifo_after_failure_and_failure_does_not_block_later_items() -> None:
    http = _FakeHttpClient(_response(204))
    api_executor = ApiExecutor(http)
    sql_connection = _FakeConnection(
        _FakeCursor(error=pymysql.MySQLError("cleanup-secret-error"))
    )
    sql_factory = _SqlFactory(sql_connection)
    plan = _plan(
        _node("start", "START"),
        _node("failed", "ASSERT_STATUS", config={"expected": 200}),
        _cleanup_api("api-first", "ON_FAILURE"),
        _cleanup_sql("sql-second", "ON_FAILURE"),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(
        plan, api_executor=api_executor, sql_connector=sql_factory
    ).execute()

    assert result.outcome == "FAILED"
    assert [call[1] for call in http.calls] == [
        "https://cleanup.example.test/api-first",
    ]
    assert sql_connection.commits == 0
    api_trace = next(item for item in result.traces if item["node_id"] == "api-first")
    sql_trace = next(item for item in result.traces if item["node_id"] == "sql-second")
    assert api_trace["status"] == "SUCCESS"
    assert sql_trace["status"] == "FAILED"
    assert "db-password-never-output" not in json.dumps(result.to_wire())


def test_cleanup_policy_runs_only_matching_outcome() -> None:
    http = _FakeHttpClient(_response(204), _response(204))
    plan = _plan(
        _node("start", "START"),
        _cleanup_api("always", "ALWAYS"),
        _cleanup_api("success", "ON_SUCCESS"),
        _cleanup_api("failure", "ON_FAILURE"),
        _cleanup_api("never", "NEVER"),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(http)).execute()

    assert result.outcome == "SUCCESS"
    assert [call[1] for call in http.calls] == [
        "https://cleanup.example.test/success",
        "https://cleanup.example.test/always",
    ]
    assert (
        next(item for item in result.traces if item["node_id"] == "success")["status"]
        == "SUCCESS"
    )
    assert (
        next(item for item in result.traces if item["node_id"] == "failure")["status"]
        == "SKIPPED"
    )
    assert (
        next(item for item in result.traces if item["node_id"] == "never")["status"]
        == "SKIPPED"
    )


def test_api_cleanup_retries_only_target_5xx_and_reports_one_trace() -> None:
    http = _FakeHttpClient(_response(503), _response(204))
    plan = _plan(
        _node("start", "START"),
        _cleanup_api("cleanup", "ALWAYS", failure_policy="RETRY_ONCE"),
        _node("end", "END"),
    )

    result = ScenarioExecutor(plan, api_executor=ApiExecutor(http)).execute()

    trace = next(item for item in result.traces if item["node_id"] == "cleanup")
    assert result.outcome == "SUCCESS"
    assert len(http.calls) == 2
    assert trace["status"] == "SUCCESS"
    assert trace["retry_count"] == 1


def test_cleanup_can_run_after_cooperative_cancel_and_secret_auth_stays_in_memory() -> None:
    http = _FakeHttpClient(_response(204))
    plan = _plan(
        _node("start", "START"),
        _cleanup_api(
            "cleanup",
            "ON_FAILURE",
            auth={"type": "BEARER", "secret_id": 10},
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )
    stop_event = Event()
    stop_event.set()

    result = ScenarioExecutor(plan, stop_event=stop_event, api_executor=ApiExecutor(http)).execute()

    assert result.outcome == "CANCELLED"
    assert len(http.calls) == 1
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer db-password-never-output"
    assert "db-password-never-output" not in json.dumps(result.to_wire())


def test_looped_sql_node_has_one_aggregated_trace() -> None:
    first = _FakeConnection(_FakeCursor(rows=[(1,)]))
    second = _FakeConnection(_FakeCursor(rows=[(2,)]))
    factory = _SqlFactory(first, second)
    plan = _plan(
        _node("start", "START"),
        _node("loop", "LOOP", config={"iterations": 2, "item_variable": "item"}),
        _node(
            "query",
            "SQL_QUERY",
            parent_id="loop",
            config={"connection_id": 1, "sql": "SELECT id FROM records", "max_rows": 10},
        ),
        _node("end", "END"),
        sql_connections=(SQL_CONNECTION,),
        secrets=(SQL_SECRET,),
    )

    result = ScenarioExecutor(plan, sql_connector=factory).execute()

    traces = [item for item in result.traces if item["node_id"] == "query"]
    assert result.outcome == "SUCCESS"
    assert len(traces) == 1
    assert traces[0]["retry_count"] == 0
    assert traces[0]["duration_ms"] >= 0
