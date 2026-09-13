from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.projects.models import Project, ProjectBusinessCounter, ProjectMember
from app.modules.requirements.models import (
    Requirement,
    RequirementDocumentVersion,
    RequirementVersion,
)
from app.modules.requirements.parser import parse_markdown_tree
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def requirement_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        ProjectBusinessCounter.__table__,
        Requirement.__table__,
        RequirementVersion.__table__,
        RequirementDocumentVersion.__table__,
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


def _scope(client: TestClient) -> tuple[dict[str, str], int]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "需求管理项目", "code": "REQUIREMENT_TEST"},
    )
    return headers, project.json()["id"]


def test_manual_requirement_version_and_diff(requirement_client: TestClient) -> None:
    headers, project_id = _scope(requirement_client)
    created = requirement_client.post(
        "/api/v1/requirements",
        headers=headers,
        json={
            "project_id": project_id,
            "title": "用户登录",
            "type": "FEATURE",
            "markdown_content": "# 用户登录\n\n支持账号密码登录。",
        },
    )
    assert created.status_code == 201
    body = created.json()
    requirement_id = body["id"]
    version_one_id = body["current_version"]["id"]
    assert body["code"].startswith("REQ-")
    assert body["current_version"]["version_no"] == 1

    updated = requirement_client.post(
        f"/api/v1/requirements/{requirement_id}/versions",
        headers=headers,
        json={
            "markdown_content": "# 用户登录\n\n支持账号密码登录。\n\n连续失败 5 次后锁定。",
            "change_summary": "增加账号锁定规则",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["current_version"]["version_no"] == 2

    versions = requirement_client.get(
        f"/api/v1/requirements/{requirement_id}/versions", headers=headers
    )
    assert [item["version_no"] for item in versions.json()] == [2, 1]

    diff = requirement_client.get(
        f"/api/v1/requirements/{requirement_id}/diff?from_version=1&to_version=2",
        headers=headers,
    )
    assert diff.status_code == 200
    assert diff.json()["additions"] == 2
    assert "锁定" in diff.json()["unified_diff"]

    switched = requirement_client.post(
        f"/api/v1/requirements/{requirement_id}/versions/{version_one_id}/current",
        headers=headers,
    )
    assert switched.status_code == 200
    assert switched.json()["current_version"]["version_no"] == 1

    unchanged = requirement_client.post(
        f"/api/v1/requirements/{requirement_id}/versions",
        headers=headers,
        json={"markdown_content": "# 用户登录\n\n支持账号密码登录。"},
    )
    assert unchanged.status_code == 409


def test_markdown_preview_and_confirm_import(requirement_client: TestClient) -> None:
    headers, project_id = _scope(requirement_client)
    markdown = """# 订单系统

订单系统需求。

## 创建订单

用户可以创建订单。

### 验收标准

- 返回订单编号

## 查询订单

用户可以查询订单。
"""
    payload = {"project_id": project_id, "filename": "orders.md", "content": markdown}
    preview = requirement_client.post("/api/v1/requirements/import/preview", json=payload)
    assert preview.status_code == 200
    assert preview.json()["total"] == 4
    nodes = preview.json()["nodes"]
    assert nodes[1]["parent_temp_id"] == nodes[0]["temp_id"]
    assert nodes[2]["parent_temp_id"] == nodes[1]["temp_id"]
    assert nodes[3]["parent_temp_id"] == nodes[0]["temp_id"]

    imported = requirement_client.post(
        "/api/v1/requirements/import", headers=headers, json=payload
    )
    assert imported.status_code == 201
    assert imported.json()["created_count"] == 4
    tree = imported.json()["root_items"]
    assert len(tree) == 1
    assert tree[0]["title"] == "订单系统"
    assert [child["title"] for child in tree[0]["children"]] == ["创建订单", "查询订单"]
    assert tree[0]["children"][0]["children"][0]["title"] == "验收标准"
    assert tree[0]["current_version"]["source_type"] == "MARKDOWN"
    assert imported.json()["document_version"]["version_no"] == 1
    assert tree[0]["outline_number"] == "1"
    assert tree[0]["children"][1]["outline_number"] == "1.2"

    versions = requirement_client.get(
        f"/api/v1/requirements/projects/{project_id}/document-versions",
        headers=headers,
    )
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert len(versions.json()[0]["snapshot"]) == 4


def test_publish_complete_document_reorders_adds_and_archives_nodes(
    requirement_client: TestClient,
) -> None:
    headers, project_id = _scope(requirement_client)
    markdown = """# 商城需求

## 登录

支持用户登录。

## 查询商品

支持查询商品。

## 旧需求

此节点将在新版移除。
"""
    imported = requirement_client.post(
        "/api/v1/requirements/import",
        headers=headers,
        json={"project_id": project_id, "filename": "mall.md", "content": markdown},
    )
    root = imported.json()["root_items"][0]
    login, query, removed_node = root["children"]
    published = requirement_client.post(
        f"/api/v1/requirements/projects/{project_id}/document-versions",
        headers=headers,
        json={
            "change_summary": "增加订单并调整目录",
            "nodes": [
                {
                    "client_id": f"existing-{root['id']}",
                    "requirement_id": root["id"],
                    "parent_client_id": None,
                    "title": root["title"],
                    "type": root["type"],
                    "markdown_content": root["current_version"]["markdown_content"],
                    "order_index": 0,
                },
                {
                    "client_id": f"existing-{login['id']}",
                    "requirement_id": login["id"],
                    "parent_client_id": f"existing-{root['id']}",
                    "title": login["title"],
                    "type": login["type"],
                    "markdown_content": "支持账号密码登录并限制失败次数。",
                    "order_index": 0,
                },
                {
                    "client_id": "new-order",
                    "parent_client_id": f"existing-{root['id']}",
                    "title": "创建订单",
                    "type": "FEATURE",
                    "markdown_content": "支持创建订单。",
                    "order_index": 1,
                },
                {
                    "client_id": f"existing-{query['id']}",
                    "requirement_id": query["id"],
                    "parent_client_id": f"existing-{root['id']}",
                    "title": query["title"],
                    "type": query["type"],
                    "markdown_content": query["current_version"]["markdown_content"],
                    "order_index": 2,
                },
            ],
        },
    )
    assert published.status_code == 201
    body = published.json()
    assert body["document_version"]["version_no"] == 2
    assert [child["outline_number"] for child in body["root_items"][0]["children"]] == [
        "1.1",
        "1.2",
        "1.3",
    ]
    assert body["root_items"][0]["children"][1]["code"].startswith("REQ-")
    archived = requirement_client.get(
        f"/api/v1/requirements/{removed_node['id']}", headers=headers
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    versions = requirement_client.get(
        f"/api/v1/requirements/projects/{project_id}/document-versions",
        headers=headers,
    ).json()
    assert [version["version_no"] for version in versions] == [2, 1]
    assert len(versions[1]["snapshot"]) == 4


def test_headingless_markdown_uses_filename_as_title() -> None:
    nodes = parse_markdown_tree("只有一段需求说明。", "simple-requirement.md")
    assert len(nodes) == 1
    assert nodes[0].title == "simple-requirement"
    assert nodes[0].markdown_content == "只有一段需求说明。"
