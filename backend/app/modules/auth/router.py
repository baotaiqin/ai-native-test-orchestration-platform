from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    AuthenticatedPrincipalDependency,
    CurrentUserDependency,
    DatabaseSessionDependency,
)
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CurrentUser,
    LoginRequest,
    LogoutResponse,
    TokenResponse,
    UserCreate,
    UserListQuery,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.modules.auth.service import (
    change_password,
    create_user,
    list_users,
    login,
    logout,
    update_user,
)

router = APIRouter()


@router.post("/login", response_model=TokenResponse, summary="登录")
def login_route(request: LoginRequest, session: DatabaseSessionDependency) -> TokenResponse:
    return login(session, request)


@router.get("/me", response_model=CurrentUser, summary="获取当前用户")
def me(current_user: CurrentUserDependency) -> CurrentUser:
    return current_user


@router.post("/logout", response_model=LogoutResponse, summary="退出登录")
def logout_route(
    session: DatabaseSessionDependency,
    principal: AuthenticatedPrincipalDependency,
) -> LogoutResponse:
    logout(session, principal)
    return LogoutResponse()


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    summary="修改本人密码",
)
def change_password_route(
    payload: ChangePasswordRequest,
    session: DatabaseSessionDependency,
    principal: AuthenticatedPrincipalDependency,
) -> ChangePasswordResponse:
    change_password(session, principal, payload)
    return ChangePasswordResponse()


@router.get("/users", response_model=UserListResponse, summary="用户列表")
def list_users_route(
    filters: Annotated[UserListQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> UserListResponse:
    return list_users(session, current_user, filters)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
)
def create_user_route(
    payload: UserCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> UserResponse:
    return UserResponse.model_validate(create_user(session, current_user, payload))


@router.patch("/users/{user_id}", response_model=UserResponse, summary="更新用户")
def update_user_route(
    user_id: str,
    payload: UserUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> UserResponse:
    return UserResponse.model_validate(
        update_user(session, current_user, user_id, payload)
    )
