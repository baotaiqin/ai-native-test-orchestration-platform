import re
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AuthorizationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.core.redaction import redact_text, redact_value
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.models import ModelConfiguration
from app.modules.projects.service import ensure_project_writable, get_project, is_admin
from app.modules.prompt_center.builtin import ensure_builtin_prompts, get_builtin_prompt_factory
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.prompt_center.schemas import (
    AiCallLogListResponse,
    AiCallLogResponse,
    OutputSchemaCreate,
    OutputSchemaListResponse,
    OutputSchemaResponse,
    OutputSchemaVersionCreate,
    ProjectPromptTemplateResponse,
    ProjectPromptVersionCreate,
    PromptCopyRequest,
    PromptCreate,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptResponse,
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionResponse,
    StructuredOutputValidateRequest,
    StructuredOutputValidateResponse,
    SystemPromptTemplateUpdate,
)
from app.modules.prompt_center.validator import (
    deterministic_repair,
    parse_json_once,
    validate_json_schema,
)

VARIABLE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        raise AuthorizationError("只有管理员可以维护 Prompt")


def _get_prompt(session: Session, prompt_id: int) -> PromptDefinition:
    prompt = session.get(PromptDefinition, prompt_id)
    if prompt is None:
        raise ResourceNotFoundError("Prompt 不存在")
    return prompt


def _current_version(
    session: Session, prompt: PromptDefinition
) -> PromptVersion | None:
    if prompt.current_version_id is None:
        return None
    return session.get(PromptVersion, prompt.current_version_id)


def _validate_schema(session: Session, schema_id: int | None) -> None:
    if schema_id is None:
        return
    schema = session.get(OutputSchema, schema_id)
    if schema is None or not schema.enabled:
        raise ResourceConflictError("输出 Schema 不存在或已停用")


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Prompt 编码已存在") from exc


def prompt_response(session: Session, prompt: PromptDefinition) -> PromptResponse:
    current = _current_version(session, prompt)
    return PromptResponse(
        **PromptResponse.model_validate(prompt).model_dump(exclude={"current_version"}),
        current_version=(PromptVersionResponse.model_validate(current) if current else None),
    )


def list_prompts(
    session: Session,
    user: CurrentUser,
    *,
    task_type: str | None = None,
    include_disabled: bool = False,
) -> list[PromptResponse]:
    ensure_builtin_prompts(session)
    statement = select(PromptDefinition).where(PromptDefinition.scope == "SYSTEM")
    if task_type:
        statement = statement.where(PromptDefinition.task_type == task_type)
    if not include_disabled:
        statement = statement.where(PromptDefinition.enabled.is_(True))
    prompts = list(session.scalars(statement.order_by(PromptDefinition.name)).all())
    return [prompt_response(session, prompt) for prompt in prompts]


