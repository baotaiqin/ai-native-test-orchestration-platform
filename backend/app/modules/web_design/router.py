from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import sessionmaker

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.core.exceptions import ResourceConflictError
from app.infrastructure.object_store.client import ObjectStore, get_object_store
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunnerHeartbeatStore,
    get_runner_heartbeat_store,
)
from app.modules.runners.router import RunnerCredentialDependency
from app.modules.web_design.schemas import (
    ExplorationClaimResponse,
    ExplorationCompleteRequest,
    ExplorationCompleteResponse,
    ExplorationControlResponse,
    ExplorationCreateRequest,
    ExplorationDecisionRequest,
    ExplorationDecisionResponse,
    ExplorationDispatchResponse,
    ExplorationEvidenceKind,
    ExplorationEvidenceUploadResponse,
    ExplorationExecutionPlanResponse,
    ExplorationListResponse,
    ExplorationMessageRequest,
    ExplorationResponse,
    ExplorationStartResponse,
    RevisionCreateRequest,
    RevisionDecisionRequest,
    RevisionListResponse,
    RevisionResponse,
    RevisionUpdateRequest,
    WebPlanCreateRequest,
    WebPlanListResponse,
    WebPlanResponse,
)
from app.modules.web_design.service import (
    cancel_exploration,
    claim_exploration,
    complete_exploration,
    create_exploration,
    create_plan_task,
    create_revision_task,
    decide_exploration_step,
    decide_revision,
    dispatch_exploration,
    download_exploration_evidence,
    get_exploration,
    get_exploration_control,
    get_exploration_execution_plan,
    get_plan,
    list_explorations,
    list_plan_explorations,
    list_plans,
    list_revisions,
    process_plan_task,
    process_revision_task,
    start_exploration,
    stop_exploration,
    update_revision,
    upload_exploration_evidence,
)

router = APIRouter()
ExplorationIdPath = Annotated[str, Path(min_length=1, max_length=128)]
HeartbeatStoreDependency = Annotated[RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)]
TaskPublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
ObjectStoreDependency = Annotated[ObjectStore, Depends(get_object_store)]


