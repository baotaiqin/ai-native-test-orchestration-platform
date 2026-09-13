import json
import re
import time
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.auth.schemas import CurrentUser
from app.modules.prompt_center.models import AiCallLog
from app.modules.prompt_center.validator import parse_json_once, validate_json_schema
from app.modules.test_cases.safe_regex import safe_regex_errors, schema_pattern_errors
from app.modules.test_cases.schemas import (
    Assertion,
    AssertionKind,
    AssertionOperator,
    AssertionResult,
    AssertionSource,
    AssertionStatus,
    DeterministicAssertionType,
    RuntimeResponseSnapshot,
)

_PATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\[['\"]([^'\"]+)['\"]\]")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|token|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(bearer\s+|(?:authorization|cookie|token|password|secret|api[_-]?key)\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"
)
_MAX_VALUE_LENGTH = 2000
_MAX_DEPTH = 6
_MAX_REGEX_INPUT_LENGTH = 20_000


class AiAssertionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2000)


class AssertionEvaluationError(Exception):
    pass


def _jsonpath_get(document: Any, expression: str) -> Any:
    if expression == "$":
        return document
    if not expression.startswith("$"):
        raise AssertionEvaluationError(f"JSONPath 必须以 $ 开始：{expression}")
    position = 1
    current = document
    while position < len(expression):
        match = _PATH_TOKEN.match(expression, position)
        if match is None:
            raise AssertionEvaluationError(f"不支持的 JSONPath：{expression}")
        key, index, quoted_key = match.groups()
        try:
            current = current[int(index)] if index is not None else current[key or quoted_key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise KeyError(expression) from exc
        position = match.end()
    return current


def _case_insensitive_get(values: dict[str, str], name: str) -> str:
    for key, value in values.items():
        if key.lower() == name.lower():
            return value
    raise KeyError(name)


def _safe_value(value: Any, *, depth: int = 0, redact_all: bool = False) -> Any:
    """Keep assertion output and model input bounded and free of credential-like values."""
    if redact_all:
        return "<redacted>"
    if depth >= _MAX_DEPTH:
        return "<truncated>"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 80:
                output["<truncated>"] = "<truncated>"
                break
            key_text = str(key)
            child_redacted = key_text.lower() == "cookie" or bool(_SENSITIVE_KEY.search(key_text))
            output[key_text] = _safe_value(child, depth=depth + 1, redact_all=child_redacted)
        return output
    if isinstance(value, (list, tuple)):
        items = [_safe_value(item, depth=depth + 1) for item in value[:80]]
        if len(value) > 80:
            items.append("<truncated>")
        return items
    if isinstance(value, str):
        value = _SENSITIVE_TEXT.sub(r"\1<redacted>", value)
        if len(value) > _MAX_VALUE_LENGTH:
            return f"{value[:_MAX_VALUE_LENGTH]}…<truncated>"
    return value


def _safe_text(value: Any) -> str:
    """Make untrusted assertion text safe for API results, traces and logs."""

    sanitized = _safe_value(value)
    if isinstance(sanitized, str):
        return sanitized[:_MAX_VALUE_LENGTH]
    return json.dumps(sanitized, ensure_ascii=False, default=str)[:_MAX_VALUE_LENGTH]


def sanitize_response_snapshot(snapshot: RuntimeResponseSnapshot) -> dict[str, Any]:
    return _safe_value(
        {
            "status_code": snapshot.status_code,
            "json_body": snapshot.json_body,
            "text": snapshot.text,
            "headers": snapshot.headers,
            # Cookie values are not needed to judge semantics and are always secret-like.
            "cookies": {key: "<redacted>" for key in snapshot.cookies},
            "response_time_ms": snapshot.response_time_ms,
            "elapsed_ms": snapshot.elapsed_ms,
        }
    )


def _strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    return left == right


def _display_value(assertion: Assertion, value: Any) -> Any:
    expression = assertion.expression or ""
    sensitive_target = assertion.source == AssertionSource.COOKIE or bool(
        _SENSITIVE_KEY.search(expression)
    )
    return _safe_value(value, redact_all=sensitive_target)


def _compare(left: Any, operator: AssertionOperator, right: Any) -> bool:
    if operator == AssertionOperator.EQ:
        return _strict_equal(left, right)
    if operator == AssertionOperator.CONTAINS:
        if not isinstance(left, str) or not isinstance(right, str):
            raise AssertionEvaluationError("Contains 只支持字符串实际值和 expected")
        return right in left
    if type(left) is not type(right) or isinstance(left, bool):
        raise AssertionEvaluationError("比较操作两侧类型不匹配")
    try:
        if operator == AssertionOperator.LT:
            return left < right
        if operator == AssertionOperator.LTE:
            return left <= right
        if operator == AssertionOperator.GT:
            return left > right
        if operator == AssertionOperator.GTE:
            return left >= right
    except TypeError as exc:
        raise AssertionEvaluationError("比较操作两侧类型不支持比较") from exc
    raise AssertionEvaluationError(f"不支持的 operator：{operator}")


def _source_value(assertion: Assertion, snapshot: RuntimeResponseSnapshot) -> Any:
    source = assertion.source
    if source == AssertionSource.STATUS_CODE:
        return snapshot.status_code
    if source == AssertionSource.JSON_BODY:
        if assertion.expression:
            return _jsonpath_get(snapshot.json_body, assertion.expression)
        return snapshot.json_body
    if source == AssertionSource.RESPONSE_TEXT:
        if snapshot.text is not None:
            return snapshot.text
        if isinstance(snapshot.json_body, str):
            return snapshot.json_body
        return json.dumps(snapshot.json_body, ensure_ascii=False, separators=(",", ":"))
    if source == AssertionSource.RESPONSE_TIME:
        return snapshot.response_time_ms
    if source == AssertionSource.JSONPATH:
        return _jsonpath_get(snapshot.json_body, assertion.expression or "")
    if source == AssertionSource.HEADER:
        return _case_insensitive_get(snapshot.headers, assertion.expression or "")
    if source == AssertionSource.COOKIE:
        return _case_insensitive_get(snapshot.cookies, assertion.expression or "")
    raise AssertionEvaluationError("断言 source 未配置")


def _schema_definition_errors(schema: Any, path: str = "$") -> list[str]:
    if not isinstance(schema, dict):
        return [f"{path}: Schema 必须是对象"]
    errors: list[str] = []
    errors.extend(schema_pattern_errors(schema, path))
    schema_type = schema.get("type")
    allowed_types = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if schema_type is not None:
        values = schema_type if isinstance(schema_type, list) else [schema_type]
        if not values or any(item not in allowed_types for item in values):
            errors.append(f"{path}.type: 不支持的 JSON Schema 类型")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or any(not isinstance(item, str) for item in required)
    ):
        errors.append(f"{path}.required: 必须是字符串数组")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties: 必须是对象")
        else:
            for key, child in properties.items():
                errors.extend(_schema_definition_errors(child, f"{path}.properties.{key}"))
    items = schema.get("items")
    if items is not None:
        errors.extend(_schema_definition_errors(items, f"{path}.items"))
    return errors


