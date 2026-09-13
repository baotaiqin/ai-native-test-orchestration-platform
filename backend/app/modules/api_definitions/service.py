import hashlib
import json
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.logging import get_logger, redact_text
from app.core.time import utc_now_naive
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.api_definitions.models import (
    ApiDefinition,
    ApiDefinitionImport,
    ApiDesignSuggestion,
    ApiScenarioPlan,
    ApiScenarioPlanItem,
)
from app.modules.api_definitions.parser import parse_openapi
from app.modules.api_definitions.schemas import (
    ApiDefinitionListResponse,
    ApiDefinitionResponse,
    ApiDefinitionStatus,
    ApiDesignRequirementReference,
    ApiDesignScenarioReference,
    ApiDesignSuggestionCreate,
    ApiDesignSuggestionDecision,
    ApiDesignSuggestionEdit,
    ApiDesignSuggestionKind,
    ApiDesignSuggestionListResponse,
    ApiDesignSuggestionResponse,
    ApiScenarioPlanCreate,
    ApiScenarioPlanGenerate,
    ApiScenarioPlanGenerateResponse,
    ApiScenarioPlanItemResponse,
    ApiScenarioPlanListResponse,
    ApiScenarioPlanResponse,
    ApiScenarioPlanResult,
    ApiScenarioRecommendation,
    DiffStatus,
    OpenApiImportRequest,
    OpenApiImportResponse,
    OpenApiPreviewResponse,
    ParsedOperation,
)
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.models import AiCallLog
from app.modules.requirements.models import RequirementDocumentVersion
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.scenarios.schemas import ScenarioDsl
from app.modules.scenarios.service import _ensure_valid_dsl
from app.modules.scenarios.validator import validate_scenario_dsl
from app.modules.secrets.models import Secret
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.test_cases.schemas import CaseGenerationResult, CaseType, SuggestedCase
from app.modules.test_cases.service import (
    _ensure_v1_api_retry_policy,
    _validate_assertions,
    _validate_cleanup_configs,
    _validate_data_source,
)

_MAX_AI_SOURCE_OPERATIONS = 200
_MAX_AI_SOURCE_BYTES = 1_000_000
_RUNTIME_PATH_VARIABLE_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_UNSUPPORTED_VARIABLE_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_.-]*\}")
logger = get_logger(__name__)
_SCENARIO_DSL_GENERATION_CONTRACT = "\n".join(
    (
        "平台 Scenario DSL 必须使用当前节点结构，禁止输出旧版 dsl.steps：",
        '- dsl 顶层只能使用 version、nodes、settings；version 固定为 "1.0"。',
        "- nodes 至少包含 START 和 END，并按执行顺序排列；每个节点使用 "
        "id、type、name、description、enabled、parent_id、config、failure_policy、timeout_ms。",
        "- 每个节点都必须提供清晰的中文 description；业务节点说明目的、输入来源、"
        "输出变量或断言意图，START/END 说明场景边界。",
        "- 普通节点 failure_policy 使用 null，表示跟随场景级失败控制；不要用节点级"
        "CONTINUE 绕过 settings.stop_on_failure。仅确需有限重试时使用 RETRY_ONCE。",
        "- HTTP 请求必须写为 type=HTTP，方法和地址放在 config.method、config.url；"
        "url 使用 {{base_url}} 加给定 OpenAPI 路径。",
        "- 提取值使用独立 EXTRACT 节点，config 使用 name、source=JSONPATH、"
        "expression；断言使用独立 ASSERT_STATUS 或 ASSERT_JSONPATH 节点。",
        "- 运行时变量统一写成 {{token}}、{{order_id}} 形式，禁止 "
        "${token}、${order_id}。",
        "- OpenAPI 中的 example/default 不能当作真实凭据。password、固定 token、"
        "Authorization、Cookie、API Key 等静态敏感值禁止输出明文，必须使用 "
        "{{secret.<逻辑名称>}} 占位，生成后由用户绑定项目 Secret。",
        "- 登录响应产生的 token 必须先通过前置 EXTRACT 节点提取，再由后续节点使用 "
        "{{token}}；禁止把密码放入初始 Runtime 变量或 SET_VARIABLE。",
        "- 需求范围是本次编排的业务事实来源。用户名、测试数据、状态码和业务规则如果"
        "已在需求中明确，必须逐字遵循；不得用 admin、password、123456 等惯用值替代。",
        "- 只有需求、OpenAPI、已有 Secret 名称或前置节点输出能够证明来源的数据才可直接"
        "写入。无法确认的必填输入必须使用语义明确的 Runtime 变量并在 rationale 中标明"
        "待配置，严禁自行猜测。",
        "- 有副作用的创建流程必须提供精确 API_CLEANUP 或 SQL_CLEANUP 节点，"
        "并引用实际提取出的资源 ID。",
        "- 所有 HTTP/API_CLEANUP 方法和路径只能来自给定接口定义，不得编造。",
    )
)


def _requirement_source_snapshot(
    session: Session,
    project_id: int,
    requested_requirement_id: int | None,
    *,
    document_version_id: int | None = None,
    whole_document: bool = False,
) -> dict:
    statement = select(RequirementDocumentVersion).where(
        RequirementDocumentVersion.project_id == project_id
    )
    if document_version_id is not None:
        statement = statement.where(RequirementDocumentVersion.id == document_version_id)
    else:
        statement = statement.order_by(RequirementDocumentVersion.version_no.desc())
    document = session.scalar(statement)
    if document is None:
        raise ResourceConflictError("所选完整需求版本不存在，请重新选择")
    raw_nodes = [item for item in document.snapshot if isinstance(item, dict)]
    nodes = [
        item for item in raw_nodes
        if isinstance(item.get("requirement_id"), int)
    ]
    if not nodes:
        raise ResourceConflictError("所选完整需求版本没有可用于场景编排的需求")
    by_id = {int(item["requirement_id"]): item for item in nodes}
    requirement_id = requested_requirement_id
    if requirement_id is None and not whole_document:
        roots = [
            item for item in nodes
            if item.get("parent_id") is None or item.get("parent_id") not in by_id
        ]
        selected = (roots or nodes)[0]
        requirement_id = int(selected["requirement_id"])
    selected = by_id.get(requirement_id) if requirement_id is not None else None
    if requirement_id is not None and selected is None:
        raise ResourceConflictError("所选需求不属于指定的完整需求版本")

    if whole_document:
        scope_ids = set(by_id)
    else:
        scope_ids = {requirement_id}
        changed = True
        while changed:
            changed = False
            for item in nodes:
                item_id = int(item["requirement_id"])
                if item_id not in scope_ids and item.get("parent_id") in scope_ids:
                    scope_ids.add(item_id)
                    changed = True
    scope = [
        {
            "requirement_id": int(item["requirement_id"]),
            "code": str(item.get("code") or ""),
            "title": str(item.get("title") or ""),
            "requirement_type": str(item.get("type") or "FEATURE"),
            "outline_number": str(item.get("outline_number") or ""),
            "technical_version_id": item.get("technical_version_id"),
            "technical_version_no": item.get("technical_version_no"),
            "content": str(item.get("markdown_content") or ""),
        }
        for item in nodes
        if int(item["requirement_id"]) in scope_ids
    ]
    return {
        "requirement_id": requirement_id,
        "requirement_code": str(selected.get("code") or "") if selected else "ALL",
        "requirement_title": str(selected.get("title") or "") if selected else "整份需求",
        "scope_mode": "DOCUMENT" if whole_document else "SUBTREE",
        "document_version_id": document.id,
        "document_version_no": document.version_no,
        "document_content_hash": document.content_hash,
        "scope": scope,
    }


