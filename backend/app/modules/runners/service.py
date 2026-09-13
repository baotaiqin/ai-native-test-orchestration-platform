from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RedisUnavailableError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.core.logging import get_logger
from app.infrastructure.redis.client import (
    RedisRunnerHeartbeatStore,
    RedisStoreUnavailableError,
)
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.service import is_admin
from app.modules.runners.models import (
    Runner,
    RunnerCapability,
    RunnerRegistrationToken,
    RunnerSlot,
    RunnerTag,
)
from app.modules.runners.schemas import (
    OnlineStatus,
    RegistrationTokenCreateRequest,
    RegistrationTokenCreateResponse,
    RegistrationTokenListResponse,
    RegistrationTokenMetadataResponse,
    RegistrationTokenStatus,
    RunnerCapabilityResponse,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerListResponse,
    RunnerRegisterResponse,
    RunnerRegistrationRequest,
    RunnerResponse,
    RunnerSlotResponse,
    RunnerStatus,
)
from app.modules.runners.security import (
    digest_secret,
    generate_registration_token,
    generate_runner_credential,
    matches_digest,
)

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        raise AuthorizationError("只有管理员可以管理 Runner")


def _token_status(
    token: RunnerRegistrationToken, now: datetime | None = None
) -> RegistrationTokenStatus:
    if (
        token.status == RegistrationTokenStatus.ACTIVE.value
        and token.expires_at <= (now or _utc_now())
    ):
        return RegistrationTokenStatus.EXPIRED
    return RegistrationTokenStatus(token.status)


def _token_metadata(token: RunnerRegistrationToken) -> RegistrationTokenMetadataResponse:
    return RegistrationTokenMetadataResponse(
        id=token.id,
        status=_token_status(token),
        expires_at=token.expires_at,
        consumed_at=token.consumed_at,
        revoked_at=token.revoked_at,
        consumed_runner_id=token.consumed_runner_id,
        created_by=token.created_by,
        created_at=token.created_at,
    )


def create_registration_token(
    session: Session, user: CurrentUser, payload: RegistrationTokenCreateRequest
) -> RegistrationTokenCreateResponse:
    _require_admin(user)
    settings = get_settings()
    expires_in = payload.expires_in_seconds or settings.runner_registration_token_ttl_seconds
    plaintext_token = generate_registration_token()
    token = RunnerRegistrationToken(
        token_digest=digest_secret(plaintext_token),
        status=RegistrationTokenStatus.ACTIVE.value,
        expires_at=_utc_now() + timedelta(seconds=expires_in),
        created_by=user.id,
    )
    session.add(token)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Runner Registration Token 创建失败") from exc
    session.refresh(token)
    return RegistrationTokenCreateResponse(
        id=token.id,
        token=plaintext_token,
        status=RegistrationTokenStatus.ACTIVE,
        expires_at=token.expires_at,
    )


def list_registration_tokens(
    session: Session, user: CurrentUser
) -> RegistrationTokenListResponse:
    _require_admin(user)
    tokens = list(
        session.scalars(
            select(RunnerRegistrationToken).order_by(
                RunnerRegistrationToken.created_at.desc(), RunnerRegistrationToken.id.desc()
            )
        ).all()
    )
    return RegistrationTokenListResponse(
        items=[_token_metadata(token) for token in tokens], total=len(tokens)
    )


def revoke_registration_token(
    session: Session, user: CurrentUser, token_id: int
) -> RegistrationTokenMetadataResponse:
    _require_admin(user)
    token = session.get(RunnerRegistrationToken, token_id)
    if token is None:
        raise ResourceNotFoundError("Runner Registration Token 不存在")
    if token.status == RegistrationTokenStatus.CONSUMED.value:
        raise ResourceConflictError("已消费的 Runner Registration Token 不能撤销")
    if token.status == RegistrationTokenStatus.REVOKED.value:
        return _token_metadata(token)
    token.status = RegistrationTokenStatus.REVOKED.value
    token.revoked_at = _utc_now()
    session.commit()
    session.refresh(token)
    return _token_metadata(token)


def _snapshot_values(payload: RunnerRegistrationRequest | RunnerHeartbeatRequest) -> dict[str, Any]:
    return {
        "name": payload.name,
        "hostname": payload.hostname,
        "ip_address": payload.ip_address,
        "os": payload.os,
        "cpu": payload.cpu,
        "ram": payload.ram,
        "disk": payload.disk,
        "python_version": payload.python,
        "chrome_version": payload.chrome,
        "playwright_version": payload.playwright,
        "java_version": payload.java,
        "jmeter_version": payload.jmeter,
    }


