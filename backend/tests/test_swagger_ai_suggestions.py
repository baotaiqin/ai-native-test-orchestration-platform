import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_runs import FakeRunHeartbeatStore, _headers
from test_runs import run_context as _shared_run_context  # noqa: F401

from app.core.exceptions import ResourceConflictError
from app.modules.ai_gateway.schemas import AiGenerateRequest, AiGenerateResponse
from app.modules.api_definitions.models import (
    ApiDefinition,
    ApiDefinitionImport,
    ApiDesignSuggestion,
)
from app.modules.api_definitions.schemas import ApiScenarioRecommendation
from app.modules.api_definitions.service import (
    _requirement_source_snapshot,
    _validate_scenario_result,
)
from app.modules.prompt_center.models import AiCallLog
from app.modules.requirements.models import (
    Requirement,
    RequirementDocumentVersion,
    RequirementVersion,
)
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.test_cases.models import TestCase, TestCaseVersion


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260911_0054_swagger_ai_design_suggestions.py"
    )
    spec = importlib.util.spec_from_file_location("swagger_ai_design_suggestions", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_async_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260912_0062_async_api_design_suggestions.py"
    )
    spec = importlib.util.spec_from_file_location("async_api_design_suggestions", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_plan_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260912_0063_api_scenario_plans.py"
    )
    spec = importlib.util.spec_from_file_location("api_scenario_plans", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class _RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_swagger_ai_migration_is_linear_complete_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert migration.revision == "20260911_0054"
    assert migration.down_revision == "20260911_0053"
    assert any(
        name == "add_column"
        and args[0] == "ai_case_suggestions"
        and args[1].name == "linked_requirement_ids"
        for name, args, _ in operations.calls
    )
    create = next(
        args for name, args, _ in operations.calls
        if name == "create_table" and args[0] == "api_design_suggestions"
    )
    columns = {item.name: item for item in create[1:] if hasattr(item, "name")}
    assert columns["created_at"].server_default is not None
    assert {"source_snapshot", "human_result", "created_test_case_ids"} <= columns.keys()
    operations.calls.clear()
    migration.downgrade()
    assert ("drop_table", ("api_design_suggestions",), {}) in operations.calls
    assert any(
        name == "drop_column"
        and args == ("ai_case_suggestions", "linked_requirement_ids")
        for name, args, _ in operations.calls
    )


def test_async_swagger_ai_migration_is_linear_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_async_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260912_0062"
    assert migration.down_revision == "20260912_0061"
    added = {
        args[1].name
        for name, args, _ in operations.calls
        if name == "add_column" and args[0] == "api_design_suggestions"
    }
    assert {
        "prompt_id", "generation_status", "started_at", "completed_at", "error_message",
    } <= added
    assert any(name == "create_check_constraint" for name, _, _ in operations.calls)
    operations.calls.clear()
    migration.downgrade()
    dropped = {
        args[1]
        for name, args, _ in operations.calls
        if name == "drop_column" and args[0] == "api_design_suggestions"
    }
    assert "generation_status" in dropped


def test_api_scenario_plan_migration_is_linear_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_plan_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "20260912_0063"
    assert migration.down_revision == "20260912_0062"
    created_tables = {
        args[0] for name, args, _ in operations.calls if name == "create_table"
    }
    assert {"api_scenario_plans", "api_scenario_plan_items"} <= created_tables
    assert any(
        name == "add_column"
        and args[0] == "api_design_suggestions"
        and args[1].name == "plan_item_id"
        for name, args, _ in operations.calls
    )

    operations.calls.clear()
    migration.downgrade()
    assert ("drop_table", ("api_scenario_plan_items",), {}) in operations.calls
    assert ("drop_table", ("api_scenario_plans",), {}) in operations.calls

@pytest.fixture(name="swagger_context")
def imported_run_context(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_run_context")


def _case_result(title: str = "创建资源") -> dict[str, Any]:
    return {
        "cases": [
            {
                "title": title,
                "case_type": "API",
                "priority": "P1",
                "preconditions": [],
                "steps": [
                    {
                        "order": 1,
                        "action": "创建资源",
                        "expected": "返回 201",
                    }
                ],
                "test_data": {},
                "expected_result": "资源创建成功",
                "tags": ["swagger"],
                "confidence": 0.92,
                "request": {
                    "method": "POST",
                    "url": "{{base_url}}/api/resources",
                    "query_params": [],
                    "headers": [],
                    "cookies": [],
                    "body": {"type": "JSON", "content": {"name": "demo"}},
                    "auth": {"type": "NONE"},
                    "timeout_ms": 10000,
                    "follow_redirects": False,
                },
            }
        ]
    }


def _scenario_result() -> dict[str, Any]:
    return {
        "name": "资源创建链路",
        "rationale": "登录后的资源创建与查询主链",
        "confidence": 0.9,
        "dsl": {
            "version": "1.0",
            "nodes": [
                {"id": "start", "type": "START", "name": "开始", "description": "场景开始"},
                {
                    "id": "create",
                    "type": "HTTP",
                    "name": "创建资源",
                    "description": "调用资源创建接口并获得新资源",
                    "config": {
                        "method": "POST",
                        "url": "{{base_url}}/api/resources",
                    },
                },
                {"id": "end", "type": "END", "name": "结束", "description": "场景结束"},
            ],
        },
    }


def test_api_scenario_recommendation_rejects_legacy_steps_shape() -> None:
    legacy = {
        "name": "旧场景结构",
        "rationale": "验证旧 dsl.steps 不会再通过输出 Schema",
        "confidence": 0.8,
        "dsl": {"steps": []},
    }

    with pytest.raises(ValueError):
        ApiScenarioRecommendation.model_validate(legacy)

    schema = ApiScenarioRecommendation.model_json_schema()
    dsl_schema = schema["$defs"]["ScenarioDsl"]
    assert {"version", "nodes", "settings"} <= dsl_schema["properties"].keys()


def test_ai_scenario_rejects_plain_sensitive_request_values() -> None:
    snapshot = {
        "definitions": [{"id": 7, "method": "POST", "path": "/api/login"}]
    }
    result = {
        "name": "登录场景",
        "rationale": "验证 AI 不能把 OpenAPI 示例密码保存到场景中",
        "confidence": 0.9,
        "dsl": {
            "version": "1.0",
            "nodes": [
                {"id": "start", "type": "START", "name": "开始", "description": "开始登录"},
                {
                    "id": "login",
                    "type": "HTTP",
                    "name": "登录系统",
                    "description": "调用登录接口获取令牌",
                    "config": {
                        "method": "POST",
                        "url": "{{base_url}}/api/login",
                        "body": {
                            "type": "JSON",
                            "content": {"username": "admin", "password": "password"},
                        },
                    },
                },
                {"id": "end", "type": "END", "name": "结束", "description": "结束登录"},
            ],
            "settings": {"initial_variables": ["base_url"]},
        },
    }

    with pytest.raises(ResourceConflictError, match="明文或未受控敏感值"):
        _validate_scenario_result(snapshot, result)

    result["dsl"]["nodes"][1]["config"]["body"]["content"]["password"] = (
        "{{secret.login-password}}"
    )
    validated = _validate_scenario_result(snapshot, result)
    assert validated["dsl"]["nodes"][1]["config"]["body"]["content"]["password"] == (
        "{{secret.login-password}}"
    )


def test_scenario_domain_validation_matches_runtime_path_and_rejects_dollar_vars() -> None:
    snapshot = {
        "definitions": [
            {"id": 7, "method": "DELETE", "path": "/api/orders/{order_id}"}
        ]
    }
    result = {
        "name": "订单清理场景",
        "rationale": "验证运行时路径变量与固定 OpenAPI 契约匹配",
        "confidence": 0.9,
        "dsl": {
            "version": "1.0",
            "nodes": [
                {"id": "start", "type": "START", "name": "开始", "description": "场景开始"},
                {
                    "id": "cleanup",
                    "type": "API_CLEANUP",
                    "name": "清理订单",
                    "description": "删除测试订单并恢复库存",
                    "config": {
                        "cleanup_type": "API",
                        "method": "DELETE",
                        "url": "{{base_url}}/api/orders/{{order_id}}",
                    },
                },
                {"id": "end", "type": "END", "name": "结束", "description": "场景结束"},
            ],
            "settings": {"initial_variables": ["base_url", "order_id"]},
        },
    }

    validated = _validate_scenario_result(snapshot, result)
    assert validated["dsl"]["nodes"][1]["type"] == "API_CLEANUP"

    invalid = json.loads(json.dumps(result))
    invalid["dsl"]["nodes"][1]["config"]["url"] = (
        "{{base_url}}/api/orders/${order_id}"
    )
    with pytest.raises(ResourceConflictError, match="不能使用"):
        _validate_scenario_result(snapshot, invalid)


def test_scenario_normalizes_unambiguous_model_aliases() -> None:
    snapshot = {
        "definitions": [
            {"id": 1, "method": "POST", "path": "/api/login"},
            {"id": 2, "method": "DELETE", "path": "/api/orders/{order_id}"},
        ]
    }
    result = {
        "name": "登录并清理订单",
        "rationale": "覆盖模型常见但可无歧义转换的字段表达",
        "confidence": 0.9,
        "dsl": {
            "version": "1.0",
            "nodes": [
                {
                    "id": "START",
                    "type": "START",
                    "name": "开始",
                    "description": "场景开始",
                    "failure_policy": "abort",
                    "timeout_ms": 0,
                },
                {
                    "id": "LOGIN",
                    "type": "HTTP",
                    "name": "登录",
                    "description": "调用登录接口获取认证响应",
                    "parent_id": "START",
                    "failure_policy": "abort",
                    "timeout_ms": 5000,
                    "config": {
                        "method": "post",
                        "url": "{{base_url}}/api/login",
                        "body": {"username": "demo"},
                    },
                },
                {
                    "id": "TOKEN",
                    "type": "EXTRACT",
                    "name": "提取令牌",
                    "description": "从登录响应提取 token 变量",
                    "parent_id": "LOGIN",
                    "failure_policy": "abort",
                    "config": {
                        "name": "token",
                        "source": "JSONPATH",
                        "expression": "$.token",
                    },
                },
                {
                    "id": "CLEANUP",
                    "type": "API_CLEANUP",
                    "name": "清理订单",
                    "description": "删除测试订单并恢复库存",
                    "parent_id": "TOKEN",
                    "failure_policy": "continue",
                    "config": {
                        "method": "delete",
                        "url": "{{base_url}}/api/orders/{{order_id}}",
                        "headers": {"Authorization": "Bearer {{token}}"},
                    },
                },
                {
                    "id": "END",
                    "type": "END",
                    "name": "结束",
                    "description": "场景结束",
                    "parent_id": "CLEANUP",
                    "failure_policy": "abort",
                    "timeout_ms": 0,
                },
            ],
            "settings": {
                "initial_variables": ["base_url", "order_id"],
            },
        },
    }

    validated = _validate_scenario_result(snapshot, result)
    nodes = validated["dsl"]["nodes"]
    assert nodes[0]["timeout_ms"] == 30000
    assert nodes[1]["failure_policy"] is None
    assert nodes[1]["parent_id"] is None
    assert nodes[1]["config"]["body"] == {
        "type": "JSON",
        "content": {"username": "demo"},
    }
    assert nodes[3]["failure_policy"] == "CONTINUE"
    assert nodes[3]["config"]["headers"] == []
    assert nodes[3]["config"]["auth"] == {
        "type": "BEARER",
        "credential_ref": "{{token}}",
    }


def test_swagger_ai_case_and_scenario_review_create_pinned_drafts(
    swagger_context: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory, _, ids = swagger_context
    headers = _headers(client)
    with factory() as session:
        imported = ApiDefinitionImport(
            project_id=ids["project_id"],
            source_filename="demo-openapi.json",
            version_no=1,
            spec_version="3.0.3",
            title="Demo API",
            content_hash="a" * 64,
            raw_content="{}",
            imported_by="dev-admin",
        )
        session.add(imported)
        session.flush()
        definition = ApiDefinition(
            project_id=ids["project_id"],
            import_id=imported.id,
            name="Create resource",
            method="POST",
            path="/api/resources",
            operation_id="createResource",
            parameters=[],
            request_schema={"type": "object"},
            response_schema={"201": {"type": "object"}},
            auth_info={},
            tags=["resources"],
            source="SWAGGER",
            source_filename=imported.source_filename,
            source_version=1,
            contract_hash="b" * 64,
            status="ACTIVE",
            created_by="dev-admin",
        )
        session.add(definition)
        requirement = Requirement(
            project_id=ids["project_id"],
            parent_id=None,
            code="REQ-SWAGGER-SCENARIO",
            title="资源业务完整需求",
            type="SECTION",
            order_index=0,
            status="ACTIVE",
            created_by="dev-admin",
        )
        session.add(requirement)
        session.flush()
        requirement_version = RequirementVersion(
            requirement_id=requirement.id,
            version_no=1,
            markdown_content="使用测试账号 demo 登录后创建资源。",
            content_hash="c" * 64,
            source_type="MARKDOWN",
            source_filename="requirements.md",
            created_by="dev-admin",
        )
        session.add(requirement_version)
        session.flush()
        requirement.current_version_id = requirement_version.id
        document_version = RequirementDocumentVersion(
            project_id=ids["project_id"],
            version_no=1,
            snapshot=[{
                "requirement_id": requirement.id,
                "code": requirement.code,
                "parent_id": None,
                "outline_number": "1",
                "title": requirement.title,
                "type": requirement.type,
                "order_index": 0,
                "markdown_content": requirement_version.markdown_content,
                "technical_version_id": requirement_version.id,
                "technical_version_no": 1,
            }],
            content_hash="d" * 64,
            source_type="MARKDOWN",
            source_filename="requirements.md",
            change_summary="确认完整需求",
            created_by="dev-admin",
        )
        session.add(document_version)
        session.commit()
        import_id = imported.id
        definition_id = definition.id
        requirement_id = requirement.id
        document_version_id = document_version.id

    with factory() as session:
        default_requirement = _requirement_source_snapshot(
            session, ids["project_id"], None
        )
    assert default_requirement["requirement_id"] == requirement_id
    assert default_requirement["document_version_id"] == document_version_id

    responses = [_case_result(), _scenario_result()]

    def fake_generate(
        session: Session,
        _user: object,
        payload: AiGenerateRequest,
        *,
        result_validator: Any = None,
    ) -> AiGenerateResponse:
        result = responses.pop(0)
        task_type = payload.task_type.value
        assert result_validator is not None
        assert result_validator(result) is True
        if task_type == "API_SCENARIO_GENERATE":
            assert "禁止输出旧版 dsl.steps" in payload.variables["additional_instructions"]
            assert "测试账号 demo" in payload.variables["requirement_scope"]
        call = AiCallLog(
            project_id=ids["project_id"],
            task_type=task_type,
            entity_type="API_DEFINITION_IMPORT",
            entity_id=str(import_id),
            model_config_id=1,
            actual_model="synthetic-swagger-model",
            prompt_version_id=11 if task_type == "API_CASE_GENERATE" else 12,
            output_schema_id=21 if task_type == "API_CASE_GENERATE" else 22,
            input_token=10,
            output_token=20,
            total_token=30,
            estimated_cost=0,
            latency_ms=5,
            success=True,
            fallback_used=False,
            retry_count=0,
            repair_used=False,
            raw_response=json.dumps(result, ensure_ascii=False) + " token=must-mask",
            parsed_result=result,
            validation_errors=[],
        )
        session.add(call)
        session.commit()
        session.refresh(call)
        return AiGenerateResponse(
            ai_call_id=call.id,
            success=True,
            content=json.dumps(result),
            parsed_result=result,
            actual_model=call.actual_model,
            fallback_used=False,
            repair_used=False,
            input_token=10,
            output_token=20,
            total_token=30,
            estimated_cost=0,
            latency_ms=5,
            response_id=None,
        )

    monkeypatch.setattr("app.modules.api_definitions.service.generate", fake_generate)

    created = client.post(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
        json={"kind": "CASE_SET", "prompt_id": 11},
    )
    assert created.status_code == 202, created.text
    assert created.json()["generation_status"] == "QUEUED"
    history = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
    )
    assert history.status_code == 200
    case_suggestion = history.json()["items"][0]
    assert case_suggestion["status"] == "DRAFT"
    assert case_suggestion["generation_status"] == "SUCCEEDED"
    assert "source_snapshot" not in case_suggestion
    assert "must-mask" not in case_suggestion["raw_response"]

    edited_result = _case_result("人工编辑后的资源创建")
    edited = client.patch(
        f"/api/v1/api-definitions/ai-suggestions/{case_suggestion['id']}",
        headers=headers,
        json={"human_result": edited_result, "decision_note": "人工核对契约"},
    )
    assert edited.status_code == 200, edited.text
    accepted = client.post(
        f"/api/v1/api-definitions/ai-suggestions/{case_suggestion['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_case = accepted.json()
    assert accepted_case["status"] == "ACCEPTED"
    assert len(accepted_case["created_test_case_ids"]) == 1
    repeated = client.post(
        f"/api/v1/api-definitions/ai-suggestions/{case_suggestion['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True

    scenario_created = client.post(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
        json={
            "kind": "SCENARIO",
            "prompt_id": 12,
            "requirement_id": requirement_id,
        },
    )
    assert scenario_created.status_code == 202, scenario_created.text
    assert scenario_created.json()["generation_status"] == "QUEUED"
    assert scenario_created.json()["requirement_source"] == {
        "id": requirement_id,
        "code": "REQ-SWAGGER-SCENARIO",
        "title": "资源业务完整需求",
        "document_version_id": document_version_id,
        "document_version_no": 1,
        "scope_count": 1,
        "scope_mode": "SUBTREE",
    }
    scenario_history = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
    )
    generated_scenario = scenario_history.json()["items"][0]
    assert generated_scenario["generation_status"] == "SUCCEEDED"
    scenario_accepted = client.post(
        f"/api/v1/api-definitions/ai-suggestions/{generated_scenario['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT", "decision_note": "人工确认流程"},
    )
    assert scenario_accepted.status_code == 200, scenario_accepted.text
    accepted_scenario = scenario_accepted.json()
    scenario_id = accepted_scenario["created_scenario_id"]
    assert scenario_id is not None
    assert accepted_scenario["created_scenario"] == {
        "id": scenario_id,
        "code": "SC-00001",
        "name": "资源创建链路",
        "status": "DRAFT",
        "version_no": 1,
    }
    baseline = client.get(
        f"/api/v1/scenarios/{scenario_id}/ai-baseline", headers=headers
    )
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["suggestion_id"] == generated_scenario["id"]
    assert baseline.json()["dsl"]["nodes"][1]["description"] == (
        "调用资源创建接口并获得新资源"
    )
    edited_dsl = baseline.json()["dsl"]
    edited_dsl["nodes"][1]["name"] = "人工修改后的资源创建"
    new_version = client.post(
        f"/api/v1/scenarios/{scenario_id}/versions",
        headers=headers,
        json={"dsl": edited_dsl, "change_note": "测试恢复原始 AI 编排"},
    )
    assert new_version.status_code == 201, new_version.text
    baseline_again = client.get(
        f"/api/v1/scenarios/{scenario_id}/ai-baseline", headers=headers
    )
    assert baseline_again.status_code == 200
    assert baseline_again.json()["dsl"]["nodes"][1]["name"] == "创建资源"

    history = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
    )
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert history.json()["items"][0]["created_scenario"]["code"] == "SC-00001"

    with factory() as session:
        case = session.get(TestCase, accepted_case["created_test_case_ids"][0])
        assert case is not None
        assert case.api_definition_id == definition_id
        assert case.source == "AI"
        version = session.get(TestCaseVersion, case.current_version_id)
        assert version is not None
        assert version.content["title"] == "人工编辑后的资源创建"
        scenario = session.get(Scenario, scenario_id)
        assert scenario is not None and scenario.status == "DRAFT"
        scenario_version = session.get(ScenarioVersion, scenario.current_version_id)
        assert scenario_version is not None
        assert scenario_version.dsl["nodes"][1]["id"] == "create"
        records = list(session.scalars(select(ApiDesignSuggestion)).all())
        assert [item.status for item in records] == ["ACCEPTED", "ACCEPTED"]


def test_scenario_plan_then_generates_selected_candidates_independently(
    swagger_context: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory, _, ids = swagger_context
    headers = _headers(client)
    with factory() as session:
        imported = ApiDefinitionImport(
            project_id=ids["project_id"],
            source_filename="planned-openapi.json",
            version_no=2,
            spec_version="3.0.3",
            title="Planned API",
            content_hash="e" * 64,
            raw_content="{}",
            imported_by="dev-admin",
        )
        session.add(imported)
        session.flush()
        definition = ApiDefinition(
            project_id=ids["project_id"],
            import_id=imported.id,
            name="Create resource",
            method="POST",
            path="/api/resources",
            operation_id="createResource",
            parameters=[],
            request_schema={"type": "object"},
            response_schema={"201": {"type": "object"}},
            auth_info={},
            tags=["resources"],
            source="SWAGGER",
            source_filename=imported.source_filename,
            source_version=2,
            contract_hash="f" * 64,
            status="ACTIVE",
            created_by="dev-admin",
        )
        session.add(definition)
        requirement = Requirement(
            project_id=ids["project_id"],
            parent_id=None,
            code="REQ-PLAN",
            title="资源业务",
            type="SECTION",
            order_index=0,
            status="ACTIVE",
            created_by="dev-admin",
        )
        session.add(requirement)
        session.flush()
        requirement_version = RequirementVersion(
            requirement_id=requirement.id,
            version_no=1,
            markdown_content="创建资源并验证结果。",
            content_hash="1" * 64,
            source_type="MARKDOWN",
            source_filename="requirements.md",
            created_by="dev-admin",
        )
        session.add(requirement_version)
        session.flush()
        requirement.current_version_id = requirement_version.id
        document_version = RequirementDocumentVersion(
            project_id=ids["project_id"],
            version_no=2,
            snapshot=[{
                "requirement_id": requirement.id,
                "code": requirement.code,
                "parent_id": None,
                "outline_number": "1",
                "title": requirement.title,
                "type": requirement.type,
                "order_index": 0,
                "markdown_content": requirement_version.markdown_content,
                "technical_version_id": requirement_version.id,
                "technical_version_no": 1,
            }],
            content_hash="2" * 64,
            source_type="MARKDOWN",
            source_filename="requirements.md",
            change_summary="规划测试",
            created_by="dev-admin",
        )
        session.add(document_version)
        session.commit()
        import_id = imported.id
        definition_id = definition.id
        requirement_id = requirement.id
        document_version_id = document_version.id

    plan_result = {
        "summary": "规划资源创建的正常和异常场景",
        "candidates": [
            {
                "candidate_key": "create-resource",
                "name": "资源创建成功",
                "objective": "验证资源创建主链路",
                "category": "POSITIVE",
                "priority": "P0",
                "rationale": "覆盖核心创建能力",
                "requirement_ids": [requirement_id],
                "api_definition_ids": [definition_id],
                "api_flow": ["POST /api/resources"],
                "preconditions": ["服务可用"],
                "expected_outcomes": ["返回 201"],
                "cleanup_required": False,
            }
        ],
        "uncovered_requirement_ids": [],
    }
    responses = [plan_result, _scenario_result()]

    def fake_generate(
        session: Session,
        _user: object,
        payload: AiGenerateRequest,
        *,
        result_validator: Any = None,
    ) -> AiGenerateResponse:
        result = responses.pop(0)
        assert result_validator is not None and result_validator(result) is True
        task_type = payload.task_type.value
        if task_type == "API_SCENARIO_GENERATE":
            assert "本次只生成下面这个已经过规划的候选场景" in (
                payload.variables["additional_instructions"]
            )
        call = AiCallLog(
            project_id=ids["project_id"],
            task_type=task_type,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            model_config_id=1,
            actual_model="synthetic-plan-model",
            prompt_version_id=12,
            output_schema_id=22,
            input_token=10,
            output_token=20,
            total_token=30,
            estimated_cost=0,
            latency_ms=5,
            success=True,
            fallback_used=False,
            retry_count=0,
            repair_used=False,
            raw_response=json.dumps(result, ensure_ascii=False),
            parsed_result=result,
            validation_errors=[],
        )
        session.add(call)
        session.commit()
        session.refresh(call)
        return AiGenerateResponse(
            ai_call_id=call.id,
            success=True,
            content=json.dumps(result),
            parsed_result=result,
            actual_model=call.actual_model,
            fallback_used=False,
            repair_used=False,
            input_token=10,
            output_token=20,
            total_token=30,
            estimated_cost=0,
            latency_ms=5,
            response_id=None,
        )

    monkeypatch.setattr("app.modules.api_definitions.service.generate", fake_generate)

    created = client.post(
        f"/api/v1/api-definitions/imports/{import_id}/scenario-plans",
        headers=headers,
        json={
            "prompt_id": 12,
            "requirement_document_version_id": document_version_id,
        },
    )
    assert created.status_code == 202, created.text
    plans = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/scenario-plans",
        headers=headers,
    )
    assert plans.status_code == 200, plans.text
    plan = plans.json()["items"][0]
    assert plan["generation_status"] == "SUCCEEDED"
    assert plan["requirement_source"]["scope_mode"] == "DOCUMENT"
    assert plan["requirement_source"]["id"] is None
    assert len(plan["items"]) == 1

    generated = client.post(
        f"/api/v1/api-definitions/scenario-plans/{plan['id']}/generate",
        headers=headers,
        json={"item_ids": [plan["items"][0]["id"]], "prompt_id": 12},
    )
    assert generated.status_code == 202, generated.text
    assert len(generated.json()["suggestions"]) == 1
    suggestions = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
    ).json()["items"]
    assert suggestions[0]["generation_status"] == "SUCCEEDED"
    assert suggestions[0]["plan_item_id"] == plan["items"][0]["id"]
    assert responses == []


