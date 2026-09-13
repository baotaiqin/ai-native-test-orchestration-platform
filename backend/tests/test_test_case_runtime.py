from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.exceptions import ResourceConflictError
from app.modules.auth.schemas import CurrentUser
from app.modules.test_cases.runtime import _run_sql_query, _validated_sql, preview_runtime
from app.modules.test_cases.schemas import ApiRequestTemplate, RuntimePreviewRequest, SqlQueryAction


def _request() -> ApiRequestTemplate:
    return ApiRequestTemplate(method="GET", url="https://example.test/users")


def test_runtime_preview_orders_pre_request_and_post_actions() -> None:
    payload = RuntimePreviewRequest(
        request=_request(),
        context={"user_id": 42},
        pre_actions=[
            {"name": "user_id", "value": 42, "enabled": True},
            {"type": "SET_VARIABLE", "name": "token", "value": "abc"},
        ],
        response={"json_body": {"id": 7}},
        post_actions=[
            {
                "type": "EXTRACT_RESPONSE",
                "name": "response_id",
                "source": "JSONPATH",
                "expression": "$.id",
            },
            {"type": "SET_VARIABLE", "name": "result", "value": "{{response_id}}"},
        ],
    )

    result = preview_runtime(payload)

    assert result.rendered_request["url"] == "https://example.test/users"
    assert [trace.phase for trace in result.traces] == [
        "PRE",
        "PRE",
        "REQUEST",
        "POST",
        "POST",
    ]
    assert [trace.action_type for trace in result.traces] == [
        "SET_VARIABLE",
        "SET_VARIABLE",
        "RENDERED_REQUEST",
        "EXTRACT_RESPONSE",
        "SET_VARIABLE",
    ]
    assert result.context["result"] == 7
    assert result.extracted == {"response_id": 7}


def test_legacy_variable_action_is_serialized_without_new_fields() -> None:
    payload = RuntimePreviewRequest(
        request=_request(),
        pre_actions=[{"name": "legacy", "value": "value", "enabled": False}],
    )

    assert payload.model_dump(mode="json")["pre_actions"] == [
        {"name": "legacy", "value": "value", "enabled": False}
    ]
    result = preview_runtime(payload)
    assert result.traces[0].status == "SKIPPED"


def test_faker_seed_is_repeatable() -> None:
    request = _request()
    payload = RuntimePreviewRequest(
        request=request,
        pre_actions=[
            {"type": "FAKER", "name": "email", "generator": "email", "seed": 1234}
        ],
    )

    first = preview_runtime(payload)
    second = preview_runtime(payload)

    assert first.context["email"] == second.context["email"]
    assert first.traces[0].detail["seed"] == 1234


def test_python_script_reuses_restricted_ast_executor() -> None:
    safe_payload = RuntimePreviewRequest(
        request=_request(),
        context={"value": 2},
        pre_actions=[
            {"type": "PYTHON_SCRIPT", "script": "context['doubled'] = context['value'] * 2"}
        ],
    )
    assert preview_runtime(safe_payload).context["doubled"] == 4

    for script in ("exec('context[\\'bad\\'] = 1')", "import os"):
        payload = RuntimePreviewRequest(
            request=_request(), pre_actions=[{"type": "PYTHON_SCRIPT", "script": script}]
        )
        with pytest.raises(ResourceConflictError):
            preview_runtime(payload)


def test_get_token_uses_simulated_snapshot_and_registers_preview_resource() -> None:
    payload = RuntimePreviewRequest(
        request=_request(),
        pre_actions=[
            {
                "type": "GET_TOKEN",
                "name": "token",
                "source": "HEADER",
                "expression": "X-Auth-Token",
                "response": {"headers": {"x-auth-token": "simulated-token"}},
            }
        ],
        post_actions=[
            {
                "type": "REGISTER_RESOURCE",
                "name": "created-user",
                "resource_type": "API",
                "value": "{{token}}",
                "metadata": {"source": "preview"},
            }
        ],
    )

    result = preview_runtime(payload)

    assert result.context["token"] == "simulated-token"
    assert result.context["__resources"]["created-user"] == {
        "resource_type": "API",
        "value": "simulated-token",
        "metadata": {"source": "preview"},
        "preview_only": True,
    }
    assert result.traces[0].detail["simulated"] is True
    assert result.traces[-1].detail["persisted"] is False


def test_action_validation_rejects_wrong_phase_and_missing_sql_project() -> None:
    with pytest.raises(ValidationError, match="Post Action 不支持类型"):
        RuntimePreviewRequest(
            request=_request(),
            post_actions=[
                {
                    "type": "GET_TOKEN",
                    "name": "token",
                    "source": "HEADER",
                    "expression": "X",
                }
            ],
        )

    with pytest.raises(ValidationError, match="必须提供 project_id"):
        RuntimePreviewRequest(
            request=_request(),
            pre_actions=[
                {
                    "type": "SQL_QUERY",
                    "connection_id": 1,
                    "sql": "SELECT 1",
                }
            ],
        )

    with pytest.raises(ValidationError):
        RuntimePreviewRequest(
            request=_request(),
            pre_actions=[{"type": "FAKER", "name": "x", "generator": "not-allowed"}],
        )


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: tuple[str, Any] | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any) -> None:
        self.executed = (statement, parameters)

    def fetchmany(self, size: int) -> list[dict[str, Any]]:
        assert size == 3
        return self.rows


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor([{"id": 1}, {"id": 2}, {"id": 3}])
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.events: list[str] = []

    def get(self, _model: Any, _identifier: int) -> Any:
        self.events.append("connection")
        return self.config


