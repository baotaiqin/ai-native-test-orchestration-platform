import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ResourceConflictError,
    ResourceNotFoundError,
    SecurityConfigurationError,
)
from app.core.time import utc_now_aware, utc_now_naive
from app.modules.auth.models import AuthAdminGuard, AuthSession, User
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    PlatformRole,
    TokenResponse,
    UserCreate,
    UserListQuery,
    UserListResponse,
    UserResponse,
    UserStatus,
    UserUpdate,
    normalize_username,
)

_JWT_ALGORITHM = "HS256"
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_INVALID_LOGIN_MESSAGE = "用户名或密码错误"
_INVALID_TOKEN_MESSAGE = "登录凭证无效或已失效"
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$5otAe5r7LjAjUh4Ys/OifA$"
    "CWuAE0tuEW/IyOtVjjrbOmNow7iA79q9CJ6uZwewLVg"
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user: CurrentUser
    session_digest: str


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    user_id: str
    username: str


def hash_password(password: str) -> str:
    if not 8 <= len(password) <= 256:
        raise ValueError("密码长度必须在 8 到 256 个字符之间")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=[user.platform_role],
    )


def _session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("ascii")).hexdigest()


def _encode_token(user: User, session_id: str, issued_at: int, expires_at: int) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user.id,
            "sid": session_id,
            "av": user.auth_version,
            "iat": issued_at,
            "exp": expires_at,
            "iss": settings.auth_token_issuer,
            "aud": settings.auth_token_audience,
        },
        settings.secret_key,
        algorithm=_JWT_ALGORITHM,
        headers={"typ": "JWT"},
    )


def _decode_token(token: str) -> dict[str, object]:
    if not isinstance(token, str) or not 1 <= len(token) <= 4096:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[_JWT_ALGORITHM],
            issuer=settings.auth_token_issuer,
            audience=settings.auth_token_audience,
            options={
                "require": ["sub", "sid", "av", "iat", "exp", "iss", "aud"],
                "verify_iat": True,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE) from exc
    subject = payload.get("sub")
    session_id = payload.get("sid")
    auth_version = payload.get("av")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    issuer = payload.get("iss")
    audience = payload.get("aud")
    if (
        not isinstance(subject, str)
        or not 1 <= len(subject) <= 64
        or not isinstance(session_id, str)
        or _SESSION_ID.fullmatch(session_id) is None
        or type(auth_version) is not int
        or auth_version < 1
        or type(issued_at) is not int
        or type(expires_at) is not int
        or issued_at < 1
        or expires_at <= issued_at
        or not isinstance(issuer, str)
        or issuer != settings.auth_token_issuer
        or not isinstance(audience, str)
        or audience != settings.auth_token_audience
    ):
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    return payload


def login(session: Session, request: LoginRequest) -> TokenResponse:
    user = session.scalar(
        select(User)
        .where(User.username == request.username)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    password = request.password.get_secret_value()
    candidate_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(candidate_hash, password)
    if user is None or not password_valid or user.status != UserStatus.ACTIVE.value:
        raise AuthenticationError(_INVALID_LOGIN_MESSAGE)

    if _PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    # MySQL DATETIME without fractional precision rounds microseconds, while JWT
    # NumericDate conversion truncates them. Persist and sign the same whole second.
    issued_aware = utc_now_aware().replace(microsecond=0)
    expires_aware = issued_aware + timedelta(seconds=expires_in)
    session_id = secrets.token_urlsafe(32)
    auth_session = AuthSession(
        session_digest=_session_digest(session_id),
        user_id=user.id,
        auth_version=user.auth_version,
        issued_at=issued_aware.replace(tzinfo=None),
        expires_at=expires_aware.replace(tzinfo=None),
    )
    session.add(auth_session)
    token = _encode_token(
        user,
        session_id,
        int(issued_aware.timestamp()),
        int(expires_aware.timestamp()),
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AuthenticationError("登录暂不可用，请重试") from exc
    return TokenResponse(access_token=token, expires_in=expires_in, user=_current_user(user))


def authenticate_access_token(session: Session, token: str) -> AuthenticatedPrincipal:
    payload = _decode_token(token)
    subject = str(payload["sub"])
    digest = _session_digest(str(payload["sid"]))
    row = session.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.session_digest == digest)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    auth_session, user = row
    now = utc_now_naive()
    token_version = int(payload["av"])
    if (
        auth_session.user_id != subject
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
        or user.status != UserStatus.ACTIVE.value
        or auth_session.auth_version != token_version
        or user.auth_version != token_version
        or int(auth_session.issued_at.replace(tzinfo=UTC).timestamp())
        != int(payload["iat"])
        or int(auth_session.expires_at.replace(tzinfo=UTC).timestamp())
        != int(payload["exp"])
    ):
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    return AuthenticatedPrincipal(user=_current_user(user), session_digest=digest)


def logout(session: Session, principal: AuthenticatedPrincipal) -> None:
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.session_digest == principal.session_digest)
        .with_for_update()
    )
    if auth_session is None or auth_session.revoked_at is not None:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    auth_session.revoked_at = utc_now_naive()
    session.commit()


