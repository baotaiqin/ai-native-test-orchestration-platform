from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.modules.auth.models import AuthAdminGuard, AuthSession, User

_TEST_ADMIN_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$Cb/HG9+DItvp8QjJ5+wwJA$"
    "kWc3aG6tQ3ufG9qOgF/5MQvjFXHXZjC/CgBOs5x15XQ"
)


def seed_test_user(
    session: Session,
    *,
    user_id: str,
    username: str,
    display_name: str,
    platform_role: str = "USER",
    status: str = "ACTIVE",
) -> User:
    user = User(
        id=user_id,
        username=username,
        display_name=display_name,
        password_hash=_TEST_ADMIN_HASH,
        status=status,
        platform_role=platform_role,
        auth_version=1,
    )
    session.add(user)
    return user


def install_test_auth(engine: Engine) -> None:
    User.__table__.create(engine)
    AuthSession.__table__.create(engine)
    AuthAdminGuard.__table__.create(engine)
    with Session(engine) as session:
        session.add(AuthAdminGuard(id=1))
        seed_test_user(
            session,
            user_id="dev-admin",
            username="admin",
            display_name="开发管理员",
            platform_role="ADMIN",
        )
        session.commit()


def uninstall_test_auth(engine: Engine) -> None:
    AuthSession.__table__.drop(engine)
    User.__table__.drop(engine)
    AuthAdminGuard.__table__.drop(engine)
