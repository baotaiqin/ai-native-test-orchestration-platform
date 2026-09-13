from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.core.exceptions import AuthenticationError
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.runners.schemas import (
    RegistrationTokenCreateRequest,
    RegistrationTokenCreateResponse,
    RegistrationTokenListResponse,
    RegistrationTokenMetadataResponse,
    RunnerDisconnectReconcileResponse,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerListResponse,
    RunnerRegisterResponse,
    RunnerRegistrationRequest,
    RunnerResponse,
)
from app.modules.runners.service import (
    create_registration_token,
    get_runner,
    heartbeat_runner,
    list_registration_tokens,
    list_runners,
    register_runner,
    revoke_registration_token,
    revoke_runner,
)
from app.modules.runs.service import reconcile_disconnected_runner_runs

router = APIRouter()
runner_bearer_scheme = HTTPBearer(auto_error=False)
RunnerHeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]
RunnerIdPath = Annotated[str, Path(min_length=1, max_length=64)]
TokenIdPath = Annotated[int, Path(gt=0)]


async def get_runner_credential(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(runner_bearer_scheme)
    ],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Runner credential 无效")
    return credentials.credentials


RunnerCredentialDependency = Annotated[str, Depends(get_runner_credential)]


@router.post(
    "/registration-tokens",
    response_model=RegistrationTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建一次性 Runner Registration Token",
)
def create_registration_token_route(
    payload: RegistrationTokenCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RegistrationTokenCreateResponse:
    return create_registration_token(session, current_user, payload)


@router.get(
    "/registration-tokens",
    response_model=RegistrationTokenListResponse,
    summary="查看 Runner Registration Token 元数据",
)
def list_registration_tokens_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RegistrationTokenListResponse:
    return list_registration_tokens(session, current_user)


@router.post(
    "/registration-tokens/{token_id}/revoke",
    response_model=RegistrationTokenMetadataResponse,
    summary="撤销 Runner Registration Token",
)
def revoke_registration_token_route(
    token_id: TokenIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RegistrationTokenMetadataResponse:
    return revoke_registration_token(session, current_user, token_id)


@router.post(
    "/register",
    response_model=RunnerRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Runner 使用一次性 Token 注册",
)
def register_runner_route(
    payload: RunnerRegistrationRequest,
    session: DatabaseSessionDependency,
) -> RunnerRegisterResponse:
    return register_runner(session, payload)


@router.post(
    "/{runner_id}/heartbeat",
    response_model=RunnerHeartbeatResponse,
    summary="Runner 上报心跳与运行环境",
)
def heartbeat_runner_route(
    payload: RunnerHeartbeatRequest,
    runner_id: RunnerIdPath,
    credential: RunnerCredentialDependency,
    session: DatabaseSessionDependency,
    store: RunnerHeartbeatStoreDependency,
) -> RunnerHeartbeatResponse:
    return heartbeat_runner(session, runner_id, credential, payload, store)


@router.get("", response_model=RunnerListResponse, summary="Runner 列表")
def list_runners_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunnerHeartbeatStoreDependency,
) -> RunnerListResponse:
    return list_runners(session, current_user, store)


@router.get("/{runner_id}", response_model=RunnerResponse, summary="Runner 详情")
def get_runner_route(
    runner_id: RunnerIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunnerHeartbeatStoreDependency,
) -> RunnerResponse:
    return get_runner(session, current_user, runner_id, store)


@router.post("/{runner_id}/revoke", response_model=RunnerResponse, summary="撤销 Runner credential")
def revoke_runner_route(
    runner_id: RunnerIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunnerHeartbeatStoreDependency,
) -> RunnerResponse:
    return revoke_runner(session, current_user, runner_id, store)


@router.post(
    "/{runner_id}/reconcile-disconnected-runs",
    response_model=RunnerDisconnectReconcileResponse,
    summary="收口离线 Runner 的执行 Run",
)
def reconcile_disconnected_runner_runs_route(
    runner_id: RunnerIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunnerHeartbeatStoreDependency,
    event_stream: Annotated[
        RedisRunEventStream, Depends(get_run_event_stream)
    ],
) -> RunnerDisconnectReconcileResponse:
    return reconcile_disconnected_runner_runs(
        session, current_user, runner_id, store, event_stream
    )
