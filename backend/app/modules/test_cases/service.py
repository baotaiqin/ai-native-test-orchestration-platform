import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.logging import get_logger, redact_text
from app.core.time import utc_now_naive
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.api_definitions.models import ApiDefinition
from app.modules.auth.schemas import CurrentUser
from app.modules.datasets.models import Dataset, DatasetVersion
from app.modules.datasets.service import validate_iteration_mapping
from app.modules.environments.models import Environment, EnvironmentVariable
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.builtin import ensure_builtin_prompts
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.resource_registry.service import validate_cleanup_scope
from app.modules.secrets.models import Secret
from app.modules.test_cases.case_compiler import CASE_COMPILER_VERSION, compile_case_intents
from app.modules.test_cases.models import (
    AiCaseDesignTask,
    AiCaseGeneration,
    AiCaseGenerationTask,
    AiCaseSuggestion,
    RequirementCaseLink,
    TestCase,
    TestCaseVersion,
)
from app.modules.test_cases.retry_limit import ensure_v1_api_step_retry_limit
from app.modules.test_cases.schemas import (
    ActionType,
    AiCaseDesignResult,
    AiRecommendedApiDecision,
    ApiCaseIntentResult,
    ApiRequestTemplate,
    Assertion,
    CaseDesignPlanResponse,
    CaseDesignScopeExclusion,
    CaseDesignTaskCreate,
    CaseDesignTaskListResponse,
    CaseDesignTaskResponse,
    CaseGenerationListResponse,
    CaseGenerationRecompileResponse,
    CaseGenerationRequest,
    CaseGenerationResponse,
    CaseGenerationResult,
    CaseGenerationTaskListResponse,
    CaseGenerationTaskResponse,
    CaseGenerationTaskStatus,
    CaseRecompileItem,
    CaseSuggestionBulkDecision,
    CaseSuggestionBulkDeleteResponse,
    CaseSuggestionBulkEdit,
    CaseSuggestionDecision,
    CaseSuggestionEdit,
    CaseSuggestionResponse,
    CaseType,
    RecommendedApi,
    RequirementCaseLinkResponse,
    SuggestedCase,
    SuggestionDecision,
    SuggestionStatus,
    TestCaseCreate,
    TestCaseDetailResponse,
    TestCaseResponse,
    TestCaseVersionCreate,
    TestCaseVersionResponse,
    TestCheckPoint,
    action_type,
)

logger = get_logger(__name__)

_OPENAPI_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_CHECK_POINT_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*|#+\s*)")
_RUNTIME_URL_PARAMETER = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$")
_SECRET_REFERENCE = re.compile(r"^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$")
_RUNTIME_REFERENCE = re.compile(r"^\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}$")
_RUNTIME_REFERENCE_ANY = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_SENSITIVE_INPUT_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "credential",
)
_AI_CASE_DESIGN_TIMEOUT_SECONDS = 300
_AI_CASE_DESIGN_BATCH_TIMEOUT_SECONDS = 120
_AI_CASE_DESIGN_LEAF_BATCH_SIZE = 4
_AI_CASE_GENERATION_TIMEOUT_SECONDS = 300

_AI_CASE_DESIGN_INSTRUCTIONS = (
    "按以下顺序完成，不得省略步骤：\n"
    "1. 完整阅读需求范围中的每条需求及其子需求，逐条提取可以独立执行和验收的检查点。\n"
    "2. 每个检查点只能描述一个输入条件、一个动作、一个期望结果和一个状态码；禁止用"
    "‘或’、‘以及’、‘同时’合并不同分支，标题不得超过 100 个字符。\n"
    "3. 正确、异常、边界、权限和恢复场景必须拆开。登录至少拆分正确凭据、错误用户名、"
    "错误密码、缺少用户名、缺少密码和未认证访问；数值的零、负数、超上限和非整数也要"
    "分别处理。过滤、分页、重试、超时、创建后查询、首次删除和重复删除必须独立。\n"
    "4. 再从候选 API 中选择接口。AI 返回的每个 recommended_apis 项都必须填写非空的"
    "check_point_keys，并且只能引用本次 check_points 中存在的编号；只推荐确实覆盖至少"
    "一个检查点的业务接口。登录准备和数据清理由平台补齐，不要为了凑闭环返回空关联。\n"
    "5. api_definition_id 只能来自候选接口真实编号。不能由候选 API、运行环境、Secret、"
    "前置请求和响应提取形成可执行闭环的内容必须写入 gaps，不得编造接口或能力。"
)


@dataclass
class _ApiDesignDecision:
    api_definition_id: int
    role: str
    required: bool
    reason: str
    check_point_keys: list[str]

_DOMAIN_TERMS: tuple[tuple[str, ...], ...] = (
    ("登录", "认证", "鉴权", "会话", "login", "auth", "session", "token"),
    ("用户", "账号", "user", "account"),
    ("商品", "库存", "product", "inventory"),
    ("订单", "购物车", "order", "cart"),
    ("资源", "resource"),
    ("创建", "新增", "create", "add", "post"),
    ("查询", "列表", "详情", "search", "list", "get"),
    ("更新", "修改", "update", "edit", "put", "patch"),
    ("删除", "清理", "delete", "remove", "cleanup"),
    ("下载", "文件", "download", "file"),
    ("超时", "慢", "timeout", "slow"),
    ("重试", "失败恢复", "retry", "flaky"),
)

_EXCLUSIVE_CHECK_POINT_MARKERS = (
    "正确凭据",
    "错误用户名",
    "错误密码",
    "缺少用户名",
    "缺少密码",
    "未认证",
    "零值",
    "负数",
    "超上限",
    "非整数",
    "商品不存在",
    "库存不足",
    "首次删除",
    "重复删除",
)

_AI_CASE_GENERATION_INSTRUCTIONS = (
    "只输出测试意图，不要输出 request、URL、认证、Runtime 变量、前置动作、"
    "extractor、assertion 或 cleanup DSL，这些由平台根据 OpenAPI 确定性编译。"
    "api_definition_id 必须使用候选接口真实编号；checkpoint_keys 必须使用本次检查点"
    "编号；expected_status 必须是该接口 OpenAPI 已定义的状态码。"
    "input_mutations.field 和 expected_claims.field 只写 OpenAPI 中真实存在的字段名或"
    "响应字段路径。每条意图只验证一个明确分支，正向、错误凭据、缺少字段、"
    "边界值和未认证访问必须分开。无法由当前需求与 OpenAPI 证明的内容放入 gaps，"
    "不得猜测。confidence 按证据充分度填写 0 到 1。"
)


def _matches_allowed_url_template(actual_url: str, template_url: str) -> bool:
    actual_parts = actual_url.split("/")
    template_parts = template_url.split("/")
    if len(actual_parts) != len(template_parts):
        return False
    for actual, template in zip(actual_parts, template_parts, strict=True):
        if template == "{{base_url}}":
            if actual != template:
                return False
        elif _RUNTIME_URL_PARAMETER.fullmatch(template):
            if not actual or "/" in actual:
                return False
            if actual.startswith("{{") and not _RUNTIME_URL_PARAMETER.fullmatch(actual):
                return False
        elif actual != template:
            return False
    return True


def _resolve_allowed_request_key(
    method: str,
    actual_url: str,
    allowed_requests: set[tuple[str, str]] | dict[tuple[str, str], object],
) -> tuple[str, str] | None:
    normalized_method = method.upper()
    direct = (normalized_method, actual_url)
    if direct in allowed_requests:
        return direct
    matches = [
        (allowed_method, template_url)
        for allowed_method, template_url in allowed_requests
        if allowed_method == normalized_method
        and _matches_allowed_url_template(actual_url, template_url)
    ]
    return matches[0] if len(matches) == 1 else None


def _normalize_generated_case_result(
    value: object,
    allowed_api_requests: set[tuple[str, str]] | None = None,
) -> object:
    """Normalize only unambiguous provider aliases before strict domain validation."""

    if not isinstance(value, dict):
        return value
    normalized = deepcopy(value)
    cases = normalized.get("cases")
    if not isinstance(cases, list):
        return normalized
    for case in cases:
        if not isinstance(case, dict):
            continue
        request = case.get("request")
        if allowed_api_requests and isinstance(request, dict):
            method = str(request.get("method") or "").upper()
            url = request.get("url")
            if isinstance(url, str):
                matches = [
                    allowed_url
                    for allowed_method, allowed_url in allowed_api_requests
                    if allowed_method == method
                    and _matches_allowed_url_template(url, allowed_url)
                ]
                uses_runtime_alias = any(
                    part != "{{base_url}}" and _RUNTIME_URL_PARAMETER.fullmatch(part)
                    for part in url.split("/")
                )
                if len(matches) == 1 and not uses_runtime_alias:
                    request["url"] = matches[0]
        assertions = case.get("assertions")
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if not isinstance(assertion, dict) or not isinstance(assertion.get("type"), str):
                continue
            assertion_type = assertion["type"].strip().upper().replace("-", "_")
            if assertion_type in {"EXISTS", "NOT_EXISTS"}:
                assertion["type"] = assertion_type
                assertion.pop("expected", None)
                continue
            if assertion_type not in {"JSON_PATH", "JSONPATH", "JSON_PATH_EQUAL"}:
                continue
            operator = str(assertion.get("operator") or "EQ").strip().upper()
            if operator == "EQ":
                assertion["type"] = "JSONPATH_EQUAL"
                assertion["source"] = assertion.get("source") or "JSON_BODY"
                assertion["operator"] = "EQ"
            elif operator == "CONTAINS":
                assertion["type"] = "CONTAINS"
                assertion["source"] = "JSONPATH"
                assertion["operator"] = "CONTAINS"
    return normalized


def _is_sensitive_input_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return any(marker in normalized for marker in _SENSITIVE_INPUT_MARKERS)


