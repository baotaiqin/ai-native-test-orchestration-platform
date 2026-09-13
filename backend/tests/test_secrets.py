from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.environments.models import Environment
from app.modules.projects.models import Project, ProjectMember
from app.modules.secrets.models import Secret
from app.modules.secrets.service import resolve_secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def secret_context() -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (Project.__table__, ProjectMember.__table__, Environment.__table__, Secret.__table__)
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
            yield client, testing_session
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _create_scope(client: TestClient) -> tuple[dict[str, str], int, int]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project_id = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Secret 测试项目", "code": "SECRET_TEST"},
    ).json()["id"]
    environment_id = client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "开发环境", "code": "DEV"},
    ).json()["id"]
    return headers, project_id, environment_id


def test_secret_is_encrypted_masked_and_rotatable(
    secret_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = secret_context
    headers, project_id, environment_id = _create_scope(client)
    original_value = "database-password-测试"
    response = client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": environment_id,
            "name": "MYSQL_PASSWORD",
            "secret_type": "DB_PASSWORD",
            "value": original_value,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["masked_value"] == "••••••••"
    assert "value" not in body
    assert "encrypted_value" not in body
    assert "fingerprint" not in body

    with session_factory() as session:
        stored = session.scalar(select(Secret).where(Secret.id == body["id"]))
        assert stored is not None
        assert stored.encrypted_value.startswith("dpapi:v1:")
        assert original_value not in stored.encrypted_value
        assert resolve_secret(session, stored.id, project_id) == original_value

    rotated_value = "rotated-password-2026"
    rotate = client.post(
        f"/api/v1/secrets/{body['id']}/rotate",
        headers=headers,
        json={"value": rotated_value},
    )
    assert rotate.status_code == 200
    assert rotate.json()["rotated_at"] is not None
    with session_factory() as session:
        assert resolve_secret(session, body["id"], project_id) == rotated_value


def test_secret_name_is_unique_and_list_never_reveals_value(
    secret_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = secret_context
    headers, project_id, environment_id = _create_scope(client)
    payload = {
        "project_id": project_id,
        "environment_id": environment_id,
        "name": "ACCESS_TOKEN",
        "secret_type": "TOKEN",
        "value": "first-token",
    }
    assert client.post("/api/v1/secrets", headers=headers, json=payload).status_code == 201
    duplicate = client.post(
        "/api/v1/secrets", headers=headers, json={**payload, "value": "second-token"}
    )
    assert duplicate.status_code == 409

    listed = client.get(f"/api/v1/secrets?project_id={project_id}", headers=headers)
    assert listed.status_code == 200
    serialized = listed.text
    assert "first-token" not in serialized
    assert "second-token" not in serialized
    assert "encrypted_value" not in serialized
    assert listed.json()["items"][0]["masked_value"] == "••••••••"
