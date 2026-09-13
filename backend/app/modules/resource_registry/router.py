from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.resource_registry.schemas import (
    CleanupExecuteRequest,
    CleanupExecuteResponse,
    CleanupPlanRequest,
    CleanupPlanResponse,
    CleanupStatus,
    ResourceListResponse,
    ResourceRegisterRequest,
    ResourceResponse,
)
from app.modules.resource_registry.service import (
    execute_cleanup,
    get_resource,
    list_resources,
    plan_cleanup,
    register_resource,
)

router = APIRouter()


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def register_resource_route(
    payload: ResourceRegisterRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ResourceResponse:
    return register_resource(session, current_user, payload)


@router.get("", response_model=ResourceListResponse)
def list_resources_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    run_id: str = Query(min_length=1, max_length=128),
    status_filter: CleanupStatus | None = Query(default=None, alias="status"),  # noqa: B008
) -> ResourceListResponse:
    return list_resources(session, current_user, project_id, run_id, status_filter)


@router.post("/cleanup/plan", response_model=CleanupPlanResponse)
def plan_cleanup_route(
    payload: CleanupPlanRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CleanupPlanResponse:
    return plan_cleanup(
        session,
        current_user,
        payload.project_id,
        payload.run_id,
        payload.outcome,
        retry_failed=payload.retry_failed,
    )


@router.post("/cleanup/execute", response_model=CleanupExecuteResponse)
def execute_cleanup_route(
    payload: CleanupExecuteRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CleanupExecuteResponse:
    return execute_cleanup(
        session,
        current_user,
        payload.project_id,
        payload.run_id,
        payload.outcome,
        retry_failed=payload.retry_failed,
    )


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource_route(
    resource_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> ResourceResponse:
    return get_resource(session, current_user, project_id, resource_id)