def _pre_action_runtime_names(item: SuggestedCase) -> set[str]:
    names: set[str] = set()
    for action in item.pre_actions:
        if not getattr(action, "enabled", True):
            continue
        if action_type(action) == ActionType.SQL_QUERY:
            result_variable = getattr(action, "result_variable", None)
            if isinstance(result_variable, str):
                names.add(result_variable)
            continue
        name = getattr(action, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _pre_action_sensitive_runtime_names(item: SuggestedCase) -> set[str]:
    return {
        action.name
        for action in item.pre_actions
        if getattr(action, "enabled", True)
        and action_type(action) in {
            ActionType.FAKER,
            ActionType.GET_TOKEN,
            ActionType.API_SETUP,
        }
        and isinstance(getattr(action, "name", None), str)
    }


def _reference_is_bound(
    value: object,
    runtime_names: set[str],
    available_secret_names: set[str],
) -> bool:
    if not isinstance(value, str):
        return False
    secret_match = _SECRET_REFERENCE.fullmatch(value)
    if secret_match:
        return secret_match.group(1) in available_secret_names
    runtime_match = _RUNTIME_REFERENCE.fullmatch(value)
    return bool(runtime_match and runtime_match.group(1) in runtime_names)


def _sensitive_payload_is_bound(
    value: object,
    runtime_names: set[str],
    available_secret_names: set[str],
) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_sensitive_input_name(str(key)) and not _reference_is_bound(
                child, runtime_names, available_secret_names
            ):
                return False
            if not _sensitive_payload_is_bound(
                child, runtime_names, available_secret_names
            ):
                return False
    elif isinstance(value, list):
        return all(
            _sensitive_payload_is_bound(child, runtime_names, available_secret_names)
            for child in value
        )
    return True


def _runtime_reference_names(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            name
            for child in value.values()
            for name in _runtime_reference_names(child)
        }
    if isinstance(value, list):
        return {name for child in value for name in _runtime_reference_names(child)}
    if not isinstance(value, str):
        return set()
    return {
        match.group(1)
        for match in _RUNTIME_REFERENCE_ANY.finditer(value)
        if not match.group(1).startswith("secret.")
    }


def _secret_reference_names(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            name
            for child in value.values()
            for name in _secret_reference_names(child)
        }
    if isinstance(value, list):
        return {name for child in value for name in _secret_reference_names(child)}
    if not isinstance(value, str):
        return set()
    match = _SECRET_REFERENCE.fullmatch(value)
    return {match.group(1)} if match else set()


def _api_request_has_bound_auth(
    request: ApiRequestTemplate | None,
    runtime_names: set[str],
    available_secret_names: set[str],
) -> bool:
    if request is None:
        return False
    auth = request.auth
    auth_type = str(auth.type.value)
    if auth_type == "BEARER":
        return _reference_is_bound(auth.token, runtime_names, available_secret_names)
    if auth_type == "BASIC":
        return bool(auth.username) and _reference_is_bound(
            auth.password, runtime_names, available_secret_names
        )
    if auth_type == "API_KEY":
        return bool(auth.key_name) and _reference_is_bound(
            auth.key_value, runtime_names, available_secret_names
        )
    return any(
        cookie.enabled
        and _reference_is_bound(cookie.value, runtime_names, available_secret_names)
        for cookie in request.cookies
    )


def _case_execution_claim_issues(item: SuggestedCase) -> list[str]:
    if item.request is None:
        return ["request: 缺少 API 请求配置"]
    issues: list[str] = []
    title = item.title.lower()
    expected_statuses = {
        int(value)
        for value in re.findall(r"(?<!\d)([1-5]\d{2})(?!\d)", title)
    }
    asserted_statuses = {
        int(assertion.expected)
        for assertion in item.assertions
        if assertion.enabled
        and str(assertion.type) == "STATUS_CODE"
        and type(assertion.expected) is int
    }
    retry_enabled = item.request.retry_policy.max_retries > 0
    if expected_statuses and not (
        expected_statuses.issubset(asserted_statuses)
        or (retry_enabled and bool(expected_statuses.intersection(asserted_statuses)))
    ):
        issues.append("assertions: 标题声明的 HTTP 状态码没有对应启用断言")
    if any(marker in title for marker in ("重试", "retry")) and not retry_enabled:
        issues.append("request.retry_policy: 标题声明重试但重试次数仍为 0")
    if any(marker in title for marker in ("过滤", "筛选", "filter")) and not any(
        parameter.enabled for parameter in item.request.query_params
    ):
        issues.append("request.query_params: 标题声明过滤但没有启用查询参数")
    if any(marker in title for marker in ("延迟参数", "分页参数", "查询参数")) and not any(
        parameter.enabled for parameter in item.request.query_params
    ):
        issues.append("request.query_params: 标题声明参数场景但没有启用查询参数")
    if "正文" in title and not any(
        assertion.enabled
        and str(assertion.source) in {"RESPONSE_TEXT", "JSON_BODY", "JSONPATH"}
        for assertion in item.assertions
    ):
        issues.append("assertions: 标题声明响应正文校验但没有启用正文断言")
    return issues


def _case_execution_claims_are_configured(item: SuggestedCase) -> bool:
    return not _case_execution_claim_issues(item)


def _semantic_coverage_issues(item: SuggestedCase, checkpoint_title: str) -> list[str]:
    """Reject coverage labels that the compiled DSL does not actually prove."""

    if item.request is None:
        return ["缺少可执行请求"]
    normalized = checkpoint_title.lower()
    issues: list[str] = []
    query_enabled = any(parameter.enabled for parameter in item.request.query_params)
    if (
        any(marker in normalized for marker in ("过滤", "筛选", "分页", "filter"))
        and not query_enabled
    ):
        issues.append("检查点要求查询参数，但请求没有启用查询参数")
    if (
        any(marker in normalized for marker in ("重试", "retry"))
        and item.request.retry_policy.max_retries <= 0
    ):
        issues.append("检查点要求重试，但请求未配置重试")
    if any(marker in normalized for marker in ("重复", "再次", "第二次")):
        issues.append("检查点要求重复操作，当前单请求用例不能证明第二次结果")
    if any(marker in normalized for marker in ("content-type", "内容类型")) and not any(
        assertion.enabled and str(assertion.source) == "HEADER"
        for assertion in item.assertions
    ):
        issues.append("检查点要求响应头校验，但没有启用响应头断言")
    if "cookie" in normalized and not any(
        assertion.enabled and str(assertion.source) == "COOKIE"
        for assertion in item.assertions
    ):
        issues.append("检查点要求 Cookie 校验，但没有启用 Cookie 断言")
    if any(
        marker in normalized
        for marker in ("合法和非法", "成功和失败", "正常和异常", "成功或失败")
    ):
        issues.append("检查点包含多个结果分支，必须拆分为原子检查点")
    return issues


def _case_execution_ready_issues(
    item: SuggestedCase,
    api_contracts: dict[tuple[str, str], dict[str, object]],
    available_secret_names: set[str],
    available_runtime_names: set[str],
) -> list[str]:
    issues = _case_execution_claim_issues(item)
    if item.request is None:
        return issues
    runtime_names = _pre_action_runtime_names(item)
    sensitive_runtime_names = _pre_action_sensitive_runtime_names(item)
    missing_secrets = _secret_reference_names(
        item.request.model_dump(mode="json")
    ) - available_secret_names
    if missing_secrets:
        issues.append(
            "request: 引用了未配置的 Secret：" + ", ".join(sorted(missing_secrets))
        )
    request_key = _resolve_allowed_request_key(
        str(item.request.method), item.request.url, api_contracts
    )
    contract = api_contracts.get(request_key, {}) if request_key is not None else {}
    if bool(contract.get("requires_auth")) and not _api_request_has_bound_auth(
        item.request, sensitive_runtime_names, available_secret_names
    ):
        issues.append("request.auth: 受保护 API 缺少 Secret 或登录前置步骤提供的认证引用")
    if not _sensitive_payload_is_bound(
        item.request.body.content, sensitive_runtime_names, available_secret_names
    ):
        issues.append("request.body: 用户名、密码或令牌等敏感字段必须引用 Secret/前置步骤变量")
    prior_runtime_names: set[str] = set()
    prior_sensitive_runtime_names: set[str] = set()
    for action_index, action in enumerate(item.pre_actions):
        if not getattr(action, "enabled", True):
            continue
        action_request = getattr(action, "request", None)
        action_path = f"pre_actions[{action_index}]"
        if action_request is not None:
            action_missing_secrets = _secret_reference_names(
                action_request.model_dump(mode="json")
            ) - available_secret_names
            if action_missing_secrets:
                issues.append(
                    f"{action_path}.request: 引用了未配置的 Secret："
                    + ", ".join(sorted(action_missing_secrets))
                )
            action_key = _resolve_allowed_request_key(
                str(action_request.method), action_request.url, api_contracts
            )
            if action_key is None:
                issues.append(f"{action_path}.request: 请求不在本次选择的 API 范围内")
            action_contract = (
                api_contracts.get(action_key, {}) if action_key is not None else {}
            )
            if bool(action_contract.get("requires_auth")) and not _api_request_has_bound_auth(
                action_request, prior_sensitive_runtime_names, available_secret_names
            ):
                issues.append(
                    f"{action_path}.request.auth: 受保护 API 缺少可用认证引用"
                )
            if not _sensitive_payload_is_bound(
                action_request.body.content,
                prior_sensitive_runtime_names,
                available_secret_names,
            ):
                issues.append(
                    f"{action_path}.request.body: 敏感字段必须引用 Secret/已有前置步骤变量"
                )
            available_before_action = {
                "base_url",
                "project_id",
                "environment_id",
                *available_runtime_names,
                *prior_runtime_names,
            }
            missing_runtime_names = _runtime_reference_names(
                action_request.model_dump(mode="json")
            ) - available_before_action
            if missing_runtime_names:
                issues.append(
                    f"{action_path}.request: 引用了尚未产生的运行变量："
                    + ", ".join(sorted(missing_runtime_names))
                )
        if action_type(action) == ActionType.SQL_QUERY:
            output_name = getattr(action, "result_variable", None)
        else:
            output_name = getattr(action, "name", None)
        if isinstance(output_name, str):
            prior_runtime_names.add(output_name)
            if action_type(action) in {
                ActionType.FAKER,
                ActionType.GET_TOKEN,
                ActionType.API_SETUP,
            }:
                prior_sensitive_runtime_names.add(output_name)
    allowed_runtime_names = {
        "base_url",
        "project_id",
        "environment_id",
        *available_runtime_names,
        *runtime_names,
    }
    missing_runtime_names = _runtime_reference_names(
        item.request.model_dump(mode="json")
    ) - allowed_runtime_names
    if missing_runtime_names:
        issues.append(
            "request: 引用了未配置或未由前置步骤产生的运行变量："
            + ", ".join(sorted(missing_runtime_names))
        )
    return issues


def _case_is_execution_ready(
    item: SuggestedCase,
    api_contracts: dict[tuple[str, str], dict[str, object]],
    available_secret_names: set[str],
    available_runtime_names: set[str],
) -> bool:
    return not _case_execution_ready_issues(
        item,
        api_contracts,
        available_secret_names,
        available_runtime_names,
    )


def _generated_case_result_validation_errors(
    value: object,
    allowed_api_requests: set[tuple[str, str]] | None = None,
    *,
    api_contracts: dict[tuple[str, str], dict[str, object]] | None = None,
    available_secret_names: set[str] | None = None,
    available_runtime_names: set[str] | None = None,
    require_execution_ready: bool = False,
) -> list[str]:
    try:
        result = CaseGenerationResult.model_validate(
            _normalize_generated_case_result(value, allowed_api_requests)
        )
    except ValidationError as exc:
        errors: list[str] = []
        for error in exc.errors(include_url=False)[:20]:
            path = "$"
            for segment in error.get("loc", ()):
                path += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
            error_type = str(error.get("type", "invalid"))
            errors.append(f"{path}: 字段结构不符合用例定义（{error_type}）")
        return errors or ["$: 用例结构不符合定义"]
    errors = []
    if not allowed_api_requests:
        return errors
    for case_index, item in enumerate(result.cases):
        case_path = f"$.cases[{case_index}]"
        if item.case_type != CaseType.API or item.request is None:
            errors.append(f"{case_path}.request: 必须配置 API 类型请求")
            continue
        request_key = _resolve_allowed_request_key(
            str(item.request.method), item.request.url, allowed_api_requests
        )
        if request_key is None:
            errors.append(f"{case_path}.request: method/url 不在本次选择的 API 范围内")
        enabled_assertions = [
            assertion for assertion in item.assertions if assertion.enabled
        ]
        if not enabled_assertions:
            errors.append(f"{case_path}.assertions: 至少需要一条启用断言")
        elif not any(
            assertion.type not in {"STATUS_CODE", "RESPONSE_TIME"}
            for assertion in enabled_assertions
        ):
            errors.append(f"{case_path}.assertions: 需要至少一条响应内容断言")
        if require_execution_ready:
            errors.extend(
                f"{case_path}.{issue}"
                for issue in _case_execution_ready_issues(
                    item,
                    api_contracts or {},
                    available_secret_names or set(),
                    available_runtime_names or set(),
                )
            )
    return errors


def _is_valid_generated_case_result(
    value: object,
    allowed_api_requests: set[tuple[str, str]] | None = None,
    *,
    api_contracts: dict[tuple[str, str], dict[str, object]] | None = None,
    available_secret_names: set[str] | None = None,
    available_runtime_names: set[str] | None = None,
    require_execution_ready: bool = False,
) -> bool:
    return not _generated_case_result_validation_errors(
        value,
        allowed_api_requests,
        api_contracts=api_contracts,
        available_secret_names=available_secret_names,
        available_runtime_names=available_runtime_names,
        require_execution_ready=require_execution_ready,
    )


def _generated_case_intent_validation_errors(
    value: object,
) -> list[str]:
    """Reserve bounded Repair for JSON/field-shape errors, not contract semantics."""

    try:
        ApiCaseIntentResult.model_validate(value)
    except ValidationError as exc:
        errors: list[str] = []
        for error in exc.errors(include_url=False)[:20]:
            path = "$"
            for segment in error.get("loc", ()):
                path += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
            errors.append(f"{path}: 意图字段不符合定义（{error.get('type', 'invalid')}）")
        return errors or ["$: 用例意图结构不符合定义"]
    return []


def _request_schema_summary(
    schema: dict | None, depth: int = 0
) -> dict[str, object] | None:
    if not isinstance(schema, dict):
        return None
    schema_type = schema.get("type", "object")
    if depth >= 2:
        return {"type": schema_type}
    if schema_type == "array":
        return {
            "type": "array",
            "items": _request_schema_summary(schema.get("items"), depth + 1),
        }
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {"type": schema_type}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return {
        "type": schema_type,
        "fields": [
            {
                "name": str(name),
                "type": value.get("type", "unknown") if isinstance(value, dict) else "unknown",
                "required": name in required,
                "description": value.get("description") if isinstance(value, dict) else None,
                "constraints": _schema_constraints(str(name), value),
                "structure": (
                    _request_schema_summary(value, depth + 1)
                    if isinstance(value, dict)
                    and ("properties" in value or "items" in value)
                    else None
                ),
            }
            for name, value in list(properties.items())[:30]
        ],
    }


def _response_schema_summaries(responses: dict) -> list[dict[str, object]]:
    return [
        {
            "status": str(status),
            "description": response.get("description"),
            "schema": _request_schema_summary(response.get("schema")),
        }
        for status, response in list(responses.items())[:20]
        if isinstance(response, dict)
    ]


def _runtime_request_url(path: str) -> str:
    runtime_path = _OPENAPI_PATH_PARAMETER.sub(
        lambda match: "{{" + match.group(1) + "}}", path
    )
    return f"{{{{base_url}}}}{runtime_path}"


def _api_requires_auth(definition: ApiDefinition) -> bool:
    auth_info = definition.auth_info if isinstance(definition.auth_info, dict) else {}
    requirements = auth_info.get("requirements")
    return isinstance(requirements, list) and bool(requirements)


def _api_auth_summary(definition: ApiDefinition) -> dict[str, object]:
    auth_info = definition.auth_info if isinstance(definition.auth_info, dict) else {}
    schemes = auth_info.get("schemes")
    return {
        "required": _api_requires_auth(definition),
        "schemes": [
            {
                "name": str(name),
                "type": value.get("type"),
                "scheme": value.get("scheme"),
                "in": value.get("in"),
                "parameter_name": value.get("name"),
            }
            for name, value in (schemes.items() if isinstance(schemes, dict) else [])
            if isinstance(value, dict)
        ],
    }


def _schema_constraints(name: str, schema: object) -> dict[str, object]:
    if not isinstance(schema, dict):
        return {}
    allowed = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "enum",
        "pattern",
        "format",
        "default",
    )
    constraints = {key: schema[key] for key in allowed if key in schema}
    if _is_sensitive_input_name(name) or schema.get("format") == "password":
        constraints.pop("default", None)
    return constraints