def change_password(
    session: Session, principal: AuthenticatedPrincipal, payload: ChangePasswordRequest
) -> None:
    user = session.scalar(select(User).where(User.id == principal.user.id).with_for_update())
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise AuthenticationError(_INVALID_TOKEN_MESSAGE)
    if not verify_password(user.password_hash, payload.current_password.get_secret_value()):
        raise AuthenticationError("当前密码错误")
    user.password_hash = hash_password(payload.new_password.get_secret_value())
    user.auth_version += 1
    _revoke_all_sessions(session, user.id, utc_now_naive())
    session.commit()


def _require_admin(user: CurrentUser) -> None:
    if PlatformRole.ADMIN.value not in user.roles:
        raise AuthorizationError("只有平台管理员可以管理用户")


def _lock_admin_guard(session: Session) -> None:
    guard = session.scalar(
        select(AuthAdminGuard).where(AuthAdminGuard.id == 1).with_for_update()
    )
    if guard is None:
        raise SecurityConfigurationError("认证管理员串行锁未初始化")


def _revoke_all_sessions(session: Session, user_id: str, revoked_at: datetime) -> None:
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )


def list_users(
    session: Session, actor: CurrentUser, query: UserListQuery
) -> UserListResponse:
    _require_admin(actor)
    total = int(session.scalar(select(func.count(User.id))) or 0)
    users = list(
        session.scalars(
            select(User)
            .order_by(User.created_at.asc(), User.id.asc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in users],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


def create_user(session: Session, actor: CurrentUser, payload: UserCreate) -> User:
    _require_admin(actor)
    _lock_admin_guard(session)
    user = User(
        id=f"usr_{secrets.token_hex(16)}",
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password.get_secret_value()),
        status=payload.status.value,
        platform_role=payload.platform_role.value,
        auth_version=1,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("用户名已存在") from exc
    session.refresh(user)
    return user


def update_user(
    session: Session, actor: CurrentUser, user_id: str, payload: UserUpdate
) -> User:
    _require_admin(actor)
    security_change = bool({"platform_role", "status"} & payload.model_fields_set)
    if security_change:
        _lock_admin_guard(session)
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        session.rollback()
        raise ResourceNotFoundError("用户不存在")

    next_role = payload.platform_role.value if payload.platform_role else user.platform_role
    next_status = payload.status.value if payload.status else user.status
    was_active_admin = (
        user.platform_role == PlatformRole.ADMIN.value
        and user.status == UserStatus.ACTIVE.value
    )
    remains_active_admin = (
        next_role == PlatformRole.ADMIN.value and next_status == UserStatus.ACTIVE.value
    )
    if was_active_admin and not remains_active_admin:
        active_admin_ids = list(
            session.scalars(
                select(User.id)
                .where(
                    User.platform_role == PlatformRole.ADMIN.value,
                    User.status == UserStatus.ACTIVE.value,
                )
                .with_for_update()
            ).all()
        )
        if len(active_admin_ids) <= 1:
            session.rollback()
            raise ResourceConflictError("系统必须保留至少一个可用平台管理员")

    changed_security = user.platform_role != next_role or user.status != next_status
    if payload.display_name is not None:
        user.display_name = payload.display_name
    user.platform_role = next_role
    user.status = next_status
    if changed_security:
        user.auth_version += 1
        _revoke_all_sessions(session, user.id, utc_now_naive())
    session.commit()
    session.refresh(user)
    return user


def bootstrap_development_admin(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: str = "开发管理员",
) -> BootstrapResult:
    normalized_username = normalize_username(username)
    if not 1 <= len(display_name.strip()) <= 128:
        raise ValueError("显示名称长度必须在 1 到 128 个字符之间")
    _lock_admin_guard(session)
    existing = list(
        session.scalars(
            select(User)
            .where(or_(User.id == "dev-admin", User.username == normalized_username))
            .with_for_update()
        ).all()
    )
    if existing:
        if (
            len(existing) == 1
            and existing[0].id == "dev-admin"
            and existing[0].username == normalized_username
        ):
            session.rollback()
            return BootstrapResult("ALREADY_EXISTS", "dev-admin", normalized_username)
        session.rollback()
        raise ResourceConflictError("开发管理员 ID 或用户名已被其他用户占用")
    user = User(
        id="dev-admin",
        username=normalized_username,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE.value,
        platform_role=PlatformRole.ADMIN.value,
        auth_version=1,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("开发管理员初始化发生并发冲突") from exc
    return BootstrapResult("CREATED", user.id, user.username)
