import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import (
    ensure_project_owner,
    ensure_project_writable,
    get_project,
)
from app.modules.resource_registry.service import validate_cleanup_scope
from app.modules.scenarios.models import Scenario, ScenarioPreviewProfile, ScenarioVersion
from app.modules.scenarios.schemas import (
    ScenarioAiBaselineResponse,
    ScenarioCreate,
    ScenarioDetailResponse,
    ScenarioDsl,
    ScenarioListResponse,
    ScenarioNodeType,
    ScenarioPreviewProfileResponse,
    ScenarioPreviewProfileSave,
    ScenarioResponse,
    ScenarioValidationIssue,
    ScenarioValidationResponse,
    ScenarioVersionCreate,
    ScenarioVersionResponse,
    ValidationSeverity,
)
from app.modules.scenarios.validator import scenario_secret_references, validate_scenario_dsl
from app.modules.secrets.models import Secret
from app.modules.test_cases.schemas import Assertion, AssertionKind, RuntimeResponseSnapshot
from app.modules.test_cases.service import validate_ai_assertion_definition

_PREVIEW_PROFILE_MAX_BYTES = 2_000_000
_PREVIEW_PATH_PART = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]")
_PREVIEW_TEMPLATE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")


def _get_scenario(session: Session, user: CurrentUser, scenario_id: int) -> Scenario:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None:
        raise ResourceNotFoundError("Scenario 不存在")
    get_project(session, user, scenario.project_id)
    return scenario


def _current_version(session: Session, scenario: Scenario) -> ScenarioVersion:
    version = (
        session.get(ScenarioVersion, scenario.current_version_id)
        if scenario.current_version_id is not None
        else None
    )
    if version is None or version.scenario_id != scenario.id:
        raise ResourceConflictError("Scenario 当前版本不存在")
    return version


def _ai_suggestion_for_scenario(session: Session, scenario_id: int):
    # Imported locally to keep the Scenario and Swagger AI service modules acyclic.
    from app.modules.api_definitions.models import ApiDesignSuggestion

    return session.scalar(
        select(ApiDesignSuggestion)
        .where(
            ApiDesignSuggestion.created_scenario_id == scenario_id,
            ApiDesignSuggestion.kind == "SCENARIO",
            ApiDesignSuggestion.status == "ACCEPTED",
        )
        .order_by(ApiDesignSuggestion.id.asc())
        .limit(1)
    )


def _request_path(value: str) -> str:
    without_base = value.strip().replace("{{base_url}}", "")
    parsed = urlsplit(without_base)
    path = parsed.path if parsed.scheme and parsed.netloc else without_base.split("?", 1)[0]
    path = _PREVIEW_TEMPLATE.sub(lambda match: "{" + match.group(1) + "}", path)
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/"


def _sample_scalar(name: str, value_type: str | None) -> Any:
    normalized = name.lower()
    if value_type == "boolean" or normalized.startswith(("is_", "has_")):
        return True
    if value_type in {"integer", "number"} or normalized == "id" or normalized.endswith("_id"):
        return 1
    if "token" in normalized:
        return "preview-token"
    if "status" in normalized:
        return "CREATED"
    if "email" in normalized:
        return "preview@example.test"
    return f"preview-{name or 'value'}"


def _sample_from_schema(schema: Any, name: str = "value", depth: int = 0) -> Any:
    if depth > 8 or not isinstance(schema, dict):
        return {}
    for explicit in ("example", "default", "const"):
        if explicit in schema:
            return deepcopy(schema[explicit])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return deepcopy(enum[0])
    value_type = schema.get("type")
    properties = schema.get("properties")
    if value_type == "object" or isinstance(properties, dict):
        return {
            str(key): _sample_from_schema(child, str(key), depth + 1)
            for key, child in (properties or {}).items()
        }
    if value_type == "array":
        return [_sample_from_schema(schema.get("items", {}), name, depth + 1)]
    if value_type == "null":
        return None
    return _sample_scalar(name, str(value_type) if value_type else None)


