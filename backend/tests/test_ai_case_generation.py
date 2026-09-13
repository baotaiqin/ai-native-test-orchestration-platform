import json
from collections.abc import Generator
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.api_definitions.models import ApiDefinition, ApiDefinitionImport
from app.modules.database_connections.models import DatabaseConnection
from app.modules.environments.models import Environment, EnvironmentVariable
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.projects.models import Project, ProjectBusinessCounter, ProjectMember
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.secrets.models import Secret
from app.modules.test_cases.models import (
    AiCaseDesignTask,
    AiCaseGeneration,
    AiCaseGenerationTask,
    AiCaseSuggestion,
    RequirementCaseLink,
)
from app.modules.test_cases.models import TestCase as CaseModel
from app.modules.test_cases.models import TestCaseVersion as CaseVersionModel
from app.modules.test_cases.schemas import (
    AiCaseDesignResult,
    ApiCaseIntentResult,
    CaseGenerationResult,
)
from app.modules.test_cases.service import (
    _ai_case_design_validation_errors,
    _ai_confidence_by_sequence,
    _api_design_scope,
    _complete_ai_case_design_result,
    _generated_case_result_validation_errors,
    _is_valid_ai_case_design,
    _is_valid_generated_case_result,
    _merge_ai_case_design_results,
    _normalize_generated_case_result,
    _runtime_request_url,
)
from tests.auth_helpers import install_test_auth, uninstall_test_auth

CASES = {
    "cases": [
        {
            "title": "登录成功",
            "case_type": "API",
            "priority": "P0",
            "preconditions": ["账号有效"],
            "steps": [
                {"order": 1, "action": "提交正确账号密码", "expected": "返回访问令牌"}
            ],
            "test_data": {"username": "demo"},
            "expected_result": "登录成功",
            "tags": ["smoke"],
            "confidence": 0.95,
        },
        {
            "title": "密码错误",
            "case_type": "API",
            "priority": "P1",
            "preconditions": ["账号有效"],
            "steps": [
                {"order": 1, "action": "提交错误密码", "expected": "返回认证失败"}
            ],
            "test_data": {"password": "wrong"},
            "expected_result": "拒绝登录",
            "tags": ["negative"],
            "confidence": 0.9,
        },
    ]
}

INTENTS = {
    "intents": [
        {
            "case_key": "LOGIN_SUCCESS",
            "checkpoint_keys": ["CP-01"],
            "title": "登录成功",
            "api_definition_id": 1,
            "scenario_type": "POSITIVE",
            "priority": "P0",
            "expected_status": 200,
            "expected_claims": [{"field": "data.token", "operator": "EXISTS"}],
            "confidence": 0.95,
        },
        {
            "case_key": "LOGIN_PASSWORD_INVALID",
            "checkpoint_keys": ["CP-01"],
            "title": "密码错误",
            "api_definition_id": 1,
            "scenario_type": "NEGATIVE",
            "priority": "P1",
            "input_mutations": [
                {"field": "password", "location": "BODY", "strategy": "INVALID_TYPE"}
            ],
            "expected_status": 401,
            "expected_claims": [{"field": "error", "operator": "EXISTS"}],
            "confidence": 0.9,
        },
    ],
    "gaps": [],
}

REQUEST_TEMPLATE = {
    "method": "POST",
    "url": "{{base_url}}/api/v1/auth/login",
    "query_params": [{"name": "locale", "value": "zh-CN", "enabled": True}],
    "headers": [
        {"name": "X-Request-ID", "value": "{{run_id}}", "enabled": True}
    ],
    "cookies": [],
    "body": {
        "type": "JSON",
        "content": {"username": "demo", "password": "{{secret.login_password}}"},
    },
    "auth": {"type": "NONE"},
    "timeout_ms": 10000,
    "follow_redirects": False,
}


