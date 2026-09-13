from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.model_center.models import ModelConfiguration, ModelProviderConnection
from app.modules.projects.models import Project, ProjectMember
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def structured_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__, ProjectMember.__table__, Secret.__table__,
        ModelProviderConnection.__table__,
        ModelConfiguration.__table__, OutputSchema.__table__,
        PromptDefinition.__table__, PromptVersion.__table__, AiCallLog.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _scope(client: TestClient) -> tuple[dict[str, str], dict[str, int]]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers,
        json={"name": "结构化输出项目", "code": "STRUCTURED_OUTPUT_TEST"},
    ).json()
    connection = client.post(
        "/api/v1/model-center/connections", headers=headers,
        json={
            "name": "结构化输出渠道", "provider": "OPENAI",
            "protocol_type": "OPENAI_COMPATIBLE", "base_url": "http://127.0.0.1:11434/v1",
        },
    ).json()
    model = client.post(
        "/api/v1/model-center", headers=headers,
        json={
            "name": "结构化输出模型", "connection_id": connection["id"],
            "model_vendor": "OPENAI", "model_name": "local-json",
            "model_type": "TEXT", "input_price": "2", "output_price": "8",
        },
    ).json()
    schema = client.post(
        "/api/v1/ai/output-schemas", headers=headers,
        json={
            "name": "RequirementReview",
            "schema_json": {
                "type": "object", "required": ["summary", "risks"],
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string", "minLength": 2},
                    "risks": {
                        "type": "array", "items": {"type": "string"},
                    },
                },
            },
        },
    ).json()
    prompt = client.post(
        "/api/v1/prompt-center", headers=headers,
        json={
            "name": "结构化需求评审", "code": "STRUCTURED_REVIEW_TEST",
            "task_type": "REQUIREMENT_REVIEW", "system_prompt": "输出 JSON",
            "user_template": "评审 {{ requirement }}", "output_schema_id": schema["id"],
        },
    ).json()
    return headers, {
        "project_id": project["id"], "model_id": model["id"],
        "schema_id": schema["id"], "prompt_version_id": prompt["current_version_id"],
    }


def _validation_payload(ids: dict[str, int], raw_output: str) -> dict:
    return {
        "project_id": ids["project_id"], "task_type": "REQUIREMENT_REVIEW",
        "entity_type": "REQUIREMENT", "entity_id": "1",
        "model_config_id": ids["model_id"],
        "prompt_version_id": ids["prompt_version_id"],
        "output_schema_id": ids["schema_id"], "raw_output": raw_output,
        "input_token": 1000, "output_token": 500, "latency_ms": 1200,
        "response_id": "response-test",
    }


def test_structured_output_valid_and_cost(structured_client: TestClient) -> None:
    headers, ids = _scope(structured_client)
    response = structured_client.post(
        "/api/v1/ai/structured-output/validate", headers=headers,
        json=_validation_payload(ids, '{"summary":"可测试","risks":["边界值"]}'),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["repair_used"] is False
    assert float(response.json()["estimated_cost"]) == pytest.approx(0.006)

    logs = structured_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    )
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["total_token"] == 1500
    assert logs.json()["items"][0]["response_id"] == "response-test"


def test_repair_once_and_invalid_audit(structured_client: TestClient) -> None:
    headers, ids = _scope(structured_client)
    repaired = structured_client.post(
        "/api/v1/ai/structured-output/validate", headers=headers,
        json=_validation_payload(
            ids, '```json\n{"summary":"已修复","risks":["超时"],}\n```'
        ),
    )
    assert repaired.json()["success"] is True
    assert repaired.json()["repair_used"] is True

    invalid_payload = _validation_payload(ids, '{"summary":"x"}')
    invalid_payload["repair_output"] = '{"summary":"仍无风险"}'
    invalid = structured_client.post(
        "/api/v1/ai/structured-output/validate", headers=headers,
        json=invalid_payload,
    )
    assert invalid.status_code == 200
    assert invalid.json()["success"] is False
    assert invalid.json()["repair_used"] is True
    assert "缺少必填字段" in invalid.json()["validation_errors"][0]

    logs = structured_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    ).json()["items"]
    assert len(logs) == 2
    assert logs[0]["error_type"] == "STRUCTURED_OUTPUT_INVALID"
    assert logs[0]["parsed_result"] is None


def test_output_schema_version(structured_client: TestClient) -> None:
    headers, ids = _scope(structured_client)
    response = structured_client.post(
        f"/api/v1/ai/output-schemas/{ids['schema_id']}/versions", headers=headers,
        json={
            "description": "V2",
            "schema_json": {
                "type": "object", "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            },
        },
    )
    assert response.status_code == 201
    assert response.json()["version_no"] == 2
