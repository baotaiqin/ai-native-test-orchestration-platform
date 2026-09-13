from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.environments.schemas import (
    EnvironmentCreate,
    EnvironmentListResponse,
    EnvironmentResponse,
    EnvironmentUpdate,
    VariableListResponse,
    VariableResponse,
    VariableUpsert,
)
from app.modules.environments.service import (
    create_environment,
    delete_variable,
    list_environments,
    list_variables,
    set_default_environment,
    update_environment,
    upsert_variable,
)

router = APIRouter()


@router.get("", response_model=EnvironmentListResponse, summary="项目环境列表")
def list_environments_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> EnvironmentListResponse:
    items = list_environments(session, current_user, project_id)
    return EnvironmentListResponse(items=items, total=len(items))


@router.post(
    "", response_model=EnvironmentResponse, status_code=status.HTTP_201_CREATED, summary="创建环境"
)
def create_environment_route(
    payload: EnvironmentCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> EnvironmentResponse:
    return EnvironmentResponse.model_validate(create_environment(session, current_user, payload))


@router.patch("/{environment_id}", response_model=EnvironmentResponse, summary="编辑环境")
def update_environment_route(
    environment_id: int,
    payload: EnvironmentUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> EnvironmentResponse:
    return EnvironmentResponse.model_validate(
        update_environment(session, current_user, environment_id, payload)
    )


@router.post(
    "/{environment_id}/default", response_model=EnvironmentResponse, summary="设为默认环境"
)
def set_default_environment_route(
    environment_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> EnvironmentResponse:
    return EnvironmentResponse.model_validate(
        set_default_environment(session, current_user, environment_id)
    )


@router.get(
    "/{environment_id}/variables", response_model=VariableListResponse, summary="环境变量列表"
)
def list_variables_route(
    environment_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> VariableListResponse:
    items = list_variables(session, current_user, environment_id)
    return VariableListResponse(items=items, total=len(items))


@router.put(
    "/{environment_id}/variables/{key}", response_model=VariableResponse, summary="保存环境变量"
)
def upsert_variable_route(
    payload: VariableUpsert,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    environment_id: int,
    key: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"),
) -> VariableResponse:
    return VariableResponse.model_validate(
        upsert_variable(session, current_user, environment_id, key, payload)
    )


@router.delete(
    "/{environment_id}/variables/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除环境变量",
)
def delete_variable_route(
    environment_id: int,
    key: str,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    delete_variable(session, current_user, environment_id, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
