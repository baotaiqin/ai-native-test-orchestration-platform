from typing import Any

import pytest

from app.modules.scenarios import executor
from app.modules.scenarios.executor import execute_preview
from app.modules.scenarios.schemas import ScenarioDsl, ScenarioExecutionPreviewRequest


def _dsl(*nodes: dict[str, Any], **settings: Any) -> ScenarioDsl:
    return ScenarioDsl.model_validate(
        {
            "settings": {
                "stop_on_failure": True,
                "initial_variables": [],
                **settings,
            },
            "nodes": [
                {"id": "start", "type": "START", "name": "开始"},
                *nodes,
                {"id": "end", "type": "END", "name": "结束"},
            ],
        }
    )


def _run(dsl: ScenarioDsl, **payload: Any):
    return execute_preview(
        ScenarioExecutionPreviewRequest(dsl=dsl, **payload), object(), object()
    )


def test_scene_stop_is_authoritative_and_node_continue_requires_scene_opt_out() -> None:
    failing = {
        "id": "fail",
        "type": "PYTHON_SCRIPT",
        "name": "运行期错误",
        "failure_policy": "STOP",
        "config": {"script": 'context["missing"]'},
    }
    after = {
        "id": "after",
        "type": "SET_VARIABLE",
        "name": "后续节点",
        "config": {"name": "after", "value": True},
    }

    stopped = _run(_dsl(failing, after))
    assert stopped.status == "FAIL"
    assert stopped.stop_reason == "FAILURE_POLICY_STOP"
    assert stopped.context.get("after") is None
    assert next(item for item in stopped.traces if item.node_id == "after").status == "SKIPPED"
    failure_trace = next(item for item in stopped.traces if item.node_id == "fail")
    assert failure_trace.error_code == "PYTHON_RUNTIME_ERROR"
    assert "missing" not in (failure_trace.message or "")

    continued = _run(
        _dsl(
            {**failing, "failure_policy": "CONTINUE"},
            after,
        )
    )
    assert continued.status == "FAIL"
    assert continued.context.get("after") is None
    forced_trace = next(item for item in continued.traces if item.node_id == "fail")
    assert forced_trace.failure_policy.value == "STOP"

    node_continue = _run(
        _dsl(
            {**failing, "failure_policy": "CONTINUE"},
            after,
            stop_on_failure=False,
        )
    )
    assert node_continue.status == "FAIL"
    assert node_continue.context["after"] is True
    assert next(item for item in node_continue.traces if item.node_id == "after").status == "PASSED"

    legacy_continue = _run(
        _dsl(
            {**failing, "failure_policy": None},
            after,
            stop_on_failure=False,
        )
    )
    assert legacy_continue.status == "FAIL"
    assert legacy_continue.context["after"] is True
    legacy_trace = next(item for item in legacy_continue.traces if item.node_id == "fail")
    assert legacy_trace.failure_policy.value == "CONTINUE"