@pytest.fixture
def case_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__, ProjectMember.__table__, ProjectBusinessCounter.__table__,
        Environment.__table__, EnvironmentVariable.__table__, Secret.__table__,
        DatabaseConnection.__table__,
        ApiDefinitionImport.__table__, ApiDefinition.__table__,
        ModelProviderConnection.__table__,
        ModelConfiguration.__table__, ProjectModelBinding.__table__,
        OutputSchema.__table__, PromptDefinition.__table__, PromptVersion.__table__,
        Requirement.__table__, RequirementVersion.__table__, AiCallLog.__table__,
        CaseModel.__table__, CaseVersionModel.__table__, AiCaseGeneration.__table__,
        AiCaseDesignTask.__table__, AiCaseGenerationTask.__table__,
        AiCaseSuggestion.__table__, RequirementCaseLink.__table__,
        Scenario.__table__, ScenarioVersion.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        if "输出测试设计 JSON" in json.dumps(request_payload, ensure_ascii=False):
            model_output = {
                "check_points": [
                    {"key": "CP-01", "title": "验证用户账号密码登录", "source": "用户登录"}
                ],
                "recommended_apis": [
                    {
                        "api_definition_id": 1,
                        "role": "核心操作",
                        "required": True,
                        "reason": "该接口直接执行用户登录",
                        "check_point_keys": ["CP-01"],
                    }
                ],
                "gaps": [],
            }
        else:
            model_output = {
                **INTENTS,
                "gaps": ["access_token=case-generation-private"],
            }
        return httpx.Response(
            200,
            json={
                "id": "case-generation-response",
                "choices": [{"message": {"content": json.dumps(model_output)}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 700},
            },
        )

    from app.modules.ai_gateway import service as gateway_service

    monkeypatch.setattr(
        gateway_service,
        "_build_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout),
    )
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


def test_api_generation_contract_requires_executable_request_and_assertion() -> None:
    allowed_requests = {("POST", "{{base_url}}/api/resources")}
    assert not _is_valid_generated_case_result(CASES, allowed_requests)

    executable_result = json.loads(json.dumps(CASES))
    for generated_case in executable_result["cases"]:
        generated_case["request"] = {
            **REQUEST_TEMPLATE,
            "url": "{{base_url}}/api/resources",
        }
        generated_case["assertions"] = [
            {
                "name": "返回资源编号",
                "type": "JSONPATH_EQUAL",
                "source": "JSON_BODY",
                "expression": "$.id",
                "operator": "EQ",
                "expected": 1,
            }
        ]
    assert _is_valid_generated_case_result(executable_result, allowed_requests)

    executable_result["cases"][0]["request"]["url"] = "{{base_url}}/api/invented"
    assert not _is_valid_generated_case_result(executable_result, allowed_requests)


def test_api_generation_execution_gate_rejects_plaintext_and_missing_auth() -> None:
    login_key = ("POST", "{{base_url}}/api/login")
    protected_key = ("GET", "{{base_url}}/api/products")
    contracts = {
        login_key: {"requires_auth": False},
        protected_key: {"requires_auth": True},
    }
    assertion = {
        "name": "响应正文存在",
        "type": "EXISTS",
        "source": "JSONPATH",
        "expression": "$.data",
    }
    plaintext_login = {
        "cases": [
            {
                **CASES["cases"][0],
                "request": {
                    **REQUEST_TEMPLATE,
                    "url": login_key[1],
                    "headers": [],
                    "body": {
                        "type": "JSON",
                        "content": {"username": "admin", "password": "password123"},
                    },
                },
                "assertions": [assertion],
            }
        ]
    }
    assert not _is_valid_generated_case_result(
        plaintext_login,
        {login_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )
    plaintext_issues = _generated_case_result_validation_errors(
        plaintext_login,
        {login_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )
    assert any("request.body" in issue and "敏感字段" in issue for issue in plaintext_issues)

    missing_auth = json.loads(json.dumps(plaintext_login))
    missing_auth["cases"][0]["request"] = {
        **REQUEST_TEMPLATE,
        "method": "GET",
        "url": protected_key[1],
        "query_params": [],
        "headers": [],
        "body": {"type": "NONE", "content": None},
    }
    assert not _is_valid_generated_case_result(
        missing_auth,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )
    missing_auth_issues = _generated_case_result_validation_errors(
        missing_auth,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )
    assert any("request.auth" in issue and "受保护 API" in issue for issue in missing_auth_issues)


def test_api_generation_execution_gate_accepts_bound_login_context() -> None:
    protected_key = ("GET", "{{base_url}}/api/products")
    login_key = ("POST", "{{base_url}}/api/login")
    contracts = {
        protected_key: {"requires_auth": True},
        login_key: {"requires_auth": False},
    }
    request = {
        **REQUEST_TEMPLATE,
        "method": "GET",
        "url": protected_key[1],
        "query_params": [],
        "headers": [],
        "body": {"type": "NONE", "content": None},
        "auth": {"type": "BEARER", "token": "{{login_token}}"},
    }
    login_request = {
        **REQUEST_TEMPLATE,
        "url": "{{base_url}}/api/login",
        "headers": [],
        "body": {
            "type": "JSON",
            "content": {
                "username": "demo",
                "password": "{{secret.LOGIN_PASSWORD}}",
            },
        },
    }
    result = {
        "cases": [
            {
                **CASES["cases"][0],
                "request": request,
                "pre_actions": [
                    {
                        "type": "GET_TOKEN",
                        "name": "login_token",
                        "source": "JSONPATH",
                        "expression": "$.data.token",
                        "request": login_request,
                    }
                ],
                "assertions": [
                    {
                        "name": "商品列表存在",
                        "type": "EXISTS",
                        "source": "JSONPATH",
                        "expression": "$.items",
                    }
                ],
            }
        ]
    }
    assert _is_valid_generated_case_result(
        result,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )

    unconfigured_filter = json.loads(json.dumps(result))
    unconfigured_filter["cases"][0]["title"] = "按名称过滤商品"
    assert not _is_valid_generated_case_result(
        unconfigured_filter,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )
    unconfigured_filter["cases"][0]["request"]["query_params"] = [
        {"name": "q", "value": "demo", "enabled": True}
    ]
    assert _is_valid_generated_case_result(
        unconfigured_filter,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )

    unsafe_variable = json.loads(json.dumps(result))
    unsafe_variable["cases"][0]["pre_actions"] = [
        {
            "type": "SET_VARIABLE",
            "name": "login_token",
            "value": "looks-like-a-token-but-is-not-secret-backed",
        }
    ]
    assert not _is_valid_generated_case_result(
        unsafe_variable,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )

    missing_username_secret = json.loads(json.dumps(result))
    missing_username_secret["cases"][0]["pre_actions"][0]["request"]["body"][
        "content"
    ]["username"] = "{{secret.DOES_NOT_EXIST}}"
    assert not _is_valid_generated_case_result(
        missing_username_secret,
        {protected_key},
        api_contracts=contracts,
        available_secret_names={"LOGIN_PASSWORD"},
        require_execution_ready=True,
    )


def test_api_generation_execution_gate_accepts_api_setup_resource_id() -> None:
    login_key = ("POST", "{{base_url}}/api/login")
    create_key = ("POST", "{{base_url}}/api/resources")
    delete_key = ("DELETE", "{{base_url}}/api/resources/{{resource_id}}")
    contracts = {
        login_key: {"requires_auth": False},
        create_key: {"requires_auth": True},
        delete_key: {"requires_auth": True},
    }
    login_request = {
        **REQUEST_TEMPLATE,
        "url": login_key[1],
        "headers": [],
        "body": {
            "type": "JSON",
            "content": {
                "username": "{{secret.AI_TEST_USERNAME}}",
                "password": "{{secret.AI_TEST_PASSWORD}}",
            },
        },
    }
    create_request = {
        **REQUEST_TEMPLATE,
        "url": create_key[1],
        "headers": [],
        "body": {"type": "JSON", "content": {"name": "generated-resource"}},
        "auth": {"type": "BEARER", "token": "{{login_token}}"},
    }
    result = {
        "cases": [
            {
                **CASES["cases"][0],
                "title": "删除刚创建的资源返回 200",
                "request": {
                    **REQUEST_TEMPLATE,
                    "method": "DELETE",
                    "url": delete_key[1],
                    "query_params": [],
                    "headers": [],
                    "body": {"type": "NONE", "content": None},
                    "auth": {"type": "BEARER", "token": "{{login_token}}"},
                },
                "pre_actions": [
                    {
                        "type": "GET_TOKEN",
                        "name": "login_token",
                        "source": "JSONPATH",
                        "expression": "$.data.token",
                        "request": login_request,
                    },
                    {
                        "type": "API_SETUP",
                        "name": "resource_id",
                        "source": "JSONPATH",
                        "expression": "$.data.id",
                        "request": create_request,
                    },
                ],
                "assertions": [
                    {
                        "name": "删除成功状态码",
                        "type": "STATUS_CODE",
                        "operator": "EQ",
                        "expected": 200,
                    },
                    {
                        "name": "删除结果存在",
                        "type": "EXISTS",
                        "source": "JSONPATH",
                        "expression": "$.deleted",
                    },
                ],
            }
        ]
    }

    assert _is_valid_generated_case_result(
        result,
        {delete_key},
        api_contracts=contracts,
        available_secret_names={"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
        require_execution_ready=True,
    )

    aliased_path_variable = json.loads(json.dumps(result))
    aliased_path_variable["cases"][0]["request"]["url"] = (
        "{{base_url}}/api/resources/{{created_resource_id}}"
    )
    aliased_path_variable["cases"][0]["pre_actions"][1]["name"] = (
        "created_resource_id"
    )
    assert _is_valid_generated_case_result(
        aliased_path_variable,
        {delete_key},
        api_contracts=contracts,
        available_secret_names={"AI_TEST_USERNAME", "AI_TEST_PASSWORD"},
        require_execution_ready=True,
    )


def test_openapi_path_parameters_become_runtime_variables() -> None:
    assert (
        _runtime_request_url("/api/resources/{resource_id}")
        == "{{base_url}}/api/resources/{{resource_id}}"
    )


def _scope(client: TestClient) -> tuple[dict[str, str], dict[str, int]]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers,
        json={"name": "AI 用例项目", "code": "AI_CASE_TEST"},
    ).json()
    connection = client.post(
        "/api/v1/model-center/connections", headers=headers,
        json={
            "name": "AI 用例生成渠道", "provider": "OPENAI",
            "protocol_type": "OPENAI_COMPATIBLE", "base_url": "http://mock-provider/v1",
        },
    ).json()
    model = client.post(
        "/api/v1/model-center", headers=headers,
        json={
            "name": "用例生成模型", "connection_id": connection["id"],
            "model_vendor": "OPENAI", "model_name": "case-model",
            "model_type": "TEXT", "supports_structured_output": True,
        },
    ).json()
    client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project["id"], "task_type": "API_CASE_GENERATE",
            "primary_model_id": model["id"], "max_fallback": 0,
        },
    ).raise_for_status()
    client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project["id"], "task_type": "API_TEST_DESIGN",
            "primary_model_id": model["id"], "max_fallback": 0,
        },
    ).raise_for_status()
    schema = client.post(
        "/api/v1/ai/output-schemas", headers=headers,
        json={
            "name": "AiCaseIntentResult",
            "schema_json": ApiCaseIntentResult.model_json_schema(),
        },
    ).json()
    prompt = client.post(
        "/api/v1/prompt-center", headers=headers,
        json={
            "name": "API 用例生成 Prompt", "code": "AI_CASE_GENERATE_TEST",
            "task_type": "API_CASE_GENERATE", "system_prompt": "输出用例 JSON",
            "user_template": "根据需求生成 API 用例：{{ requirement }}",
            "output_schema_id": schema["id"],
        },
    ).json()
    design_schema = client.post(
        "/api/v1/ai/output-schemas", headers=headers,
        json={
            "name": "AiCaseDesignResult",
            "schema_json": AiCaseDesignResult.model_json_schema(),
        },
    ).json()
    design_prompt = client.post(
        "/api/v1/prompt-center", headers=headers,
        json={
            "name": "AI 测试设计 Prompt", "code": "AI_TEST_DESIGN_TEST",
            "task_type": "API_TEST_DESIGN", "system_prompt": "输出测试设计 JSON",
            "user_template": (
                "需求范围：{{ requirement_scope }}；候选接口：{{ api_definitions }}；"
                "指定接口：{{ specified_api_ids }}"
            ),
            "output_schema_id": design_schema["id"],
        },
    ).json()
    requirement = client.post(
        "/api/v1/requirements", headers=headers,
        json={
            "project_id": project["id"], "title": "用户登录",
            "markdown_content": "# 用户登录\n支持账号密码登录。",
        },
    ).json()
    environment = client.post(
        "/api/v1/environments",
        headers=headers,
        json={
            "project_id": project["id"],
            "name": "默认环境",
            "code": "default",
            "base_url": "https://api.example.test",
        },
    ).json()
    client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": project["id"],
            "environment_id": environment["id"],
            "name": "LOGIN_PASSWORD",
            "secret_type": "PASSWORD",
            "value": "test-only-password",
        },
    ).raise_for_status()
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "登录接口", "version": "1.0.0"},
        "paths": {
            "/api/login": {
                "post": {
                    "operationId": "login",
                    "summary": "用户账号密码登录",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["username", "password"],
                                    "properties": {
                                        "username": {"type": "string", "example": "demo"},
                                        "password": {"type": "string", "format": "password"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "登录成功",
                            "content": {
                                "application/json": {
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
                        },
                        "401": {
                            "description": "认证失败",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"error": {"type": "string"}},
                                    }
                                }
                            },
                        },
                    },
                }
            }
        },
    }
    imported = client.post(
        "/api/v1/api-definitions/import",
        headers=headers,
        json={
            "project_id": project["id"],
            "filename": "login.json",
            "content": json.dumps(openapi),
        },
    )
    assert imported.status_code == 201
    return headers, {
        "project_id": project["id"], "prompt_id": prompt["id"],
        "design_prompt_id": design_prompt["id"],
        "requirement_id": requirement["id"],
    }


