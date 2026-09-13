"""Deterministic compiler from AI-owned test intent to executable API case DSL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.modules.api_definitions.models import ApiDefinition
from app.modules.resource_registry.schemas import CleanupAuth, CleanupConfig
from app.modules.test_cases.schemas import (
    ApiCaseExpectedClaim,
    ApiCaseInputMutation,
    ApiCaseInputStrategy,
    ApiCaseIntent,
    ApiCaseIntentScenario,
    ApiRequestTemplate,
    ApiRetryPolicy,
    ApiSetupAction,
    Assertion,
    CaseGenerationResult,
    CaseType,
    FakerAction,
    FakerGenerator,
    GetTokenAction,
    RequestAuth,
    RequestBody,
    RequestBodyType,
    RequestValueItem,
    ResponseExtractor,
    SuggestedCase,
    SuggestedStep,
)

_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_SENSITIVE_MARKERS = ("password", "passwd", "secret", "token", "api_key", "apikey")
_UNIQUE_RUNTIME_MARKERS = (
    "idempotency",
    "request_id",
    "requestid",
    "trace_id",
    "traceid",
    "flaky_key",
)
CASE_COMPILER_VERSION = "2026.09.14.1"


class CaseCompilationError(ValueError):
    """A deterministic, user-actionable contract compilation failure."""


@dataclass(frozen=True)
class CaseCompileDiagnostic:
    case_key: str
    title: str
    message: str

    def display(self) -> str:
        return f"{self.case_key}「{self.title}」：{self.message}"


@dataclass(frozen=True)
class CaseCompilationResult:
    result: CaseGenerationResult | None
    diagnostics: list[CaseCompileDiagnostic]
    compiled_case_keys: list[str]


@dataclass(frozen=True)
class _SchemaField:
    name: str
    path: str
    schema: dict[str, Any]


@dataclass
class _CompileState:
    definitions: list[ApiDefinition]
    secrets: list[dict[str, object]]
    pre_actions: list[Any] = field(default_factory=list)
    produced_names: set[str] = field(default_factory=set)
    compiling_names: set[str] = field(default_factory=set)
    bearer_reference: str | None = None
    needs_login: bool = False


def compile_case_intents(
    intents: list[ApiCaseIntent],
    definitions: list[ApiDefinition],
    secret_metadata: list[dict[str, object]],
) -> CaseCompilationResult:
    """Compile intents independently so one incomplete contract cannot discard a batch."""

    definitions_by_id = {item.id: item for item in definitions}
    cases: list[SuggestedCase] = []
    diagnostics: list[CaseCompileDiagnostic] = []
    compiled_keys: list[str] = []
    for intent in intents:
        definition = definitions_by_id.get(intent.api_definition_id)
        if definition is None:
            diagnostics.append(
                CaseCompileDiagnostic(
                    intent.case_key,
                    intent.title,
                    f"API #{intent.api_definition_id} 不在本次已确认的接口范围内",
                )
            )
            continue
        try:
            state = _CompileState(definitions=definitions, secrets=secret_metadata)
            compiled = _compile_intent(intent, definition, state)
        except (CaseCompilationError, ValidationError, ValueError) as exc:
            diagnostics.append(
                CaseCompileDiagnostic(intent.case_key, intent.title, str(exc))
            )
            continue
        cases.append(compiled)
        compiled_keys.append(intent.case_key)
    return CaseCompilationResult(
        result=CaseGenerationResult(cases=cases) if cases else None,
        diagnostics=diagnostics,
        compiled_case_keys=compiled_keys,
    )


def _compile_intent(
    intent: ApiCaseIntent,
    definition: ApiDefinition,
    state: _CompileState,
) -> SuggestedCase:
    response_entry = _response_entry(definition, intent.expected_status)
    if response_entry is None:
        raise CaseCompilationError(
            f"OpenAPI 未定义 {intent.expected_status} 响应，无法生成可验证断言"
        )
    request = _compile_request(
        definition,
        state,
        intent.input_mutations,
        auth_mode=intent.auth_mode,
        retry_on_transient=intent.retry_on_transient,
        scenario_type=intent.scenario_type,
    )
    _prepend_login_if_needed(state)
    assertions = _compile_assertions(intent, response_entry)
    extractors, cleanup = _compile_cleanup(intent, definition, state)
    _prepend_login_if_needed(state)
    steps = [
        SuggestedStep(
            order=1,
            action=f"调用 {definition.method.upper()} {definition.path}",
            expected=f"返回 HTTP {intent.expected_status} 并满足响应断言",
        )
    ]
    expected_claims = "、".join(item.field for item in intent.expected_claims)
    return SuggestedCase(
        title=intent.title,
        case_type=CaseType.API,
        priority=intent.priority,
        preconditions=["项目默认环境可用", "OpenAPI 契约与当前接口一致"],
        steps=steps,
        test_data={
            "intent_key": intent.case_key,
            "scenario_type": intent.scenario_type.value,
            "compiler_version": CASE_COMPILER_VERSION,
        },
        expected_result=(
            f"HTTP {intent.expected_status}"
            + (f"，并验证 {expected_claims}" if expected_claims else "，响应结构符合契约")
        ),
        tags=[
            *(f"coverage:{key}" for key in intent.checkpoint_keys),
            f"intent:{intent.case_key}",
            "ai-compiled",
            intent.scenario_type.value.lower(),
        ],
        confidence=intent.confidence,
        request=request,
        pre_actions=state.pre_actions,
        extractors=extractors,
        assertions=assertions,
        cleanup=cleanup,
    )


def _compile_request(
    definition: ApiDefinition,
    state: _CompileState,
    mutations: list[ApiCaseInputMutation] | None = None,
    *,
    auth_mode: str = "AUTO",
    retry_on_transient: bool = False,
    scenario_type: ApiCaseIntentScenario = ApiCaseIntentScenario.POSITIVE,
) -> ApiRequestTemplate:
    mutations = mutations or []
    by_target = {
        (item.location, item.field.strip().lower()): item for item in mutations
    }
    used_mutations: set[tuple[str, str]] = set()
    path_values: dict[str, str] = {}
    query_params: list[RequestValueItem] = []
    headers: list[RequestValueItem] = []
    cookies: list[RequestValueItem] = []

    for parameter in definition.parameters or []:
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name") or "").strip()
        location = str(parameter.get("in") or "").upper()
        if not name or location not in {"PATH", "QUERY", "HEADER", "COOKIE"}:
            continue
        mutation = _matching_mutation(by_target, location, name)
        if mutation is not None:
            used_mutations.add((mutation.location, mutation.field.strip().lower()))
        required = bool(parameter.get("required")) or location == "PATH"
        if mutation is None and not required:
            continue
        if mutation is not None and mutation.strategy == ApiCaseInputStrategy.OMIT:
            if location == "PATH":
                raise CaseCompilationError(f"路径参数 {name} 不能编译为缺失值")
            continue
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
        value = _compiled_input_value(
            name,
            schema,
            mutation,
            state,
            definition,
            path_parameter=location == "PATH",
            scenario_type=scenario_type,
        )
        if location == "PATH":
            path_values[name] = str(value)
        else:
            item = RequestValueItem(name=name, value=_parameter_text(value), enabled=True)
            {"QUERY": query_params, "HEADER": headers, "COOKIE": cookies}[location].append(item)

    request_schema = (
        definition.request_schema
        if isinstance(definition.request_schema, dict)
        else None
    )
    body_content: Any | None = None
    if request_schema is not None:
        body_content = _sample_schema_object(request_schema, state, definition)
        if not isinstance(body_content, dict):
            body_content = _sample_value("body", request_schema, state)
        for mutation in mutations:
            key = (mutation.location, mutation.field.strip().lower())
            if key in used_mutations or mutation.location not in {"AUTO", "BODY"}:
                continue
            property_schema = _property_schema(request_schema, mutation.field)
            if not isinstance(body_content, dict) or not property_schema:
                continue
            used_mutations.add(key)
            if mutation.strategy == ApiCaseInputStrategy.OMIT:
                body_content.pop(mutation.field, None)
            else:
                body_content[mutation.field] = _mutation_value(
                    mutation,
                    property_schema,
                    state,
                    scenario_type=scenario_type,
                )

    unused = [
        item.field
        for item in mutations
        if (item.location, item.field.strip().lower()) not in used_mutations
    ]
    if unused:
        raise CaseCompilationError(
            "输入字段未在 OpenAPI 请求中找到：" + ", ".join(unused)
        )
    auth = _compile_request_auth(definition, state, auth_mode)
    retry = ApiRetryPolicy(max_retries=1 if retry_on_transient else 0)
    return ApiRequestTemplate(
        method=definition.method.upper(),
        url=_runtime_url(definition.path, path_values),
        query_params=query_params,
        headers=headers,
        cookies=cookies,
        body=(
            RequestBody(type=RequestBodyType.JSON, content=body_content)
            if body_content is not None
            else RequestBody()
        ),
        auth=auth,
        timeout_ms=30000,
        follow_redirects=True,
        retry_policy=retry,
    )


def _matching_mutation(
    mutations: dict[tuple[str, str], ApiCaseInputMutation],
    location: str,
    name: str,
) -> ApiCaseInputMutation | None:
    normalized = name.lower()
    return mutations.get((location, normalized)) or mutations.get(("AUTO", normalized))


def _compiled_input_value(
    name: str,
    schema: dict[str, Any],
    mutation: ApiCaseInputMutation | None,
    state: _CompileState,
    definition: ApiDefinition,
    *,
    path_parameter: bool,
    scenario_type: ApiCaseIntentScenario,
) -> Any:
    if mutation is not None and (
        mutation.strategy != ApiCaseInputStrategy.VALID
        or _requires_unique_runtime_value(mutation.field, scenario_type)
    ):
        return _mutation_value(
            mutation,
            schema,
            state,
            scenario_type=scenario_type,
        )
    if name.lower().endswith("_id") or path_parameter:
        dependency = _ensure_dependency(name, definition, state, path_parameter=path_parameter)
        if dependency is not None:
            return f"{{{{{dependency}}}}}"
        if path_parameter:
            raise CaseCompilationError(
                f"路径参数 {name} 没有可确定的前置生产接口和响应提取路径"
            )
    return _sample_value(name, schema, state)


def _sample_schema_object(
    schema: dict[str, Any],
    state: _CompileState,
    definition: ApiDefinition,
) -> Any:
    schema_type = schema.get("type")
    if schema_type != "object" and not isinstance(schema.get("properties"), dict):
        return _sample_value("body", schema, state)
    properties = schema.get("properties") or {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    result: dict[str, Any] = {}
    for name in required:
        field_schema = properties.get(name) if isinstance(properties.get(name), dict) else {}
        credential_secret = _login_credential_secret(str(name), definition, state)
        if credential_secret is not None:
            result[str(name)] = f"{{{{secret.{credential_secret}}}}}"
            continue
        if str(name).lower().endswith("_id"):
            dependency = _ensure_dependency(str(name), definition, state, path_parameter=False)
            if dependency is not None:
                result[str(name)] = f"{{{{{dependency}}}}}"
                continue
        result[str(name)] = _sample_value(str(name), field_schema, state)
    return result


def _sample_value(name: str, schema: dict[str, Any], state: _CompileState) -> Any:
    if _is_sensitive(name, schema):
        secret = _select_secret(state.secrets, name)
        if secret is None:
            raise CaseCompilationError(f"敏感字段 {name} 没有可用的项目 Secret")
        return f"{{{{secret.{secret}}}}}"
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    schema_type = schema.get("type", "string")
    if schema_type == "integer":
        return int(schema.get("minimum", 1))
    if schema_type == "number":
        return float(schema.get("minimum", 1))
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        return [_sample_value(name, item_schema, state)]
    if schema_type == "object":
        return _sample_schema_object(schema, state, _NullDefinition())
    if schema.get("format") == "email":
        return "test@example.com"
    if schema.get("format") == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    min_length = max(int(schema.get("minLength", 1)), 1)
    return (f"test-{name}" if min_length <= len(f"test-{name}") else "x" * min_length)[:
        int(schema.get("maxLength", 255))
    ]


class _NullDefinition:
    id = -1
    path = ""


def _mutation_value(
    mutation: ApiCaseInputMutation,
    schema: dict[str, Any],
    state: _CompileState,
    *,
    scenario_type: ApiCaseIntentScenario,
) -> Any:
    strategy = mutation.strategy
    name = mutation.field
    if _is_sensitive(name, schema):
        # AI 只表达测试意图，不能把它返回的敏感字面量写入正式 DSL。
        # 正向用例中的 FIXED 只表达“使用正确凭据”，仍绑定项目 Secret。
        # 负向/鉴权用例中的 FIXED 则表达错误凭据，必须生成无效值，不能
        # 因为去除 AI 字面量而反向替换成正确 Secret。
        if strategy == ApiCaseInputStrategy.VALID or (
            strategy == ApiCaseInputStrategy.FIXED
            and scenario_type not in {
                ApiCaseIntentScenario.NEGATIVE,
                ApiCaseIntentScenario.AUTHORIZATION,
            }
        ):
            return _sample_value(name, schema, state)
        if strategy == ApiCaseInputStrategy.EMPTY:
            return ""
        variable_name = f"invalid_{_safe_name(name)}"
        if variable_name not in state.produced_names:
            state.pre_actions.append(
                FakerAction(name=variable_name, generator=FakerGenerator.WORD)
            )
            state.produced_names.add(variable_name)
        return f"{{{{{variable_name}}}}}"
    if _requires_unique_runtime_value(name, scenario_type):
        variable_name = f"unique_{_safe_name(name)}"
        if variable_name not in state.produced_names:
            state.pre_actions.append(
                FakerAction(name=variable_name, generator=FakerGenerator.UUID)
            )
            state.produced_names.add(variable_name)
        return f"{{{{{variable_name}}}}}"
    if strategy == ApiCaseInputStrategy.FIXED:
        if mutation.value is None:
            raise CaseCompilationError(f"字段 {name} 使用 FIXED 时必须提供 value")
        return mutation.value
    if strategy == ApiCaseInputStrategy.EMPTY:
        return ""
    if strategy == ApiCaseInputStrategy.INVALID_TYPE:
        return "invalid" if schema.get("type") in {"integer", "number", "boolean"} else 12345
    if strategy in {ApiCaseInputStrategy.MINIMUM, ApiCaseInputStrategy.BELOW_MINIMUM}:
        if "minimum" not in schema:
            raise CaseCompilationError(f"字段 {name} 没有 minimum 契约")
        value = schema["minimum"]
        return value - 1 if strategy == ApiCaseInputStrategy.BELOW_MINIMUM else value
    if strategy in {ApiCaseInputStrategy.MAXIMUM, ApiCaseInputStrategy.ABOVE_MAXIMUM}:
        if "maximum" not in schema:
            raise CaseCompilationError(f"字段 {name} 没有 maximum 契约")
        value = schema["maximum"]
        return value + 1 if strategy == ApiCaseInputStrategy.ABOVE_MAXIMUM else value
    return _sample_value(name, schema, state)


def _requires_unique_runtime_value(
    name: str,
    scenario_type: ApiCaseIntentScenario,
) -> bool:
    if scenario_type != ApiCaseIntentScenario.RELIABILITY:
        return False
    normalized = name.strip().lower().replace("-", "_")
    return normalized == "key" or any(
        marker in normalized for marker in _UNIQUE_RUNTIME_MARKERS
    )


def _compile_request_auth(
    definition: ApiDefinition,
    state: _CompileState,
    auth_mode: str,
) -> RequestAuth:
    if auth_mode == "OMIT" or not _requires_auth(definition):
        return RequestAuth(type="NONE")
    schemes = _auth_schemes(definition)
    bearer = next(
        (item for item in schemes if item.get("type") == "http" and item.get("scheme") == "bearer"),
        None,
    )
    if bearer is not None:
        reference = _bearer_reference(state)
        return RequestAuth(type="BEARER", token=reference)
    api_key = next((item for item in schemes if item.get("type") == "apiKey"), None)
    if api_key is not None:
        secret = _select_secret(state.secrets, "api_key")
        if secret is None:
            raise CaseCompilationError("受保护 API 需要 API Key，但项目没有 API_KEY Secret")
        placement = "QUERY" if api_key.get("in") == "query" else "HEADER"
        return RequestAuth(
            type="API_KEY",
            key_name=str(api_key.get("name") or "X-API-Key"),
            key_value=f"{{{{secret.{secret}}}}}",
            placement=placement,
        )
    raise CaseCompilationError("受保护 API 的认证方式尚无确定性编译规则")


def _bearer_reference(state: _CompileState) -> str:
    if state.bearer_reference is not None:
        return state.bearer_reference
    token_secret = _select_secret(state.secrets, "token", accepted_types={"TOKEN", "API_KEY"})
    if token_secret is not None:
        state.bearer_reference = f"{{{{secret.{token_secret}}}}}"
    else:
        state.bearer_reference = "{{token}}"
        state.needs_login = True
    return state.bearer_reference


def _prepend_login_if_needed(state: _CompileState) -> None:
    if not state.needs_login or "token" in state.produced_names:
        return
    candidates = [
        item
        for item in state.definitions
        if item.method.upper() == "POST"
        and any(marker in item.path.lower() for marker in ("login", "auth", "session"))
    ]
    for definition in candidates:
        try:
            success_status = _success_status(definition)
        except CaseCompilationError:
            continue
        token_path = _find_response_field_path(definition, success_status, "token")
        if token_path is None:
            continue
        request = _compile_request(definition, state, auth_mode="OMIT")
        action = GetTokenAction(
            name="token",
            source="JSONPATH",
            expression=token_path,
            request=request,
        )
        state.pre_actions.insert(0, action)
        state.produced_names.add("token")
        return
    raise CaseCompilationError(
        "受保护 API 需要 Bearer Token，但未找到可从响应 Schema 提取 token 的登录接口"
    )


def _ensure_dependency(
    requested_name: str,
    consumer: ApiDefinition | _NullDefinition,
    state: _CompileState,
    *,
    path_parameter: bool,
) -> str | None:
    resource_name = (
        _singular(_collection_name(consumer.path))
        if path_parameter
        else requested_name.lower().removesuffix("_id")
    )
    variable_name = f"{resource_name}_id" if resource_name else requested_name
    variable_name = _safe_name(variable_name)
    if variable_name in state.produced_names:
        return variable_name
    if variable_name in state.compiling_names:
        raise CaseCompilationError(f"接口依赖存在循环：{variable_name}")
    state.compiling_names.add(variable_name)
    try:
        producer = _find_dependency_producer(resource_name, consumer, state.definitions)
        if producer is None:
            return None
        expression = _find_response_field_path(
            producer,
            _success_status(producer),
            "id",
            prefer_items=producer.method.upper() == "GET",
        )
        if expression is None:
            raise CaseCompilationError(
                f"前置接口 {producer.method} {producer.path} 的响应 Schema 中没有可提取的 id"
            )
        request = _compile_request(producer, state, auth_mode="AUTO")
        state.pre_actions.append(
            ApiSetupAction(
                name=variable_name,
                source="JSONPATH",
                expression=expression,
                request=request,
            )
        )
        state.produced_names.add(variable_name)
        return variable_name
    finally:
        state.compiling_names.discard(variable_name)


def _find_dependency_producer(
    resource_name: str,
    consumer: ApiDefinition | _NullDefinition,
    definitions: list[ApiDefinition],
) -> ApiDefinition | None:
    collection = _collection_name(consumer.path)
    if collection:
        post = next(
            (
                item
                for item in definitions
                if item.id != consumer.id
                and item.method.upper() == "POST"
                and _collection_name(item.path) == collection
            ),
            None,
        )
        if post is not None:
            return post
    candidates = [
        item
        for item in definitions
        if item.id != consumer.id
        and item.method.upper() in {"GET", "POST"}
        and resource_name
        and (
            resource_name in _singular(_collection_name(item.path))
            or resource_name in item.name.lower()
        )
    ]
    candidates.sort(key=lambda item: 0 if item.method.upper() == "GET" else 1)
    return candidates[0] if candidates else None


def _compile_assertions(
    intent: ApiCaseIntent,
    response_entry: dict[str, Any],
) -> list[Assertion]:
    assertions = [
        Assertion(
            name=f"status_is_{intent.expected_status}",
            type="STATUS_CODE",
            expected=intent.expected_status,
        )
    ]
    schema = response_entry.get("schema") if isinstance(response_entry.get("schema"), dict) else {}
    used_names = {assertions[0].name}
    for index, claim in enumerate(intent.expected_claims, start=1):
        assertion = _claim_assertion(claim, schema, index)
        name = assertion.name
        suffix = 2
        while name in used_names:
            name = f"{assertion.name}_{suffix}"
            suffix += 1
        assertion.name = name
        used_names.add(name)
        assertions.append(assertion)
    if len(assertions) == 1:
        assertions.append(_automatic_content_assertion(schema))
    return assertions


def _claim_assertion(
    claim: ApiCaseExpectedClaim,
    schema: dict[str, Any],
    index: int,
) -> Assertion:
    field_ref = _find_schema_field(schema, claim.field)
    if field_ref is None:
        raise CaseCompilationError(
            f"响应字段 {claim.field} 未在对应状态码的 OpenAPI Schema 中找到"
        )
    name = f"response_{_safe_name(claim.field)}_{index}"
    if claim.operator == "EXISTS":
        return Assertion(name=name, type="EXISTS", source="JSONPATH", expression=field_ref.path)
    if claim.operator == "CONTAINS":
        if not isinstance(claim.value, str) or not claim.value:
            raise CaseCompilationError(f"响应字段 {claim.field} 使用 CONTAINS 时需要文本 value")
        return Assertion(
            name=name,
            type="CONTAINS",
            source="JSONPATH",
            expression=field_ref.path,
            expected=claim.value,
        )
    expected = claim.value
    if expected is None:
        enum = field_ref.schema.get("enum")
        expected = enum[0] if isinstance(enum, list) and enum else field_ref.schema.get("const")
    if expected is None:
        return Assertion(name=name, type="EXISTS", source="JSONPATH", expression=field_ref.path)
    return Assertion(
        name=name,
        type="JSONPATH_EQUAL",
        source="JSONPATH",
        expression=field_ref.path,
        operator="EQ",
        expected=expected,
    )


def _automatic_content_assertion(schema: dict[str, Any]) -> Assertion:
    if schema.get("type") == "string":
        return Assertion(
            name="response_text_not_empty",
            type="REGEX",
            source="RESPONSE_TEXT",
            operator="EQ",
            expected=r".+",
        )
    fields = _flatten_schema(schema)
    enum_field = next(
        (
            item
            for item in fields
            if isinstance(item.schema.get("enum"), list) and item.schema["enum"]
        ),
        None,
    )
    if enum_field is not None:
        return Assertion(
            name=f"response_{_safe_name(enum_field.name)}_matches_contract",
            type="JSONPATH_EQUAL",
            source="JSONPATH",
            expression=enum_field.path,
            operator="EQ",
            expected=enum_field.schema["enum"][0],
        )
    array_field = next((item for item in fields if item.schema.get("type") == "array"), None)
    if array_field is not None:
        return Assertion(
            name=f"response_{_safe_name(array_field.name)}_is_array",
            type="TYPE",
            source="JSONPATH",
            expression=array_field.path,
            operator="EQ",
            expected="array",
        )
    field_ref = next(iter(fields), None)
    if field_ref is None:
        raise CaseCompilationError("OpenAPI 响应 Schema 中没有可用于内容断言的字段")
    return Assertion(
        name=f"response_{_safe_name(field_ref.name)}_exists",
        type="EXISTS",
        source="JSONPATH",
        expression=field_ref.path,
    )


def _compile_cleanup(
    intent: ApiCaseIntent,
    definition: ApiDefinition,
    state: _CompileState,
) -> tuple[list[ResponseExtractor], list[CleanupConfig]]:
    if definition.method.upper() != "POST" or not 200 <= intent.expected_status < 300:
        if intent.cleanup_required:
            raise CaseCompilationError("当前意图要求清理，但目标接口不是成功创建操作")
        return [], []
    collection = _collection_name(definition.path)
    cleanup_api = next(
        (
            item
            for item in state.definitions
            if item.method.upper() == "DELETE"
            and _collection_name(item.path) == collection
            and _PATH_PARAMETER.search(item.path)
        ),
        None,
    )
    if cleanup_api is None:
        if intent.cleanup_required:
            raise CaseCompilationError("未找到与创建接口匹配的 DELETE 清理契约")
        return [], []
    id_path = _find_response_field_path(definition, intent.expected_status, "id")
    if id_path is None:
        raise CaseCompilationError("创建接口响应 Schema 中没有可提取的资源 ID")
    resource_name = _singular(collection) or "resource"
    variable_name = f"{_safe_name(resource_name)}_id"
    extractors = [
        ResponseExtractor(
            name=variable_name,
            source="JSONPATH",
            expression=id_path,
        )
    ]
    path_values = {
        name: f"{{{{{variable_name}}}}}" for name in _PATH_PARAMETER.findall(cleanup_api.path)
    }
    cleanup_auth = CleanupAuth(type="NONE")
    if _requires_auth(cleanup_api):
        reference = _bearer_reference(state)
        cleanup_auth = CleanupAuth(type="BEARER", credential_ref=reference)
    cleanup = CleanupConfig(
        cleanup_id=f"cleanup_{_safe_name(resource_name)}",
        cleanup_type="API",
        policy="ALWAYS",
        method="DELETE",
        url=_runtime_url(cleanup_api.path, path_values),
        auth=cleanup_auth,
    )
    return extractors, [cleanup]


def _response_entry(definition: ApiDefinition, status: int) -> dict[str, Any] | None:
    responses = definition.response_schema if isinstance(definition.response_schema, dict) else {}
    value = responses.get(str(status)) or responses.get(status)
    return value if isinstance(value, dict) else None


def _success_status(definition: ApiDefinition) -> int:
    responses = definition.response_schema if isinstance(definition.response_schema, dict) else {}
    for raw_status in responses:
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            continue
        if 200 <= status < 300:
            return status
    raise CaseCompilationError(f"{definition.method} {definition.path} 没有 2xx 响应契约")


def _find_response_field_path(
    definition: ApiDefinition,
    status: int,
    field_name: str,
    *,
    prefer_items: bool = False,
) -> str | None:
    entry = _response_entry(definition, status)
    if entry is None or not isinstance(entry.get("schema"), dict):
        return None
    fields = [item for item in _flatten_schema(entry["schema"]) if item.name == field_name]
    if prefer_items:
        fields.sort(key=lambda item: 0 if ".items[0]." in item.path else 1)
    else:
        fields.sort(key=lambda item: (item.path.count("[0]"), item.path.count(".")))
    return fields[0].path if fields else None


def _find_schema_field(schema: dict[str, Any], requested: str) -> _SchemaField | None:
    normalized = requested.strip().removeprefix("$").removeprefix(".")
    fields = _flatten_schema(schema)
    exact = [
        item
        for item in fields
        if item.path.removeprefix("$.").replace("[0]", "") == normalized.replace("[0]", "")
    ]
    if len(exact) == 1:
        return exact[0]
    leaf = normalized.rsplit(".", 1)[-1]
    matches = [item for item in fields if item.name == leaf]
    return matches[0] if len(matches) == 1 else None


def _flatten_schema(schema: dict[str, Any], path: str = "$") -> list[_SchemaField]:
    result: list[_SchemaField] = []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if not isinstance(child, dict):
                continue
            child_path = f"{path}.{name}"
            result.append(_SchemaField(str(name), child_path, child))
            result.extend(_flatten_schema(child, child_path))
    items = schema.get("items")
    if isinstance(items, dict):
        result.extend(_flatten_schema(items, f"{path}[0]"))
    return result


def _property_schema(schema: dict[str, Any], field_name: str) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not isinstance(properties.get(field_name), dict):
        return {}
    return properties[field_name]


def _requires_auth(definition: ApiDefinition) -> bool:
    auth_info = definition.auth_info if isinstance(definition.auth_info, dict) else {}
    requirements = auth_info.get("requirements")
    return isinstance(requirements, list) and bool(requirements)


def _auth_schemes(definition: ApiDefinition) -> list[dict[str, Any]]:
    auth_info = definition.auth_info if isinstance(definition.auth_info, dict) else {}
    schemes = auth_info.get("schemes")
    if not isinstance(schemes, dict):
        return []
    return [value for value in schemes.values() if isinstance(value, dict)]


def _select_secret(
    secrets: list[dict[str, object]],
    field_name: str,
    *,
    accepted_types: set[str] | None = None,
) -> str | None:
    normalized = field_name.lower()
    ranked = [
        item
        for item in secrets
        if isinstance(item.get("name"), str)
        and (accepted_types is None or str(item.get("type")) in accepted_types)
    ]
    ranked.sort(
        key=lambda item: (
            0 if normalized in str(item["name"]).lower() else 1,
            0 if str(item.get("scope")) == "ENVIRONMENT" else 1,
            0 if str(item.get("type")) in {"PASSWORD", "TOKEN", "API_KEY", "CLIENT_SECRET"} else 1,
            str(item["name"]),
        )
    )
    return str(ranked[0]["name"]) if ranked else None


def _login_credential_secret(
    field_name: str,
    definition: ApiDefinition | _NullDefinition,
    state: _CompileState,
) -> str | None:
    path = str(getattr(definition, "path", "")).lower()
    method = str(getattr(definition, "method", "")).upper()
    normalized_field = _safe_name(field_name).lower()
    if method != "POST" or not any(
        marker in path for marker in ("login", "signin", "sign-in", "session", "auth")
    ):
        return None
    if normalized_field not in {
        "username",
        "user_name",
        "user",
        "account",
        "login",
        "email",
    }:
        return None
    matches = [
        item
        for item in state.secrets
        if isinstance(item.get("name"), str)
        and normalized_field in _safe_name(str(item["name"])).lower()
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda item: (
            0 if _safe_name(str(item["name"])).lower().endswith(normalized_field) else 1,
            0 if str(item.get("scope")) == "ENVIRONMENT" else 1,
            str(item["name"]),
        )
    )
    return str(matches[0]["name"])


def _is_sensitive(name: str, schema: dict[str, Any]) -> bool:
    normalized = name.lower()
    return schema.get("format") == "password" or any(
        marker in normalized for marker in _SENSITIVE_MARKERS
    )


def _runtime_url(path: str, path_values: dict[str, str] | None = None) -> str:
    values = path_values or {}
    runtime_path = _PATH_PARAMETER.sub(
        lambda match: values.get(match.group(1), "{{" + match.group(1) + "}}"),
        path,
    )
    return f"{{{{base_url}}}}{runtime_path}"


def _collection_name(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part and not part.startswith("{")]
    return parts[-1].lower() if parts else ""


def _singular(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("s") and len(value) > 1:
        return value[:-1]
    return value


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-").lower()
    if not normalized or not normalized[0].isalpha():
        normalized = f"value_{normalized}"
    return normalized[:255]


def _parameter_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)
