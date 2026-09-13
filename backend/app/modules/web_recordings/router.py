from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunnerHeartbeatStore,
    get_runner_heartbeat_store,
)
from app.modules.runners.router import RunnerCredentialDependency
from app.modules.web_recording_ai.schemas import (
    WebRecordingAiSuggestionCreateRequest,
    WebRecordingAiSuggestionListResponse,
    WebRecordingAiSuggestionRejectRequest,
    WebRecordingAiSuggestionResponse,
)
from app.modules.web_recording_ai.service import (
    generate_ai_suggestion,
    list_ai_suggestions,
    reject_ai_suggestion,
)
from app.modules.web_recordings.schemas import (
    WebRecordingClaimRequest,
    WebRecordingClaimResponse,
    WebRecordingCompleteRequest,
    WebRecordingCompleteResponse,
    WebRecordingConfirmRequest,
    WebRecordingConfirmResponse,
    WebRecordingControlResponse,
    WebRecordingCreateRequest,
    WebRecordingDetailResponse,
    WebRecordingDispatchResponse,
    WebRecordingExecutionPlanResponse,
    WebRecordingExecutionStartRequest,
    WebRecordingExecutionStartResponse,
    WebRecordingListResponse,
)
from app.modules.web_recordings.service import (
    cancel_recording,
    claim_recording,
    complete_recording,
    confirm_recording,
    create_recording,
    dispatch_recording,
    get_recording_control,
    get_recording_detail,
    get_recording_execution_plan,
    list_recordings,
    start_recording,
    stop_recording,
)

router = APIRouter()
RecordingIdPath = Annotated[str, Path(min_length=1, max_length=128)]
HeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]
TaskPublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]


@router.post("", response_model=WebRecordingDetailResponse, status_code=status.HTTP_201_CREATED)
def create_recording_route(
    payload: WebRecordingCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
) -> WebRecordingDetailResponse:
    return create_recording(session, current_user, payload, heartbeat_store)


@router.get("", response_model=WebRecordingListResponse)
def list_recordings_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    plan_item_id: int | None = Query(default=None, gt=0),
) -> WebRecordingListResponse:
    return list_recordings(
        session,
        current_user,
        project_id,
        page=page,
        page_size=page_size,
        plan_item_id=plan_item_id,
    )


@router.post("/{recording_id}/dispatch", response_model=WebRecordingDispatchResponse)
def dispatch_recording_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: TaskPublisherDependency,
) -> WebRecordingDispatchResponse:
    return dispatch_recording(session, current_user, recording_id, heartbeat_store, publisher)


@router.post("/{recording_id}/stop", response_model=WebRecordingDetailResponse)
def stop_recording_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingDetailResponse:
    return stop_recording(session, current_user, recording_id)


@router.post("/{recording_id}/cancel", response_model=WebRecordingDetailResponse)
def cancel_recording_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingDetailResponse:
    return cancel_recording(session, current_user, recording_id)


@router.get("/{recording_id}", response_model=WebRecordingDetailResponse)
def get_recording_detail_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingDetailResponse:
    return get_recording_detail(session, current_user, recording_id)


@router.post("/{recording_id}/claim", response_model=WebRecordingClaimResponse)
def claim_recording_route(
    recording_id: RecordingIdPath,
    payload: WebRecordingClaimRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> WebRecordingClaimResponse:
    return claim_recording(session, recording_id, credential, payload.message_id)


@router.get("/{recording_id}/execution-plan", response_model=WebRecordingExecutionPlanResponse)
def recording_execution_plan_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
) -> WebRecordingExecutionPlanResponse:
    return get_recording_execution_plan(session, recording_id, credential, message_id)


@router.post(
    "/{recording_id}/execution-start",
    response_model=WebRecordingExecutionStartResponse,
)
def recording_execution_start_route(
    recording_id: RecordingIdPath,
    payload: WebRecordingExecutionStartRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> WebRecordingExecutionStartResponse:
    return start_recording(session, recording_id, credential, payload)


@router.get("/{recording_id}/control", response_model=WebRecordingControlResponse)
def recording_control_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
) -> WebRecordingControlResponse:
    return get_recording_control(session, recording_id, credential, message_id)


@router.post(
    "/{recording_id}/execution-complete",
    response_model=WebRecordingCompleteResponse,
)
def recording_execution_complete_route(
    recording_id: RecordingIdPath,
    payload: WebRecordingCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> WebRecordingCompleteResponse:
    return complete_recording(session, recording_id, credential, payload)


@router.post("/{recording_id}/confirm", response_model=WebRecordingConfirmResponse)
def confirm_recording_route(
    recording_id: RecordingIdPath,
    payload: WebRecordingConfirmRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingConfirmResponse:
    return confirm_recording(session, current_user, recording_id, payload)


@router.post(
    "/{recording_id}/ai-suggestions",
    response_model=WebRecordingAiSuggestionResponse,
)
def generate_ai_suggestion_route(
    recording_id: RecordingIdPath,
    payload: WebRecordingAiSuggestionCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingAiSuggestionResponse:
    return generate_ai_suggestion(session, current_user, recording_id, payload)


@router.get(
    "/{recording_id}/ai-suggestions",
    response_model=WebRecordingAiSuggestionListResponse,
)
def list_ai_suggestions_route(
    recording_id: RecordingIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingAiSuggestionListResponse:
    return list_ai_suggestions(session, current_user, recording_id)


@router.post(
    "/{recording_id}/ai-suggestions/{suggestion_id}/reject",
    response_model=WebRecordingAiSuggestionResponse,
)
def reject_ai_suggestion_route(
    recording_id: RecordingIdPath,
    suggestion_id: int,
    payload: WebRecordingAiSuggestionRejectRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebRecordingAiSuggestionResponse:
    return reject_ai_suggestion(
        session,
        current_user,
        recording_id,
        suggestion_id,
        payload.decision_note,
    )
