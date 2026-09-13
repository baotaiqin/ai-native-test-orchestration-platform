import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.modules.scenarios.schemas import (
    ScenarioDsl,
    ScenarioNode,
    ScenarioNodeType,
    ScenarioValidationIssue,
    ScenarioValidationResponse,
    ValidationSeverity,
)

_VARIABLE_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_EXACT_VARIABLE_PATTERN = re.compile(r"^\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}$")
_SECRET_VARIABLE_PATTERN = re.compile(
    r"^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$"
)
_SYSTEM_VARIABLES = {"run_id", "scenario_id", "project_id", "environment_id"}
_PARENT_TYPES = {ScenarioNodeType.IF, ScenarioNodeType.ELSE, ScenarioNodeType.LOOP}
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "access_token",
    "refresh_token",
    "credential",
}
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credential",
)


@dataclass(frozen=True)
class ScenarioSecretReference:
    node_id: str
    node_name: str
    path: str
    name: str


def _issue(code: str, message: str, node: ScenarioNode | None = None) -> ScenarioValidationIssue:
    return ScenarioValidationIssue(
        severity=ValidationSeverity.ERROR,
        code=code,
        message=message,
        node_id=node.id if node else None,
    )


def _config_text(node: ScenarioNode) -> str:
    return str(node.config)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _SENSITIVE_KEYS or any(
        marker in normalized for marker in _SENSITIVE_KEY_MARKERS
    )