def _schema_has_sensitive_input(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, value in properties.items():
            if _is_sensitive_input_name(str(name)):
                return True
            if _schema_has_sensitive_input(value):
                return True
    return _schema_has_sensitive_input(schema.get("items"))


def _project_secret_generation_context(
    session: Session, project_id: int
) -> tuple[str, set[str], bool]:
    environment_id = session.scalar(
        select(Environment.id)
        .where(Environment.project_id == project_id, Environment.enabled.is_(True))
        .order_by(Environment.is_default.desc(), Environment.id)
        .limit(1)
    )
    secrets = list(session.scalars(
        select(Secret)
        .where(
            Secret.project_id == project_id,
            Secret.enabled.is_(True),
            or_(
                Secret.environment_id.is_(None),
                Secret.environment_id == environment_id,
            ),
        )
        .order_by(Secret.name)
    ).all())
    metadata = [
        {
            "name": item.name,
            "type": item.secret_type,
            "scope": "PROJECT" if item.environment_id is None else "ENVIRONMENT",
            "environment_id": item.environment_id,
            "reference": f"{{{{secret.{item.name}}}}}",
        }
        for item in secrets
    ]
    return (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        {item.name for item in secrets},
        any(item.secret_type in {"PASSWORD", "CLIENT_SECRET"} for item in secrets),
    )


def _project_runtime_generation_context(
    session: Session, project_id: int
) -> tuple[str, set[str], bool]:
    environment = session.scalar(
        select(Environment)
        .where(Environment.project_id == project_id, Environment.enabled.is_(True))
        .order_by(Environment.is_default.desc(), Environment.id)
        .limit(1)
    )
    if environment is None:
        return "[]", set(), False
    variables = list(session.scalars(
        select(EnvironmentVariable)
        .where(
            EnvironmentVariable.environment_id == environment.id,
            EnvironmentVariable.enabled.is_(True),
        )
        .order_by(EnvironmentVariable.key)
    ).all())
    names = {item.key for item in variables}
    metadata = {
        "environment_id": environment.id,
        "environment_name": environment.name,
        "base_url_configured": bool(environment.base_url),
        "variable_names": sorted(names),
    }
    return (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        names,
        bool(environment.base_url),
    )


def _project_api_generation_context(
    session: Session,
    project_id: int,
    api_definition_ids: list[int] | None = None,
) -> tuple[
    str,
    set[tuple[str, str]],
    dict[tuple[str, str], dict[str, object]],
]:
    statement = select(ApiDefinition).where(
        ApiDefinition.project_id == project_id,
        ApiDefinition.status == "ACTIVE",
    )
    if api_definition_ids is not None:
        statement = statement.where(ApiDefinition.id.in_(api_definition_ids))
    definitions = list(session.scalars(
        statement.order_by(ApiDefinition.path, ApiDefinition.method).limit(30)
    ).all())
    allowed_requests = {
        (item.method.upper(), _runtime_request_url(item.path)) for item in definitions
    }
    contracts = {
        (item.method.upper(), _runtime_request_url(item.path)): {
            "api_definition_id": item.id,
            "requires_auth": _api_requires_auth(item),
            "requires_sensitive_input": _schema_has_sensitive_input(item.request_schema),
            "authentication": _api_auth_summary(item),
        }
        for item in definitions
    }
    summaries = [
        {
            "api_definition_id": item.id,
            "name": item.name,
            "method": item.method.upper(),
            "url": _runtime_request_url(item.path),
            "summary": item.summary,
            "authentication": _api_auth_summary(item),
            "parameters": [
                {
                    "name": parameter.get("name"),
                    "in": parameter.get("in"),
                    "required": bool(parameter.get("required", False)),
                    "type": (
                        parameter.get("schema", {}).get("type")
                        if isinstance(parameter.get("schema"), dict)
                        else None
                    ),
                    "description": parameter.get("description"),
                    "constraints": _schema_constraints(
                        str(parameter.get("name") or ""), parameter.get("schema")
                    ),
                }
                for parameter in item.parameters[:30]
                if isinstance(parameter, dict)
            ],
            "request_body": _request_schema_summary(item.request_schema),
            "responses": _response_schema_summaries(item.response_schema),
        }
        for item in definitions
    ]
    return (
        json.dumps(summaries, ensure_ascii=False, separators=(",", ":")),
        allowed_requests,
        contracts,
    )


def _requirement_scope(
    session: Session, requirement: Requirement
) -> list[tuple[Requirement, RequirementVersion]]:
    requirements = list(session.scalars(
        select(Requirement)
        .where(
            Requirement.project_id == requirement.project_id,
            Requirement.status == "ACTIVE",
        )
        .order_by(Requirement.order_index, Requirement.id)
    ).all())
    by_parent: dict[int | None, list[Requirement]] = {}
    for item in requirements:
        by_parent.setdefault(item.parent_id, []).append(item)
    ordered: list[Requirement] = []

    def visit(item: Requirement) -> None:
        ordered.append(item)
        for child in by_parent.get(item.id, []):
            visit(child)

    visit(requirement)
    result: list[tuple[Requirement, RequirementVersion]] = []
    for item in ordered:
        version = session.get(RequirementVersion, item.current_version_id)
        if version is not None:
            result.append((item, version))
    return result


_API_DESIGN_VERIFICATION_TYPES = {"AUTO", "API"}


def _api_design_scope(
    scope: list[tuple[Requirement, RequirementVersion]],
) -> tuple[
    list[tuple[Requirement, RequirementVersion]],
    list[CaseDesignScopeExclusion],
]:
    """Route a selected subtree to the API-design capability.

    Parent sections are retained as context only when they lead to an applicable
    leaf.  Non-API and non-automatable leaves remain visible in the returned plan
    instead of being silently sent to an incompatible model task.
    """

    by_id = {item.id: item for item, _ in scope}
    parent_ids = {
        item.parent_id for item, _ in scope if item.parent_id is not None
    }
    leaves = [item for item, _ in scope if item.id not in parent_ids]
    included_leaf_ids: set[int] = set()
    exclusions: list[CaseDesignScopeExclusion] = []
    for item in leaves:
        if item.automation_readiness != "READY":
            reason = (
                "需求仍需澄清，暂不生成自动化 API 检查点"
                if item.automation_readiness == "NEEDS_CLARIFICATION"
                else "需求被标记为仅人工验证"
            )
        elif item.verification_type not in _API_DESIGN_VERIFICATION_TYPES:
            labels = {
                "WEB": "Web 自动化",
                "PERFORMANCE": "性能测试",
                "PLATFORM": "平台流程验证",
                "MANUAL": "人工验证",
            }
            reason = f"已分流至{labels.get(item.verification_type, '其他验证能力')}"
        else:
            included_leaf_ids.add(item.id)
            continue
        exclusions.append(
            CaseDesignScopeExclusion(
                requirement_code=item.code,
                requirement_title=item.title,
                verification_type=item.verification_type,
                automation_readiness=item.automation_readiness,
                reason=reason,
            )
        )

    included_ids = set(included_leaf_ids)
    for leaf_id in included_leaf_ids:
        current = by_id.get(leaf_id)
        visited: set[int] = set()
        while current is not None and current.parent_id is not None:
            if current.id in visited:
                break
            visited.add(current.id)
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            included_ids.add(parent.id)
            current = parent
    excluded_ids = {item.requirement_code for item in exclusions}
    for item, _ in scope:
        if item.id in included_ids or item.code in excluded_ids:
            continue
        exclusions.append(
            CaseDesignScopeExclusion(
                requirement_code=item.code,
                requirement_title=item.title,
                verification_type=item.verification_type,
                automation_readiness=item.automation_readiness,
                reason="下级需求均已分流，本节点无需作为 API 设计上下文",
            )
        )
    return (
        [(item, version) for item, version in scope if item.id in included_ids],
        exclusions,
    )


def _extract_check_points(
    requirement: Requirement, version: RequirementVersion, *, start_index: int = 1
) -> list[TestCheckPoint]:
    candidates: list[str] = []
    for raw_line in version.markdown_content.splitlines():
        line = _CHECK_POINT_PREFIX.sub("", raw_line).strip()
        if not line or line == requirement.title or len(line) < 2:
            continue
        if raw_line.lstrip().startswith(("-", "*", "+")) or re.match(
            r"^\s*\d+[.)、]", raw_line
        ):
            candidates.append(line)
    if not candidates:
        plain = re.sub(r"[#>*`]", " ", version.markdown_content)
        candidates = [
            item.strip()
            for item in re.split(r"[\n。；;]+", plain)
            if len(item.strip()) >= 2 and item.strip() != requirement.title
        ]
    if not candidates:
        candidates = [requirement.title]
    unique = list(dict.fromkeys(item[:160] for item in candidates))[:12]
    return [
        TestCheckPoint(
            key=f"CP-{index:02d}",
            title=item,
            source=f"{requirement.code} · {requirement.title}",
        )
        for index, item in enumerate(unique, start=start_index)
    ]


def _scope_check_points(
    scope: list[tuple[Requirement, RequirementVersion]],
) -> list[TestCheckPoint]:
    points: list[TestCheckPoint] = []
    for requirement, version in scope:
        remaining = 100 - len(points)
        if remaining <= 0:
            break
        points.extend(
            _extract_check_points(
                requirement, version, start_index=len(points) + 1
            )[:remaining]
        )
    return points


def _api_contract_fingerprint(
    definitions: list[ApiDefinition], include_api_ids: list[int]
) -> str:
    payload = {
        "contracts": [(item.id, item.contract_hash) for item in definitions],
        "included": sorted(set(include_api_ids)),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _matching_terms(left: str, right: str) -> list[str]:
    left_lower = left.lower()
    right_lower = right.lower()
    matches: list[str] = []
    for group in _DOMAIN_TERMS:
        left_terms = [term for term in group if term in left_lower]
        if left_terms and any(term in right_lower for term in group):
            matches.extend(left_terms[:1])
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", left_lower):
        if token in right_lower:
            matches.append(token)
    return list(dict.fromkeys(matches))


def _api_role(definition: ApiDefinition, requirement_text: str) -> str:
    path = definition.path.lower()
    method = definition.method.upper()
    if any(term in path for term in ("login", "auth", "session")):
        return "前置准备"
    if method == "DELETE":
        return "数据清理"
    if method == "GET" and not any(
        term in requirement_text.lower() for term in ("查询", "列表", "详情", "search", "list")
    ):
        return "结果验证"
    return "核心操作"


def _best_api_matches(
    check_point_title: str,
    definitions: list[ApiDefinition],
) -> list[tuple[ApiDefinition, list[str]]]:
    """Return the strongest deterministic API matches for one check point."""

    ranked: list[tuple[int, ApiDefinition, list[str]]] = []
    for definition in definitions:
        api_text = " ".join(
            filter(
                None,
                [
                    definition.name,
                    definition.path,
                    definition.summary,
                    definition.description,
                ],
            )
        )
        terms = _matching_terms(check_point_title, api_text)
        if terms:
            ranked.append((len(terms), definition, terms))
    ranked.sort(key=lambda item: (-item[0], item[1].path, item[1].method))
    if not ranked:
        return []
    best_score = ranked[0][0]
    return [
        (definition, terms)
        for score, definition, terms in ranked
        if score == best_score
    ][:3]


def _check_point_matches_api(
    check_point_title: str,
    definition: ApiDefinition,
) -> bool:
    api_text = " ".join(
        filter(
            None,
            [
                definition.name,
                definition.path,
                definition.summary,
                definition.description,
            ],
        )
    )
    return bool(_matching_terms(check_point_title, api_text))


def build_case_design_plan(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    include_api_ids: list[int] | None = None,
) -> CaseDesignPlanResponse:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    version = session.get(RequirementVersion, requirement.current_version_id)
    if version is None:
        raise ResourceConflictError("需求没有当前版本")
    full_scope = _requirement_scope(session, requirement)
    scope, excluded_requirements = _api_design_scope(full_scope)
    check_points = _scope_check_points(scope)
    definitions = list(session.scalars(
        select(ApiDefinition)
        .where(
            ApiDefinition.project_id == requirement.project_id,
            ApiDefinition.status == "ACTIVE",
        )
        .order_by(ApiDefinition.path, ApiDefinition.method)
        .limit(100)
    ).all())
    requirement_text = "\n".join(
        f"{item.title}\n{item_version.markdown_content}"
        for item, item_version in scope
    )
    api_matches: dict[int, set[str]] = {item.id: set() for item in definitions}
    api_reasons: dict[int, list[str]] = {item.id: [] for item in definitions}
    gaps: list[str] = []
    for point in check_points:
        matches = _best_api_matches(point.title, definitions)
        if not matches:
            gaps.append(f"{point.key} {point.title}：未找到语义匹配的接口，请人工确认")
            continue
        for definition, terms in matches:
            api_matches[definition.id].add(point.key)
            api_reasons[definition.id].append(
                f"匹配 {point.key} 的关键词：{'、'.join(terms)}"
            )
    matched_ids = {key for key, value in api_matches.items() if value}
    requested_ids = set(include_api_ids or [])
    for definition in definitions:
        if definition.id in requested_ids:
            matched_ids.add(definition.id)
            api_reasons[definition.id].append("从 API 定义页带入，等待与需求一起确认")
    if definitions and not matched_ids:
        for definition in definitions[:3]:
            matched_ids.add(definition.id)
            api_reasons[definition.id].append("需求描述未出现明确接口词，作为候选契约供人工确认")
    matched_definitions = [item for item in definitions if item.id in matched_ids]
    needs_auth = any(
        item.auth_info and not any(term in item.path.lower() for term in ("login", "auth"))
        for item in matched_definitions
    )
    if needs_auth:
        login_api = next(
            (
                item
                for item in definitions
                if any(term in item.path.lower() for term in ("login", "auth"))
            ),
            None,
        )
        if login_api is not None:
            matched_ids.add(login_api.id)
            api_reasons[login_api.id].append("所选业务接口需要认证，用于准备登录态")
    for core_api in [item for item in matched_definitions if item.method.upper() == "POST"]:
        resource_path = re.sub(r"/\{[^}]+\}$", "", core_api.path).rstrip("/")
        cleanup_api = next(
            (
                item
                for item in definitions
                if item.method.upper() == "DELETE"
                and re.sub(r"/\{[^}]+\}$", "", item.path).rstrip("/")
                == resource_path
            ),
            None,
        )
        if cleanup_api is not None:
            matched_ids.add(cleanup_api.id)
            api_reasons[cleanup_api.id].append(f"清理 {core_api.name} 产生的测试数据")
    recommended = [
        RecommendedApi(
            api_definition_id=item.id,
            name=item.name,
            method=item.method.upper(),
            path=item.path,
            role=_api_role(item, requirement_text),
            required=_api_role(item, requirement_text) == "核心操作",
            reason="；".join(dict.fromkeys(api_reasons[item.id])),
            check_point_keys=sorted(api_matches[item.id]),
            selected_by_default=item.id in matched_ids,
        )
        for item in definitions
        if item.id in matched_ids
    ]
    existing_case_count = int(session.scalar(
        select(func.count(RequirementCaseLink.id)).where(
            RequirementCaseLink.requirement_id == requirement.id,
            RequirementCaseLink.status == "ACTIVE",
        )
    ) or 0)
    if not definitions:
        gaps.append("项目尚未导入有效 API 定义，不能形成可执行的接口闭环")
    if not check_points:
        gaps.append("所选需求范围没有可进入 API 测试设计的自动化需求")
    return CaseDesignPlanResponse(
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        requirement_version_no=version.version_no,
        check_points=check_points,
        recommended_apis=recommended,
        gaps=gaps,
        existing_case_count=existing_case_count,
        source="RULE",
        scope_requirement_count=len(scope),
        scope_requirement_total=len(full_scope),
        excluded_requirements=excluded_requirements,
    )


def _active_api_definitions(session: Session, project_id: int) -> list[ApiDefinition]:
    return list(session.scalars(
        select(ApiDefinition)
        .where(
            ApiDefinition.project_id == project_id,
            ApiDefinition.status == "ACTIVE",
        )
        .order_by(ApiDefinition.path, ApiDefinition.method)
        .limit(100)
    ).all())


def _case_design_task_response(
    session: Session, task: AiCaseDesignTask, *, reused: bool = False
) -> CaseDesignTaskResponse:
    requirement = session.get(Requirement, task.requirement_id)
    version = session.get(RequirementVersion, task.requirement_version_id)
    if requirement is None or version is None:
        raise ResourceNotFoundError("AI 测试设计记录关联的需求或版本不存在")
    response = CaseDesignTaskResponse.model_validate(
        {
            **{column.name: getattr(task, column.name) for column in task.__table__.columns},
            "requirement_code": requirement.code,
            "requirement_title": requirement.title,
            "requirement_version_no": version.version_no,
            "reused": reused,
        }
    )
    return response


def _ai_case_design_validation_errors(
    value: object,
    allowed_api_ids: set[int],
    required_sources: dict[str, str] | None = None,
) -> list[str]:
    try:
        result = AiCaseDesignResult.model_validate(value)
    except ValidationError as exc:
        errors: list[str] = []
        for error in exc.errors()[:20]:
            path = ".".join(str(part) for part in error["loc"])
            error_type = str(error["type"])
            context = error.get("ctx") or {}
            if error_type == "missing":
                message = "缺少必填字段"
            elif error_type == "too_short":
                message = f"元素数量不能少于 {context.get('min_length', 1)}"
            elif error_type == "string_too_long":
                message = f"字符长度不能多于 {context.get('max_length', 100)}"
            elif error_type == "string_too_short":
                message = f"字符长度不能少于 {context.get('min_length', 1)}"
            else:
                message = str(error["msg"])
            errors.append(f"$.{path}: {message}")
        return errors
    point_keys = [item.key for item in result.check_points]
    api_ids = [item.api_definition_id for item in result.recommended_apis]
    errors: list[str] = []
    duplicate_point_keys = sorted(
        {key for key in point_keys if point_keys.count(key) > 1}
    )
    if duplicate_point_keys:
        errors.append(
            f"$.check_points: 检查点编号重复：{', '.join(duplicate_point_keys)}"
        )
    duplicate_api_ids = sorted({api_id for api_id in api_ids if api_ids.count(api_id) > 1})
    if duplicate_api_ids:
        errors.append(
            "$.recommended_apis: API 编号重复："
            + ", ".join(str(api_id) for api_id in duplicate_api_ids)
        )
    invalid_api_ids = sorted(set(api_ids) - allowed_api_ids)
    if invalid_api_ids:
        errors.append(
            "$.recommended_apis: 包含候选范围外的 API 编号："
            + ", ".join(str(api_id) for api_id in invalid_api_ids)
        )
    allowed_point_keys = set(point_keys)
    for index, point in enumerate(result.check_points):
        markers = [
            marker for marker in _EXCLUSIVE_CHECK_POINT_MARKERS if marker in point.title
        ]
        status_codes = set(re.findall(r"(?<!\d)[1-5]\d{2}(?!\d)", point.title))
        # “按用户名或显示名匹配”仍是同一次查询的一个可观察结果，不能仅凭
        # 连接词“或”判成多个场景。只有明确命中多个互斥输入分支或多个状态码
        # 时才要求模型拆分。
        if len(markers) > 1 or len(status_codes) > 1:
            errors.append(
                f"$.check_points[{index}].title: 合并了多个条件或期望，"
                "请拆成独立检查点"
            )
    # Missing leaf coverage and unknown checkpoint references are completeness
    # defects, not unsafe output.  The platform deterministically removes unknown
    # references and fills uncovered leaf requirements before compiling the plan.
    # Keeping them out of the hard Repair gate lets weaker but usable models
    # complete successfully without weakening API-id or schema safety checks.
    del allowed_point_keys, required_sources
    return errors


def _is_valid_ai_case_design(value: object, allowed_api_ids: set[int]) -> bool:
    return not _ai_case_design_validation_errors(value, allowed_api_ids)


def _complete_ai_case_design_result(
    scope: list[tuple[Requirement, RequirementVersion]],
    result: AiCaseDesignResult,
) -> tuple[AiCaseDesignResult, int, int]:
    """Salvage a schema-valid design with deterministic platform completion."""

    original_keys = {point.key for point in result.check_points}
    ignored_reference_count = 0
    cleaned_recommendations = []
    for recommendation in result.recommended_apis:
        known_keys = list(
            dict.fromkeys(
                key for key in recommendation.check_point_keys if key in original_keys
            )
        )
        ignored_reference_count += len(recommendation.check_point_keys) - len(known_keys)
        if known_keys:
            cleaned_recommendations.append(
                recommendation.model_copy(update={"check_point_keys": known_keys})
            )

    points = list(result.check_points)
    parent_ids = {
        item.parent_id for item, _ in scope if item.parent_id is not None
    }
    leaves = [(item, version) for item, version in scope if item.id not in parent_ids]
    represented_codes = {
        item.code
        for item, _ in leaves
        if any(
            item.code in point.source or item.title in point.source
            for point in points
        )
    }
    numeric_keys = [
        int(match.group(1))
        for point in points
        if (match := re.fullmatch(r"CP-(\d{2,3})", point.key)) is not None
    ]
    next_index = max(numeric_keys, default=0) + 1
    completed_count = 0
    completion_gaps: list[str] = []
    for requirement, version in leaves:
        if requirement.code in represented_codes:
            continue
        added_for_requirement = 0
        for extracted in _extract_check_points(requirement, version):
            if len(points) >= 100:
                break
            while f"CP-{next_index:02d}" in {point.key for point in points}:
                next_index += 1
            points.append(
                type(result.check_points[0])(
                    key=f"CP-{next_index:02d}",
                    title=extracted.title[:100],
                    source=extracted.source,
                )
            )
            next_index += 1
            completed_count += 1
            added_for_requirement += 1
        if added_for_requirement:
            completion_gaps.append(
                f"平台已补全模型遗漏的 {requirement.code}（{requirement.title}）检查点"
            )
        else:
            completion_gaps.append(
                f"{requirement.code}（{requirement.title}）未生成检查点，请人工补充"
            )
    if ignored_reference_count:
        completion_gaps.append(
            f"平台已忽略模型返回的 {ignored_reference_count} 个无效检查点引用"
        )
    return (
        result.model_copy(
            update={
                "check_points": points,
                "recommended_apis": cleaned_recommendations,
                "gaps": list(dict.fromkeys([*result.gaps, *completion_gaps])),
            }
        ),
        completed_count,
        ignored_reference_count,
    )


def _case_design_scope_batches(
    scope: list[tuple[Requirement, RequirementVersion]],
    *,
    leaf_batch_size: int = _AI_CASE_DESIGN_LEAF_BATCH_SIZE,
) -> list[list[tuple[Requirement, RequirementVersion]]]:
    """Split a large subtree by leaf requirements while retaining ancestor context."""

    if not scope:
        return []
    parent_ids = {item.parent_id for item, _ in scope if item.parent_id is not None}
    leaves = [item for item, _ in scope if item.id not in parent_ids]
    if len(leaves) <= leaf_batch_size:
        return [scope]
    by_id = {item.id: item for item, _ in scope}
    batches: list[list[tuple[Requirement, RequirementVersion]]] = []
    for offset in range(0, len(leaves), leaf_batch_size):
        included_ids = {item.id for item in leaves[offset : offset + leaf_batch_size]}
        for leaf in leaves[offset : offset + leaf_batch_size]:
            current = leaf
            visited: set[int] = set()
            while current.parent_id is not None and current.id not in visited:
                visited.add(current.id)
                parent = by_id.get(current.parent_id)
                if parent is None:
                    break
                included_ids.add(parent.id)
                current = parent
        batches.append(
            [(item, version) for item, version in scope if item.id in included_ids]
        )
    return batches


def _merge_ai_case_design_results(
    results: list[AiCaseDesignResult],
    *,
    extra_gaps: list[str] | None = None,
) -> AiCaseDesignResult:
    """Merge independently generated batches into one collision-free design result."""

    points = []
    decisions: dict[int, AiRecommendedApiDecision] = {}
    gaps: list[str] = []
    next_index = 1
    for result in results:
        key_map: dict[str, str] = {}
        for point in result.check_points:
            if len(points) >= 100:
                break
            key = f"CP-{next_index:02d}"
            next_index += 1
            key_map[point.key] = key
            points.append(point.model_copy(update={"key": key}))
        for recommendation in result.recommended_apis:
            mapped_keys = list(
                dict.fromkeys(
                    key_map[key]
                    for key in recommendation.check_point_keys
                    if key in key_map
                )
            )
            if not mapped_keys:
                continue
            current = decisions.get(recommendation.api_definition_id)
            if current is None:
                decisions[recommendation.api_definition_id] = recommendation.model_copy(
                    update={"check_point_keys": mapped_keys}
                )
                continue
            merged_reason = "；".join(
                dict.fromkeys([current.reason, recommendation.reason])
            )[:500]
            decisions[recommendation.api_definition_id] = current.model_copy(
                update={
                    "required": current.required or recommendation.required,
                    "reason": merged_reason,
                    "check_point_keys": list(
                        dict.fromkeys([*current.check_point_keys, *mapped_keys])
                    )[:100],
                }
            )
        gaps.extend(result.gaps)
    gaps.extend(extra_gaps or [])
    return AiCaseDesignResult(
        check_points=points,
        recommended_apis=list(decisions.values())[:30],
        gaps=list(dict.fromkeys(gaps))[:100],
    )


def _case_design_batch_api_ids(
    definitions: list[ApiDefinition],
    scope: list[tuple[Requirement, RequirementVersion]],
    included_api_ids: list[int],
) -> list[int]:
    """Keep each model batch focused without hiding explicitly selected APIs."""

    selected = list(dict.fromkeys(included_api_ids))
    for point in _scope_check_points(scope):
        for definition, _ in _best_api_matches(point.title, definitions):
            if definition.id not in selected:
                selected.append(definition.id)
    selected_definitions = [item for item in definitions if item.id in selected]
    if any(_api_requires_auth(item) for item in selected_definitions):
        login = next(
            (
                item
                for item in definitions
                if item.method.upper() == "POST"
                and any(
                    marker in item.path.lower()
                    for marker in ("login", "auth", "session")
                )
            ),
            None,
        )
        if login is not None and login.id not in selected:
            selected.append(login.id)
    if not selected:
        selected = [item.id for item in definitions]
    return selected[:30]


def _enrich_ai_case_design(
    session: Session,
    requirement: Requirement,
    version: RequirementVersion,
    scope: list[tuple[Requirement, RequirementVersion]],
    definitions: list[ApiDefinition],
    result: AiCaseDesignResult,
    included_api_ids: list[int],
    *,
    scope_requirement_total: int | None = None,
    excluded_requirements: list[CaseDesignScopeExclusion] | None = None,
    platform_completed_checkpoint_count: int = 0,
    ignored_ai_reference_count: int = 0,
) -> CaseDesignPlanResponse:
    by_id = {item.id: item for item in definitions}
    decisions = {
        item.api_definition_id: _ApiDesignDecision(
            api_definition_id=item.api_definition_id,
            role=item.role,
            required=item.required,
            reason=item.reason,
            check_point_keys=list(item.check_point_keys),
        )
        for item in result.recommended_apis
    }
    requirement_text = "\n".join(
        f"{item.title}\n{item_version.markdown_content}"
        for item, item_version in scope
    )
    for api_id in included_api_ids:
        if api_id not in decisions and api_id in by_id:
            definition = by_id[api_id]
            decisions[api_id] = _ApiDesignDecision(
                api_definition_id=api_id,
                role=_api_role(definition, requirement_text),
                required=True,
                reason="用户从 API 详情指定，作为本轮测试设计起点",
                check_point_keys=[],
            )
    compiler_gaps: list[str] = []
    for point in result.check_points:
        linked_definitions = [
            by_id[decision.api_definition_id]
            for decision in decisions.values()
            if point.key in decision.check_point_keys
            and decision.api_definition_id in by_id
        ]
        if any(
            _check_point_matches_api(point.title, definition)
            for definition in linked_definitions
        ):
            continue
        matches = _best_api_matches(point.title, definitions)
        if not matches:
            compiler_gaps.append(
                f"{point.key} {point.title}：平台未找到语义匹配的接口，请人工确认"
            )
            continue
        for definition, terms in matches:
            current = decisions.get(definition.id)
            if current is None:
                role = _api_role(definition, requirement_text)
                decisions[definition.id] = _ApiDesignDecision(
                    api_definition_id=definition.id,
                    role=role,
                    required=role == "核心操作",
                    reason=(
                        f"平台根据 {point.key} 补齐接口，匹配关键词："
                        f"{'、'.join(terms)}"
                    ),
                    check_point_keys=[point.key],
                )
                continue
            decisions[definition.id] = _ApiDesignDecision(
                api_definition_id=current.api_definition_id,
                role=current.role,
                required=current.required,
                reason=(
                    f"{current.reason}；平台补齐 {point.key}，匹配关键词："
                    f"{'、'.join(terms)}"
                ),
                check_point_keys=list(
                    dict.fromkeys([*current.check_point_keys, point.key])
                ),
            )
    selected_definitions = [by_id[api_id] for api_id in decisions if api_id in by_id]
    if any(
        item.auth_info and not any(term in item.path.lower() for term in ("login", "auth"))
        for item in selected_definitions
    ):
        login_api = next(
            (
                item
                for item in definitions
                if any(term in item.path.lower() for term in ("login", "auth", "session"))
            ),
            None,
        )
        if login_api is not None and login_api.id not in decisions:
            decisions[login_api.id] = _ApiDesignDecision(
                api_definition_id=login_api.id,
                role="前置准备",
                required=True,
                reason="平台根据接口认证要求补充登录或鉴权准备",
                check_point_keys=[],
            )
    for core_api in [item for item in selected_definitions if item.method.upper() == "POST"]:
        resource_path = re.sub(r"/\{[^}]+\}$", "", core_api.path).rstrip("/")
        cleanup_api = next(
            (
                item
                for item in definitions
                if item.method.upper() == "DELETE"
                and re.sub(r"/\{[^}]+\}$", "", item.path).rstrip("/") == resource_path
            ),
            None,
        )
        if cleanup_api is not None and cleanup_api.id not in decisions:
            decisions[cleanup_api.id] = _ApiDesignDecision(
                api_definition_id=cleanup_api.id,
                role="数据清理",
                required=False,
                reason=f"平台补充清理 {core_api.name} 产生的测试数据",
                check_point_keys=[],
            )
    recommended = [
        RecommendedApi(
            api_definition_id=definition.id,
            name=definition.name,
            method=definition.method.upper(),
            path=definition.path,
            role=decision.role,
            required=decision.required,
            reason=decision.reason,
            check_point_keys=decision.check_point_keys,
            selected_by_default=True,
        )
        for definition in definitions
        if (decision := decisions.get(definition.id)) is not None
    ]
    scope_requirement_ids = [item.id for item, _ in scope]
    existing_case_count = int(session.scalar(
        select(func.count(RequirementCaseLink.id)).where(
            RequirementCaseLink.requirement_id.in_(scope_requirement_ids),
            RequirementCaseLink.status == "ACTIVE",
        )
    ) or 0)
    return CaseDesignPlanResponse(
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        requirement_version_no=version.version_no,
        check_points=[
            TestCheckPoint.model_validate(item.model_dump())
            for item in result.check_points
        ],
        recommended_apis=recommended,
        gaps=list(dict.fromkeys([*result.gaps, *compiler_gaps])),
        existing_case_count=existing_case_count,
        source="AI",
        scope_requirement_count=len(scope),
        scope_requirement_total=scope_requirement_total or len(scope),
        excluded_requirements=excluded_requirements or [],
        platform_completed_checkpoint_count=platform_completed_checkpoint_count,
        ignored_ai_reference_count=ignored_ai_reference_count,
    )


def create_case_design_task(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: CaseDesignTaskCreate,
) -> CaseDesignTaskResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_writable(session, user, requirement)
    ensure_builtin_prompts(session)
    version = session.get(RequirementVersion, requirement.current_version_id)
    if version is None:
        raise ResourceConflictError("需求没有当前版本")
    prompt = session.get(PromptDefinition, payload.prompt_id)
    if (
        prompt is None
        or not prompt.enabled
        or prompt.task_type != AiTaskType.API_TEST_DESIGN.value
    ):
        raise ResourceConflictError("请选择可用的 AI 测试设计 Prompt")
    definitions = _active_api_definitions(session, requirement.project_id)
    allowed_ids = {item.id for item in definitions}
    included_api_ids = list(dict.fromkeys(payload.include_api_ids))
    if not set(included_api_ids).issubset(allowed_ids):
        raise ResourceConflictError("指定的 API 不存在、已停用或不属于当前项目")
    fingerprint = _api_contract_fingerprint(definitions, included_api_ids)
    active = session.scalar(
        select(AiCaseDesignTask)
        .where(
            AiCaseDesignTask.requirement_id == requirement_id,
            AiCaseDesignTask.status.in_(["QUEUED", "RUNNING"]),
        )
        .order_by(AiCaseDesignTask.id.desc())
    )
    if active is not None:
        if (
            active.api_contract_fingerprint == fingerprint
            and active.prompt_id == payload.prompt_id
            and active.requirement_version_id == version.id
        ):
            return _case_design_task_response(session, active, reused=True)
        raise ResourceConflictError("当前需求已有不同输入的 AI 测试设计任务，请稍后再试")
    if not payload.force_refresh:
        cached = session.scalar(
            select(AiCaseDesignTask)
            .where(
                AiCaseDesignTask.requirement_id == requirement_id,
                AiCaseDesignTask.requirement_version_id == version.id,
                AiCaseDesignTask.prompt_id == payload.prompt_id,
                AiCaseDesignTask.api_contract_fingerprint == fingerprint,
                AiCaseDesignTask.status == "SUCCEEDED",
                AiCaseDesignTask.source == "AI",
            )
            .order_by(AiCaseDesignTask.id.desc())
        )
        if cached is not None:
            return _case_design_task_response(session, cached, reused=True)
    task = AiCaseDesignTask(
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        prompt_id=payload.prompt_id,
        status="QUEUED",
        api_contract_fingerprint=fingerprint,
        included_api_definition_ids=included_api_ids,
        created_by=user.id,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return _case_design_task_response(session, task)


def list_case_design_tasks(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    page: int = 1,
    page_size: int = 10,
) -> CaseDesignTaskListResponse:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    total = int(session.scalar(
        select(func.count(AiCaseDesignTask.id)).where(
            AiCaseDesignTask.requirement_id == requirement_id
        )
    ) or 0)
    active_count = int(session.scalar(
        select(func.count(AiCaseDesignTask.id)).where(
            AiCaseDesignTask.requirement_id == requirement_id,
            AiCaseDesignTask.status.in_(["QUEUED", "RUNNING"]),
        )
    ) or 0)
    items = list(session.scalars(
        select(AiCaseDesignTask)
        .where(AiCaseDesignTask.requirement_id == requirement_id)
        .order_by(AiCaseDesignTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all())
    return CaseDesignTaskListResponse(
        items=[_case_design_task_response(session, item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        active_count=active_count,
    )


def list_project_case_design_tasks(
    session: Session,
    user: CurrentUser,
    project_id: int,
    page: int = 1,
    page_size: int = 10,
) -> CaseDesignTaskListResponse:
    get_project(session, user, project_id)
    filters = (AiCaseDesignTask.project_id == project_id,)
    total = int(session.scalar(
        select(func.count(AiCaseDesignTask.id)).where(*filters)
    ) or 0)
    active_count = int(session.scalar(
        select(func.count(AiCaseDesignTask.id)).where(
            *filters,
            AiCaseDesignTask.status.in_(["QUEUED", "RUNNING"]),
        )
    ) or 0)
    items = list(session.scalars(
        select(AiCaseDesignTask)
        .where(*filters)
        .order_by(AiCaseDesignTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all())
    return CaseDesignTaskListResponse(
        items=[_case_design_task_response(session, item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        active_count=active_count,
    )


def delete_case_design_task(
    session: Session,
    user: CurrentUser,
    task_id: int,
) -> None:
    task = session.get(AiCaseDesignTask, task_id)
    if task is None:
        raise ResourceNotFoundError("AI 测试设计记录不存在")
    requirement = _get_requirement(session, task.requirement_id)
    _ensure_writable(session, user, requirement)
    if task.status in {"QUEUED", "RUNNING"}:
        raise ResourceConflictError("正在处理的 AI 测试设计不能删除，请等待任务结束")

    referenced_plans = [
        *session.scalars(
            select(AiCaseGenerationTask.coverage_plan).where(
                AiCaseGenerationTask.requirement_id == task.requirement_id
            )
        ).all(),
        *session.scalars(
            select(AiCaseGeneration.coverage_plan).where(
                AiCaseGeneration.requirement_id == task.requirement_id
            )
        ).all(),
    ]
    if any(
        isinstance(plan, dict) and plan.get("design_task_id") == task.id
        for plan in referenced_plans
    ):
        raise ResourceConflictError("该设计已被用例生成记录引用，不能删除")

    session.delete(task)
    session.commit()


def process_case_design_task(
    session_factory: Callable[[], Session], task_id: int, user: CurrentUser
) -> None:
    with session_factory() as session:
        task = session.get(AiCaseDesignTask, task_id)
        if task is None or task.status not in {"QUEUED", "RUNNING"}:
            return
        task.status = "RUNNING"
        task.started_at = utc_now_naive()
        session.commit()
        try:
            requirement = _get_requirement(session, task.requirement_id)
            version = session.get(RequirementVersion, task.requirement_version_id)
            if version is None or requirement.current_version_id != version.id:
                raise ResourceConflictError("需求版本已经变化，请重新发起 AI 测试设计")
            full_scope = _requirement_scope(session, requirement)
            scope, excluded_requirements = _api_design_scope(full_scope)
            if not _scope_check_points(scope):
                fallback = build_case_design_plan(
                    session,
                    user,
                    task.requirement_id,
                    include_api_ids=task.included_api_definition_ids,
                ).model_copy(update={"source": "RULE_FALLBACK"})
                task.source = "RULE_FALLBACK"
                task.plan = fallback.model_dump(mode="json")
                task.error_message = "当前需求已分流到其他验证能力，不需要调用 API 设计模型"
                task.status = "SUCCEEDED"
                task.completed_at = utc_now_naive()
                session.commit()
                return
            definitions = _active_api_definitions(session, requirement.project_id)
            candidate_api_ids = list(dict.fromkeys([
                *task.included_api_definition_ids,
                *(item.id for item in definitions),
            ]))[:30]
            del candidate_api_ids
            batch_results: list[AiCaseDesignResult] = []
            batch_gaps: list[str] = []
            successful_call_ids: list[int] = []
            batches = _case_design_scope_batches(scope)
            for batch_index, batch_scope in enumerate(batches, start=1):
                batch_api_ids = _case_design_batch_api_ids(
                    definitions,
                    batch_scope,
                    task.included_api_definition_ids,
                )
                api_context, _, _ = _project_api_generation_context(
                    session, requirement.project_id, batch_api_ids
                )
                scope_payload = [
                    {
                        "requirement_code": item.code,
                        "title": item.title,
                        "requirement_type": item.type,
                        "verification_type": item.verification_type,
                        "automation_readiness": item.automation_readiness,
                        "version_no": item_version.version_no,
                        "content": item_version.markdown_content,
                    }
                    for item, item_version in batch_scope
                ]
                parent_ids = {
                    item.parent_id
                    for item, _ in batch_scope
                    if item.parent_id is not None
                }
                required_sources = {
                    item.code: item.title
                    for item, _ in batch_scope
                    if item.id not in parent_ids
                }
                try:
                    ai_result = generate(
                        session,
                        user,
                        AiGenerateRequest(
                            project_id=requirement.project_id,
                            task_type=AiTaskType.API_TEST_DESIGN,
                            prompt_id=task.prompt_id,
                            variables={
                                "requirement_scope": json.dumps(
                                    scope_payload, ensure_ascii=False
                                ),
                                "api_definitions": api_context,
                                "specified_api_ids": ",".join(
                                    str(item)
                                    for item in task.included_api_definition_ids
                                )
                                if task.included_api_definition_ids
                                else "无",
                            },
                            entity_type="REQUIREMENT",
                            entity_id=str(requirement.id),
                        ),
                        result_validator=(
                            lambda value,
                            allowed_ids=set(batch_api_ids),
                            required=required_sources: (
                                _ai_case_design_validation_errors(
                                    value, allowed_ids, required
                                )
                            )
                        ),
                        timeout_seconds=(
                            _AI_CASE_DESIGN_TIMEOUT_SECONDS
                            if len(batches) == 1
                            else _AI_CASE_DESIGN_BATCH_TIMEOUT_SECONDS
                        ),
                        trusted_system_instruction=_AI_CASE_DESIGN_INSTRUCTIONS,
                    )
                except AppError as exc:
                    batch_gaps.append(
                        f"第 {batch_index} 批 AI 分析未返回，平台已按规则补全：{exc.message}"
                    )
                    continue
                successful_call_ids.append(ai_result.ai_call_id)
                batch_results.append(
                    AiCaseDesignResult.model_validate(ai_result.parsed_result)
                )
            if not batch_results:
                raise ResourceConflictError(
                    batch_gaps[0] if batch_gaps else "模型未返回可用的测试设计"
                )
            result = _merge_ai_case_design_results(
                batch_results,
                extra_gaps=batch_gaps,
            )
            result, completed_count, ignored_reference_count = (
                _complete_ai_case_design_result(scope, result)
            )
            plan = _enrich_ai_case_design(
                session,
                requirement,
                version,
                scope,
                definitions,
                result,
                task.included_api_definition_ids,
                scope_requirement_total=len(full_scope),
                excluded_requirements=excluded_requirements,
                platform_completed_checkpoint_count=completed_count,
                ignored_ai_reference_count=ignored_reference_count,
            )
            task.ai_call_id = successful_call_ids[-1]
            task.source = "AI"
            task.plan = plan.model_dump(mode="json")
            task.error_message = None
        except Exception as exc:
            logger.exception(
                "AI_CASE_DESIGN_FALLBACK",
                extra={"task_id": task_id, "error_type": type(exc).__name__},
            )
            session.rollback()
            task = session.get(AiCaseDesignTask, task_id)
            if task is None:
                return
            try:
                fallback = build_case_design_plan(
                    session,
                    user,
                    task.requirement_id,
                    include_api_ids=task.included_api_definition_ids,
                ).model_copy(update={"source": "RULE_FALLBACK"})
                task.source = "RULE_FALLBACK"
                task.plan = fallback.model_dump(mode="json")
                message = exc.message if isinstance(exc, AppError) else "模型调用或输出校验失败"
                task.error_message = redact_text(f"AI 推荐未完成，已使用规则候选：{message}")[:500]
            except Exception:
                session.rollback()
                task = session.get(AiCaseDesignTask, task_id)
                if task is None:
                    return
                task.status = "FAILED"
                task.error_message = "AI 测试设计失败，且无法生成规则候选"
                task.completed_at = utc_now_naive()
                session.commit()
                logger.exception("AI_CASE_DESIGN_TASK_FAILED", extra={"task_id": task_id})
                return
        task.status = "SUCCEEDED"
        task.completed_at = utc_now_naive()
        session.commit()


def _ensure_v1_api_retry_policy(content: SuggestedCase) -> None:
    if content.case_type == CaseType.API and content.request is not None:
        ensure_v1_api_step_retry_limit(content.request.retry_policy.max_retries)


def validate_ai_assertion_definition(
    session: Session, project_id: int, assertion: Assertion
) -> None:
    """Validate one formal AI assertion against the project's active Prompt contract."""

    if assertion.kind.value != "AI_SEMANTIC":
        return
    prompt = session.get(PromptDefinition, assertion.prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceConflictError(f"AI 断言 {assertion.name} 引用的 Prompt 不存在或已停用")
    if prompt.task_type != "AI_ASSERTION":
        raise ResourceConflictError(
            f"AI 断言 {assertion.name} 的 Prompt 必须是 AI_ASSERTION 任务"
        )
    version = session.get(PromptVersion, prompt.current_version_id)
    if version is None:
        raise ResourceConflictError(f"AI 断言 {assertion.name} 引用的 Prompt 没有当前版本")
    if version.output_schema_id is None:
        raise ResourceConflictError(
            f"AI 断言 {assertion.name} 的 Prompt 必须绑定 Output Schema"
        )
    output_schema = session.get(OutputSchema, version.output_schema_id)
    if output_schema is None or not output_schema.enabled:
        raise ResourceConflictError(f"AI 断言 {assertion.name} 的 Output Schema 不存在或已停用")
    properties = output_schema.schema_json.get("properties", {})
    required = set(output_schema.schema_json.get("required", []))
    required_fields = {"passed", "confidence", "reason"}
    schema_types = {"passed": "boolean", "confidence": "number", "reason": "string"}
    if (
        not required_fields.issubset(required)
        or not required_fields.issubset(properties)
        or any(
            not isinstance(properties.get(field), dict)
            or properties[field].get("type") != expected_type
            for field, expected_type in schema_types.items()
        )
    ):
        raise ResourceConflictError(
            f"AI 断言 {assertion.name} 的 Output Schema 必须包含 passed/confidence/reason"
        )


def _validate_assertions(session: Session, project_id: int, content: SuggestedCase) -> None:
    if not content.assertions:
        return
    if content.case_type.value != "API":
        raise ResourceConflictError("只有 API 用例可以配置 API 断言")
    for assertion in content.assertions:
        validate_ai_assertion_definition(session, project_id, assertion)


def _validate_data_source(session: Session, project_id: int, content: SuggestedCase) -> None:
    data_source = content.data_source
    if data_source is None:
        return
    if content.case_type.value != "API":
        raise ResourceConflictError("只有 API 用例可以绑定数据集")
    dataset = session.get(Dataset, data_source.dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise ResourceConflictError("数据集不存在或不属于当前项目")
    if dataset.status != "ACTIVE":
        raise ResourceConflictError("已归档数据集不能绑定到用例")
    version_id = data_source.dataset_version_id or dataset.current_version_id
    version = session.get(DatasetVersion, version_id) if version_id else None
    if version is None or version.dataset_id != dataset.id:
        raise ResourceConflictError("数据集版本不存在或不属于该数据集")
    validate_iteration_mapping(
        [str(item["name"]) for item in version.columns],
        data_source.column_mapping,
        data_source.prefix,
    )
    if data_source.dataset_version_id is None:
        data_source.dataset_version_id = version.id


def _validate_cleanup_configs(session: Session, project_id: int, content: SuggestedCase) -> None:
    if content.case_type.value != "API" and (content.cleanup or any(
        getattr(action, "cleanup", None) is not None
        for action in [*content.pre_actions, *content.post_actions]
    )):
        raise ResourceConflictError("只有 API 用例可以配置 Cleanup")
    cleanup_ids = {item.cleanup_id for item in content.cleanup if item.cleanup_id}
    for cleanup in content.cleanup:
        validate_cleanup_scope(session, project_id, cleanup)
    for action in [*content.pre_actions, *content.post_actions]:
        if not hasattr(action, "cleanup"):
            continue
        inline_cleanup = getattr(action, "cleanup", None)
        if inline_cleanup is not None:
            validate_cleanup_scope(session, project_id, inline_cleanup)
        cleanup_ref = getattr(action, "cleanup_ref", None)
        if cleanup_ref and cleanup_ref not in cleanup_ids:
            raise ResourceConflictError(f"REGISTER_RESOURCE 引用的 Cleanup 不存在：{cleanup_ref}")


def _get_requirement(session: Session, requirement_id: int) -> Requirement:
    requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        raise ResourceNotFoundError("需求不存在")
    return requirement


def _ensure_writable(session: Session, user: CurrentUser, requirement: Requirement) -> None:
    project = get_project(session, user, requirement.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能生成或审核用例建议")
    if requirement.status != "ACTIVE":
        raise ResourceConflictError("归档需求不能生成或审核用例建议")


def _get_suggestion(session: Session, suggestion_id: int) -> AiCaseSuggestion:
    suggestion = session.get(AiCaseSuggestion, suggestion_id)
    if suggestion is None:
        raise ResourceNotFoundError("AI 用例建议不存在")
    return suggestion


def _ai_confidence_by_sequence(
    call: AiCallLog,
    compiled_intent_keys: list[str] | None = None,
) -> dict[int, float]:
    """Expose only confidence values explicitly returned by the model."""

    parsed = call.parsed_result
    if not isinstance(parsed, dict):
        return {}
    items = (
        parsed.get("intents")
        if isinstance(parsed.get("intents"), list)
        else parsed.get("cases")
    )
    if not isinstance(items, list):
        return {}
    if compiled_intent_keys is not None and isinstance(parsed.get("intents"), list):
        confidence_by_key = {
            str(item.get("case_key")): item.get("confidence")
            for item in items
            if isinstance(item, dict) and isinstance(item.get("case_key"), str)
        }
        items = [
            {"confidence": confidence_by_key.get(key)}
            for key in compiled_intent_keys
        ]
    values: dict[int, float] = {}
    for sequence_no, item in enumerate(items, start=1):
        if not isinstance(item, dict) or "confidence" not in item:
            continue
        value = item["confidence"]
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        normalized = float(value)
        if 0 <= normalized <= 1:
            values[sequence_no] = normalized
    return values


def _generation_response(session: Session, generation: AiCaseGeneration) -> CaseGenerationResponse:
    suggestions = list(
        session.scalars(
            select(AiCaseSuggestion)
            .where(AiCaseSuggestion.generation_id == generation.id)
            .order_by(AiCaseSuggestion.sequence_no)
        ).all()
    )
    call = session.get(AiCallLog, generation.ai_call_id)
    if call is None:
        raise ResourceConflictError("AI 用例生成审计记录不存在")
    requirement = session.get(Requirement, generation.requirement_id)
    requirement_version = session.get(RequirementVersion, generation.requirement_version_id)
    prompt_version = session.get(PromptVersion, call.prompt_version_id)
    if requirement is None or requirement_version is None or prompt_version is None:
        raise ResourceConflictError("AI 用例生成来源记录不完整")
    prompt = session.get(PromptDefinition, prompt_version.prompt_id)
    if prompt is None:
        raise ResourceConflictError("AI 用例生成提示词记录不存在")
    output_schema = (
        session.get(OutputSchema, call.output_schema_id)
        if call.output_schema_id is not None
        else None
    )
    return CaseGenerationResponse(
        id=generation.id,
        project_id=generation.project_id,
        requirement_id=generation.requirement_id,
        requirement_code=requirement.code,
        requirement_title=requirement.title,
        requirement_version_id=generation.requirement_version_id,
        requirement_version_no=requirement_version.version_no,
        ai_call_id=generation.ai_call_id,
        additional_instructions=generation.additional_instructions,
        raw_response=redact_text(generation.raw_response),
        actual_model=call.actual_model,
        prompt_version_id=call.prompt_version_id,
        prompt_name=prompt.name,
        prompt_version_no=prompt_version.version_no,
        output_schema_id=call.output_schema_id,
        output_schema_name=output_schema.name if output_schema is not None else None,
        output_schema_version_no=(
            output_schema.version_no if output_schema is not None else None
        ),
        ai_confidence_by_sequence=_ai_confidence_by_sequence(
            call,
            generation.coverage_plan.get("compiled_intent_keys")
            if isinstance(generation.coverage_plan, dict)
            and isinstance(generation.coverage_plan.get("compiled_intent_keys"), list)
            else None,
        ),
        fallback_used=call.fallback_used,
        repair_used=call.repair_used,
        selected_api_definition_ids=generation.selected_api_definition_ids,
        coverage_plan=generation.coverage_plan,
        structured_result=generation.structured_result,
        created_by=generation.created_by,
        created_at=generation.created_at,
        suggestions=[CaseSuggestionResponse.model_validate(item) for item in suggestions],
    )


def generate_case_suggestions(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: CaseGenerationRequest,
) -> CaseGenerationResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_writable(session, user, requirement)
    version = session.get(RequirementVersion, requirement.current_version_id)
    if version is None:
        raise ResourceConflictError("需求没有当前版本")
    plan = build_case_design_plan(session, user, requirement_id)
    return _generate_case_suggestions_for_version(
        session, user, requirement, version, payload, coverage_plan=plan.model_dump(mode="json")
    )


def _generate_case_suggestions_for_version(
    session: Session,
    user: CurrentUser,
    requirement: Requirement,
    version: RequirementVersion,
    payload: CaseGenerationRequest,
    *,
    coverage_plan: dict | None = None,
) -> CaseGenerationResponse:
    selected_api_ids = list(dict.fromkeys(payload.selected_api_definition_ids or []))
    project_api_count = int(session.scalar(
        select(func.count(ApiDefinition.id)).where(
            ApiDefinition.project_id == requirement.project_id,
            ApiDefinition.status == "ACTIVE",
        )
    ) or 0)
    if (
        payload.selected_api_definition_ids is not None
        and not selected_api_ids
        and project_api_count
    ):
        raise ResourceConflictError("请至少选择一个用于测试设计的 API")
    api_context, allowed_api_requests, api_contracts = _project_api_generation_context(
        session,
        requirement.project_id,
        selected_api_ids if payload.selected_api_definition_ids is not None else None,
    )
    secret_context, available_secret_names, has_login_password_secret = (
        _project_secret_generation_context(session, requirement.project_id)
    )
    runtime_context, available_runtime_names, runtime_environment_ready = (
        _project_runtime_generation_context(
            session, requirement.project_id
        )
    )
    if payload.selected_api_definition_ids is not None and len(allowed_api_requests) != len(
        selected_api_ids
    ):
        raise ResourceConflictError("所选 API 不存在、已停用或不属于当前项目")
    if allowed_api_requests and not runtime_environment_ready:
        raise ResourceConflictError(
            "请先在项目设置中配置一个已启用且包含 Base URL 的默认运行环境"
        )
    if any(
        bool(contract.get("requires_sensitive_input"))
        for contract in api_contracts.values()
    ) and not has_login_password_secret:
        raise ResourceConflictError(
            "所选 API 包含登录或其他敏感输入，请先在项目设置中配置测试凭据 Secret"
        )
    effective_plan = coverage_plan or build_case_design_plan(
        session, user, requirement.id
    ).model_dump(mode="json")
    scope_payload = [
        {
            "requirement_code": item.code,
            "title": item.title,
            "requirement_type": item.type,
            "version_no": item_version.version_no,
            "content": item_version.markdown_content,
        }
        for item, item_version in _requirement_scope(session, requirement)
    ]
    check_points = effective_plan.get("check_points", [])
    allowed_checkpoint_keys = {
        str(item["key"]).strip().upper()
        for item in check_points
        if isinstance(item, dict) and item.get("key")
    } or {"CP-01"}
    definitions = [
        item
        for item in _active_api_definitions(session, requirement.project_id)
        if payload.selected_api_definition_ids is None or item.id in selected_api_ids
    ]
    if not definitions:
        raise ResourceConflictError("当前需求范围没有已确认的 API，无法编译可执行用例")
    coverage_instruction = "\n".join(
        f"- {item['key']} [{item.get('source', requirement.code)}]：{item['title']}"
        for item in check_points
        if isinstance(item, dict)
    )
    api_context_instruction = (
        "项目可用 API 定义如下。只能选择其中的 api_definition_id 和契约字段：\n"
        f"{api_context}"
        if allowed_api_requests
        else "当前项目没有可用的 API 定义；请根据需求生成建议，并明确指出待人工补充的请求配置。"
    )
    ai_result = generate(
        session,
        user,
        AiGenerateRequest(
            project_id=requirement.project_id,
            task_type="API_CASE_GENERATE",
            prompt_id=payload.prompt_id,
            variables={
                "requirement": json.dumps(scope_payload, ensure_ascii=False),
                "requirement_title": requirement.title,
                "requirement_code": requirement.code,
                "additional_instructions": (
                    f"{payload.additional_instructions or '无'}\n"
                    f"{api_context_instruction}\n"
                    "项目当前启用的 Secret 元数据如下（这里只包含名称和类型，不包含真实值）：\n"
                    f"{secret_context}\n"
                    "默认运行环境及可用变量名称如下：\n"
                    f"{runtime_context}\n"
                    "Secret 与 Runtime 的具体引用由平台编译器生成，意图中不要输出凭据值。\n"
                    "本次需要覆盖的验收检查点如下：\n"
                    f"{coverage_instruction or '- CP-01：当前需求'}\n"
                    "每条意图的 checkpoint_keys 必须列出它实际覆盖的检查点，"
                    "不要标记未验证的检查点。\n"
                    f"{_AI_CASE_GENERATION_INSTRUCTIONS}"
                ),
            },
            entity_type="REQUIREMENT",
            entity_id=str(requirement.id),
        ),
        result_validator=lambda value: _generated_case_intent_validation_errors(
            value,
        ),
        timeout_seconds=_AI_CASE_GENERATION_TIMEOUT_SECONDS,
    )
    try:
        intent_result = ApiCaseIntentResult.model_validate(ai_result.parsed_result)
    except ValidationError as exc:
        raise ResourceConflictError("AI 输出不符合用例意图结构") from exc
    try:
        secret_metadata = json.loads(secret_context)
    except json.JSONDecodeError:
        secret_metadata = []
    compilable_intents = []
    semantic_gaps: list[str] = []
    for intent in intent_result.intents:
        known_checkpoint_keys = [
            key for key in intent.checkpoint_keys if key in allowed_checkpoint_keys
        ]
        unknown_checkpoint_keys = sorted(
            set(intent.checkpoint_keys) - allowed_checkpoint_keys
        )
        if unknown_checkpoint_keys:
            semantic_gaps.append(
                f"编译跳过检查点引用：{intent.case_key}「{intent.title}」："
                + ", ".join(unknown_checkpoint_keys)
            )
        if not known_checkpoint_keys:
            continue
        compilable_intents.append(
            intent.model_copy(update={"checkpoint_keys": known_checkpoint_keys})
        )
    compilation = compile_case_intents(
        compilable_intents,
        definitions,
        secret_metadata if isinstance(secret_metadata, list) else [],
    )
    if compilation.result is None:
        detail = (
            compilation.diagnostics[0].display()
            if compilation.diagnostics
            else semantic_gaps[0]
            if semantic_gaps
            else "无可编译意图"
        )
        raise ResourceConflictError(f"平台未能编译出可执行用例：{detail}")
    ready_cases: list[SuggestedCase] = []
    ready_intent_keys: list[str] = []
    readiness_gaps: list[str] = []
    coverage_gaps: list[str] = []
    checkpoint_titles = {
        str(item.get("key", "")).strip().upper(): str(item.get("title", "")).strip()
        for item in check_points
        if isinstance(item, dict) and item.get("key")
    }
    for intent_key, compiled_case in zip(
        compilation.compiled_case_keys,
        compilation.result.cases,
        strict=True,
    ):
        readiness_issues = _case_execution_ready_issues(
            compiled_case,
            api_contracts,
            available_secret_names,
            available_runtime_names,
        )
        if readiness_issues:
            readiness_gaps.append(
                f"编译跳过：{intent_key}「{compiled_case.title}」："
                + "；".join(readiness_issues)
            )
            continue
        invalid_coverage: dict[str, list[str]] = {}
        for tag in compiled_case.tags:
            if not tag.startswith("coverage:"):
                continue
            checkpoint_key = tag.removeprefix("coverage:").strip().upper()
            issues = _semantic_coverage_issues(
                compiled_case,
                checkpoint_titles.get(checkpoint_key, ""),
            )
            if issues:
                invalid_coverage[checkpoint_key] = issues
        if invalid_coverage:
            invalid_keys = set(invalid_coverage)
            compiled_case = compiled_case.model_copy(
                update={
                    "tags": [
                        tag
                        for tag in compiled_case.tags
                        if not (
                            tag.startswith("coverage:")
                            and tag.removeprefix("coverage:").strip().upper()
                            in invalid_keys
                        )
                    ]
                }
            )
            for checkpoint_key, issues in invalid_coverage.items():
                coverage_gaps.append(
                    f"覆盖未成立：{intent_key} 未能证明 {checkpoint_key}："
                    + "；".join(issues)
                )
        ready_cases.append(compiled_case)
        ready_intent_keys.append(intent_key)
    if not ready_cases:
        details = [item.display() for item in compilation.diagnostics]
        details.extend(readiness_gaps)
        raise ResourceConflictError(
            "平台未能编译出可执行用例："
            + (details[0] if details else "契约闭环不完整")
        )
    result = CaseGenerationResult(cases=ready_cases)
    effective_plan = deepcopy(effective_plan)
    gaps = effective_plan.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    gaps.extend(intent_result.gaps)
    gaps.extend(semantic_gaps)
    gaps.extend(f"编译跳过：{item.display()}" for item in compilation.diagnostics)
    gaps.extend(readiness_gaps)
    gaps.extend(coverage_gaps)
    effective_plan["gaps"] = list(dict.fromkeys(str(item) for item in gaps))
    effective_plan["intent_count"] = len(intent_result.intents)
    effective_plan["compiled_count"] = len(result.cases)
    effective_plan["compile_skipped_count"] = (
        len(intent_result.intents)
        - len(compilable_intents)
        + len(compilation.diagnostics)
        + len(readiness_gaps)
    )
    effective_plan["compiled_intent_keys"] = ready_intent_keys
    effective_plan["compiler_version"] = CASE_COMPILER_VERSION
    call_log = session.get(AiCallLog, ai_result.ai_call_id)
    if call_log is None:
        raise ResourceConflictError("AI 调用记录不存在")
    generation = AiCaseGeneration(
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        ai_call_id=call_log.id,
        additional_instructions=payload.additional_instructions,
        selected_api_definition_ids=selected_api_ids,
        coverage_plan=effective_plan,
        raw_response=call_log.raw_response,
        structured_result=result.model_dump(mode="json"),
        created_by=user.id,
    )
    session.add(generation)
    session.flush()
    for sequence_no, suggested_case in enumerate(result.cases, start=1):
        session.add(
            AiCaseSuggestion(
                generation_id=generation.id,
                sequence_no=sequence_no,
                status=SuggestionStatus.DRAFT.value,
                structured_result=suggested_case.model_dump(mode="json"),
            )
        )
    session.commit()
    session.refresh(generation)
    return _generation_response(session, generation)


def create_case_generation_task(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: CaseGenerationRequest,
) -> CaseGenerationTaskResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_writable(session, user, requirement)
    version = session.get(RequirementVersion, requirement.current_version_id)
    if version is None:
        raise ResourceConflictError("需求没有当前版本")
    active_task = session.scalar(
        select(AiCaseGenerationTask)
        .where(
            AiCaseGenerationTask.requirement_id == requirement_id,
            AiCaseGenerationTask.status.in_([
                CaseGenerationTaskStatus.QUEUED.value,
                CaseGenerationTaskStatus.RUNNING.value,
            ]),
        )
        .order_by(AiCaseGenerationTask.id.desc())
    )
    if active_task is not None:
        raise ResourceConflictError("当前需求已有进行中的 AI 生成任务")
    if payload.design_task_id is not None:
        design_task = session.get(AiCaseDesignTask, payload.design_task_id)
        if (
            design_task is None
            or design_task.requirement_id != requirement.id
            or design_task.requirement_version_id != version.id
            or design_task.project_id != requirement.project_id
            or design_task.status != "SUCCEEDED"
            or design_task.plan is None
        ):
            raise ResourceConflictError("AI 测试设计记录无效或需求版本已变化，请重新分析")
        plan = CaseDesignPlanResponse.model_validate(design_task.plan)
    else:
        design_task = None
        plan = build_case_design_plan(session, user, requirement_id)
    selected_api_ids = list(dict.fromkeys(
        payload.selected_api_definition_ids
        if payload.selected_api_definition_ids is not None
        else [
            item.api_definition_id
            for item in plan.recommended_apis
            if item.selected_by_default
        ]
    ))
    project_api_count = int(session.scalar(
        select(func.count(ApiDefinition.id)).where(
            ApiDefinition.project_id == requirement.project_id,
            ApiDefinition.status == "ACTIVE",
        )
    ) or 0)
    if not selected_api_ids and project_api_count:
        raise ResourceConflictError("当前需求没有可用于生成的 API，请先导入或选择 API")
    active_api_count = int(session.scalar(
        select(func.count(ApiDefinition.id)).where(
            ApiDefinition.project_id == requirement.project_id,
            ApiDefinition.status == "ACTIVE",
            ApiDefinition.id.in_(selected_api_ids),
        )
    ) or 0)
    if active_api_count != len(selected_api_ids):
        raise ResourceConflictError("所选 API 不存在、已停用或不属于当前项目")
    if design_task is not None:
        planned_api_ids = {item.api_definition_id for item in plan.recommended_apis}
        if not set(selected_api_ids).issubset(planned_api_ids):
            raise ResourceConflictError("所选 API 不在本次 AI 测试设计结果中，请重新分析")
        required_api_ids = {
            item.api_definition_id for item in plan.recommended_apis if item.required
        }
        if not required_api_ids.issubset(selected_api_ids):
            raise ResourceConflictError("核心接口不能取消，请保留后再创建用例生成任务")
    coverage_plan = plan.model_dump(mode="json")
    if design_task is not None:
        coverage_plan["design_task_id"] = design_task.id
    task = AiCaseGenerationTask(
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        prompt_id=payload.prompt_id,
        status=CaseGenerationTaskStatus.QUEUED.value,
        additional_instructions=payload.additional_instructions,
        selected_api_definition_ids=selected_api_ids,
        coverage_plan=coverage_plan,
        created_by=user.id,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return CaseGenerationTaskResponse.model_validate(task)


def list_case_generation_tasks(
    session: Session, user: CurrentUser, requirement_id: int
) -> CaseGenerationTaskListResponse:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    items = list(
        session.scalars(
            select(AiCaseGenerationTask)
            .where(AiCaseGenerationTask.requirement_id == requirement_id)
            .order_by(AiCaseGenerationTask.id.desc())
        ).all()
    )
    return CaseGenerationTaskListResponse(
        items=[CaseGenerationTaskResponse.model_validate(item) for item in items],
        total=len(items),
    )


def process_case_generation_task(
    session_factory: Callable[[], Session], task_id: int, user: CurrentUser
) -> None:
    with session_factory() as session:
        task = session.get(AiCaseGenerationTask, task_id)
        if task is None or task.status != CaseGenerationTaskStatus.QUEUED.value:
            return
        task.status = CaseGenerationTaskStatus.RUNNING.value
        task.started_at = utc_now_naive()
        session.commit()
        try:
            requirement = _get_requirement(session, task.requirement_id)
            _ensure_writable(session, user, requirement)
            version = session.get(RequirementVersion, task.requirement_version_id)
            if version is None or version.requirement_id != requirement.id:
                raise ResourceConflictError("任务关联的需求版本不存在")
            generation = _generate_case_suggestions_for_version(
                session,
                user,
                requirement,
                version,
                CaseGenerationRequest(
                    prompt_id=task.prompt_id,
                    additional_instructions=task.additional_instructions,
                    selected_api_definition_ids=task.selected_api_definition_ids,
                ),
                coverage_plan=task.coverage_plan,
            )
            task = session.get(AiCaseGenerationTask, task_id)
            if task is None:
                return
            task.status = CaseGenerationTaskStatus.SUCCEEDED.value
            task.generation_id = generation.id
            task.error_code = None
            task.error_message = None
            task.completed_at = utc_now_naive()
            session.commit()
        except AppError as exc:
            session.rollback()
            error_message = exc.message
            if isinstance(exc.details, dict) and isinstance(
                exc.details.get("validation_errors"), list
            ):
                validation_errors = [
                    str(item) for item in exc.details["validation_errors"][:3]
                ]
                if validation_errors:
                    error_message += "：" + "；".join(validation_errors)
            _fail_case_generation_task(session, task_id, exc.code, error_message)
        except Exception:
            session.rollback()
            logger.exception(
                "ai_case_generation_task_failed",
                extra={"event": "AI_CASE_GENERATION_TASK_FAILED", "task_id": task_id},
            )
            _fail_case_generation_task(session, task_id, "INTERNAL_ERROR", "AI 生成任务执行失败")


def _fail_case_generation_task(
    session: Session, task_id: int, error_code: str, error_message: str
) -> None:
    task = session.get(AiCaseGenerationTask, task_id)
    if task is None:
        return
    task.status = CaseGenerationTaskStatus.FAILED.value
    task.error_code = error_code[:64]
    task.error_message = redact_text(error_message)[:500]
    task.completed_at = utc_now_naive()
    session.commit()


def list_case_generations(
    session: Session, user: CurrentUser, requirement_id: int
) -> CaseGenerationListResponse:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    generations = list(
        session.scalars(
            select(AiCaseGeneration)
            .where(AiCaseGeneration.requirement_id == requirement_id)
            .order_by(AiCaseGeneration.id.desc())
        ).all()
    )
    return CaseGenerationListResponse(
        items=[_generation_response(session, item) for item in generations],
        total=len(generations),
    )


def recompile_generation_cases(
    session: Session,
    user: CurrentUser,
    generation_id: int,
) -> CaseGenerationRecompileResponse:
    """Recompile accepted AI intents without another model call or an in-place edit."""

    generation = session.get(AiCaseGeneration, generation_id)
    if generation is None:
        raise ResourceNotFoundError("AI 用例生成批次不存在")
    requirement = _get_requirement(session, generation.requirement_id)
    _ensure_writable(session, user, requirement)
    call = session.get(AiCallLog, generation.ai_call_id)
    if call is None:
        raise ResourceConflictError("AI 用例生成审计记录不存在")
    try:
        source_intents = ApiCaseIntentResult.model_validate(call.parsed_result)
    except ValidationError as exc:
        raise ResourceConflictError(
            "该历史记录没有可复用的结构化 AI 意图，不能免 AI 重编译"
        ) from exc

    definitions = _active_api_definitions(session, generation.project_id)
    _, _, api_contracts = _project_api_generation_context(
        session,
        generation.project_id,
    )
    secret_context, available_secret_names, _ = _project_secret_generation_context(
        session,
        generation.project_id,
    )
    _, available_runtime_names, runtime_environment_ready = (
        _project_runtime_generation_context(session, generation.project_id)
    )
    if not runtime_environment_ready:
        raise ResourceConflictError("项目没有可用的默认运行环境，不能重编译")
    try:
        secret_metadata = json.loads(secret_context)
    except json.JSONDecodeError:
        secret_metadata = []
    intents_by_key = {item.case_key: item for item in source_intents.intents}
    check_points = (
        generation.coverage_plan.get("check_points", [])
        if isinstance(generation.coverage_plan, dict)
        else []
    )
    checkpoint_titles = {
        str(item.get("key", "")).strip().upper(): str(item.get("title", "")).strip()
        for item in check_points
        if isinstance(item, dict) and item.get("key")
    }
    suggestions = list(session.scalars(
        select(AiCaseSuggestion)
        .where(AiCaseSuggestion.generation_id == generation.id)
        .order_by(AiCaseSuggestion.sequence_no)
    ).all())
    items: list[CaseRecompileItem] = []

    for suggestion in suggestions:
        if suggestion.status != SuggestionStatus.ACCEPTED.value or suggestion.test_case_id is None:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                status="SKIPPED",
                message="仅重编译已保存为正式用例的建议",
            ))
            continue
        if suggestion.human_result is not None:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                status="SKIPPED",
                message="该建议曾人工编辑，为避免覆盖人工判断已跳过",
            ))
            continue
        case = session.get(TestCase, suggestion.test_case_id)
        if case is None or case.status != "ACTIVE" or case.current_version_id is None:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                status="SKIPPED",
                message="正式用例不存在、已归档或没有当前版本",
            ))
            continue
        version_count = int(session.scalar(
            select(func.count(TestCaseVersion.id)).where(TestCaseVersion.case_id == case.id)
        ) or 0)
        current_version = session.get(TestCaseVersion, case.current_version_id)
        if current_version is None:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                status="SKIPPED",
                message="正式用例当前版本不存在",
            ))
            continue
        current_content = SuggestedCase.model_validate(current_version.content)
        if current_content.test_data.get("compiler_version") == CASE_COMPILER_VERSION:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                version_no=current_version.version_no,
                status="SKIPPED",
                message="当前版本已由最新平台编译器生成",
            ))
            continue
        if (
            version_count > 1
            and not (
                current_content.test_data.get("compiler_version")
                and str(current_version.change_note or "").startswith(
                    "复用原 AI 意图，由平台编译器"
                )
            )
        ):
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                version_no=current_version.version_no,
                status="SKIPPED",
                message="用例已有后续版本，为避免覆盖人工修改已跳过",
            ))
            continue
        intent_key = str(
            SuggestedCase.model_validate(suggestion.structured_result).test_data.get(
                "intent_key", ""
            )
        )
        intent = intents_by_key.get(intent_key)
        if intent is None:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                status="FAILED",
                message="找不到该建议对应的原始 AI 意图",
            ))
            continue
        compilation = compile_case_intents(
            [intent],
            definitions,
            secret_metadata if isinstance(secret_metadata, list) else [],
        )
        if compilation.result is None:
            message = (
                compilation.diagnostics[0].display()
                if compilation.diagnostics
                else "平台未能编译该意图"
            )
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                status="FAILED",
                message=message,
            ))
            continue
        compiled_case = compilation.result.cases[0]
        readiness_issues = _case_execution_ready_issues(
            compiled_case,
            api_contracts,
            available_secret_names,
            available_runtime_names,
        )
        if readiness_issues:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                status="FAILED",
                message="；".join(readiness_issues),
            ))
            continue
        invalid_coverage = {
            tag.removeprefix("coverage:").strip().upper()
            for tag in compiled_case.tags
            if tag.startswith("coverage:")
            and _semantic_coverage_issues(
                compiled_case,
                checkpoint_titles.get(
                    tag.removeprefix("coverage:").strip().upper(), ""
                ),
            )
        }
        compiled_case = compiled_case.model_copy(update={
            "tags": [
                tag for tag in compiled_case.tags
                if not (
                    tag.startswith("coverage:")
                    and tag.removeprefix("coverage:").strip().upper()
                    in invalid_coverage
                )
            ],
        })
        try:
            with session.begin_nested():
                _ensure_v1_api_retry_policy(compiled_case)
                _validate_data_source(session, generation.project_id, compiled_case)
                _validate_assertions(session, generation.project_id, compiled_case)
                _validate_cleanup_configs(session, generation.project_id, compiled_case)
                new_version = _create_case_version(
                    session,
                    user,
                    case,
                    compiled_case,
                    f"复用原 AI 意图，由平台编译器 {CASE_COMPILER_VERSION} 重新编译",
                )
        except (AppError, ValidationError, ValueError) as exc:
            items.append(CaseRecompileItem(
                suggestion_id=suggestion.id,
                case_code=case.code,
                status="FAILED",
                message=redact_text(str(exc))[:500],
            ))
            continue
        items.append(CaseRecompileItem(
            suggestion_id=suggestion.id,
            case_code=case.code,
            version_no=new_version.version_no,
            status="RECOMPILED",
            message=(
                "已创建新的不可变版本"
                if not invalid_coverage
                else "已创建新版本；未被实际证明的覆盖标签已移除"
            ),
        ))

    session.commit()
    return CaseGenerationRecompileResponse(
        generation_id=generation.id,
        compiler_version=CASE_COMPILER_VERSION,
        recompiled_count=sum(item.status == "RECOMPILED" for item in items),
        skipped_count=sum(item.status == "SKIPPED" for item in items),
        failed_count=sum(item.status == "FAILED" for item in items),
        items=items,
    )


