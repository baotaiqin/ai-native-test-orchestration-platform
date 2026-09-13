import hashlib
import hmac
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    AuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.core.secret_cipher import decrypt_secret, encrypt_secret
from app.core.time import to_utc_isoformat, utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublisher
from app.infrastructure.redis.client import RedisRunEventStream, RedisRunnerHeartbeatStore
from app.modules.auth.models import User
from app.modules.auth.schemas import CurrentUser, UserStatus
from app.modules.ci_cd.models import (
    CiAccessToken,
    CiRunInvocation,
    WebhookDelivery,
    WebhookEndpoint,
)
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
from app.modules.ci_cd.security import generate_ci_token
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_owner, ensure_project_writable, get_project
from app.modules.runners.security import digest_secret
from app.modules.test_plans.models import TestPlan, TestPlanRun
from app.modules.test_plans.service import get_test_plan_run, start_test_plan_run

_WEBHOOK_EVENT = "TEST_PLAN_RUN_COMPLETED"
_WEBHOOK_SENDING_LEASE_SECONDS = 90


def _actor(session: Session, user_id: str) -> CurrentUser:
    user = session.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise ResourceConflictError("凭据创建人不存在或已停用")
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=[user.platform_role],
    )


def _token_metadata(token: CiAccessToken) -> CiTokenMetadataResponse:
    now = utc_now_naive()
    status = token.status
    if status == "ACTIVE" and token.expires_at <= now:
        status = "EXPIRED"
    return CiTokenMetadataResponse(
        id=token.id,
        project_id=token.project_id,
        name=token.name,
        token_prefix=token.token_prefix,
        allowed_plan_ids=token.allowed_plan_ids,
        status=status,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
        created_by=token.created_by,
        created_at=token.created_at,
    )


def _validate_plan_scope(
    session: Session, project_id: int, allowed_plan_ids: list[int] | None
) -> None:
    if allowed_plan_ids is None:
        return
    found = set(
        session.scalars(
            select(TestPlan.id).where(
                TestPlan.project_id == project_id,
                TestPlan.id.in_(allowed_plan_ids),
            )
        ).all()
    )
    if found != set(allowed_plan_ids):
        raise ResourceConflictError("CI Token 包含不属于当前项目的 Test Plan")


def create_ci_token(
    session: Session, user: CurrentUser, payload: CiTokenCreate
) -> CiTokenCreateResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建 CI Token")
    _validate_plan_scope(session, payload.project_id, payload.allowed_plan_ids)
    plaintext = generate_ci_token()
    token = CiAccessToken(
        project_id=payload.project_id,
        name=payload.name,
        token_digest=digest_secret(plaintext),
        token_prefix=plaintext[:12],
        allowed_plan_ids=payload.allowed_plan_ids,
        status="ACTIVE",
        expires_at=utc_now_naive() + timedelta(days=payload.expires_in_days),
        created_by=user.id,
    )
    session.add(token)
    session.commit()
    return CiTokenCreateResponse(token=plaintext, metadata=_token_metadata(token))


def list_ci_tokens(session: Session, user: CurrentUser, project_id: int) -> CiTokenListResponse:
    project = get_project(session, user, project_id)
    ensure_project_owner(session, project, user)
    tokens = list(
        session.scalars(
            select(CiAccessToken)
            .where(CiAccessToken.project_id == project_id)
            .order_by(CiAccessToken.created_at.desc(), CiAccessToken.id.desc())
        ).all()
    )
    changed = False
    now = utc_now_naive()
    for token in tokens:
        if token.status == "ACTIVE" and token.expires_at <= now:
            token.status = "EXPIRED"
            changed = True
    if changed:
        session.commit()
    return CiTokenListResponse(
        items=[_token_metadata(token) for token in tokens], total=len(tokens)
    )


def revoke_ci_token(session: Session, user: CurrentUser, token_id: int) -> CiTokenMetadataResponse:
    token = session.get(CiAccessToken, token_id)
    if token is None:
        raise ResourceNotFoundError("CI Token 不存在")
    project = get_project(session, user, token.project_id)
    ensure_project_owner(session, project, user)
    if token.status == "ACTIVE":
        token.status = "REVOKED"
        token.revoked_at = utc_now_naive()
        session.commit()
    return _token_metadata(token)