def _cleanup_resources(
    client: TestClient, headers: dict[str, str], project_id: int, suffix: str
) -> dict[str, int]:
    environment = client.post(
        "/api/v1/environments",
        headers=headers,
        json={
            "project_id": project_id,
            "name": f"Cleanup 环境 {suffix}",
            "code": f"cleanup_{suffix.lower()}",
            "base_url": "https://cleanup.example.test",
        },
    )
    assert environment.status_code == 201
    environment_id = environment.json()["id"]
    secret = client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": environment_id,
            "name": f"cleanup_secret_{suffix.lower()}",
            "secret_type": "DB_PASSWORD",
            "value": "test-only-secret",
        },
    )
    assert secret.status_code == 201
    secret_id = secret.json()["id"]
    connection = client.post(
        "/api/v1/database-connections",
        headers=headers,
        json={
            "project_id": project_id,
            "environment_id": environment_id,
            "name": f"Cleanup DB {suffix}",
            "host": "mock-db",
            "port": 3306,
            "database_name": "demo",
            "username": "tester",
            "password_secret_id": secret_id,
        },
    )
    assert connection.status_code == 201
    return {"secret_id": secret_id, "connection_id": connection.json()["id"]}


def _api_cleanup_with_secret(secret_id: int) -> dict[str, object]:
    return {
        "cleanup_type": "API",
        "method": "DELETE",
        "url": "https://cleanup.example.test/users/{{resource_id}}",
        "auth": {"type": "BEARER", "secret_id": secret_id},
    }


def _sql_cleanup_with_connection(connection_id: int) -> dict[str, object]:
    return {
        "cleanup_type": "SQL",
        "connection_id": connection_id,
        "sql": "DELETE FROM users WHERE id=%(resource_id)s",
        "params": {},
        "resource_id_param": "resource_id",
    }


def test_edit_accept_reject_and_formal_case_link(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers, json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 201
    suggestions = generated.json()["suggestions"]
    assert len(suggestions) == 2

    edited_case = {**CASES["cases"][0], "title": "登录成功（人工修订）"}
    edited = case_client.patch(
        f"/api/v1/test-cases/suggestions/{suggestions[0]['id']}", headers=headers,
        json={"human_result": edited_case, "decision_note": "补充标题"},
    )
    assert edited.status_code == 200
    accepted = case_client.post(
        f"/api/v1/test-cases/suggestions/{suggestions[0]['id']}/decision",
        headers=headers, json={"action": "ACCEPT"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    case_id = accepted.json()["test_case_id"]

    rejected = case_client.post(
        f"/api/v1/test-cases/suggestions/{suggestions[1]['id']}/decision",
        headers=headers, json={"action": "REJECT", "decision_note": "重复覆盖"},
    )
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["test_case_id"] is None

    formal_case = case_client.get(f"/api/v1/test-cases/{case_id}", headers=headers)
    assert formal_case.status_code == 200
    assert formal_case.json()["name"] == "登录成功（人工修订）"
    assert formal_case.json()["current_version"]["version_no"] == 1
    assert formal_case.json()["current_version"]["content"]["priority"] == "P0"
    links = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/links",
        headers=headers,
    )
    assert links.status_code == 200
    assert links.json()[0]["case_id"] == case_id
    assert links.json()[0]["asset_type"] == "TEST_CASE"
    assert links.json()[0]["case_version_id"] == formal_case.json()["current_version"]["id"]
    assert links.json()[0]["created_at_time_basis"] == "UTC"
    created_at = datetime.fromisoformat(links.json()[0]["created_at"].replace("Z", "+00:00"))
    assert created_at.utcoffset() == timedelta(0)
    assert float(links.json()[0]["confidence"]) == pytest.approx(0.95)

    frozen = case_client.patch(
        f"/api/v1/test-cases/suggestions/{suggestions[0]['id']}", headers=headers,
        json={"human_result": CASES["cases"][0]},
    )
    assert frozen.status_code == 409


def test_async_generation_task_is_recorded_before_result_can_be_reviewed(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    created = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generation-tasks",
        headers=headers,
        json={"prompt_id": ids["prompt_id"], "additional_instructions": "覆盖异常路径"},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "QUEUED"
    assert created.json()["generation_id"] is None

    listed = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generation-tasks",
        headers=headers,
    )
    assert listed.status_code == 200
    task = listed.json()["items"][0]
    assert task["status"] == "SUCCEEDED"
    assert task["generation_id"] is not None
    assert task["error_message"] is None

    generations = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
    )
    assert generations.status_code == 200
    assert generations.json()["items"][0]["id"] == task["generation_id"]
    assert len(generations.json()["items"][0]["suggestions"]) == 2


def test_latest_compiler_recompile_is_idempotent_and_does_not_call_ai(
    case_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, ids = _scope(case_client)
    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 201
    generation = generated.json()
    first = generation["suggestions"][0]
    accepted = case_client.post(
        f"/api/v1/test-cases/suggestions/{first['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    assert accepted.status_code == 200

    from app.modules.test_cases import case_compiler
    from app.modules.test_cases import service as case_service

    next_compiler_version = "test-next-compiler"
    monkeypatch.setattr(case_compiler, "CASE_COMPILER_VERSION", next_compiler_version)
    monkeypatch.setattr(case_service, "CASE_COMPILER_VERSION", next_compiler_version)

    recompiled = case_client.post(
        f"/api/v1/test-cases/generations/{generation['id']}/recompile",
        headers=headers,
    )

    assert recompiled.status_code == 200
    result = recompiled.json()
    assert result["compiler_version"] == next_compiler_version
    assert result["recompiled_count"] == 1
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    versions = case_client.get(
        f"/api/v1/test-cases/{accepted.json()['test_case_id']}/versions",
        headers=headers,
    )
    assert versions.status_code == 200
    assert len(versions.json()) == 2
    assert versions.json()[0]["content"]["test_data"]["compiler_version"] == (
        next_compiler_version
    )

    repeated = case_client.post(
        f"/api/v1/test-cases/generations/{generation['id']}/recompile",
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["recompiled_count"] == 0
    assert repeated.json()["skipped_count"] == 2


def test_design_plan_recommends_matching_api_and_persists_selection(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "登录接口", "version": "1.0.0"},
        "paths": {
            "/api/login": {
                "post": {
                    "operationId": "login",
                    "summary": "用户账号密码登录",
                    "responses": {"200": {"description": "登录成功"}},
                }
            }
        },
    }
    imported = case_client.post(
        "/api/v1/api-definitions/import",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "filename": "login.json",
            "content": json.dumps(openapi),
        },
    )
    assert imported.status_code == 201
    definitions = case_client.get(
        "/api/v1/api-definitions",
        headers=headers,
        params={"project_id": ids["project_id"]},
    ).json()["items"]
    api_id = definitions[0]["id"]

    planned = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-plan",
        headers=headers,
    )
    assert planned.status_code == 200
    assert planned.json()["check_points"][0]["key"] == "CP-01"
    assert planned.json()["recommended_apis"][0]["api_definition_id"] == api_id
    assert planned.json()["recommended_apis"][0]["selected_by_default"] is True

    created = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generation-tasks",
        headers=headers,
        json={
            "prompt_id": ids["prompt_id"],
            "selected_api_definition_ids": [api_id],
        },
    )
    assert created.status_code == 202
    assert created.json()["selected_api_definition_ids"] == [api_id]
    assert created.json()["coverage_plan"]["check_points"][0]["key"] == "CP-01"


def test_ai_design_requires_at_least_one_check_point_api_link() -> None:
    value = {
        "check_points": [
            {"key": "CP-01", "title": "验证用户登录", "source": "用户登录"}
        ],
        "recommended_apis": [
            {
                "api_definition_id": 1,
                "role": "核心操作",
                "required": True,
                "reason": "执行登录",
                "check_point_keys": [],
            }
        ],
        "gaps": [],
    }
    assert not _is_valid_ai_case_design(value, {1})
    errors = _ai_case_design_validation_errors(value, {1})
    assert any(
        "$.recommended_apis.0.check_point_keys" in error
        and "元素数量不能少于 1" in error
        for error in errors
    )


