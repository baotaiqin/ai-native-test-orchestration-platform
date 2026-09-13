import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.modules.ai_gateway.schemas import AiGenerateResponse
from app.modules.prompt_center.models import OutputSchema, PromptDefinition, PromptVersion
from app.modules.scenarios.executor import execute_preview
from app.modules.scenarios.schemas import ScenarioDsl, ScenarioExecutionPreviewRequest
from app.modules.test_cases.assertions import (
    run_assertion,
    sanitize_response_snapshot,
    summarize_final_status,
)
from app.modules.test_cases.runtime import preview_runtime
from app.modules.test_cases.schemas import (
    ApiRequestTemplate,
    Assertion,
    AssertionStatus,
    HttpMethod,
    RequestBody,
    RuntimePreviewRequest,
    RuntimeResponseSnapshot,
    SuggestedCase,
)
from app.modules.test_cases.service import _validate_assertions


def _snapshot() -> RuntimeResponseSnapshot:
    return RuntimeResponseSnapshot(
        status_code=201,
        json_body={"id": 7, "active": True, "items": [{"id": 1}], "bad": "x"},
        text="created order",
        headers={"Content-Type": "application/json", "X-Trace": "abc"},
        cookies={"SESSION": "cookie-value"},
        response_time_ms=42,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "STATUS_CODE", "source": "STATUS_CODE", "operator": "EQ", "expected": 201},
        {
            "type": "JSONPATH_EQUAL",
            "source": "JSON_BODY",
            "expression": "$.id",
            "operator": "EQ",
            "expected": 7,
        },
        {
            "type": "CONTAINS",
            "source": "RESPONSE_TEXT",
            "operator": "CONTAINS",
            "expected": "order",
        },
        {
            "type": "REGEX",
            "source": "RESPONSE_TEXT",
            "operator": "EQ",
            "expected": r"^created\s+order$",
        },
        {
            "type": "HEADER",
            "source": "HEADER",
            "expression": "content-type",
            "operator": "EQ",
            "expected": "application/json",
        },
        {
            "type": "COOKIE",
            "source": "COOKIE",
            "expression": "session",
            "operator": "EQ",
            "expected": "cookie-value",
        },
        {
            "type": "JSON_SCHEMA",
            "source": "JSON_BODY",
            "operator": "EQ",
            "expected": {"type": "object", "required": ["id"]},
        },
        {"type": "RESPONSE_TIME", "source": "RESPONSE_TIME", "operator": "LTE", "expected": 50},
        {"type": "EXISTS", "source": "JSONPATH", "expression": "$.id"},
        {"type": "NOT_EXISTS", "source": "JSONPATH", "expression": "$.missing"},
        {
            "type": "ARRAY_LENGTH",
            "source": "JSONPATH",
            "expression": "$.items",
            "operator": "EQ",
            "expected": 1,
        },
        {
            "type": "TYPE",
            "source": "JSONPATH",
            "expression": "$.id",
            "operator": "EQ",
            "expected": "integer",
        },
    ],
)
def test_all_deterministic_assertions_pass(payload: dict) -> None:
    result = run_assertion(Assertion(name="assertion", enabled=True, **payload), 1, _snapshot())
    assert result.status == AssertionStatus.PASS
    assert result.sequence == 1
    assert result.duration_ms >= 0


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "STATUS_CODE", "source": "STATUS_CODE", "operator": "EQ", "expected": 200},
        {
            "type": "JSONPATH_EQUAL",
            "source": "JSON_BODY",
            "expression": "$.missing",
            "operator": "EQ",
            "expected": 7,
        },
        {
            "type": "CONTAINS",
            "source": "RESPONSE_TEXT",
            "operator": "CONTAINS",
            "expected": "absent",
        },
        {"type": "REGEX", "source": "RESPONSE_TEXT", "operator": "EQ", "expected": r"^failed$"},
        {
            "type": "HEADER",
            "source": "HEADER",
            "expression": "x-trace",
            "operator": "EQ",
            "expected": "other",
        },
        {
            "type": "COOKIE",
            "source": "COOKIE",
            "expression": "unknown",
            "operator": "EQ",
            "expected": "x",
        },
        {
            "type": "JSON_SCHEMA",
            "source": "JSON_BODY",
            "operator": "EQ",
            "expected": {"type": "object", "required": ["id", "missing"]},
        },
        {"type": "RESPONSE_TIME", "source": "RESPONSE_TIME", "operator": "GT", "expected": 50},
        {"type": "EXISTS", "source": "JSONPATH", "expression": "$.missing"},
        {"type": "NOT_EXISTS", "source": "JSONPATH", "expression": "$.id"},
        {
            "type": "ARRAY_LENGTH",
            "source": "JSONPATH",
            "expression": "$.items",
            "operator": "EQ",
            "expected": 2,
        },
        {
            "type": "TYPE",
            "source": "JSONPATH",
            "expression": "$.active",
            "operator": "EQ",
            "expected": "integer",
        },
    ],
)
def test_all_deterministic_assertions_fail_without_500(payload: dict) -> None:
    result = run_assertion(Assertion(name="assertion", enabled=True, **payload), 1, _snapshot())
    assert result.status == AssertionStatus.FAIL
    assert result.message