def authenticate_ci_token(session: Session, plaintext: str) -> CiAccessToken:
    if not plaintext.startswith("cit_") or not 40 <= len(plaintext) <= 64:
        raise AuthenticationError("CI Token 无效或已失效")
    token = session.scalar(
        select(CiAccessToken).where(CiAccessToken.token_digest == digest_secret(plaintext))
    )
    now = utc_now_naive()
    if token is None or token.status != "ACTIVE":
        raise AuthenticationError("CI Token 无效或已失效")
    if token.expires_at <= now:
        token.status = "EXPIRED"
        session.commit()
        raise AuthenticationError("CI Token 无效或已失效")
    token.last_used_at = now
    session.commit()
    return token


def _ensure_ci_plan_allowed(token: CiAccessToken, plan_id: int) -> None:
    if token.allowed_plan_ids is not None and plan_id not in token.allowed_plan_ids:
        raise AuthenticationError("CI Token 无权访问该 Test Plan")


def trigger_ci_plan(
    session: Session,
    token: CiAccessToken,
    plan_id: int,
    idempotency_key: str,
    payload: CiRunRequest,
    heartbeat_store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> CiRunStartResponse:
    _ensure_ci_plan_allowed(token, plan_id)
    plan = session.get(TestPlan, plan_id)
    if plan is None or plan.project_id != token.project_id:
        raise ResourceNotFoundError("Test Plan 不存在")
    key_digest = digest_secret(f"ci-idempotency:{idempotency_key}")
    invocation = CiRunInvocation(
        id=f"ciinv_{uuid4().hex}",
        token_id=token.id,
        project_id=token.project_id,
        plan_id=plan_id,
        idempotency_digest=key_digest,
        pipeline_ref=payload.pipeline_ref,
        commit_sha=payload.commit_sha,
        status="STARTING",
    )
    session.add(invocation)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(CiRunInvocation).where(
                CiRunInvocation.token_id == token.id,
                CiRunInvocation.idempotency_digest == key_digest,
            )
        )
        if existing is not None and (
            existing.plan_id != plan_id
            or existing.pipeline_ref != payload.pipeline_ref
            or existing.commit_sha != payload.commit_sha
        ):
            raise ResourceConflictError("相同 Idempotency-Key 已用于不同的 CI 触发请求") from None
        if existing is not None and existing.status == "STARTED" and existing.plan_run_id:
            plan_run = session.get(TestPlanRun, existing.plan_run_id)
            return CiRunStartResponse(
                plan_run_id=existing.plan_run_id,
                status=plan_run.status if plan_run is not None else "CREATED",
                status_url=f"/api/v1/ci/test-plan-runs/{existing.plan_run_id}",
                idempotent_replay=True,
            )
        raise ResourceConflictError("相同 Idempotency-Key 的触发仍在处理或已经失败") from None
    try:
        result = start_test_plan_run(
            session,
            _actor(session, token.created_by),
            plan_id,
            heartbeat_store,
            publisher,
            event_stream,
            trigger_type="API",
        )
    except AppError as exc:
        session.rollback()
        persisted = session.get(CiRunInvocation, invocation.id)
        if persisted is not None:
            persisted.status = "FAILED"
            persisted.error_code = exc.code
            persisted.error_message = exc.message
            persisted.completed_at = utc_now_naive()
            session.commit()
        raise
    persisted = session.get(CiRunInvocation, invocation.id)
    assert persisted is not None
    persisted.status = "STARTED"
    persisted.plan_run_id = result.plan_run.id
    persisted.completed_at = utc_now_naive()
    session.commit()
    return CiRunStartResponse(
        plan_run_id=result.plan_run.id,
        status=result.plan_run.status.value,
        status_url=f"/api/v1/ci/test-plan-runs/{result.plan_run.id}",
        idempotent_replay=False,
    )


