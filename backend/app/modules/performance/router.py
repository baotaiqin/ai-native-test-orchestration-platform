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
from app.modules.performance.schemas import (
    PerformanceAnalysisCreateRequest,
    PerformanceAnalysisListResponse,
    PerformanceAnalysisResponse,
    PerformanceComparisonResponse,
    PerformanceCompleteRequest,
    PerformanceCompleteResponse,
    PerformanceExecutionPlanResponse,
    PerformanceProfileCreate,
    PerformanceProfileListResponse,
    PerformanceProfileResponse,
    PerformanceRunCreate,
    PerformanceRunListResponse,
    PerformanceRunnerOptionListResponse,
    PerformanceRunResponse,
    PerformanceRunStartResponse,
)
from app.modules.performance.service import (
    compare_runs,
    complete_run,
    create_profile,
    generate_performance_analysis,
    get_execution_plan_for_runner,
    get_run,
    list_performance_analyses,
    list_profiles,
    list_runner_options,
    list_runs,
    start_run,
)
from app.modules.runners.router import RunnerCredentialDependency

router = APIRouter()
HeartbeatStore = Annotated[RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)]
Publisher = Annotated[TaskPublisher, Depends(get_task_publisher)]
EventStream = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]
ProfileId = Annotated[int, Path(gt=0)]
ComparisonRunIds = Annotated[list[str], Query(min_length=2, max_length=5)]


@router.post(
    "/profiles", response_model=PerformanceProfileResponse, status_code=status.HTTP_201_CREATED
)
def create_profile_route(
    payload: PerformanceProfileCreate,
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
) -> PerformanceProfileResponse:
    return create_profile(session, user, payload)


@router.get("/profiles", response_model=PerformanceProfileListResponse)
def list_profiles_route(
    session: DatabaseSessionDependency, user: CurrentUserDependency, project_id: int = Query(gt=0)
) -> PerformanceProfileListResponse:
    return list_profiles(session, user, project_id)


@router.get("/runner-options", response_model=PerformanceRunnerOptionListResponse)
def list_runner_options_route(
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
    heartbeat_store: HeartbeatStore,
    project_id: int = Query(gt=0),
) -> PerformanceRunnerOptionListResponse:
    return list_runner_options(session, user, project_id, heartbeat_store)


@router.post(
    "/profiles/{profile_id}/runs",
    response_model=PerformanceRunStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_run_route(
    profile_id: ProfileId,
    payload: PerformanceRunCreate,
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
    heartbeat_store: HeartbeatStore,
    publisher: Publisher,
    event_stream: EventStream,
) -> PerformanceRunStartResponse:
    return start_run(session, user, profile_id, payload, heartbeat_store, publisher, event_stream)


@router.get("/runs", response_model=PerformanceRunListResponse)
def list_runs_route(
    session: DatabaseSessionDependency, user: CurrentUserDependency, project_id: int = Query(gt=0)
) -> PerformanceRunListResponse:
    return list_runs(session, user, project_id)


@router.get("/runs-comparison", response_model=PerformanceComparisonResponse)
def compare_runs_route(
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
    run_ids: ComparisonRunIds,
    project_id: int = Query(gt=0),
) -> PerformanceComparisonResponse:
    return compare_runs(session, user, project_id, run_ids)


@router.get("/runs/{run_id}", response_model=PerformanceRunResponse)
def get_run_route(
    run_id: str, session: DatabaseSessionDependency, user: CurrentUserDependency
) -> PerformanceRunResponse:
    return get_run(session, user, run_id)


@router.post(
    "/runs/{run_id}/analyses",
    response_model=PerformanceAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_performance_analysis_route(
    run_id: str,
    payload: PerformanceAnalysisCreateRequest,
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
) -> PerformanceAnalysisResponse:
    return generate_performance_analysis(session, user, run_id, payload)


@router.get(
    "/runs/{run_id}/analyses",
    response_model=PerformanceAnalysisListResponse,
)
def list_performance_analyses_route(
    run_id: str,
    session: DatabaseSessionDependency,
    user: CurrentUserDependency,
) -> PerformanceAnalysisListResponse:
    return list_performance_analyses(session, user, run_id)


@router.get("/runs/{run_id}/execution-plan", response_model=PerformanceExecutionPlanResponse)
def execution_plan_route(
    run_id: str,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
) -> PerformanceExecutionPlanResponse:
    return get_execution_plan_for_runner(session, run_id, credential, message_id)


@router.post("/runs/{run_id}/complete", response_model=PerformanceCompleteResponse)
def complete_route(
    run_id: str,
    payload: PerformanceCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: EventStream,
) -> PerformanceCompleteResponse:
    return complete_run(session, run_id, credential, payload, event_stream)