@router.post(
    "/plans",
    response_model=WebPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="从固定需求/API 快照生成 Web 测试方案",
)
def create_plan_route(
    payload: WebPlanCreateRequest,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebPlanResponse:
    plan = create_plan_task(session, current_user, payload)
    if plan.generation_status in {"QUEUED", "RUNNING"} and not plan.reused:
        factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
        background_tasks.add_task(process_plan_task, factory, plan.id, current_user)
    return plan


@router.get("/plans", response_model=WebPlanListResponse, summary="Web 测试方案历史")
def list_plans_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> WebPlanListResponse:
    return list_plans(session, current_user, project_id)


@router.get("/plans/{plan_id}", response_model=WebPlanResponse, summary="Web 测试方案详情")
def get_plan_route(
    plan_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebPlanResponse:
    return get_plan(session, current_user, plan_id)


@router.get(
    "/plans/{plan_id}/explorations",
    response_model=ExplorationListResponse,
    summary="Web 测试方案全部候选用例的 MCP 探索历史",
)
def list_plan_explorations_route(
    plan_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ExplorationListResponse:
    return list_plan_explorations(session, current_user, plan_id)


@router.post(
    "/plan-items/{item_id}/explorations",
    response_model=ExplorationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="为候选用例创建 Playwright MCP 探索",
)
def create_exploration_route(
    item_id: int,
    payload: ExplorationCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
) -> ExplorationResponse:
    return create_exploration(session, current_user, item_id, payload, heartbeat_store)


@router.get(
    "/plan-items/{item_id}/explorations",
    response_model=ExplorationListResponse,
    summary="候选用例 MCP 探索历史",
)
def list_explorations_route(
    item_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ExplorationListResponse:
    return list_explorations(session, current_user, item_id)


@router.get("/explorations/{exploration_id}", response_model=ExplorationResponse)
def get_exploration_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ExplorationResponse:
    return get_exploration(session, current_user, exploration_id)


@router.post(
    "/explorations/{exploration_id}/dispatch",
    response_model=ExplorationDispatchResponse,
)
def dispatch_exploration_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: TaskPublisherDependency,
) -> ExplorationDispatchResponse:
    return dispatch_exploration(session, current_user, exploration_id, heartbeat_store, publisher)


@router.post("/explorations/{exploration_id}/stop", response_model=ExplorationResponse)
def stop_exploration_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ExplorationResponse:
    return stop_exploration(session, current_user, exploration_id)


@router.post("/explorations/{exploration_id}/cancel", response_model=ExplorationResponse)
def cancel_exploration_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ExplorationResponse:
    return cancel_exploration(session, current_user, exploration_id)


@router.post("/explorations/{exploration_id}/claim", response_model=ExplorationClaimResponse)
def claim_exploration_route(
    exploration_id: ExplorationIdPath,
    payload: ExplorationMessageRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ExplorationClaimResponse:
    return claim_exploration(session, exploration_id, credential, payload.message_id)


@router.get(
    "/explorations/{exploration_id}/execution-plan",
    response_model=ExplorationExecutionPlanResponse,
)
def exploration_execution_plan_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
) -> ExplorationExecutionPlanResponse:
    return get_exploration_execution_plan(session, exploration_id, credential, message_id)


@router.post(
    "/explorations/{exploration_id}/execution-start",
    response_model=ExplorationStartResponse,
)
def exploration_execution_start_route(
    exploration_id: ExplorationIdPath,
    payload: ExplorationMessageRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ExplorationStartResponse:
    return start_exploration(session, exploration_id, credential, payload.message_id)


@router.get(
    "/explorations/{exploration_id}/control",
    response_model=ExplorationControlResponse,
)
def exploration_control_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    message_id: str = Query(min_length=1, max_length=64),
) -> ExplorationControlResponse:
    return get_exploration_control(session, exploration_id, credential, message_id)


@router.post(
    "/explorations/{exploration_id}/decision",
    response_model=ExplorationDecisionResponse,
)
def exploration_decision_route(
    exploration_id: ExplorationIdPath,
    payload: ExplorationDecisionRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ExplorationDecisionResponse:
    return decide_exploration_step(session, exploration_id, credential, payload)


@router.post(
    "/explorations/{exploration_id}/evidence",
    response_model=ExplorationEvidenceUploadResponse,
)
async def exploration_evidence_upload_route(
    exploration_id: ExplorationIdPath,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
    evidence_store: ObjectStoreDependency,
    message_id: Annotated[str, Form(min_length=1, max_length=64)],
    sequence: Annotated[int, Form(ge=1, le=50)],
    kind: Annotated[ExplorationEvidenceKind, Form()],
    label: Annotated[str, Form(min_length=1, max_length=255)],
    artifact_name: Annotated[str, Form(min_length=1, max_length=128)],
    sha256: Annotated[str, Form(min_length=64, max_length=64)],
    file: Annotated[UploadFile, File(...)],
) -> ExplorationEvidenceUploadResponse:
    content = await file.read(5_000_001)
    if len(content) > 5_000_000:
        raise ResourceConflictError("探索截图超过 5MB 限制")
    return upload_exploration_evidence(
        session,
        exploration_id,
        credential,
        message_id=message_id,
        sequence=sequence,
        kind=kind.value,
        label=label,
        artifact_name=artifact_name,
        supplied_sha256=sha256,
        content=content,
        uploaded_mime=file.content_type,
        evidence_store=evidence_store,
    )


@router.post(
    "/explorations/{exploration_id}/execution-complete",
    response_model=ExplorationCompleteResponse,
)
def exploration_complete_route(
    exploration_id: ExplorationIdPath,
    payload: ExplorationCompleteRequest,
    session: DatabaseSessionDependency,
    credential: RunnerCredentialDependency,
) -> ExplorationCompleteResponse:
    return complete_exploration(session, exploration_id, credential, payload)


@router.get("/exploration-evidence/{evidence_id}/download")
def exploration_evidence_download_route(
    evidence_id: Annotated[str, Path(min_length=1, max_length=128)],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    evidence_store: ObjectStoreDependency,
) -> StreamingResponse:
    evidence, content = download_exploration_evidence(
        session, current_user, evidence_id, evidence_store
    )
    return StreamingResponse(
        iter((content,)),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{evidence.file_name}"'},
    )


@router.post(
    "/plan-items/{item_id}/revisions",
    response_model=RevisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="用录制或 MCP 观察校准候选 Web 用例",
)
def create_revision_route(
    item_id: int,
    payload: RevisionCreateRequest,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RevisionResponse:
    revision = create_revision_task(session, current_user, item_id, payload)
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    background_tasks.add_task(process_revision_task, factory, revision.id, current_user)
    return revision


@router.get("/plan-items/{item_id}/revisions", response_model=RevisionListResponse)
def list_revisions_route(
    item_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RevisionListResponse:
    return list_revisions(session, current_user, item_id)


@router.patch("/revisions/{revision_id}", response_model=RevisionResponse)
def update_revision_route(
    revision_id: int,
    payload: RevisionUpdateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RevisionResponse:
    return update_revision(session, current_user, revision_id, payload)


@router.post("/revisions/{revision_id}/decision", response_model=RevisionResponse)
def decide_revision_route(
    revision_id: int,
    payload: RevisionDecisionRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RevisionResponse:
    return decide_revision(session, current_user, revision_id, payload)