def edit_suggestion(
    session: Session,
    user: CurrentUser,
    suggestion_id: int,
    payload: CaseSuggestionEdit,
) -> CaseSuggestionResponse:
    suggestion = _get_suggestion(session, suggestion_id)
    generation = session.get(AiCaseGeneration, suggestion.generation_id)
    if generation is None:
        raise ResourceNotFoundError("用例生成批次不存在")
    requirement = _get_requirement(session, generation.requirement_id)
    _ensure_writable(session, user, requirement)
    if suggestion.status != SuggestionStatus.DRAFT.value:
        raise ResourceConflictError("已决策的用例建议不能再编辑")
    suggestion.human_result = payload.human_result.model_dump(mode="json")
    suggestion.decision_note = payload.decision_note
    suggestion.reviewed_by = user.id
    session.commit()
    session.refresh(suggestion)
    return CaseSuggestionResponse.model_validate(suggestion)


def _create_case_asset(
    session: Session,
    user: CurrentUser,
    *,
    project_id: int,
    result: SuggestedCase,
    source: str,
    change_note: str | None,
    requirement_versions: list[tuple[int, int]],
    link_confidence: Decimal,
) -> tuple[TestCase, TestCaseVersion]:
    """Create one canonical formal test-case asset regardless of authoring source."""
    _ensure_v1_api_retry_policy(result)
    _validate_data_source(session, project_id, result)
    _validate_assertions(session, project_id, result)
    _validate_cleanup_configs(session, project_id, result)
    case = TestCase(
        project_id=project_id,
        code=next_project_business_code(
            session,
            project_id=project_id,
            namespace=BusinessCodeNamespace.TEST_CASE,
            model=TestCase,
        ),
        name=result.title,
        case_type=result.case_type.value,
        status="ACTIVE",
        source=source,
        created_by=user.id,
    )
    session.add(case)
    session.flush()
    version = _create_case_version(session, user, case, result, change_note)
    for requirement_id, requirement_version_id in dict.fromkeys(requirement_versions):
        session.add(
            RequirementCaseLink(
                requirement_id=requirement_id,
                requirement_version_id=requirement_version_id,
                asset_type="TEST_CASE",
                case_type=result.case_type.value,
                case_id=case.id,
                case_version_id=version.id,
                relation_type="COVERAGE",
                source=source,
                confidence=link_confidence,
                status="ACTIVE",
                active_slot=1,
                created_by=user.id,
                created_at_time_basis="UTC",
                created_at=utc_now_naive(),
            )
        )
    return case, version


