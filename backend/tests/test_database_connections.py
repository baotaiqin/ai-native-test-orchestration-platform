from collections.abc import Generator
from typing import Any

import pymysql
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.database_connections.models import DatabaseConnection
from app.modules.environments.models import Environment
from app.modules.projects.models import Project, ProjectMember
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def database_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        Environment.__table__,
        Secret.__table__,
        DatabaseConnection.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override_db_session() -> Generator[Session, None, None]:
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _create_connection(client: TestClient) -> tuple[dict[str, str], int, str]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project_id = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "数据库测试项目", "code": "DATABASE_TEST"},
    ).json()["id"]
    environment_id = client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "开发环境", "code": "DEV"},
    ).json()["id"]
    password = "mysql-password-测试"
    secret_id = client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": environment_id,
            "name": "MYSQL_PASSWORD",
            "secret_type": "DB_PASSWORD",
            "value": password,
        },
    ).json()["id"]
    response = client.post(
        "/api/v1/database-connections",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": environment_id,
            "name": "本地 MySQL",
            "host": "127.0.0.1",
            "port": 3306,
            "database_name": "ai_test_platform",
            "username": "test_platform",
            "password_secret_id": secret_id,
            "ssl_enabled": False,
        },
    )
    assert response.status_code == 201
    assert response.json()["password_masked"] == "••••••••"
    assert password not in response.text
    return headers, response.json()["id"], password


class _FakeCursor:
    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        assert sql == "SELECT DATABASE(), VERSION()"

    def fetchone(self) -> tuple[str, str]:
        return "ai_test_platform", "8.4.0"


class _FakeConnection:
    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def close(self) -> None:
        return None


def test_mysql_connection_uses_resolved_secret_and_masks_response(
    database_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, connection_id, password = _create_connection(database_client)
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection()

    monkeypatch.setattr(
        "app.modules.database_connections.service.pymysql.connect", fake_connect
    )
    response = database_client.post(
        f"/api/v1/database-connections/{connection_id}/test", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ai_test_platform"
    assert captured["password"] == password

    listed = database_client.get(
        "/api/v1/database-connections?project_id=1", headers=headers
    )
    assert listed.status_code == 200
    assert password not in listed.text
    assert "encrypted_value" not in listed.text


def test_mysql_connection_failure_is_sanitized(
    database_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, connection_id, password = _create_connection(database_client)

    def fail_connect(**_: Any) -> _FakeConnection:
        raise pymysql.OperationalError(1045, f"Access denied using password {password}")

    monkeypatch.setattr(
        "app.modules.database_connections.service.pymysql.connect", fail_connect
    )
    response = database_client.post(
        f"/api/v1/database-connections/{connection_id}/test", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert password not in response.text
