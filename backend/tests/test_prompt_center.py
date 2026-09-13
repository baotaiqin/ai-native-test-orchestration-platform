from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.prompt_center.models import OutputSchema, PromptDefinition, PromptVersion
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def prompt_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (OutputSchema.__table__, PromptDefinition.__table__, PromptVersion.__table__)
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            client.testing_session_factory = testing_session
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _payload() -> dict:
    return {
        "name": "需求评审 Prompt",
        "code": "REQUIREMENT_REVIEW_DEFAULT",
        "task_type": "REQUIREMENT_REVIEW",
        "description": "生成需求风险清单",
        "system_prompt": "你是 {{ role }}。",
        "user_template": "评审以下需求：{{ requirement }}，项目：{{ project }}",
    }


def test_public_prompt_updates_current_content_without_new_version(
    prompt_client: TestClient,
) -> None:
    headers = _headers(prompt_client)
    created = prompt_client.post(
        "/api/v1/prompt-center", headers=headers, json=_payload()
    )
    assert created.status_code == 201
    prompt_id = created.json()["id"]
    version_one = created.json()["current_version"]

    rendered = prompt_client.post(
        f"/api/v1/prompt-center/{prompt_id}/render", headers=headers,
        json={"variables": {"role": "测试专家", "requirement": "登录"}},
    )
    assert rendered.status_code == 200
    assert rendered.json()["system_prompt"] == "你是 测试专家。"
    assert rendered.json()["missing_variables"] == ["project"]

    updated = prompt_client.patch(
        f"/api/v1/prompt-center/system-templates/{prompt_id}", headers=headers,
        json={
            "system_prompt": "你是高级测试专家。",
            "user_template": "评审：{{ requirement }}",
            "output_schema_id": None,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"]["id"] == version_one["id"]
    assert updated.json()["current_version"]["version_no"] == 1
    assert updated.json()["current_version"]["system_prompt"] == "你是高级测试专家。"

    versions = prompt_client.get(
        f"/api/v1/prompt-center/{prompt_id}/versions", headers=headers
    )
    assert [item["version_no"] for item in versions.json()] == [1]

    rejected_version = prompt_client.post(
        f"/api/v1/prompt-center/{prompt_id}/versions", headers=headers,
        json={
            "system_prompt": "不应创建的版本",
            "user_template": "{{ requirement }}",
        },
    )
    assert rejected_version.status_code == 409


def test_prompt_copy_disable_and_duplicate_version(prompt_client: TestClient) -> None:
    headers = _headers(prompt_client)
    created = prompt_client.post(
        "/api/v1/prompt-center", headers=headers, json=_payload()
    ).json()
    copied = prompt_client.post(
        f"/api/v1/prompt-center/{created['id']}/copy", headers=headers,
        json={"name": "需求评审副本", "code": "REQUIREMENT_REVIEW_COPY"},
    )
    assert copied.status_code == 201
    assert copied.json()["current_version"]["system_prompt"] == "你是 {{ role }}。"

    disabled = prompt_client.patch(
        f"/api/v1/prompt-center/{created['id']}", headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    listed = prompt_client.get("/api/v1/prompt-center", headers=headers)
    custom_codes = {
        item["code"] for item in listed.json()["items"] if not item["is_builtin"]
    }
    assert custom_codes == {"REQUIREMENT_REVIEW_COPY"}

    current = created["current_version"]
    unchanged = prompt_client.post(
        f"/api/v1/prompt-center/{created['id']}/versions", headers=headers,
        json={
            "system_prompt": current["system_prompt"],
            "user_template": current["user_template"],
        },
    )
    assert unchanged.status_code == 409


def test_builtin_prompt_has_one_public_version_and_can_restore_factory(
    prompt_client: TestClient,
) -> None:
    headers = _headers(prompt_client)
    listed = prompt_client.get("/api/v1/prompt-center", headers=headers).json()
    builtin = next(
        item for item in listed["items"] if item["code"] == "DEMO_REQUIREMENT_REVIEW"
    )
    original = builtin["current_version"]

    updated = prompt_client.patch(
        f"/api/v1/prompt-center/system-templates/{builtin['id']}",
        headers=headers,
        json={
            "system_prompt": "管理员维护后的公共模板",
            "user_template": original["user_template"],
            "output_schema_id": original["output_schema_id"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"]["version_no"] == original["version_no"]
    assert updated.json()["current_version"]["system_prompt"] == "管理员维护后的公共模板"

    restored = prompt_client.post(
        f"/api/v1/prompt-center/system-templates/{builtin['id']}/restore",
        headers=headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["current_version"]["version_no"] == original["version_no"]
    assert restored.json()["current_version"]["system_prompt"] == original["system_prompt"]


def test_legacy_builtin_initial_version_is_synchronized(
    prompt_client: TestClient,
) -> None:
    headers = _headers(prompt_client)
    listed = prompt_client.get("/api/v1/prompt-center", headers=headers).json()
    builtin = next(
        item for item in listed["items"] if item["code"] == "DEMO_API_TEST_DESIGN"
    )
    with prompt_client.testing_session_factory() as session:
        version = session.get(PromptVersion, builtin["current_version"]["id"])
        assert version is not None
        version.system_prompt = "旧版初始化提示词"
        version.user_template = "旧版 {{ requirement_scope }}"
        version.change_note = "初始版本"
        version.created_by = "dev-admin"
        session.commit()

    synchronized = prompt_client.get(
        "/api/v1/prompt-center", headers=headers
    ).json()
    current = next(
        item
        for item in synchronized["items"]
        if item["code"] == "DEMO_API_TEST_DESIGN"
    )["current_version"]
    assert "每个推荐 API 都必须填写非空 check_point_keys" in current["system_prompt"]
    assert current["change_note"] == "同步系统默认提示词"
    assert current["created_by"] == "system"