def _create_formal_case(
    session: Session,
    user: CurrentUser,
    generation: AiCaseGeneration,
    suggestion: AiCaseSuggestion,
    result: SuggestedCase,
) -> TestCase:
    requirement_versions = [(generation.requirement_id, generation.requirement_version_id)]
    for requirement_id in suggestion.linked_requirement_ids:
        requirement = _get_requirement(session, int(requirement_id))
        if (
            requirement.project_id != generation.project_id
            or requirement.status != "ACTIVE"
            or requirement.current_version_id is None
        ):
            raise ResourceConflictError("建议关联的 Requirement 已失效")
        requirement_versions.append((requirement.id, requirement.current_version_id))
    case, _ = _create_case_asset(
        session,
        user,
        project_id=generation.project_id,
        result=result,
        source="AI",
        change_note="AI 建议经人工确认生成",
        requirement_versions=requirement_versions,
        link_confidence=Decimal(str(result.confidence)),
    )
    return case


def _apply_decision(
    session: Session,
    user: CurrentUser,
    suggestion: AiCaseSuggestion,
    action: SuggestionDecision,
    decision_note: str | None,
) -> None:
    if suggestion.status != SuggestionStatus.DRAFT.value:
        raise ResourceConflictError(f"建议 #{suggestion.id} 已完成决策")
    generation = session.get(AiCaseGeneration, suggestion.generation_id)
    if generation is None:
        raise ResourceNotFoundError("用例生成批次不存在")
    requirement = _get_requirement(session, generation.requirement_id)
    _ensure_writable(session, user, requirement)
    suggestion.decision_note = decision_note or suggestion.decision_note
    suggestion.reviewed_by = user.id
    suggestion.reviewed_at = utc_now_naive()
    if action == SuggestionDecision.REJECT:
        suggestion.status = SuggestionStatus.REJECTED.value
        return
    result = SuggestedCase.model_validate(suggestion.human_result or suggestion.structured_result)
    case = _create_formal_case(session, user, generation, suggestion, result)
    session.flush()
    suggestion.test_case_id = case.id
    suggestion.status = SuggestionStatus.ACCEPTED.value


