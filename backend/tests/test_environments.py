from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.environments.models import Environment, EnvironmentVariable
from app.modules.projects.models import Project, ProjectMember
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def environment_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        Environment.__table__,
        EnvironmentVariable.__table__,
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


def _headers_and_project(client: TestClient) -> tuple[dict[str, str], int]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "环境测试项目", "code": "ENV_TEST"},
    )
    return headers, project.json()["id"]


def test_environment_lifecycle_and_default_switch(environment_client: TestClient) -> None:
    headers, project_id = _headers_and_project(environment_client)
    dev = environment_client.post(
        "/api/v1/environments",
        headers=headers,
        json={
            "project_id": project_id,
            "name": "开发环境",
            "code": "dev",
            "base_url": "http://127.0.0.1:8000",
        },
    )
    assert dev.status_code == 201
    assert dev.json()["is_default"] is True
    assert dev.json()["code"] == "DEV"

    test_environment = environment_client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "测试环境", "code": "test"},
    )
    assert test_environment.status_code == 201
    assert test_environment.json()["is_default"] is False

    switched = environment_client.post(
        f"/api/v1/environments/{test_environment.json()['id']}/default", headers=headers
    )
    assert switched.status_code == 200
    assert switched.json()["is_default"] is True

    items = environment_client.get(
        f"/api/v1/environments?project_id={project_id}", headers=headers
    ).json()["items"]
    assert len(items) == 2
    assert items[0]["code"] == "TEST"
    assert sum(item["is_default"] for item in items) == 1

    duplicate = environment_client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "重复环境", "code": "DEV"},
    )
    assert duplicate.status_code == 409


def test_environment_variables_validate_types(environment_client: TestClient) -> None:
    headers, project_id = _headers_and_project(environment_client)
    environment_id = environment_client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "开发环境", "code": "DEV"},
    ).json()["id"]

    json_variable = environment_client.put(
        f"/api/v1/environments/{environment_id}/variables/config",
        headers=headers,
        json={"value": '{"retry": 2}', "value_type": "JSON"},
    )
    assert json_variable.status_code == 200
    assert json_variable.json()["value"] == '{"retry":2}'

    boolean_variable = environment_client.put(
        f"/api/v1/environments/{environment_id}/variables/feature_enabled",
        headers=headers,
        json={"value": "TRUE", "value_type": "BOOLEAN"},
    )
    assert boolean_variable.status_code == 200
    assert boolean_variable.json()["value"] == "true"

    invalid_list = environment_client.put(
        f"/api/v1/environments/{environment_id}/variables/users",
        headers=headers,
        json={"value": '{"name": "admin"}', "value_type": "LIST"},
    )
    assert invalid_list.status_code == 409

    variables = environment_client.get(
        f"/api/v1/environments/{environment_id}/variables", headers=headers
    ).json()
    assert variables["total"] == 2

    deleted = environment_client.delete(
        f"/api/v1/environments/{environment_id}/variables/config", headers=headers
    )
    assert deleted.status_code == 204


def test_default_environment_cannot_be_disabled(environment_client: TestClient) -> None:
    headers, project_id = _headers_and_project(environment_client)
    environment_id = environment_client.post(
        "/api/v1/environments",
        headers=headers,
        json={"project_id": project_id, "name": "开发环境", "code": "DEV"},
    ).json()["id"]
    response = environment_client.patch(
        f"/api/v1/environments/{environment_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert response.status_code == 409
