from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.test_plans.schemas import (
    TestPlanCreate,
    TestPlanListResponse,
    TestPlanResponse,
    TestPlanRunListResponse,
    TestPlanRunnerOptionListResponse,
    TestPlanRunResponse,
    TestPlanRunStartResponse,
    TestPlanUpdate,
    TestPlanValidationResponse,
)
from app.modules.test_plans.service import (
    cancel_test_plan_run,
    create_test_plan,
    get_test_plan,
    get_test_plan_run,
    list_runner_options,
    list_test_plan_runs,
    list_test_plans,
    retry_test_plan_run_dispatch,
    set_test_plan_status,
    start_test_plan_run,
    update_test_plan,
    validate_test_plan,
)

router = APIRouter()
PlanIdPath = Annotated[int, Path(gt=0)]
PlanRunIdPath = Annotated[str, Path(min_length=9, max_length=128)]
HeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]
PublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
EventStreamDependency = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]


@router.get("/runner-options", response_model=TestPlanRunnerOptionListResponse)
def list_runner_options_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    project_id: int = Query(gt=0),
) -> TestPlanRunnerOptionListResponse:
    return list_runner_options(session, current_user, project_id, heartbeat_store)


@router.post("", response_model=TestPlanResponse, status_code=status.HTTP_201_CREATED)
def create_test_plan_route(
    payload: TestPlanCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanResponse:
    return create_test_plan(session, current_user, payload)


@router.get("", response_model=TestPlanListResponse)
def list_test_plans_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> TestPlanListResponse:
    return list_test_plans(session, current_user, project_id)


@router.get("/runs", response_model=TestPlanRunListResponse)
def list_test_plan_runs_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> TestPlanRunListResponse:
    return list_test_plan_runs(session, current_user, project_id)


@router.get("/runs/{plan_run_id}", response_model=TestPlanRunResponse)
def get_test_plan_run_route(
    plan_run_id: PlanRunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanRunResponse:
    return get_test_plan_run(session, current_user, plan_run_id)


@router.post("/runs/{plan_run_id}/cancel", response_model=TestPlanRunResponse)
def cancel_test_plan_run_route(
    plan_run_id: PlanRunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: EventStreamDependency,
) -> TestPlanRunResponse:
    return cancel_test_plan_run(
        session, current_user, plan_run_id, event_stream
    )


@router.post("/runs/{plan_run_id}/retry", response_model=TestPlanRunResponse)
def retry_test_plan_run_route(
    plan_run_id: PlanRunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: PublisherDependency,
    event_stream: EventStreamDependency,
) -> TestPlanRunResponse:
    return retry_test_plan_run_dispatch(
        session,
        current_user,
        plan_run_id,
        heartbeat_store,
        publisher,
        event_stream,
    )


@router.get("/{plan_id}", response_model=TestPlanResponse)
def get_test_plan_route(
    plan_id: PlanIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanResponse:
    return get_test_plan(session, current_user, plan_id)


@router.put("/{plan_id}", response_model=TestPlanResponse)
def update_test_plan_route(
    plan_id: PlanIdPath,
    payload: TestPlanUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanResponse:
    return update_test_plan(session, current_user, plan_id, payload)


@router.post("/{plan_id}/validate", response_model=TestPlanValidationResponse)
def validate_test_plan_route(
    plan_id: PlanIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
) -> TestPlanValidationResponse:
    return validate_test_plan(session, current_user, plan_id, heartbeat_store)


@router.post("/{plan_id}/run", response_model=TestPlanRunStartResponse)
def start_test_plan_run_route(
    plan_id: PlanIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: PublisherDependency,
    event_stream: EventStreamDependency,
) -> TestPlanRunStartResponse:
    return start_test_plan_run(
        session,
        current_user,
        plan_id,
        heartbeat_store,
        publisher,
        event_stream,
    )


@router.post("/{plan_id}/archive", response_model=TestPlanResponse)
def archive_test_plan_route(
    plan_id: PlanIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanResponse:
    return set_test_plan_status(session, current_user, plan_id, "ARCHIVED")


@router.post("/{plan_id}/restore", response_model=TestPlanResponse)
def restore_test_plan_route(
    plan_id: PlanIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestPlanResponse:
    return set_test_plan_status(session, current_user, plan_id, "ACTIVE")
