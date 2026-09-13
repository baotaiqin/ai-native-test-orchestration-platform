import time

import pymysql
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.database_connections.schemas import (
    ConnectionTestResponse,
    DatabaseConnectionCreate,
    DatabaseConnectionUpdate,
)
from app.modules.environments.models import Environment
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import (
    ensure_project_owner,
    ensure_project_visible,
    get_project,
)
from app.modules.secrets.models import Secret
from app.modules.secrets.schemas import SecretType
from app.modules.secrets.service import resolve_secret


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("同一环境中的数据库连接名称不能重复") from exc


def _validate_scope_and_secret(
    session: Session,
    project_id: int,
    environment_id: int,
    password_secret_id: int,
) -> None:
    environment = session.get(Environment, environment_id)
    if environment is None or environment.project_id != project_id:
        raise ResourceConflictError("数据库连接所属环境与项目不匹配")
    secret = session.get(Secret, password_secret_id)
    if secret is None or secret.project_id != project_id:
        raise ResourceConflictError("数据库密码 Secret 与项目不匹配")
    if secret.environment_id not in {None, environment_id}:
        raise ResourceConflictError("数据库密码 Secret 与环境不匹配")
    if secret.secret_type not in {SecretType.DB_PASSWORD.value, SecretType.PASSWORD.value}:
        raise ResourceConflictError("数据库连接必须引用 PASSWORD 或 DB_PASSWORD 类型 Secret")


def _get_connection(session: Session, connection_id: int) -> DatabaseConnection:
    connection = session.get(DatabaseConnection, connection_id)
    if connection is None:
        raise ResourceNotFoundError("数据库连接不存在")
    return connection


def _get_connection_with_access(
    session: Session, user: CurrentUser, connection_id: int, *, writable: bool = False
) -> DatabaseConnection:
    connection = _get_connection(session, connection_id)
    project = session.get(Project, connection.project_id)
    if project is None:
        raise ResourceNotFoundError("项目不存在")
    if writable:
        ensure_project_owner(session, project, user)
        if project.status == ProjectStatus.ARCHIVED.value:
            raise ResourceConflictError("归档项目不能修改或测试数据库连接")
    else:
        ensure_project_visible(session, project, user)
    return connection


def list_database_connections(
    session: Session, user: CurrentUser, project_id: int, environment_id: int | None = None
) -> list[DatabaseConnection]:
    get_project(session, user, project_id)
    statement = select(DatabaseConnection).where(DatabaseConnection.project_id == project_id)
    if environment_id is not None:
        statement = statement.where(DatabaseConnection.environment_id == environment_id)
    return list(session.scalars(statement.order_by(DatabaseConnection.name.asc())).all())


def create_database_connection(
    session: Session, user: CurrentUser, payload: DatabaseConnectionCreate
) -> DatabaseConnection:
    project = get_project(session, user, payload.project_id)
    ensure_project_owner(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建数据库连接")
    _validate_scope_and_secret(
        session, payload.project_id, payload.environment_id, payload.password_secret_id
    )
    connection = DatabaseConnection(
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        name=payload.name.strip(),
        host=payload.host.strip(),
        port=payload.port,
        database_name=payload.database_name.strip(),
        username=payload.username.strip(),
        password_secret_id=payload.password_secret_id,
        ssl_enabled=payload.ssl_enabled,
        enabled=True,
    )
    session.add(connection)
    _commit(session)
    session.refresh(connection)
    return connection


def update_database_connection(
    session: Session,
    user: CurrentUser,
    connection_id: int,
    payload: DatabaseConnectionUpdate,
) -> DatabaseConnection:
    connection = _get_connection_with_access(session, user, connection_id, writable=True)
    changes = payload.model_dump(exclude_unset=True)
    password_secret_id = changes.get("password_secret_id", connection.password_secret_id)
    _validate_scope_and_secret(
        session, connection.project_id, connection.environment_id, password_secret_id
    )
    for field in (
        "name",
        "host",
        "port",
        "database_name",
        "username",
        "password_secret_id",
        "ssl_enabled",
        "enabled",
    ):
        if field in changes and changes[field] is not None:
            value = changes[field]
            setattr(connection, field, value.strip() if isinstance(value, str) else value)
    _commit(session)
    session.refresh(connection)
    return connection


def test_database_connection(
    session: Session, user: CurrentUser, connection_id: int
) -> ConnectionTestResponse:
    config = _get_connection_with_access(session, user, connection_id, writable=True)
    if not config.enabled:
        raise ResourceConflictError("数据库连接已停用")
    password = resolve_secret(session, config.password_secret_id, config.project_id)
    started_at = time.perf_counter()
    try:
        connection = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=password,
            database=config.database_name,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            ssl={} if config.ssl_enabled else None,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(), VERSION()")
                database, server_version = cursor.fetchone()
        finally:
            connection.close()
    except pymysql.MySQLError:
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        return ConnectionTestResponse(
            status="failed",
            latency_ms=latency_ms,
            message="连接失败，请检查主机、端口、数据库、用户名、Secret 和 MySQL 权限",
        )
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return ConnectionTestResponse(
        status="ok",
        latency_ms=latency_ms,
        database=str(database),
        server_version=str(server_version),
        message="MySQL 连接成功",
    )