def _set_preview_path(root: Any, expression: str, value: Any) -> Any:
    if expression == "$":
        return deepcopy(value)
    if not expression.startswith("$."):
        return root
    tokens: list[str | int] = []
    for match in _PREVIEW_PATH_PART.finditer(expression[1:]):
        tokens.append(int(match.group(2)) if match.group(2) is not None else match.group(1))
    if not tokens:
        return root
    if not isinstance(root, (dict, list)):
        root = {}
    current = root
    for index, token in enumerate(tokens):
        last = index == len(tokens) - 1
        next_token = tokens[index + 1] if not last else None
        if isinstance(token, int):
            if not isinstance(current, list):
                return root
            while len(current) <= token:
                current.append({} if not isinstance(next_token, int) else [])
            if last:
                current[token] = deepcopy(value)
            else:
                if not isinstance(current[token], (dict, list)):
                    current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
        else:
            if not isinstance(current, dict):
                return root
            if last:
                current[token] = deepcopy(value)
            else:
                child = current.get(token)
                if not isinstance(child, (dict, list)):
                    child = [] if isinstance(next_token, int) else {}
                    current[token] = child
                current = child
    return root


def _source_snapshot(session: Session, scenario_id: int) -> dict[str, Any] | None:
    if not inspect(session.get_bind()).has_table("api_design_suggestions"):
        return None
    suggestion = _ai_suggestion_for_scenario(session, scenario_id)
    return suggestion.source_snapshot if suggestion is not None else None


def _default_preview_profile(
    dsl: ScenarioDsl, source_snapshot: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, RuntimeResponseSnapshot]]:
    context = {
        variable: (
            "https://api.example.test"
            if variable == "base_url"
            else _sample_scalar(variable, None)
        )
        for variable in dsl.settings.initial_variables
    }
    definitions = (
        source_snapshot.get("definitions", [])
        if isinstance(source_snapshot, dict)
        else []
    )
    responses: dict[str, RuntimeResponseSnapshot] = {}
    nodes = dsl.nodes
    http_indexes = [
        index for index, node in enumerate(nodes) if node.type == ScenarioNodeType.HTTP
    ]
    for position, node_index in enumerate(http_indexes):
        node = nodes[node_index]
        request = node.config.get("request", node.config)
        method = str(request.get("method", "GET")).upper() if isinstance(request, dict) else "GET"
        url = str(request.get("url", "")) if isinstance(request, dict) else ""
        definition = next(
            (
                item
                for item in definitions
                if isinstance(item, dict)
                and str(item.get("method", "")).upper() == method
                and _request_path(str(item.get("path", ""))) == _request_path(url)
            ),
            None,
        )
        response_schemas = (
            definition.get("response_schema", {}) if isinstance(definition, dict) else {}
        )
        success_codes = sorted(
            int(code)
            for code in response_schemas
            if str(code).isdigit() and 200 <= int(code) <= 299
        )
        status_code = success_codes[0] if success_codes else 200
        response_schema = response_schemas.get(str(status_code), {})
        body_schema = (
            response_schema.get("schema", {})
            if isinstance(response_schema, dict)
            else {}
        )
        json_body = _sample_from_schema(body_schema)
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        next_http_index = (
            http_indexes[position + 1] if position + 1 < len(http_indexes) else len(nodes)
        )
        for dependent in nodes[node_index + 1 : next_http_index]:
            if dependent.type == ScenarioNodeType.ASSERT_STATUS:
                expected = dependent.config.get("expected")
                if type(expected) is int:
                    status_code = expected
            elif dependent.type in {
                ScenarioNodeType.EXTRACT,
                ScenarioNodeType.ASSERT_JSONPATH,
            }:
                expression = str(dependent.config.get("expression", ""))
                source = str(dependent.config.get("source", "JSONPATH"))
                expected = dependent.config.get("expected", _sample_scalar(dependent.name, None))
                if source == "HEADER":
                    headers[expression] = str(expected)
                elif source == "COOKIE":
                    cookies[expression] = str(expected)
                else:
                    json_body = _set_preview_path(json_body, expression, expected)
        responses[node.id] = RuntimeResponseSnapshot(
            status_code=status_code,
            json_body=json_body,
            headers=headers,
            cookies=cookies,
            response_time_ms=0,
        )
    return context, responses