def get_ci_plan_run_status(
    session: Session, token: CiAccessToken, plan_run_id: str
) -> CiRunStatusResponse:
    invocation = session.scalar(
        select(CiRunInvocation).where(
            CiRunInvocation.project_id == token.project_id,
            CiRunInvocation.plan_run_id == plan_run_id,
            CiRunInvocation.status == "STARTED",
        )
    )
    if invocation is None:
        raise ResourceNotFoundError("CI Test Plan Run 不存在")
    _ensure_ci_plan_allowed(token, invocation.plan_id)
    plan_run = get_test_plan_run(session, _actor(session, token.created_by), plan_run_id)
    return CiRunStatusResponse(
        invocation_id=invocation.id,
        pipeline_ref=invocation.pipeline_ref,
        commit_sha=invocation.commit_sha,
        plan_run=plan_run,
    )


def _webhook_context(project_id: int) -> str:
    return f"{get_settings().secret_key}:webhook:{project_id}"


def _target_hint(url: str) -> str:
    parsed = urlsplit(url)
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{parsed.hostname}{port}/…"


def _endpoint_response(endpoint: WebhookEndpoint) -> WebhookEndpointResponse:
    return WebhookEndpointResponse(
        id=endpoint.id,
        project_id=endpoint.project_id,
        name=endpoint.name,
        target_hint=endpoint.target_hint,
        has_signing_secret=endpoint.encrypted_signing_secret is not None,
        events=endpoint.events,
        enabled=endpoint.enabled,
        status=endpoint.status,
        max_attempts=endpoint.max_attempts,
        timeout_seconds=endpoint.timeout_seconds,
        created_by=endpoint.created_by,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


def create_webhook_endpoint(
    session: Session, user: CurrentUser, payload: WebhookEndpointCreate
) -> WebhookEndpointResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建 Webhook")
    url = payload.url.get_secret_value()
    context = _webhook_context(payload.project_id)
    endpoint = WebhookEndpoint(
        project_id=payload.project_id,
        name=payload.name,
        encrypted_url=encrypt_secret(url, context),
        target_hint=_target_hint(url),
        encrypted_signing_secret=(
            encrypt_secret(payload.signing_secret.get_secret_value(), context)
            if payload.signing_secret
            else None
        ),
        events=payload.events,
        enabled=payload.enabled,
        status="ACTIVE",
        max_attempts=payload.max_attempts,
        timeout_seconds=payload.timeout_seconds,
        created_by=user.id,
    )
    session.add(endpoint)
    session.commit()
    return _endpoint_response(endpoint)


def list_webhook_endpoints(
    session: Session, user: CurrentUser, project_id: int
) -> WebhookEndpointListResponse:
    project = get_project(session, user, project_id)
    ensure_project_owner(session, project, user)
    endpoints = list(
        session.scalars(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.project_id == project_id)
            .order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
        ).all()
    )
    return WebhookEndpointListResponse(
        items=[_endpoint_response(endpoint) for endpoint in endpoints], total=len(endpoints)
    )


def set_webhook_endpoint_status(
    session: Session, user: CurrentUser, endpoint_id: int, status: str
) -> WebhookEndpointResponse:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise ResourceNotFoundError("Webhook 不存在")
    project = get_project(session, user, endpoint.project_id)
    ensure_project_owner(session, project, user)
    endpoint.status = status
    endpoint.enabled = status == "ACTIVE"
    session.commit()
    return _endpoint_response(endpoint)