def _replace_snapshot_children(
    session: Session, runner: Runner, payload: RunnerRegistrationRequest | RunnerHeartbeatRequest
) -> None:
    session.execute(delete(RunnerTag).where(RunnerTag.runner_id == runner.id))
    session.execute(delete(RunnerCapability).where(RunnerCapability.runner_id == runner.id))
    session.execute(delete(RunnerSlot).where(RunnerSlot.runner_id == runner.id))
    session.add_all([RunnerTag(runner_id=runner.id, tag=tag) for tag in payload.tags])
    session.add_all(
        [
            RunnerCapability(
                runner_id=runner.id,
                capability=capability.name.value,
                status=capability.status.value,
                reason=capability.reason,
            )
            for capability in payload.capabilities
        ]
    )
    session.add_all(
        [
            RunnerSlot(
                runner_id=runner.id,
                slot_type=slot.type.value,
                total=slot.total,
                available=slot.available,
            )
            for slot in payload.slots
        ]
    )


def _apply_snapshot(
    session: Session, runner: Runner, payload: RunnerRegistrationRequest | RunnerHeartbeatRequest
) -> None:
    for field, value in _snapshot_values(payload).items():
        setattr(runner, field, value)
    _replace_snapshot_children(session, runner, payload)


def register_runner(
    session: Session, payload: RunnerRegistrationRequest
) -> RunnerRegisterResponse:
    if not payload.registration_token or not 16 <= len(payload.registration_token) <= 512:
        raise AuthenticationError("Runner Registration Token 无效或已过期")

    token_digest = digest_secret(payload.registration_token)
    now = _utc_now()
    token = session.scalar(
        select(RunnerRegistrationToken)
        .where(RunnerRegistrationToken.token_digest == token_digest)
        .with_for_update()
    )
    if token is None:
        raise AuthenticationError("Runner Registration Token 无效或已过期")

    runner_id = uuid4().hex
    credential = generate_runner_credential()
    settings = get_settings()
    runner = Runner(
        id=runner_id,
        credential_digest=digest_secret(credential),
        status=RunnerStatus.ACTIVE.value,
        heartbeat_interval_seconds=settings.runner_heartbeat_interval_seconds,
        **_snapshot_values(payload),
    )
    session.add(runner)
    session.flush()

    consumed = session.execute(
        update(RunnerRegistrationToken)
        .where(
            RunnerRegistrationToken.id == token.id,
            RunnerRegistrationToken.status == RegistrationTokenStatus.ACTIVE.value,
            RunnerRegistrationToken.expires_at > now,
        )
        .values(
            status=RegistrationTokenStatus.CONSUMED.value,
            consumed_at=now,
            consumed_runner_id=runner_id,
        )
    )
    if consumed.rowcount != 1:
        session.rollback()
        raise AuthenticationError("Runner Registration Token 无效或已过期")

    _replace_snapshot_children(session, runner, payload)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Runner 注册失败") from exc
    return RunnerRegisterResponse(
        runner_id=runner_id,
        credential=credential,
        heartbeat_interval_seconds=runner.heartbeat_interval_seconds,
    )


def _get_runner(session: Session, runner_id: str, *, for_update: bool = False) -> Runner:
    statement = select(Runner).where(Runner.id == runner_id)
    if for_update:
        statement = statement.with_for_update()
    runner = session.scalar(statement)
    if runner is None:
        raise ResourceNotFoundError("Runner 不存在")
    return runner


def _online_state(
    runner: Runner, store: RedisRunnerHeartbeatStore
) -> tuple[OnlineStatus, bool, bool]:
    try:
        heartbeat = store.get_heartbeat(runner.id)
    except RedisStoreUnavailableError as exc:
        logger.warning(
            "runner_redis_read_unavailable",
            extra={
                "event": "RUNNER_REDIS_READ_UNAVAILABLE",
                "runner_id": runner.id,
                "error_type": type(exc).__name__,
            },
        )
        return OnlineStatus.UNKNOWN, False, False
    if runner.status != RunnerStatus.ACTIVE.value:
        return OnlineStatus.OFFLINE, False, True
    if heartbeat is None:
        return OnlineStatus.OFFLINE, False, True
    return OnlineStatus.ONLINE, True, True


def get_runner_online_state(
    runner: Runner, store: RedisRunnerHeartbeatStore
) -> tuple[OnlineStatus, bool, bool]:
    """Return the existing heartbeat-backed online state for cross-module coordination."""

    return _online_state(runner, store)