def test_sql_query_is_parameterized_read_only_scoped_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        id=9,
        project_id=10,
        enabled=True,
        host="localhost",
        port=3306,
        username="tester",
        password_secret_id=3,
        database_name="testing",
        ssl_enabled=False,
    )
    connection = _FakeConnection()
    captured: dict[str, Any] = {}
    monkeypatch.setattr("app.modules.test_cases.runtime.get_project", lambda *_: None)
    monkeypatch.setattr("app.modules.test_cases.runtime.resolve_secret", lambda *_: "secret")

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr("app.modules.test_cases.runtime.pymysql.connect", fake_connect)
    action = {
        "type": "SQL_QUERY",
        "connection_id": 9,
        "sql": "SELECT id FROM users WHERE name = %(name)s;",
        "params": {"name": "{{name}}"},
        "result_variable": "rows",
        "max_rows": 2,
    }
    payload = RuntimePreviewRequest(
        request=_request(),
        project_id=10,
        context={"name": "alice"},
        pre_actions=[action],
    )

    result = preview_runtime(payload, _FakeSession(config), CurrentUser(
        id="admin", username="admin", display_name="Admin", roles=["ADMIN"]
    ))

    assert result.context["rows"] == [{"id": 1}, {"id": 2}]
    assert result.traces[0].detail == {
        "result_variable": "rows",
        "row_count": 2,
        "truncated": True,
        "preview_rolled_back": True,
    }
    assert connection.cursor_instance.executed == (
        "SELECT id FROM users WHERE name = %(name)s",
        {"name": "alice"},
    )
    assert connection.rolled_back is True
    assert connection.closed is True
    assert captured["autocommit"] is False


def test_sql_query_rejects_project_isolation_and_dangerous_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(project_id=20, enabled=True)
    session = _FakeSession(config)
    action = {
        "type": "SQL_QUERY",
        "connection_id": 9,
        "sql": "SELECT 1",
    }
    monkeypatch.setattr(
        "app.modules.test_cases.runtime.get_project",
        lambda *_: session.events.append("project"),
    )
    with pytest.raises(ResourceConflictError, match="项目不匹配"):
        _run_sql_query(
            SqlQueryAction(**action),
            {},
            session,
            CurrentUser(id="admin", username="admin", display_name="Admin", roles=["ADMIN"]),
            10,
        )
    assert session.events == ["project", "connection"]

    for statement in (
        "UPDATE users SET name = 'x'",
        "SELECT 1; SELECT 2",
        "SELECT SLEEP(1)",
        "SELECT SLEEP\t(1)",
        "SELECT LOAD_FILE something",
        "SELECT 1 -- SLEEP(1)",
        "SELECT 1 # SLEEP(1)",
        "SELECT 1 /* SLEEP(1) */",
    ):
        with pytest.raises(ResourceConflictError):
            _validated_sql(statement)


def test_sql_query_rejects_runtime_templates_in_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(project_id=10, enabled=True)
    session = _FakeSession(config)
    monkeypatch.setattr("app.modules.test_cases.runtime.get_project", lambda *_: None)
    monkeypatch.setattr(
        "app.modules.test_cases.runtime.pymysql.connect",
        lambda **_: pytest.fail("SQL 文本模板被拒绝前不应建立连接"),
    )

    with pytest.raises(ResourceConflictError, match="请使用 params"):
        _run_sql_query(
            SqlQueryAction(
                connection_id=9,
                sql="SELECT id FROM users WHERE name = '{{name}}'",
                params={"name": "{{name}}"},
            ),
            {"name": "alice"},
            session,
            CurrentUser(id="admin", username="admin", display_name="Admin", roles=["ADMIN"]),
            10,
        )


def _assert_invalid_sql(statement: str) -> None:
    with pytest.raises(ResourceConflictError):
        _validated_sql(statement)


def test_sql_query_rejects_open_runtime_template_marker() -> None:
    _assert_invalid_sql("SELECT '{{ name'")


def test_sql_query_rejects_close_runtime_template_marker() -> None:
    _assert_invalid_sql("SELECT 'name }}'")


def test_sql_query_rejects_double_dash_comment() -> None:
    _assert_invalid_sql("SELECT 1 -- comment")


def test_sql_query_rejects_hash_comment() -> None:
    _assert_invalid_sql("SELECT 1 # comment")


def test_sql_query_rejects_block_comment_start() -> None:
    _assert_invalid_sql("SELECT 1 /* comment")


def test_sql_query_rejects_block_comment_end() -> None:
    _assert_invalid_sql("SELECT 1 comment */")


def test_sql_query_rejects_spaced_benchmark_function() -> None:
    _assert_invalid_sql("SELECT BENCHMARK (1, 1)")
