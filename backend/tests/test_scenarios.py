from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.projects.models import Project, ProjectBusinessCounter, ProjectMember
from app.modules.scenarios.models import Scenario, ScenarioPreviewProfile, ScenarioVersion
from app.modules.scenarios.schemas import ScenarioDsl
from app.modules.scenarios.service import _default_preview_profile
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def scenario_client() -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        ProjectBusinessCounter.__table__,
        Environment.__table__,
        Secret.__table__,
        Scenario.__table__,
        ScenarioVersion.__table__,
        ScenarioPreviewProfile.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override_db_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

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


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _valid_dsl() -> dict[str, object]:
    return {
        "version": "1.0",
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "request",
                "type": "HTTP",
                "name": "请求",
                "config": {"url": "https://example.test/health", "method": "GET"},
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
    }


def _create_scenario(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Scenario 批准项目", "code": "SCENARIO_APPROVAL"},
    )
    project.raise_for_status()
    response = client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "project_id": project.json()["id"],
            "name": "登录场景",
            "dsl": _valid_dsl(),
        },
    )
    response.raise_for_status()
    return response.json()


def test_scenario_approval_is_idempotent_and_new_version_requires_reapproval(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]
    assert created["status"] == "DRAFT"

    approved = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    repeated = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "APPROVED"

    version = client.post(
        f"/api/v1/scenarios/{scenario_id}/versions",
        headers=headers,
        json={"dsl": _valid_dsl(), "change_note": "新增版本"},
    )
    assert version.status_code == 201
    detail = client.get(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "DRAFT"

    approved_again = client.post(
        f"/api/v1/scenarios/{scenario_id}/approve", headers=headers
    )
    assert approved_again.status_code == 200
    assert approved_again.json()["status"] == "APPROVED"


def test_scenario_approval_rechecks_current_version_and_scope_permissions(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, testing_session = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]
    project_id = created["project_id"]

    with testing_session() as session:
        scenario = session.get(Scenario, scenario_id)
        assert scenario is not None
        version = session.get(ScenarioVersion, scenario.current_version_id)
        assert version is not None
        version.dsl = {**_valid_dsl(), "nodes": []}
        session.commit()

    invalid = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "RESOURCE_CONFLICT"

    with testing_session() as session:
        scenario = session.get(Scenario, scenario_id)
        assert scenario is not None
        scenario.current_version_id = None
        session.commit()

    missing_version = client.post(
        f"/api/v1/scenarios/{scenario_id}/approve", headers=headers
    )
    assert missing_version.status_code == 409

    with testing_session() as session:
        scenario = session.get(Scenario, scenario_id)
        assert scenario is not None
        scenario.current_version_id = created["current_version"]["id"]
        scenario.status = "REVIEW"
        session.commit()

    illegal_status = client.post(
        f"/api/v1/scenarios/{scenario_id}/approve", headers=headers
    )
    assert illegal_status.status_code == 409

    with testing_session() as session:
        session.add(
            ProjectMember(
                project_id=project_id,
                user_id="scenario-tester",
                role="TESTER",
            )
        )
        scenario = session.get(Scenario, scenario_id)
        assert scenario is not None
        scenario.status = "DRAFT"
        session.commit()

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="scenario-tester",
        username="tester",
        display_name="测试用户",
        roles=["TESTER"],
    )
    tester_created = client.post(
        "/api/v1/scenarios",
        json={
            "project_id": project_id,
            "name": "测试人员可创建场景",
            "dsl": _valid_dsl(),
        },
    )
    assert tester_created.status_code == 201, tester_created.text
    forbidden = client.post(f"/api/v1/scenarios/{scenario_id}/approve")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"
    app.dependency_overrides.pop(get_current_user, None)