def _is_controlled_credential_reference(value: Any, extracted: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    if _SECRET_VARIABLE_PATTERN.fullmatch(value):
        return True
    matched = _EXACT_VARIABLE_PATTERN.fullmatch(value)
    return bool(matched and matched.group(1) in extracted)


def _credential_message(node: ScenarioNode, path: str) -> str:
    return (
        f'节点“{node.name}”的 {path} 含明文或未受控敏感值；'
        "静态凭据请选择 Secret，运行中产生的 Token 必须引用前置 EXTRACT 变量"
    )


def _validate_sensitive_value(
    issues: list[ScenarioValidationIssue],
    node: ScenarioNode,
    value: Any,
    path: str,
    extracted: set[str],
) -> None:
    if not _is_controlled_credential_reference(value, extracted):
        issues.append(
            _issue(
                "SCENARIO_HTTP_CREDENTIAL_INVALID",
                _credential_message(node, path),
                node,
            )
        )


def _validate_http_credentials(
    issues: list[ScenarioValidationIssue],
    node: ScenarioNode,
    value: Any,
    *,
    extracted: set[str],
    path: str = "config",
    cookie_values: bool = False,
) -> None:
    if isinstance(value, dict):
        named_field = value.get("name")
        named_value_path: str | None = None
        if isinstance(named_field, str) and _is_sensitive_key(named_field):
            named_value_path = f"{path}.value"
            _validate_sensitive_value(
                issues, node, value.get("value"), named_value_path, extracted
            )

        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "value" and (named_value_path == child_path or cookie_values):
                if cookie_values and named_value_path != child_path:
                    _validate_sensitive_value(issues, node, child, child_path, extracted)
                continue
            if key == "cookies" and isinstance(child, (list, tuple)):
                _validate_http_credentials(
                    issues,
                    node,
                    child,
                    extracted=extracted,
                    path=child_path,
                    cookie_values=True,
                )
                continue
            if _is_sensitive_key(str(key)):
                _validate_sensitive_value(issues, node, child, child_path, extracted)
                continue
            if key == "key_value" and str(value.get("type", "")).upper() == "API_KEY":
                _validate_sensitive_value(issues, node, child, child_path, extracted)
                continue
            _validate_http_credentials(
                issues,
                node,
                child,
                extracted=extracted,
                path=child_path,
                cookie_values=key == "cookies",
            )
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_http_credentials(
                issues,
                node,
                child,
                extracted=extracted,
                path=f"{path}[{index}]",
                cookie_values=cookie_values,
            )


def scenario_secret_references(dsl: ScenarioDsl) -> list[ScenarioSecretReference]:
    references: list[ScenarioSecretReference] = []

    def collect(node: ScenarioNode, value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                collect(node, child, f"{path}.{key}")
            return
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                collect(node, child, f"{path}[{index}]")
            return
        if not isinstance(value, str):
            return
        matched = _SECRET_VARIABLE_PATTERN.fullmatch(value)
        if matched:
            references.append(
                ScenarioSecretReference(node.id, node.name, path, matched.group(1))
            )

    for node in dsl.nodes:
        collect(node, node.config, "config")
    return references


def validate_scenario_dsl(
    dsl: ScenarioDsl, *, context_keys: set[str] | None = None
) -> ScenarioValidationResponse:
    issues: list[ScenarioValidationIssue] = []
    node_ids = [node.id for node in dsl.nodes]
    if len(node_ids) != len(set(node_ids)):
        issues.append(_issue("DUPLICATE_NODE_ID", "节点 ID 必须唯一"))
    node_map = {node.id: node for node in dsl.nodes}
    starts = [node for node in dsl.nodes if node.type == ScenarioNodeType.START]
    ends = [node for node in dsl.nodes if node.type == ScenarioNodeType.END]
    if len(starts) != 1 or dsl.nodes[0].type != ScenarioNodeType.START:
        issues.append(_issue("INVALID_START", "必须且只能有一个 START，并位于首位"))
    if len(ends) != 1 or dsl.nodes[-1].type != ScenarioNodeType.END:
        issues.append(_issue("INVALID_END", "必须且只能有一个 END，并位于末位"))

    available = set(dsl.settings.initial_variables) | (context_keys or set()) | _SYSTEM_VARIABLES
    extracted: set[str] = set()
    for node in dsl.nodes:
        if node.parent_id:
            parent = node_map.get(node.parent_id)
            if parent is None:
                issues.append(_issue("PARENT_NOT_FOUND", "父节点不存在", node))
            elif parent.type not in _PARENT_TYPES:
                issues.append(_issue("INVALID_PARENT", "只能缩进到 IF/ELSE/LOOP 节点", node))
            cursor = parent
            visited = {node.id}
            while cursor is not None:
                if cursor.id in visited:
                    issues.append(_issue("PARENT_CYCLE", "节点父子关系形成循环", node))
                    break
                visited.add(cursor.id)
                cursor = node_map.get(cursor.parent_id) if cursor.parent_id else None

        config = node.config
        if node.type in {ScenarioNodeType.API_CLEANUP, ScenarioNodeType.SQL_CLEANUP}:
            try:
                node.cleanup_config(dsl.settings.cleanup_policy)
            except (TypeError, ValueError, ValidationError) as exc:
                issues.append(_issue("CLEANUP_CONFIG_INVALID", str(exc), node))
        if node.type == ScenarioNodeType.HTTP and not (
            config.get("case_id") or str(config.get("url", "")).strip()
        ):
            issues.append(_issue("HTTP_TARGET_MISSING", "HTTP 节点缺少 case_id 或 URL", node))
        if node.type == ScenarioNodeType.HTTP:
            _validate_http_credentials(issues, node, config, extracted=extracted)
        if node.type == ScenarioNodeType.IF and not str(config.get("condition", "")).strip():
            issues.append(_issue("IF_CONDITION_MISSING", "IF 节点缺少条件", node))
        if node.type == ScenarioNodeType.ELSE and not node.parent_id:
            issues.append(_issue("ELSE_PARENT_MISSING", "ELSE 节点必须设置父节点", node))
        if node.type == ScenarioNodeType.LOOP:
            iterations = config.get("iterations")
            items = config.get("items")
            if not items and not isinstance(iterations, int):
                issues.append(_issue("LOOP_CONFIG_INVALID", "LOOP 需要 iterations 或 items", node))
            elif isinstance(iterations, int) and not (
                1 <= iterations <= dsl.settings.max_loop_iterations
            ):
                issues.append(_issue("LOOP_LIMIT_INVALID", "LOOP 次数超过允许范围", node))
            available.add(str(config.get("item_variable", "loop_item")))
            available.add(str(config.get("index_variable", "loop_index")))
        if node.type == ScenarioNodeType.WAIT:
            duration = config.get("duration_ms")
            if not isinstance(duration, int) or not 1 <= duration <= 600000:
                issues.append(_issue("WAIT_DURATION_INVALID", "WAIT 时长必须为 1～600000ms", node))
        if node.type in {
            ScenarioNodeType.SQL_QUERY,
            ScenarioNodeType.SQL_EXECUTE,
            ScenarioNodeType.SQL_CLEANUP,
        }:
            if (
                not isinstance(config.get("connection_id"), int)
                or not str(config.get("sql", "")).strip()
            ):
                issues.append(
                    _issue(
                        "SQL_CONFIG_INVALID",
                        "SQL 节点必须配置 connection_id 和单条 SQL",
                        node,
                    )
                )
            if not isinstance(config.get("params", {}), (dict, list)):
                issues.append(_issue("SQL_PARAMS_INVALID", "SQL params 必须是对象或数组", node))
            available.add(
                str(
                    config.get(
                        "result_variable",
                        "sql_rows" if node.type == ScenarioNodeType.SQL_QUERY else "affected_rows",
                    )
                )
            )
        if node.type == ScenarioNodeType.PYTHON_SCRIPT:
            script = config.get("script")
            if not isinstance(script, str) or not script.strip() or len(script) > 5000:
                issues.append(
                    _issue(
                        "PYTHON_SCRIPT_INVALID",
                        "Python Script 不能为空且不能超过 5000 字符",
                        node,
                    )
                )

        if node.type == ScenarioNodeType.ASSERT_STATUS:
            expected = config.get("expected")
            if type(expected) is not int or not 100 <= expected <= 599:
                issues.append(
                    _issue(
                        "ASSERT_STATUS_INVALID",
                        "ASSERT_STATUS expected 必须是 100～599 的整数",
                        node,
                    )
                )
        if node.type == ScenarioNodeType.ASSERT_JSONPATH:
            expression = config.get("expression")
            if not isinstance(expression, str) or not expression.startswith("$"):
                issues.append(
                    _issue(
                        "ASSERT_JSONPATH_INVALID",
                        "ASSERT_JSONPATH 必须配置以 $ 开始的 expression",
                        node,
                    )
                )
            if "expected" not in config:
                issues.append(
                    _issue(
                        "ASSERT_JSONPATH_EXPECTED_MISSING", "ASSERT_JSONPATH 缺少 expected", node
                    )
                )

        if node.type == ScenarioNodeType.AI_ASSERTION:
            prompt_id = config.get("prompt_id")
            criteria = config.get("criteria")
            threshold = config.get("confidence_threshold", 0.8)
            allowed = {"prompt_id", "criteria", "confidence_threshold"}
            if set(config) - allowed:
                issues.append(
                    _issue("AI_ASSERTION_CONFIG_INVALID", "AI_ASSERTION 包含未知配置", node)
                )
            if type(prompt_id) is not int or prompt_id <= 0:
                issues.append(
                    _issue(
                        "AI_ASSERTION_PROMPT_INVALID",
                        "AI_ASSERTION 必须配置 prompt_id",
                        node,
                    )
                )
            if not isinstance(criteria, str) or not criteria.strip() or len(criteria) > 4000:
                issues.append(
                    _issue(
                        "AI_ASSERTION_CRITERIA_INVALID",
                        "AI_ASSERTION criteria 必须为 1～4000 字符",
                        node,
                    )
                )
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not 0 <= threshold <= 1
            ):
                issues.append(
                    _issue(
                        "AI_ASSERTION_THRESHOLD_INVALID",
                        "AI_ASSERTION confidence_threshold 必须为 0～1",
                        node,
                    )
                )
            if node.parent_id is not None:
                issues.append(
                    _issue(
                        "AI_ASSERTION_POSITION_INVALID",
                        "AI_ASSERTION 只能位于 Scenario 顶层",
                        node,
                    )
                )
            if node.failure_policy == "RETRY_ONCE":
                issues.append(
                    _issue(
                        "AI_ASSERTION_RETRY_INVALID",
                        "AI_ASSERTION 不允许 Runner 重试",
                        node,
                    )
                )
            node_index = dsl.nodes.index(node)
            if not any(
                item.type == ScenarioNodeType.HTTP and item.enabled
                for item in dsl.nodes[:node_index]
            ):
                issues.append(
                    _issue(
                        "AI_ASSERTION_RESPONSE_MISSING",
                        "AI_ASSERTION 前必须存在启用的 HTTP 节点",
                        node,
                    )
                )
            if any(
                item.enabled
                and item.type not in {
                    ScenarioNodeType.AI_ASSERTION,
                    ScenarioNodeType.API_CLEANUP,
                    ScenarioNodeType.SQL_CLEANUP,
                    ScenarioNodeType.END,
                }
                for item in dsl.nodes[node_index + 1 :]
            ):
                issues.append(
                    _issue(
                        "AI_ASSERTION_POSITION_INVALID",
                        "AI_ASSERTION 后只能继续 AI 断言、Cleanup 或 END",
                        node,
                    )
                )
        referenced = {
            variable
            for variable in _VARIABLE_PATTERN.findall(_config_text(node))
            if not variable.startswith("secret.")
        }
        for variable in sorted(referenced - available):
            issues.append(_issue("UNDEFINED_VARIABLE", f"变量未定义：{variable}", node))
        if node.type == ScenarioNodeType.SET_VARIABLE:
            name = config.get("name")
            if not isinstance(name, str) or not name.strip():
                issues.append(_issue("VARIABLE_NAME_MISSING", "SET_VARIABLE 缺少变量名", node))
            else:
                available.add(name)
        if node.type == ScenarioNodeType.EXTRACT:
            name = config.get("name")
            expression = config.get("expression")
            if not isinstance(name, str) or not name.strip() or not expression:
                issues.append(_issue("EXTRACT_CONFIG_INVALID", "EXTRACT 配置不完整", node))
            else:
                available.add(name)
                extracted.add(name)

    return ScenarioValidationResponse(
        valid=not issues,
        issues=issues,
        node_count=len(dsl.nodes),
    )