def create_prompt(
    session: Session, user: CurrentUser, payload: PromptCreate
) -> PromptResponse:
    _require_admin(user)
    _validate_schema(session, payload.output_schema_id)
    prompt = PromptDefinition(
        name=payload.name.strip(),
        code=payload.code.upper(),
        task_type=payload.task_type.value,
        scope="SYSTEM",
        is_builtin=False,
        description=payload.description.strip() if payload.description else None,
        enabled=True,
        created_by=user.id,
    )
    session.add(prompt)
    session.flush()
    version = PromptVersion(
        prompt_id=prompt.id,
        version_no=1,
        system_prompt=payload.system_prompt,
        user_template=payload.user_template,
        output_schema_id=payload.output_schema_id,
        change_note="初始版本",
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    prompt.current_version_id = version.id
    _commit(session)
    session.refresh(prompt)
    return prompt_response(session, prompt)


def update_prompt(
    session: Session, user: CurrentUser, prompt_id: int, payload: PromptUpdate
) -> PromptResponse:
    _require_admin(user)
    prompt = _get_prompt(session, prompt_id)
    if prompt.is_builtin:
        raise ResourceConflictError("系统默认模板元数据不可修改，请使用模板内容维护接口")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(prompt, key, value)
    session.commit()
    session.refresh(prompt)
    return prompt_response(session, prompt)


def create_version(
    session: Session,
    user: CurrentUser,
    prompt_id: int,
    payload: PromptVersionCreate,
) -> PromptResponse:
    _require_admin(user)
    prompt = _get_prompt(session, prompt_id)
    if prompt.scope == "SYSTEM":
        raise ResourceConflictError("公共模板不创建版本，请直接更新当前内容")
    _validate_schema(session, payload.output_schema_id)
    current = _current_version(session, prompt)
    if current and (
        current.system_prompt == payload.system_prompt
        and current.user_template == payload.user_template
        and current.output_schema_id == payload.output_schema_id
    ):
        raise ResourceConflictError("Prompt 内容未发生变化")
    latest = session.scalar(
        select(func.max(PromptVersion.version_no)).where(
            PromptVersion.prompt_id == prompt_id
        )
    )
    version = PromptVersion(
        prompt_id=prompt_id,
        version_no=int(latest or 0) + 1,
        system_prompt=payload.system_prompt,
        user_template=payload.user_template,
        output_schema_id=payload.output_schema_id,
        change_note=payload.change_note,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    prompt.current_version_id = version.id
    session.commit()
    session.refresh(prompt)
    return prompt_response(session, prompt)


def update_system_prompt_template(
    session: Session,
    user: CurrentUser,
    prompt_id: int,
    payload: SystemPromptTemplateUpdate,
) -> PromptResponse:
    _require_admin(user)
    prompt = _get_prompt(session, prompt_id)
    if prompt.scope != "SYSTEM":
        raise ResourceConflictError("目标不是公共模板")
    _validate_schema(session, payload.output_schema_id)
    version = _current_version(session, prompt)
    if version is None:
        raise ResourceConflictError("公共模板没有当前内容")
    if (
        version.system_prompt == payload.system_prompt
        and version.user_template == payload.user_template
        and version.output_schema_id == payload.output_schema_id
    ):
        return prompt_response(session, prompt)
    version.system_prompt = payload.system_prompt
    version.user_template = payload.user_template
    version.output_schema_id = payload.output_schema_id
    version.change_note = "管理员更新公共模板"
    version.created_by = user.id
    session.commit()
    session.refresh(prompt)
    return prompt_response(session, prompt)


def restore_system_prompt_template(
    session: Session,
    user: CurrentUser,
    prompt_id: int,
) -> PromptResponse:
    _require_admin(user)
    prompt = _get_prompt(session, prompt_id)
    if not prompt.is_builtin or prompt.scope != "SYSTEM":
        raise ResourceConflictError("目标不是系统默认模板")
    factory = get_builtin_prompt_factory(prompt.code)
    if factory is None:
        raise ResourceConflictError("系统初始模板不存在")
    system_prompt, user_template, result_type = factory
    ensure_builtin_prompts(session)
    schema_name = f"{prompt.name} Schema"
    schema = session.scalar(
        select(OutputSchema)
        .where(OutputSchema.name == schema_name, OutputSchema.enabled.is_(True))
        .order_by(OutputSchema.version_no.desc())
    )
    if schema is None or schema.schema_json != result_type.model_json_schema():
        raise ResourceConflictError("系统初始输出 Schema 不可用")
    version = _current_version(session, prompt)
    if version is None:
        raise ResourceConflictError("系统默认模板没有当前内容")
    version.system_prompt = system_prompt
    version.user_template = user_template
    version.output_schema_id = schema.id
    version.change_note = "恢复系统初始模板"
    version.created_by = user.id
    session.commit()
    session.refresh(prompt)
    return prompt_response(session, prompt)


def list_versions(
    session: Session, user: CurrentUser, prompt_id: int
) -> list[PromptVersion]:
    _get_prompt(session, prompt_id)
    return list(
        session.scalars(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version_no.desc())
        ).all()
    )


def rollback_version(
    session: Session, user: CurrentUser, prompt_id: int, version_id: int
) -> PromptResponse:
    _require_admin(user)
    prompt = _get_prompt(session, prompt_id)
    if prompt.scope == "SYSTEM":
        raise ResourceConflictError("公共模板不提供版本回滚")
    version = session.get(PromptVersion, version_id)
    if version is None or version.prompt_id != prompt_id:
        raise ResourceNotFoundError("Prompt 版本不存在")
    prompt.current_version_id = version.id
    session.commit()
    session.refresh(prompt)
    return prompt_response(session, prompt)


def _project_prompt_response(
    session: Session,
    base: PromptDefinition,
    override: PromptDefinition | None,
) -> ProjectPromptTemplateResponse:
    effective = override if override is not None and override.enabled else base
    version = _current_version(session, effective)
    if version is None:
        raise ResourceConflictError("Prompt 没有当前版本")
    return ProjectPromptTemplateResponse(
        base_prompt_id=base.id,
        name=base.name,
        code=base.code,
        task_type=base.task_type,
        description=base.description,
        using_system_default=effective.id == base.id,
        project_prompt_id=override.id if override is not None else None,
        project_version_no=(version.version_no if effective.id != base.id else None),
        system_prompt=version.system_prompt,
        user_template=version.user_template,
        output_schema_id=version.output_schema_id,
        change_note=version.change_note if effective.id != base.id else None,
        updated_at=effective.updated_at,
    )


def list_project_prompt_templates(
    session: Session,
    user: CurrentUser,
    project_id: int,
) -> list[ProjectPromptTemplateResponse]:
    get_project(session, user, project_id)
    ensure_builtin_prompts(session)
    bases = list(
        session.scalars(
            select(PromptDefinition)
            .where(
                PromptDefinition.scope == "SYSTEM",
                PromptDefinition.is_builtin.is_(True),
                PromptDefinition.enabled.is_(True),
            )
            .order_by(PromptDefinition.name)
        ).all()
    )
    overrides = {
        item.base_prompt_id: item
        for item in session.scalars(
            select(PromptDefinition).where(
                PromptDefinition.scope == "PROJECT",
                PromptDefinition.project_id == project_id,
            )
        ).all()
    }
    return [
        _project_prompt_response(session, base, overrides.get(base.id)) for base in bases
    ]


def save_project_prompt_version(
    session: Session,
    user: CurrentUser,
    project_id: int,
    base_prompt_id: int,
    payload: ProjectPromptVersionCreate,
) -> ProjectPromptTemplateResponse:
    project = get_project(session, user, project_id)
    ensure_project_writable(session, project, user)
    base = _get_prompt(session, base_prompt_id)
    if base.scope != "SYSTEM" or not base.is_builtin or not base.enabled:
        raise ResourceConflictError("只能基于启用的系统默认模板创建项目版本")
    _validate_schema(session, payload.output_schema_id)
    override = session.scalar(
        select(PromptDefinition).where(
            PromptDefinition.scope == "PROJECT",
            PromptDefinition.project_id == project_id,
            PromptDefinition.base_prompt_id == base_prompt_id,
        )
    )
    if override is None:
        override = PromptDefinition(
            name=f"{base.name} · {project.name}",
            code=f"PROJECT_{project_id}_{base.id}",
            task_type=base.task_type,
            scope="PROJECT",
            project_id=project_id,
            base_prompt_id=base.id,
            is_builtin=False,
            description=f"项目 {project.name} 对 {base.name} 的定制版本",
            enabled=True,
            created_by=user.id,
        )
        session.add(override)
        session.flush()
    current = _current_version(session, override)
    if current and (
        current.system_prompt == payload.system_prompt
        and current.user_template == payload.user_template
        and current.output_schema_id == payload.output_schema_id
    ):
        raise ResourceConflictError("项目提示词内容未发生变化")
    latest = session.scalar(
        select(func.max(PromptVersion.version_no)).where(
            PromptVersion.prompt_id == override.id
        )
    )
    version = PromptVersion(
        prompt_id=override.id,
        version_no=int(latest or 0) + 1,
        system_prompt=payload.system_prompt,
        user_template=payload.user_template,
        output_schema_id=payload.output_schema_id,
        change_note=payload.change_note.strip(),
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    override.current_version_id = version.id
    override.enabled = True
    session.commit()
    session.refresh(override)
    return _project_prompt_response(session, base, override)


def restore_project_prompt_default(
    session: Session,
    user: CurrentUser,
    project_id: int,
    base_prompt_id: int,
) -> ProjectPromptTemplateResponse:
    project = get_project(session, user, project_id)
    ensure_project_writable(session, project, user)
    base = _get_prompt(session, base_prompt_id)
    if base.scope != "SYSTEM" or not base.is_builtin or not base.enabled:
        raise ResourceConflictError("系统默认模板不可用")
    override = session.scalar(
        select(PromptDefinition).where(
            PromptDefinition.scope == "PROJECT",
            PromptDefinition.project_id == project_id,
            PromptDefinition.base_prompt_id == base_prompt_id,
        )
    )
    if override is not None:
        override.enabled = False
        session.commit()
        session.refresh(override)
    return _project_prompt_response(session, base, override)


def list_project_prompt_versions(
    session: Session,
    user: CurrentUser,
    project_id: int,
    base_prompt_id: int,
) -> list[PromptVersion]:
    get_project(session, user, project_id)
    override = session.scalar(
        select(PromptDefinition).where(
            PromptDefinition.scope == "PROJECT",
            PromptDefinition.project_id == project_id,
            PromptDefinition.base_prompt_id == base_prompt_id,
        )
    )
    if override is None:
        return []
    return list(
        session.scalars(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == override.id)
            .order_by(PromptVersion.version_no.desc())
        ).all()
    )


def resolve_effective_prompt(
    session: Session,
    project_id: int,
    prompt: PromptDefinition,
) -> PromptDefinition:
    """Resolve a selected public template to the active project override."""

    base = prompt
    if prompt.scope == "PROJECT":
        if prompt.project_id != project_id:
            raise ResourceNotFoundError("Prompt 不存在或无权访问")
        return prompt
    override = session.scalar(
        select(PromptDefinition).where(
            PromptDefinition.scope == "PROJECT",
            PromptDefinition.project_id == project_id,
            PromptDefinition.base_prompt_id == base.id,
            PromptDefinition.enabled.is_(True),
        )
    )
    return override or base


def copy_prompt(
    session: Session, user: CurrentUser, prompt_id: int, payload: PromptCopyRequest
) -> PromptResponse:
    _require_admin(user)
    source = _get_prompt(session, prompt_id)
    current = _current_version(session, source)
    if current is None:
        raise ResourceConflictError("源 Prompt 没有可复制版本")
    return create_prompt(
        session,
        user,
        PromptCreate(
            name=payload.name,
            code=payload.code,
            task_type=source.task_type,
            description=f"复制自 {source.code}",
            system_prompt=current.system_prompt,
            user_template=current.user_template,
            output_schema_id=current.output_schema_id,
        ),
    )


def render_prompt(
    session: Session,
    user: CurrentUser,
    prompt_id: int,
    payload: PromptRenderRequest,
) -> PromptRenderResponse:
    prompt = _get_prompt(session, prompt_id)
    version = _current_version(session, prompt)
    if version is None:
        raise ResourceConflictError("Prompt 没有当前版本")
    required = sorted(
        set(VARIABLE_PATTERN.findall(version.system_prompt + version.user_template))
    )
    missing = [name for name in required if name not in payload.variables]

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(payload.variables.get(name, match.group(0)))

    return PromptRenderResponse(
        prompt_id=prompt.id,
        prompt_version_id=version.id,
        system_prompt=VARIABLE_PATTERN.sub(replace, version.system_prompt),
        user_prompt=VARIABLE_PATTERN.sub(replace, version.user_template),
        required_variables=required,
        missing_variables=missing,
    )


def _validate_schema_document(schema_json: dict) -> None:
    if not schema_json:
        raise ResourceConflictError("Schema 不能为空")
    schema_type = schema_json.get("type")
    if schema_type not in {"object", "array"}:
        raise ResourceConflictError("输出 Schema 根类型必须为 object 或 array")


def list_output_schemas(
    session: Session, user: CurrentUser, *, include_disabled: bool = False
) -> OutputSchemaListResponse:
    statement = select(OutputSchema)
    if not include_disabled:
        statement = statement.where(OutputSchema.enabled.is_(True))
    items = list(
        session.scalars(
            statement.order_by(OutputSchema.name, OutputSchema.version_no.desc())
        ).all()
    )
    return OutputSchemaListResponse(
        items=[OutputSchemaResponse.model_validate(item) for item in items],
        total=len(items),
    )


def create_output_schema(
    session: Session, user: CurrentUser, payload: OutputSchemaCreate
) -> OutputSchemaResponse:
    _require_admin(user)
    _validate_schema_document(payload.json_schema)
    schema = OutputSchema(
        name=payload.name.strip(),
        version_no=1,
        description=payload.description,
        schema_json=payload.json_schema,
        enabled=True,
        created_by=user.id,
    )
    session.add(schema)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Output Schema 名称已存在") from exc
    session.refresh(schema)
    return OutputSchemaResponse.model_validate(schema)


def create_output_schema_version(
    session: Session,
    user: CurrentUser,
    schema_id: int,
    payload: OutputSchemaVersionCreate,
) -> OutputSchemaResponse:
    _require_admin(user)
    source = session.get(OutputSchema, schema_id)
    if source is None:
        raise ResourceNotFoundError("Output Schema 不存在")
    _validate_schema_document(payload.json_schema)
    latest = session.scalar(
        select(func.max(OutputSchema.version_no)).where(OutputSchema.name == source.name)
    )
    schema = OutputSchema(
        name=source.name,
        version_no=int(latest or 0) + 1,
        description=payload.description,
        schema_json=payload.json_schema,
        enabled=True,
        created_by=user.id,
    )
    session.add(schema)
    session.commit()
    session.refresh(schema)
    return OutputSchemaResponse.model_validate(schema)


def set_output_schema_enabled(
    session: Session, user: CurrentUser, schema_id: int, enabled: bool
) -> OutputSchemaResponse:
    _require_admin(user)
    schema = session.get(OutputSchema, schema_id)
    if schema is None:
        raise ResourceNotFoundError("Output Schema 不存在")
    schema.enabled = enabled
    session.commit()
    session.refresh(schema)
    return OutputSchemaResponse.model_validate(schema)


def validate_structured_output(
    session: Session,
    user: CurrentUser,
    payload: StructuredOutputValidateRequest,
) -> StructuredOutputValidateResponse:
    get_project(session, user, payload.project_id)
    model = session.get(ModelConfiguration, payload.model_config_id)
    if model is None:
        raise ResourceNotFoundError("模型配置不存在")
    prompt_version = session.get(PromptVersion, payload.prompt_version_id)
    if prompt_version is None:
        raise ResourceNotFoundError("Prompt 版本不存在")
    prompt = session.get(PromptDefinition, prompt_version.prompt_id)
    if prompt is None or prompt.task_type != payload.task_type.value:
        raise ResourceConflictError("Prompt 版本与任务类型不匹配")
    schema = session.get(OutputSchema, payload.output_schema_id)
    if schema is None or not schema.enabled:
        raise ResourceConflictError("Output Schema 不存在或已停用")
    if (
        prompt_version.output_schema_id is not None
        and prompt_version.output_schema_id != schema.id
    ):
        raise ResourceConflictError("Output Schema 与 Prompt 版本不匹配")

    parsed, parse_error = parse_json_once(payload.raw_output)
    errors = [parse_error] if parse_error else validate_json_schema(parsed, schema.schema_json)
    repair_used = False
    repair_response: str | None = None
    if errors:
        candidate = payload.repair_output or deterministic_repair(payload.raw_output)
        if payload.repair_output is not None or candidate != payload.raw_output:
            repair_used = True
            repair_response = candidate
            repaired, repair_parse_error = parse_json_once(candidate)
            repair_errors = (
                [repair_parse_error]
                if repair_parse_error
                else validate_json_schema(repaired, schema.schema_json)
            )
            parsed = repaired
            errors = repair_errors
    success = not errors
    total_token = payload.input_token + payload.output_token
    estimated_cost = (
        Decimal(payload.input_token) * model.input_price
        + Decimal(payload.output_token) * model.output_price
    ) / Decimal(1_000_000)
    log = AiCallLog(
        project_id=payload.project_id,
        task_type=payload.task_type.value,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        model_config_id=model.id,
        actual_model=model.model_name,
        prompt_version_id=prompt_version.id,
        output_schema_id=schema.id,
        input_token=payload.input_token,
        output_token=payload.output_token,
        total_token=total_token,
        estimated_cost=estimated_cost,
        latency_ms=payload.latency_ms,
        success=success,
        fallback_used=payload.fallback_used,
        retry_count=payload.retry_count,
        repair_used=repair_used,
        error_type=None if success else "STRUCTURED_OUTPUT_INVALID",
        response_id=payload.response_id,
        raw_response=payload.raw_output,
        repair_response=repair_response,
        parsed_result=parsed if success else None,
        validation_errors=errors,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return StructuredOutputValidateResponse(
        ai_call_id=log.id,
        success=success,
        repair_used=repair_used,
        parsed_result=log.parsed_result,
        validation_errors=errors,
        estimated_cost=estimated_cost,
    )


def list_ai_calls(
    session: Session,
    user: CurrentUser,
    project_id: int,
    *,
    task_type: str | None = None,
) -> AiCallLogListResponse:
    get_project(session, user, project_id)
    statement = select(AiCallLog).where(AiCallLog.project_id == project_id)
    if task_type:
        statement = statement.where(AiCallLog.task_type == task_type)
    items = list(session.scalars(statement.order_by(AiCallLog.id.desc())).all())
    responses: list[AiCallLogResponse] = []
    for item in items:
        response = AiCallLogResponse.model_validate(item)
        displayed = response.model_dump()
        displayed.update(
            {
                "raw_response": redact_text(response.raw_response),
                "repair_response": (
                    redact_text(response.repair_response)
                    if response.repair_response is not None
                    else None
                ),
                "parsed_result": redact_value(response.parsed_result),
                "validation_errors": redact_value(response.validation_errors),
            }
        )
        responses.append(AiCallLogResponse.model_validate(displayed))
    return AiCallLogListResponse(
        items=responses,
        total=len(responses),
    )
