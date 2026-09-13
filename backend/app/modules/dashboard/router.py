from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.infrastructure.redis.client import (
    RedisRunnerHeartbeatStore,
    get_runner_heartbeat_store,
)

from .schemas import DashboardQuery, DashboardResponse
from .service import get_dashboard

router = APIRouter()
RunnerHeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]


@router.get("", response_model=DashboardResponse)
def get_dashboard_route(
    filters: Annotated[DashboardQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunnerHeartbeatStoreDependency,
) -> DashboardResponse:
    return get_dashboard(session, current_user, filters, store)