def test_assertion_dsl_rejects_type_mismatch_duplicate_and_unsafe_regex() -> None:
    with pytest.raises(ValidationError, match="boolean"):
        Assertion(
            name="status", type="STATUS_CODE", source="STATUS_CODE", operator="EQ", expected=True
        )
    with pytest.raises(ValidationError, match="ReDoS"):
        Assertion(
            name="regex", type="REGEX", source="RESPONSE_TEXT", operator="EQ", expected=r"(a+)+$"
        )
    with pytest.raises(ValidationError, match="断言名称不能重复"):
        SuggestedCase(
            title="API",
            case_type="API",
            priority="P1",
            steps=[{"order": 1, "action": "a", "expected": "b"}],
            expected_result="ok",
            assertions=[
                {
                    "name": "same",
                    "type": "STATUS_CODE",
                    "source": "STATUS_CODE",
                    "operator": "EQ",
                    "expected": 200,
                },
                {
                    "name": "same",
                    "type": "STATUS_CODE",
                    "source": "STATUS_CODE",
                    "operator": "EQ",
                    "expected": 200,
                },
            ],
        )


def test_disabled_and_final_status_rules() -> None:
    disabled = Assertion(
        name="disabled",
        type="STATUS_CODE",
        source="STATUS_CODE",
        operator="EQ",
        expected=500,
        enabled=False,
    )
    passed = Assertion(
        name="passed", type="STATUS_CODE", source="STATUS_CODE", operator="EQ", expected=201
    )
    failed = Assertion(
        name="failed", type="STATUS_CODE", source="STATUS_CODE", operator="EQ", expected=500
    )
    disabled_result = run_assertion(disabled, 1, _snapshot())
    pass_result = run_assertion(passed, 2, _snapshot())
    fail_result = run_assertion(failed, 3, _snapshot())
    assert disabled_result.status == AssertionStatus.SKIPPED
    assert summarize_final_status([disabled_result]) == "PASS"
    assert summarize_final_status([pass_result, fail_result]) == "FAIL"


def _ai_result(parsed: object) -> AiGenerateResponse:
    return AiGenerateResponse(
        ai_call_id=91,
        success=True,
        content="{}",
        parsed_result=parsed,
        actual_model="assertion-model",
        fallback_used=True,
        repair_used=True,
        input_token=10,
        output_token=5,
        total_token=15,
        estimated_cost=Decimal("0.001"),
        latency_ms=12,
        response_id="resp-91",
    )


def _ai_assertion() -> Assertion:
    return Assertion(
        kind="AI_SEMANTIC",
        type="AI_SEMANTIC",
        name="semantic",
        enabled=True,
        prompt_id=9,
        criteria="响应必须说明订单已创建",
        confidence_threshold=0.8,
    )