def decide_suggestion(
    session: Session,
    user: CurrentUser,
    suggestion_id: int,
    payload: CaseSuggestionDecision,
) -> CaseSuggestionResponse:
    suggestion = _get_suggestion(session, suggestion_id)
    _apply_decision(session, user, suggestion, payload.action, payload.decision_note)
    session.commit()
    session.refresh(suggestion)
    return CaseSuggestionResponse.model_validate(suggestion)


def bulk_decide_suggestions(
    session: Session,
    user: CurrentUser,
    payload: CaseSuggestionBulkDecision,
) -> list[CaseSuggestionResponse]:
    unique_ids = list(dict.fromkeys(payload.suggestion_ids))
    suggestions = [session.get(AiCaseSuggestion, item_id) for item_id in unique_ids]
    if any(item is None for item in suggestions):
        raise ResourceNotFoundError("部分 AI 用例建议不存在")
    typed_suggestions = [item for item in suggestions if item is not None]
    for suggestion in typed_suggestions:
        _apply_decision(session, user, suggestion, payload.action, payload.decision_note)
    session.commit()
    return [CaseSuggestionResponse.model_validate(item) for item in typed_suggestions]


def _bulk_draft_suggestions(
    session: Session, user: CurrentUser, suggestion_ids: list[int]
) -> list[AiCaseSuggestion]:
    unique_ids = list(dict.fromkeys(suggestion_ids))
    suggestions = [session.get(AiCaseSuggestion, item_id) for item_id in unique_ids]
    if any(item is None for item in suggestions):
        raise ResourceNotFoundError("部分 AI 用例建议不存在")
    typed = [item for item in suggestions if item is not None]
    for suggestion in typed:
        if suggestion.status != SuggestionStatus.DRAFT.value:
            raise ResourceConflictError(f"建议 #{suggestion.id} 已完成决策")
        generation = session.get(AiCaseGeneration, suggestion.generation_id)
        if generation is None:
            raise ResourceNotFoundError("用例生成批次不存在")
        _ensure_writable(session, user, _get_requirement(session, generation.requirement_id))
    return typed