def _available_secret_metadata(session: Session, project_id: int) -> list[dict]:
    return [
        {
            "name": secret.name,
            "secret_type": secret.secret_type,
            "environment_id": secret.environment_id,
        }
        for secret in session.scalars(
            select(Secret)
            .where(Secret.project_id == project_id, Secret.enabled.is_(True))
            .order_by(Secret.name)
        ).all()
    ]


def _existing_for_file(
    session: Session, project_id: int, filename: str
) -> list[ApiDefinition]:
    return list(
        session.scalars(
            select(ApiDefinition).where(
                ApiDefinition.project_id == project_id,
                ApiDefinition.source == "SWAGGER",
                ApiDefinition.source_filename == filename,
            )
        ).all()
    )


def _existing_for_project(session: Session, project_id: int) -> list[ApiDefinition]:
    return list(
        session.scalars(
            select(ApiDefinition).where(ApiDefinition.project_id == project_id)
        ).all()
    )


def preview_import(
    session: Session, user: CurrentUser, payload: OpenApiImportRequest
) -> OpenApiPreviewResponse:
    get_project(session, user, payload.project_id)
    metadata, operations = parse_openapi(payload.content)
    existing = {
        (item.method, item.path): item
        for item in _existing_for_project(session, payload.project_id)
    }
    incoming_keys: set[tuple[str, str]] = set()
    for operation in operations:
        key = (operation.method, operation.path)
        incoming_keys.add(key)
        current = existing.get(key)
        if current is None:
            operation.diff_status = DiffStatus.ADDED
        elif (
            current.contract_hash != operation.contract_hash
            or current.status == ApiDefinitionStatus.REMOVED.value
        ):
            operation.diff_status = DiffStatus.CHANGED
            operation.existing_definition_id = current.id
        else:
            operation.diff_status = DiffStatus.UNCHANGED
            operation.existing_definition_id = current.id

    removed: list[ParsedOperation] = []
    if payload.mark_missing_removed:
        file_existing = {
            (item.method, item.path): item
            for item in _existing_for_file(session, payload.project_id, payload.filename)
        }
        for key, current in file_existing.items():
            if key in incoming_keys or current.status == ApiDefinitionStatus.REMOVED.value:
                continue
            removed.append(
                ParsedOperation(
                    name=current.name,
                    method=current.method,
                    path=current.path,
                    operation_id=current.operation_id,
                    summary=current.summary,
                    description=current.description,
                    parameters=current.parameters,
                    request_schema=current.request_schema,
                    response_schema=current.response_schema,
                    auth_info=current.auth_info,
                    tags=current.tags,
                    contract_hash=current.contract_hash,
                    diff_status=DiffStatus.REMOVED,
                    existing_definition_id=current.id,
                )
            )
    counts = {status: 0 for status in DiffStatus}
    for operation in [*operations, *removed]:
        counts[operation.diff_status] += 1
    return OpenApiPreviewResponse(
        filename=payload.filename,
        title=metadata["title"],
        spec_version=metadata["spec_version"],
        operations=operations,
        removed=removed,
        added_count=counts[DiffStatus.ADDED],
        changed_count=counts[DiffStatus.CHANGED],
        unchanged_count=counts[DiffStatus.UNCHANGED],
        removed_count=counts[DiffStatus.REMOVED],
    )


def import_openapi(
    session: Session, user: CurrentUser, payload: OpenApiImportRequest
) -> OpenApiImportResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        from app.core.exceptions import ResourceConflictError

        raise ResourceConflictError("归档项目不能导入 API 定义")

    metadata, _ = parse_openapi(payload.content)
    preview = preview_import(session, user, payload)
    latest = session.scalar(
        select(func.max(ApiDefinitionImport.version_no)).where(
            ApiDefinitionImport.project_id == payload.project_id,
            ApiDefinitionImport.source_filename == payload.filename,
        )
    )
    version_no = int(latest or 0) + 1
    import_record = ApiDefinitionImport(
        project_id=payload.project_id,
        source_filename=payload.filename,
        version_no=version_no,
        spec_version=metadata["spec_version"],
        title=metadata["title"],
        content_hash=metadata["content_hash"],
        raw_content=payload.content,
        imported_by=user.id,
    )
    session.add(import_record)
    session.flush()

    existing = {
        (item.method, item.path): item
        for item in _existing_for_project(session, payload.project_id)
    }
    created_count = updated_count = unchanged_count = 0
    for operation in preview.operations:
        values = operation.model_dump(
            exclude={"diff_status", "existing_definition_id"}
        )
        current = existing.get((operation.method, operation.path))
        if current is None:
            session.add(
                ApiDefinition(
                    project_id=payload.project_id,
                    import_id=import_record.id,
                    source="SWAGGER",
                    source_filename=payload.filename,
                    source_version=version_no,
                    status=ApiDefinitionStatus.ACTIVE.value,
                    created_by=user.id,
                    **values,
                )
            )
            created_count += 1
            continue
        if operation.diff_status == DiffStatus.UNCHANGED:
            unchanged_count += 1
        else:
            updated_count += 1
        for key, value in values.items():
            setattr(current, key, value)
        current.import_id = import_record.id
        current.source_filename = payload.filename
        current.source_version = version_no
        current.status = ApiDefinitionStatus.ACTIVE.value

    for operation in preview.removed:
        current = existing[(operation.method, operation.path)]
        current.status = ApiDefinitionStatus.REMOVED.value
        current.import_id = import_record.id
        current.source_version = version_no
    session.commit()
    return OpenApiImportResponse(
        import_id=import_record.id,
        version_no=version_no,
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        removed_count=len(preview.removed),
    )