def test_ai_high_confidence_metadata_and_sensitive_input(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_generate(_session, _user, payload):
        captured.update(payload.variables)
        return _ai_result({"passed": True, "confidence": 0.95, "reason": "明确说明已创建"})

    monkeypatch.setattr("app.modules.test_cases.assertions.generate", fake_generate)
    snapshot = RuntimeResponseSnapshot(
        status_code=200,
        json_body={"message": "created", "token": "do-not-send"},
        headers={"Authorization": "Bearer do-not-send"},
        cookies={"sid": "do-not-send"},
    )
    result = run_assertion(
        _ai_assertion(), 1, snapshot, project_id=1, session=object(), user=object()
    )
    assert result.status == AssertionStatus.PASS
    assert result.confidence == 0.95
    assert result.ai_call_id == 91
    assert result.actual_model == "assertion-model"
    assert result.fallback_used is True and result.repair_used is True
    serialized = str(captured)
    assert "do-not-send" not in serialized
    assert "<redacted>" in serialized


def test_ai_low_confidence_review_and_ai_only_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result({"passed": False, "confidence": 0.4, "reason": "证据不足"}),
    )
    result = run_assertion(
        _ai_assertion(), 1, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert result.status == AssertionStatus.REVIEW
    assert summarize_final_status([result]) == "REVIEW"

    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result({"passed": False, "confidence": 0.95, "reason": "明确失败"}),
    )
    negative = run_assertion(
        _ai_assertion(), 1, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert negative.status == AssertionStatus.FAIL
    assert summarize_final_status([negative]) == "FAIL"


def test_ai_type_is_normalized_through_run_and_final_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result({"passed": True, "confidence": 0.4, "reason": "证据不足"}),
    )
    assertion = Assertion(
        kind="AI_SEMANTIC",
        type=" aI_sEmAnTiC ",
        name=" semantic ",
        prompt_id=9,
        criteria="响应语义符合要求",
    )
    result = run_assertion(
        assertion, 1, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert assertion.type == "AI_SEMANTIC"
    assert assertion.name == "semantic"
    assert result.type == "AI_SEMANTIC"
    assert result.status == AssertionStatus.REVIEW
    assert summarize_final_status([result]) == "REVIEW"


def test_ai_zero_confidence_threshold_is_not_replaced_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result({"passed": True, "confidence": 0.1, "reason": "可通过"}),
    )
    assertion = Assertion(
        kind="AI_SEMANTIC",
        type="AI_SEMANTIC",
        name="threshold-zero",
        prompt_id=9,
        criteria="响应语义符合要求",
        confidence_threshold=0,
    )
    result = run_assertion(
        assertion, 1, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert assertion.confidence_threshold == 0
    assert result.status == AssertionStatus.PASS
    assert summarize_final_status([result]) == "PASS"


@pytest.mark.parametrize(
    "pattern",
    [r"(a|aa)+$", r"(a?)+$", r"(?=a)a", r"(a)\1", r"(?(1)a|b)"],
)
def test_regex_rejects_backtracking_and_unsafe_extensions(pattern: str) -> None:
    with pytest.raises(ValidationError, match="ReDoS"):
        Assertion(
            name="unsafe",
            type="REGEX",
            source="RESPONSE_TEXT",
            operator="EQ",
            expected=pattern,
        )


def test_safe_regex_passes_and_long_actual_is_rejected_before_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = Assertion(
        name="safe",
        type="REGEX",
        source="RESPONSE_TEXT",
        operator="EQ",
        expected=r"^user_[A-Za-z0-9_-]{1,32}$",
    )
    safe_result = run_assertion(
        safe,
        1,
        RuntimeResponseSnapshot(status_code=200, text="user_abc", headers={}, cookies={}),
    )
    assert safe_result.status == AssertionStatus.PASS

    long_actual = Assertion(
        name="long",
        type="REGEX",
        source="RESPONSE_TEXT",
        operator="EQ",
        expected=r"^a+$",
    )
    import app.modules.test_cases.assertions as assertions_impl

    def forbidden_search(*_args, **_kwargs):
        raise AssertionError("re.search must not run for an overlong value")

    monkeypatch.setattr(assertions_impl.re, "search", forbidden_search)
    result = run_assertion(
        long_actual,
        1,
        RuntimeResponseSnapshot(status_code=200, text="a" * 20_001, headers={}, cookies={}),
    )
    assert result.status == AssertionStatus.FAIL
    assert "20000" in result.message


def test_json_schema_pattern_cannot_bypass_safe_regex_validation() -> None:
    with pytest.raises(ValidationError, match="ReDoS"):
        Assertion(
            name="schema-pattern",
            type="JSON_SCHEMA",
            source="JSON_BODY",
            operator="EQ",
            expected={"type": "object", "properties": {"name": {"pattern": r"(a|aa)+$"}}},
        )


def test_ai_and_deterministic_dsl_fields_cannot_be_mixed() -> None:
    with pytest.raises(ValidationError, match="source/expression/operator/expected"):
        Assertion(
            kind="AI_SEMANTIC",
            type="AI_SEMANTIC",
            name="mixed-ai",
            source="JSON_BODY",
            prompt_id=9,
            criteria="符合要求",
        )
    with pytest.raises(ValidationError, match="确定性断言不能配置 AI 字段"):
        Assertion(
            type="STATUS_CODE",
            name="mixed-deterministic",
            source="STATUS_CODE",
            operator="EQ",
            expected=200,
            prompt_id=9,
        )


def test_response_time_aliases_reject_conflict_and_normalize_both_directions() -> None:
    with pytest.raises(ValidationError, match="必须一致"):
        RuntimeResponseSnapshot(
            status_code=200,
            headers={},
            cookies={},
            elapsed_ms=10,
            response_time_ms=11,
        )
    from_elapsed = RuntimeResponseSnapshot(
        status_code=200, headers={}, cookies={}, elapsed_ms=10
    )
    from_response_time = RuntimeResponseSnapshot(
        status_code=200, headers={}, cookies={}, response_time_ms=11
    )
    assert from_elapsed.elapsed_ms == from_elapsed.response_time_ms == 10
    assert from_response_time.elapsed_ms == from_response_time.response_time_ms == 11


def test_assertion_result_serialization_redacts_schema_and_ai_sensitive_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_result = run_assertion(
        Assertion(
            name="schema-secret",
            type="JSON_SCHEMA",
            source="JSON_BODY",
            operator="EQ",
            expected={
                "type": "object",
                "properties": {"token": {"type": "string", "pattern": "^safe$"}},
            },
        ),
        1,
        RuntimeResponseSnapshot(
            status_code=200,
            json_body={"token": "password=top-secret"},
            headers={"Authorization": "Bearer header-secret"},
            cookies={"sid": "cookie-secret"},
        ),
    )
    schema_serialized = json.dumps(schema_result.model_dump(mode="json"), ensure_ascii=False)
    for secret in ("top-secret", "header-secret", "cookie-secret"):
        assert secret not in schema_serialized

    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result(
            {
                "passed": False,
                "confidence": 0.95,
                "reason": "Authorization: Bearer model-secret password=reason-secret "
                "Cookie: cookie-reason-secret",
            }
        ),
    )
    ai_result = run_assertion(
        _ai_assertion(),
        2,
        _snapshot(),
        project_id=1,
        session=object(),
        user=object(),
    )
    ai_serialized = json.dumps(ai_result.model_dump(mode="json"), ensure_ascii=False)
    for secret in ("model-secret", "reason-secret", "cookie-reason-secret"):
        assert secret not in ai_serialized
    assert ai_result.reason == (
        "Authorization: Bearer <redacted> password=<redacted> Cookie: <redacted>"
    )