def enqueue_test_plan_run_webhooks(session: Session, plan_run: TestPlanRun) -> int:
    if plan_run.status not in {"SUCCESS", "FAILED", "CANCELLED", "TIMEOUT"}:
        return 0
    endpoints = list(
        session.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.project_id == plan_run.project_id,
                WebhookEndpoint.status == "ACTIVE",
                WebhookEndpoint.enabled.is_(True),
            )
        ).all()
    )
    statuses = [item.status for item in plan_run.items]
    now = utc_now_naive()
    event_id = f"test-plan-run:{plan_run.id}:completed"
    created = 0
    for endpoint in endpoints:
        if _WEBHOOK_EVENT not in endpoint.events:
            continue
        exists = session.scalar(
            select(WebhookDelivery.id).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_id == event_id,
            )
        )
        if exists is not None:
            continue
        session.add(
            WebhookDelivery(
                id=f"whd_{uuid4().hex}",
                endpoint_id=endpoint.id,
                project_id=plan_run.project_id,
                plan_run_id=plan_run.id,
                event_id=event_id,
                event_type=_WEBHOOK_EVENT,
                payload={
                    "event_id": event_id,
                    "event_type": _WEBHOOK_EVENT,
                    "project_id": plan_run.project_id,
                    "plan_id": plan_run.plan_id,
                    "plan_run_id": plan_run.id,
                    "plan_name": str(plan_run.plan_snapshot.get("name") or "Test Plan"),
                    "trigger_type": plan_run.trigger_type,
                    "status": plan_run.status,
                    "total": len(statuses),
                    "passed": sum(status == "SUCCESS" for status in statuses),
                    "failed": sum(status != "SUCCESS" for status in statuses),
                    "started_at": to_utc_isoformat(plan_run.started_at),
                    "ended_at": to_utc_isoformat(plan_run.ended_at),
                    "status_url": f"/api/v1/test-plans/runs/{plan_run.id}",
                },
                status="PENDING",
                next_attempt_at=now,
            )
        )
        created += 1
    if created:
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return 0
    return created


@dataclass(frozen=True)
class WebhookSendResult:
    status_code: int


