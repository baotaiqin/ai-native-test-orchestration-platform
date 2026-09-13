from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.exceptions import ResourceConflictError
from app.modules.demo_bootstrap.service import PROMPTS
from app.modules.model_center.schemas import AiTaskType
from app.modules.web_design import service as web_design_service
from app.modules.web_design.schemas import (
    ExplorationCompleteRequest,
    ExplorationCreateRequest,
    ExplorationDecision,
    WebPlanResult,
)
from app.modules.web_design.service import (
    _decision_validator,
    _json_snapshot,
    _reconcile_validator,
    _validate_plan_result,
    get_exploration_execution_plan,
)


def test_builtin_catalog_contains_all_web_design_structured_prompts() -> None:
    by_task = {task_type: result_type for task_type, _, _, result_type, _, _ in PROMPTS}
    assert by_task[AiTaskType.WEB_TEST_PLAN] is WebPlanResult
    assert AiTaskType.WEB_EXPLORATION_DECISION in by_task
    assert AiTaskType.WEB_PLAN_RECONCILE in by_task


def test_plan_result_must_only_reference_immutable_source_ids() -> None:
    snapshot = {
        "requirement_source": {"scope": [{"requirement_id": 10}]},
        "api_definitions": [{"id": 20}],
    }
    value = {
        "summary": "覆盖登录",
        "candidates": [
            {
                "candidate_key": "login_ok",
                "name": "登录成功",
                "objective": "验证登录",
                "category": "POSITIVE",
                "priority": "P0",
                "rationale": "核心路径",
                "requirement_ids": [10],
                "api_definition_ids": [20],
                "preconditions": [],
                "planned_steps": ["打开登录页"],
                "expected_outcomes": ["登录成功"],
                "start_url_hint": "https://example.test/login",
            }
        ],
        "uncovered_requirement_ids": [],
    }
    assert _validate_plan_result(snapshot, value)["candidates"][0]["candidate_key"] == "login_ok"
    value["candidates"][0]["start_url_hint"] = "/login"
    assert _validate_plan_result(snapshot, value)["candidates"][0]["start_url_hint"] == "/login"
    value["candidates"][0]["api_definition_ids"] = [99]
    with pytest.raises(ResourceConflictError, match="API 范围外"):
        _validate_plan_result(snapshot, value)
    value["candidates"][0]["api_definition_ids"] = [20]
    value["candidates"][0]["planned_steps"] = ["调用 GET /api/products 接口"]
    with pytest.raises(ResourceConflictError, match="UI 操作"):
        _validate_plan_result(snapshot, value)


def test_exploration_request_normalizes_and_binds_start_origin() -> None:
    request = ExplorationCreateRequest(
        runner_id="runner-1",
        decision_prompt_id=1,
        start_url="https://EXAMPLE.test/path",
        allowed_origins=[],
    )
    assert request.allowed_origins == ["https://example.test"]
    assert request.headless is True
    assert [item.value for item in request.permissions] == ["PAGE_READ"]
    with pytest.raises(ValidationError, match="MANAGED_LOGIN"):
        ExplorationCreateRequest(
            runner_id="runner-1",
            decision_prompt_id=1,
            start_url="https://example.test/path",
            use_login_credentials=True,
        )
    with pytest.raises(ValidationError):
        ExplorationCreateRequest(
            runner_id="runner-1",
            decision_prompt_id=1,
            start_url="https://example.test/path",
            allowed_origins=["https://outside.test"],
        )


def test_exploration_decision_and_completion_are_bounded() -> None:
    decision = ExplorationDecision(
        action="CLICK", reason="展开只读详情", element="详情", ref="e12"
    )
    assert decision.ref == "e12"
    with pytest.raises(ValidationError):
        ExplorationDecision(action="CLICK", reason="缺少快照引用", element="详情")
    with pytest.raises(ValidationError):
        ExplorationDecision(
            action="TYPE",
            reason="输入",
            element="Password",
            ref="e13",
            value="password=plain-secret",
        )
    with pytest.raises(ValidationError):
        ExplorationCompleteRequest(
            message_id="message-1",
            outcome="FAILED",
            observations=[],
            action_trace=[],
        )
    with pytest.raises(ValidationError, match="凭据"):
        ExplorationCompleteRequest(
            message_id="message-1",
            outcome="COMPLETED",
            observations=[],
            action_trace=[{"result": "token=plain-secret"}],
        )
    safe = ExplorationCompleteRequest(
        message_id="message-1",
        outcome="COMPLETED",
        observations=[],
        action_trace=[{"result": "token=[REDACTED]"}],
    )
    assert safe.outcome == "COMPLETED"


