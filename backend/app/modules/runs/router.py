from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.core.exceptions import ResourceConflictError
from app.infrastructure.object_store.client import ObjectStore, get_object_store
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.evidence.schemas import WebEvidenceArtifactType, WebEvidenceUploadResponse
from app.modules.runners.router import RunnerCredentialDependency
from app.modules.runs.schemas import (
    CaseRunListResponse,
    ExecutionCancellationResponse,
    ExecutionCompleteRequest,
    ExecutionCompleteResponse,
    ExecutionEvaluationRequest,
    ExecutionEvaluationResponse,
    ExecutionPlanResponse,
    ExecutionStartRequest,
    ExecutionStartResponse,
    NodeStatusUpdateRequest,
    RunBatchDispatchRequest,
    RunBatchDispatchResponse,
    RunClaimRequest,
    RunClaimResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunDispatchResponse,
    RunEventListResponse,
    RunListResponse,
    RunStatusUpdateRequest,
    RunValidationResponse,
    ScenarioExecutionCompleteRequest,
    ScenarioExecutionCompleteResponse,
    ScenarioExecutionEvaluationRequest,
    ScenarioExecutionEvaluationResponse,
    ScenarioExecutionPlanResponse,
    ScenarioExecutionStartResponse,
    StepRunListResponse,
    WebExecutionCompleteRequest,
    WebExecutionCompleteResponse,
    WebExecutionEvaluationRequest,
    WebExecutionEvaluationResponse,
    WebExecutionPlanResponse,
    WebExecutionStartResponse,
)
from app.modules.runs.service import (
    authorize_run_event_stream,
    cancel_run,
    claim_run,
    complete_api_execution,
    complete_scenario_execution,
    complete_web_execution,
    create_run,
    dispatch_run,
    dispatch_runs_batch,
    evaluate_api_execution,
    evaluate_scenario_execution,
    evaluate_web_execution,
    force_stop_run,
    get_execution_cancellation,
    get_execution_plan,
    get_run_detail,
    get_scenario_execution_plan,
    get_web_execution_plan,
    list_case_run_steps,
    list_project_runs,
    list_run_case_runs,
    list_run_events,
    run_event_stream_generator,
    start_api_execution,
    start_scenario_execution,
    start_web_execution,
    update_case_run_status,
    update_run_status,
    update_step_run_status,
    upload_web_evidence,
    validate_run_request,
)
from app.modules.web_failure_analysis.router import router as web_failure_analysis_router
from app.modules.web_healing.router import router as web_healing_router

router = APIRouter()
router.include_router(web_healing_router)
router.include_router(web_failure_analysis_router)
RunHeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]
TaskPublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
ObjectStoreDependency = Annotated[ObjectStore, Depends(get_object_store)]
RunEventStreamDependency = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]
RunIdPath = Annotated[str, Path(min_length=1, max_length=128)]


@router.post("/validate", response_model=RunValidationResponse)
def validate_run_route(
    payload: RunCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunHeartbeatStoreDependency,
) -> RunValidationResponse:
    validation, _ = validate_run_request(session, current_user, payload, store)
    return validation


@router.post("", response_model=RunDetailResponse, status_code=status.HTTP_201_CREATED)
def create_run_route(
    payload: RunCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunHeartbeatStoreDependency,
    event_stream: RunEventStreamDependency,
) -> RunDetailResponse:
    return create_run(session, current_user, payload, store, event_stream)


@router.get("", response_model=RunListResponse)
def list_runs_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> RunListResponse:
    return list_project_runs(session, current_user, project_id, page=page, page_size=page_size)


@router.post("/{run_id}/dispatch", response_model=RunDispatchResponse)
def dispatch_run_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunHeartbeatStoreDependency,
    publisher: TaskPublisherDependency,
    event_stream: RunEventStreamDependency,
) -> RunDispatchResponse:
    return dispatch_run(session, current_user, run_id, store, publisher, event_stream)


@router.post("/dispatch-batch", response_model=RunBatchDispatchResponse)
def dispatch_runs_batch_route(
    payload: RunBatchDispatchRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunHeartbeatStoreDependency,
    publisher: TaskPublisherDependency,
    event_stream: RunEventStreamDependency,
) -> RunBatchDispatchResponse:
    return dispatch_runs_batch(
        session,
        current_user,
        payload,
        store,
        publisher,
        event_stream,
    )


@router.post("/{run_id}/claim", response_model=RunClaimResponse)
def claim_run_route(
    run_id: RunIdPath,
    payload: RunClaimRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> RunClaimResponse:
    return claim_run(session, run_id, credential, payload, event_stream)


@router.post("/{run_id}/cancel", response_model=RunDetailResponse)
def cancel_run_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: RunEventStreamDependency,
) -> RunDetailResponse:
    return cancel_run(session, current_user, run_id, event_stream)