def bulk_edit_suggestions(
    session: Session,
    user: CurrentUser,
    payload: CaseSuggestionBulkEdit,
) -> list[CaseSuggestionResponse]:
    suggestions = _bulk_draft_suggestions(session, user, payload.suggestion_ids)
    for suggestion in suggestions:
        generation = session.get(AiCaseGeneration, suggestion.generation_id)
        if generation is None:  # guarded above; keeps the type boundary explicit
            raise ResourceNotFoundError("用例生成批次不存在")
        if payload.requirement_ids is not None:
            linked_ids: list[int] = []
            for requirement_id in payload.requirement_ids:
                requirement = _get_requirement(session, requirement_id)
                if (
                    requirement.project_id != generation.project_id
                    or requirement.status != "ACTIVE"
                    or requirement.current_version_id is None
                ):
                    raise ResourceConflictError("只能关联同项目下有当前版本的活动 Requirement")
                if requirement.id != generation.requirement_id:
                    linked_ids.append(requirement.id)
            suggestion.linked_requirement_ids = linked_ids
        result = SuggestedCase.model_validate(
            suggestion.human_result or suggestion.structured_result
        ).model_dump(mode="json")
        if payload.priority is not None:
            result["priority"] = payload.priority.value
        if payload.tags is not None:
            result["tags"] = payload.tags
        suggestion.human_result = SuggestedCase.model_validate(result).model_dump(mode="json")
        suggestion.reviewed_by = user.id
    session.commit()
    return [CaseSuggestionResponse.model_validate(item) for item in suggestions]


