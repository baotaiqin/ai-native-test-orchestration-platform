from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.projects.models import Project, ProjectMember
from tests.auth_helpers import install_test_auth, seed_test_user, uninstall_test_auth


@pytest.fixture
def project_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_test_auth(engine)
    Project.__table__.create(engine)
    ProjectMember.__table__.create(engine)
    with testing_session() as session:
        seed_test_user(
            session,
            user_id="tester-01",
            username="tester-01",
            display_name="测试成员",
        )
        seed_test_user(
            session,
            user_id="viewer-01",
            username="viewer-01",
            display_name="只读用户",
        )
        seed_test_user(
            session,
            user_id="disabled-01",
            username="disabled-01",
            display_name="停用用户",
            status="DISABLED",
        )
        session.commit()

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
        ProjectMember.__table__.drop(engine)
        Project.__table__.drop(engine)
        uninstall_test_auth(engine)
        engine.dispose()


def _auth_headers(client: TestClient, username: str = "admin") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_project_lifecycle(project_client: TestClient) -> None:
    headers = _auth_headers(project_client)
    create_response = project_client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "订单自动化",
            "code": "order-api",
            "description": "订单 API 与 Web 自动化测试",
        },
    )
    assert create_response.status_code == 201
    project = create_response.json()
    project_id = project["id"]
    assert project["code"] == "ORDER-API"
    assert project["status"] == "ACTIVE"
    assert project["owner_id"] == "dev-admin"
    assert project["current_user_role"] is None

    list_response = project_client.get("/api/v1/projects", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1

    update_response = project_client.patch(
        f"/api/v1/projects/{project_id}",
        headers=headers,
        json={"name": "订单测试平台"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "订单测试平台"

    archive_response = project_client.post(
        f"/api/v1/projects/{project_id}/archive", headers=headers
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "ARCHIVED"
    assert archive_response.json()["archived_at_basis"] == "UTC"
    archived_at = datetime.fromisoformat(
        archive_response.json()["archived_at"].replace("Z", "+00:00")
    )
    assert archived_at.tzinfo is not None and archived_at.utcoffset() is not None
    assert project_client.get("/api/v1/projects", headers=headers).json()["total"] == 0
    assert (
        project_client.get(
            "/api/v1/projects?include_archived=true", headers=headers
        ).json()["total"]
        == 1
    )

    blocked_update = project_client.patch(
        f"/api/v1/projects/{project_id}", headers=headers, json={"name": "不可修改"}
    )
    assert blocked_update.status_code == 409

    restore_response = project_client.post(
        f"/api/v1/projects/{project_id}/restore", headers=headers
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["status"] == "ACTIVE"
    assert restore_response.json()["archived_at"] is None
    assert restore_response.json()["archived_at_basis"] is None


def test_project_code_is_unique(project_client: TestClient) -> None:
    headers = _auth_headers(project_client)
    payload = {"name": "接口平台", "code": "api-platform"}
    assert project_client.post("/api/v1/projects", headers=headers, json=payload).status_code == 201
    response = project_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "重复项目", "code": "API-PLATFORM"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RESOURCE_CONFLICT"


def test_non_admin_cannot_create_or_read_unrelated_project(
    project_client: TestClient,
) -> None:
    headers = _auth_headers(project_client)
    created = project_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "权限测试", "code": "permission"},
    ).json()

    viewer_headers = _auth_headers(project_client, "viewer-01")
    create_response = project_client.post(
        "/api/v1/projects",
        headers=viewer_headers,
        json={"name": "无权创建", "code": "forbidden"},
    )
    assert create_response.status_code == 403
    assert create_response.json()["code"] == "PERMISSION_DENIED"

    detail_response = project_client.get(
        f"/api/v1/projects/{created['id']}", headers=viewer_headers
    )
    assert detail_response.status_code == 404


def test_project_member_roles_and_owner_protection(project_client: TestClient) -> None:
    headers = _auth_headers(project_client)
    project_id = project_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "成员权限项目", "code": "MEMBER_ROLES"},
    ).json()["id"]

    initial = project_client.get(f"/api/v1/projects/{project_id}/members", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["total"] == 1
    assert initial.json()["items"][0]["role"] == "PROJECT_OWNER"

    added = project_client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": "tester-01", "role": "TESTER"},
    )
    assert added.status_code == 201
    assert added.json()["role"] == "TESTER"

    tester_headers = _auth_headers(project_client, "tester-01")
    tester_detail = project_client.get(
        f"/api/v1/projects/{project_id}", headers=tester_headers
    )
    assert tester_detail.status_code == 200
    assert tester_detail.json()["current_user_role"] == "TESTER"
    tester_project_update = project_client.patch(
        f"/api/v1/projects/{project_id}",
        headers=tester_headers,
        json={"name": "测试人员不能修改项目配置"},
    )
    assert tester_project_update.status_code == 403
    assert tester_project_update.json()["code"] == "PERMISSION_DENIED"
    tester_member_add = project_client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=tester_headers,
        json={"user_id": "viewer-01", "role": "VIEWER"},
    )
    assert tester_member_add.status_code == 403

    updated = project_client.patch(
        f"/api/v1/projects/{project_id}/members/tester-01",
        headers=headers,
        json={"role": "VIEWER"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "VIEWER"

    removed = project_client.delete(
        f"/api/v1/projects/{project_id}/members/tester-01", headers=headers
    )
    assert removed.status_code == 204

    owner_remove = project_client.delete(
        f"/api/v1/projects/{project_id}/members/dev-admin", headers=headers
    )
    assert owner_remove.status_code == 409

    missing = project_client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": "missing-user", "role": "VIEWER"},
    )
    assert missing.status_code == 409

    disabled = project_client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": "disabled-01", "role": "VIEWER"},
    )
    assert disabled.status_code == 409
