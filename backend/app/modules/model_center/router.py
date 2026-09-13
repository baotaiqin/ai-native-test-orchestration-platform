from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.model_center.schemas import (
    ModelCatalogCapability,
    ModelCatalogImportRequest,
    ModelCatalogItemResponse,
    ModelCatalogListResponse,
    ModelCatalogQuery,
    ModelConfigurationCreate,
    ModelConfigurationListResponse,
    ModelConfigurationResponse,
    ModelConfigurationUpdate,
    ModelConnectionVerificationResponse,
    ModelProviderConnectionCreate,
    ModelProviderConnectionListResponse,
    ModelProviderConnectionResponse,
    ModelProviderConnectionUpdate,
    ModelType,
    ProjectModelBindingBulkApplyRequest,
    ProjectModelBindingBulkApplyResponse,
    ProjectModelBindingListResponse,
    ProjectModelBindingResponse,
    ProjectModelBindingUpsert,
)
from app.modules.model_center.service import (
    bulk_apply_binding_model,
    create_connection,
    create_model,
    delete_binding,
    delete_model,
    import_catalog_model,
    list_bindings,
    list_catalog_models,
    list_connections,
    list_models,
    update_connection,
    update_model,
    upsert_binding,
    verify_model_connection,
)

router = APIRouter()


@router.get(
    "/connections",
    response_model=ModelProviderConnectionListResponse,
    summary="模型接入渠道列表",
)
def list_connections_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    include_disabled: bool = False,
) -> ModelProviderConnectionListResponse:
    items = list_connections(session, current_user, include_disabled=include_disabled)
    responses = [ModelProviderConnectionResponse.model_validate(item) for item in items]
    return ModelProviderConnectionListResponse(items=responses, total=len(responses))


@router.post(
    "/connections",
    response_model=ModelProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建模型接入渠道",
)
def create_connection_route(
    payload: ModelProviderConnectionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelProviderConnectionResponse:
    return ModelProviderConnectionResponse.model_validate(
        create_connection(session, current_user, payload)
    )


@router.patch(
    "/connections/{connection_id}",
    response_model=ModelProviderConnectionResponse,
    summary="更新模型接入渠道",
)
def update_connection_route(
    connection_id: int,
    payload: ModelProviderConnectionUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelProviderConnectionResponse:
    return ModelProviderConnectionResponse.model_validate(
        update_connection(session, current_user, connection_id, payload)
    )


@router.get(
    "/connections/{connection_id}/catalog/models",
    response_model=ModelCatalogListResponse,
    summary="查询百炼模型目录",
)
def list_catalog_models_route(
    connection_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    q: str | None = Query(default=None, min_length=1, max_length=128),
    model_type: ModelType | None = None,
    reasoning: bool | None = None,
    tool_call: bool | None = None,
    structured_output: bool | None = None,
    capabilities: list[ModelCatalogCapability] | None = Query(default=None),  # noqa: B008
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=20, ge=1, le=50),
) -> ModelCatalogListResponse:
    result = list_catalog_models(
        session,
        current_user,
        connection_id,
        ModelCatalogQuery(
            q=q,
            model_type=model_type,
            reasoning=reasoning,
            tool_call=tool_call,
            structured_output=structured_output,
            capabilities=capabilities or [],
            page=page,
            page_size=page_size,
        ),
    )
    return ModelCatalogListResponse(
        items=[ModelCatalogItemResponse.model_validate(item) for item in result.items],
        total=result.total,
        page=page,
        page_size=page_size,
        connection_id=connection_id,
        source=result.source,
        fetched_at=result.fetched_at,
    )


@router.post(
    "/connections/{connection_id}/catalog/models/import",
    response_model=ModelConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="导入百炼模型",
)
def import_catalog_model_route(
    connection_id: int,
    payload: ModelCatalogImportRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelConfigurationResponse:
    return ModelConfigurationResponse.model_validate(
        import_catalog_model(session, current_user, connection_id, payload)
    )


@router.get("", response_model=ModelConfigurationListResponse, summary="模型配置列表")
def list_models_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    include_disabled: bool = False,
) -> ModelConfigurationListResponse:
    items = list_models(session, current_user, include_disabled=include_disabled)
    responses = [ModelConfigurationResponse.model_validate(item) for item in items]
    return ModelConfigurationListResponse(items=responses, total=len(responses))


@router.post(
    "", response_model=ModelConfigurationResponse,
    status_code=status.HTTP_201_CREATED, summary="创建模型配置",
)
def create_model_route(
    payload: ModelConfigurationCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelConfigurationResponse:
    return ModelConfigurationResponse.model_validate(
        create_model(session, current_user, payload)
    )


@router.patch("/{model_id}", response_model=ModelConfigurationResponse, summary="更新模型配置")
def update_model_route(
    model_id: int,
    payload: ModelConfigurationUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelConfigurationResponse:
    return ModelConfigurationResponse.model_validate(
        update_model(session, current_user, model_id, payload)
    )


@router.delete(
    "/{model_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除模型配置"
)
def delete_model_route(
    model_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    delete_model(session, current_user, model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{model_id}/verify-connection",
    response_model=ModelConnectionVerificationResponse,
    summary="验证模型连接",
)
def verify_model_connection_route(
    model_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ModelConnectionVerificationResponse:
    result = verify_model_connection(session, current_user, model_id)
    return ModelConnectionVerificationResponse(
        model_id=model_id,
        success=result.success,
        status=result.status,
        duration_ms=result.duration_ms,
        summary=result.summary,
        error_type=result.error_type,
    )


@router.get(
    "/bindings/project", response_model=ProjectModelBindingListResponse,
    summary="项目任务模型绑定",
)
def list_bindings_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> ProjectModelBindingListResponse:
    items = list_bindings(session, current_user, project_id)
    responses = [ProjectModelBindingResponse.model_validate(item) for item in items]
    return ProjectModelBindingListResponse(items=responses, total=len(responses))


@router.put(
    "/bindings/project", response_model=ProjectModelBindingResponse,
    summary="设置项目任务模型绑定",
)
def upsert_binding_route(
    payload: ProjectModelBindingUpsert,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectModelBindingResponse:
    return ProjectModelBindingResponse.model_validate(
        upsert_binding(session, current_user, payload)
    )


@router.put(
    "/bindings/project/bulk-apply",
    response_model=ProjectModelBindingBulkApplyResponse,
    summary="将一个模型批量应用到全部项目任务",
)
def bulk_apply_binding_model_route(
    payload: ProjectModelBindingBulkApplyRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectModelBindingBulkApplyResponse:
    return bulk_apply_binding_model(session, current_user, payload)


@router.delete(
    "/bindings/project/{task_type}", status_code=status.HTTP_204_NO_CONTENT,
    summary="删除项目任务模型绑定",
)
def delete_binding_route(
    task_type: str,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> Response:
    delete_binding(session, current_user, project_id, task_type)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