def test_exploration_login_inputs_only_accept_scoped_runner_references() -> None:
    exploration = SimpleNamespace(
        allowed_origins=["https://example.test"],
        use_login_credentials=True,
    )
    validator = _decision_validator(
        exploration,
        {"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
    )

    assert validator({
        "action": "TYPE",
        "reason": "输入项目测试账号",
        "element": "用户名输入框",
        "ref": "e1",
        "value": "{{secret.AI_TEST_USERNAME}}",
    })
    assert validator({
        "action": "TYPE",
        "reason": "输入合成错误密码",
        "element": "Password",
        "ref": "e2",
        "value": "{{faker.INVALID_PASSWORD}}",
    })
    assert not validator({
        "action": "TYPE",
        "reason": "错误字段",
        "element": "Password",
        "ref": "e2",
        "value": "{{secret.AI_TEST_USERNAME}}",
    })
    assert not validator({
        "action": "TYPE",
        "reason": "禁止明文",
        "element": "Password",
        "ref": "e2",
        "value": "guessed-password",
    })


def test_exploration_decision_fills_element_from_current_snapshot_ref() -> None:
    exploration = SimpleNamespace(
        allowed_origins=["https://example.test"],
        use_login_credentials=True,
    )
    value = {
        "action": "TYPE",
        "reason": "填写用户名",
        "ref": "e24",
        "value": "{{secret.AI_TEST_USERNAME}}",
    }
    validator = _decision_validator(
        exploration,
        {"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
        accessibility_snapshot=(
            '- textbox "用户名" [ref=e24]\n'
            '- textbox "密码" [ref=e25]\n'
            '- button "登录" [ref=e26]'
        ),
    )

    assert validator(value)
    assert value["element"] == 'textbox "用户名"'
    assert not validator({
        "action": "CLICK",
        "reason": "引用不存在",
        "ref": "missing",
    })


def test_exploration_decision_recovers_ref_misplaced_in_element() -> None:
    exploration = SimpleNamespace(
        allowed_origins=["https://example.test"],
        use_login_credentials=False,
    )
    snapshot = (
        '- spinbutton "数量" [ref=f1e91]\n'
        '- button "创建订单" [ref=f1e92]'
    )
    validator = _decision_validator(
        exploration,
        set(),
        accessibility_snapshot=snapshot,
    )
    value = {
        "action": "CLICK",
        "element": 'button "创建订单" [ref=f1e92]',
        "reason": "提交数量 21，验证边界校验",
    }

    assert validator(value)
    assert value["ref"] == "f1e92"
    assert value["element"] == 'button "创建订单"'

    compact_value = {
        "action": "CLICK",
        "element": "f1e92",
        "reason": "点击创建订单",
    }
    assert validator(compact_value)
    assert compact_value["ref"] == "f1e92"

    assert not validator({
        "action": "CLICK",
        "element": 'button "伪造目标" [ref=missing]',
        "reason": "引用不在当前快照",
    })


def test_exploration_cannot_finish_while_managed_login_form_is_untested() -> None:
    exploration = SimpleNamespace(
        allowed_origins=["https://example.test"],
        use_login_credentials=True,
    )
    snapshot = (
        '- textbox "用户名" [ref=e1]\n'
        '- textbox "密码" [ref=e2]\n'
        '- button "登录" [ref=e3]\n'
        '- paragraph: 不要输入真实凭据'
    )
    finish = {
        "action": "FINISH",
        "reason": "页面提示不要输入真实凭据",
    }

    validator = _decision_validator(
        exploration,
        {"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
        accessibility_snapshot=snapshot,
        history=[],
    )
    assert not validator(finish)

    completed_validator = _decision_validator(
        exploration,
        {"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
        accessibility_snapshot=snapshot,
        history=[
            {
                "action": "TYPE",
                "element": "用户名",
                "value": "{{secret.AI_TEST_USERNAME}}",
            },
            {
                "action": "TYPE",
                "element": "密码",
                "value": "{{secret.AI_TEST_PASSWORD}}",
            },
            {"action": "CLICK", "element": "登录"},
        ],
    )
    assert completed_validator(finish)


def test_running_exploration_can_reload_plan_after_transport_redelivery(monkeypatch) -> None:
    exploration = SimpleNamespace(
        id="exploration-1",
        status="RUNNING",
        claimed_runner_id="runner-1",
        claimed_at=object(),
        plan_item_id=12,
        use_login_credentials=False,
        project_id=7,
        environment_id=None,
        session_profile_id=None,
        start_url="https://example.test",
        allowed_origins=["https://example.test"],
        max_steps=20,
        headless=False,
    )
    runner = SimpleNamespace(id="runner-1")
    item = SimpleNamespace(
        objective="验证商品列表",
        planned_steps=["打开页面"],
        expected_outcomes=["列表可见"],
    )
    monkeypatch.setattr(
        web_design_service,
        "_runner_context",
        lambda *_args, **_kwargs: (exploration, runner),
    )
    monkeypatch.setattr(web_design_service, "_get_item", lambda *_args: item)

    plan = get_exploration_execution_plan(
        SimpleNamespace(), "exploration-1", "credential", "message-1"
    )

    assert plan.exploration_id == "exploration-1"
    assert plan.runner_id == "runner-1"
    assert plan.headless is False


def test_revision_snapshot_can_use_a_separate_bounded_limit() -> None:
    source = {"observation": "x" * 1_000_000}
    with pytest.raises(ResourceConflictError, match="1MB"):
        _json_snapshot(source, label="规划")
    _, digest, size = _json_snapshot(source, label="校准", max_bytes=2_000_000)
    assert len(digest) == 64
    assert size > 1_000_000


def test_reconcile_result_cannot_invent_managed_secret_names() -> None:
    value = {
        "suggested_name": "登录流程",
        "summary": "使用项目测试账号登录",
        "coverage_notes": [],
        "unresolved_gaps": [],
        "content": {
            "start_url": "https://example.test/login",
            "actions": [
                {
                    "type": "FILL",
                    "locator": {"strategy": "test_id", "value": "username"},
                    "value": "{{secret.AI_TEST_USERNAME}}",
                }
            ],
        },
    }

    validator = _reconcile_validator({"AI_TEST_USERNAME", "AI_TEST_PASSWORD"})
    assert validator(value)
    value["content"]["actions"][0]["value"] = "{{secret.DEMO_USERNAME}}"
    assert not validator(value)