def _runner_response(runner: Runner, store: RedisRunnerHeartbeatStore) -> RunnerResponse:
    online_status, online, redis_available = get_runner_online_state(runner, store)
    return RunnerResponse(
        id=runner.id,
        name=runner.name,
        hostname=runner.hostname,
        ip_address=runner.ip_address,
        os=runner.os,
        cpu=runner.cpu,
        ram=runner.ram,
        disk=runner.disk,
        python=runner.python_version,
        chrome=runner.chrome_version,
        playwright=runner.playwright_version,
        java=runner.java_version,
        jmeter=runner.jmeter_version,
        status=RunnerStatus(runner.status),
        online=online,
        online_status=online_status,
        redis_available=redis_available,
        heartbeat_interval_seconds=runner.heartbeat_interval_seconds,
        last_heartbeat_at=runner.last_heartbeat_at,
        revoked_at=runner.revoked_at,
        created_at=runner.created_at,
        updated_at=runner.updated_at,
        tags=sorted(tag.tag for tag in runner.tags),
        capabilities=[
            RunnerCapabilityResponse(
                name=capability.capability,
                status=capability.status,
                reason=capability.reason,
            )
            for capability in sorted(runner.capabilities, key=lambda item: item.capability)
        ],
        slots=[
            RunnerSlotResponse(
                type=slot.slot_type,
                total=slot.total,
                available=slot.available,
            )
            for slot in sorted(runner.slots, key=lambda item: item.slot_type)
        ],
    )


def list_runners(
    session: Session, user: CurrentUser, store: RedisRunnerHeartbeatStore
) -> RunnerListResponse:
    _require_admin(user)
    runners = list(
        session.scalars(
            select(Runner).order_by(Runner.created_at.desc(), Runner.id.desc())
        ).all()
    )
    return RunnerListResponse(
        items=[_runner_response(runner, store) for runner in runners], total=len(runners)
    )


def get_runner(
    session: Session, user: CurrentUser, runner_id: str, store: RedisRunnerHeartbeatStore
) -> RunnerResponse:
    _require_admin(user)
    return _runner_response(_get_runner(session, runner_id), store)


def revoke_runner(
    session: Session, user: CurrentUser, runner_id: str, store: RedisRunnerHeartbeatStore
) -> RunnerResponse:
    _require_admin(user)
    runner = _get_runner(session, runner_id, for_update=True)
    if runner.status != RunnerStatus.REVOKED.value:
        runner.status = RunnerStatus.REVOKED.value
        runner.revoked_at = _utc_now()
        session.commit()
        session.refresh(runner)
    try:
        store.delete_heartbeat(runner.id)
    except RedisStoreUnavailableError as exc:
        logger.warning(
            "runner_redis_delete_unavailable",
            extra={
                "event": "RUNNER_REDIS_DELETE_UNAVAILABLE",
                "runner_id": runner.id,
                "error_type": type(exc).__name__,
            },
        )
    return _runner_response(runner, store)


def heartbeat_runner(
    session: Session,
    runner_id: str,
    credential: str,
    payload: RunnerHeartbeatRequest,
    store: RedisRunnerHeartbeatStore,
) -> RunnerHeartbeatResponse:
    runner = _get_runner(session, runner_id, for_update=True)
    if (
        not credential
        or len(credential) > 512
        or runner.status != RunnerStatus.ACTIVE.value
        or not matches_digest(credential, runner.credential_digest)
    ):
        raise AuthenticationError("Runner credential 无效")

    now = _utc_now()
    _apply_snapshot(session, runner, payload)
    runner.last_heartbeat_at = now
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Runner 心跳元数据保存失败") from exc

    settings = get_settings()
    try:
        store.set_heartbeat(
            runner.id,
            {
                "runner_id": runner.id,
                "status": OnlineStatus.ONLINE.value,
                "last_heartbeat_at": now.isoformat(),
            },
            ttl=settings.runner_heartbeat_ttl_seconds,
        )
    except RedisStoreUnavailableError as exc:
        logger.warning(
            "runner_heartbeat_redis_unavailable",
            extra={
                "event": "RUNNER_HEARTBEAT_REDIS_UNAVAILABLE",
                "runner_id": runner.id,
                "error_type": type(exc).__name__,
            },
        )
        raise RedisUnavailableError() from exc

    return RunnerHeartbeatResponse(
        runner_id=runner.id,
        status=RunnerStatus.ACTIVE,
        heartbeat_interval_seconds=runner.heartbeat_interval_seconds,
        server_time=_utc_aware(now),
    )