def test_ai_design_keeps_atomicity_hard_but_completes_missing_coverage_later() -> None:
    errors = _ai_case_design_validation_errors(
        {
            "check_points": [
                {
                    "key": "CP-01",
                    "title": "错误用户名或错误密码登录返回 HTTP 401 或 HTTP 403",
                    "source": "REQ-0001 · 登录",
                }
            ],
            "recommended_apis": [
                {
                    "api_definition_id": 1,
                    "role": "核心操作",
                    "required": True,
                    "reason": "验证登录",
                    "check_point_keys": ["CP-01"],
                }
            ],
            "gaps": [],
        },
        {1},
        {"REQ-0001": "登录", "REQ-0002": "商品查询"},
    )
    assert any(
        "$.check_points[0].title" in error and "请拆成独立检查点" in error
        for error in errors
    )
    assert not any("叶子需求 REQ-0002（商品查询）" in error for error in errors)


def test_ai_design_routes_non_api_leaves_and_completes_usable_model_output() -> None:
    root = Requirement(
        id=1,
        project_id=1,
        code="REQ-0001",
        title="完整需求",
        type="SECTION",
        verification_type="AUTO",
        automation_readiness="READY",
        status="ACTIVE",
        created_by="admin",
    )
    api_leaf = Requirement(
        id=2,
        project_id=1,
        parent_id=1,
        code="REQ-0002",
        title="商品查询",
        type="FEATURE",
        verification_type="API",
        automation_readiness="READY",
        status="ACTIVE",
        created_by="admin",
    )
    web_leaf = Requirement(
        id=3,
        project_id=1,
        parent_id=1,
        code="REQ-0003",
        title="登录页面",
        type="FEATURE",
        verification_type="WEB",
        automation_readiness="READY",
        status="ACTIVE",
        created_by="admin",
    )
    versions = [
        RequirementVersion(
            requirement_id=index,
            version_no=1,
            markdown_content=content,
            content_hash="0" * 64,
            source_type="MARKDOWN",
            created_by="admin",
        )
        for index, content in enumerate(
            ["# 完整需求", "- 查询商品返回 HTTP 200", "- 页面显示登录按钮"],
            start=1,
        )
    ]
    routed, excluded = _api_design_scope(
        list(zip([root, api_leaf, web_leaf], versions, strict=True))
    )
    assert [item.code for item, _ in routed] == ["REQ-0001", "REQ-0002"]
    assert excluded[0].requirement_code == "REQ-0003"
    assert excluded[0].reason == "已分流至Web 自动化"

    result = AiCaseDesignResult.model_validate(
        {
            "check_points": [
                {"key": "CP-01", "title": "检查服务可用", "source": "其他需求"}
            ],
            "recommended_apis": [
                {
                    "api_definition_id": 1,
                    "role": "核心操作",
                    "required": True,
                    "reason": "查询商品",
                    "check_point_keys": ["CP-01", "CP-99"],
                }
            ],
            "gaps": [],
        }
    )
    completed, completed_count, ignored_count = _complete_ai_case_design_result(
        routed, result
    )
    assert completed_count == 1
    assert ignored_count == 1
    assert any("REQ-0002" in point.source for point in completed.check_points)
    assert completed.recommended_apis[0].check_point_keys == ["CP-01"]


def test_ai_design_schema_supports_large_atomic_check_point_catalog() -> None:
    check_points = [
        {
            "key": f"CP-{index:02d}",
            "title": f"验证第 {index} 个独立业务分支",
            "source": f"REQ-{index:04d}",
        }
        for index in range(1, 59)
    ]
    result = AiCaseDesignResult.model_validate(
        {
            "check_points": check_points,
            "recommended_apis": [
                {
                    "api_definition_id": 1,
                    "role": "核心操作",
                    "required": True,
                    "reason": "覆盖完整业务目录",
                    "check_point_keys": [item["key"] for item in check_points],
                }
            ],
            "gaps": [],
        }
    )
    assert len(result.check_points) == 58
    assert result.check_points[-1].key == "CP-58"
    assert len(result.recommended_apis[0].check_point_keys) == 58


def test_ai_design_batch_merge_renumbers_and_combines_api_decisions() -> None:
    first = AiCaseDesignResult.model_validate({
        "check_points": [
            {"key": "CP-01", "title": "登录成功", "source": "REQ-0001"}
        ],
        "recommended_apis": [{
            "api_definition_id": 7,
            "role": "核心操作",
            "required": True,
            "reason": "验证登录",
            "check_point_keys": ["CP-01"],
        }],
        "gaps": [],
    })
    second = AiCaseDesignResult.model_validate({
        "check_points": [
            {"key": "CP-01", "title": "退出成功", "source": "REQ-0002"}
        ],
        "recommended_apis": [{
            "api_definition_id": 7,
            "role": "核心操作",
            "required": False,
            "reason": "验证会话",
            "check_point_keys": ["CP-01"],
        }],
        "gaps": [],
    })

    merged = _merge_ai_case_design_results(
        [first, second], extra_gaps=["第 3 批由平台补全"]
    )

    assert [item.key for item in merged.check_points] == ["CP-01", "CP-02"]
    assert merged.recommended_apis[0].check_point_keys == ["CP-01", "CP-02"]
    assert merged.recommended_apis[0].required is True
    assert merged.gaps == ["第 3 批由平台补全"]