def _preview_profile_response(
    scenario: Scenario,
    version: ScenarioVersion,
    *,
    context: dict[str, Any],
    responses_by_node: dict[str, Any],
    cleanup_outcome: str,
    source: str,
    saved_at=None,
) -> ScenarioPreviewProfileResponse:
    return ScenarioPreviewProfileResponse(
        scenario_id=scenario.id,
        scenario_version_id=version.id,
        context=context,
        responses_by_node=responses_by_node,
        cleanup_outcome=cleanup_outcome,
        source=source,
        saved_at=saved_at,
    )


def _ensure_valid(
    session: Session,
    user: CurrentUser,
    project_id: int,
    payload: ScenarioCreate | ScenarioVersionCreate,
) -> None:
    _ensure_valid_dsl(session, user, project_id, payload.dsl)


def _ensure_valid_dsl(
    session: Session,
    user: CurrentUser,
    project_id: int,
    dsl: ScenarioDsl,
    *,
    require_bound_secrets: bool = False,
) -> None:
    validation = validate_scenario_dsl(dsl)
    if not validation.valid:
        first = validation.issues[0]
        raise ResourceConflictError(f"Scenario DSL 校验失败：{first.message}")
    get_project(session, user, project_id)
    if require_bound_secrets:
        binding_issues = _scenario_secret_binding_issues(session, project_id, dsl)
        if binding_issues:
            raise ResourceConflictError(
                f"Scenario 凭据绑定校验失败：{binding_issues[0].message}"
            )
    for node in dsl.nodes:
        if node.type.value == "AI_ASSERTION":
            assertion = Assertion(
                kind=AssertionKind.AI_SEMANTIC,
                type="AI_SEMANTIC",
                name=node.name,
                prompt_id=node.config.get("prompt_id"),
                criteria=node.config.get("criteria"),
                confidence_threshold=node.config.get("confidence_threshold", 0.8),
            )
            validate_ai_assertion_definition(session, project_id, assertion)
            continue
        if node.type.value not in {"API_CLEANUP", "SQL_CLEANUP"}:
            continue
        try:
            validate_cleanup_scope(
                session, project_id, node.cleanup_config(dsl.settings.cleanup_policy)
            )
        except (TypeError, ValueError, ResourceConflictError) as exc:
            raise ResourceConflictError(f"Scenario Cleanup 校验失败：{exc}") from exc


def _scenario_secret_binding_issues(
    session: Session, project_id: int, dsl: ScenarioDsl
) -> list[ScenarioValidationIssue]:
    references = scenario_secret_references(dsl)
    names = {reference.name for reference in references}
    if not names:
        return []
    available = {
        secret.name: secret
        for secret in session.scalars(
            select(Secret).where(
                Secret.project_id == project_id,
                Secret.name.in_(names),
            )
        ).all()
    }
    issues: list[ScenarioValidationIssue] = []
    for reference in references:
        secret = available.get(reference.name)
        if secret is None:
            code = "SCENARIO_SECRET_UNBOUND"
            state = "尚未创建或绑定"
        elif not secret.enabled:
            code = "SCENARIO_SECRET_DISABLED"
            state = "已停用"
        else:
            continue
        issues.append(
            ScenarioValidationIssue(
                severity=ValidationSeverity.WARNING,
                code=code,
                message=(
                    f'节点“{reference.node_name}”的 {reference.path} 引用 Secret '
                    f'“{reference.name}”，但该 Secret {state}；草稿可以保存，批准执行前必须处理'
                ),
                node_id=reference.node_id,
            )
        )
    return issues


def _create_version(
    session: Session,
    user: CurrentUser,
    scenario: Scenario,
    payload: ScenarioCreate | ScenarioVersionCreate,
) -> ScenarioVersion:
    latest = session.scalar(
        select(ScenarioVersion.version_no)
        .where(ScenarioVersion.scenario_id == scenario.id)
        .order_by(ScenarioVersion.version_no.desc())
        .limit(1)
    )
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version_no=(latest or 0) + 1,
        dsl=payload.dsl.model_dump(mode="json"),
        change_note=payload.change_note,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    scenario.current_version_id = version.id
    if scenario.status == "APPROVED":
        scenario.status = "DRAFT"
    return version


