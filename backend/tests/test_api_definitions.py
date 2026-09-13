from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.api_definitions.models import ApiDefinition, ApiDefinitionImport
from app.modules.api_definitions.parser import parse_openapi
from app.modules.projects.models import Project, ProjectMember
from tests.auth_helpers import install_test_auth, uninstall_test_auth

SPEC = """
openapi: 3.0.3
info:
  title: Pet Service
  version: 1.0.0
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
  schemas:
    Pet:
      type: object
      required: [name]
      properties:
        name: {type: string}
security:
  - bearerAuth: []
paths:
  /pets/{petId}:
    get:
      operationId: getPet
      summary: 查询宠物
      tags: [pet]
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      responses:
        '200':
          description: success
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Pet'
"""


@pytest.fixture
def api_definition_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        ApiDefinitionImport.__table__,
        ApiDefinition.__table__,
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
        json={"name": "API 导入项目", "code": "API_IMPORT_TEST"},
    )
    return headers, project.json()["id"]


def test_parser_expands_schema_and_auth() -> None:
    metadata, operations = parse_openapi(SPEC)
    assert metadata["spec_version"] == "3.0.3"
    assert operations[0].response_schema["200"]["schema"]["required"] == ["name"]
    assert operations[0].auth_info["schemes"]["bearerAuth"]["scheme"] == "bearer"
    assert operations[0].parameters[0]["in"] == "path"


def test_preview_import_and_diff(api_definition_client: TestClient) -> None:
    headers, project_id = _scope(api_definition_client)
    payload = {"project_id": project_id, "filename": "pet.yaml", "content": SPEC}

    preview = api_definition_client.post(
        "/api/v1/api-definitions/import/preview", headers=headers, json=payload
    )
    assert preview.status_code == 200
    assert preview.json()["added_count"] == 1

    imported = api_definition_client.post(
        "/api/v1/api-definitions/import", headers=headers, json=payload
    )
    assert imported.status_code == 201
    assert imported.json()["version_no"] == 1
    assert imported.json()["created_count"] == 1

    unchanged = api_definition_client.post(
        "/api/v1/api-definitions/import/preview", headers=headers, json=payload
    )
    assert unchanged.json()["unchanged_count"] == 1

    changed_spec = SPEC.replace("查询宠物", "查询宠物详情").replace(
        "paths:\n",
        "paths:\n  /health:\n    get:\n      responses:\n"
        "        '200': {description: ok}\n",
    )
    changed_payload = {**payload, "content": changed_spec}
    changed = api_definition_client.post(
        "/api/v1/api-definitions/import/preview", headers=headers, json=changed_payload
    )
    assert changed.status_code == 200
    assert changed.json()["changed_count"] == 1
    assert changed.json()["added_count"] == 1

    second_import = api_definition_client.post(
        "/api/v1/api-definitions/import", headers=headers, json=changed_payload
    )
    assert second_import.json()["version_no"] == 2
    assert second_import.json()["updated_count"] == 1

    definitions = api_definition_client.get(
        f"/api/v1/api-definitions?project_id={project_id}", headers=headers
    )
    assert definitions.status_code == 200
    assert definitions.json()["total"] == 2


def test_removed_and_invalid_document(api_definition_client: TestClient) -> None:
    headers, project_id = _scope(api_definition_client)
    payload = {"project_id": project_id, "filename": "pet.yaml", "content": SPEC}
    api_definition_client.post(
        "/api/v1/api-definitions/import", headers=headers, json=payload
    )
    only_health = """
openapi: 3.0.0
info: {title: Health, version: 1.0.0}
paths:
  /health:
    get:
      responses:
        '200': {description: ok}
"""
    removed = api_definition_client.post(
        "/api/v1/api-definitions/import/preview",
        headers=headers,
        json={**payload, "content": only_health},
    )
    assert removed.json()["removed_count"] == 1
    assert removed.json()["removed"][0]["diff_status"] == "REMOVED"

    invalid = api_definition_client.post(
        "/api/v1/api-definitions/import/preview",
        headers=headers,
        json={**payload, "content": "name: not-openapi"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_DOCUMENT"
