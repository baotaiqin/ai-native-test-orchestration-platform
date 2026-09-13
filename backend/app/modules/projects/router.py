from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.projects.schemas import (
    ProjectCreate,
    ProjectListResponse,
    ProjectMemberCreate,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    ProjectMemberUpdate,
    ProjectResponse,
    ProjectUpdate,
)
from app.modules.projects.service import (
    add_project_member,
    archive_project,
    create_project,
    get_project,
    list_project_members,
    list_projects,
    remove_project_member,
    restore_project,
    update_project,
    update_project_member,
)

router = APIRouter()


@router.get("", response_model=ProjectListResponse, summary="项目列表")
def list_projects_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    include_archived: bool = Query(default=False),
) -> ProjectListResponse:
    items = list_projects(session, current_user, include_archived=include_archived)
    return ProjectListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建项目",
)
def create_project_route(
    payload: ProjectCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectResponse:
    return ProjectResponse.model_validate(create_project(session, current_user, payload))


@router.get("/{project_id}", response_model=ProjectResponse, summary="项目详情")
def get_project_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectResponse:
    return ProjectResponse.model_validate(get_project(session, current_user, project_id))


@router.patch("/{project_id}", response_model=ProjectResponse, summary="编辑项目")
def update_project_route(
    project_id: int,
    payload: ProjectUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectResponse:
    return ProjectResponse.model_validate(
        update_project(session, current_user, project_id, payload)
    )


@router.post("/{project_id}/archive", response_model=ProjectResponse, summary="归档项目")
def archive_project_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectResponse:
    return ProjectResponse.model_validate(archive_project(session, current_user, project_id))


@router.post("/{project_id}/restore", response_model=ProjectResponse, summary="恢复项目")
def restore_project_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectResponse:
    return ProjectResponse.model_validate(restore_project(session, current_user, project_id))


@router.get(
    "/{project_id}/members", response_model=ProjectMemberListResponse, summary="项目成员列表"
)
def list_project_members_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectMemberListResponse:
    members = list_project_members(session, current_user, project_id)
    responses = [ProjectMemberResponse.model_validate(member) for member in members]
    return ProjectMemberListResponse(items=responses, total=len(responses))


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="添加项目成员",
)
def add_project_member_route(
    project_id: int,
    payload: ProjectMemberCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectMemberResponse:
    member = add_project_member(session, current_user, project_id, payload)
    return ProjectMemberResponse.model_validate(member)


@router.patch(
    "/{project_id}/members/{member_user_id}",
    response_model=ProjectMemberResponse,
    summary="调整项目成员角色",
)
def update_project_member_route(
    project_id: int,
    member_user_id: str,
    payload: ProjectMemberUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectMemberResponse:
    member = update_project_member(
        session, current_user, project_id, member_user_id, payload
    )
    return ProjectMemberResponse.model_validate(member)


@router.delete(
    "/{project_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除项目成员",
)
def remove_project_member_route(
    project_id: int,
    member_user_id: str,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    remove_project_member(session, current_user, project_id, member_user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