def test_retry_once_only_retries_retryable_current_node(monkeypatch: pytest.MonkeyPatch) -> None:
    dsl = _dsl(
        {
            "id": "flaky",
            "type": "SET_VARIABLE",
            "name": "瞬时错误",
            "failure_policy": "RETRY_ONCE",
            "config": {"name": "value", "value": "ok"},
        },
        {
            "id": "after",
            "type": "SET_VARIABLE",
            "name": "后续",
            "config": {"name": "after", "value": True},
        },
    )
    original = executor._PreviewExecutor._execute_node_once
    calls = 0

    def fail_then_pass(self: Any, node: Any) -> dict[str, Any]:
        nonlocal calls
        if node.id == "flaky":
            calls += 1
            if calls == 1:
                raise executor._PreviewRuntimeError(
                    "TRANSIENT_RUNTIME_ERROR", "可重试运行期错误", retryable=True
                )
        return original(self, node)

    monkeypatch.setattr(executor._PreviewExecutor, "_execute_node_once", fail_then_pass)
    result = _run(dsl)
    flaky = [item for item in result.traces if item.node_id == "flaky"]
    assert [item.status for item in flaky] == ["FAILED", "PASSED"]
    assert [item.attempt for item in flaky] == [1, 2]
    assert all(item.max_attempts == 2 for item in flaky)
    assert flaky[0].terminal is False
    assert result.status == "PASS"
    assert result.context["after"] is True

    calls = 0

    def fail_twice(self: Any, node: Any) -> dict[str, Any]:
        nonlocal calls
        if node.id == "flaky":
            calls += 1
            raise executor._PreviewRuntimeError(
                "TRANSIENT_RUNTIME_ERROR", "可重试运行期错误", retryable=True
            )
        return original(self, node)

    monkeypatch.setattr(executor._PreviewExecutor, "_execute_node_once", fail_twice)
    result = _run(dsl)
    flaky = [item for item in result.traces if item.node_id == "flaky"]
    assert [item.attempt for item in flaky] == [1, 2]
    assert flaky[-1].terminal is True
    assert result.status == "FAIL"
    assert result.stop_reason == "RETRY_EXHAUSTED"
    assert next(item for item in result.traces if item.node_id == "after").status == "SKIPPED"

    non_retryable = _run(
        _dsl(
            {
                "id": "not_retryable",
                "type": "PYTHON_SCRIPT",
                "name": "不可重试错误",
                "failure_policy": "RETRY_ONCE",
                "config": {"script": 'context["missing"]'},
            },
            {
                "id": "after",
                "type": "SET_VARIABLE",
                "name": "后续",
                "config": {"name": "after", "value": True},
            },
            initial_variables=["missing"],
        )
    )
    trace = next(item for item in non_retryable.traces if item.node_id == "not_retryable")
    assert trace.attempt == 1
    assert trace.max_attempts == 2
    assert trace.retryable is False


def test_wait_and_python_timeout_are_bounded_without_sleep() -> None:
    wait_result = _run(
        _dsl(
            {
                "id": "wait",
                "type": "WAIT",
                "name": "超时等待",
                "timeout_ms": 100,
                "config": {"duration_ms": 101},
            }
        )
    )
    wait = next(item for item in wait_result.traces if item.node_id == "wait")
    assert wait.status == "TIMEOUT"
    assert wait.error_code == "WAIT_TIMEOUT"
    assert wait.duration_ms == 100
    assert wait.timeout_ms == 100
    assert wait_result.status == "FAIL"

    long_expression = "+".join("1" for _ in range(40))
    script_result = _run(
        _dsl(
            {
                "id": "script",
                "type": "PYTHON_SCRIPT",
                "name": "预算超时",
                "timeout_ms": 100,
                "config": {"script": f'context["value"] = {long_expression}'},
            }
        )
    )
    script = next(item for item in script_result.traces if item.node_id == "script")
    assert script.status == "TIMEOUT"
    assert script.error_code == "PYTHON_TIMEOUT"
    assert script.detail["max_steps"] == 20


def test_sql_timeout_maps_safely_and_rolls_back_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    class ConnectionConfig:
        project_id = 1
        enabled = True
        password_secret_id = 1
        host = "mock"
        port = 3306
        username = "tester"
        database_name = "demo"
        ssl_enabled = False

    class Session:
        def get(self, model: Any, identifier: int) -> Any:
            return ConnectionConfig()

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, statement: str, parameters: dict[str, Any]) -> int:
            raise executor.pymysql.OperationalError(2013, "timed out")

    class Connection:
        rolled_back = False
        closed = False

        def cursor(self) -> Cursor:
            return Cursor()

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    connect_kwargs: list[dict[str, Any]] = []
    monkeypatch.setattr(executor, "get_project", lambda *args: object())
    monkeypatch.setattr(executor, "resolve_secret", lambda *args: "password")
    monkeypatch.setattr(
        executor.pymysql,
        "connect",
        lambda **kwargs: (connect_kwargs.append(kwargs) or connection),
    )
    result = execute_preview(
        ScenarioExecutionPreviewRequest(
            project_id=1,
            dsl=_dsl(
                {
                    "id": "query",
                    "type": "SQL_QUERY",
                    "name": "超时查询",
                    "timeout_ms": 100,
                    "failure_policy": "RETRY_ONCE",
                    "config": {
                        "connection_id": 1,
                        "sql": "SELECT 1",
                        "params": {},
                    },
                }
            ),
        ),
        Session(),
        object(),
    )
    query = [item for item in result.traces if item.node_id == "query"]
    assert [item.status for item in query] == ["TIMEOUT", "TIMEOUT"]
    assert query[-1].error_code == "SQL_TIMEOUT"
    assert result.status == "FAIL"
    assert connection.rolled_back is True
    assert connection.closed is True
    assert connect_kwargs[0]["connect_timeout"] == 0.1


