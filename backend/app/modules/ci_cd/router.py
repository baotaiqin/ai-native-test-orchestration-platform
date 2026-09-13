from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.core.exceptions import AuthenticationError
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.ci_cd.models import CiAccessToken
from app.modules.ci_cd.schemas import (
    CiRunRequest,
    CiRunStartResponse,
    CiRunStatusResponse,
    CiTokenCreate,
    CiTokenCreateResponse,
    CiTokenListResponse,
    CiTokenMetadataResponse,
    WebhookDeliveryListResponse,
    WebhookDeliveryResponse,
    WebhookEndpointCreate,
    WebhookEndpointListResponse,
    WebhookEndpointResponse,
)
from app.modules.ci_cd.service import (
    HttpxWebhookSender,
    WebhookSender,
    authenticate_ci_token,
    create_ci_token,
    create_webhook_endpoint,
    get_ci_plan_run_status,
    list_ci_tokens,
    list_webhook_deliveries,
    list_webhook_endpoints,
    retry_webhook_delivery,
    revoke_ci_token,
    set_webhook_endpoint_status,
    trigger_ci_plan,
)

management_router = APIRouter()
ci_router = APIRouter()
ci_bearer_scheme = HTTPBearer(auto_error=False)
TokenIdPath = Annotated[int, Path(gt=0)]
PlanIdPath = Annotated[int, Path(gt=0)]
EndpointIdPath = Annotated[int, Path(gt=0)]
PlanRunIdPath = Annotated[str, Path(min_length=9, max_length=128)]
DeliveryIdPath = Annotated[str, Path(min_length=8, max_length=128)]
HeartbeatStoreDependency = Annotated[RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)]
PublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
EventStreamDependency = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]


def get_webhook_sender() -> WebhookSender:
    return HttpxWebhookSender()


WebhookSenderDependency = Annotated[WebhookSender, Depends(get_webhook_sender)]


async def get_ci_access_token(
    session: DatabaseSessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(ci_bearer_scheme)],
) -> CiAccessToken:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("CI Token 无效或已失效")
    return authenticate_ci_token(session, credentials.credentials)


CiAccessTokenDependency = Annotated[CiAccessToken, Depends(get_ci_access_token)]


@management_router.post(
    "/tokens", response_model=CiTokenCreateResponse, status_code=status.HTTP_201_CREATED
)
def create_ci_token_route(
    payload: CiTokenCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CiTokenCreateResponse:
    return create_ci_token(session, current_user, payload)


@management_router.get("/tokens", response_model=CiTokenListResponse)
def list_ci_tokens_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> CiTokenListResponse:
    return list_ci_tokens(session, current_user, project_id)


@management_router.post("/tokens/{token_id}/revoke", response_model=CiTokenMetadataResponse)
def revoke_ci_token_route(
    token_id: TokenIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CiTokenMetadataResponse:
    return revoke_ci_token(session, current_user, token_id)


@management_router.post(
    "/webhooks", response_model=WebhookEndpointResponse, status_code=status.HTTP_201_CREATED
)
def create_webhook_endpoint_route(
    payload: WebhookEndpointCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebhookEndpointResponse:
    return create_webhook_endpoint(session, current_user, payload)


@management_router.get("/webhooks", response_model=WebhookEndpointListResponse)
def list_webhook_endpoints_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> WebhookEndpointListResponse:
    return list_webhook_endpoints(session, current_user, project_id)


@management_router.post("/webhooks/{endpoint_id}/archive", response_model=WebhookEndpointResponse)
def archive_webhook_endpoint_route(
    endpoint_id: EndpointIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebhookEndpointResponse:
    return set_webhook_endpoint_status(session, current_user, endpoint_id, "ARCHIVED")


@management_router.post("/webhooks/{endpoint_id}/restore", response_model=WebhookEndpointResponse)
def restore_webhook_endpoint_route(
    endpoint_id: EndpointIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebhookEndpointResponse:
    return set_webhook_endpoint_status(session, current_user, endpoint_id, "ACTIVE")


@management_router.get("/webhook-deliveries", response_model=WebhookDeliveryListResponse)
def list_webhook_deliveries_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> WebhookDeliveryListResponse:
    return list_webhook_deliveries(session, current_user, project_id)


@management_router.post(
    "/webhook-deliveries/{delivery_id}/retry", response_model=WebhookDeliveryResponse
)
def retry_webhook_delivery_route(
    delivery_id: DeliveryIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    sender: WebhookSenderDependency,
) -> WebhookDeliveryResponse:
    return retry_webhook_delivery(session, current_user, delivery_id, sender)


@ci_router.post("/test-plans/{plan_id}/run", response_model=CiRunStartResponse)
def trigger_ci_plan_route(
    plan_id: PlanIdPath,
    payload: CiRunRequest,
    token: CiAccessTokenDependency,
    session: DatabaseSessionDependency,
    heartbeat_store: HeartbeatStoreDependency,
    publisher: PublisherDependency,
    event_stream: EventStreamDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> CiRunStartResponse:
    return trigger_ci_plan(
        session,
        token,
        plan_id,
        idempotency_key,
        payload,
        heartbeat_store,
        publisher,
        event_stream,
    )


@ci_router.get("/test-plan-runs/{plan_run_id}", response_model=CiRunStatusResponse)
def get_ci_plan_run_status_route(
    plan_run_id: PlanRunIdPath,
    token: CiAccessTokenDependency,
    session: DatabaseSessionDependency,
) -> CiRunStatusResponse:
    return get_ci_plan_run_status(session, token, plan_run_id)