def bulk_delete_suggestions(
    session: Session,
    user: CurrentUser,
    suggestion_ids: list[int],
) -> CaseSuggestionBulkDeleteResponse:
    suggestions = _bulk_draft_suggestions(session, user, suggestion_ids)
    for suggestion in suggestions:
        session.delete(suggestion)
    session.commit()
    return CaseSuggestionBulkDeleteResponse(deleted_count=len(suggestions))


def list_project_cases(
    session: Session, user: CurrentUser, project_id: int
) -> list[TestCaseResponse]:
    get_project(session, user, project_id)
    cases = list(
        session.scalars(
            select(TestCase).where(TestCase.project_id == project_id).order_by(TestCase.id.desc())
        ).all()
    )
    return [TestCaseResponse.model_validate(item) for item in cases]


def _create_case_version(
    session: Session,
    user: CurrentUser,
    case: TestCase,
    content: SuggestedCase,
    change_note: str | None,
) -> TestCaseVersion:
    latest_version_no = session.scalar(
        select(TestCaseVersion.version_no)
        .where(TestCaseVersion.case_id == case.id)
        .order_by(TestCaseVersion.version_no.desc())
        .limit(1)
    )
    version = TestCaseVersion(
        case_id=case.id,
        version_no=(latest_version_no or 0) + 1,
        content=content.model_dump(mode="json"),
        change_note=change_note,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    _inherit_active_case_requirement_links(
        session,
        user,
        case,
        version,
        case_type=content.case_type.value,
    )
    case.name = content.title
    case.case_type = content.case_type.value
    case.current_version_id = version.id
    return version


def _inherit_active_case_requirement_links(
    session: Session,
    user: CurrentUser,
    case: TestCase,
    version: TestCaseVersion,
    *,
    case_type: str,
) -> None:
    """Move exact-version requirement links to a newly created case version.

    Requirement links have one ACTIVE slot per requirement, case and relation type.
    Keeping both versions active would violate that invariant, so inheritance is an
    auditable supersession: the old row is retained as REMOVED and the new ACTIVE
    row points back to it. Asset-level legacy links (no case_version_id) already
    apply to every version and therefore do not need to be rewritten.
    """

    inherited_at = utc_now_naive()
    previous_links = list(
        session.scalars(
            select(RequirementCaseLink)
            .where(
                RequirementCaseLink.asset_type == "TEST_CASE",
                RequirementCaseLink.case_id == case.id,
                RequirementCaseLink.case_version_id.is_not(None),
                RequirementCaseLink.case_version_id != version.id,
                RequirementCaseLink.status == "ACTIVE",
            )
            .order_by(RequirementCaseLink.id.asc())
            .with_for_update()
        ).all()
    )
    if not previous_links:
        return

    for link in previous_links:
        link.status = "REMOVED"
        link.active_slot = None
        link.removed_by = user.id
        link.removed_at = inherited_at
    # Release the unique ACTIVE slots before inserting their successors.
    session.flush()

    for link in previous_links:
        session.add(
            RequirementCaseLink(
                requirement_id=link.requirement_id,
                requirement_version_id=link.requirement_version_id,
                asset_type="TEST_CASE",
                case_type=case_type,
                case_id=case.id,
                case_version_id=version.id,
                relation_type=link.relation_type,
                source=link.source,
                confidence=link.confidence,
                status="ACTIVE",
                active_slot=1,
                supersedes_link_id=link.id,
                created_by=user.id,
                created_at_time_basis="UTC",
                created_at=inherited_at,
            )
        )


def create_test_case(
    session: Session, user: CurrentUser, payload: TestCaseCreate
) -> TestCaseDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建测试用例")
    requirement_versions: list[tuple[int, int]] = []
    for requirement_id in payload.requirement_ids:
        requirement = _get_requirement(session, requirement_id)
        if requirement.project_id != payload.project_id:
            raise ResourceConflictError("需求与测试用例必须属于同一项目")
        if requirement.status != "ACTIVE":
            raise ResourceConflictError("归档需求不能新增测试用例关联")
        requirement_version_id = requirement.current_version_id
        if requirement_version_id is None:
            raise ResourceConflictError("需求缺少可靠的当前版本，不能新增测试用例关联")
        requirement_version = session.get(RequirementVersion, requirement_version_id)
        if requirement_version is None or requirement_version.requirement_id != requirement.id:
            raise ResourceConflictError("需求缺少可靠的当前版本，不能新增测试用例关联")
        requirement_versions.append((requirement.id, requirement_version_id))
    case, version = _create_case_asset(
        session,
        user,
        project_id=payload.project_id,
        result=payload.content,
        source="MANUAL",
        change_note=payload.change_note,
        requirement_versions=requirement_versions,
        link_confidence=Decimal("1"),
    )
    session.commit()
    session.refresh(case)
    session.refresh(version)
    return TestCaseDetailResponse(
        **TestCaseResponse.model_validate(case).model_dump(),
        current_version=TestCaseVersionResponse.model_validate(version),
    )


def create_test_case_version(
    session: Session,
    user: CurrentUser,
    case_id: int,
    payload: TestCaseVersionCreate,
) -> TestCaseVersionResponse:
    case = session.get(TestCase, case_id)
    if case is None:
        raise ResourceNotFoundError("测试用例不存在")
    project = get_project(session, user, case.project_id)
    ensure_project_writable(session, project, user)
    if case.status != "ACTIVE":
        raise ResourceConflictError("已归档测试用例不能创建新版本")
    _ensure_v1_api_retry_policy(payload.content)
    _validate_data_source(session, case.project_id, payload.content)
    _validate_assertions(session, case.project_id, payload.content)
    _validate_cleanup_configs(session, case.project_id, payload.content)
    version = _create_case_version(session, user, case, payload.content, payload.change_note)
    session.commit()
    session.refresh(version)
    return TestCaseVersionResponse.model_validate(version)


def list_test_case_versions(
    session: Session, user: CurrentUser, case_id: int
) -> list[TestCaseVersionResponse]:
    case = session.get(TestCase, case_id)
    if case is None:
        raise ResourceNotFoundError("测试用例不存在")
    get_project(session, user, case.project_id)
    versions = list(
        session.scalars(
            select(TestCaseVersion)
            .where(TestCaseVersion.case_id == case_id)
            .order_by(TestCaseVersion.version_no.desc())
        ).all()
    )
    return [TestCaseVersionResponse.model_validate(item) for item in versions]


def archive_test_case(session: Session, user: CurrentUser, case_id: int) -> TestCaseResponse:
    case = session.get(TestCase, case_id)
    if case is None:
        raise ResourceNotFoundError("测试用例不存在")
    project = get_project(session, user, case.project_id)
    ensure_project_writable(session, project, user)
    case.status = "ARCHIVED"
    session.commit()
    session.refresh(case)
    return TestCaseResponse.model_validate(case)


def get_test_case(session: Session, user: CurrentUser, case_id: int) -> TestCaseDetailResponse:
    case = session.get(TestCase, case_id)
    if case is None:
        raise ResourceNotFoundError("测试用例不存在")
    get_project(session, user, case.project_id)
    version = session.get(TestCaseVersion, case.current_version_id)
    return TestCaseDetailResponse(
        **TestCaseDetailResponse.model_validate(case).model_dump(exclude={"current_version"}),
        current_version=(TestCaseVersionResponse.model_validate(version) if version else None),
    )


def list_requirement_case_links(
    session: Session, user: CurrentUser, requirement_id: int
) -> list[RequirementCaseLinkResponse]:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    links = list(
        session.scalars(
            select(RequirementCaseLink)
            .where(
                RequirementCaseLink.requirement_id == requirement_id,
                RequirementCaseLink.asset_type == "TEST_CASE",
                RequirementCaseLink.status == "ACTIVE",
            )
            .order_by(RequirementCaseLink.id.desc())
        ).all()
    )
    return [RequirementCaseLinkResponse.model_validate(item) for item in links]