def _schema_contains_pattern(schema: Any) -> bool:
    if isinstance(schema, dict):
        if "pattern" in schema:
            return True
        return any(_schema_contains_pattern(child) for child in schema.values())
    if isinstance(schema, list):
        return any(_schema_contains_pattern(child) for child in schema)
    return False


def _reject_overlong_strings(value: Any, *, path: str = "$") -> None:
    if isinstance(value, str):
        if len(value) > _MAX_REGEX_INPUT_LENGTH:
            raise AssertionEvaluationError(
                f"Regex 实际值在 {path} 超过 {_MAX_REGEX_INPUT_LENGTH} 字符，已拒绝执行"
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_overlong_strings(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_overlong_strings(child, path=f"{path}[{index}]")


def evaluate_deterministic_assertion(
    assertion: Assertion, snapshot: RuntimeResponseSnapshot
) -> tuple[bool, Any, str]:
    assertion_type = assertion.type.upper()
    if assertion_type == DeterministicAssertionType.STATUS_CODE.value:
        actual = _source_value(assertion, snapshot)
        return (
            _strict_equal(actual, assertion.expected),
            actual,
            (f"状态码实际为 {actual}，期望 {assertion.expected}"),
        )

    if assertion_type == DeterministicAssertionType.JSON_SCHEMA.value:
        schema_errors = _schema_definition_errors(assertion.expected)
        if schema_errors:
            raise AssertionEvaluationError("JSON Schema 定义无效：" + "; ".join(schema_errors[:5]))
        actual = _source_value(assertion, snapshot)
        if _schema_contains_pattern(assertion.expected):
            _reject_overlong_strings(actual)
        errors = validate_json_schema(actual, assertion.expected)
        if errors:
            return False, actual, "JSON Schema 校验失败：" + "; ".join(errors[:5])
        return True, actual, "JSON Schema 校验通过"

    if assertion_type in {
        DeterministicAssertionType.EXISTS.value,
        DeterministicAssertionType.NOT_EXISTS.value,
    }:
        try:
            actual = _source_value(assertion, snapshot)
            exists = True
        except KeyError:
            actual = None
            exists = False
        passed = exists if assertion_type == DeterministicAssertionType.EXISTS.value else not exists
        return passed, actual, "路径存在" if exists else "路径不存在"

    actual = _source_value(assertion, snapshot)
    if assertion_type == DeterministicAssertionType.ARRAY_LENGTH.value:
        if not isinstance(actual, list):
            raise AssertionEvaluationError("Array Length 实际值不是数组")
        actual = len(actual)
    elif assertion_type == DeterministicAssertionType.TYPE.value:
        actual = (
            "null"
            if actual is None
            else "boolean"
            if isinstance(actual, bool)
            else "integer"
            if type(actual) is int
            else "number"
            if isinstance(actual, float)
            else "string"
            if isinstance(actual, str)
            else "array"
            if isinstance(actual, list)
            else "object"
            if isinstance(actual, dict)
            else type(actual).__name__
        )
    elif assertion_type == DeterministicAssertionType.REGEX.value:
        if not isinstance(actual, str):
            raise AssertionEvaluationError("Regex 实际值不是字符串")
        regex_errors = safe_regex_errors(assertion.expected)
        if regex_errors:
            raise AssertionEvaluationError(
                "Regex 模式过于复杂，已拒绝潜在 ReDoS：" + "; ".join(regex_errors[:3])
            )
        if len(actual) > _MAX_REGEX_INPUT_LENGTH:
            raise AssertionEvaluationError(
                f"Regex 实际值超过 {_MAX_REGEX_INPUT_LENGTH} 字符，已拒绝执行"
            )
        try:
            matched = re.search(assertion.expected, actual, flags=re.ASCII) is not None
        except re.error as exc:
            raise AssertionEvaluationError(f"Regex 执行失败：{exc.msg}") from exc
        return matched, actual, "Regex 匹配" if matched else "Regex 未匹配"

    passed = _compare(actual, assertion.operator or AssertionOperator.EQ, assertion.expected)
    display_actual = _display_value(assertion, actual)
    display_expected = _display_value(assertion, assertion.expected)
    return (
        passed,
        actual,
        "断言通过" if passed else f"实际值为 {display_actual!r}，期望 {display_expected!r}",
    )


def _call_log_metadata(session: Session | None, ai_call_id: int | None) -> dict[str, Any]:
    if session is None or ai_call_id is None:
        return {}
    get = getattr(session, "get", None)
    if not callable(get):
        return {}
    log = get(AiCallLog, ai_call_id)
    if log is None:
        return {}
    return {
        "ai_call_id": log.id,
        "actual_model": log.actual_model,
        "fallback_used": log.fallback_used,
        "repair_used": log.repair_used,
    }


def evaluate_ai_assertion(
    assertion: Assertion,
    snapshot: RuntimeResponseSnapshot,
    *,
    project_id: int | None,
    session: Session | None,
    user: CurrentUser | None,
    commit: bool = True,
) -> tuple[AssertionStatus, Any, str, dict[str, Any]]:
    if project_id is None or session is None or user is None:
        return AssertionStatus.REVIEW, None, "AI 断言缺少项目权限上下文", {}
    variables = {
        "response_snapshot": sanitize_response_snapshot(snapshot),
        "criteria": _safe_value(assertion.criteria),
    }
    try:
        request = AiGenerateRequest(
            project_id=project_id,
            task_type="AI_ASSERTION",
            prompt_id=assertion.prompt_id or 0,
            variables=variables,
            entity_type="TEST_CASE_ASSERTION",
            entity_id=assertion.name,
        )
        ai_result = (
            generate(session, user, request)
            if commit
            else generate(session, user, request, commit=False)
        )
    except AppError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        call_id = details.get("ai_call_id")
        metadata = _call_log_metadata(session, call_id)
        if call_id is not None:
            metadata["ai_call_id"] = call_id
        return (
            AssertionStatus.REVIEW,
            None,
            _safe_text("AI Gateway 调用失败：" + exc.message),
            metadata,
        )

    parsed = ai_result.parsed_result
    if parsed is None or isinstance(parsed, str):
        parsed, parse_error = parse_json_once(parsed or ai_result.content)
        if parse_error:
            metadata = {
                "ai_call_id": ai_result.ai_call_id,
                "actual_model": ai_result.actual_model,
                "fallback_used": ai_result.fallback_used,
                "repair_used": ai_result.repair_used,
            }
            return (
                AssertionStatus.REVIEW,
                None,
                _safe_text("AI 输出不是有效 JSON：" + parse_error),
                metadata,
            )
    try:
        output = AiAssertionOutput.model_validate(parsed)
    except ValidationError as exc:
        metadata = {
            "ai_call_id": ai_result.ai_call_id,
            "actual_model": ai_result.actual_model,
            "fallback_used": ai_result.fallback_used,
            "repair_used": ai_result.repair_used,
        }
        return (
            AssertionStatus.REVIEW,
            None,
            _safe_text("AI 输出结构无效：" + "; ".join(item["msg"] for item in exc.errors()[:3])),
            metadata,
        )

    metadata = {
        "ai_call_id": ai_result.ai_call_id,
        "actual_model": ai_result.actual_model,
        "fallback_used": ai_result.fallback_used,
        "repair_used": ai_result.repair_used,
    }
    threshold = (
        assertion.confidence_threshold
        if assertion.confidence_threshold is not None
        else 0.8
    )
    safe_reason = _safe_text(output.reason)
    if output.confidence < threshold:
        return (
            AssertionStatus.REVIEW,
            output.passed,
            (f"AI 置信度 {output.confidence:.2f} 低于阈值 {threshold:.2f}"),
            {**metadata, "confidence": output.confidence, "reason": safe_reason},
        )
    status = AssertionStatus.PASS if output.passed else AssertionStatus.FAIL
    return (
        status,
        output.passed,
        safe_reason,
        {**metadata, "confidence": output.confidence, "reason": safe_reason},
    )


def run_assertion(
    assertion: Assertion,
    sequence: int,
    snapshot: RuntimeResponseSnapshot,
    *,
    project_id: int | None = None,
    session: Session | None = None,
    user: CurrentUser | None = None,
    commit: bool = True,
) -> AssertionResult:
    started = time.perf_counter()
    expected = _display_value(assertion, assertion.expected)
    if not assertion.enabled:
        return AssertionResult(
            sequence=sequence,
            name=assertion.name,
            type=assertion.type,
            status=AssertionStatus.SKIPPED,
            expected=expected,
            actual=None,
            message="断言已停用",
            duration_ms=0,
        )
    try:
        if assertion.kind == AssertionKind.AI_SEMANTIC:
            status, actual, message, metadata = evaluate_ai_assertion(
                assertion,
                snapshot,
                project_id=project_id,
                session=session,
                user=user,
                commit=commit,
            )
            return AssertionResult(
                sequence=sequence,
                name=assertion.name,
                type=assertion.type,
                status=status,
                expected=expected,
                actual=_display_value(assertion, actual),
                message=_safe_text(message),
                duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                confidence=metadata.pop("confidence", None),
                reason=(
                    _safe_text(metadata.pop("reason"))
                    if metadata.get("reason") is not None
                    else None
                ),
                **metadata,
            )
        passed, actual, message = evaluate_deterministic_assertion(assertion, snapshot)
        return AssertionResult(
            sequence=sequence,
            name=assertion.name,
            type=assertion.type,
            status=AssertionStatus.PASS if passed else AssertionStatus.FAIL,
            expected=expected,
            actual=_display_value(assertion, actual),
            message=_safe_text(message),
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )
    except KeyError:
        message = "响应中缺失目标路径或 Header/Cookie"
    except (AssertionEvaluationError, ResourceConflictError) as exc:
        message = str(exc)
    except Exception:
        message = "断言执行失败，请检查响应快照和断言配置"
    return AssertionResult(
        sequence=sequence,
        name=assertion.name,
        type=assertion.type,
        status=AssertionStatus.FAIL
        if assertion.kind == AssertionKind.DETERMINISTIC
        else AssertionStatus.REVIEW,
        expected=expected,
        actual=None,
        message=_safe_text(message),
        duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
    )


def summarize_final_status(results: list[AssertionResult]) -> str:
    active = [item for item in results if item.status != AssertionStatus.SKIPPED]
    if not active:
        return "PASS"
    typed = [(item, item.type.strip().upper()) for item in active]
    if any(item.status == AssertionStatus.FAIL and item_type != AssertionKind.AI_SEMANTIC.value
           for item, item_type in typed):
        return "FAIL"
    ai_results = [item for item, item_type in typed if item_type == AssertionKind.AI_SEMANTIC.value]
    if not ai_results:
        return "PASS"
    if any(item.status == AssertionStatus.REVIEW for item in ai_results):
        return "REVIEW"
    if any(item.status == AssertionStatus.FAIL for item in ai_results):
        return (
            "REVIEW"
            if any(
                item_type != AssertionKind.AI_SEMANTIC.value and item.status == AssertionStatus.PASS
                for item, item_type in typed
            )
            else "FAIL"
        )
    return "PASS"


def sanitized_assertion_input(snapshot: RuntimeResponseSnapshot) -> dict[str, Any]:
    return deepcopy(sanitize_response_snapshot(snapshot))