def test_ai_design_compiler_supplements_uncovered_api(
    case_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, ids = _scope(case_client)
    imported = case_client.post(
        "/api/v1/api-definitions/import",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "filename": "design-coverage.json",
            "content": json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "演示接口", "version": "1.0.0"},
                    "paths": {
                        "/api/login": {
                            "post": {
                                "operationId": "login",
                                "summary": "用户登录认证",
                                "responses": {"200": {"description": "成功"}},
                            }
                        },
                        "/api/products": {
                            "get": {
                                "operationId": "listProducts",
                                "summary": "查询商品列表",
                                "responses": {"200": {"description": "成功"}},
                            }
                        },
                    },
                }
            ),
        },
    )
    assert imported.status_code == 201
    definitions = case_client.get(
        "/api/v1/api-definitions",
        headers=headers,
        params={"project_id": ids["project_id"]},
    ).json()["items"]
    by_path = {item["path"]: item for item in definitions}
    login_id = by_path["/api/login"]["id"]
    products_id = by_path["/api/products"]["id"]
    captured_system_messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        captured_system_messages.append(request_payload["messages"][0]["content"])
        model_output = {
            "check_points": [
                {"key": "CP-01", "title": "验证用户登录", "source": "用户登录"},
                {
                    "key": "CP-02",
                    "title": "验证查询商品列表",
                    "source": "商品查询",
                },
            ],
            "recommended_apis": [
                {
                    "api_definition_id": login_id,
                    "role": "前置准备",
                    "required": True,
                    "reason": "用于登录",
                    "check_point_keys": ["CP-01"],
                }
            ],
            "gaps": [],
        }
        return httpx.Response(
            200,
            json={
                "id": "design-coverage-response",
                "choices": [{"message": {"content": json.dumps(model_output)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    from app.modules.ai_gateway import service as gateway_service

    monkeypatch.setattr(
        gateway_service,
        "_build_client",
        lambda timeout: httpx.Client(
            transport=httpx.MockTransport(handler), timeout=timeout
        ),
    )
    created = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
        json={"prompt_id": ids["design_prompt_id"], "include_api_ids": []},
    )
    assert created.status_code == 202
    tasks = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
    ).json()["items"]
    assert tasks[0]["status"] == "SUCCEEDED"
    plan = tasks[0]["plan"]
    decisions = {
        item["api_definition_id"]: item for item in plan["recommended_apis"]
    }
    assert set(decisions) == {login_id, products_id}
    assert decisions[products_id]["check_point_keys"] == ["CP-02"]
    assert "平台根据 CP-02 补齐接口" in decisions[products_id]["reason"]
    assert "平台不可由项目提示词覆盖的输出契约" in captured_system_messages[0]
    assert "每个 recommended_apis 项都必须填写非空" in captured_system_messages[0]


def test_async_ai_design_task_recommends_real_api_and_is_reused(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    imported = case_client.post(
        "/api/v1/api-definitions/import",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "filename": "login-ai-design.json",
            "content": json.dumps({
                "openapi": "3.0.3",
                "info": {"title": "登录接口", "version": "1.0.0"},
                "paths": {
                    "/api/login": {
                        "post": {
                            "operationId": "login",
                            "summary": "用户账号密码登录",
                            "responses": {"200": {"description": "登录成功"}},
                        }
                    }
                },
            }),
        },
    )
    assert imported.status_code == 201
    api_id = case_client.get(
        "/api/v1/api-definitions",
        headers=headers,
        params={"project_id": ids["project_id"]},
    ).json()["items"][0]["id"]

    created = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
        json={"prompt_id": ids["design_prompt_id"], "include_api_ids": [api_id]},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "QUEUED"

    tasks = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
    ).json()["items"]
    assert tasks[0]["status"] == "SUCCEEDED"
    assert tasks[0]["source"] == "AI", tasks[0]
    assert tasks[0]["plan"]["source"] == "AI"
    assert tasks[0]["plan"]["recommended_apis"][0]["api_definition_id"] == api_id
    assert tasks[0]["requirement_code"]
    assert tasks[0]["requirement_title"] == "用户登录"
    assert tasks[0]["requirement_version_no"] == 1

    project_tasks = case_client.get(
        f"/api/v1/test-cases/projects/{ids['project_id']}/design-tasks",
        headers=headers,
        params={"page": 1, "page_size": 10},
    )
    assert project_tasks.status_code == 200
    assert project_tasks.json()["total"] == 1
    assert project_tasks.json()["page"] == 1
    assert project_tasks.json()["page_size"] == 10
    assert project_tasks.json()["active_count"] == 0
    assert project_tasks.json()["items"][0]["id"] == tasks[0]["id"]

    reused = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
        json={"prompt_id": ids["design_prompt_id"], "include_api_ids": [api_id]},
    )
    assert reused.status_code == 202
    assert reused.json()["id"] == tasks[0]["id"]
    assert reused.json()["reused"] is True

    generation_task = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generation-tasks",
        headers=headers,
        json={
            "prompt_id": ids["prompt_id"],
            "design_task_id": tasks[0]["id"],
            "selected_api_definition_ids": [api_id],
        },
    )
    assert generation_task.status_code == 202
    assert generation_task.json()["coverage_plan"]["design_task_id"] == tasks[0]["id"]

    referenced_delete = case_client.delete(
        f"/api/v1/test-cases/design-tasks/{tasks[0]['id']}",
        headers=headers,
    )
    assert referenced_delete.status_code == 409
    assert referenced_delete.json()["message"] == "该设计已被用例生成记录引用，不能删除"

    refreshed = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/design-tasks",
        headers=headers,
        json={
            "prompt_id": ids["design_prompt_id"],
            "include_api_ids": [api_id],
            "force_refresh": True,
        },
    )
    assert refreshed.status_code == 202
    assert refreshed.json()["id"] != tasks[0]["id"]
    deleted = case_client.delete(
        f"/api/v1/test-cases/design-tasks/{refreshed.json()['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204
    remaining = case_client.get(
        f"/api/v1/test-cases/projects/{ids['project_id']}/design-tasks",
        headers=headers,
    ).json()
    assert remaining["total"] == 1
    assert remaining["items"][0]["id"] == tasks[0]["id"]


def test_generated_json_path_aliases_are_normalized_without_relaxing_domain_contract() -> None:
    payload = {
        "cases": [
            {
                **CASES["cases"][0],
                "assertions": [
                    {
                        "type": "json_path",
                        "name": "状态正确",
                        "expression": "$.status",
                        "operator": "EQ",
                        "expected": "ok",
                    },
                    {
                        "type": "JSON_PATH",
                        "name": "消息包含成功",
                        "expression": "$.message",
                        "operator": "CONTAINS",
                        "expected": "成功",
                    },
                ],
            }
        ]
    }
    normalized = _normalize_generated_case_result(payload)
    validated = CaseGenerationResult.model_validate(normalized)
    assert validated.cases[0].assertions[0].type == "JSONPATH_EQUAL"
    assert validated.cases[0].assertions[0].source == "JSON_BODY"
    assert validated.cases[0].assertions[1].type == "CONTAINS"
    assert validated.cases[0].assertions[1].source == "JSONPATH"


def test_generated_exists_assertion_drops_redundant_expected_value() -> None:
    payload = {
        "cases": [
            {
                **CASES["cases"][0],
                "assertions": [
                    {
                        "type": "exists",
                        "name": "令牌存在",
                        "source": "JSONPATH",
                        "expression": "$.data.token",
                        "expected": True,
                        "kind": "DETERMINISTIC",
                    },
                ],
            }
        ]
    }
    normalized = _normalize_generated_case_result(payload)
    validated = CaseGenerationResult.model_validate(normalized)
    assertion = validated.cases[0].assertions[0]
    assert assertion.type == "EXISTS"
    assert assertion.expected is None
    assert payload["cases"][0]["assertions"][0]["expected"] is True


def test_generated_concrete_path_value_is_restored_to_unique_selected_api_template() -> None:
    payload = {
        "cases": [
            {
                    **CASES["cases"][0],
                    "request": {
                        **REQUEST_TEMPLATE,
                    "method": "DELETE",
                    "url": "{{base_url}}/api/orders/99999",
                },
            }
        ]
    }
    allowed = {
        ("DELETE", "{{base_url}}/api/orders/{{order_id}}"),
        ("DELETE", "{{base_url}}/api/resources/{{resource_id}}"),
    }
    normalized = _normalize_generated_case_result(payload, allowed)
    assert (
        normalized["cases"][0]["request"]["url"]
        == "{{base_url}}/api/orders/{{order_id}}"
    )
    assert payload["cases"][0]["request"]["url"] == "{{base_url}}/api/orders/99999"


def test_generated_concrete_path_value_is_not_changed_when_template_is_ambiguous() -> None:
    payload = {
        "cases": [
            {
                    **CASES["cases"][0],
                    "request": {
                        **REQUEST_TEMPLATE,
                    "method": "GET",
                    "url": "{{base_url}}/api/items/42",
                },
            }
        ]
    }
    allowed = {
        ("GET", "{{base_url}}/api/items/{{item_id}}"),
        ("GET", "{{base_url}}/api/items/{{item_code}}"),
    }
    normalized = _normalize_generated_case_result(payload, allowed)
    assert normalized["cases"][0]["request"]["url"] == "{{base_url}}/api/items/42"


def test_ai_confidence_only_exposes_values_explicitly_returned_by_model() -> None:
    call = AiCallLog(
        parsed_result={
            "cases": [
                {"title": "未自评"},
                {"title": "明确自评", "confidence": 0.73},
                {"title": "非法布尔值", "confidence": True},
            ]
        }
    )
    assert _ai_confidence_by_sequence(call) == {2: 0.73}


def test_bulk_accept_creates_two_cases(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers, json={"prompt_id": ids["prompt_id"]},
    ).json()
    suggestion_ids = [item["id"] for item in generated["suggestions"]]
    bulk = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-decision", headers=headers,
        json={"suggestion_ids": suggestion_ids, "action": "ACCEPT"},
    )
    assert bulk.status_code == 200
    assert all(item["status"] == "ACCEPTED" for item in bulk.json())
    cases = case_client.get(
        f"/api/v1/test-cases?project_id={ids['project_id']}", headers=headers
    )
    assert len(cases.json()) == 2


def test_bulk_review_edits_metadata_links_requirements_and_deletes_drafts(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    extra_requirement = case_client.post(
        "/api/v1/requirements",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "title": "安全审计",
            "markdown_content": "# 安全审计\n用例需要额外覆盖。",
        },
    )
    assert extra_requirement.status_code == 201
    extra_requirement_id = extra_requirement.json()["id"]
    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 201
    generation = generated.json()
    assert generation["actual_model"] == "case-model"
    assert generation["requirement_title"] == "用户登录"
    assert generation["requirement_version_no"] == 1
    assert generation["prompt_version_id"] > 0
    assert generation["prompt_name"] == "API 用例生成 Prompt"
    assert generation["prompt_version_no"] == 1
    assert generation["output_schema_id"] > 0
    assert generation["output_schema_name"] == "AiCaseIntentResult"
    assert generation["output_schema_version_no"] == 1
    assert generation["ai_confidence_by_sequence"] == {"1": 0.95, "2": 0.9}
    assert generation["fallback_used"] is False
    assert generation["repair_used"] is False
    assert "case-generation-private" not in generation["raw_response"]
    suggestion_ids = [item["id"] for item in generation["suggestions"]]

    edited = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-edit",
        headers=headers,
        json={
            "suggestion_ids": suggestion_ids,
            "priority": "P3",
            "tags": ["regression", "regression", " security "],
            "requirement_ids": [ids["requirement_id"], extra_requirement_id],
        },
    )
    assert edited.status_code == 200, edited.text
    assert all(item["human_result"]["priority"] == "P3" for item in edited.json())
    assert all(item["human_result"]["tags"] == ["regression", "security"] for item in edited.json())
    assert all(item["linked_requirement_ids"] == [extra_requirement_id] for item in edited.json())

    accepted = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-decision",
        headers=headers,
        json={"suggestion_ids": suggestion_ids, "action": "ACCEPT"},
    )
    assert accepted.status_code == 200, accepted.text
    extra_links = case_client.get(
        f"/api/v1/test-cases/requirements/{extra_requirement_id}/links",
        headers=headers,
    )
    assert extra_links.status_code == 200
    assert len(extra_links.json()) == 2

    second_generation = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    ).json()
    deletable_ids = [item["id"] for item in second_generation["suggestions"]]
    deleted = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-delete",
        headers=headers,
        json={"suggestion_ids": deletable_ids},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_count": 2}
    generations = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
    ).json()["items"]
    assert next(item for item in generations if item["id"] == second_generation["id"])[
        "suggestions"
    ] == []