def test_swagger_ai_suggestion_rejects_unpinned_request(
    swagger_context: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory, _, ids = swagger_context
    headers = _headers(client)
    with factory() as session:
        imported = ApiDefinitionImport(
            project_id=ids["project_id"],
            source_filename="safe.json",
            version_no=1,
            spec_version="3.0.3",
            content_hash="c" * 64,
            raw_content="{}",
            imported_by="dev-admin",
        )
        session.add(imported)
        session.flush()
        session.add(
            ApiDefinition(
                project_id=ids["project_id"],
                import_id=imported.id,
                name="Safe",
                method="GET",
                path="/safe",
                parameters=[],
                response_schema={},
                auth_info={},
                tags=[],
                source="SWAGGER",
                source_filename="safe.json",
                source_version=1,
                contract_hash="d" * 64,
                status="ACTIVE",
                created_by="dev-admin",
            )
        )
        session.commit()
        import_id = imported.id

    invalid = _case_result()

    def fake_generate(
        session: Session,
        _user: object,
        _payload: object,
        *,
        result_validator: Any = None,
    ) -> AiGenerateResponse:
        assert result_validator is not None
        assert result_validator(invalid) is False
        call = AiCallLog(
            project_id=ids["project_id"],
            task_type="API_CASE_GENERATE",
            entity_type="API_DEFINITION_IMPORT",
            entity_id=str(import_id),
            model_config_id=1,
            actual_model="synthetic",
            prompt_version_id=1,
            success=True,
            raw_response="{}",
            parsed_result=invalid,
            validation_errors=[],
        )
        session.add(call)
        session.commit()
        session.refresh(call)
        return AiGenerateResponse(
            ai_call_id=call.id,
            success=True,
            content="{}",
            parsed_result=invalid,
            actual_model="synthetic",
            fallback_used=False,
            repair_used=False,
            input_token=0,
            output_token=0,
            total_token=0,
            estimated_cost=0,
            latency_ms=0,
            response_id=None,
        )

    monkeypatch.setattr("app.modules.api_definitions.service.generate", fake_generate)
    rejected = client.post(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
        json={"kind": "CASE_SET", "prompt_id": 1},
    )
    assert rejected.status_code == 202
    assert rejected.json()["generation_status"] == "QUEUED"
    history = client.get(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
    )
    failed = history.json()["items"][0]
    assert failed["generation_status"] == "FAILED"
    assert "未唯一匹配" in failed["error_message"]
    blocked_edit = client.patch(
        f"/api/v1/api-definitions/ai-suggestions/{failed['id']}",
        headers=headers,
        json={"human_result": invalid},
    )
    assert blocked_edit.status_code == 409
    blocked_decision = client.post(
        f"/api/v1/api-definitions/ai-suggestions/{failed['id']}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    assert blocked_decision.status_code == 409
    retried = client.post(
        f"/api/v1/api-definitions/imports/{import_id}/ai-suggestions",
        headers=headers,
        json={"kind": "CASE_SET", "prompt_id": 1},
    )
    assert retried.status_code == 202
    with factory() as session:
        suggestions = list(session.scalars(select(ApiDesignSuggestion)).all())
        assert len(suggestions) == 2
        assert all(item.generation_status == "FAILED" for item in suggestions)
        assert all(item.draft_key is None for item in suggestions)
