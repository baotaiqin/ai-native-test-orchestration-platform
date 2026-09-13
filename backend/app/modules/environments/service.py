import json
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment, EnvironmentVariable
from app.modules.environments.schemas import (
    EnvironmentCreate,
    EnvironmentUpdate,
    VariableType,
    VariableUpsert,
)
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import (
    ensure_project_owner,
    ensure_project_visible,
    get_project,
)


def _commit(session: Session, conflict_message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(conflict_message) from exc


def _normalize_code(code: str) -> str:
    return code.strip().upper()


def _get_environment(session: Session, environment_id: int) -> Environment:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise ResourceNotFoundError("环境不存在")
    return environment


def _get_environment_with_access(
    session: Session, user: CurrentUser, environment_id: int, *, writable: bool = False
) -> Environment:
    environment = _get_environment(session, environment_id)
    project = session.get(Project, environment.project_id)
    if project is None:
        raise ResourceNotFoundError("项目不存在")
    if writable:
        ensure_project_owner(session, project, user)
        if project.status == ProjectStatus.ARCHIVED.value:
            raise ResourceConflictError("归档项目不能修改环境配置")
    else:
        ensure_project_visible(session, project, user)
    return environment


def list_environments(session: Session, user: CurrentUser, project_id: int) -> list[Environment]:
    get_project(session, user, project_id)
    statement = select(Environment).where(Environment.project_id == project_id).order_by(
        Environment.is_default.desc(), Environment.id.asc()
    )
    return list(session.scalars(statement).all())


def create_environment(
    session: Session, user: CurrentUser, payload: EnvironmentCreate
) -> Environment:
    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建环境")

    has_environment = session.scalar(
        select(Environment.id).where(Environment.project_id == payload.project_id).limit(1)
    )
    should_be_default = payload.is_default or has_environment is None
    if should_be_default:
        session.execute(
            update(Environment)
            .where(Environment.project_id == payload.project_id)
            .values(is_default=False)
        )
    environment = Environment(
        project_id=payload.project_id,
        name=payload.name.strip(),
        code=_normalize_code(payload.code),
        base_url=str(payload.base_url) if payload.base_url else None,
        description=payload.description.strip() if payload.description else None,
        is_default=should_be_default,
        enabled=True,
    )
    session.add(environment)
    _commit(session, "同一项目中的环境编码不能重复")
    session.refresh(environment)
    return environment


def update_environment(
    session: Session,
    user: CurrentUser,
    environment_id: int,
    payload: EnvironmentUpdate,
) -> Environment:
    environment = _get_environment_with_access(session, user, environment_id, writable=True)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        environment.name = changes["name"].strip()
    if "code" in changes and changes["code"] is not None:
        environment.code = _normalize_code(changes["code"])
    if "base_url" in changes:
        environment.base_url = str(changes["base_url"]) if changes["base_url"] else None
    if "description" in changes:
        description = changes["description"]
        environment.description = description.strip() if description else None
    if "enabled" in changes and changes["enabled"] is not None:
        if environment.is_default and not changes["enabled"]:
            raise ResourceConflictError("默认环境不能停用，请先切换默认环境")
        environment.enabled = changes["enabled"]
    _commit(session, "同一项目中的环境编码不能重复")
    session.refresh(environment)
    return environment


def set_default_environment(
    session: Session, user: CurrentUser, environment_id: int
) -> Environment:
    environment = _get_environment_with_access(session, user, environment_id, writable=True)
    if not environment.enabled:
        raise ResourceConflictError("停用环境不能设为默认环境")
    session.execute(
        update(Environment)
        .where(Environment.project_id == environment.project_id)
        .values(is_default=False)
    )
    environment.is_default = True
    _commit(session, "默认环境切换失败")
    session.refresh(environment)
    return environment


def list_variables(
    session: Session, user: CurrentUser, environment_id: int
) -> list[EnvironmentVariable]:
    _get_environment_with_access(session, user, environment_id)
    statement = (
        select(EnvironmentVariable)
        .where(EnvironmentVariable.environment_id == environment_id)
        .order_by(EnvironmentVariable.key.asc())
    )
    return list(session.scalars(statement).all())


def _canonicalize_value(value: str, value_type: VariableType) -> str:
    stripped = value.strip()
    if value_type == VariableType.NUMBER:
        try:
            Decimal(stripped)
        except InvalidOperation as exc:
            raise ResourceConflictError("NUMBER 类型变量必须是有效数字") from exc
    elif value_type == VariableType.BOOLEAN:
        normalized = stripped.lower()
        if normalized not in {"true", "false"}:
            raise ResourceConflictError("BOOLEAN 类型变量只能是 true 或 false")
        return normalized
    elif value_type in {VariableType.JSON, VariableType.LIST}:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ResourceConflictError(f"{value_type.value} 类型变量必须是有效 JSON") from exc
        if value_type == VariableType.LIST and not isinstance(parsed, list):
            raise ResourceConflictError("LIST 类型变量必须是 JSON 数组")
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return value


def upsert_variable(
    session: Session,
    user: CurrentUser,
    environment_id: int,
    key: str,
    payload: VariableUpsert,
) -> EnvironmentVariable:
    _get_environment_with_access(session, user, environment_id, writable=True)
    normalized_key = key.strip()
    value = _canonicalize_value(payload.value, payload.value_type)
    variable = session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.environment_id == environment_id,
            EnvironmentVariable.key == normalized_key,
        )
    )
    if variable is None:
        variable = EnvironmentVariable(environment_id=environment_id, key=normalized_key)
        session.add(variable)
    variable.value = value
    variable.value_type = payload.value_type.value
    variable.enabled = payload.enabled
    _commit(session, "环境变量保存失败")
    session.refresh(variable)
    return variable


def delete_variable(session: Session, user: CurrentUser, environment_id: int, key: str) -> None:
    _get_environment_with_access(session, user, environment_id, writable=True)
    variable = session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.environment_id == environment_id,
            EnvironmentVariable.key == key,
        )
    )
    if variable is None:
        raise ResourceNotFoundError("环境变量不存在")
    session.delete(variable)
    session.commit()
