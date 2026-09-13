from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AuthorizationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.core.time import utc_now_naive
from app.modules.auth.models import User
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.schemas import UserStatus as AuthUserStatus
from app.modules.projects.models import Project, ProjectMember
from app.modules.projects.schemas import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberUpdate,
    ProjectRole,
    ProjectStatus,
    ProjectUpdate,
)


def is_admin(user: CurrentUser) -> bool:
    return "ADMIN" in user.roles


def _normalize_code(code: str) -> str:
    return code.strip().upper()


def _get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ResourceNotFoundError("项目不存在")
    return project


def _require_active_user(session: Session, user_id: str) -> None:
    target = session.get(User, user_id)
    if target is None or target.status != AuthUserStatus.ACTIVE.value:
        raise ResourceConflictError("目标用户不存在或已停用")


def ensure_project_visible(session: Session, project: Project, user: CurrentUser) -> None:
    if is_admin(user):
        return
    membership = session.get(ProjectMember, (project.id, user.id))
    if membership is None:
        raise ResourceNotFoundError("项目不存在")


def ensure_project_writable(session: Session, project: Project, user: CurrentUser) -> None:
    if is_admin(user):
        return
    membership = session.get(ProjectMember, (project.id, user.id))
    if membership is None or membership.role not in {
        ProjectRole.PROJECT_OWNER.value,
        ProjectRole.TESTER.value,
    }:
        raise AuthorizationError("只有管理员、项目负责人或测试人员可以修改测试资产")


def ensure_project_owner(session: Session, project: Project, user: CurrentUser) -> None:
    if is_admin(user):
        return
    membership = session.get(ProjectMember, (project.id, user.id))
    if membership is None or membership.role != ProjectRole.PROJECT_OWNER.value:
        raise AuthorizationError("只有管理员或项目负责人可以修改项目配置")


def _attach_current_user_role(
    session: Session, project: Project, user: CurrentUser
) -> Project:
    membership = (
        None if is_admin(user) else session.get(ProjectMember, (project.id, user.id))
    )
    project.current_user_role = membership.role if membership is not None else None
    return project


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("项目编码已存在") from exc


def list_projects(
    session: Session, user: CurrentUser, *, include_archived: bool = False
) -> list[Project]:
    statement = select(Project)
    if not is_admin(user):
        statement = statement.join(ProjectMember).where(ProjectMember.user_id == user.id)
    if not include_archived:
        statement = statement.where(Project.status == ProjectStatus.ACTIVE.value)
    statement = statement.order_by(Project.updated_at.desc(), Project.id.desc())
    return [
        _attach_current_user_role(session, project, user)
        for project in session.scalars(statement).all()
    ]


def create_project(session: Session, user: CurrentUser, payload: ProjectCreate) -> Project:
    if not is_admin(user):
        raise AuthorizationError("只有管理员可以创建项目")

    code = _normalize_code(payload.code)
    existing_id = session.scalar(select(Project.id).where(func.upper(Project.code) == code))
    if existing_id is not None:
        raise ResourceConflictError("项目编码已存在")

    project = Project(
        name=payload.name.strip(),
        code=code,
        description=payload.description.strip() if payload.description else None,
        status=ProjectStatus.ACTIVE.value,
        owner_id=user.id,
    )
    project.members.append(
        ProjectMember(user_id=user.id, role=ProjectRole.PROJECT_OWNER.value)
    )
    session.add(project)
    _commit(session)
    session.refresh(project)
    return _attach_current_user_role(session, project, user)


def get_project(session: Session, user: CurrentUser, project_id: int) -> Project:
    project = _get_project(session, project_id)
    ensure_project_visible(session, project, user)
    return _attach_current_user_role(session, project, user)


def update_project(
    session: Session, user: CurrentUser, project_id: int, payload: ProjectUpdate
) -> Project:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目请先恢复后再编辑")

    changes = payload.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] is not None:
        code = _normalize_code(changes["code"])
        existing_id = session.scalar(
            select(Project.id).where(func.upper(Project.code) == code, Project.id != project.id)
        )
        if existing_id is not None:
            raise ResourceConflictError("项目编码已存在")
        project.code = code
    if "name" in changes and changes["name"] is not None:
        project.name = changes["name"].strip()
    if "description" in changes:
        description = changes["description"]
        project.description = description.strip() if description else None

    _commit(session)
    session.refresh(project)
    return _attach_current_user_role(session, project, user)


def archive_project(session: Session, user: CurrentUser, project_id: int) -> Project:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    if project.status != ProjectStatus.ARCHIVED.value:
        project.status = ProjectStatus.ARCHIVED.value
        project.archived_at = utc_now_naive()
        project.archived_at_basis = "UTC"
        _commit(session)
        session.refresh(project)
    return _attach_current_user_role(session, project, user)


def restore_project(session: Session, user: CurrentUser, project_id: int) -> Project:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    if project.status != ProjectStatus.ACTIVE.value:
        project.status = ProjectStatus.ACTIVE.value
        project.archived_at = None
        project.archived_at_basis = None
        _commit(session)
        session.refresh(project)
    return _attach_current_user_role(session, project, user)


def list_project_members(
    session: Session, user: CurrentUser, project_id: int
) -> list[ProjectMember]:
    project = _get_project(session, project_id)
    ensure_project_visible(session, project, user)
    statement = (
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at.asc(), ProjectMember.user_id.asc())
    )
    return list(session.scalars(statement).all())


def add_project_member(
    session: Session,
    user: CurrentUser,
    project_id: int,
    payload: ProjectMemberCreate,
) -> ProjectMember:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能修改成员")
    _require_active_user(session, payload.user_id)
    if session.get(ProjectMember, (project_id, payload.user_id)) is not None:
        raise ResourceConflictError("该用户已经是项目成员")
    member = ProjectMember(project_id=project_id, user_id=payload.user_id, role=payload.role.value)
    session.add(member)
    _commit(session)
    session.refresh(member)
    return member


def update_project_member(
    session: Session,
    user: CurrentUser,
    project_id: int,
    member_user_id: str,
    payload: ProjectMemberUpdate,
) -> ProjectMember:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    member = session.get(ProjectMember, (project_id, member_user_id))
    if member is None:
        raise ResourceNotFoundError("项目成员不存在")
    _require_active_user(session, member_user_id)
    if member.user_id == project.owner_id and payload.role != ProjectRole.PROJECT_OWNER:
        raise ResourceConflictError("项目创建者必须保留 PROJECT_OWNER 角色")
    member.role = payload.role.value
    session.commit()
    session.refresh(member)
    return member


def remove_project_member(
    session: Session, user: CurrentUser, project_id: int, member_user_id: str
) -> None:
    project = _get_project(session, project_id)
    ensure_project_owner(session, project, user)
    member = session.get(ProjectMember, (project_id, member_user_id))
    if member is None:
        raise ResourceNotFoundError("项目成员不存在")
    if member.user_id == project.owner_id:
        raise ResourceConflictError("不能移除项目创建者")
    session.delete(member)
    session.commit()