def test_debug_modes_respect_branch_loop_and_explicit_context() -> None:
    dsl = _dsl(
        {
            "id": "check",
            "type": "IF",
            "name": "分支",
            "config": {"condition": "{{ready}} == true"},
        },
        {
            "id": "target",
            "type": "SET_VARIABLE",
            "name": "目标",
            "parent_id": "check",
            "config": {"name": "target_ran", "value": True},
        },
        {
            "id": "else_branch",
            "type": "ELSE",
            "name": "否则",
            "parent_id": "check",
        },
        {
            "id": "after",
            "type": "SET_VARIABLE",
            "name": "后续",
            "config": {"name": "after_ran", "value": True},
        },
        initial_variables=["ready"],
    )
    node = _run(
        dsl,
        mode="NODE",
        target_node_id="target",
        context={"ready": True},
    )
    assert [item.node_id for item in node.traces] == ["target"]
    assert node.context["target_ran"] is True

    to_here = _run(dsl, mode="RUN_TO_HERE", target_node_id="target", context={"ready": True})
    assert to_here.status == "PASS"
    assert to_here.target_reached is True
    assert to_here.stop_reason == "DEBUG_TARGET_REACHED"
    assert next(item for item in to_here.traces if item.node_id == "after").status == "SKIPPED"

    unreachable = _run(
        dsl,
        mode="RUN_TO_HERE",
        target_node_id="target",
        context={"ready": False},
    )
    assert unreachable.status == "REVIEW"
    assert unreachable.target_reached is False
    assert unreachable.stop_reason == "TARGET_UNREACHABLE"
    assert next(item for item in unreachable.traces if item.node_id == "target").status == "SKIPPED"

    from_here = _run(dsl, mode="RUN_FROM_HERE", target_node_id="target", context={})
    assert [item.node_id for item in from_here.traces] == [
        "target",
        "else_branch",
        "after",
        "end",
    ]
    else_trace = next(item for item in from_here.traces if item.node_id == "else_branch")
    assert else_trace.status == "SKIPPED"
    assert from_here.context["after_ran"] is True

    loop_dsl = _dsl(
        {"id": "loop", "type": "LOOP", "name": "循环", "config": {"iterations": 3}},
        {
            "id": "loop_target",
            "type": "SET_VARIABLE",
            "name": "循环目标",
            "parent_id": "loop",
            "config": {"name": "last", "value": "{{loop_index}}"},
        },
        initial_variables=["loop_index"],
    )
    full = _run(loop_dsl)
    hits = [item for item in full.traces if item.node_id == "loop_target"]
    assert [item.iteration_path for item in hits] == [[0], [1], [2]]
    to_loop = _run(loop_dsl, mode="RUN_TO_HERE", target_node_id="loop_target")
    assert to_loop.target_reached is True
    assert [item.status for item in to_loop.traces if item.node_id == "loop_target"] == [
        "PASSED",
        "SKIPPED",
        "SKIPPED",
    ]