def test_manual_case_version_link_and_archive(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    v1_content = {**CASES["cases"][0], "request": REQUEST_TEMPLATE}
    created = case_client.post(
        "/api/v1/test-cases",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "requirement_id": ids["requirement_id"],
            "content": v1_content,
            "change_note": "创建登录接口用例",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert created.json()["source"] == "MANUAL"
    assert created.json()["current_version"]["version_no"] == 1
    assert created.json()["current_version"]["content"]["cleanup"] == []

    changed_content = {
        **v1_content,
        "title": "登录成功 V2",
        "expected_result": "返回访问令牌与刷新令牌",
    }
    version = case_client.post(
        f"/api/v1/test-cases/{case_id}/versions",
        headers=headers,
        json={"content": changed_content, "change_note": "补充刷新令牌断言"},
    )
    assert version.status_code == 201
    assert version.json()["version_no"] == 2
    versions = case_client.get(
        f"/api/v1/test-cases/{case_id}/versions", headers=headers
    )
    assert [item["version_no"] for item in versions.json()] == [2, 1]
    assert versions.json()[1]["content"]["title"] == "登录成功"

    links = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/links",
        headers=headers,
    )
    assert links.json()[0]["case_id"] == case_id
    assert links.json()[0]["source"] == "MANUAL"
    assert links.json()[0]["case_version_id"] == version.json()["id"]
    assert links.json()[0]["created_at_time_basis"] == "UTC"
    created_at = datetime.fromisoformat(links.json()[0]["created_at"].replace("Z", "+00:00"))
    assert created_at.utcoffset() == timedelta(0)

    link_history = case_client.get(
        f"/api/v1/requirements/{ids['requirement_id']}/links",
        headers=headers,
        params={"include_removed": True},
    )
    assert link_history.status_code == 200
    assert link_history.json()["total"] == 2
    removed_link = next(
        item for item in link_history.json()["items"] if item["status"] == "REMOVED"
    )
    active_link = next(
        item for item in link_history.json()["items"] if item["status"] == "ACTIVE"
    )
    assert removed_link["asset_version_id"] == created.json()["current_version"]["id"]
    assert active_link["asset_version_id"] == version.json()["id"]
    assert active_link["requirement_version_id"] == removed_link["requirement_version_id"]
    assert active_link["supersedes_link_id"] == removed_link["id"]

    archived = case_client.post(
        f"/api/v1/test-cases/{case_id}/archive", headers=headers
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    blocked = case_client.post(
        f"/api/v1/test-cases/{case_id}/versions",
        headers=headers,
        json={"content": changed_content, "change_note": "不应写入"},
    )
    assert blocked.status_code == 409


def test_manual_case_supports_multiple_requirement_links(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    extra_requirement = case_client.post(
        "/api/v1/requirements",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "title": "登录审计",
            "markdown_content": "# 登录审计\n记录成功与失败登录。",
        },
    )
    assert extra_requirement.status_code == 201
    extra_requirement_id = extra_requirement.json()["id"]

    created = case_client.post(
        "/api/v1/test-cases",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "requirement_ids": [
                ids["requirement_id"],
                extra_requirement_id,
                ids["requirement_id"],
            ],
            "content": {**CASES["cases"][0], "request": REQUEST_TEMPLATE},
            "change_note": "人工创建并关联多个需求",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "MANUAL"
    assert created.json()["status"] == "ACTIVE"
    assert created.json()["current_version"]["version_no"] == 1

    case_id = created.json()["id"]
    for requirement_id in (ids["requirement_id"], extra_requirement_id):
        links = case_client.get(
            f"/api/v1/test-cases/requirements/{requirement_id}/links",
            headers=headers,
        )
        matching = [item for item in links.json() if item["case_id"] == case_id]
        assert len(matching) == 1
        assert matching[0]["source"] == "MANUAL"
        assert float(matching[0]["confidence"]) == pytest.approx(1)


def test_cleanup_scope_is_enforced_at_all_formalization_entries(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    own = _cleanup_resources(case_client, headers, ids["project_id"], "OWN")
    disabled = _cleanup_resources(case_client, headers, ids["project_id"], "OFF")
    disabled_secret_only = _cleanup_resources(
        case_client, headers, ids["project_id"], "SECOFF"
    )
    assert case_client.patch(
        f"/api/v1/secrets/{disabled['secret_id']}",
        headers=headers,
        json={"enabled": False},
    ).status_code == 200
    assert case_client.patch(
        f"/api/v1/database-connections/{disabled['connection_id']}",
        headers=headers,
        json={"enabled": False},
    ).status_code == 200
    assert case_client.patch(
        f"/api/v1/secrets/{disabled_secret_only['secret_id']}",
        headers=headers,
        json={"enabled": False},
    ).status_code == 200

    other_project = case_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Cleanup Other Project", "code": "AI_CASE_OTHER"},
    )
    assert other_project.status_code == 201
    other = _cleanup_resources(
        case_client, headers, other_project.json()["id"], "OTHER"
    )

    invalid_cleanups = [
        _api_cleanup_with_secret(other["secret_id"]),
        _api_cleanup_with_secret(disabled["secret_id"]),
        _sql_cleanup_with_connection(other["connection_id"]),
        _sql_cleanup_with_connection(disabled["connection_id"]),
        _sql_cleanup_with_connection(disabled_secret_only["connection_id"]),
    ]
    base_content = {**CASES["cases"][0], "request": REQUEST_TEMPLATE}
    for cleanup in invalid_cleanups:
        response = case_client.post(
            "/api/v1/test-cases",
            headers=headers,
            json={
                "project_id": ids["project_id"],
                "content": {**base_content, "cleanup": [cleanup]},
            },
        )
        assert response.status_code == 409

    created = case_client.post(
        "/api/v1/test-cases",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "content": {**base_content, "cleanup": [_api_cleanup_with_secret(own["secret_id"])]},
        },
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    invalid_version = case_client.post(
        f"/api/v1/test-cases/{case_id}/versions",
        headers=headers,
        json={
            "content": {
                **base_content,
                "cleanup": [_sql_cleanup_with_connection(disabled["connection_id"])],
            },
            "change_note": "拒绝停用连接",
        },
    )
    assert invalid_version.status_code == 409
    valid_version = case_client.post(
        f"/api/v1/test-cases/{case_id}/versions",
        headers=headers,
        json={
            "content": {
                **base_content,
                "title": "登录成功 V2",
                "cleanup": [_sql_cleanup_with_connection(own["connection_id"])],
            },
            "change_note": "切换 SQL Cleanup",
        },
    )
    assert valid_version.status_code == 201

    for cleanup in invalid_cleanups:
        generated = case_client.post(
            f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
            headers=headers,
            json={"prompt_id": ids["prompt_id"]},
        )
        assert generated.status_code == 201
        suggestion = generated.json()["suggestions"][0]
        edited = case_client.patch(
            f"/api/v1/test-cases/suggestions/{suggestion['id']}",
            headers=headers,
            json={"human_result": {**base_content, "cleanup": [cleanup]}},
        )
        assert edited.status_code == 200
        decision = case_client.post(
            f"/api/v1/test-cases/suggestions/{suggestion['id']}/decision",
            headers=headers,
            json={"action": "ACCEPT"},
        )
        assert decision.status_code == 409

    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 201
    suggestion = generated.json()["suggestions"][0]
    assert case_client.patch(
        f"/api/v1/test-cases/suggestions/{suggestion['id']}",
        headers=headers,
        json={
            "human_result": {
                **base_content,
                "cleanup": [_api_cleanup_with_secret(own["secret_id"])],
            }
        },
    ).status_code == 200
    accepted = case_client.post(
        f"/api/v1/test-cases/suggestions/{suggestion['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    assert accepted.status_code == 200
    accepted_case = case_client.get(
        f"/api/v1/test-cases/{accepted.json()['test_case_id']}", headers=headers
    )
    assert accepted_case.json()["current_version"]["content"]["cleanup"][0]["cleanup_type"] == "API"


def test_request_template_rejects_duplicate_headers(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    duplicate_headers = {
        **REQUEST_TEMPLATE,
        "headers": [
            {"name": "Authorization", "value": "Bearer first"},
            {"name": "authorization", "value": "Bearer second"},
        ],
    }
    response = case_client.post(
        "/api/v1/test-cases",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "content": {**CASES["cases"][0], "request": duplicate_headers},
        },
    )
    assert response.status_code == 422


def test_runtime_template_actions_and_extractors(case_client: TestClient) -> None:
    headers, _ = _scope(case_client)
    request_template = {
        **REQUEST_TEMPLATE,
        "url": "{{base_url}}/orders/{{order_no}}",
        "headers": [
            {"name": "Authorization", "value": "Bearer {{access_token}}"},
            {"name": "X-Disabled", "value": "{{missing}}", "enabled": False},
        ],
        "auth": {"type": "BEARER", "token": "{{access_token}}"},
        "body": {
            "type": "JSON",
            "content": {"amount": "{{amount}}", "owner": "{{user.name}}"},
        },
    }
    preview = case_client.post(
        "/api/v1/test-cases/runtime/preview",
        headers=headers,
        json={
            "request": request_template,
            "context": {
                "base_url": "https://api.example.test",
                "order_no": "A-100",
                "amount": 99.5,
                "user.name": "Alice",
                "seed_token": "token-123",
            },
            "pre_actions": [
                {"name": "access_token", "value": "{{seed_token}}"}
            ],
            "response": {
                "status_code": 201,
                "json_body": {"data": {"items": [{"id": 9527}]}},
                "headers": {"X-Trace-ID": "trace-001"},
                "cookies": {"session_id": "session-001"},
            },
            "extractors": [
                {"name": "order_id", "source": "JSONPATH", "expression": "$.data.items[0].id"},
                {"name": "trace_id", "source": "HEADER", "expression": "x-trace-id"},
                {"name": "session", "source": "COOKIE", "expression": "session_id"},
                {
                    "name": "optional",
                    "source": "JSONPATH",
                    "expression": "$.missing",
                    "required": False,
                    "default_value": "fallback",
                },
            ],
            "post_actions": [
                {"name": "result_key", "value": "order-{{order_id}}-{{response.status_code}}"}
            ],
        },
    )
    assert preview.status_code == 200
    result = preview.json()
    assert result["rendered_request"]["url"].endswith("/orders/A-100")
    assert result["rendered_request"]["body"]["content"]["amount"] == 99.5
    assert len(result["rendered_request"]["headers"]) == 1
    assert result["extracted"] == {
        "order_id": 9527,
        "trace_id": "trace-001",
        "session": "session-001",
        "optional": "fallback",
    }
    assert result["context"]["result_key"] == "order-9527-201"

    undefined = case_client.post(
        "/api/v1/test-cases/runtime/preview",
        headers=headers,
        json={"request": {**REQUEST_TEMPLATE, "url": "{{undefined}}/login"}},
    )
    assert undefined.status_code == 409


def test_scenario_versions_and_dsl_validation(case_client: TestClient) -> None:
    headers, ids = _scope(case_client)
    dsl = {
        "version": "1.0",
        "settings": {
            "initial_variables": ["base_url", "seed_token"],
            "cleanup_policy": "ALWAYS",
        },
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "set_token",
                "type": "SET_VARIABLE",
                "name": "设置令牌",
                "config": {"name": "token", "value": "{{seed_token}}"},
            },
            {
                "id": "login",
                "type": "HTTP",
                "name": "调用登录接口",
                "config": {"url": "{{base_url}}/login", "method": "POST"},
            },
            {
                "id": "extract_user",
                "type": "EXTRACT",
                "name": "提取用户 ID",
                "config": {
                    "name": "user_id",
                    "source": "JSONPATH",
                    "expression": "$.data.user_id",
                },
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
    }
    validation = case_client.post(
        "/api/v1/scenarios/validate",
        headers=headers,
        json={"project_id": ids["project_id"], "name": "登录场景", "dsl": dsl},
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    created = case_client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={"project_id": ids["project_id"], "name": "登录场景", "dsl": dsl},
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    assert created.json()["current_version"]["version_no"] == 1
    v2_dsl = {
        **dsl,
        "nodes": [
            *dsl["nodes"][:-1],
            {
                "id": "wait",
                "type": "WAIT",
                "name": "等待状态同步",
                "config": {"duration_ms": 500},
            },
            dsl["nodes"][-1],
        ],
    }
    version = case_client.post(
        f"/api/v1/scenarios/{scenario_id}/versions",
        headers=headers,
        json={"dsl": v2_dsl, "change_note": "增加等待节点"},
    )
    assert version.status_code == 201
    assert version.json()["version_no"] == 2
    versions = case_client.get(
        f"/api/v1/scenarios/{scenario_id}/versions", headers=headers
    )
    assert [item["version_no"] for item in versions.json()] == [2, 1]

    invalid_dsl = {
        **dsl,
        "settings": {"initial_variables": []},
        "nodes": [*dsl["nodes"][:-1], {"id": "start", "type": "END", "name": "结束"}],
    }
    invalid = case_client.post(
        "/api/v1/scenarios/validate",
        headers=headers,
        json={"project_id": ids["project_id"], "name": "非法场景", "dsl": invalid_dsl},
    )
    assert invalid.status_code == 200
    codes = {item["code"] for item in invalid.json()["issues"]}
    assert "DUPLICATE_NODE_ID" in codes
    assert "UNDEFINED_VARIABLE" in codes
    rejected = case_client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={"project_id": ids["project_id"], "name": "非法场景", "dsl": invalid_dsl},
    )
    assert rejected.status_code == 409


def test_scenario_cleanup_scope_is_checked_on_validate_create_and_version(
    case_client: TestClient,
) -> None:
    headers, ids = _scope(case_client)
    own = _cleanup_resources(case_client, headers, ids["project_id"], "SCNOWN")
    disabled = _cleanup_resources(case_client, headers, ids["project_id"], "SCNOFF")
    assert case_client.patch(
        f"/api/v1/database-connections/{disabled['connection_id']}",
        headers=headers,
        json={"enabled": False},
    ).status_code == 200
    other_project = case_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Scenario Other Project", "code": "SCENARIO_OTHER"},
    )
    assert other_project.status_code == 201
    other = _cleanup_resources(
        case_client, headers, other_project.json()["id"], "SCNOTHER"
    )

    def dsl(cleanup: dict[str, object], node_type: str) -> dict[str, object]:
        return {
            "version": "1.0",
            "settings": {
                "cleanup_policy": "ALWAYS",
                "initial_variables": ["resource_id"],
            },
            "nodes": [
                {"id": "start", "type": "START", "name": "开始"},
                {
                    "id": "cleanup",
                    "type": node_type,
                    "name": "清理",
                    "config": cleanup,
                },
                {"id": "end", "type": "END", "name": "结束"},
            ],
        }

    cross_project = dsl(
        _api_cleanup_with_secret(other["secret_id"]), "API_CLEANUP"
    )
    validation = case_client.post(
        "/api/v1/scenarios/validate",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "name": "跨项目 Cleanup",
            "dsl": cross_project,
        },
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is False
    assert any(item["code"] == "CLEANUP_SCOPE_INVALID" for item in validation.json()["issues"])

    disabled_sql = dsl(
        _sql_cleanup_with_connection(disabled["connection_id"]), "SQL_CLEANUP"
    )
    rejected = case_client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "name": "停用连接 Cleanup",
            "dsl": disabled_sql,
        },
    )
    assert rejected.status_code == 409

    created = case_client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "name": "有效 Cleanup",
            "dsl": dsl(_api_cleanup_with_secret(own["secret_id"]), "API_CLEANUP"),
        },
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    invalid_version = case_client.post(
        f"/api/v1/scenarios/{scenario_id}/versions",
        headers=headers,
        json={
            "dsl": cross_project,
            "change_note": "拒绝跨项目 Secret",
        },
    )
    assert invalid_version.status_code == 409
    valid_version = case_client.post(
        f"/api/v1/scenarios/{scenario_id}/versions",
        headers=headers,
        json={
            "dsl": dsl(
                _sql_cleanup_with_connection(own["connection_id"]), "SQL_CLEANUP"
            ),
            "change_note": "切换 SQL Cleanup",
        },
    )
    assert valid_version.status_code == 201