class WebhookSender(Protocol):
    def send(
        self,
        *,
        url: str,
        body: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> WebhookSendResult: ...


class WebhookSendError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _validate_public_webhook_target(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username:
        raise WebhookSendError(
            "WEBHOOK_TARGET_BLOCKED", "Webhook 目标不符合安全策略", retryable=False
        )
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except (OSError, ValueError) as exc:
        raise WebhookSendError(
            "WEBHOOK_DNS_FAILED", "Webhook 目标域名解析失败", retryable=True
        ) from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise WebhookSendError(
            "WEBHOOK_TARGET_BLOCKED", "Webhook 目标不符合安全策略", retryable=False
        )


class HttpxWebhookSender:
    def send(
        self,
        *,
        url: str,
        body: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> WebhookSendResult:
        _validate_public_webhook_target(url)
        try:
            with httpx.Client(
                timeout=timeout_seconds, follow_redirects=False, trust_env=False
            ) as client:
                response = client.post(url, content=body.encode("utf-8"), headers=headers)
        except httpx.TimeoutException as exc:
            raise WebhookSendError("WEBHOOK_TIMEOUT", "Webhook 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WebhookSendError(
                "WEBHOOK_NETWORK_ERROR", "Webhook 网络请求失败", retryable=True
            ) from exc
        return WebhookSendResult(status_code=response.status_code)


def _delivery_response(session: Session, delivery: WebhookDelivery) -> WebhookDeliveryResponse:
    endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
    return WebhookDeliveryResponse(
        id=delivery.id,
        endpoint_id=delivery.endpoint_id,
        endpoint_name=endpoint.name if endpoint is not None else "已删除 Webhook",
        project_id=delivery.project_id,
        plan_run_id=delivery.plan_run_id,
        event_id=delivery.event_id,
        event_type=delivery.event_type,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        next_attempt_at=delivery.next_attempt_at,
        response_status=delivery.response_status,
        last_error_code=delivery.last_error_code,
        last_error_message=delivery.last_error_message,
        last_attempt_at=delivery.last_attempt_at,
        created_at=delivery.created_at,
        completed_at=delivery.completed_at,
    )


def list_webhook_deliveries(
    session: Session, user: CurrentUser, project_id: int
) -> WebhookDeliveryListResponse:
    get_project(session, user, project_id)
    deliveries = list(
        session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.project_id == project_id)
            .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(100)
        ).all()
    )
    return WebhookDeliveryListResponse(
        items=[_delivery_response(session, delivery) for delivery in deliveries],
        total=len(deliveries),
    )


def dispatch_webhook_delivery(
    session: Session, delivery_id: str, sender: WebhookSender
) -> WebhookDeliveryResponse:
    delivery = session.scalar(
        select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None:
        raise ResourceNotFoundError("Webhook Delivery 不存在")
    now = utc_now_naive()
    stale_sending = (
        delivery.status == "SENDING"
        and delivery.last_attempt_at is not None
        and delivery.last_attempt_at <= now - timedelta(seconds=_WEBHOOK_SENDING_LEASE_SECONDS)
    )
    if stale_sending:
        delivery.status = "RETRY"
    if delivery.status not in {"PENDING", "RETRY"} or delivery.next_attempt_at > now:
        return _delivery_response(session, delivery)
    endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None or endpoint.status != "ACTIVE" or not endpoint.enabled:
        delivery.status = "FAILED"
        delivery.last_error_code = "WEBHOOK_DISABLED"
        delivery.last_error_message = "Webhook 已停用或归档"
        delivery.completed_at = utc_now_naive()
        session.commit()
        return _delivery_response(session, delivery)
    delivery.status = "SENDING"
    delivery.attempt_count += 1
    delivery.last_attempt_at = now
    session.commit()
    context = _webhook_context(endpoint.project_id)
    url = decrypt_secret(endpoint.encrypted_url, context)
    signing_secret = (
        decrypt_secret(endpoint.encrypted_signing_secret, context)
        if endpoint.encrypted_signing_secret
        else None
    )
    timestamp = str(int(utc_now_naive().timestamp()))
    body = json.dumps(delivery.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    headers = {
        "Content-Type": "application/json",
        "X-AI-Test-Event": delivery.event_type,
        "X-AI-Test-Delivery": delivery.id,
        "X-AI-Test-Timestamp": timestamp,
    }
    if signing_secret:
        signature = hmac.new(
            signing_secret.encode("utf-8"),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-AI-Test-Signature-256"] = f"sha256={signature}"
    error: WebhookSendError | None = None
    response_status: int | None = None
    try:
        result = sender.send(
            url=url,
            body=body,
            headers=headers,
            timeout_seconds=endpoint.timeout_seconds,
        )
        response_status = result.status_code
        if not 200 <= result.status_code < 300:
            error = WebhookSendError(
                "WEBHOOK_HTTP_ERROR",
                f"Webhook 返回 HTTP {result.status_code}",
                retryable=result.status_code >= 500 or result.status_code == 429,
            )
    except WebhookSendError as exc:
        error = exc
    delivery = session.get(WebhookDelivery, delivery_id)
    endpoint = session.get(WebhookEndpoint, delivery.endpoint_id) if delivery else None
    assert delivery is not None and endpoint is not None
    delivery.response_status = response_status
    if error is None:
        delivery.status = "SUCCEEDED"
        delivery.last_error_code = None
        delivery.last_error_message = None
        delivery.completed_at = utc_now_naive()
    elif error.retryable and delivery.attempt_count < endpoint.max_attempts:
        delivery.status = "RETRY"
        delivery.last_error_code = error.code
        delivery.last_error_message = error.message
        delivery.next_attempt_at = utc_now_naive() + timedelta(
            seconds=min(900, 30 * (2 ** (delivery.attempt_count - 1)))
        )
    else:
        delivery.status = "FAILED"
        delivery.last_error_code = error.code
        delivery.last_error_message = error.message
        delivery.completed_at = utc_now_naive()
    session.commit()
    return _delivery_response(session, delivery)


def retry_webhook_delivery(
    session: Session,
    user: CurrentUser,
    delivery_id: str,
    sender: WebhookSender,
) -> WebhookDeliveryResponse:
    delivery = session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise ResourceNotFoundError("Webhook Delivery 不存在")
    project = get_project(session, user, delivery.project_id)
    ensure_project_writable(session, project, user)
    if delivery.status != "FAILED":
        raise ResourceConflictError("只有最终失败的 Webhook Delivery 可以手动重试")
    delivery.status = "PENDING"
    delivery.attempt_count = 0
    delivery.next_attempt_at = utc_now_naive()
    delivery.completed_at = None
    delivery.response_status = None
    delivery.last_error_code = None
    delivery.last_error_message = None
    delivery.last_attempt_at = None
    session.commit()
    return dispatch_webhook_delivery(session, delivery.id, sender)