def create_scenario(
    session: Session, user: CurrentUser, payload: ScenarioCreate
) -> ScenarioDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建 Scenario")
    _ensure_valid(session, user, payload.project_id, payload)
    scenario = Scenario(
        project_id=payload.project_id,
        code=next_project_business_code(
            session,
            project_id=payload.project_id,
            namespace=BusinessCodeNamespace.SCENARIO,
            model=Scenario,
        ),
        name=payload.name,
        status="DRAFT",
        created_by=user.id,
    )
    session.add(scenario)
    session.flush()
    version = _create_version(session, user, scenario, payload)
    session.commit()
    session.refresh(scenario)
    session.refresh(version)
    return ScenarioDetailResponse(
        **ScenarioResponse.model_validate(scenario).model_dump(),
        current_version=ScenarioVersionResponse.model_validate(version),
    )


def list_scenarios(
    session: Session, user: CurrentUser, project_id: int
) -> ScenarioListResponse:
    get_project(session, user, project_id)
    items = list(
        session.scalars(
            select(Scenario)
            .where(Scenario.project_id == project_id)
            .order_by(Scenario.id.desc())
        ).all()
    )
    return ScenarioListResponse(
        items=[ScenarioResponse.model_validate(item) for item in items], total=len(items)
    )


def get_scenario(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioDetailResponse:
    scenario = _get_scenario(session, user, scenario_id)
    version = session.get(ScenarioVersion, scenario.current_version_id)
    return ScenarioDetailResponse(
        **ScenarioResponse.model_validate(scenario).model_dump(),
        current_version=(
            ScenarioVersionResponse.model_validate(version) if version else None
        ),
    )


def get_scenario_ai_baseline(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioAiBaselineResponse:
    """Return the immutable V1 created from the accepted AI suggestion."""

    scenario = _get_scenario(session, user, scenario_id)
    suggestion = _ai_suggestion_for_scenario(session, scenario.id)
    if suggestion is None:
        raise ResourceNotFoundError("当前 Scenario 没有关联的 AI 原始编排")
    version = session.scalar(
        select(ScenarioVersion)
        .where(
            ScenarioVersion.scenario_id == scenario.id,
            ScenarioVersion.version_no == 1,
        )
        .limit(1)
    )
    if version is None:
        raise ResourceNotFoundError("AI 原始编排版本不存在")
    try:
        baseline = ScenarioDsl.model_validate(version.dsl)
    except ValidationError as exc:
        raise ResourceConflictError("AI 原始编排已损坏，无法恢复") from exc
    return ScenarioAiBaselineResponse(
        scenario_id=scenario.id,
        suggestion_id=suggestion.id,
        source_version_id=version.id,
        name=scenario.name,
        dsl=baseline,
    )


def get_scenario_preview_defaults(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioPreviewProfileResponse:
    scenario = _get_scenario(session, user, scenario_id)
    version = _current_version(session, scenario)
    dsl = ScenarioDsl.model_validate(version.dsl)
    context, responses = _default_preview_profile(
        dsl, _source_snapshot(session, scenario.id)
    )
    return _preview_profile_response(
        scenario,
        version,
        context=context,
        responses_by_node=responses,
        cleanup_outcome="SUCCESS",
        source="GENERATED",
    )


def get_scenario_preview_profile(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioPreviewProfileResponse:
    scenario = _get_scenario(session, user, scenario_id)
    version = _current_version(session, scenario)
    profile = session.scalar(
        select(ScenarioPreviewProfile).where(
            ScenarioPreviewProfile.scenario_version_id == version.id
        )
    )
    if profile is None:
        return get_scenario_preview_defaults(session, user, scenario_id)
    return _preview_profile_response(
        scenario,
        version,
        context=profile.context,
        responses_by_node=profile.responses_by_node,
        cleanup_outcome=profile.cleanup_outcome,
        source="SAVED",
        saved_at=profile.updated_at,
    )


def save_scenario_preview_profile(
    session: Session,
    user: CurrentUser,
    scenario_id: int,
    payload: ScenarioPreviewProfileSave,
) -> ScenarioPreviewProfileResponse:
    scenario = _get_scenario(session, user, scenario_id)
    project = get_project(session, user, scenario.project_id)
    ensure_project_writable(session, project, user)
    if scenario.status == "ARCHIVED":
        raise ResourceConflictError("已归档 Scenario 不能保存 Preview 模拟数据")
    version = _current_version(session, scenario)
    if payload.scenario_version_id != version.id:
        raise ResourceConflictError("Scenario 版本已变化，请重新打开 Preview")
    dsl = ScenarioDsl.model_validate(version.dsl)
    http_node_ids = {
        node.id for node in dsl.nodes if node.type == ScenarioNodeType.HTTP
    }
    unknown = set(payload.responses_by_node) - http_node_ids
    if unknown:
        raise ResourceConflictError(
            f"Preview 模拟响应引用了不存在的 HTTP 节点：{sorted(unknown)[0]}"
        )
    serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    if len(serialized.encode("utf-8")) > _PREVIEW_PROFILE_MAX_BYTES:
        raise ResourceConflictError("Preview 模拟数据不能超过 2MB")
    profile = session.scalar(
        select(ScenarioPreviewProfile).where(
            ScenarioPreviewProfile.scenario_version_id == version.id
        )
    )
    if profile is None:
        profile = ScenarioPreviewProfile(
            scenario_version_id=version.id,
            context=payload.context,
            responses_by_node={
                key: value.model_dump(mode="json")
                for key, value in payload.responses_by_node.items()
            },
            cleanup_outcome=payload.cleanup_outcome.value,
            updated_by=user.id,
        )
        session.add(profile)
    else:
        profile.context = payload.context
        profile.responses_by_node = {
            key: value.model_dump(mode="json")
            for key, value in payload.responses_by_node.items()
        }
        profile.cleanup_outcome = payload.cleanup_outcome.value
        profile.updated_by = user.id
    session.commit()
    session.refresh(profile)
    return _preview_profile_response(
        scenario,
        version,
        context=profile.context,
        responses_by_node=profile.responses_by_node,
        cleanup_outcome=profile.cleanup_outcome,
        source="SAVED",
        saved_at=profile.updated_at,
    )


def create_scenario_version(
    session: Session,
    user: CurrentUser,
    scenario_id: int,
    payload: ScenarioVersionCreate,
) -> ScenarioVersionResponse:
    scenario = _get_scenario(session, user, scenario_id)
    project = get_project(session, user, scenario.project_id)
    ensure_project_writable(session, project, user)
    if scenario.status == "ARCHIVED":
        raise ResourceConflictError("已归档 Scenario 不能创建新版本")
    _ensure_valid(session, user, scenario.project_id, payload)
    if payload.name is not None:
        scenario.name = payload.name
    version = _create_version(session, user, scenario, payload)
    session.commit()
    session.refresh(version)
    return ScenarioVersionResponse.model_validate(version)


def list_scenario_versions(
    session: Session, user: CurrentUser, scenario_id: int
) -> list[ScenarioVersionResponse]:
    _get_scenario(session, user, scenario_id)
    versions = list(
        session.scalars(
            select(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == scenario_id)
            .order_by(ScenarioVersion.version_no.desc())
        ).all()
    )
    return [ScenarioVersionResponse.model_validate(item) for item in versions]


def archive_scenario(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioResponse:
    scenario = _get_scenario(session, user, scenario_id)
    project = get_project(session, user, scenario.project_id)
    ensure_project_writable(session, project, user)
    scenario.status = "ARCHIVED"
    session.commit()
    session.refresh(scenario)
    return ScenarioResponse.model_validate(scenario)


def delete_scenario(
    session: Session, user: CurrentUser, scenario_id: int
) -> None:
    """Delete an unused draft while preserving execution and approval audit data."""

    scenario = _get_scenario(session, user, scenario_id)
    project = get_project(session, user, scenario.project_id)
    ensure_project_writable(session, project, user)
    ensure_project_owner(session, project, user)
    if scenario.status != "DRAFT":
        raise ResourceConflictError("仅未批准的草稿 Scenario 可以删除；其他状态请改用归档")

    database = inspect(session.get_bind())
    if database.has_table("runs"):
        # Keep historical runs immutable. This also covers scenarios that returned
        # to DRAFT after an approved version was executed and then edited.
        from app.modules.runs.models import TestRun

        run_id = session.scalar(
            select(TestRun.id)
            .where(TestRun.scenario_id == scenario_id)
            .limit(1)
        )
        if run_id is not None:
            raise ResourceConflictError(
                "该 Scenario 已产生运行记录，不能删除；请改用归档"
            )

    session.delete(scenario)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(
            "该 Scenario 已被运行记录或其他资产引用，不能删除；请改用归档"
        ) from exc


def approve_scenario(
    session: Session, user: CurrentUser, scenario_id: int
) -> ScenarioResponse:
    scenario = _get_scenario(session, user, scenario_id)
    project = get_project(session, user, scenario.project_id)
    ensure_project_owner(session, project, user)
    if scenario.status == "ARCHIVED":
        raise ResourceConflictError("已归档 Scenario 不能批准")
    if scenario.status not in {"DRAFT", "APPROVED"}:
        raise ResourceConflictError("当前 Scenario 状态不能批准")
    if scenario.current_version_id is None:
        raise ResourceConflictError("Scenario 缺少当前版本，不能批准")
    version = session.get(ScenarioVersion, scenario.current_version_id)
    if version is None or version.scenario_id != scenario.id:
        raise ResourceConflictError("Scenario 当前版本不存在，不能批准")
    try:
        dsl = ScenarioDsl.model_validate(version.dsl)
    except ValidationError as exc:
        raise ResourceConflictError("Scenario 当前版本 DSL 无效，不能批准") from exc
    _ensure_valid_dsl(
        session,
        user,
        scenario.project_id,
        dsl,
        require_bound_secrets=True,
    )
    if scenario.status == "DRAFT":
        scenario.status = "APPROVED"
        session.commit()
        session.refresh(scenario)
    return ScenarioResponse.model_validate(scenario)


def validate_dsl(
    session: Session, user: CurrentUser, payload: ScenarioCreate
) -> ScenarioValidationResponse:
    get_project(session, user, payload.project_id)
    validation = validate_scenario_dsl(payload.dsl)
    if not validation.valid:
        return validation
    for node in payload.dsl.nodes:
        if node.type.value == "AI_ASSERTION":
            try:
                assertion = Assertion(
                    kind=AssertionKind.AI_SEMANTIC,
                    type="AI_SEMANTIC",
                    name=node.name,
                    prompt_id=node.config.get("prompt_id"),
                    criteria=node.config.get("criteria"),
                    confidence_threshold=node.config.get("confidence_threshold", 0.8),
                )
                validate_ai_assertion_definition(
                    session, payload.project_id, assertion
                )
            except (TypeError, ValueError, ResourceConflictError) as exc:
                validation.issues.append(
                    ScenarioValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="AI_ASSERTION_REFERENCE_INVALID",
                        message=str(exc),
                        node_id=node.id,
                    )
                )
            continue
        if node.type.value not in {"API_CLEANUP", "SQL_CLEANUP"}:
            continue
        try:
            validate_cleanup_scope(
                session,
                payload.project_id,
                node.cleanup_config(payload.dsl.settings.cleanup_policy),
            )
        except (TypeError, ValueError, ResourceConflictError) as exc:
            validation.issues.append(
                ScenarioValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="CLEANUP_SCOPE_INVALID",
                    message=str(exc),
                    node_id=node.id,
                )
                )
    validation.issues.extend(
        _scenario_secret_binding_issues(session, payload.project_id, payload.dsl)
    )
    validation.valid = not any(
        item.severity == ValidationSeverity.ERROR for item in validation.issues
    )
    return validation