@router.post("/{run_id}/force-stop", response_model=RunDetailResponse)
def force_stop_run_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: RunEventStreamDependency,
) -> RunDetailResponse:
    return force_stop_run(session, current_user, run_id, event_stream)


@router.get("/{run_id}/execution-plan", response_model=ExecutionPlanResponse)
def execution_plan_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
    case_run_id: int | None = Query(default=None, gt=0),
) -> ExecutionPlanResponse:
    return get_execution_plan(
        session,
        run_id,
        credential,
        message_id,
        case_run_id,
    )


@router.get(
    "/{run_id}/execution-cancellation",
    response_model=ExecutionCancellationResponse,
)
def execution_cancellation_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
    case_run_id: int = Query(gt=0),
) -> ExecutionCancellationResponse:
    return get_execution_cancellation(session, run_id, credential, message_id, case_run_id)


@router.post("/{run_id}/execution-start", response_model=ExecutionStartResponse)
def execution_start_route(
    run_id: RunIdPath,
    payload: ExecutionStartRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> ExecutionStartResponse:
    return start_api_execution(session, run_id, credential, payload, event_stream)


@router.post("/{run_id}/execution-complete", response_model=ExecutionCompleteResponse)
def execution_complete_route(
    run_id: RunIdPath,
    payload: ExecutionCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    evidence_store: ObjectStoreDependency,
    event_stream: RunEventStreamDependency,
) -> ExecutionCompleteResponse:
    return complete_api_execution(
        session, run_id, credential, payload, evidence_store, event_stream
    )


@router.post("/{run_id}/execution-evaluate", response_model=ExecutionEvaluationResponse)
def execution_evaluate_route(
    run_id: RunIdPath,
    payload: ExecutionEvaluationRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ExecutionEvaluationResponse:
    return evaluate_api_execution(session, run_id, credential, payload)


@router.get(
    "/{run_id}/scenario-execution-plan",
    response_model=ScenarioExecutionPlanResponse,
)
def scenario_execution_plan_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
    case_run_id: int | None = Query(default=None, gt=0),
) -> ScenarioExecutionPlanResponse:
    return get_scenario_execution_plan(session, run_id, credential, message_id, case_run_id)


@router.post(
    "/{run_id}/scenario-execution-start",
    response_model=ScenarioExecutionStartResponse,
)
def scenario_execution_start_route(
    run_id: RunIdPath,
    payload: ExecutionStartRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> ScenarioExecutionStartResponse:
    return start_scenario_execution(
        session,
        run_id,
        credential,
        payload.message_id,
        payload.case_run_id,
        event_stream,
    )


@router.post(
    "/{run_id}/scenario-execution-evaluate",
    response_model=ScenarioExecutionEvaluationResponse,
)
def scenario_execution_evaluate_route(
    run_id: RunIdPath,
    payload: ScenarioExecutionEvaluationRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ScenarioExecutionEvaluationResponse:
    return evaluate_scenario_execution(session, run_id, credential, payload)


@router.post(
    "/{run_id}/scenario-execution-complete",
    response_model=ScenarioExecutionCompleteResponse,
)
def scenario_execution_complete_route(
    run_id: RunIdPath,
    payload: ScenarioExecutionCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> ScenarioExecutionCompleteResponse:
    return complete_scenario_execution(session, run_id, credential, payload, event_stream)


@router.get("/{run_id}/web-execution-plan", response_model=WebExecutionPlanResponse)
def web_execution_plan_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
    case_run_id: int | None = Query(default=None, gt=0),
) -> WebExecutionPlanResponse:
    return get_web_execution_plan(session, run_id, credential, message_id, case_run_id)


@router.post("/{run_id}/web-execution-start", response_model=WebExecutionStartResponse)
def web_execution_start_route(
    run_id: RunIdPath,
    payload: ExecutionStartRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> WebExecutionStartResponse:
    return start_web_execution(session, run_id, credential, payload, event_stream)


@router.post("/{run_id}/web-execution-complete", response_model=WebExecutionCompleteResponse)
def web_execution_complete_route(
    run_id: RunIdPath,
    payload: WebExecutionCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> WebExecutionCompleteResponse:
    return complete_web_execution(session, run_id, credential, payload, event_stream)


@router.post(
    "/{run_id}/web-execution-evaluate",
    response_model=WebExecutionEvaluationResponse,
)
def web_execution_evaluate_route(
    run_id: RunIdPath,
    payload: WebExecutionEvaluationRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> WebExecutionEvaluationResponse:
    return evaluate_web_execution(session, run_id, credential, payload)


@router.post("/{run_id}/web-evidence", response_model=WebEvidenceUploadResponse)
async def web_evidence_upload_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    evidence_store: ObjectStoreDependency,
    message_id: Annotated[str, Form(min_length=1, max_length=64)],
    case_run_id: Annotated[int, Form(gt=0)],
    artifact_type: Annotated[WebEvidenceArtifactType, Form()],
    artifact_name: Annotated[str, Form(min_length=1, max_length=128)],
    sha256: Annotated[str, Form(min_length=64, max_length=64)],
    file: Annotated[UploadFile, File(...)],
    metadata_json: Annotated[str | None, Form(max_length=65536)] = None,
) -> WebEvidenceUploadResponse:
    from app.modules.evidence.service import web_evidence_spec

    _, maximum_size, _ = web_evidence_spec(artifact_type.value)
    content = await file.read(maximum_size + 1)
    if len(content) > maximum_size:
        raise ResourceConflictError("Web Evidence 文件超过类型大小限制")
    return upload_web_evidence(
        session,
        run_id,
        credential,
        message_id=message_id,
        case_run_id=case_run_id,
        artifact_type=artifact_type.value,
        artifact_name=artifact_name,
        supplied_sha256=sha256,
        content=content,
        uploaded_mime=file.content_type,
        metadata_json=metadata_json,
        evidence_store=evidence_store,
    )


@router.post("/{run_id}/status", response_model=RunDetailResponse)
def update_run_status_route(
    run_id: RunIdPath,
    payload: RunStatusUpdateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: RunEventStreamDependency,
) -> RunDetailResponse:
    return update_run_status(session, run_id, payload, user=current_user, event_stream=event_stream)


@router.post("/{run_id}/runner-status", response_model=RunDetailResponse)
def update_run_status_from_runner_route(
    run_id: RunIdPath,
    payload: RunStatusUpdateRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    event_stream: RunEventStreamDependency,
) -> RunDetailResponse:
    return update_run_status(
        session, run_id, payload, runner_credential=credential, event_stream=event_stream
    )


@router.get("/{run_id}/case-runs", response_model=CaseRunListResponse)
def list_case_runs_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseRunListResponse:
    return list_run_case_runs(session, current_user, run_id)


@router.post("/{run_id}/case-runs/{case_run_id}/status", response_model=RunDetailResponse)
def update_case_run_status_route(
    run_id: RunIdPath,
    case_run_id: Annotated[int, Path(gt=0)],
    payload: NodeStatusUpdateRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> RunDetailResponse:
    return update_case_run_status(
        session, run_id, case_run_id, payload, runner_credential=credential
    )


@router.get("/{run_id}/case-runs/{case_run_id}/step-runs", response_model=StepRunListResponse)
def list_step_runs_route(
    run_id: RunIdPath,
    case_run_id: Annotated[int, Path(gt=0)],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> StepRunListResponse:
    return list_case_run_steps(session, current_user, run_id, case_run_id)


@router.get("/{run_id}/events", response_model=RunEventListResponse)
def list_run_events_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: RunEventStreamDependency,
    after_id: str = Query(default="0-0", min_length=3, max_length=64, pattern=r"^\d+-\d+$"),
    limit: int = Query(default=100, ge=1, le=100),
) -> RunEventListResponse:
    return list_run_events(
        session,
        current_user,
        run_id,
        event_stream,
        after_id=after_id,
        limit=limit,
    )


@router.get("/{run_id}/events/stream")
def run_events_stream_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    event_stream: RunEventStreamDependency,
    after_id: str = Query(default="0-0", min_length=3, max_length=64, pattern=r"^\d+-\d+$"),
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
        min_length=3,
        max_length=64,
        pattern=r"^\d+-\d+$",
    ),
) -> StreamingResponse:
    project_id, resolved_run_id = authorize_run_event_stream(session, current_user, run_id)
    cursor = last_event_id or after_id
    session.close()
    return StreamingResponse(
        run_event_stream_generator(
            event_stream,
            project_id,
            resolved_run_id,
            after_id=cursor,
            limit=100,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{run_id}/case-runs/{case_run_id}/step-runs/{step_run_id}/status",
    response_model=RunDetailResponse,
)
def update_step_run_status_route(
    run_id: RunIdPath,
    case_run_id: Annotated[int, Path(gt=0)],
    step_run_id: Annotated[int, Path(gt=0)],
    payload: NodeStatusUpdateRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> RunDetailResponse:
    return update_step_run_status(
        session,
        run_id,
        case_run_id,
        step_run_id,
        payload,
        runner_credential=credential,
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RunDetailResponse:
    return get_run_detail(session, current_user, run_id)
