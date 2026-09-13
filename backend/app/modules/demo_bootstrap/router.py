from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.infrastructure.object_store.client import ObjectStore, get_object_store

from .schemas import (
    DemoBootstrapRequest,
    DemoBootstrapResponse,
    DemoBootstrapStatus,
    DemoResetPreview,
    DemoResetRequest,
    DemoResetResponse,
)
from .service import bootstrap_demo, get_demo_reset_preview, get_demo_status, reset_demo

router = APIRouter()
ObjectStoreDependency = Annotated[ObjectStore, Depends(get_object_store)]


@router.get("/status", response_model=DemoBootstrapStatus, summary="查看内置 AI Demo 状态")
def get_demo_status_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DemoBootstrapStatus:
    return get_demo_status(session, current_user)


@router.post("/bootstrap", response_model=DemoBootstrapResponse, summary="初始化内置 AI Demo")
def bootstrap_demo_route(
    payload: DemoBootstrapRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DemoBootstrapResponse:
    return bootstrap_demo(session, current_user, payload)


@router.get(
    "/reset-preview",
    response_model=DemoResetPreview,
    summary="预览内置 AI Demo 重置影响",
)
def get_demo_reset_preview_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DemoResetPreview:
    return get_demo_reset_preview(session, current_user)


@router.post("/reset", response_model=DemoResetResponse, summary="重置内置 AI Demo 数据")
def reset_demo_route(
    payload: DemoResetRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    object_store: ObjectStoreDependency,
) -> DemoResetResponse:
    return reset_demo(session, current_user, payload, object_store)