def test_scenario_if_loop_wait_execution_preview(case_client: TestClient) -> None:
    headers, _ = _scope(case_client)
    dsl = {
        "version": "1.0",
        "settings": {
            "initial_variables": ["status", "items"],
            "max_loop_iterations": 10,
        },
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "check_status",
                "type": "IF",
                "name": "检查状态",
                "config": {"condition": "{{status}} == \"READY\""},
            },
            {
                "id": "ready_branch",
                "type": "SET_VARIABLE",
                "name": "记录就绪",
                "parent_id": "check_status",
                "config": {"name": "selected_branch", "value": "ready"},
            },
            {
                "id": "else_branch",
                "type": "ELSE",
                "name": "其他状态",
                "parent_id": "check_status",
            },
            {
                "id": "not_ready",
                "type": "SET_VARIABLE",
                "name": "记录未就绪",
                "parent_id": "else_branch",
                "config": {"name": "selected_branch", "value": "not-ready"},
            },
            {
                "id": "item_loop",
                "type": "LOOP",
                "name": "遍历项目",
                "config": {
                    "items": "{{items}}",
                    "item_variable": "current_item",
                    "index_variable": "current_index",
                },
            },
            {
                "id": "remember_item",
                "type": "SET_VARIABLE",
                "name": "记录项目",
                "parent_id": "item_loop",
                "config": {"name": "last_item", "value": "{{current_item}}"},
            },
            {
                "id": "wait",
                "type": "WAIT",
                "name": "等待同步",
                "config": {"duration_ms": 250},
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
    }
    response = case_client.post(
        "/api/v1/scenarios/execute-preview",
        headers=headers,
        json={"dsl": dsl, "context": {"status": "READY", "items": ["a", "b", "c"]}},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["context"]["selected_branch"] == "ready"
    assert result["context"]["last_item"] == "c"
    assert "not-ready" not in result["context"].values()
    loop_traces = [item for item in result["traces"] if item["node_id"] == "remember_item"]
    assert [item["iteration_path"] for item in loop_traces] == [[0], [1], [2]]
    wait_trace = next(item for item in result["traces"] if item["node_id"] == "wait")
    assert wait_trace["detail"] == {"duration_ms": 250, "simulated": True}


def test_scenario_cleanup_preview_uses_shared_policy_and_redacts_values(
    case_client: TestClient,
) -> None:
    headers, _ = _scope(case_client)
    cleanup = {
        "cleanup_type": "API",
        "method": "DELETE",
        "url": "{{base_url}}/orders/{{resource_id}}",
        "query_params": [{"name": "id", "value": "{{resource_id}}"}],
        "headers": [{"name": "X-Trace", "value": "{{token}}"}],
        "cookies": [{"name": "trace_id", "value": "{{cookie_id}}"}],
        "body": {
            "type": "JSON",
            "content": {"id": "{{resource_id}}", "amount": 1},
        },
    }
    dsl = {
        "version": "1.0",
        "settings": {
            "cleanup_policy": "ON_FAILURE",
            "initial_variables": ["base_url", "resource_id", "token", "cookie_id"],
        },
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "cleanup",
                "type": "API_CLEANUP",
                "name": "API 清理",
                "config": cleanup,
            },
            {
                "id": "disabled",
                "type": "API_CLEANUP",
                "name": "禁用清理",
                "config": {**cleanup, "enabled": False, "policy": "ALWAYS"},
            },
            {
                "id": "never",
                "type": "API_CLEANUP",
                "name": "永不清理",
                "config": {**cleanup, "policy": "NEVER"},
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
    }

    def preview(outcome: str) -> dict[str, object]:
        response = case_client.post(
            "/api/v1/scenarios/execute-preview",
            headers=headers,
            json={
                "dsl": dsl,
                "context": {
                    "base_url": "https://api.example.test",
                    "resource_id": "order-1",
                    "token": "token-value",
                    "cookie_id": "cookie-value",
                },
                "cleanup_outcome": outcome,
            },
        )
        assert response.status_code == 200, response.text
        return {item["node_id"]: item for item in response.json()["traces"]}

    success = preview("SUCCESS")
    assert success["cleanup"]["status"] == "SKIPPED"
    assert success["cleanup"]["detail"]["reason"] == "policy ON_FAILURE does not match SUCCESS"
    assert success["disabled"]["status"] == "SKIPPED"
    assert success["disabled"]["detail"]["reason"] == "disabled"
    assert success["never"]["status"] == "SKIPPED"

    for outcome in ("FAILURE", "CANCELLED", "TIMEOUT"):
        traces = preview(outcome)
        detail = traces["cleanup"]["detail"]
        assert traces["cleanup"]["status"] == "PASSED"
        assert detail["cleanup_type"] == "API"
        assert detail["cleanup_action"] == "CLEAN"
        assert detail["execution_boundary"] == "render_validate_plan_only"
        assert detail["simulated"] is True
        assert detail["persisted"] is False
        assert detail["request"]["url"] == "https://api.example.test/orders/order-1"
        assert detail["request"]["query_params"][0]["value"] == "order-1"
        assert detail["request"]["headers"][0]["value"] == "<redacted>"
        assert detail["request"]["cookies"][0]["value"] == "cookie-value"
        assert detail["request"]["body"]["content"]["id"] == "order-1"
        assert traces["disabled"]["status"] == "SKIPPED"
        assert traces["never"]["status"] == "SKIPPED"


def test_scenario_sql_and_restricted_python_nodes(
    case_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(case_client)
    environment = case_client.post(
        "/api/v1/environments",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "name": "SQL 节点环境",
            "code": "sql_node",
            "base_url": "https://example.test",
        },
    ).json()
    secret = case_client.post(
        "/api/v1/secrets",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "environment_id": environment["id"],
            "name": "sql_password",
            "secret_type": "DB_PASSWORD",
            "value": "not-returned",
        },
    ).json()
    connection_config = case_client.post(
        "/api/v1/database-connections",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "environment_id": environment["id"],
            "name": "业务库",
            "host": "mock-db",
            "port": 3306,
            "database_name": "demo",
            "username": "tester",
            "password_secret_id": secret["id"],
        },
    ).json()

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, statement: str, parameters: dict) -> int:
            self.statement = statement
            self.parameters = parameters
            return 2 if statement.upper().startswith("SELECT") else 1

        def fetchmany(self, size: int) -> list[dict]:
            return [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}][:size]

    class FakeConnection:
        def __init__(self) -> None:
            self.rolled_back = False
            self.closed = False

        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    fake_connection = FakeConnection()
    from app.modules.scenarios import executor

    monkeypatch.setattr(executor.pymysql, "connect", lambda **kwargs: fake_connection)
    dsl = {
        "version": "1.0",
        "settings": {"initial_variables": ["tenant"]},
        "nodes": [
            {"id": "start", "type": "START", "name": "开始"},
            {
                "id": "query",
                "type": "SQL_QUERY",
                "name": "查询用户",
                "config": {
                    "connection_id": connection_config["id"],
                    "sql": "SELECT id, name FROM users WHERE tenant=%(tenant)s",
                    "params": {"tenant": "{{tenant}}"},
                    "result_variable": "rows",
                    "max_rows": 10,
                },
            },
            {
                "id": "update",
                "type": "SQL_EXECUTE",
                "name": "更新标记",
                "config": {
                    "connection_id": connection_config["id"],
                    "sql": "UPDATE users SET checked=1 WHERE tenant=%(tenant)s",
                    "params": {"tenant": "{{tenant}}"},
                    "result_variable": "updated",
                },
            },
            {
                "id": "cleanup",
                "type": "SQL_CLEANUP",
                "name": "清理用户",
                "config": {
                    "cleanup_type": "SQL",
                    "connection_id": connection_config["id"],
                    "sql": "DELETE FROM users WHERE id=%(resource_id)s",
                    "params": {},
                    "resource_id_param": "resource_id",
                },
            },
            {
                "id": "script",
                "type": "PYTHON_SCRIPT",
                "name": "整理结果",
                "config": {
                    "script": (
                        'context["total_rows"] = len(context["rows"])\n'
                        'if context["total_rows"] > 1:\n'
                        '    context["summary"] = "multiple"'
                    )
                },
            },
            {"id": "end", "type": "END", "name": "结束"},
        ],
    }
    response = case_client.post(
        "/api/v1/scenarios/execute-preview",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "dsl": dsl,
            "context": {"tenant": "T1", "resource_id": "user-1"},
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["context"]["total_rows"] == 2
    assert result["context"]["summary"] == "multiple"
    assert result["context"]["updated"] == 1
    assert fake_connection.rolled_back is True
    assert fake_connection.closed is True
    sql_traces = [item for item in result["traces"] if item["node_type"].startswith("SQL_")]
    assert all(item["detail"]["preview_rolled_back"] is True for item in sql_traces)
    cleanup_trace = next(item for item in result["traces"] if item["node_id"] == "cleanup")
    assert cleanup_trace["detail"] == {
        "affected_rows": 1,
        "result_variable": "affected_rows",
        "preview_rolled_back": True,
        "cleanup_type": "SQL",
        "cleanup_action": "CLEAN",
        "policy": "ALWAYS",
        "cleanup_outcome": "SUCCESS",
        "simulated": True,
        "persisted": False,
    }

    forbidden_script = {
        **dsl,
        "nodes": [
            dsl["nodes"][0],
            {
                "id": "unsafe",
                "type": "PYTHON_SCRIPT",
                "name": "禁止导入",
                "config": {"script": "import os"},
            },
            dsl["nodes"][-1],
        ],
    }
    rejected = case_client.post(
        "/api/v1/scenarios/execute-preview",
        headers=headers,
        json={"project_id": ids["project_id"], "dsl": forbidden_script},
    )
    assert rejected.status_code == 409
