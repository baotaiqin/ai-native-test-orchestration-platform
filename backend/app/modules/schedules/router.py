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
from app.modules.schedules.schemas import (
    ScheduleCreate,
    ScheduleListResponse,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    ScheduleResponse,
    ScheduleTriggerListResponse,
    ScheduleTriggerResponse,
    ScheduleUpdate,
)
from app.modules.schedules.service import (
    create_schedule,
    get_schedule,
    list_schedule_triggers,
    list_schedules,
    preview_schedule,
    retry_schedule_trigger,
    set_schedule_status,
    update_schedule,
)

router = APIRouter()
ScheduleIdPath = Annotated[int, Path(gt=0)]
TriggerIdPath = Annotated[str, Path(min_length=10, max_length=128)]
HeartbeatStoreDependency = Annotated[RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)]
PublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
EventStreamDependency = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]


@router.post("/preview", response_model=SchedulePreviewResponse)
def preview_schedule_route(payload: SchedulePreviewRequest) -> SchedulePreviewResponse:
    return preview_schedule(payload)


@router.get("/triggers", response_model=ScheduleTriggerListResponse)
def list_schedule_triggers_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> ScheduleTriggerListResponse:
    return list_schedule_triggers(session, current_user, project_id)


@router.post("/triggers/{trigger_id}/retry", response_model=ScheduleTriggerResponse)
def retry_schedule_trigger_route(
    trigger_id: TriggerIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: PublisherDependency,
    event_stream: EventStreamDependency,
) -> ScheduleTriggerResponse:
    return retry_schedule_trigger(
        session, current_user, trigger_id, heartbeat_store, publisher, event_stream
    )


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule_route(
    payload: ScheduleCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScheduleResponse:
    return create_schedule(session, current_user, payload)


@router.get("", response_model=ScheduleListResponse)
def list_schedules_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> ScheduleListResponse:
    return list_schedules(session, current_user, project_id)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule_route(
    schedule_id: ScheduleIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScheduleResponse:
    return get_schedule(session, current_user, schedule_id)


@router.put("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule_route(
    schedule_id: ScheduleIdPath,
    payload: ScheduleUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScheduleResponse:
    return update_schedule(session, current_user, schedule_id, payload)


@router.post("/{schedule_id}/archive", response_model=ScheduleResponse)
def archive_schedule_route(
    schedule_id: ScheduleIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScheduleResponse:
    return set_schedule_status(session, current_user, schedule_id, "ARCHIVED")


@router.post("/{schedule_id}/restore", response_model=ScheduleResponse)
def restore_schedule_route(
    schedule_id: ScheduleIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScheduleResponse:
    return set_schedule_status(session, current_user, schedule_id, "ACTIVE")