def test_debug_target_validation_and_node_missing_context_are_explicit() -> None:
    dsl = _dsl(
        {
            "id": "disabled",
            "type": "SET_VARIABLE",
            "name": "禁用",
            "enabled": False,
            "config": {"name": "skip", "value": True},
        },
        {
            "id": "leaf",
            "type": "SET_VARIABLE",
            "name": "叶子",
            "config": {"name": "x", "value": "{{required}}"},
        },
        initial_variables=["required"],
    )
    with pytest.raises(Exception, match="停用"):
        _run(dsl, mode="NODE", target_node_id="disabled")
    with pytest.raises(Exception, match="不存在"):
        _run(dsl, mode="NODE", target_node_id="unknown")
    with pytest.raises(Exception, match="START"):
        _run(dsl, mode="NODE", target_node_id="start")
    with pytest.raises(Exception, match="START"):
        _run(dsl, mode="NODE", target_node_id="end")

    missing = _run(dsl, mode="NODE", target_node_id="leaf", context={})
    trace = next(item for item in missing.traces if item.node_id == "leaf")
    assert missing.status == "FAIL"
    assert trace.error_code == "MISSING_RUNTIME_CONTEXT"
    assert trace.message == "Runtime Context 缺少变量：required"

    loop_target = _dsl(
        {"id": "loop", "type": "LOOP", "name": "循环", "config": {"iterations": 2}},
        {
            "id": "inside",
            "type": "SET_VARIABLE",
            "name": "循环内",
            "parent_id": "loop",
            "config": {"name": "x", "value": True},
        },
    )
    with pytest.raises(Exception, match="LOOP"):
        _run(loop_target, mode="RUN_FROM_HERE", target_node_id="inside")

    with pytest.raises(ValueError, match="target_node_id"):
        ScenarioExecutionPreviewRequest.model_validate(
            {"dsl": dsl.model_dump(mode="json"), "mode": "NODE"}
        )
    with pytest.raises(ValueError, match="extra"):
        ScenarioExecutionPreviewRequest.model_validate(
            {"dsl": dsl.model_dump(mode="json"), "unexpected": True}
        )


def test_cleanup_is_not_skipped_by_normal_stop_and_old_full_request_works() -> None:
    dsl = _dsl(
        {
            "id": "fail",
            "type": "PYTHON_SCRIPT",
            "name": "失败",
            "config": {"script": 'context["missing"]'},
        },
        {
            "id": "cleanup",
            "type": "API_CLEANUP",
            "name": "清理",
            "config": {
                "cleanup_type": "API",
                "method": "DELETE",
                "url": "https://example.test/items/{{item_id}}",
                "query_params": [],
                "headers": [],
                "cookies": [],
                "body": {"type": "NONE"},
                "auth": {"type": "NONE"},
            },
        },
        initial_variables=["item_id", "missing"],
    )
    result = _run(dsl, context={"item_id": "item-1"})
    assert result.status == "FAIL"
    assert next(item for item in result.traces if item.node_id == "cleanup").status == "PASSED"
    cleanup_trace = next(item for item in result.traces if item.node_id == "cleanup")
    assert cleanup_trace.detail["persisted"] is False

    old_request = _run(
        _dsl(
            {
                "id": "set",
                "type": "SET_VARIABLE",
                "name": "设置",
                "config": {"name": "ok", "value": True},
            }
        )
    )
    assert old_request.mode.value == "FULL"
    assert old_request.status == "PASS"


def test_each_http_node_activates_its_own_response_fixture() -> None:
    dsl = _dsl(
        {
            "id": "login",
            "type": "HTTP",
            "name": "登录",
            "config": {"method": "POST", "url": "https://example.test/login"},
        },
        {
            "id": "extract_token",
            "type": "EXTRACT",
            "name": "提取令牌",
            "config": {
                "name": "token",
                "source": "JSONPATH",
                "expression": "$.data.token",
            },
        },
        {
            "id": "products",
            "type": "HTTP",
            "name": "查询商品",
            "config": {"method": "GET", "url": "https://example.test/products"},
        },
        {
            "id": "extract_product",
            "type": "EXTRACT",
            "name": "提取商品",
            "config": {
                "name": "product_id",
                "source": "JSONPATH",
                "expression": "$.items[0].id",
            },
        },
    )
    responses = {
        "login": {"status_code": 200, "json_body": {"data": {"token": "demo"}}},
        "products": {"status_code": 200, "json_body": {"items": [{"id": 7}]}},
    }

    result = _run(dsl, responses_by_node=responses)

    assert result.status == "PASS"
    assert result.context["token"] == "demo"
    assert result.context["product_id"] == 7
    product_only = _run(
        dsl,
        responses_by_node=responses,
        mode="NODE",
        target_node_id="extract_product",
    )
    assert product_only.status == "PASS"
    assert product_only.context["product_id"] == 7