def test_ai_conflict_is_review_and_gateway_error_is_traceable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.test_cases.assertions.generate",
        lambda *_: _ai_result({"passed": False, "confidence": 0.95, "reason": "语义失败"}),
    )
    deterministic = run_assertion(
        Assertion(
            name="status", type="STATUS_CODE", source="STATUS_CODE", operator="EQ", expected=201
        ),
        1,
        _snapshot(),
    )
    ai = run_assertion(
        _ai_assertion(), 2, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert summarize_final_status([deterministic, ai]) == "REVIEW"

    def fail_generate(*_):
        raise AppError("MODEL_PROVIDER_ERROR", "模型服务不可用", details={"ai_call_id": 123})

    monkeypatch.setattr("app.modules.test_cases.assertions.generate", fail_generate)
    error = run_assertion(
        _ai_assertion(), 1, _snapshot(), project_id=1, session=object(), user=object()
    )
    assert error.status == AssertionStatus.REVIEW
    assert error.ai_call_id == 123
    assert "AI Gateway" in error.message


def test_sanitizer_redacts_nested_credentials_and_truncates() -> None:
    snapshot = RuntimeResponseSnapshot(
        json_body={"nested": {"password": "secret-value", "safe": "x"}, "large": "a" * 3000},
        headers={"Authorization": "Bearer x", "X": "ok"},
        cookies={"sid": "x"},
    )
    sanitized = sanitize_response_snapshot(snapshot)
    serialized = str(sanitized)
    assert (
        "secret-value" not in serialized
        and "Bearer x" not in serialized
        and "'sid': 'x'" not in serialized
    )
    assert "<truncated>" in serialized


def test_runtime_preview_returns_ordered_assertions_and_legacy_time_compatibility() -> None:
    request = ApiRequestTemplate(
        method=HttpMethod.GET,
        url="https://example.test/orders",
        body=RequestBody(),
    )
    payload = RuntimePreviewRequest(
        request=request,
        response=RuntimeResponseSnapshot(
            status_code=200, json_body={"ok": True}, headers={}, cookies={}, elapsed_ms=18
        ),
        assertions=[
            Assertion(
                name="status", type="STATUS_CODE", source="STATUS_CODE", operator="EQ", expected=200
            ),
            Assertion(
                name="disabled",
                type="STATUS_CODE",
                source="STATUS_CODE",
                operator="EQ",
                expected=500,
                enabled=False,
            ),
            Assertion(
                name="missing",
                type="JSONPATH_EQUAL",
                source="JSON_BODY",
                expression="$.id",
                operator="EQ",
                expected=1,
            ),
        ],
    )
    result = preview_runtime(payload)
    assert [item.sequence for item in result.assertion_results] == [1, 2, 3]
    assert [item.status for item in result.assertion_results] == [
        AssertionStatus.PASS,
        AssertionStatus.SKIPPED,
        AssertionStatus.FAIL,
    ]
    assert result.final_status == "FAIL"
    assert payload.response.response_time_ms == 18


def test_formal_case_entry_validates_ai_prompt_task_and_output_schema() -> None:
    prompt = SimpleNamespace(enabled=True, task_type="AI_ASSERTION", current_version_id=4)
    version = SimpleNamespace(output_schema_id=5)
    schema = SimpleNamespace(
        enabled=True,
        schema_json={
            "type": "object",
            "required": ["passed", "confidence", "reason"],
            "properties": {
                "passed": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
        },
    )

    class FakeSession:
        def scalar(self, _statement):
            return object()

        def get(self, model, identifier):
            if model is PromptDefinition:
                return prompt if identifier == 9 else None
            if model is PromptVersion:
                return version if identifier == 4 else None
            if model is OutputSchema:
                return schema if identifier == 5 else None
            return None

    content = SuggestedCase(
        title="AI API",
        case_type="API",
        priority="P1",
        steps=[{"order": 1, "action": "request", "expected": "created"}],
        expected_result="created",
        assertions=[
            {
                "kind": "AI_SEMANTIC",
                "type": "AI_SEMANTIC",
                "name": "semantic",
                "prompt_id": 9,
                "criteria": "说明已创建",
                "confidence_threshold": 0.8,
            }
        ],
    )
    _validate_assertions(FakeSession(), 1, content)
    prompt.task_type = "API_CASE_GENERATE"
    with pytest.raises(Exception, match="AI_ASSERTION"):
        _validate_assertions(FakeSession(), 1, content)


def test_scenario_assertion_nodes_reuse_evaluator_and_trace_result() -> None:
    dsl = ScenarioDsl.model_validate(
        {
            "nodes": [
                {"id": "start", "type": "START", "name": "开始"},
                {
                    "id": "assert_status",
                    "type": "ASSERT_STATUS",
                    "name": "状态码",
                    "config": {"expected": 200},
                },
                {
                    "id": "assert_json",
                    "type": "ASSERT_JSONPATH",
                    "name": "字段",
                    "config": {"expression": "$.ok", "expected": True},
                },
                {"id": "end", "type": "END", "name": "结束"},
            ]
        }
    )
    result = execute_preview(
        ScenarioExecutionPreviewRequest(
            dsl=dsl,
            response=RuntimeResponseSnapshot(
                status_code=200, json_body={"ok": False}, headers={}, cookies={}
            ),
        ),
        object(),
        object(),
    )
    assert result.final_status == "FAIL"
    assert result.assertion_results[0].status == AssertionStatus.PASS
    assert result.assertion_results[1].status == AssertionStatus.FAIL
    assert result.traces[1].detail["assertion_result"]["name"] == "状态码"
