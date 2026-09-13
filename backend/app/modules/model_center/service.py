import hashlib
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    AuthorizationError,
    ResourceConflictError,
    ResourceNotFoundError,
    SecurityConfigurationError,
)
from app.core.secret_cipher import decrypt_secret, encrypt_secret
from app.core.time import to_utc_aware, utc_now_naive
from app.modules.ai_gateway.service import (
    ModelConnectionProbeResult,
    probe_model_connection,
    validate_model_base_url,
)
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.catalog import (
    BailianCatalogError,
    CatalogPage,
    build_bailian_catalog_url,
    default_prices,
    fetch_bailian_catalog,
)
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.model_center.schemas import (
    AiTaskType,
    ModelCatalogImportRequest,
    ModelCatalogQuery,
    ModelConfigurationCreate,
    ModelConfigurationUpdate,
    ModelProviderConnectionCreate,
    ModelProviderConnectionUpdate,
    ProjectModelBindingBulkApplyItem,
    ProjectModelBindingBulkApplyRequest,
    ProjectModelBindingBulkApplyResponse,
    ProjectModelBindingUpsert,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_owner, get_project, is_admin
from app.modules.prompt_center.models import AiCallLog


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        raise AuthorizationError("只有管理员可以维护模型配置")


def _get_model(session: Session, model_id: int) -> ModelConfiguration:
    model = session.get(ModelConfiguration, model_id)
    if model is None:
        raise ResourceNotFoundError("模型配置不存在")
    return model


def _get_connection(session: Session, connection_id: int) -> ModelProviderConnection:
    connection = session.get(ModelProviderConnection, connection_id)
    if connection is None:
        raise ResourceNotFoundError("模型接入渠道不存在")
    return connection


def _require_enabled_connection(
    session: Session, connection_id: int
) -> ModelProviderConnection:
    connection = _get_connection(session, connection_id)
    if not connection.enabled:
        raise ResourceConflictError("不能使用已停用的模型接入渠道")
    return connection


def _commit(session: Session, *, duplicate_message: str = "模型配置名称已存在") -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(duplicate_message) from exc


def _set_api_key(
    target: ModelProviderConnection, api_key: str | None
) -> None:
    if api_key is None:
        target.api_key_encrypted_value = None
        target.api_key_fingerprint = None
        target.api_key_rotated_at = None
        return
    target.api_key_encrypted_value = encrypt_secret(
        api_key, get_settings().secret_key
    )
    target.api_key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    target.api_key_rotated_at = utc_now_naive()


def _validate_base_url(base_url: str) -> None:
    try:
        validate_model_base_url(base_url)
    except ValueError as exc:
        raise ResourceConflictError("模型服务地址不受支持") from exc


def _catalog_credentials(
    session: Session, user: CurrentUser, connection_id: int
) -> tuple[ModelProviderConnection, str]:
    _require_admin(user)
    connection = _get_connection(session, connection_id)
    if connection.provider.strip().upper() != "ALIYUN_BAILIAN":
        raise ResourceConflictError("当前仅支持阿里云百炼模型目录")
    if not connection.enabled:
        raise ResourceConflictError("模型接入渠道已停用")
    if not connection.api_key_encrypted_value:
        raise ResourceConflictError("百炼接入渠道尚未配置 API Key")
    try:
        api_key = decrypt_secret(
            connection.api_key_encrypted_value, get_settings().secret_key
        )
    except SecurityConfigurationError as exc:
        raise AppError(
            "MODEL_CATALOG_UNAVAILABLE",
            "百炼模型目录暂不可用，请检查接入渠道 API Key",
            status_code=503,
        ) from exc
    return connection, api_key


def _catalog_error(exc: BailianCatalogError) -> AppError:
    if exc.error_type == "UNSAFE_ENDPOINT":
        return ResourceConflictError("百炼模型目录地址不受支持")
    if exc.error_type == "RESPONSE_TOO_LARGE":
        return AppError(
            "MODEL_CATALOG_UNAVAILABLE",
            "百炼模型目录响应过大，请使用工作空间专属 Base URL",
            status_code=503,
        )
    if exc.error_type == "REDIRECT_NOT_ALLOWED":
        return AppError(
            "MODEL_CATALOG_UNAVAILABLE",
            "百炼模型目录不接受重定向，请使用工作空间专属 Base URL",
            status_code=503,
        )
    return AppError(
        "MODEL_CATALOG_UNAVAILABLE",
        "百炼模型目录暂不可用，请使用百炼工作空间专属 Base URL",
        status_code=503,
    )


def _fetch_catalog(
    connection: ModelProviderConnection, api_key: str, query: ModelCatalogQuery
) -> CatalogPage:
    try:
        build_bailian_catalog_url(connection.base_url)
        return fetch_bailian_catalog(
            base_url=connection.base_url,
            api_key=api_key,
            query=query,
        )
    except BailianCatalogError as exc:
        raise _catalog_error(exc) from exc


def list_catalog_models(
    session: Session,
    user: CurrentUser,
    connection_id: int,
    query: ModelCatalogQuery,
) -> CatalogPage:
    connection, api_key = _catalog_credentials(session, user, connection_id)
    return _fetch_catalog(connection, api_key, query)


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        normalized = to_utc_aware(parsed)
        return normalized.replace(tzinfo=None) if normalized is not None else None
    except ValueError:
        return None


_OFFICIAL_READONLY_FIELDS = frozenset(
    {
        "model_vendor",
        "model_name",
        "model_type",
        "model_category",
        "supports_tool_call",
        "supports_structured_output",
        "max_context",
        "connection_id",
        "input_price",
        "output_price",
    }
)


def import_catalog_model(
    session: Session,
    user: CurrentUser,
    connection_id: int,
    payload: ModelCatalogImportRequest,
) -> ModelConfiguration:
    connection, api_key = _catalog_credentials(session, user, connection_id)
    query = ModelCatalogQuery(exact_model=payload.model_id, page=1, page_size=50)
    page = _fetch_catalog(connection, api_key, query)
    item = next((item for item in page.items if item.model_id == payload.model_id), None)
    if item is None:
        raise ResourceNotFoundError("百炼模型目录中未找到指定模型")
    if not item.supported_by_platform:
        raise ResourceConflictError(item.unsupported_reason or "当前平台不支持该模型")
    input_price, output_price = default_prices(item.pricing_tiers)
    model = ModelConfiguration(
        name=payload.configuration_name or item.name,
        connection_id=connection.id,
        model_vendor=(item.provider or item.inference_provider or "UNKNOWN")[:64],
        model_name=item.model_id,
        model_type=item.model_type.value,
        model_category=item.model_category.value,
        metadata_source="ALIYUN_BAILIAN",
        metadata_synced_at=utc_now_naive(),
        description=item.description,
        supports_reasoning=item.supports_reasoning,
        max_input_tokens=item.max_input_tokens,
        max_output_tokens=item.max_output_tokens,
        max_reasoning_tokens=item.max_reasoning_tokens,
        reasoning_max_input_tokens=item.reasoning_max_input_tokens,
        reasoning_max_output_tokens=item.reasoning_max_output_tokens,
        input_modalities=item.input_modalities,
        output_modalities=item.output_modalities,
        capabilities=item.capabilities,
        features=item.features,
        pricing_tiers=item.pricing_tiers,
        published_at=_parse_published_at(item.published_time),
        supports_tool_call=item.supports_tool_call,
        supports_structured_output=item.supports_structured_output,
        max_context=item.context_window,
        timeout_seconds=payload.timeout_seconds,
        input_price=input_price,
        output_price=output_price,
        enabled=payload.enabled,
        created_by=user.id,
    )
    session.add(model)
    _commit(session)
    session.refresh(model)
    return model


def list_connections(
    session: Session, user: CurrentUser, *, include_disabled: bool = False
) -> list[ModelProviderConnection]:
    if include_disabled:
        _require_admin(user)
    statement = select(ModelProviderConnection)
    if not include_disabled:
        statement = statement.where(ModelProviderConnection.enabled.is_(True))
    return list(session.scalars(statement.order_by(ModelProviderConnection.name)).all())


def create_connection(
    session: Session, user: CurrentUser, payload: ModelProviderConnectionCreate
) -> ModelProviderConnection:
    _require_admin(user)
    _validate_base_url(str(payload.base_url))
    values = payload.model_dump(mode="json", exclude={"api_key"})
    values["base_url"] = str(payload.base_url).rstrip("/")
    connection = ModelProviderConnection(**values, created_by=user.id)
    if payload.api_key is not None:
        _set_api_key(connection, payload.api_key)
    session.add(connection)
    _commit(session, duplicate_message="接入渠道名称已存在")
    session.refresh(connection)
    return connection


def update_connection(
    session: Session,
    user: CurrentUser,
    connection_id: int,
    payload: ModelProviderConnectionUpdate,
) -> ModelProviderConnection:
    _require_admin(user)
    connection = _get_connection(session, connection_id)
    changes = payload.model_dump(
        exclude_unset=True, mode="json", exclude={"api_key"}
    )
    if "base_url" in changes and changes["base_url"] is not None:
        _validate_base_url(str(changes["base_url"]))
    for key, value in changes.items():
        if key == "base_url" and value is not None:
            value = str(value).rstrip("/")
        setattr(connection, key, value)
    if "api_key" in payload.model_fields_set:
        _set_api_key(connection, payload.api_key)
    _commit(session, duplicate_message="接入渠道名称已存在")
    session.refresh(connection)
    return connection


def list_models(
    session: Session, user: CurrentUser, *, include_disabled: bool = False
) -> list[ModelConfiguration]:
    if include_disabled:
        _require_admin(user)
    statement = select(ModelConfiguration).join(
        ModelProviderConnection,
        ModelProviderConnection.id == ModelConfiguration.connection_id,
    )
    if not include_disabled:
        statement = statement.where(
            ModelConfiguration.enabled.is_(True),
            ModelProviderConnection.enabled.is_(True),
        )
    return list(session.scalars(statement.order_by(ModelConfiguration.name)).all())


def create_model(
    session: Session, user: CurrentUser, payload: ModelConfigurationCreate
) -> ModelConfiguration:
    _require_admin(user)
    _require_enabled_connection(session, payload.connection_id)
    values = payload.model_dump(mode="json")
    model = ModelConfiguration(**values, created_by=user.id)
    session.add(model)
    _commit(session)
    session.refresh(model)
    return model


def update_model(
    session: Session,
    user: CurrentUser,
    model_id: int,
    payload: ModelConfigurationUpdate,
) -> ModelConfiguration:
    _require_admin(user)
    model = _get_model(session, model_id)
    changes = payload.model_dump(
        exclude_unset=True, mode="json"
    )
    if model.metadata_source and _OFFICIAL_READONLY_FIELDS.intersection(changes):
        raise ResourceConflictError("官方目录模型元数据为只读")
    if "connection_id" in changes:
        _require_enabled_connection(session, changes["connection_id"])
    for key, value in changes.items():
        setattr(model, key, value)
    _commit(session)
    session.refresh(model)
    return model


def delete_model(session: Session, user: CurrentUser, model_id: int) -> None:
    _require_admin(user)
    model = session.scalar(
        select(ModelConfiguration)
        .where(ModelConfiguration.id == model_id)
        .with_for_update()
    )
    if model is None:
        raise ResourceNotFoundError("模型配置不存在")

    has_history = session.scalar(
        select(AiCallLog.id)
        .where(AiCallLog.model_config_id == model_id)
        .limit(1)
    )
    if has_history is not None:
        raise ResourceConflictError("该模型已有 AI 调用历史，为保护审计只能停用")

    bindings = list(
        session.scalars(
            select(ProjectModelBinding)
            .where(
                or_(
                    ProjectModelBinding.primary_model_id == model_id,
                    ProjectModelBinding.fallback_model_id == model_id,
                )
            )
            .with_for_update()
        ).all()
    )
    for binding in bindings:
        if binding.primary_model_id == model_id:
            session.delete(binding)
        else:
            binding.fallback_model_id = None
            binding.max_fallback = 0

    session.delete(model)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if session.scalar(
            select(AiCallLog.id)
            .where(AiCallLog.model_config_id == model_id)
            .limit(1)
        ) is not None:
            raise ResourceConflictError(
                "该模型已有 AI 调用历史，为保护审计只能停用"
            ) from exc
        raise ResourceConflictError("模型删除失败，请稍后重试") from exc
    except Exception:
        session.rollback()
        raise


def verify_model_connection(
    session: Session, user: CurrentUser, model_id: int
) -> ModelConnectionProbeResult:
    _require_admin(user)
    model = _get_model(session, model_id)
    return probe_model_connection(session, model)


def list_bindings(
    session: Session, user: CurrentUser, project_id: int
) -> list[ProjectModelBinding]:
    get_project(session, user, project_id)
    return list(
        session.scalars(
            select(ProjectModelBinding)
            .where(ProjectModelBinding.project_id == project_id)
            .order_by(ProjectModelBinding.task_type)
        ).all()
    )


def _validate_binding_model(
    session: Session, project_id: int, model_id: int
) -> ModelConfiguration:
    model = _get_model(session, model_id)
    if not model.enabled:
        raise ResourceConflictError("不能绑定已停用的模型")
    if model.connection is None or not model.connection.enabled:
        raise ResourceConflictError("不能绑定接入渠道已停用的模型")
    return model


_TASK_MIN_INPUT_TOKENS: dict[AiTaskType, int] = {
    AiTaskType.REQUIREMENT_REVIEW: 16_384,
    AiTaskType.API_DOC_REVIEW: 16_384,
    AiTaskType.API_TEST_DESIGN: 16_384,
    AiTaskType.API_CASE_GENERATE: 16_384,
    AiTaskType.API_SCENARIO_PLAN: 16_384,
    AiTaskType.API_SCENARIO_GENERATE: 16_384,
    AiTaskType.WEB_CASE_GENERATE: 16_384,
    AiTaskType.WEB_TEST_PLAN: 16_384,
    AiTaskType.WEB_PLAN_RECONCILE: 16_384,
    AiTaskType.PERFORMANCE_ANALYSIS: 16_384,
    AiTaskType.IMPACT_ANALYSIS: 16_384,
}
_DEFAULT_TASK_MIN_INPUT_TOKENS = 8_192


def _binding_model_incompatibility(
    model: ModelConfiguration, task_type: AiTaskType
) -> str | None:
    if model.model_type == "EMBEDDING":
        return "Embedding 模型不能用于生成式 AI 任务"
    capacity = (
        model.max_input_tokens
        if model.max_input_tokens is not None
        else model.max_context
    )
    required = _TASK_MIN_INPUT_TOKENS.get(
        task_type, _DEFAULT_TASK_MIN_INPUT_TOKENS
    )
    if capacity is not None and capacity < required:
        return f"模型最大输入 {capacity} Token，当前任务至少需要 {required} Token"
    return None


def _require_task_compatible_model(
    model: ModelConfiguration, task_type: AiTaskType
) -> None:
    reason = _binding_model_incompatibility(model, task_type)
    if reason is not None:
        raise ResourceConflictError(f"模型与当前任务不兼容：{reason}")


def upsert_binding(
    session: Session, user: CurrentUser, payload: ProjectModelBindingUpsert
) -> ProjectModelBinding:
    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能修改模型绑定")
    primary = _validate_binding_model(session, payload.project_id, payload.primary_model_id)
    _require_task_compatible_model(primary, payload.task_type)
    fallback = None
    if payload.fallback_model_id is not None:
        fallback = _validate_binding_model(
            session, payload.project_id, payload.fallback_model_id
        )
        _require_task_compatible_model(fallback, payload.task_type)
        if fallback.model_type != primary.model_type:
            raise ResourceConflictError("主模型与 fallback 模型类型必须一致")
    binding = session.scalar(
        select(ProjectModelBinding).where(
            ProjectModelBinding.project_id == payload.project_id,
            ProjectModelBinding.task_type == payload.task_type.value,
        )
    )
    if binding is None:
        binding = ProjectModelBinding(
            project_id=payload.project_id,
            task_type=payload.task_type.value,
            updated_by=user.id,
        )
        session.add(binding)
    binding.primary_model_id = payload.primary_model_id
    binding.fallback_model_id = payload.fallback_model_id
    binding.max_fallback = payload.max_fallback
    binding.updated_by = user.id
    session.commit()
    session.refresh(binding)
    return binding


def bulk_apply_binding_model(
    session: Session,
    user: CurrentUser,
    payload: ProjectModelBindingBulkApplyRequest,
) -> ProjectModelBindingBulkApplyResponse:
    """Apply one model to every task binding in a single transaction."""

    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能修改模型绑定")
    selected_model = _validate_binding_model(
        session,
        payload.project_id,
        payload.model_id,
    )
    existing_bindings = list(session.scalars(
        select(ProjectModelBinding)
        .where(ProjectModelBinding.project_id == payload.project_id)
        .with_for_update()
    ).all())
    bindings_by_task = {item.task_type: item for item in existing_bindings}
    binding_model_ids = {
        model_id
        for item in existing_bindings
        for model_id in (item.primary_model_id, item.fallback_model_id)
        if model_id is not None
    }
    binding_models = {
        item.id: item
        for item in session.scalars(
            select(ModelConfiguration).where(
                ModelConfiguration.id.in_(binding_model_ids)
            )
        ).all()
    }
    items: list[ProjectModelBindingBulkApplyItem] = []

    for task_type in AiTaskType:
        binding = bindings_by_task.get(task_type.value)
        incompatibility = _binding_model_incompatibility(selected_model, task_type)
        if incompatibility is not None:
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="SKIPPED",
                message=incompatibility,
            ))
            continue
        if payload.role == "PRIMARY":
            if binding is None:
                binding = ProjectModelBinding(
                    project_id=payload.project_id,
                    task_type=task_type.value,
                    primary_model_id=selected_model.id,
                    fallback_model_id=None,
                    max_fallback=0,
                    updated_by=user.id,
                )
                session.add(binding)
                bindings_by_task[task_type.value] = binding
                items.append(ProjectModelBindingBulkApplyItem(
                    task_type=task_type,
                    status="APPLIED",
                    message="已设置主模型",
                ))
                continue
            current_fallback = (
                binding_models.get(binding.fallback_model_id)
                if binding.fallback_model_id is not None
                else None
            )
            fallback_cleared = (
                binding.fallback_model_id == selected_model.id
                or (
                    binding.fallback_model_id is not None
                    and (
                        current_fallback is None
                        or not current_fallback.enabled
                        or current_fallback.connection is None
                        or not current_fallback.connection.enabled
                        or current_fallback.model_type != selected_model.model_type
                    )
                )
            )
            unchanged = (
                binding.primary_model_id == selected_model.id
                and not fallback_cleared
            )
            binding.primary_model_id = selected_model.id
            if fallback_cleared:
                binding.fallback_model_id = None
                binding.max_fallback = 0
            binding.updated_by = user.id
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="UNCHANGED" if unchanged else "APPLIED",
                message=(
                    "主模型已经是所选模型"
                    if unchanged
                    else "已设置主模型；冲突或类型不兼容的原备用模型已清除"
                    if fallback_cleared
                    else "已设置主模型"
                ),
            ))
            continue

        if binding is None:
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="SKIPPED",
                message="尚未设置主模型，不能单独设置备用模型",
            ))
            continue
        if binding.primary_model_id == selected_model.id:
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="SKIPPED",
                message="所选模型已是主模型，不能同时作为备用模型",
            ))
            continue
        primary_model = binding_models.get(binding.primary_model_id)
        if (
            primary_model is None
            or not primary_model.enabled
            or primary_model.connection is None
            or not primary_model.connection.enabled
        ):
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="SKIPPED",
                message="当前主模型或其接入渠道不可用",
            ))
            continue
        if primary_model.model_type != selected_model.model_type:
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="SKIPPED",
                message="所选模型与当前主模型类型不一致",
            ))
            continue
        if binding.fallback_model_id == selected_model.id and binding.max_fallback == 1:
            items.append(ProjectModelBindingBulkApplyItem(
                task_type=task_type,
                status="UNCHANGED",
                message="备用模型已经是所选模型",
            ))
            continue
        binding.fallback_model_id = selected_model.id
        binding.max_fallback = 1
        binding.updated_by = user.id
        items.append(ProjectModelBindingBulkApplyItem(
            task_type=task_type,
            status="APPLIED",
            message="已设置备用模型",
        ))

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("模型绑定已被其他操作更新，请刷新后重试") from exc
    return ProjectModelBindingBulkApplyResponse(
        project_id=payload.project_id,
        model_id=payload.model_id,
        role=payload.role,
        applied_count=sum(item.status == "APPLIED" for item in items),
        unchanged_count=sum(item.status == "UNCHANGED" for item in items),
        skipped_count=sum(item.status == "SKIPPED" for item in items),
        items=items,
    )


def delete_binding(
    session: Session, user: CurrentUser, project_id: int, task_type: str
) -> None:
    project = get_project(session, user, project_id)
    ensure_project_owner(session, project, user)
    binding = session.scalar(
        select(ProjectModelBinding).where(
            ProjectModelBinding.project_id == project_id,
            ProjectModelBinding.task_type == task_type,
        )
    )
    if binding is None:
        raise ResourceNotFoundError("模型绑定不存在")
    session.delete(binding)
    session.commit()
