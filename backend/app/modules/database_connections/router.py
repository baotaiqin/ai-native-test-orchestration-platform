from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.database_connections.schemas import (
    ConnectionTestResponse,
    DatabaseConnectionCreate,
    DatabaseConnectionListResponse,
    DatabaseConnectionResponse,
    DatabaseConnectionUpdate,
)
from app.modules.database_connections.service import (
    create_database_connection,
    list_database_connections,
    test_database_connection,
    update_database_connection,
)

router = APIRouter()


@router.get("", response_model=DatabaseConnectionListResponse, summary="数据库连接列表")
def list_database_connections_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    environment_id: int | None = Query(default=None, gt=0),
) -> DatabaseConnectionListResponse:
    items = list_database_connections(session, current_user, project_id, environment_id)
    responses = [DatabaseConnectionResponse.model_validate(item) for item in items]
    return DatabaseConnectionListResponse(items=responses, total=len(responses))


@router.post(
    "",
    response_model=DatabaseConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建数据库连接",
)
def create_database_connection_route(
    payload: DatabaseConnectionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatabaseConnectionResponse:
    connection = create_database_connection(session, current_user, payload)
    return DatabaseConnectionResponse.model_validate(connection)


@router.patch(
    "/{connection_id}", response_model=DatabaseConnectionResponse, summary="编辑数据库连接"
)
def update_database_connection_route(
    connection_id: int,
    payload: DatabaseConnectionUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DatabaseConnectionResponse:
    connection = update_database_connection(session, current_user, connection_id, payload)
    return DatabaseConnectionResponse.model_validate(connection)


@router.post(
    "/{connection_id}/test", response_model=ConnectionTestResponse, summary="测试 MySQL 连接"
)
def test_database_connection_route(
    connection_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ConnectionTestResponse:
    return test_database_connection(session, current_user, connection_id)