def list_definitions(
    session: Session,
    user: CurrentUser,
    project_id: int,
    *,
    include_removed: bool = False,
) -> ApiDefinitionListResponse:
    get_project(session, user, project_id)
    statement = select(ApiDefinition).where(ApiDefinition.project_id == project_id)
    if not include_removed:
        statement = statement.where(ApiDefinition.status == ApiDefinitionStatus.ACTIVE.value)
    items = list(
        session.scalars(statement.order_by(ApiDefinition.path, ApiDefinition.method)).all()
    )
    return ApiDefinitionListResponse(
        items=[ApiDefinitionResponse.model_validate(item) for item in items],
        total=len(items),
    )


def get_definition(
    session: Session, user: CurrentUser, definition_id: int
) -> ApiDefinitionResponse:
    definition = session.get(ApiDefinition, definition_id)
    if definition is None:
        raise ResourceNotFoundError("API 定义不存在")
    get_project(session, user, definition.project_id)
    return ApiDefinitionResponse.model_validate(definition)


def _get_import(session: Session, import_id: int) -> ApiDefinitionImport:
    imported = session.get(ApiDefinitionImport, import_id)
    if imported is None:
        raise ResourceNotFoundError("OpenAPI 导入版本不存在")
    return imported


def _ensure_import_writable(
    session: Session, user: CurrentUser, imported: ApiDefinitionImport
) -> None:
    project = get_project(session, user, imported.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能生成或审核 Swagger AI 建议")


def _api_source_snapshot(
    session: Session,
    imported: ApiDefinitionImport,
    *,
    requirement_source: dict | None = None,
) -> tuple[dict, str, int]:
    definitions = list(
        session.scalars(
            select(ApiDefinition)
            .where(
                ApiDefinition.project_id == imported.project_id,
                ApiDefinition.import_id == imported.id,
                ApiDefinition.status == ApiDefinitionStatus.ACTIVE.value,
            )
            .order_by(ApiDefinition.path, ApiDefinition.method, ApiDefinition.id)
        ).all()
    )
    if not definitions:
        raise ResourceConflictError("该 OpenAPI 导入版本没有可生成建议的有效接口")
    if len(definitions) > _MAX_AI_SOURCE_OPERATIONS:
        raise ResourceConflictError("单次 Swagger AI 建议最多处理 200 个接口")
    snapshot = {
        "schema_version": 2 if requirement_source is not None else 1,
        "project_id": imported.project_id,
        "import_id": imported.id,
        "source_filename": imported.source_filename,
        "source_version": imported.version_no,
        "spec_version": imported.spec_version,
        "title": imported.title,
        "definitions": [
            {
                "id": item.id,
                "method": item.method,
                "path": item.path,
                "operation_id": item.operation_id,
                "summary": item.summary,
                "description": item.description,
                "parameters": item.parameters,
                "request_schema": item.request_schema,
                "response_schema": item.response_schema,
                "auth_info": item.auth_info,
                "tags": item.tags,
                "contract_hash": item.contract_hash,
            }
            for item in definitions
        ],
    }
    if requirement_source is not None:
        snapshot["requirement_source"] = requirement_source
        snapshot["available_secrets"] = _available_secret_metadata(
            session, imported.project_id
        )
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_AI_SOURCE_BYTES:
        raise ResourceConflictError("Swagger AI 建议来源快照超过 1MB")
    return snapshot, hashlib.sha256(encoded).hexdigest(), len(encoded)


def _normalized_request_path(url: str) -> str:
    value = url.strip().replace("{{base_url}}", "")
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme and parsed.netloc else value.split("?", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    path = _RUNTIME_PATH_VARIABLE_PATTERN.sub(r"{\1}", path)
    return path.rstrip("/") or "/"


def _definition_for_case(snapshot: dict, case: SuggestedCase) -> dict:
    if case.case_type != CaseType.API or case.request is None:
        raise ResourceConflictError("Swagger AI Case 必须是包含请求模板的 API 用例")
    method = case.request.method.value
    path = _normalized_request_path(case.request.url)
    matches = [
        item
        for item in snapshot["definitions"]
        if item["method"] == method
        and (str(item["path"]).rstrip("/") or "/") == path
    ]
    if len(matches) != 1:
        raise ResourceConflictError(
            f"AI Case 请求 {method} {path} 未唯一匹配固定 OpenAPI 接口"
        )
    return matches[0]


def _request_items_from_mapping(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return [
        {"name": str(name), "value": item if isinstance(item, str) else str(item)}
        for name, item in value.items()
    ]


def _normalize_scenario_request_config(config: dict, node_type: str) -> dict:
    normalized = dict(config)
    for field in ("query_params", "headers", "cookies"):
        normalized[field] = _request_items_from_mapping(normalized.get(field, []))

    headers = normalized.get("headers")
    if isinstance(headers, list):
        retained_headers: list[dict] = []
        for item in headers:
            if not isinstance(item, dict):
                retained_headers.append(item)
                continue
            name = str(item.get("name", ""))
            value = item.get("value")
            bearer_match = (
                re.fullmatch(r"Bearer\s+(\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\})", value)
                if name.lower() == "authorization" and isinstance(value, str)
                else None
            )
            if bearer_match and "auth" not in normalized:
                token = bearer_match.group(1)
                normalized["auth"] = (
                    {"type": "BEARER", "credential_ref": token}
                    if node_type == "API_CLEANUP"
                    else {"type": "BEARER", "token": token}
                )
                continue
            retained_headers.append(item)
        normalized["headers"] = retained_headers

    body = normalized.get("body")
    if isinstance(body, dict) and "type" not in body:
        content = body["json"] if set(body) == {"json"} else body
        normalized["body"] = {"type": "JSON", "content": content}
    if node_type == "API_CLEANUP":
        normalized.setdefault("cleanup_type", "API")
    method = normalized.get("method")
    if isinstance(method, str):
        normalized["method"] = method.upper()
    return normalized


def _normalize_scenario_result(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = json.loads(json.dumps(value))
    dsl = normalized.get("dsl")
    nodes = dsl.get("nodes") if isinstance(dsl, dict) else None
    if not isinstance(nodes, list):
        return normalized
    settings = dsl.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        dsl["settings"] = settings
    # AI-generated scenes start fail-closed. Users may explicitly relax the
    # scene switch later in the editor, where the effective policy is visible.
    settings["stop_on_failure"] = True
    node_types = {
        item.get("id"): str(item.get("type", "")).upper()
        for item in nodes
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    policy_aliases = {
        "abort": "STOP",
        "stop": "STOP",
        "continue": "CONTINUE",
        "retry": "RETRY_ONCE",
        "retry_once": "RETRY_ONCE",
    }
    for item in nodes:
        if not isinstance(item, dict):
            continue
        node_type = str(item.get("type", "")).upper()
        item["type"] = node_type
        policy = item.get("failure_policy")
        if isinstance(policy, str):
            item["failure_policy"] = policy_aliases.get(policy.lower(), policy)
        if node_type not in {"API_CLEANUP", "SQL_CLEANUP"} and item.get(
            "failure_policy"
        ) != "RETRY_ONCE":
            item["failure_policy"] = None
        description = item.get("description")
        if isinstance(description, str):
            item["description"] = description.strip()
        if node_type in {"START", "END"} and (
            not isinstance(item.get("timeout_ms"), int) or item.get("timeout_ms", 0) < 100
        ):
            item.pop("timeout_ms", None)
        parent_id = item.get("parent_id")
        if isinstance(parent_id, str) and node_types.get(parent_id) not in {
            "IF",
            "ELSE",
            "LOOP",
        }:
            item["parent_id"] = None
        config = item.get("config")
        if node_type in {"HTTP", "API_CLEANUP"} and isinstance(config, dict):
            item["config"] = _normalize_scenario_request_config(config, node_type)
    return normalized


def _validate_case_result(snapshot: dict, value: object) -> dict:
    try:
        result = CaseGenerationResult.model_validate(value)
    except ValidationError as exc:
        raise ResourceConflictError("AI 输出不符合 Swagger 用例建议结构") from exc
    for case in result.cases:
        _definition_for_case(snapshot, case)
    return result.model_dump(mode="json")


def _validate_scenario_result(snapshot: dict, value: object) -> dict:
    try:
        result = ApiScenarioRecommendation.model_validate(
            _normalize_scenario_result(value)
        )
        dsl = ScenarioDsl.model_validate(result.dsl)
    except ValidationError as exc:
        raise ResourceConflictError("AI 输出不符合 API Scenario 建议结构") from exc
    missing_descriptions = [
        node.id for node in dsl.nodes if not (node.description or "").strip()
    ]
    if missing_descriptions:
        raise ResourceConflictError(
            f"AI Scenario 节点缺少 description：{missing_descriptions[0]}"
        )
    # Swagger-generated requests conventionally use ``{{base_url}}``.  It is
    # supplied by the selected execution environment, so pin it as an explicit
    # initial variable when the model omitted the Scenario setting.
    if "{{base_url}}" in json.dumps(dsl.model_dump(mode="json"), ensure_ascii=False):
        normalized = dsl.model_dump(mode="json")
        initial_variables = normalized["settings"]["initial_variables"]
        if "base_url" not in initial_variables:
            initial_variables.append("base_url")
        dsl = ScenarioDsl.model_validate(normalized)
    for node in dsl.nodes:
        if _UNSUPPORTED_VARIABLE_PATTERN.search(
            json.dumps(node.config, ensure_ascii=False)
        ):
            raise ResourceConflictError(
                "AI Scenario 变量必须使用 {{name}}，不能使用 ${name}"
            )
    validation = validate_scenario_dsl(dsl)
    if not validation.valid:
        raise ResourceConflictError(
            f"AI Scenario DSL 校验失败：{validation.issues[0].message}"
        )
    source_keys = {
        (item["method"], str(item["path"]).rstrip("/") or "/")
        for item in snapshot["definitions"]
    }
    for node in dsl.nodes:
        if node.type.value not in {"HTTP", "API_CLEANUP"}:
            continue
        request = (
            node.config.get("request", node.config)
            if node.type.value == "HTTP" and isinstance(node.config, dict)
            else node.config
            if isinstance(node.config, dict)
            else None
        )
        if not isinstance(request, dict):
            raise ResourceConflictError(f"AI Scenario {node.type.value} 节点缺少请求配置")
        method = str(request.get("method", "")).upper()
        url = request.get("url")
        if not isinstance(url, str) or (
            method,
            _normalized_request_path(url),
        ) not in source_keys:
            raise ResourceConflictError(
                f"AI Scenario {node.type.value} 节点未匹配固定 OpenAPI 接口"
            )
    canonical = result.model_dump(mode="json")
    canonical["dsl"] = dsl.model_dump(mode="json")
    return canonical


def _validate_design_result(kind: str, snapshot: dict, value: object) -> dict:
    if kind == ApiDesignSuggestionKind.CASE_SET.value:
        return _validate_case_result(snapshot, value)
    return _validate_scenario_result(snapshot, value)


def _design_result_validator(kind: str, snapshot: dict):
    def validate(value: object) -> bool:
        try:
            _validate_design_result(kind, snapshot, value)
        except ResourceConflictError:
            return False
        return True

    return validate


def _validate_scenario_plan_result(snapshot: dict, value: object) -> dict:
    try:
        result = ApiScenarioPlanResult.model_validate(value)
    except ValidationError as exc:
        raise ResourceConflictError("AI 输出不符合场景方案目录结构") from exc
    requirement_source = snapshot.get("requirement_source")
    scope = requirement_source.get("scope", []) if isinstance(requirement_source, dict) else []
    allowed_requirement_ids = {
        int(item["requirement_id"])
        for item in scope
        if isinstance(item, dict) and isinstance(item.get("requirement_id"), int)
    }
    allowed_api_ids = {
        int(item["id"])
        for item in snapshot.get("definitions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    for candidate in result.candidates:
        if not set(candidate.requirement_ids).issubset(allowed_requirement_ids):
            raise ResourceConflictError("场景方案引用了所选需求范围之外的需求")
        if not set(candidate.api_definition_ids).issubset(allowed_api_ids):
            raise ResourceConflictError("场景方案引用了当前 OpenAPI 版本之外的接口")
    if not set(result.uncovered_requirement_ids).issubset(allowed_requirement_ids):
        raise ResourceConflictError("未覆盖需求列表包含所选范围之外的需求")
    return result.model_dump(mode="json")


def _scenario_plan_validator(snapshot: dict):
    def validate(value: object) -> bool:
        try:
            _validate_scenario_plan_result(snapshot, value)
        except ResourceConflictError:
            return False
        return True

    return validate


def _generation_instructions(
    kind: ApiDesignSuggestionKind,
    additional_instructions: str | None,
    requirement_source: dict | None = None,
    available_secrets: list[dict] | None = None,
    scenario_candidate: dict | None = None,
) -> str:
    user_instructions = additional_instructions.strip() if additional_instructions else "无"
    if kind != ApiDesignSuggestionKind.SCENARIO:
        return user_instructions
    if requirement_source is None:
        requirement_context = "本次任务是历史记录，未关联固定需求来源。"
    else:
        requirement_context = (
            "本次关联的固定需求来源如下。必须以该需求及其子需求为业务事实，不能用常识"
            "替换明确写出的账号、测试数据、状态码或业务约束：\n"
            f"{json.dumps(requirement_source['scope'], ensure_ascii=False)}"
        )
    secret_context = json.dumps(available_secrets or [], ensure_ascii=False)
    candidate_context = (
        "本次只生成下面这个已经过规划的候选场景，不要扩展成其他场景：\n"
        f"{json.dumps(scenario_candidate, ensure_ascii=False)}\n"
        if scenario_candidate is not None
        else ""
    )
    return (
        f"{_SCENARIO_DSL_GENERATION_CONTRACT}\n{requirement_context}\n{candidate_context}"
        "当前项目可引用的已启用 Secret 元数据如下，仅提供名称与类型，不包含真实值。"
        "若语义匹配，优先直接使用精确的 {{secret.NAME}} 引用：\n"
        f"{secret_context}\n用户补充要求：{user_instructions}"
    )


def _requirement_reference(source_value: dict) -> ApiDesignRequirementReference:
    scope = source_value.get("scope")
    return ApiDesignRequirementReference(
        id=(
            int(source_value["requirement_id"])
            if source_value.get("requirement_id") is not None
            else None
        ),
        code=str(source_value.get("requirement_code") or ""),
        title=str(source_value.get("requirement_title") or ""),
        document_version_id=int(source_value["document_version_id"]),
        document_version_no=int(source_value["document_version_no"]),
        scope_count=len(scope) if isinstance(scope, list) else 0,
        scope_mode=str(source_value.get("scope_mode") or "SUBTREE"),
    )


def _design_response(
    session: Session,
    suggestion: ApiDesignSuggestion,
    *,
    idempotent: bool = False,
    reused: bool = False,
) -> ApiDesignSuggestionResponse:
    imported = session.get(ApiDefinitionImport, suggestion.import_id)
    call = (
        session.get(AiCallLog, suggestion.ai_call_id)
        if suggestion.ai_call_id is not None
        else None
    )
    if imported is None:
        raise ResourceConflictError("Swagger AI 建议审计引用不可用")
    created_scenario = None
    if suggestion.created_scenario_id is not None:
        scenario = session.get(Scenario, suggestion.created_scenario_id)
        if scenario is not None and scenario.project_id == suggestion.project_id:
            version = (
                session.get(ScenarioVersion, scenario.current_version_id)
                if scenario.current_version_id is not None
                else None
            )
            created_scenario = ApiDesignScenarioReference(
                id=scenario.id,
                code=scenario.code,
                name=scenario.name,
                status=scenario.status,
                version_no=version.version_no if version is not None else None,
            )
    requirement_source = None
    source_value = suggestion.source_snapshot.get("requirement_source")
    if isinstance(source_value, dict):
        requirement_source = _requirement_reference(source_value)
    return ApiDesignSuggestionResponse(
        id=suggestion.id,
        project_id=suggestion.project_id,
        import_id=suggestion.import_id,
        source_filename=imported.source_filename,
        source_version=imported.version_no,
        prompt_id=suggestion.prompt_id,
        plan_item_id=suggestion.plan_item_id,
        ai_call_id=suggestion.ai_call_id,
        kind=suggestion.kind,
        status=suggestion.status,
        generation_status=suggestion.generation_status,
        source_snapshot_sha256=suggestion.source_snapshot_sha256,
        source_snapshot_size=suggestion.source_snapshot_size,
        additional_instructions=suggestion.additional_instructions,
        structured_result=suggestion.structured_result,
        human_result=suggestion.human_result,
        decision_note=suggestion.decision_note,
        created_test_case_ids=[int(item) for item in suggestion.created_test_case_ids],
        created_scenario_id=suggestion.created_scenario_id,
        created_scenario=created_scenario,
        requirement_source=requirement_source,
        actual_model=call.actual_model if call is not None else None,
        prompt_version_id=call.prompt_version_id if call is not None else None,
        output_schema_id=call.output_schema_id if call is not None else None,
        fallback_used=call.fallback_used if call is not None else False,
        repair_used=call.repair_used if call is not None else False,
        raw_response=redact_text(call.raw_response) if call is not None else "",
        created_by=suggestion.created_by,
        reviewed_by=suggestion.reviewed_by,
        created_at=suggestion.created_at,
        reviewed_at=suggestion.reviewed_at,
        started_at=suggestion.started_at,
        completed_at=suggestion.completed_at,
        error_message=suggestion.error_message,
        idempotent=idempotent,
        reused=reused,
    )


def _scenario_plan_response(
    session: Session, plan: ApiScenarioPlan, *, reused: bool = False
) -> ApiScenarioPlanResponse:
    imported = session.get(ApiDefinitionImport, plan.import_id)
    if imported is None:
        raise ResourceConflictError("场景方案引用的 OpenAPI 版本不可用")
    source = plan.source_snapshot.get("requirement_source")
    if not isinstance(source, dict):
        raise ResourceConflictError("场景方案缺少固定需求来源")
    call = session.get(AiCallLog, plan.ai_call_id) if plan.ai_call_id is not None else None
    items = list(
        session.scalars(
            select(ApiScenarioPlanItem)
            .where(ApiScenarioPlanItem.plan_id == plan.id)
            .order_by(ApiScenarioPlanItem.order_index, ApiScenarioPlanItem.id)
        ).all()
    )
    item_responses: list[ApiScenarioPlanItemResponse] = []
    for item in items:
        latest = session.scalar(
            select(ApiDesignSuggestion)
            .where(ApiDesignSuggestion.plan_item_id == item.id)
            .order_by(ApiDesignSuggestion.id.desc())
        )
        item_responses.append(
            ApiScenarioPlanItemResponse(
                id=item.id,
                candidate_key=item.candidate_key,
                order_index=item.order_index,
                name=item.name,
                objective=item.objective,
                category=item.category,
                priority=item.priority,
                rationale=item.rationale,
                requirement_ids=[int(value) for value in item.requirement_ids],
                api_definition_ids=[int(value) for value in item.api_definition_ids],
                api_flow=[str(value) for value in item.api_flow],
                preconditions=[str(value) for value in item.preconditions],
                expected_outcomes=[str(value) for value in item.expected_outcomes],
                cleanup_required=item.cleanup_required,
                latest_suggestion=(
                    _design_response(session, latest) if latest is not None else None
                ),
            )
        )
    result = plan.structured_result if isinstance(plan.structured_result, dict) else {}
    return ApiScenarioPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        import_id=plan.import_id,
        source_filename=imported.source_filename,
        source_version=imported.version_no,
        prompt_id=plan.prompt_id,
        ai_call_id=plan.ai_call_id,
        generation_status=plan.generation_status,
        requirement_source=_requirement_reference(source),
        summary=str(result.get("summary")) if result.get("summary") else None,
        items=item_responses,
        actual_model=call.actual_model if call is not None else None,
        fallback_used=call.fallback_used if call is not None else False,
        repair_used=call.repair_used if call is not None else False,
        created_at=plan.created_at,
        started_at=plan.started_at,
        completed_at=plan.completed_at,
        error_message=plan.error_message,
        reused=reused,
    )


def create_scenario_plan_task(
    session: Session,
    user: CurrentUser,
    import_id: int,
    payload: ApiScenarioPlanCreate,
) -> ApiScenarioPlanResponse:
    imported = _get_import(session, import_id)
    _ensure_import_writable(session, user, imported)
    requirement_source = _requirement_source_snapshot(
        session,
        imported.project_id,
        payload.requirement_id,
        document_version_id=payload.requirement_document_version_id,
        whole_document=payload.requirement_id is None,
    )
    snapshot, digest, size = _api_source_snapshot(
        session, imported, requirement_source=requirement_source
    )
    active = session.scalar(
        select(ApiScenarioPlan).where(
            ApiScenarioPlan.import_id == import_id,
            ApiScenarioPlan.active_key == "ACTIVE",
        )
    )
    if active is not None:
        if (
            active.prompt_id == payload.prompt_id
            and active.source_snapshot_sha256 == digest
            and active.additional_instructions == payload.additional_instructions
        ):
            return _scenario_plan_response(session, active, reused=True)
        raise ResourceConflictError("当前 OpenAPI 版本已有场景方案正在生成")
    plan = ApiScenarioPlan(
        project_id=imported.project_id,
        import_id=imported.id,
        prompt_id=payload.prompt_id,
        generation_status="QUEUED",
        active_key="ACTIVE",
        source_snapshot=snapshot,
        source_snapshot_sha256=digest,
        source_snapshot_size=size,
        additional_instructions=payload.additional_instructions,
        created_by=user.id,
    )
    session.add(plan)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("当前 OpenAPI 版本已有场景方案正在生成") from exc
    session.refresh(plan)
    return _scenario_plan_response(session, plan)


def process_scenario_plan_task(
    session_factory: Callable[[], Session], plan_id: int, user: CurrentUser
) -> None:
    with session_factory() as session:
        plan = session.get(ApiScenarioPlan, plan_id)
        if plan is None or plan.generation_status not in {"QUEUED", "RUNNING"}:
            return
        plan.generation_status = "RUNNING"
        plan.started_at = utc_now_naive()
        session.commit()
        ai_call_id: int | None = None
        try:
            imported = _get_import(session, plan.import_id)
            requirement_source = plan.source_snapshot["requirement_source"]
            ai_result = generate(
                session,
                user,
                AiGenerateRequest(
                    project_id=plan.project_id,
                    task_type="API_SCENARIO_PLAN",
                    prompt_id=plan.prompt_id,
                    variables={
                        "api_title": imported.title or imported.source_filename,
                        "api_definitions": plan.source_snapshot["definitions"],
                        "requirement_scope": json.dumps(
                            requirement_source["scope"], ensure_ascii=False
                        ),
                        "additional_instructions": plan.additional_instructions or "无",
                    },
                    entity_type="API_SCENARIO_PLAN",
                    entity_id=str(plan.id),
                ),
                result_validator=_scenario_plan_validator(plan.source_snapshot),
            )
            ai_call_id = ai_result.ai_call_id
            canonical = _validate_scenario_plan_result(
                plan.source_snapshot, ai_result.parsed_result
            )
            plan.ai_call_id = ai_call_id
            plan.structured_result = canonical
            for index, candidate in enumerate(canonical["candidates"], start=1):
                session.add(
                    ApiScenarioPlanItem(
                        plan_id=plan.id,
                        candidate_key=candidate["candidate_key"],
                        order_index=index,
                        name=candidate["name"],
                        objective=candidate["objective"],
                        category=candidate["category"],
                        priority=candidate["priority"],
                        rationale=candidate["rationale"],
                        requirement_ids=candidate["requirement_ids"],
                        api_definition_ids=candidate["api_definition_ids"],
                        api_flow=candidate["api_flow"],
                        preconditions=candidate["preconditions"],
                        expected_outcomes=candidate["expected_outcomes"],
                        cleanup_required=candidate["cleanup_required"],
                    )
                )
            plan.generation_status = "SUCCEEDED"
            plan.error_message = None
        except Exception as exc:
            logger.exception(
                "API_SCENARIO_PLAN_TASK_FAILED",
                extra={"plan_id": plan_id, "error_type": type(exc).__name__},
            )
            session.rollback()
            plan = session.get(ApiScenarioPlan, plan_id)
            if plan is None:
                return
            if ai_call_id is not None:
                plan.ai_call_id = ai_call_id
            plan.generation_status = "FAILED"
            message = exc.message if isinstance(exc, AppError) else "模型调用或输出校验失败"
            plan.error_message = redact_text(message)[:500]
        plan.active_key = None
        plan.completed_at = utc_now_naive()
        session.commit()


def list_scenario_plans(
    session: Session, user: CurrentUser, import_id: int
) -> ApiScenarioPlanListResponse:
    imported = _get_import(session, import_id)
    get_project(session, user, imported.project_id)
    plans = list(
        session.scalars(
            select(ApiScenarioPlan)
            .where(ApiScenarioPlan.import_id == import_id)
            .order_by(ApiScenarioPlan.id.desc())
        ).all()
    )
    return ApiScenarioPlanListResponse(
        items=[_scenario_plan_response(session, item) for item in plans],
        total=len(plans),
    )


def create_plan_item_suggestion_tasks(
    session: Session,
    user: CurrentUser,
    plan_id: int,
    payload: ApiScenarioPlanGenerate,
) -> tuple[ApiScenarioPlanGenerateResponse, list[int]]:
    plan = session.get(ApiScenarioPlan, plan_id)
    if plan is None:
        raise ResourceNotFoundError("场景方案目录不存在")
    imported = _get_import(session, plan.import_id)
    _ensure_import_writable(session, user, imported)
    if plan.generation_status != "SUCCEEDED":
        raise ResourceConflictError("场景方案目录尚未生成完成")
    items = list(
        session.scalars(
            select(ApiScenarioPlanItem)
            .where(
                ApiScenarioPlanItem.plan_id == plan.id,
                ApiScenarioPlanItem.id.in_(payload.item_ids),
            )
            .order_by(ApiScenarioPlanItem.order_index)
            .with_for_update()
        ).all()
    )
    if len(items) != len(payload.item_ids):
        raise ResourceConflictError("所选候选场景不属于当前方案目录")
    responses: list[ApiDesignSuggestionResponse] = []
    created_ids: list[int] = []
    for item in items:
        existing = session.scalar(
            select(ApiDesignSuggestion)
            .where(
                ApiDesignSuggestion.plan_item_id == item.id,
                ApiDesignSuggestion.status == "DRAFT",
                ApiDesignSuggestion.generation_status.in_(["QUEUED", "RUNNING", "SUCCEEDED"]),
            )
            .order_by(ApiDesignSuggestion.id.desc())
        )
        if existing is not None:
            responses.append(_design_response(session, existing, reused=True))
            continue
        candidate = {
            "candidate_key": item.candidate_key,
            "name": item.name,
            "objective": item.objective,
            "category": item.category,
            "priority": item.priority,
            "rationale": item.rationale,
            "requirement_ids": item.requirement_ids,
            "api_definition_ids": item.api_definition_ids,
            "api_flow": item.api_flow,
            "preconditions": item.preconditions,
            "expected_outcomes": item.expected_outcomes,
            "cleanup_required": item.cleanup_required,
        }
        snapshot = json.loads(json.dumps(plan.source_snapshot))
        snapshot["scenario_candidate"] = candidate
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_AI_SOURCE_BYTES:
            raise ResourceConflictError("候选场景来源快照超过 1MB")
        combined_instructions = "\n".join(
            value.strip()
            for value in [plan.additional_instructions, payload.additional_instructions]
            if value and value.strip()
        ) or None
        suggestion = ApiDesignSuggestion(
            project_id=plan.project_id,
            import_id=plan.import_id,
            prompt_id=payload.prompt_id,
            plan_item_id=item.id,
            kind=ApiDesignSuggestionKind.SCENARIO.value,
            status="DRAFT",
            generation_status="QUEUED",
            draft_key=None,
            source_snapshot=snapshot,
            source_snapshot_sha256=hashlib.sha256(encoded).hexdigest(),
            source_snapshot_size=len(encoded),
            additional_instructions=combined_instructions,
            structured_result=None,
            created_test_case_ids=[],
            created_by=user.id,
        )
        session.add(suggestion)
        session.flush()
        created_ids.append(suggestion.id)
        responses.append(_design_response(session, suggestion))
    session.commit()
    return ApiScenarioPlanGenerateResponse(suggestions=responses), created_ids


def create_design_suggestion_task(
    session: Session,
    user: CurrentUser,
    import_id: int,
    payload: ApiDesignSuggestionCreate,
) -> ApiDesignSuggestionResponse:
    imported = _get_import(session, import_id)
    _ensure_import_writable(session, user, imported)
    requirement_source = (
        _requirement_source_snapshot(
            session,
            imported.project_id,
            payload.requirement_id,
            document_version_id=payload.requirement_document_version_id,
        )
        if payload.kind == ApiDesignSuggestionKind.SCENARIO
        else None
    )
    resolved_requirement_id = (
        int(requirement_source["requirement_id"])
        if requirement_source is not None
        else None
    )
    existing = session.scalar(
        select(ApiDesignSuggestion).where(
            ApiDesignSuggestion.import_id == import_id,
            ApiDesignSuggestion.kind == payload.kind.value,
            ApiDesignSuggestion.draft_key == "DRAFT",
        )
    )
    if existing is not None:
        existing_requirement = existing.source_snapshot.get("requirement_source")
        existing_requirement_id = (
            int(existing_requirement["requirement_id"])
            if isinstance(existing_requirement, dict)
            and existing_requirement.get("requirement_id") is not None
            else None
        )
        if (
            existing.generation_status in {"QUEUED", "RUNNING"}
            and existing.prompt_id == payload.prompt_id
            and existing.additional_instructions == payload.additional_instructions
            and existing_requirement_id == resolved_requirement_id
        ):
            return _design_response(session, existing, reused=True)
        if existing.generation_status in {"QUEUED", "RUNNING"}:
            raise ResourceConflictError(
                "该导入版本已有同类型 AI 编排正在生成，请从建议历史查看"
            )
        raise ResourceConflictError("该导入版本已有同类型待审核 AI 建议")
    snapshot, digest, size = _api_source_snapshot(
        session, imported, requirement_source=requirement_source
    )
    suggestion = ApiDesignSuggestion(
        project_id=imported.project_id,
        import_id=imported.id,
        prompt_id=payload.prompt_id,
        ai_call_id=None,
        kind=payload.kind.value,
        status="DRAFT",
        generation_status="QUEUED",
        draft_key="DRAFT",
        source_snapshot=snapshot,
        source_snapshot_sha256=digest,
        source_snapshot_size=size,
        additional_instructions=payload.additional_instructions,
        structured_result=None,
        created_test_case_ids=[],
        created_by=user.id,
    )
    session.add(suggestion)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("该导入版本已有同类型待审核 AI 建议") from exc
    session.refresh(suggestion)
    return _design_response(session, suggestion)


def process_design_suggestion_task(
    session_factory: Callable[[], Session], suggestion_id: int, user: CurrentUser
) -> None:
    """Generate and validate one persisted OpenAPI design task in the background."""

    with session_factory() as session:
        suggestion = session.get(ApiDesignSuggestion, suggestion_id)
        if suggestion is None or suggestion.generation_status not in {"QUEUED", "RUNNING"}:
            return
        suggestion.generation_status = "RUNNING"
        suggestion.started_at = utc_now_naive()
        session.commit()
        ai_call_id: int | None = None
        try:
            imported = _get_import(session, suggestion.import_id)
            if suggestion.prompt_id is None:
                raise ResourceConflictError("AI 编排任务缺少生成 Prompt")
            kind = ApiDesignSuggestionKind(suggestion.kind)
            task_type = (
                "API_CASE_GENERATE"
                if kind == ApiDesignSuggestionKind.CASE_SET
                else "API_SCENARIO_GENERATE"
            )
            ai_result = generate(
                session,
                user,
                AiGenerateRequest(
                    project_id=imported.project_id,
                    task_type=task_type,
                    prompt_id=suggestion.prompt_id,
                    variables={
                        "api_definitions": suggestion.source_snapshot["definitions"],
                        "api_title": imported.title or imported.source_filename,
                        "requirement_scope": json.dumps(
                            suggestion.source_snapshot.get("requirement_source", {}).get(
                                "scope", []
                            ),
                            ensure_ascii=False,
                        ),
                        "additional_instructions": _generation_instructions(
                            kind,
                            suggestion.additional_instructions,
                            suggestion.source_snapshot.get("requirement_source"),
                            suggestion.source_snapshot.get("available_secrets"),
                            suggestion.source_snapshot.get("scenario_candidate"),
                        ),
                    },
                    entity_type="API_DEFINITION_IMPORT",
                    entity_id=str(imported.id),
                ),
                result_validator=_design_result_validator(
                    suggestion.kind, suggestion.source_snapshot
                ),
            )
            ai_call_id = ai_result.ai_call_id
            suggestion.ai_call_id = ai_call_id
            suggestion.structured_result = _validate_design_result(
                suggestion.kind,
                suggestion.source_snapshot,
                ai_result.parsed_result,
            )
            suggestion.generation_status = "SUCCEEDED"
            suggestion.error_message = None
        except Exception as exc:
            logger.exception(
                "API_DESIGN_SUGGESTION_TASK_FAILED",
                extra={"suggestion_id": suggestion_id, "error_type": type(exc).__name__},
            )
            session.rollback()
            suggestion = session.get(ApiDesignSuggestion, suggestion_id)
            if suggestion is None:
                return
            if ai_call_id is not None:
                suggestion.ai_call_id = ai_call_id
            suggestion.generation_status = "FAILED"
            suggestion.draft_key = None
            message = exc.message if isinstance(exc, AppError) else "模型调用或输出校验失败"
            suggestion.error_message = redact_text(message)[:500]
        suggestion.completed_at = utc_now_naive()
        session.commit()


def list_design_suggestions(
    session: Session, user: CurrentUser, import_id: int
) -> ApiDesignSuggestionListResponse:
    imported = _get_import(session, import_id)
    get_project(session, user, imported.project_id)
    items = list(
        session.scalars(
            select(ApiDesignSuggestion)
            .where(ApiDesignSuggestion.import_id == import_id)
            .order_by(ApiDesignSuggestion.id.desc())
        ).all()
    )
    return ApiDesignSuggestionListResponse(
        items=[_design_response(session, item) for item in items], total=len(items)
    )


def edit_design_suggestion(
    session: Session,
    user: CurrentUser,
    suggestion_id: int,
    payload: ApiDesignSuggestionEdit,
) -> ApiDesignSuggestionResponse:
    suggestion = session.get(ApiDesignSuggestion, suggestion_id)
    if suggestion is None:
        raise ResourceNotFoundError("Swagger AI 建议不存在")
    imported = _get_import(session, suggestion.import_id)
    _ensure_import_writable(session, user, imported)
    if suggestion.status != "DRAFT":
        raise ResourceConflictError("已决策的 Swagger AI 建议不能编辑")
    if suggestion.generation_status != "SUCCEEDED":
        raise ResourceConflictError("AI 建议尚未生成成功，不能编辑")
    suggestion.human_result = _validate_design_result(
        suggestion.kind, suggestion.source_snapshot, payload.human_result
    )
    suggestion.decision_note = payload.decision_note
    suggestion.reviewed_by = user.id
    session.commit()
    session.refresh(suggestion)
    return _design_response(session, suggestion)


def _source_definition(
    session: Session, suggestion: ApiDesignSuggestion, source: dict
) -> ApiDefinition:
    definition = session.get(ApiDefinition, int(source["id"]))
    if (
        definition is None
        or definition.project_id != suggestion.project_id
        or definition.contract_hash != source["contract_hash"]
        or definition.status != ApiDefinitionStatus.ACTIVE.value
    ):
        raise ResourceConflictError("OpenAPI 接口已变化，请重新生成 AI 建议")
    return definition


def _accept_case_set(
    session: Session,
    user: CurrentUser,
    suggestion: ApiDesignSuggestion,
    result: dict,
) -> list[int]:
    case_set = CaseGenerationResult.model_validate(result)
    created_ids: list[int] = []
    for content in case_set.cases:
        source = _definition_for_case(suggestion.source_snapshot, content)
        definition = _source_definition(session, suggestion, source)
        _ensure_v1_api_retry_policy(content)
        _validate_data_source(session, suggestion.project_id, content)
        _validate_assertions(session, suggestion.project_id, content)
        _validate_cleanup_configs(session, suggestion.project_id, content)
        case = TestCase(
            project_id=suggestion.project_id,
            api_definition_id=definition.id,
            code=next_project_business_code(
                session,
                project_id=suggestion.project_id,
                namespace=BusinessCodeNamespace.TEST_CASE,
                model=TestCase,
            ),
            name=content.title,
            case_type=CaseType.API.value,
            status="ACTIVE",
            source="AI",
            created_by=user.id,
        )
        session.add(case)
        session.flush()
        version = TestCaseVersion(
            case_id=case.id,
            version_no=1,
            content=content.model_dump(mode="json"),
            change_note=(
                "OpenAPI AI 建议经人工确认；"
                f"接口 {definition.method} {definition.path}"
            ),
            created_by=user.id,
        )
        session.add(version)
        session.flush()
        case.current_version_id = version.id
        created_ids.append(case.id)
    return created_ids


def _accept_scenario(
    session: Session,
    user: CurrentUser,
    suggestion: ApiDesignSuggestion,
    result: dict,
) -> int:
    recommendation = ApiScenarioRecommendation.model_validate(result)
    dsl = ScenarioDsl.model_validate(recommendation.dsl)
    _ensure_valid_dsl(session, user, suggestion.project_id, dsl)
    for node in dsl.nodes:
        if node.type.value != "HTTP":
            continue
        request = node.config.get("request", node.config)
        source = next(
            item
            for item in suggestion.source_snapshot["definitions"]
            if item["method"] == str(request["method"]).upper()
            and (str(item["path"]).rstrip("/") or "/")
            == _normalized_request_path(str(request["url"]))
        )
        _source_definition(session, suggestion, source)
    scenario = Scenario(
        project_id=suggestion.project_id,
        code=next_project_business_code(
            session,
            project_id=suggestion.project_id,
            namespace=BusinessCodeNamespace.SCENARIO,
            model=Scenario,
        ),
        name=recommendation.name,
        status="DRAFT",
        created_by=user.id,
    )
    session.add(scenario)
    session.flush()
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version_no=1,
        dsl=dsl.model_dump(mode="json"),
        change_note="OpenAPI AI 建议经人工确认生成",
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    scenario.current_version_id = version.id
    return scenario.id


def decide_design_suggestion(
    session: Session,
    user: CurrentUser,
    suggestion_id: int,
    payload: ApiDesignSuggestionDecision,
) -> ApiDesignSuggestionResponse:
    suggestion = session.get(ApiDesignSuggestion, suggestion_id)
    if suggestion is None:
        raise ResourceNotFoundError("Swagger AI 建议不存在")
    imported = _get_import(session, suggestion.import_id)
    _ensure_import_writable(session, user, imported)
    if suggestion.generation_status != "SUCCEEDED":
        raise ResourceConflictError("AI 建议尚未生成成功，不能审核")
    if suggestion.status != "DRAFT":
        return _design_response(session, suggestion, idempotent=True)
    suggestion.decision_note = payload.decision_note or suggestion.decision_note
    suggestion.reviewed_by = user.id
    suggestion.reviewed_at = utc_now_naive()
    suggestion.draft_key = None
    if payload.action == "REJECT":
        suggestion.status = "REJECTED"
    else:
        result = suggestion.human_result or suggestion.structured_result
        canonical = _validate_design_result(
            suggestion.kind, suggestion.source_snapshot, result
        )
        if suggestion.kind == ApiDesignSuggestionKind.CASE_SET.value:
            suggestion.created_test_case_ids = _accept_case_set(
                session, user, suggestion, canonical
            )
        else:
            suggestion.created_scenario_id = _accept_scenario(
                session, user, suggestion, canonical
            )
        suggestion.status = "ACCEPTED"
    session.commit()
    session.refresh(suggestion)
    return _design_response(session, suggestion)
