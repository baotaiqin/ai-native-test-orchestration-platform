from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationError
from app.infrastructure.db.session import SessionLocal
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.service import AuthenticatedPrincipal, authenticate_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


DatabaseSessionDependency = Annotated[Session, Depends(get_db_session)]


async def get_authenticated_principal(
    session: DatabaseSessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("请先登录")
    return authenticate_access_token(session, credentials.credentials)


AuthenticatedPrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_authenticated_principal)
]


async def get_current_user(
    principal: AuthenticatedPrincipalDependency,
) -> CurrentUser:
    return principal.user


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]