def test_archived_scenario_cannot_be_approved(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]

    archived = client.post(f"/api/v1/scenarios/{scenario_id}/archive", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    approved = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert approved.status_code == 409
    assert approved.json()["code"] == "RESOURCE_CONFLICT"


def test_unused_draft_scenario_can_be_deleted(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]

    deleted = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""

    missing = client.get(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert missing.status_code == 404


def test_approved_or_archived_scenario_cannot_be_deleted(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]

    approved = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert approved.status_code == 200
    rejected = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "RESOURCE_CONFLICT"

    archived = client.post(f"/api/v1/scenarios/{scenario_id}/archive", headers=headers)
    assert archived.status_code == 200
    rejected_again = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert rejected_again.status_code == 409


def test_draft_scenario_with_run_history_cannot_be_deleted(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, testing_session = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]

    # This focused fixture only needs the two columns queried by the deletion
    # guard; production keeps the complete immutable runs table.
    with testing_session() as session:
        session.execute(
            text("CREATE TABLE runs (id VARCHAR(128) PRIMARY KEY, scenario_id INTEGER)")
        )
        session.execute(
            text("INSERT INTO runs (id, scenario_id) VALUES ('run-1', :scenario_id)"),
            {"scenario_id": scenario_id},
        )
        session.commit()

    try:
        rejected = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=headers)
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "RESOURCE_CONFLICT"
        assert "运行记录" in rejected.json()["message"]
        assert client.get(
            f"/api/v1/scenarios/{scenario_id}", headers=headers
        ).status_code == 200
    finally:
        with testing_session() as session:
            session.execute(text("DROP TABLE runs"))
            session.commit()


def test_preview_profile_defaults_can_be_overwritten_for_current_version(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    created = _create_scenario(client, headers)
    scenario_id = created["id"]
    version_id = created["current_version"]["id"]

    generated = client.get(
        f"/api/v1/scenarios/{scenario_id}/preview-profile", headers=headers
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["source"] == "GENERATED"
    assert generated.json()["responses_by_node"]["request"]["status_code"] == 200

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/preview-profile",
        headers=headers,
        json={
            "scenario_version_id": version_id,
            "context": {"base_url": "https://saved.example.test"},
            "responses_by_node": {
                "request": {
                    "status_code": 204,
                    "json_body": None,
                    "headers": {},
                    "cookies": {},
                    "response_time_ms": 3,
                }
            },
            "cleanup_outcome": "SUCCESS",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "SAVED"
    assert saved.json()["responses_by_node"]["request"]["status_code"] == 204

    loaded = client.get(
        f"/api/v1/scenarios/{scenario_id}/preview-profile", headers=headers
    )
    assert loaded.status_code == 200
    assert loaded.json()["context"]["base_url"] == "https://saved.example.test"


def test_default_preview_profile_uses_openapi_and_downstream_dependencies() -> None:
    dsl = ScenarioDsl.model_validate(
        {
            "nodes": [
                {"id": "start", "type": "START", "name": "开始"},
                {
                    "id": "login",
                    "type": "HTTP",
                    "name": "登录",
                    "config": {"method": "POST", "url": "{{base_url}}/api/login"},
                },
                {
                    "id": "token",
                    "type": "EXTRACT",
                    "name": "提取 Token",
                    "config": {
                        "name": "token",
                        "source": "JSONPATH",
                        "expression": "$.data.token",
                    },
                },
                {
                    "id": "status",
                    "type": "ASSERT_STATUS",
                    "name": "校验登录状态",
                    "config": {"expected": 201},
                },
                {"id": "end", "type": "END", "name": "结束"},
            ],
            "settings": {"initial_variables": ["base_url"]},
        }
    )
    source = {
        "definitions": [
            {
                "method": "POST",
                "path": "/api/login",
                "response_schema": {
                    "200": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "data": {
                                    "type": "object",
                                    "properties": {"token": {"type": "string"}},
                                }
                            },
                        }
                    }
                },
            }
        ]
    }

    context, responses = _default_preview_profile(dsl, source)

    assert context["base_url"] == "https://api.example.test"
    assert responses["login"].status_code == 201
    assert responses["login"].json_body["data"]["token"]


def _login_credential_dsl(password: str) -> dict[str, object]:
    return {
        "version": "1.0",
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "login",
                "type": "HTTP",
                "name": "登录系统",
                "config": {
                    "method": "POST",
                    "url": "{{base_url}}/api/login",
                    "body": {
                        "type": "JSON",
                        "content": {"username": "admin", "password": password},
                    },
                },
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
        "settings": {"initial_variables": ["base_url"]},
    }


def test_scenario_plain_password_is_rejected_with_exact_field_path(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "凭据校验项目", "code": "SCENARIO_CREDENTIAL"},
    )
    project.raise_for_status()

    validation = client.post(
        "/api/v1/scenarios/validate",
        headers=headers,
        json={
            "project_id": project.json()["id"],
            "name": "登录场景",
            "dsl": _login_credential_dsl("password"),
        },
    )

    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is False
    issue = validation.json()["issues"][0]
    assert issue["code"] == "SCENARIO_HTTP_CREDENTIAL_INVALID"
    assert issue["node_id"] == "login"
    assert "config.body.content.password" in issue["message"]


def test_unbound_secret_can_be_saved_as_draft_but_must_bind_before_approval(
    scenario_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = scenario_client
    headers = _auth_headers(client)
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Secret 绑定项目", "code": "SCENARIO_SECRET_BINDING"},
    )
    project.raise_for_status()
    project_id = project.json()["id"]
    dsl = _login_credential_dsl("{{secret.login-password}}")

    validation = client.post(
        "/api/v1/scenarios/validate",
        headers=headers,
        json={"project_id": project_id, "name": "登录场景", "dsl": dsl},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True
    assert validation.json()["issues"][0]["code"] == "SCENARIO_SECRET_UNBOUND"

    created = client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={"project_id": project_id, "name": "登录场景", "dsl": dsl},
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]
    blocked = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert blocked.status_code == 409, blocked.text
    assert "login-password" in blocked.json()["message"]

    secret = client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": None,
            "name": "login-password",
            "secret_type": "PASSWORD",
            "value": "synthetic-password",
        },
    )
    assert secret.status_code == 201, secret.text
    approved = client.post(f"/api/v1/scenarios/{scenario_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
